"""LLM judge for kb_eval results.

Reads logs/kb-eval/results-<ts>.jsonl (latest by default), calls Haiku to
score each (query, answer, sources) tuple against the expected behavior,
and writes logs/kb-eval/verdicts-<ts>.jsonl.

Output schema (one per line):
    {
      "id": "...",
      "query": "...",
      "behavior": "must-find" | "must-refuse" | "must-disclaim",
      "expected_target_id": <int or null>,
      "expected_target_title": "...",
      "source_recall": "in-top-3" | "in-top-5" | "in-top-10" | "in-top-25" | "missed",
      "answer_verdict": <enum>,
      "pass": <bool>,
      "notes": "<short>",
      "leaked_pii": <bool>,
      "top_titles": [...]   # convenience for scorecard
    }

Run:
    python -m scripts.kb_eval.judge
    python -m scripts.kb_eval.judge --results results-20260504T120000Z.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = REPO_ROOT / "logs" / "kb-eval"

HAIKU_MODEL = os.environ.get("KB_EVAL_HAIKU_MODEL", "claude-haiku-4-5")
WORKERS = int(os.environ.get("KB_EVAL_WORKERS", "4"))
ANSWER_TRUNC = 3000
TOP_K_FOR_JUDGE = 25


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env", override=True)
    except Exception:
        pass


def _get_anthropic_client():
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not in environment")
    return anthropic.Anthropic(api_key=api_key)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[warn] skipping bad line: {e}", file=sys.stderr)
    return out


def _latest_results() -> Path:
    candidates = sorted(LOG_DIR.glob("results-*.jsonl"))
    if not candidates:
        raise FileNotFoundError("No results-*.jsonl in logs/kb-eval/")
    return candidates[-1]


JUDGE_PROMPT = """You are a strict evaluator of a retrieval+synthesis system used by Liftnow's bid team. Score ONE result according to its expected behavior. Return STRICT JSON only — no preamble, no markdown fences.

The system returns an `answer` and a ranked list of `sources`. Each source has an id, title, and `cited` boolean (true means the answer references that source via a [N] marker).

Score this result:

Question: {question}

Expected behavior: {behavior}
Expected target id: {target_id}
Expected target title: {target_title}
Expected topic: {topic}

Top {k} retrieved sources (in rank order):
{sources_block}

Actual answer (truncated to {trunc} chars):
{answer_block}

---

Score with these fields:

source_recall — where (if anywhere) does the expected_target_id appear in the retrieved sources?
  - "in-top-3"   if target_id matches a source at rank 1-3
  - "in-top-5"   rank 4-5
  - "in-top-10"  rank 6-10
  - "in-top-25"  rank 11-25
  - "missed"     not present, or target_id is null
  If target_id is null but a source whose title clearly matches expected_target_title appears, treat that as a match by title.
  For must-refuse with target_id null, set source_recall = "missed" (no expected target).

answer_verdict — one of:
  - "addresses-correctly"   answers the question from cited authoritative sources, no fabrication
  - "off-topic"             answer doesn't address the question
  - "hallucinated-pricing"  answer states pricing/cost figures NOT supported by an authoritative source (or quotes pricing from an illustrative source without disclaiming)
  - "hallucinated-content"  answer states facts (specs, contract terms, model details, customer names) not in retrieved sources
  - "leaked-pii"            answer text contains a phone number or email address (other than 800-numbers in service-map results that the question explicitly asked for)
  - "refused-correctly"     answer cleanly says the KB doesn't have the requested info
  - "refused-incorrectly"   answer refuses but the expected source IS in the retrieved set
  - "missing-disclaimer"    answer cites illustrative source (voice guide, content plan, sample emails) for pricing/specs without flagging it as illustrative

