"""Reassemble split legal clauses before they are quoted.

The chunker caps a Khoản at 512 characters, so 14.4% of clause units in this
corpus exist as two or more sub-chunks. Retrieval is happy with that — small
units match queries more precisely — but quoting is not: returning sub-chunk
2 of 3 hands the reader the middle of a clause, with its opening condition and
its closing exception missing. In a legal setting that is not merely terse,
it can invert the meaning of the provision.

So the index stays fine-grained and the *answer* is coarse-grained: retrieve
on the sub-chunk, quote the whole Khoản. This is the small-to-big pattern,
with one domain-specific constraint — a handful of units in this corpus are
pathological (amending laws that nest every revision under "Điều 1, Khoản n",
and one 44k-character appendix parsed as a single clause). Expanding those
verbatim would bury the answer, so expansion is bounded: past the cap the
clause is reassembled as a window around the chunk that was actually
retrieved, and the result is marked as partial.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ClauseIndex:
    """Maps a chunk back to the full text of the clause it belongs to."""

    def __init__(self, processed_dir: str | Path, max_chars: int = 3000):
        self.max_chars = max_chars
        self._parts: dict[tuple, list[dict]] = {}
        self._by_id: dict[str, dict] = {}

        directory = Path(processed_dir)
        for path in sorted(directory.glob("*.jsonl")):
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    self._by_id[chunk["chunk_id"]] = chunk
                    key = self._key(chunk)
                    if key is not None:
                        self._parts.setdefault(key, []).append(chunk)

        for parts in self._parts.values():
            parts.sort(key=lambda c: (c.get("page") or 0, c.get("chunk_index") or 0))

    @staticmethod
    def _key(chunk: dict) -> tuple | None:
        """Group key for a clause. Only clauses with an explicit Khoản qualify.

        Chunks without a Khoản (article preambles, prose paragraphs, table rows)
        have no well-defined parent unit, so they are quoted as retrieved.
        """
        article = (chunk.get("article") or "").strip()
        khoan = (chunk.get("khoan") or "").strip()
        if not article or not khoan:
            return None
        return (chunk.get("source", ""), article, khoan)

    def is_split(self, chunk: dict) -> bool:
        """True when this chunk is one piece of a genuinely continuous clause."""
        key = self._key(chunk)
        if key is None:
            return False
        group = self._parts.get(key, [])
        anchor = next(
            (i for i, p in enumerate(group) if p.get("chunk_id") == chunk.get("chunk_id")),
            None,
        )
        if anchor is None:
            return False
        return len(self._contiguous_run(group, anchor)) > 1

    @staticmethod
    def _contiguous_run(parts: list[dict], anchor: int) -> list[dict]:
        """The maximal run of genuinely adjacent sub-chunks around `anchor`.

        A clause split by the 512-character cap yields consecutive chunk_index
        values on the same or the next page. Sharing an (article, khoan) label
        proves much less: in decisions that carry several appendices the
        numbering restarts, so the parser files unrelated passages — a form
        header on page 3, a clause on page 15, a letterhead on page 19 — under
        one key. Concatenating those produces a collage, not a provision.
        Requiring adjacency keeps real splits and rejects the collisions.
        """
        run = [anchor]
        i = anchor - 1
        while i >= 0 and ClauseIndex._adjacent(parts[i], parts[i + 1]):
            run.insert(0, i)
            i -= 1
        i = anchor + 1
        while i < len(parts) and ClauseIndex._adjacent(parts[i - 1], parts[i]):
            run.append(i)
            i += 1
        return [parts[i] for i in run]

    # A clause never runs on past one of these: each opens a new document
    # section, so text after it belongs to an appendix or a form, not to the
    # provision being quoted. The parser does not always break there — several
    # appendices in this corpus inherit the Điều/Khoản label of whatever
    # preceded them — so assembly has to stop on its own.
    _SECTION_BREAK = (
        "phụ lục",
        "cộng hòa xã hội",
        "đại học quốc gia",
        "kính gửi",
        "đơn đề nghị",
        "mẫu số",
        "biểu mẫu",
    )

    @classmethod
    def _starts_new_section(cls, chunk: dict) -> bool:
        head = (chunk.get("text") or "").strip().lower()[:60]
        return any(head.startswith(marker) for marker in cls._SECTION_BREAK)

    @classmethod
    def _adjacent(cls, first: dict, second: dict) -> bool:
        idx_a, idx_b = first.get("chunk_index"), second.get("chunk_index")
        if idx_a is None or idx_b is None or idx_b - idx_a != 1:
            return False
        page_a, page_b = first.get("page") or 0, second.get("page") or 0
        if not 0 <= page_b - page_a <= 1:
            return False
        return not cls._starts_new_section(second)

    def expand(self, doc: dict) -> dict[str, Any]:
        """Return `doc` with its text replaced by the full clause.

        Adds bookkeeping the synthesis layer and the evaluation can read:
          _assembled     the text was reassembled from several sub-chunks
          _parts_used    how many sub-chunks the quoted text covers
          _parts_total   how many the clause has in total
          _partial       the cap was hit, so this is a window, not the whole clause
        """
        key = self._key(doc)
        if key is None:
            return doc

        group = self._parts.get(key, [])
        if len(group) <= 1:
            return doc

        anchor_idx = next(
            (i for i, p in enumerate(group) if p.get("chunk_id") == doc.get("chunk_id")),
            None,
        )
        if anchor_idx is None:
            return doc

        parts = self._contiguous_run(group, anchor_idx)
        if len(parts) <= 1:
            # The label is shared but the text is not continuous — quote as retrieved.
            return doc
        if self._starts_new_section(parts[0]):
            # The run opens on an appendix or form header, so it is a document
            # section wearing a clause's label, not a clause. Assembling it
            # would prepend letterhead and blank form fields to the answer.
            return doc

        texts = [(p.get("text") or "").strip() for p in parts]
        total_chars = sum(len(t) for t in texts) + 2 * (len(texts) - 1)

        out = dict(doc)
        out["_parts_total"] = len(parts)
        # Identifies the specific run, so two runs that share an (article,
        # khoan) label are not mistaken for each other during deduplication.
        out["_run_id"] = parts[0].get("chunk_id")

        if total_chars <= self.max_chars:
            out["text"] = "\n\n".join(t for t in texts if t)
            out["_assembled"] = True
            out["_parts_used"] = len(parts)
            out["_partial"] = False
            return out

        # Too large to quote whole — keep a window centred on what was retrieved.
        anchor = next(
            (i for i, p in enumerate(parts) if p.get("chunk_id") == doc.get("chunk_id")),
            0,
        )
        selected: list[int] = [anchor]
        budget = self.max_chars - len(texts[anchor])
        low, high = anchor - 1, anchor + 1
        while budget > 0 and (low >= 0 or high < len(parts)):
            if low >= 0 and len(texts[low]) <= budget:
                selected.insert(0, low)
                budget -= len(texts[low]) + 2
                low -= 1
            elif high < len(parts) and len(texts[high]) <= budget:
                selected.append(high)
                budget -= len(texts[high]) + 2
                high += 1
            else:
                break

        out["text"] = "\n\n".join(texts[i] for i in selected if texts[i])
        out["_assembled"] = True
        out["_parts_used"] = len(selected)
        out["_partial"] = True
        return out

    def expand_all(self, docs: list[dict]) -> list[dict]:
        """Expand each doc, then drop duplicates that collapsed onto one clause.

        Two sub-chunks of the same Khoản both expand to identical text; without
        this the answer would quote the same provision twice.
        """
        seen: set[str] = set()
        out: list[dict] = []
        for doc in docs:
            expanded = self.expand(doc)
            run_id = expanded.get("_run_id")
            if expanded.get("_assembled") and run_id:
                if run_id in seen:
                    continue
                seen.add(run_id)
            out.append(expanded)
        return out

    def stats(self) -> dict[str, int]:
        split = [p for p in self._parts.values() if len(p) > 1]
        return {
            "clause_units": len(self._parts),
            "split_units": len(split),
            "max_parts": max((len(p) for p in split), default=0),
            "chunks_indexed": len(self._by_id),
        }
