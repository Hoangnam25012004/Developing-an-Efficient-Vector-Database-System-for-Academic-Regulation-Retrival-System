"""Keyboard-driven relevance judging, plus export to graded qrels and kappa.

Grades follow the scale used by TREC Web and NTCIR rather than a binary
relevant/not, because binary labels are what made the previous ground truth
uninformative: marking all 79 rows of a scoring table "relevant" for a question
answered by one row makes Hit Rate measure table routing, not row resolution.

  2  answers the question directly — quoting this unit alone would satisfy
     the asker
  1  supporting — a condition, exception, definition or procedure needed to
     apply the answer correctly, but not the answer itself
  0  not relevant — including units from the right document or the right
     table that do not bear on the question

Judgments are appended after every keystroke, so quitting mid-topic loses
nothing and the session resumes where it stopped.

  python eval/judge.py                 judge (resumes automatically)
  python eval/judge.py --stats         progress and grade distribution
  python eval/judge.py --export        write graded ground truth
  python eval/judge.py --kappa         inter-annotator agreement
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import textwrap
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

POOL = ROOT / "eval" / "pool.jsonl"
JUDGMENTS = ROOT / "eval" / "judgments.jsonl"
GT_OUT = ROOT / "eval" / "test_queries_gt_v2.jsonl"

GRADE_LABEL = {
    0: "0 không liên quan",
    1: "1 hỗ trợ",
    2: "2 trả lời trực tiếp",
}


# ── input ─────────────────────────────────────────────────────────────────────

def read_key() -> str:
    """One keystroke, without waiting for Enter, falling back to line input."""
    try:
        import msvcrt

        ch = msvcrt.getch()
        try:
            return ch.decode("utf-8", errors="ignore").lower()
        except Exception:
            return ""
    except ImportError:
        try:
            return (input("> ").strip() or " ")[0].lower()
        except EOFError:
            return "q"


# ── storage ───────────────────────────────────────────────────────────────────

def load_pool() -> list[dict]:
    if not POOL.exists():
        sys.exit(f"Pool not found: {POOL}\nRun:  python eval/build_pool.py")
    return [
        json.loads(line)
        for line in POOL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_judgments() -> dict[tuple, dict]:
    """Keyed by (topic_id, chunk_id, pass_no); later lines supersede earlier."""
    out: dict[tuple, dict] = {}
    if JUDGMENTS.exists():
        for line in JUDGMENTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[(rec["topic_id"], rec["chunk_id"], rec.get("pass_no", 1))] = rec
    return out


def append_judgment(rec: dict) -> None:
    with JUDGMENTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── display ───────────────────────────────────────────────────────────────────

def render(topic: dict, unit: dict, idx: int, total: int, done: int, overall: int,
           pass_no: int) -> None:
    width = min(shutil.get_terminal_size((100, 30)).columns, 100)
    bar = "═" * width
    print("\n" * 2 + bar)
    pass_tag = "  [PASS 2 — độc lập]" if pass_no == 2 else ""
    print(f"Câu hỏi {topic['topic_id']}{pass_tag}   |   đơn vị {idx}/{total}"
          f"   |   tổng {done}/{overall}")
    print(bar)
    print("\n".join(textwrap.wrap(f"HỎI: {topic['question']}", width)))

    if topic.get("reference_answer") and pass_no == 1:
        print("\n" + "\n".join(
            textwrap.wrap(f"(tham chiếu: {topic['reference_answer']})", width)
        ))

    print("-" * width)
    loc = unit.get("locator") or unit.get("section_type") or "?"
    page = unit.get("page")
    print(f"{loc}   |   {unit.get('source', '')}"
          + (f"   |   trang {page}" if page else ""))
    print("-" * width)

    text = " ".join((unit.get("text") or "").split())
    if len(text) > 1400:
        text = text[:1400] + " …"
    print("\n".join(textwrap.wrap(text, width)))
    print("-" * width)
    print("[2] trả lời trực tiếp   [1] hỗ trợ   [0] không   "
          "[b] lùi   [q] lưu & thoát")


# ── judging loop ──────────────────────────────────────────────────────────────

def run_judging() -> None:
    pool = load_pool()
    judged = load_judgments()

    # (topic, unit, pass_no) in the order they will be shown. Second passes run
    # after every first pass, so the earlier grade is well out of mind.
    queue: list[tuple[dict, dict, int]] = []
    for pass_no in (1, 2):
        for topic in pool:
            if pass_no == 2 and not topic.get("double_judge"):
                continue
            for unit in topic["units"]:
                queue.append((topic, unit, pass_no))

    overall = len(queue)
    pos = 0
    session_start = time.perf_counter()
    session_count = 0

    while pos < len(queue):
        topic, unit, pass_no = queue[pos]
        key = (topic["topic_id"], unit["chunk_id"], pass_no)
        if key in judged:
            pos += 1
            continue

        topic_units = topic["units"]
        idx = topic_units.index(unit) + 1
        render(topic, unit, idx, len(topic_units), len(judged), overall, pass_no)

        ch = read_key()
        if ch == "q":
            break
        if ch == "b":
            back = pos - 1
            while back >= 0:
                t, u, p = queue[back]
                k = (t["topic_id"], u["chunk_id"], p)
                if k in judged:
                    del judged[k]
                    append_judgment(
                        {"topic_id": t["topic_id"], "chunk_id": u["chunk_id"],
                         "pass_no": p, "grade": None, "undo": True,
                         "ts": time.time()}
                    )
                    pos = back
                    break
                back -= 1
            continue
        if ch not in ("0", "1", "2"):
            continue

        rec = {
            "topic_id": topic["topic_id"],
            "chunk_id": unit["chunk_id"],
            "pass_no": pass_no,
            "grade": int(ch),
            # Denormalised so qrels survive a re-chunk: chunk_id is a content
            # hash and changes whenever boundaries move.
            "source": unit.get("source", ""),
            "article": unit.get("article", ""),
            "khoan": unit.get("khoan", ""),
            "page": unit.get("page"),
            "ts": time.time(),
        }
        judged[key] = rec
        append_judgment(rec)
        session_count += 1
        pos += 1

    elapsed = time.perf_counter() - session_start
    print(f"\n\nĐã chấm {session_count} đơn vị trong phiên này "
          f"({elapsed/max(session_count,1):.1f}s/đơn vị).")
    print(f"Tổng tiến độ: {len(judged)}/{overall}")
    if len(judged) < overall:
        remaining = (overall - len(judged)) * (elapsed / max(session_count, 1))
        print(f"Còn lại ước tính ~{remaining/60:.0f} phút. Chạy lại để tiếp tục.")
    else:
        print("Hoàn tất. Chạy:  python eval/judge.py --export")


# ── reporting ─────────────────────────────────────────────────────────────────

def show_stats() -> None:
    pool = load_pool()
    judged = {k: v for k, v in load_judgments().items() if v.get("grade") is not None}
    total = sum(len(t["units"]) for t in pool)
    total += sum(len(t["units"]) for t in pool if t.get("double_judge"))

    first = [v for k, v in judged.items() if k[2] == 1]
    grades = Counter(v["grade"] for v in first)
    topics_done = len({k[0] for k, v in judged.items() if k[2] == 1})

    print(f"Judged      {len(judged)}/{total}")
    print(f"Topics seen {topics_done}/{len(pool)}")
    print("\nGrade distribution (pass 1):")
    for g in (2, 1, 0):
        n = grades.get(g, 0)
        pct = 100 * n / len(first) if first else 0
        print(f"  {GRADE_LABEL[g]:<24} {n:>5}  {pct:5.1f}%")

    per_topic = Counter()
    for v in first:
        if v["grade"] >= 1:
            per_topic[v["topic_id"]] += 1
    if per_topic:
        vals = list(per_topic.values())
        print(f"\nRelevant units per judged topic: mean {sum(vals)/len(vals):.2f}, "
              f"max {max(vals)}")
        print("(the previous ground truth averaged 29.8 — that was the problem)")


def cohens_kappa() -> None:
    judged = {k: v for k, v in load_judgments().items() if v.get("grade") is not None}
    pass1 = {(k[0], k[1]): v["grade"] for k, v in judged.items() if k[2] == 1}
    pass2 = {(k[0], k[1]): v["grade"] for k, v in judged.items() if k[2] == 2}
    shared = sorted(set(pass1) & set(pass2))

    if not shared:
        print("No doubly-judged units yet — finish pass 2 first.")
        return

    a = [pass1[k] for k in shared]
    b = [pass2[k] for k in shared]

    def kappa(x: list[int], y: list[int], labels: tuple[int, ...]) -> float:
        n = len(x)
        po = sum(1 for i, j in zip(x, y) if i == j) / n
        cx, cy = Counter(x), Counter(y)
        pe = sum((cx.get(l, 0) / n) * (cy.get(l, 0) / n) for l in labels)
        return (po - pe) / (1 - pe) if pe < 1 else 1.0

    k3 = kappa(a, b, (0, 1, 2))
    ab = [1 if g >= 1 else 0 for g in a]
    bb = [1 if g >= 1 else 0 for g in b]
    k2 = kappa(ab, bb, (0, 1))
    agree = sum(1 for i, j in zip(a, b) if i == j) / len(a)

    print(f"Doubly judged units: {len(shared)}")
    print(f"Raw agreement      : {agree:.3f}")
    print(f"Cohen's kappa (0/1/2): {k3:.3f}")
    print(f"Cohen's kappa (binary): {k2:.3f}")
    print("\nLandis & Koch: >0.60 substantial, >0.80 almost perfect.")
    print("Voorhees (2000): assessors disagree, yet system *rankings* stay")
    print("stable — consistency matters more than a perfect kappa.")


def export_gt() -> None:
    pool = load_pool()
    judged = {k: v for k, v in load_judgments().items() if v.get("grade") is not None}
    by_topic: dict[int, dict[str, dict]] = {}
    for (topic_id, chunk_id, pass_no), rec in judged.items():
        if pass_no == 1:
            by_topic.setdefault(topic_id, {})[chunk_id] = rec

    rows, skipped = [], 0
    for topic in pool:
        graded = by_topic.get(topic["topic_id"], {})
        if not graded:
            skipped += 1
            continue
        qrels = [
            {
                "chunk_id": cid,
                "source": rec.get("source", ""),
                "article": rec.get("article", ""),
                "khoan": rec.get("khoan", ""),
                "page": rec.get("page"),
                "grade": rec["grade"],
            }
            for cid, rec in graded.items()
        ]
        qrels.sort(key=lambda q: -q["grade"])
        rows.append(
            {
                "id": topic["topic_id"],
                "question": topic["question"],
                # Kept for readability. It is a human paraphrase, so it is not a
                # scoring target for a system that quotes verbatim — measuring
                # n-gram overlap against it penalises correct extraction.
                "answer": topic.get("reference_answer", ""),
                "qrels": qrels,
                # Consumed by the existing evaluator unchanged.
                "relevant_ids": [q["chunk_id"] for q in qrels if q["grade"] >= 1],
                "primary_ids": [q["chunk_id"] for q in qrels if q["grade"] == 2],
                "n_pooled": len(topic["units"]),
                "unjudged_assumed_irrelevant": True,
            }
        )

    with GT_OUT.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    sizes = [len(r["relevant_ids"]) for r in rows]
    prim = [len(r["primary_ids"]) for r in rows]
    print(f"Exported {len(rows)} topics → {GT_OUT}")
    if skipped:
        print(f"  skipped {skipped} topics with no judgments yet")
    if sizes:
        print(f"  relevant (grade>=1) per topic: mean {sum(sizes)/len(sizes):.2f}")
        print(f"  primary  (grade==2) per topic: mean {sum(prim)/len(prim):.2f}")
        print(f"  topics with no relevant unit : {sum(1 for s in sizes if s == 0)}")
    print("\nEvaluate with:")
    print("  python eval/run_ablation.py --gt eval/test_queries_gt_v2.jsonl --tag v2")


def main() -> None:
    ap = argparse.ArgumentParser(description="Relevance judging tool")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--kappa", action="store_true")
    args = ap.parse_args()

    if args.stats:
        show_stats()
    elif args.kappa:
        cohens_kappa()
    elif args.export:
        export_gt()
    else:
        run_judging()


if __name__ == "__main__":
    main()