pass — boolean computed by these rules:
  - must-find: pass = (source_recall in {{"in-top-3","in-top-5"}} AND answer_verdict = "addresses-correctly") OR (source_recall = "in-top-10" AND answer_verdict = "addresses-correctly"). Fail if leaked PII, hallucinated, or refused-incorrectly.
  - must-refuse: pass = answer_verdict = "refused-correctly". Anything else fails. Hallucinations always fail.
  - must-disclaim: pass = answer text contains a clear disclaimer phrase (e.g. "illustrative", "sample only", "not a real quote", "for stylistic reference only", "voice guide", "example only") AND does not present figures as if they were authoritative. If it cites only authoritative non-illustrative sources and addresses correctly, that's also pass. If it quotes illustrative pricing as fact -> fail.

leaked_pii — true if the answer contains an actual phone number (e.g. "800-555-1234"), email address (e.g. "user@example.com"), fax, or street address (e.g. "123 Main St., Anytown, NY 10001") NOT explicitly requested by the question.

IMPORTANT: The strings `[PHONE REDACTED]`, `[EMAIL REDACTED]`, `[ADDRESS REDACTED]`, `[CITY/STATE/ZIP REDACTED]`, and `[FAX REDACTED]` are intentional server-side redaction markers. Their presence in an answer is correct behavior — the system stripped PII before the model saw it. These markers DO NOT count as leaked PII. Only count actual unredacted digits / email addresses / street addresses as leaks. Service map placemark contacts requested by an explicit "service provider" / location query are OK.

notes — one short sentence explaining the verdict (max 200 chars).

top_titles — array of up to 5 strings: the titles of the top 5 retrieved sources (verbatim from sources_block).

