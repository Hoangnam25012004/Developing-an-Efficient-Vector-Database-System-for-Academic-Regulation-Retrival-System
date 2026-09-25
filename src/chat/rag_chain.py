"""RAG chain: retrieval → rerank → LLM-free synthesis.

Generation providers
--------------------
hybrid     LLM-free 4-tier synthesis based on Khoản-level chunks:
             Tier 1 (single dominant Khoản)        → focused single-clause answer
             Tier 2 (multiple Khoản, same Điều)    → grouped legal-list answer
             Tier 3 (multi-source, mixed Điều)     → cross-reference summary
             Tier 4 (no chunk above min_score)     → "not found"
extractive Raw verbatim chunks (legacy, no synthesis)

Why LLM-free works here:
  Each retrieved chunk IS a self-contained legal unit (Khoản or paragraph).
  "Synthesis" reduces to: select the right Khoản, order them by legal
  hierarchy, format with precise citations. No paraphrasing or generation
  needed — therefore zero hallucination.
"""

import re
from collections import defaultdict
from typing import Any

from src.config import load_config
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.reranker import Reranker


# ── Sentence splitter ─────────────────────────────────────────────────────────

_SENT_RE = re.compile(
    r'(?<=[.!?;:])\s+(?=[A-ZÁĂÂĐÉÊÍÓÔƠÚƯÝA-ZÁĂÂĐÉÊÍÓÔƠÚƯÝ\d])'
    r'|'
    r'\n{1,}'
)


def split_sentences(text: str, min_len: int = 20) -> list[str]:
    raw = _SENT_RE.split(text.strip())
    return [s.strip() for s in raw if len(s.strip()) >= min_len]


# ══════════════════════════════════════════════════════════════════════════════
#  Relevance highlighting — lexical, no model required
# ══════════════════════════════════════════════════════════════════════════════

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Function words carry no topical signal but appear in every clause, so they
# would otherwise dominate the overlap score.
_STOPWORDS = {
    "của", "và", "các", "có", "được", "cho", "là", "trong", "với", "theo",
    "về", "khi", "này", "đó", "những", "một", "hoặc", "để", "từ", "tại",
    "bị", "do", "nếu", "thì", "mà", "như", "sẽ", "đã", "không", "phải",
}


def _content_tokens(text: str, extra_stopwords: frozenset = frozenset()) -> set[str]:
    return {
        t for t in (w.lower() for w in _WORD_RE.findall(text))
        if len(t) >= 2 and t not in _STOPWORDS and t not in extra_stopwords
    }


# Function words of English documents. Applied only when the quoted chunk comes
# from a document declared English — several of these ("an", "to", "in",
# "can", "may") are also Vietnamese syllables, so using them on Vietnamese text
# would change which sentence gets bolded.
_ENGLISH_STOPWORDS = frozenset(
    "the of and to in is for that on with as by be are this or from at an it "
    "not which shall will must all any may have has was were their its can".split()
)


def _stopwords_for(d: dict) -> frozenset:
    return _ENGLISH_STOPWORDS if (d.get("language") or "") == "en" else frozenset()


_SOFT_WRAP_RE = re.compile(r"[ \t]*\n(?!\n)[ \t]*")


def _unwrap(text: str) -> str:
    """Undo the hard line breaks PDF extraction leaves mid-sentence.

    Without this, a sentence is split wherever the original PDF wrapped, so
    emphasis lands on a fragment: "...theo thời gian thiết kế chương trình đào"
    bolded, with "tạo" left outside it. Only single newlines are collapsed;
    blank lines stay, since those are real paragraph breaks. No word is
    changed, so the quotation remains verbatim.
    """
    return _SOFT_WRAP_RE.sub(" ", text)


