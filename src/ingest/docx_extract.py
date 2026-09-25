"""DOCX → text pages, table rows and heading structure for the shared chunker.

Why not convert the DOCX to PDF and reuse the PDF path: measured with the
PyMuPDF in this venv, the conversion drops Word's automatic list numbers
("1.", "a)") and turns tables into one cell per line. The chunker needs both —
the numbers are what the heading catalog detects as clauses, and a table row
is only interpretable next to its column headers — so the document is read
from its XML instead.

What is extracted, in reading order:
  - body paragraphs, with automatic numbering rebuilt from numbering.xml and
    tracked changes resolved as "accept all" (insertions kept, deletions
    dropped — python-docx's Paragraph.text silently drops tracked insertions);
  - tables: a real table becomes one self-contained chunk per data row, in the
    same format the PDF table chunker emits; a 1-row or 1-column layout table
    is read as plain text;
  - page numbers, from the page breaks Word recorded the last time it laid the
    document out (w:lastRenderedPageBreak), else from explicit breaks.

Not extracted (reported as warnings): headers/footers, text boxes, footnotes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import docx
from docx.oxml.ns import qn
from docx.table import Table

_W = {t: qn(f"w:{t}") for t in (
    "p", "tbl", "r", "t", "tab", "br", "cr", "noBreakHyphen", "sdt", "sdtContent",
    "customXml", "del", "moveFrom", "txbxContent", "lastRenderedPageBreak", "pPr",
    "numPr", "numId", "ilvl", "pStyle", "outlineLvl", "pageBreakBefore", "sectPr",
    "type", "val", "tr", "tc", "trPr", "tblHeader", "num", "abstractNum",
    "abstractNumId", "lvl", "start", "numFmt", "lvlText", "lvlOverride",
    "startOverride", "numStyleLink", "styleLink", "style", "styleId", "name",
    "basedOn", "footnoteReference",
)}
_SKIP_ANCESTORS = {_W["del"], _W["moveFrom"], _W["txbxContent"]}
_FALSE = {"0", "false", "off"}


@dataclass
class Block:
    kind: str              # "heading" | "para"
    text: str
    level: int | None      # outline level 0-8 for headings
    page: int


@dataclass
class DocxExtract:
    pages: list[dict]                  # [{page, text}] — same shape as the PDF path
    table_chunks: list[dict]           # [{page, section_type, text, table_caption}]
    blocks: list[Block]
    linear_text: str                   # everything in reading order (doc-number lookup)
    page_count: int
    warnings: list[str] = field(default_factory=list)


def _on(el) -> bool:
    """OOXML on/off property: present means on unless w:val says otherwise."""
    if el is None:
        return False
    val = el.get(_W["val"])
    return val is None or val.lower() not in _FALSE


# ─── Styles and numbering ───────────────────────────────────────────────────

class _Styles:
    def __init__(self, document):
        self._by_id: dict[str, dict] = {}
        root = document.styles.element
        for st in root.iterchildren(_W["style"]):
            sid = st.get(_W["styleId"])
            if not sid:
                continue
            name_el = st.find(_W["name"])
            based = st.find(_W["basedOn"])
            ppr = st.find(_W["pPr"])
            num_id = ilvl = outline = None
            if ppr is not None:
                npr = ppr.find(_W["numPr"])
                if npr is not None:
                    nid = npr.find(_W["numId"])
                    lvl = npr.find(_W["ilvl"])
                    num_id = nid.get(_W["val"]) if nid is not None else None
                    ilvl = int(lvl.get(_W["val"])) if lvl is not None else None
                ol = ppr.find(_W["outlineLvl"])
                if ol is not None:
                    outline = int(ol.get(_W["val"]))
            self._by_id[sid] = {
                "name": (name_el.get(_W["val"]) if name_el is not None else "").lower(),
                "based_on": based.get(_W["val"]) if based is not None else None,
                "num_id": num_id, "ilvl": ilvl, "outline": outline,
            }

    def _chain(self, style_id: str | None):
        seen = set()
        while style_id and style_id not in seen and style_id in self._by_id:
            seen.add(style_id)
            yield self._by_id[style_id]
            style_id = self._by_id[style_id]["based_on"]

    def num_pr(self, style_id: str | None) -> tuple[str, int] | None:
        for st in self._chain(style_id):
            if st["num_id"]:
                return st["num_id"], st["ilvl"] or 0
        return None

    def heading_level(self, style_id: str | None) -> int | None:
        """Built-in names are stored in English ("heading 1") whatever the UI
        language, so match the name, not the (localised) style id."""
        for st in self._chain(style_id):
            m = re.fullmatch(r"heading (\d)", st["name"])
            if m:
                return int(m.group(1)) - 1
            if st["name"] == "title":
                return 0
            if st["outline"] is not None and st["outline"] < 9:
                return st["outline"]
        return None


def _roman(n: int) -> str:
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = []
    for v, s in vals:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out) or "0"


def _letters(n: int) -> str:
    # Word repeats the letter: a..z, aa..zz, aaa..
    n = max(n, 1)
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)


def _format_number(n: int, fmt: str) -> str:
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)


class _Numbering:
    """Rebuilds the list markers Word draws but does not store in the text."""

    def __init__(self, document, styles: _Styles):
        self._num_to_abs: dict[str, str] = {}
        self._overrides: dict[str, dict[int, int]] = {}
        self._levels: dict[str, dict[int, tuple[str, str, int]]] = {}
        self._style_links: dict[str, str] = {}
        self._styles = styles
        self._counters: dict[str, dict[int, int]] = {}
        self._started: set[str] = set()
        try:
            root = document.part.numbering_part.element
        except Exception:
            root = None
        if root is None:
            return
        for an in root.iterchildren(_W["abstractNum"]):
            aid = an.get(_W["abstractNumId"])
            levels: dict[int, tuple[str, str, int]] = {}
            for lvl in an.iterchildren(_W["lvl"]):
                ilvl = int(lvl.get(_W["ilvl"]) or 0)
                fmt_el, txt_el, start_el = lvl.find(_W["numFmt"]), lvl.find(_W["lvlText"]), lvl.find(_W["start"])
                levels[ilvl] = (
                    fmt_el.get(_W["val"]) if fmt_el is not None else "decimal",
                    txt_el.get(_W["val"]) if txt_el is not None else "",
                    int(start_el.get(_W["val"])) if start_el is not None else 1,
                )
            self._levels[aid] = levels
            link = an.find(_W["numStyleLink"])
            if link is not None:
                self._style_links[aid] = link.get(_W["val"])
        for num in root.iterchildren(_W["num"]):
            nid = num.get(_W["numId"])
            ref = num.find(_W["abstractNumId"])
            if ref is None:
                continue
            self._num_to_abs[nid] = ref.get(_W["val"])
            ov: dict[int, int] = {}
            for lo in num.iterchildren(_W["lvlOverride"]):
                so = lo.find(_W["startOverride"])
                if so is not None:
                    ov[int(lo.get(_W["ilvl"]) or 0)] = int(so.get(_W["val"]))
            if ov:
                self._overrides[nid] = ov

    def _abstract(self, num_id: str) -> str | None:
        aid = self._num_to_abs.get(num_id)
        # A list style (numStyleLink) points at a style whose numPr holds the real list.
        for _ in range(3):
            if aid is None or aid not in self._style_links:
                break
            linked = self._styles.num_pr(self._style_links[aid])
            if not linked:
                break
            aid = self._num_to_abs.get(linked[0], aid)
        return aid

    def marker(self, num_id: str, ilvl: int) -> str:
        if not num_id or num_id == "0":
            return ""
        aid = self._abstract(num_id)
        levels = self._levels.get(aid or "")
        if not levels or ilvl not in levels:
            return ""
        counters = self._counters.setdefault(aid, {})
        if num_id not in self._started:
            self._started.add(num_id)
            for lv, start in self._overrides.get(num_id, {}).items():
                counters[lv] = start - 1
                for deeper in [k for k in counters if k > lv]:
                    del counters[deeper]
        fmt, text, start = levels[ilvl]
        counters[ilvl] = counters.get(ilvl, start - 1) + 1
        for deeper in [k for k in counters if k > ilvl]:
            del counters[deeper]            # a deeper list restarts under a new parent
        if fmt == "bullet":
            return "- "
        if fmt == "none" or not text:
            return ""

        def repl(m: re.Match) -> str:
            lv = int(m.group(1)) - 1
            lv_fmt, _, lv_start = levels.get(lv, ("decimal", "", 1))
            return _format_number(counters.get(lv, lv_start), lv_fmt)

        rendered = re.sub(r"%([1-9])", repl, text).strip()
        return f"{rendered} " if rendered else ""


# ─── Paragraph text ─────────────────────────────────────────────────────────

def _has_skip_ancestor(el, stop) -> bool:
    parent = el.getparent()
    while parent is not None and parent is not stop:
        if parent.tag in _SKIP_ANCESTORS:
            return True
        parent = parent.getparent()
    return False


class _Reader:
    def __init__(self, document, rendered_mode: bool):
        self.styles = _Styles(document)
        self.numbering = _Numbering(document, self.styles)
        self.rendered = rendered_mode
        self.page = 1
        self._pending_new_page = 0      # explicit section/page break owed to the next content

    def paragraph(self, p) -> tuple[str, int | None, int]:
        """Return (text with list marker, heading level, page the paragraph starts on)."""
        ppr = p.find(_W["pPr"])
        style_id = None
        level: int | None = None
        num: tuple[str, int] | None = None
        if ppr is not None:
            ps = ppr.find(_W["pStyle"])
            style_id = ps.get(_W["val"]) if ps is not None else None
            npr = ppr.find(_W["numPr"])
            if npr is not None:
                nid, lvl = npr.find(_W["numId"]), npr.find(_W["ilvl"])
                if nid is not None:
                    num = (nid.get(_W["val"]), int(lvl.get(_W["val"])) if lvl is not None else 0)
            ol = ppr.find(_W["outlineLvl"])
            if ol is not None and int(ol.get(_W["val"])) < 9:
                level = int(ol.get(_W["val"]))
        if level is None:
            level = self.styles.heading_level(style_id)
        if num is None:
            num = self.styles.num_pr(style_id)

        if self._pending_new_page:
            self.page += self._pending_new_page
            self._pending_new_page = 0
        if not self.rendered and ppr is not None and _on(ppr.find(_W["pageBreakBefore"])):
            self.page += 1

        parts: list[str] = []
        seen_text = False
        breaks_after = 0
        for r in p.iter(_W["r"]):
            if _has_skip_ancestor(r, p):
                continue
            for ch in r:
                tag = ch.tag
                if tag == _W["t"]:
                    value = ch.text or ""
                    parts.append(value)
                    if value.strip():
                        seen_text = True
                elif tag == _W["tab"]:
                    parts.append(" ")
                elif tag == _W["br"]:
                    if ch.get(_W["type"]) == "page":
                        if not self.rendered:
                            if seen_text:
                                breaks_after += 1
                            else:
                                self.page += 1
                    else:
                        parts.append("\n")
                elif tag == _W["cr"]:
                    parts.append("\n")
                elif tag == _W["noBreakHyphen"]:
                    parts.append("-")
                elif tag == _W["lastRenderedPageBreak"] and self.rendered:
                    if seen_text:
                        breaks_after += 1
                    else:
                        self.page += 1
        start_page = self.page
        self.page += breaks_after

        if not self.rendered and ppr is not None:
            sect = ppr.find(_W["sectPr"])
            if sect is not None:
                t = sect.find(_W["type"])
                if (t.get(_W["val"]) if t is not None else "nextPage") != "continuous":
                    self._pending_new_page += 1

        marker = self.numbering.marker(*num) if num else ""
        text = "".join(parts)
        text = "\n".join(" ".join(line.split()) for line in text.split("\n")).strip()
        if text and marker:
            text = marker + text
        return text, level, start_page


def _iter_blocks(parent):
    """Body-level paragraphs and tables in order, looking inside block-level
    content controls (w:sdt) and custom XML, which python-docx skips."""
    for el in parent.iterchildren():
        if el.tag in (_W["p"], _W["tbl"]):
            yield el
        elif el.tag == _W["sdt"]:
            content = el.find(_W["sdtContent"])
            if content is not None:
                yield from _iter_blocks(content)
        elif el.tag == _W["customXml"]:
            yield from _iter_blocks(el)


# ─── Main entry ─────────────────────────────────────────────────────────────

def extract_docx(
    path: Path,
    *,
    caption_ok: Callable[[str], bool],
    header_candidate: Callable[[list[str]], bool],
    build_labels: Callable[[list[list[str]], int], list[str]],
) -> DocxExtract:
    """Read a .docx. The three callables are the PDF table chunker's own
    helpers, passed in so both formats label and caption tables identically."""
    document = docx.Document(str(path))
    body = document.element.body
    rendered = body.find(".//" + _W["lastRenderedPageBreak"]) is not None
    reader = _Reader(document, rendered)

    blocks: list[Block] = []
    table_chunks: list[dict] = []
    linear: list[str] = []
    table_no = 0
    last_block_was_text = False

    for el in _iter_blocks(body):
        if el.tag == _W["p"]:
            text, level, page = reader.paragraph(el)
            if text:
                kind = "heading" if level is not None else "para"
                blocks.append(Block(kind, text, level, page))
                linear.append(text)
                last_block_was_text = True
            continue

        # ── table ──
        caption = ""
        if last_block_was_text and blocks:
            candidate = " ".join(blocks[-1].text.split())
            if caption_ok(candidate):
                caption = candidate[:160]
        last_block_was_text = False

        table = Table(el, document._body)
        rows: list[list[str]] = []
        row_pages: list[int] = []
        header_rows = 0
        header_zone = True
        # A vertically merged cell is handed back again for every row it spans;
        # reading it twice would advance list numbering and page breaks twice.
        cell_text: dict = {}
        try:
            doc_rows = list(table.rows)
        except Exception:
            doc_rows = []
        for row in doc_rows:
            try:
                cells = row.cells
            except Exception:
                continue
            row_pages.append(reader.page)
            values: list[str] = []
            prev = None
            for cell in cells:
                if cell is None:                   # grid position with no cell
                    values.append("")
                    prev = None
                    continue
                if prev is not None and cell._tc is prev:
                    values.append("")          # horizontal merge: text once per row
                    continue
                prev = cell._tc
                if cell._tc in cell_text:
                    values.append(cell_text[cell._tc])
                    continue
                lines = []
                for child in cell._tc.iterchildren():
                    if child.tag == _W["p"]:
                        t, _, _ = reader.paragraph(child)
                        if t:
                            lines.append(t)
                    elif child.tag == _W["tbl"]:
                        for nested in child.iter(_W["tr"]):
                            nested_cells = []
                            for tc in nested.iterchildren(_W["tc"]):
                                txt = " ".join(
                                    reader.paragraph(np_)[0] for np_ in tc.iterchildren(_W["p"])
                                ).strip()
                                if txt:
                                    nested_cells.append(txt)
                            if nested_cells:
                                lines.append(" | ".join(nested_cells))
                cell_text[cell._tc] = "\n".join(lines).strip()
                values.append(cell_text[cell._tc])
            trpr = row._tr.trPr
            if header_zone and trpr is not None and _on(trpr.find(_W["tblHeader"])):
                header_rows += 1
            else:
                header_zone = False
            rows.append(values)

        n_rows = len(rows)
        n_cols = max((len(r) for r in rows), default=0)
        for r in rows:
            joined = " | ".join(v for v in r if v)
            if joined:
                linear.append(joined)

        if n_rows < 2 or n_cols < 2:
            # Layout table (letterhead, signature block): plain text.
            page = row_pages[0] if row_pages else reader.page
            for r in rows:
                for value in r:
                    if value:
                        blocks.append(Block("para", value, None, page))
            last_block_was_text = bool(rows)
            continue

        table_no += 1
        rows = [r + [""] * (n_cols - len(r)) for r in rows]
        if header_rows == 0 and header_candidate(rows[0]):
            header_rows = 1
        header_rows = min(header_rows, n_rows - 1)
        labels = (build_labels(rows[:header_rows], n_cols) if header_rows
                  else [f"Cột {i + 1}" for i in range(n_cols)])
        header_values = [" ".join(v.split()) for v in rows[0]] if header_rows else None
        data_no = 0
        for i in range(header_rows, n_rows):
            cells = rows[i]
            if not any(v.strip() for v in cells):
                continue
            if header_values is not None and [" ".join(v.split()) for v in cells] == header_values:
                continue                        # header repeated on a new page
            data_no += 1
            page = row_pages[i] if i < len(row_pages) else reader.page
            head = f"[Bảng {table_no} · trang {page} · dòng {data_no}]"
            lines = [f"{caption}\n{head}" if caption else head]
            for ci, value in enumerate(cells):
                value = " ".join(value.split())
                if value:
                    lines.append(f"{labels[ci]}: {value}")
            table_chunks.append({
                "page": page,
                "section_type": "table_row",
                "text": "\n".join(lines),
                "table_caption": caption,
            })

    # One paragraph per line, as PDF extraction yields: chunk text then never
    # carries a blank line, which would cut the "> " quote of an answer short.
    by_page: dict[int, list[str]] = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b.text)
    pages = [{"page": p, "text": "\n".join(texts)} for p, texts in sorted(by_page.items())]
    page_count = max([reader.page] + [b.page for b in blocks] + [t["page"] for t in table_chunks])

    warnings: list[str] = []
    try:
        if any(
            any(par.text.strip() for par in part.paragraphs)
            for section in document.sections
            for part in (section.header, section.footer)
        ):
            warnings.append("header_footer_skipped")
    except Exception:
        pass
    if body.find(".//" + _W["txbxContent"]) is not None:
        warnings.append("text_boxes_skipped")
    if body.find(".//" + _W["footnoteReference"]) is not None:
        warnings.append("footnotes_skipped")

    return DocxExtract(
        pages=pages,
        table_chunks=table_chunks,
        blocks=blocks,
        linear_text="\n".join(linear),
        page_count=page_count,
        warnings=warnings,
    )