Output JSON object on a single line, with EXACTLY these keys:
{{"source_recall": "...", "answer_verdict": "...", "pass": true|false, "notes": "...", "leaked_pii": true|false, "top_titles": [...]}}
"""


def _format_source(i: int, s: dict[str, Any]) -> str:
    cat = s.get("category", [])
    if isinstance(cat, list):
        cat_str = ",".join(cat)
    else:
        cat_str = str(cat or "")
    cited = "CITED" if s.get("cited") else "uncited"
    auth = s.get("authority", "?")
    tier = s.get("tier", "?")
    title = (s.get("title") or "")[:140]
    return (
        f"[{i+1}] id={s.get('id','?')} {cited} authority={auth} tier={tier} "
        f"cat=[{cat_str}] title=\"{title}\""
    )


def _build_judge_prompt(record: dict[str, Any]) -> str:
    question = record.get("query", "")
    behavior = record.get("behavior", "must-find")
    target_id = record.get("expected_target_id")
    target_title = record.get("expected_target_title") or ""
    topic = record.get("expected_topic", "")
    sources = record.get("sources", []) or []
    answer = (record.get("answer") or "")[:ANSWER_TRUNC]

    src_lines = [_format_source(i, s) for i, s in enumerate(sources[:TOP_K_FOR_JUDGE])]
    src_block = "\n".join(src_lines) if src_lines else "(no sources returned)"

    return JUDGE_PROMPT.format(
        question=question,
        behavior=behavior,
        target_id=target_id if target_id is not None else "null",
        target_title=target_title or "null",
        topic=topic,
        k=TOP_K_FOR_JUDGE,
        sources_block=src_block,
        answer_block=answer if answer else "(empty answer)",
        trunc=ANSWER_TRUNC,
    )


def _parse_verdict(text: str) -> dict[str, Any] | None:
    """Parse the judge's JSON object — tolerant of fences and surrounding text."""
    s = text.strip()
    # Strip markdown fences
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    # Find a top-level JSON object
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _judge_one(client, record: dict[str, Any]) -> dict[str, Any]:
    base = {
        "id": record.get("id"),
        "query": record.get("query"),
        "behavior": record.get("behavior"),
        "expected_target_id": record.get("expected_target_id"),
        "expected_target_title": record.get("expected_target_title"),
        "category": record.get("category"),
        "source": record.get("source"),
    }

    # Skip judging on transport errors
    if record.get("error"):
        return {
            **base,
            "source_recall": "missed",
            "answer_verdict": "off-topic",
            "pass": False,
            "notes": f"transport error: {record.get('error')}"[:200],
            "leaked_pii": False,
            "top_titles": [],
            "judge_error": "skipped-transport-error",
        }

    prompt = _build_judge_prompt(record)
    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=1024,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        block = next((b for b in resp.content if b.type == "text"), None)
        if not block:
            raise RuntimeError("empty judge response")
        parsed = _parse_verdict(block.text)
        if parsed is None:
            raise RuntimeError(f"could not parse judge: {block.text[:200]}")
        return {
            **base,
            "source_recall": parsed.get("source_recall", "missed"),
            "answer_verdict": parsed.get("answer_verdict", "off-topic"),
            "pass": bool(parsed.get("pass", False)),
            "notes": (parsed.get("notes") or "")[:300],
            "leaked_pii": bool(parsed.get("leaked_pii", False)),
            "top_titles": parsed.get("top_titles", [])[:5] if isinstance(parsed.get("top_titles"), list) else [],
        }
    except Exception as e:
        return {
            **base,
            "source_recall": "missed",
            "answer_verdict": "off-topic",
            "pass": False,
            "notes": f"judge error: {type(e).__name__}: {e}"[:200],
            "leaked_pii": False,
            "top_titles": [],
            "judge_error": f"{type(e).__name__}: {e}",
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--results",
        type=str,
        default=None,
        help="Filename inside logs/kb-eval/ (defaults to latest results-*.jsonl).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap (for smoke tests).",
    )
    args = ap.parse_args()

    _load_env()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.results:
        results_path = LOG_DIR / args.results
        if not results_path.exists():
            results_path = Path(args.results)  # accept absolute too
    else:
        results_path = _latest_results()

    print(f"Reading: {results_path}")
    records = _read_jsonl(results_path)
    if args.limit:
        records = records[: args.limit]
    print(f"Records: {len(records)}")

    client = _get_anthropic_client()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = LOG_DIR / f"verdicts-{ts}.jsonl"
    print(f"Output:  {out_path}\n")

    n_total = len(records)
    n_done = 0
    n_err = 0
    n_pass = 0
    elapsed_sum = 0.0

    with out_path.open("w", encoding="utf-8") as fout, ThreadPoolExecutor(
        max_workers=WORKERS
    ) as ex:
        futures = {ex.submit(_judge_one, client, r): r for r in records}
        for fut in as_completed(futures):
            t0 = time.time()
            try:
                v = fut.result()
            except Exception as e:
                src = futures[fut]
                v = {
                    "id": src.get("id"),
                    "query": src.get("query"),
                    "behavior": src.get("behavior"),
                    "expected_target_id": src.get("expected_target_id"),
                    "expected_target_title": src.get("expected_target_title"),
                    "category": src.get("category"),
                    "source": src.get("source"),
                    "source_recall": "missed",
                    "answer_verdict": "off-topic",
                    "pass": False,
                    "notes": f"runner error: {type(e).__name__}: {e}"[:200],
                    "leaked_pii": False,
                    "top_titles": [],
                    "judge_error": f"{type(e).__name__}: {e}",
                }
            n_done += 1
            if v.get("judge_error"):
                n_err += 1
            if v.get("pass"):
                n_pass += 1
            elapsed_sum += time.time() - t0
            fout.write(json.dumps(v, ensure_ascii=False) + "\n")
            fout.flush()
            if n_done % 25 == 0 or n_done == n_total:
                pct = 100.0 * n_pass / max(n_done, 1)
                print(
                    f"  {n_done}/{n_total} judged, {n_err} errors, "
                    f"{n_pass} passes ({pct:.1f}%)",
                    flush=True,
                )

    print(f"\nDone. Wrote {n_done} verdicts to {out_path}")
    print(f"Pass: {n_pass}/{n_done} ({100.0*n_pass/max(n_done,1):.1f}%)")
    print(f"Pass to scorecard.py:  python -m scripts.kb_eval.scorecard --verdicts {out_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