def highlight_relevant(text: str, query: str, top_sentences: int = 1,
                       extra_stopwords: frozenset = frozenset()) -> str:
    """Bold the sentence(s) of `text` that best match `query`.

    The clause is still quoted in full — bolding adds emphasis without removing
    a word, so the citation stays verbatim and legally complete while the reader
    is pointed at the part that answers the question. Truncating to the matching
    sentence would be shorter but would risk dropping a condition or exception
    attached elsewhere in the same provision.
    """
    if top_sentences <= 0:
        return text

    query_tokens = _content_tokens(query, extra_stopwords)
    if not query_tokens:
        return text

    text = _unwrap(text)
    sentences = split_sentences(text, min_len=15)
    if len(sentences) <= 1:
        return text

    scored: list[tuple[float, str]] = []
    for sentence in sentences:
        tokens = _content_tokens(sentence, extra_stopwords)
        if not tokens:
            continue
        overlap = len(query_tokens & tokens)
        if overlap:
            # Normalise by sentence length so a long sentence does not win on
            # sheer size alone.
            scored.append((overlap / (len(tokens) ** 0.5), sentence))

    if not scored:
        return text

    scored.sort(key=lambda pair: -pair[0])
    chosen = {sentence for _, sentence in scored[:top_sentences]}

    out = text
    for sentence in chosen:
        if sentence in out and f"**{sentence}**" not in out:
            out = out.replace(sentence, f"**{sentence}**", 1)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Citation helpers — work at Khoản granularity
# ══════════════════════════════════════════════════════════════════════════════

def _legal_locator(d: dict) -> str:
    """Build the most precise legal locator available for a chunk.

    Examples:
      'Điều 15, Khoản 3 – Hình thức xử lý kỷ luật'
      'Điều 7 – Đối tượng áp dụng'
      'Chương II – Quy định chung'
      'Đoạn 3'   (paragraph chunker)
    """
    article       = (d.get("article") or "").strip()
    article_title = (d.get("article_title") or "").strip()
    chapter       = (d.get("chapter") or "").strip()
    chapter_title = (d.get("chapter_title") or "").strip()
    khoan         = (d.get("khoan") or "").strip()

    if article and khoan:
        head = f"Điều {article}, Khoản {khoan}"
        return f"{head} – {article_title}" if article_title else head
    if article:
        return f"Điều {article} – {article_title}" if article_title else f"Điều {article}"
    if chapter:
        return f"Chương {chapter} – {chapter_title}" if chapter_title else f"Chương {chapter}"
    return ""


# ── Structure-aware labels for documents outside the legal domain ────────────
#
# The chunker stores the three structure levels it detected in the same fields
# (chapter / article / khoan) whatever the document calls them, and documents
# uploaded through the ingestion flow also record which heading pattern filled
# each level (`level_labels`). The labels above always read Chương/Điều/Khoản;
# for a manual organised in Chapters and Sections that is simply wrong, so such
# chunks are named after their own pattern. Chunks without `level_labels` — the
# whole original corpus — and chunks of Vietnamese legal documents keep the
# labels above unchanged.

_LEGAL_VN_PATTERNS = {"CHUONG_VN", "DIEU_VN", "KHOAN_VN"}

_LEVEL_NAMES = {
    "chapter": {
        "CHUONG_VN": ("Chương {v}", "Chương {v}"), "CHAPTER_EN": ("Chapter {v}", "Chapter {v}"),
        "PHAN_VN": ("Phần {v}", "Phần {v}"), "PART_EN": ("Part {v}", "Part {v}"),
        "ROMAN_TITLE": ("Phần {v}", "Part {v}"), "NAMED_VN": ("{v}", "{v}"),
        "NAMED_EN": ("{v}", "{v}"), "HEADING_1": ("{t}", "{t}"),
    },
    "article": {
        "DIEU_VN": ("Điều {v}", "Article {v}"), "ARTICLE_EN": ("Article {v}", "Article {v}"),
        "SECTION_EN": ("Section {v}", "Section {v}"), "PARAGRAPH_SIGN": ("§{v}", "§{v}"),
        "MUC_VN": ("Mục {v}", "Section {v}"), "HEADING_2": ("{t}", "{t}"),
    },
    "khoan": {
        "KHOAN_VN": ("Khoản {v}", "Clause {v}"), "NUM_DOT": ("mục {v}", "item {v}"),
        "NUM_DOT_NUM": ("mục {v}", "item {v}"), "PAREN_DIGIT": ("mục ({v})", "item ({v})"),
        "LETTER_LIST": ("điểm {v}", "point {v}"), "ROMAN_LOWER": ("mục {v}", "item {v}"),
        "THEOREM_STYLE": ("{v}", "{v}"), "CAU_BAI": ("{v}", "{v}"), "HEADING_3": ("mục {v}", "item {v}"),
    },
}


