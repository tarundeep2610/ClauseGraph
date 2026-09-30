"""One-off comparison tool: re-runs AI structure-indexing on a copy of an
already-OCR'd job using a different AI_INDEX_* provider, then diffs the
resulting headings/structure against the original job's own (already
persisted) indexing run.

Built to compare Gemini (the provider these jobs were originally indexed
with) against Qwen running locally via Ollama, without touching the live
app or its already-indexed jobs: each source job is duplicated (PDF file +
ocr_pages OCR text only — never ocr_reference_targets/ai_index_report, so
the duplicate starts with zero indexing state), and only the DUPLICATE is
re-indexed with whatever provider this process's environment names.

Must run inside the CPU app container (needs `config`/`database`/
`ai_indexing`/`ai_reference_index`/`reference_index` importable, and
DATA_DIR pointed at the real job data) — e.g.:

    docker cp scripts/compare_ai_indexing_qwen_vs_gemini.py \\
        ocr-services-glm-ocr-api-1:/app/scripts/compare_ai_indexing_qwen_vs_gemini.py
    docker exec \\
        -e AI_INDEX_PROVIDER=openai_compatible \\
        -e AI_INDEX_MODEL=qwen2.5:7b \\
        -e AI_INDEX_BASE_URL=http://127.0.0.1:11434/v1 \\
        -e AI_INDEX_API_KEY=ollama \\
        ocr-services-glm-ocr-api-1 \\
        python3 /app/scripts/compare_ai_indexing_qwen_vs_gemini.py \\
        --chunk-pages 4 --compact-prompt --run-tag my-experiment \\
        8b47d00a-9828-46ad-9fb8-f1a0e2b17d7a \\
        770f2802-bd20-4bd1-9703-204336201a99 \\
        b6a06baf-a4a9-4285-aa4b-c66b69bbf4ec

The env vars above are read by config.py at import time, so they must be
set on THIS process (via `docker exec -e ...`) — never by mutating
os.environ after import, and never by touching the live app container's
own environment.

--chunk-pages/--compact-prompt/--run-tag are all optional and independent:
omitting all three reproduces today's default-prompt, default-chunk-size
Qwen run exactly, for an apples-to-apples baseline.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone

import config
import database

BASE_REPORT_DIR = os.path.join(database.DATA_DIR, "qwen_comparison")


def _assert_target_provider_configured() -> None:
    if config.AI_INDEX_PROVIDER == "gemini" or not config.AI_INDEX_API_KEY:
        raise SystemExit(
            "Refusing to run: AI_INDEX_PROVIDER/AI_INDEX_MODEL/AI_INDEX_BASE_URL/"
            "AI_INDEX_API_KEY must be set on THIS process's environment (via "
            "`docker exec -e ...`) to point at the comparison target (e.g. Qwen "
            "via Ollama), not left at the live app's Gemini defaults. Currently: "
            f"provider={config.AI_INDEX_PROVIDER!r} model={config.AI_INDEX_MODEL!r} "
            f"base_url={config.AI_INDEX_BASE_URL!r} api_key_set={bool(config.AI_INDEX_API_KEY)}"
        )


def _copy_columns(cursor: sqlite3.Cursor, table: str, job_id: str) -> "list[dict]":
    cursor.execute(f"SELECT * FROM {table} WHERE job_id = ?", (job_id,))
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def duplicate_job(
    job_id: str, chunk_pages: "int | None" = None, pleading_points_via_ai: "bool | None" = None,
) -> str:
    """Copies the job's ocr_jobs row + uploaded PDF + all ocr_pages rows
    under a fresh job_id, resetting every indexing-related field. Does NOT
    copy ocr_reference_targets or ocr_page_attempts — the duplicate starts
    with zero indexing state so re-indexing it never touches the original.

    `chunk_pages`, when given, is written into the duplicate's own
    engine_params["ai_index_chunk_pages"] — reindex_job_with_ai() already
    honors that per-job override (ai_reference_index.py), so this needs no
    other code changes to try a smaller/larger chunk size.

    `pleading_points_via_ai`, when given, is written into
    engine_params["ai_index_pleading_points_via_ai"] — Round 10's opt-in
    full-AI-handoff test flag (reindex_job_with_ai already honors it)."""
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    job_rows = _copy_columns(cursor, "ocr_jobs", job_id)
    if not job_rows:
        conn.close()
        raise ValueError(f"No job found with job_id={job_id!r}")
    job = job_rows[0]

    page_rows = _copy_columns(cursor, "ocr_pages", job_id)

    new_job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    src_path = job["file_path"]
    ext = os.path.splitext(src_path)[1]
    new_filename = f"{job['filename']} [qwen-test]"
    new_file_path = os.path.join(database.UPLOADS_DIR, f"{new_job_id}_{new_filename}{ext}")
    shutil.copy2(src_path, new_file_path)

    job["job_id"] = new_job_id
    job["filename"] = new_filename
    job["file_path"] = new_file_path
    job["created_at"] = now
    job["updated_at"] = now
    job["ai_index_report"] = "{}"
    job["reference_index_version"] = 0
    job["reference_index_frontier"] = 0
    if chunk_pages is not None or pleading_points_via_ai is not None:
        try:
            engine_params = json.loads(job.get("engine_params") or "{}")
        except Exception:
            engine_params = {}
        if chunk_pages is not None:
            engine_params["ai_index_chunk_pages"] = chunk_pages
        if pleading_points_via_ai is not None:
            engine_params["ai_index_pleading_points_via_ai"] = pleading_points_via_ai
        job["engine_params"] = json.dumps(engine_params)

    job_cols = list(job.keys())
    placeholders = ", ".join("?" for _ in job_cols)
    cursor.execute(
        f"INSERT INTO ocr_jobs ({', '.join(job_cols)}) VALUES ({placeholders})",
        [job[c] for c in job_cols],
    )

    for page in page_rows:
        page["job_id"] = new_job_id
        page["reference_index_touched_version"] = 0
        page_cols = list(page.keys())
        placeholders = ", ".join("?" for _ in page_cols)
        cursor.execute(
            f"INSERT INTO ocr_pages ({', '.join(page_cols)}) VALUES ({placeholders})",
            [page[c] for c in page_cols],
        )

    conn.commit()
    conn.close()
    return new_job_id


def apply_compact_prompt() -> None:
    """Monkeypatches ai_indexing.prompt's two module-level prompt constants
    IN THIS PROCESS ONLY. build_structure_prompt() (ai_indexing/prompt.py)
    reads these as globals on every call, so this affects every chunk call
    this script makes from here on — without editing prompt.py on disk and
    without any effect on the live app (a separate, already-running process
    with its own already-imported copy of the module)."""
    import ai_indexing.prompt as prompt_mod
    from qwen_compact_prompt import COMPACT_OUTPUT_CONTRACT, COMPACT_SYSTEM_PROMPT

    prompt_mod.STRUCTURE_SYSTEM_PROMPT = COMPACT_SYSTEM_PROMPT
    prompt_mod.STRUCTURE_OUTPUT_CONTRACT = COMPACT_OUTPUT_CONTRACT


async def run_reindex(job_id: str) -> dict:
    import ai_reference_index  # deferred: must import AFTER env vars are in place

    return await ai_reference_index.reindex_job_with_ai(job_id)


def load_structure(job_id: str) -> dict:
    return database.get_reference_targets_since(job_id, since_version=-1)


_WS_RE = re.compile(r"\s+")


def _heading_list(structure: dict) -> "list[tuple[int, str]]":
    """Flattens {page: {key: [candidate,...]}} into an ordered
    [(page_num, normalized_label), ...] list for a simple page-scoped diff."""
    out = []
    for page_num in sorted(structure.keys()):
        by_key = structure[page_num]
        for key, candidates in by_key.items():
            for cand in candidates:
                label = cand.get("own_label") or cand.get("scope_label") or key
                label = _WS_RE.sub(" ", (label or "").strip())
                if label:
                    out.append((page_num, label))
    return out


def build_report(
    source_job_id: str, qwen_job_id: str,
    gemini_structure: dict, qwen_structure: dict,
    gemini_report: dict, qwen_report: dict,
) -> "tuple[str, dict]":
    gemini_headings = _heading_list(gemini_structure)
    qwen_headings = _heading_list(qwen_structure)

    gemini_by_page: "dict[int, set]" = {}
    for page, label in gemini_headings:
        gemini_by_page.setdefault(page, set()).add(label)
    qwen_by_page: "dict[int, set]" = {}
    for page, label in qwen_headings:
        qwen_by_page.setdefault(page, set()).add(label)

    all_pages = sorted(set(gemini_by_page) | set(qwen_by_page))
    matched = missing_in_qwen = extra_in_qwen = 0
    lines = []
    lines.append(f"# Gemini vs. Qwen — source job `{source_job_id}`, qwen-copy job `{qwen_job_id}`\n")
    lines.append(f"- Gemini model: `{gemini_report.get('model')}` — status: `{gemini_report.get('status')}`")
    lines.append(f"- Qwen model: `{qwen_report.get('model')}` — status: `{qwen_report.get('status')}`")
    qwen_fallbacks = sum(1 for c in (qwen_report.get("chunks") or []) if c.get("status") == "fallback")
    lines.append(f"- Qwen chunks that fell back to the rule-based detector: {qwen_fallbacks}/{len(qwen_report.get('chunks') or [])}")
    lines.append(f"- Total headings — Gemini: {len(gemini_headings)}, Qwen: {len(qwen_headings)}\n")

    for page in all_pages:
        g = gemini_by_page.get(page, set())
        q = qwen_by_page.get(page, set())
        both = g & q
        only_g = g - q
        only_q = q - g
        matched += len(both)
        missing_in_qwen += len(only_g)
        extra_in_qwen += len(only_q)
        if not (g or q):
            continue
        lines.append(f"## Page {page}")
        for label in sorted(both):
            lines.append(f"- [x] {label}")
        for label in sorted(only_g):
            lines.append(f"- [ ] **Gemini only** — {label}")
        for label in sorted(only_q):
            lines.append(f"- [+] **Qwen only** — {label}")
        lines.append("")

    lines.insert(6, (
        f"- Matched: {matched}  |  Gemini-only (Qwen missed): {missing_in_qwen}  |  "
        f"Qwen-only (Qwen added): {extra_in_qwen}\n"
    ))

    json_obj = {
        "source_job_id": source_job_id,
        "qwen_job_id": qwen_job_id,
        "gemini_report": gemini_report,
        "qwen_report": qwen_report,
        "gemini_structure": gemini_structure,
        "qwen_structure": qwen_structure,
        "summary": {
            "gemini_heading_count": len(gemini_headings),
            "qwen_heading_count": len(qwen_headings),
            "matched": matched,
            "missing_in_qwen": missing_in_qwen,
            "extra_in_qwen": extra_in_qwen,
        },
    }
    return "\n".join(lines), json_obj


async def process_job(
    source_job_id: str, report_dir: str, chunk_pages: "int | None" = None,
    no_duplicate: bool = False, gemini_baseline_job_id: "str | None" = None,
) -> None:
    if no_duplicate:
        qwen_job_id = source_job_id
        print(f"[{source_job_id}] --no-duplicate: reindexing this job directly "
              f"(overwrites its existing ocr_reference_targets/ai_index_report)...")
    else:
        print(f"[{source_job_id}] duplicating job (PDF + OCR text only)...")
        qwen_job_id = duplicate_job(source_job_id, chunk_pages=chunk_pages)
        print(f"[{source_job_id}] duplicate created: {qwen_job_id}")

    print(f"[{qwen_job_id}] running AI reindex with provider={config.AI_INDEX_PROVIDER} model={config.AI_INDEX_MODEL}...")
    qwen_result = await run_reindex(qwen_job_id)
    print(f"[{qwen_job_id}] reindex result: {qwen_result}")

    gemini_job_id = gemini_baseline_job_id or source_job_id
    gemini_structure = load_structure(gemini_job_id)
    qwen_structure = load_structure(qwen_job_id)
    gemini_report = database.get_ai_index_report(gemini_job_id)
    qwen_report = database.get_ai_index_report(qwen_job_id)

    md, js = build_report(gemini_job_id, qwen_job_id, gemini_structure, qwen_structure, gemini_report, qwen_report)

    os.makedirs(report_dir, exist_ok=True)
    model_tag = re.sub(r"[^A-Za-z0-9._-]+", "-", config.AI_INDEX_MODEL)
    md_path = os.path.join(report_dir, f"{source_job_id}_vs_{model_tag}.md")
    json_path = os.path.join(report_dir, f"{source_job_id}_vs_{model_tag}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(js, f, indent=2)
    print(f"[{source_job_id}] wrote {md_path} and {json_path}")


def _derive_run_tag(chunk_pages: "int | None", compact_prompt: bool) -> str:
    model_tag = re.sub(r"[^A-Za-z0-9._-]+", "-", config.AI_INDEX_MODEL)
    chunk_part = f"chunk{chunk_pages}" if chunk_pages is not None else "chunk-default"
    prompt_part = "compact-prompt" if compact_prompt else "default-prompt"
    return f"{model_tag}_{chunk_part}_{prompt_part}"


async def main(
    job_ids: "list[str]", chunk_pages: "int | None", compact_prompt: bool, run_tag: "str | None",
    no_duplicate: bool = False, gemini_baseline: "str | None" = None,
) -> None:
    _assert_target_provider_configured()
    if compact_prompt:
        apply_compact_prompt()
    tag = run_tag or _derive_run_tag(chunk_pages, compact_prompt)
    report_dir = os.path.join(BASE_REPORT_DIR, tag)
    print(f"Run tag: {tag} -> reports under {report_dir}")
    for job_id in job_ids:
        await process_job(
            job_id, report_dir, chunk_pages=chunk_pages,
            no_duplicate=no_duplicate, gemini_baseline_job_id=gemini_baseline,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_ids", nargs="*", default=[
        "8b47d00a-9828-46ad-9fb8-f1a0e2b17d7a",  # 100-page
        "770f2802-bd20-4bd1-9703-204336201a99",  # 17-page
        "b6a06baf-a4a9-4285-aa4b-c66b69bbf4ec",  # 6-page
    ])
    parser.add_argument("--chunk-pages", type=int, default=None,
                         help="Override ai_index_chunk_pages on the duplicate job (default: config.AI_INDEX_CHUNK_PAGES).")
    parser.add_argument("--compact-prompt", action="store_true",
                         help="Use the condensed few-shot prompt from qwen_compact_prompt.py instead of the default.")
    parser.add_argument("--run-tag", type=str, default=None,
                         help="Subfolder name under qwen_comparison/ for this run's reports (default: auto-derived).")
    parser.add_argument("--no-duplicate", action="store_true",
                         help="Skip duplicate_job() and reindex the given job_id(s) directly — OVERWRITES their "
                              "existing ocr_reference_targets/ai_index_report. Use for a job that's already a "
                              "throwaway/fresh test upload, not a job you want preserved.")
    parser.add_argument("--gemini-baseline", type=str, default=None,
                         help="job_id to source the Gemini side of the comparison from, instead of the job(s) "
                              "being reindexed — useful with --no-duplicate when the target job's own Gemini "
                              "data is missing/partial/stale.")
    args = parser.parse_args()
    asyncio.run(main(
        args.job_ids, args.chunk_pages, args.compact_prompt, args.run_tag,
        no_duplicate=args.no_duplicate, gemini_baseline=args.gemini_baseline,
    ))