def _uses_generic_labels(d: dict) -> bool:
    labels = d.get("level_labels")
    if not isinstance(labels, dict) or not any(labels.values()):
        return False
    return not (set(labels.values()) & _LEGAL_VN_PATTERNS)


def _level_name(d: dict, level: str, value: str, title: str = "") -> str:
    pattern = (d.get("level_labels") or {}).get(level) or ""
    en = (d.get("language") or "") == "en"
    names = _LEVEL_NAMES[level].get(pattern)
    if names is None:
        return value
    template = names[1] if en else names[0]
    if "{t}" in template:
        return title or value
    return template.format(v=value)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _generic_locator(d: dict) -> str:
    article = (d.get("article") or "").strip()
    article_title = (d.get("article_title") or "").strip()
    chapter = (d.get("chapter") or "").strip()
    chapter_title = (d.get("chapter_title") or "").strip()
    khoan = (d.get("khoan") or "").strip()

    if article:
        head, title = _level_name(d, "article", article, article_title), article_title
    elif chapter:
        head, title = _level_name(d, "chapter", chapter, chapter_title), chapter_title
    else:
        head, title = "", ""
    if khoan:
        leaf = _level_name(d, "khoan", khoan)
        head = f"{head}, {leaf}" if head else _cap(leaf)
    if title and title != head and not head.endswith(title):
        head = f"{head} – {title}"
    return head


def _locator(d: dict) -> str:
    return _generic_locator(d) if _uses_generic_labels(d) else _legal_locator(d)


def _khoan_sort_key_generic(d: dict) -> tuple:
    """Order "2", "2.1", "10" numerically, letters after numbers."""
    k = (d.get("khoan") or "").strip()
    if not k:
        return (-1, ())
    nums = re.findall(r"\d+", k)
    if nums:
        return (0, tuple(int(n) for n in nums))
    return (1, (k,))


def _doc_label(d: dict) -> str:
    """Human-readable document identifier for citation footer."""
    src = d.get("source", "unknown")
    num = (d.get("doc_number") or "").strip()
    return f"{num} – {src}" if num else src


def build_citations_footer(docs: list[dict]) -> str:
    """Deterministic 'Tài liệu đính kèm' footer, deduplicated by source."""
    if not docs:
        return ""
    lines = ["", "---", "**Tài liệu đính kèm:**"]
    seen: set[str] = set()
    n = 1
    for d in docs:
        src = d.get("source") or ""
        if not src or src == "unknown":
            continue
        if src in seen:
            continue
        seen.add(src)
        page = d.get("page") or "?"
        label = _doc_label(d)
        lines.append(f"[{n}]: {label} — trang {page}")
        n += 1
    if len(lines) == 3:  # header only, no valid sources
        return ""
    return "\n".join(lines)


def _source_to_footnote(docs: list[dict]) -> dict[str, int]:
    """Map source filename → footnote number (1-based, ordered by first appearance)."""
    mapping: dict[str, int] = {}
    n = 1
    for d in docs:
        src = d.get("source", "unknown")
        if src not in mapping:
            mapping[src] = n
            n += 1
    return mapping


# ══════════════════════════════════════════════════════════════════════════════
#  Tier classifier — decides which synthesis pattern to use
# ══════════════════════════════════════════════════════════════════════════════

def _classify_tier(docs: list[dict], top_score: float,
                   min_score: float, single_dominance_gap: float) -> str:
    """
    Decide synthesis pattern:
      tier1  → 1 Khoản clearly dominates (large gap to #2)
      tier2  → multiple Khoản from the SAME Điều of the SAME source
      tier3  → mixed Điều / mixed source
      tier4  → top score below min_score
    """
    if not docs or top_score < min_score:
        return "tier4"

    # Dominance check
    if len(docs) >= 2:
        gap = docs[0].get("_score_rerank", 0) - docs[1].get("_score_rerank", 0)
        if gap >= single_dominance_gap:
            return "tier1"
    else:
        return "tier1"

    # Same-Điều check on top 4
    head = docs[:4]
    same_article = (
        all(d.get("source") == head[0].get("source") for d in head)
        and all(d.get("article") == head[0].get("article") for d in head)
        and head[0].get("article")
    )
    if same_article:
        return "tier2"

    return "tier3"


# ══════════════════════════════════════════════════════════════════════════════
#  Tier 1 — single dominant Khoản
# ══════════════════════════════════════════════════════════════════════════════

def _partial_note(d: dict) -> str:
    """Flag a clause that was too large to quote in full."""
    if d.get("_partial"):
        used, total = d.get("_parts_used", 0), d.get("_parts_total", 0)
        return f"\n\n*(Trích {used}/{total} phần của Khoản này — xem văn bản gốc để đầy đủ.)*"
    return ""


def tier1_single_clause(docs: list[dict], query: str = "", highlight: int = 0) -> str:
    """One Khoản is clearly the answer. Show it verbatim with full locator."""
    d = docs[0]
    fn_map = _source_to_footnote(docs)
    fn = fn_map[d.get("source", "unknown")]
    locator = _locator(d)
    text = (d.get("text") or "").strip()
    if highlight and query:
        text = highlight_relevant(text, query, highlight, _stopwords_for(d))

    header = f"**{locator}**" if locator else "**Trích dẫn liên quan**"
    body = f"{header} [{fn}]\n\n> {text}{_partial_note(d)}"
    return body + "\n" + build_citations_footer(docs)


# ══════════════════════════════════════════════════════════════════════════════
#  Tier 2 — multiple Khoản from the same Điều
# ══════════════════════════════════════════════════════════════════════════════

def _khoan_sort_key(d: dict) -> tuple:
    """Sort by Khoản number numerically; empty Khoản goes first (Điều intro)."""
    k = d.get("khoan", "")
    try:
        return (0, int(k)) if k else (-1, 0)
    except ValueError:
        return (1, 0)


def tier2_same_article(docs: list[dict], query: str = "", highlight: int = 0,
                       max_clauses: int = 0) -> str:
    """
    All top docs come from the same Điều. Group by Khoản number, render each
    Khoản verbatim. If a single Khoản was split across multiple sub-chunks
    (long-Khoản case), all its sub-chunks render in retrieval order.
    """
    head = docs[0]
    fn_map = _source_to_footnote(docs)
    fn = fn_map[head.get("source", "unknown")]
    generic = _uses_generic_labels(head)

    article = head.get("article", "")
    article_title = head.get("article_title", "") or ""
    if generic:
        name = _cap(_level_name(head, "article", article, article_title))
        heading = f"**{name} – {article_title}**" if article_title and article_title != name else f"**{name}**"
    else:
        heading = f"**Điều {article} – {article_title}**" if article_title else f"**Điều {article}**"

    # Group by Khoản number, preserving order; dedup only on identical text
    grouped: dict[str, list[dict]] = {}
    seen_texts: dict[str, set[str]] = {}
    for d in docs:
        if d.get("article") != article:
            continue
        k = d.get("khoan", "")
        text_key = (d.get("text") or "")[:80]
        if text_key in seen_texts.setdefault(k, set()):
            continue
        seen_texts[k].add(text_key)
        grouped.setdefault(k, []).append(d)

    sort_key = _khoan_sort_key_generic if generic else _khoan_sort_key
    sorted_keys = sorted(grouped.keys(), key=lambda k: sort_key({"khoan": k}))

    # Verbosity cap: an answer that lists every Khoản of a long Điều buries the
    # one the reader asked about. Keys stay in legal order; only the tail is cut.
    omitted = 0
    if max_clauses and len(sorted_keys) > max_clauses:
        omitted = len(sorted_keys) - max_clauses
        sorted_keys = sorted_keys[:max_clauses]

    parts = [f"{heading} [{fn}]", ""]
    for k in sorted_keys:
        if k:
            if generic:
                parts.append(f"**{_cap(_level_name(head, 'khoan', k))}:**")
            else:
                parts.append(f"**Khoản {k}:**")
        for d in grouped[k]:
            text = (d.get("text") or "").strip()
            if highlight and query:
                text = highlight_relevant(text, query, highlight, _stopwords_for(d))
            parts.append(f"> {text}{_partial_note(d)}")
        parts.append("")

    if omitted:
        if generic:
            parts.append(f"*(Còn {omitted} mục khác trong phần này không được trích.)*")
        else:
            parts.append(f"*(Còn {omitted} khoản khác trong Điều này không được trích.)*")

    return "\n".join(parts).rstrip() + "\n" + build_citations_footer(docs)


# ══════════════════════════════════════════════════════════════════════════════
#  Tier 3 — multi-source / multi-Điều synthesis
# ══════════════════════════════════════════════════════════════════════════════

def tier3_multi_source(docs: list[dict], query: str = "", highlight: int = 0,
                       max_clauses: int = 0) -> str:
    """
    Top docs span multiple Điều or sources. Group by source, then by Điều,
    list relevant Khoản under each. No paraphrasing — verbatim quotes only.
    """
    # This tier is where answers sprawl: every additional retrieved chunk adds
    # another quoted block. Trim to the highest-scoring clauses before grouping,
    # so the structure below reflects what is actually shown.
    if max_clauses and len(docs) > max_clauses:
        docs = docs[:max_clauses]

    fn_map = _source_to_footnote(docs)

    # Group: source → article → list[chunks]
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    article_titles: dict[tuple[str, str], str] = {}
    article_first_score: dict[tuple[str, str], float] = {}

    for d in docs:
        src = d.get("source", "unknown")
        art = d.get("article", "") or "_no_article_"
        grouped[src][art].append(d)
        key = (src, art)
        if d.get("article_title") and key not in article_titles:
            article_titles[key] = d["article_title"]
        if key not in article_first_score:
            article_first_score[key] = d.get("_score_rerank", 0.0)

    # Sort sources by best score among their chunks
    src_best_score = {
        src: max(d.get("_score_rerank", 0) for d in [c for arts in articles.values() for c in arts])
        for src, articles in grouped.items()
    }
    sorted_sources = sorted(grouped.keys(), key=lambda s: -src_best_score[s])

    parts: list[str] = []
    section_idx = 1

    for src in sorted_sources:
        articles = grouped[src]
        # Sort articles within a source by relevance score
        sorted_arts = sorted(articles.keys(), key=lambda a: -article_first_score[(src, a)])
        fn = fn_map[src]

        for art in sorted_arts:
            chunks = articles[art]
            # Group by Khoản; dedup only on identical text within same Khoản
            khoan_groups: dict[str, list[dict]] = {}
            seen_texts: dict[str, set[str]] = {}
            for c in chunks:
                k = c.get("khoan", "")
                tk = (c.get("text") or "")[:80]
                if tk in seen_texts.setdefault(k, set()):
                    continue
                seen_texts[k].add(tk)
                khoan_groups.setdefault(k, []).append(c)
            generic = _uses_generic_labels(chunks[0])
            sort_key = _khoan_sort_key_generic if generic else _khoan_sort_key
            sorted_keys = sorted(khoan_groups.keys(),
                                 key=lambda k: sort_key({"khoan": k}))

            title = article_titles.get((src, art), "")
            if art and art != "_no_article_":
                if generic:
                    name = _cap(_level_name(chunks[0], "article", art, title))
                    heading = f"**{section_idx}. {name}"
                    if title and title != name:
                        heading += f" – {title}"
                else:
                    heading = f"**{section_idx}. Điều {art}"
                    if title:
                        heading += f" – {title}"
                heading += f"** [{fn}]"
            else:
                heading = f"**{section_idx}. {_doc_label(chunks[0])}** [{fn}]"

            parts.append(heading)

            for k in sorted_keys:
                if k:
                    if generic:
                        parts.append(f"\n*{_cap(_level_name(chunks[0], 'khoan', k))}:*")
                    else:
                        parts.append(f"\n*Khoản {k}:*")
                for d in khoan_groups[k]:
                    text = (d.get("text") or "").strip()
                    if highlight and query:
                        text = highlight_relevant(text, query, highlight, _stopwords_for(d))
                    parts.append(f"> {text}{_partial_note(d)}")
                parts.append("")

            section_idx += 1

    return "\n".join(parts).rstrip() + "\n" + build_citations_footer(docs)


# ══════════════════════════════════════════════════════════════════════════════
#  Hybrid router — LLM-free
# ══════════════════════════════════════════════════════════════════════════════

def hybrid_answer(
    query: str,
    docs: list[dict],
    min_score: float = 0.05,
    single_dominance_gap: float = 0.30,
    not_found_message: str = "Tôi không tìm thấy thông tin này trong tài liệu hiện có.",
    highlight: int = 0,
    tier2_max_clauses: int = 0,
    tier3_max_clauses: int = 0,
) -> tuple[str, str]:
    """Route to the appropriate tier based on rerank score distribution and metadata.

    Returns (answer, tier) so callers can measure how often each pattern fires —
    the tier mix is what drives answer length, and it shifts when the candidate
    pool is widened.
    """
    if not docs:
        return not_found_message, "tier4"

    top_score = docs[0].get("_score_rerank", 0.0)
    tier = _classify_tier(docs, top_score, min_score, single_dominance_gap)

    if tier == "tier1":
        return tier1_single_clause(docs, query, highlight), tier
    if tier == "tier2":
        return tier2_same_article(docs, query, highlight, tier2_max_clauses), tier
    if tier == "tier3":
        return tier3_multi_source(docs, query, highlight, tier3_max_clauses), tier
    return not_found_message, "tier4"


# ══════════════════════════════════════════════════════════════════════════════
#  Legacy extractive
# ══════════════════════════════════════════════════════════════════════════════

def extractive_answer(query: str, docs: list[dict]) -> str:
    lines = [f"**Trả lời trích xuất từ {len(docs)} đoạn liên quan nhất:**\n"]
    for i, d in enumerate(docs, 1):
        src   = d.get("source", "?")
        page  = d.get("page", "?")
        text  = (d.get("text") or "").strip()
        loc   = _locator(d)
        loc_str = f" · {loc}" if loc else ""
        score = d.get("_score_rerank")
        score_str = f" · rerank={score:.3f}" if isinstance(score, (int, float)) else ""
        lines.append(f"**[{i}] {src} — trang {page}{loc_str}**{score_str}\n> {text}\n")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  RAGChain
# ══════════════════════════════════════════════════════════════════════════════

class RAGChain:
    def __init__(self, config_path: str = "config.yaml"):
        self.cfg       = load_config(config_path)
        self.retriever = HybridRetriever(self.cfg)
        self.reranker  = Reranker(self.cfg)

        llm_cfg = self.cfg["llm"]
        self.provider = llm_cfg.get("provider", "hybrid").lower()

        # Hybrid (LLM-free) parameters
        self.min_score            = float(llm_cfg.get("min_score", 0.05))
        self.single_dominance_gap = float(llm_cfg.get("single_dominance_gap", 0.30))
        self.not_found_message    = llm_cfg.get(
            "not_found_message",
            "Tôi không tìm thấy thông tin này trong tài liệu hiện có.",
        )

        # ── Answer-quality controls ──────────────────────────────────────────
        self.highlight         = int(llm_cfg.get("highlight_sentences", 0))
        self.tier2_max_clauses = int(llm_cfg.get("tier2_max_clauses", 0))
        self.tier3_max_clauses = int(llm_cfg.get("tier3_max_clauses", 0))

        # Split clauses are reassembled before quoting so an answer never shows
        # the middle of a provision without its opening and closing text.
        self.clause_index = None
        assembly = llm_cfg.get("clause_assembly") or {}
        if assembly.get("enabled", False):
            from src.chat.clause_assembler import ClauseIndex

            self.clause_index = ClauseIndex(
                self.cfg["data"]["processed_dir"],
                max_chars=int(assembly.get("max_chars", 3000)),
            )

        if self.provider not in {"hybrid", "extractive"}:
            raise ValueError(
                f"Unknown llm.provider: {self.provider!r} "
                f"(supported: hybrid, extractive)"
            )

    def reload_indexes(self) -> None:
        """Pick up index files an ingestion job rewrote, without reloading models.

        BM25 and the clause index are read from disk at start-up; the dense
        branch queries Qdrant live. Each replacement is built completely before
        it is swapped in, so a query in flight finishes on the previous one.
        """
        self.retriever.reload_sparse()
        if self.clause_index is not None:
            from src.chat.clause_assembler import ClauseIndex

            assembly = self.cfg["llm"].get("clause_assembly") or {}
            self.clause_index = ClauseIndex(
                self.cfg["data"]["processed_dir"],
                max_chars=int(assembly.get("max_chars", 3000)),
            )

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        candidates = self.retriever.search(query)
        return self.reranker.rerank(query, candidates)

    def generate(self, query: str, docs: list[dict]) -> tuple[str, str]:
        """Returns (answer, tier)."""
        if not docs:
            return self.not_found_message, "tier4"

        if self.provider == "hybrid":
            return hybrid_answer(
                query, docs,
                min_score=self.min_score,
                single_dominance_gap=self.single_dominance_gap,
                not_found_message=self.not_found_message,
                highlight=self.highlight,
                tier2_max_clauses=self.tier2_max_clauses,
                tier3_max_clauses=self.tier3_max_clauses,
            )

        # extractive
        return extractive_answer(query, docs), "extractive"

    def query(self, question: str) -> dict[str, Any]:
        docs = self.retrieve(question)
        # Expand after reranking, not before: the reranker scores the precise
        # sub-chunk that matched, while the reader receives the whole clause.
        if self.clause_index is not None:
            docs = self.clause_index.expand_all(docs)
        answer, tier = self.generate(question, docs)
        return {
            "question": question,
            "answer":   answer,
            "tier":     tier,
            "sources": [
                {
                    "chunk_id":     d.get("chunk_id"),
                    "source":       d.get("source"),
                    "page":         d.get("page"),
                    "article":      d.get("article", ""),
                    "khoan":        d.get("khoan", ""),
                    "text":         d.get("text", "")[:800],
                    "score_rrf":    d.get("_score_rrf"),
                    "score_rerank": d.get("_score_rerank"),
                    # Only documents uploaded through ingestion carry these;
                    # the UI names their structure levels from them.
                    "level_labels": d.get("level_labels"),
                    "file_type":    d.get("file_type"),
                    "language":     d.get("language"),
                }
                for d in docs
            ],
        }
