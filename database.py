import sys
import sqlite3
import json
import os
import secrets
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "ocr_jobs.db")

# Additive columns on ocr_jobs, applied via ALTER TABLE ... ADD COLUMN so existing
# databases upgrade in place without a destructive migration. Keep in sync with the
# CREATE TABLE below (which only matters for a brand-new DB file).
_OCR_JOBS_NEW_COLUMNS = [
    ("chunk_size", "INTEGER DEFAULT 0"),          # 0 = use config.OCR_BATCH_SIZE at run time
    ("concurrency", "INTEGER DEFAULT 0"),          # 0 = use config default
    ("engine_params", "TEXT DEFAULT '{}'"),        # frozen per-job snapshot: engine hyperparams + resolved app-settings
    ("processing_mode", "TEXT DEFAULT 'single'"),  # 'batch' | 'single' (unlimited engine only; ignored for glm)
    ("batch_size", "INTEGER DEFAULT 5"),
    ("enable_overlap", "INTEGER DEFAULT 1"),       # batch-mode-only; ignored for single-page mode (never has overlap)
    ("escalate_before_flagging", "INTEGER DEFAULT 1"),
    ("succeeded_pages", "INTEGER DEFAULT 0"),
    ("review_pages", "INTEGER DEFAULT 0"),
    ("failed_pages", "INTEGER DEFAULT 0"),
    ("blank_pages", "INTEGER DEFAULT 0"),
    ("base_pass_completed_at", "TIMESTAMP"),
]


def init_db():
    """Initializes the SQLite database schema and creates necessary storage directories."""
    os.makedirs(UPLOADS_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_jobs (
            job_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT NOT NULL,
            engine TEXT NOT NULL,
            scale REAL NOT NULL,
            tiling BOOLEAN NOT NULL,
            prompt_mode TEXT,
            custom_prompt TEXT,
            status TEXT NOT NULL,
            total_pages INTEGER DEFAULT 0,
            processed_pages INTEGER DEFAULT 0,
            pages_data TEXT DEFAULT '[]',
            error_message TEXT DEFAULT '',
            page_range TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migrate existing databases that predate the page_range column
    try:
        cursor.execute("ALTER TABLE ocr_jobs ADD COLUMN page_range TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    for col_name, col_def in _OCR_JOBS_NEW_COLUMNS:
        try:
            cursor.execute(f"ALTER TABLE ocr_jobs ADD COLUMN {col_name} {col_def}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            key_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revoked_at TIMESTAMP,
            last_used_at TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_pages (
            job_id TEXT NOT NULL,
            page_num INTEGER NOT NULL,
            chunk_id INTEGER,
            is_boundary INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            text TEXT DEFAULT '',
            confidence REAL,
            needs_review INTEGER NOT NULL DEFAULT 0,
            review_reason TEXT DEFAULT '',
            signals TEXT DEFAULT '{}',
            attempts INTEGER NOT NULL DEFAULT 0,
            escalated INTEGER NOT NULL DEFAULT 0,
            source TEXT DEFAULT 'single',
            overlap_alt_text TEXT,
            primer_source_page INTEGER,
            duration_ms INTEGER,
            reviewed_by TEXT,
            reviewed_at TIMESTAMP,
            original_text TEXT,
            error TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, page_num)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ocr_pages_job_status ON ocr_pages(job_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ocr_pages_review ON ocr_pages(needs_review, job_id, page_num)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ocr_pages_job_chunk ON ocr_pages(job_id, chunk_id)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_page_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            page_num INTEGER NOT NULL,
            attempt_no INTEGER NOT NULL,
            escalation_level TEXT DEFAULT 'base',
            engine TEXT,
            scale REAL,
            tiling INTEGER,
            base_size INTEGER,
            batch_id INTEGER,
            timeout INTEGER,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            duration_ms INTEGER,
            outcome TEXT,
            error TEXT DEFAULT '',
            confidence REAL,
            signals TEXT DEFAULT '{}',
            text_len INTEGER,
            text TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_attempts_job_page ON ocr_page_attempts(job_id, page_num)")

    conn.commit()

    _migrate_pages_data_to_ocr_pages(conn)

    conn.commit()
    conn.close()


def _migrate_pages_data_to_ocr_pages(conn: sqlite3.Connection):
    """One-time, idempotent backfill of the legacy pages_data JSON blob into ocr_pages.

    Gated on schema_meta so it only actually scans once — init_db() runs on every
    new connection, so this must be cheap after the first successful run.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM schema_meta WHERE key = 'pages_migrated_v1'")
    row = cursor.fetchone()
    if row and row[0] == 'done':
        return

    cursor.execute("SELECT job_id, pages_data FROM ocr_jobs WHERE pages_data IS NOT NULL AND pages_data NOT IN ('', '[]')")
    rows = cursor.fetchall()
    for job_id, pages_data_raw in rows:
        try:
            legacy_pages = json.loads(pages_data_raw)
        except Exception:
            continue
        if not isinstance(legacy_pages, list):
            continue
        for p in legacy_pages:
            if not isinstance(p, dict) or 'page' not in p:
                continue
            page_num = p.get('page')
            legacy_status = p.get('status')
            text = p.get('text', '') or ''
            error = p.get('error', '') or ''
            if legacy_status == 'success':
                status = 'success'
                needs_review = 0
                review_reason = ''
            elif legacy_status == 'failed':
                status = 'failed'
                needs_review = 1
                review_reason = 'legacy_failed'
            else:
                status = legacy_status or 'pending'
                needs_review = 0
                review_reason = ''
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO ocr_pages (
                        job_id, page_num, status, text, confidence, needs_review,
                        review_reason, attempts, source, error
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, 1, 'single', ?)
                """, (job_id, page_num, status, text, needs_review, review_reason, error))
            except sqlite3.OperationalError:
                pass

    cursor.execute("""
        INSERT INTO schema_meta (key, value) VALUES ('pages_migrated_v1', 'done')
        ON CONFLICT(key) DO UPDATE SET value = 'done'
    """)


def get_db_connection():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if mode != "wal":
        print(f"[DB] Warning: WAL mode unavailable on this filesystem, using {mode!r}", file=sys.stderr)
    # NORMAL under WAL fsyncs at checkpoint time instead of on every commit — the
    # only risk is losing the most recent commits on an OS-level power-loss-equivalent
    # event, which is an accepted trade-off (see plan notes); it fully survives an
    # application crash. Kept off the WAL-unavailable path since it's meaningless there.
    if mode == "wal":
        conn.execute("PRAGMA synchronous=NORMAL")
    busy_timeout_ms = int(os.environ.get("OCR_DB_BUSY_TIMEOUT_MS", "5000"))
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


def checkpoint_wal(mode: str = "TRUNCATE"):
    """Best-effort WAL checkpoint — call on graceful shutdown, and periodically during
    long jobs, to bound how much uncommitted-to-main-file work a crash could cost."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(f"PRAGMA wal_checkpoint({mode})")
        conn.close()
    except Exception as e:
        print(f"[DB] Warning: wal_checkpoint({mode}) failed: {e}", file=sys.stderr)


def create_job(
    job_id: str,
    filename: str,
    file_path: str,
    file_type: str,
    engine: str = "unlimited",
    scale: float = 1.5,
    tiling: bool = True,
    prompt_mode: str = "document",
    custom_prompt: Optional[str] = None,
    total_pages: int = 1,
    page_range: str = "",
    chunk_size: int = 0,
    concurrency: int = 0,
    engine_params: Optional[Dict[str, Any]] = None,
    processing_mode: str = "single",
    batch_size: int = 5,
    enable_overlap: bool = True,
    escalate_before_flagging: bool = True,
) -> Dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute("""
        INSERT INTO ocr_jobs (
            job_id, filename, file_path, file_type, engine, scale, tiling,
            prompt_mode, custom_prompt, status, total_pages, processed_pages,
            pages_data, error_message, page_range, created_at, updated_at,
            chunk_size, concurrency, engine_params, processing_mode, batch_size,
            enable_overlap, escalate_before_flagging
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0, '[]', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id, filename, file_path, file_type, engine, scale, int(tiling),
        prompt_mode, custom_prompt or '', total_pages, page_range or '', now, now,
        chunk_size, concurrency, json.dumps(engine_params or {}), processing_mode, batch_size,
        int(enable_overlap), int(escalate_before_flagging),
    ))
    conn.commit()
    conn.close()
    return get_job(job_id)


def _row_to_job(row: sqlite3.Row, include_pages: bool = True) -> Dict[str, Any]:
    job = dict(row)
    job['tiling'] = bool(job['tiling'])
    job['escalate_before_flagging'] = bool(job.get('escalate_before_flagging', 1))
    job['enable_overlap'] = bool(job.get('enable_overlap', 1))
    try:
        job['engine_params'] = json.loads(job.get('engine_params') or '{}')
    except Exception:
        job['engine_params'] = {}
    if include_pages:
        pages = get_pages(job['job_id'])
        if pages:
            job['pages_data'] = pages
        else:
            # No normalized rows yet (e.g. brand new job) — fall back to the legacy blob.
            try:
                job['pages_data'] = json.loads(job['pages_data'])
            except Exception:
                job['pages_data'] = []
    else:
        job.pop('pages_data', None)
    return job


def get_job(job_id: str, include_pages: bool = True) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ocr_jobs WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_job(row, include_pages=include_pages)


def list_jobs(limit: int = 50, include_pages: bool = False) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ocr_jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [_row_to_job(r, include_pages=include_pages) for r in rows]


def update_job_progress(job_id: str, processed_pages: int, pages_data: List[Dict[str, Any]], status: str = "processing"):
    """Legacy dual-write path: still writes pages_data for backward compatibility with
    any code not yet reading through ocr_pages, but new code should prefer upsert_page()."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute("""
        UPDATE ocr_jobs
        SET processed_pages = ?, pages_data = ?, status = ?, updated_at = ?
        WHERE job_id = ?
    """, (processed_pages, json.dumps(pages_data), status, now, job_id))
    conn.commit()
    conn.close()


def mark_job_completed(job_id: str, pages_data: List[Dict[str, Any]]):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    processed = len([p for p in pages_data if p.get('status') == 'success'])
    cursor.execute("""
        UPDATE ocr_jobs
        SET status = 'completed', processed_pages = ?, pages_data = ?, updated_at = ?
        WHERE job_id = ?
    """, (processed, json.dumps(pages_data), now, job_id))
    conn.commit()
    conn.close()


def mark_job_paused(job_id: str, pages_data: List[Dict[str, Any]]):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    processed = len([p for p in pages_data if p.get('status') == 'success'])
    cursor.execute("""
        UPDATE ocr_jobs
        SET status = 'paused', processed_pages = ?, pages_data = ?, updated_at = ?
        WHERE job_id = ?
    """, (processed, json.dumps(pages_data), now, job_id))
    conn.commit()
    conn.close()


def mark_job_failed(job_id: str, error_message: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute("""
        UPDATE ocr_jobs
        SET status = 'failed', error_message = ?, updated_at = ?
        WHERE job_id = ?
    """, (error_message, now, job_id))
    conn.commit()
    conn.close()


def set_job_status(job_id: str, status: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    extra = ", base_pass_completed_at = ?" if status == 'escalating' else ""
    params = (status, now) + ((now,) if status == 'escalating' else ()) + (job_id,)
    cursor.execute(f"UPDATE ocr_jobs SET status = ?, updated_at = ?{extra} WHERE job_id = ?", params)
    conn.commit()
    conn.close()


def _count_pages_by_outcome(cursor, job_id: str) -> dict:
    """Shared aggregate-counting query behind refresh_job_page_counts and
    get_live_page_counts. processed_pages counts terminal statuses (success,
    review, failed, skipped) so the progress bar can reach 100% even with failures."""
    counts = {"success": 0, "review": 0, "failed": 0, "skipped": 0}
    cursor.execute("SELECT status, COUNT(*) FROM ocr_pages WHERE job_id = ? GROUP BY status", (job_id,))
    for status, n in cursor.fetchall():
        if status in counts:
            counts[status] = n
    cursor.execute("SELECT COUNT(*) FROM ocr_pages WHERE job_id = ? AND needs_review = 1", (job_id,))
    review_pages = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) FROM ocr_pages
        WHERE job_id = ? AND status = 'success' AND signals LIKE '%"blank_page": true%'
    """, (job_id,))
    blank_pages = cursor.fetchone()[0]
    processed = counts["success"] + counts["review"] + counts["failed"] + counts["skipped"]
    return {
        "processed_pages": processed,
        "succeeded_pages": counts["success"],
        "review_pages": review_pages,
        "failed_pages": counts["failed"],
        "blank_pages": blank_pages,
    }


def refresh_job_page_counts(job_id: str):
    """Recompute the per-status counters and processed_pages on ocr_jobs from the
    authoritative ocr_pages rows and persist them."""
    conn = get_db_connection()
    cursor = conn.cursor()
    counts = _count_pages_by_outcome(cursor, job_id)
    now = datetime.utcnow().isoformat()
    cursor.execute("""
        UPDATE ocr_jobs
        SET processed_pages = ?, succeeded_pages = ?, review_pages = ?, failed_pages = ?,
            blank_pages = ?, updated_at = ?
        WHERE job_id = ?
    """, (counts["processed_pages"], counts["succeeded_pages"], counts["review_pages"],
          counts["failed_pages"], counts["blank_pages"], now, job_id))
    conn.commit()
    conn.close()


def get_live_page_counts(job_id: str) -> dict:
    """Read-only counterpart to refresh_job_page_counts - computes the same
    aggregate counts directly from ocr_pages without writing them back, so a
    poller can see true live progress in between the points where
    refresh_job_page_counts actually runs (today: only at the end of a base
    pass, an escalation pass, or a bulk retry - never after each page)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    counts = _count_pages_by_outcome(cursor, job_id)
    conn.close()
    return counts


def delete_job(job_id: str) -> bool:
    job = get_job(job_id, include_pages=False)
    if not job:
        return False
    if os.path.exists(job['file_path']):
        try:
            os.remove(job['file_path'])
        except Exception as e:
            print(f"Warning: Failed to remove file {job['file_path']}: {e}")
    conn = get_db_connection()
    cursor = conn.cursor()
    # SQLite doesn't enforce FKs by default — cascade explicitly.
    cursor.execute("DELETE FROM ocr_page_attempts WHERE job_id = ?", (job_id,))
    cursor.execute("DELETE FROM ocr_pages WHERE job_id = ?", (job_id,))
    cursor.execute("DELETE FROM ocr_jobs WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()
    return True


# ─── Per-page data access (ocr_pages) ─────────────────────────────────────────

def init_pages_for_job(job_id: str, total_pages: int, selected_pages: Optional[set] = None, chunk_of=None):
    """Idempotent: creates a 'pending' row for every selected page that doesn't
    already have one. chunk_of(page_num) -> (chunk_id, is_boundary) is optional."""
    conn = get_db_connection()
    cursor = conn.cursor()
    pages = selected_pages if selected_pages is not None else set(range(1, total_pages + 1))
    for page_num in sorted(pages):
        chunk_id, is_boundary = chunk_of(page_num) if chunk_of else (None, False)
        cursor.execute("""
            INSERT OR IGNORE INTO ocr_pages (job_id, page_num, chunk_id, is_boundary, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (job_id, page_num, chunk_id, int(is_boundary)))
    conn.commit()
    conn.close()


def claim_page_for_processing(job_id: str, page_num: int) -> bool:
    """Atomic claim: only succeeds if the page isn't already being processed. Used by
    both the automatic worker and manual retries to prevent double-processing the
    same page concurrently. Returns True if this call won the claim."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute("""
        UPDATE ocr_pages SET status = 'processing', updated_at = ?
        WHERE job_id = ? AND page_num = ? AND status != 'processing'
    """, (now, job_id, page_num))
    won = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return won


def upsert_page(job_id: str, page_num: int, **fields):
    """Insert or update a page row. Accepts any subset of ocr_pages' non-key columns
    as keyword args, e.g. upsert_page(job_id, 3, status='success', text=..., confidence=0.92)."""
    if not fields:
        return
    allowed = {
        "chunk_id", "is_boundary", "status", "text", "confidence", "needs_review",
        "review_reason", "signals", "attempts", "escalated", "source",
        "overlap_alt_text", "primer_source_page", "duration_ms", "reviewed_by",
        "reviewed_at", "original_text", "error",
    }
    cols = [k for k in fields if k in allowed]
    if not cols:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute(f"""
        INSERT INTO ocr_pages (job_id, page_num, {', '.join(cols)}, updated_at)
        VALUES (?, ?, {', '.join('?' for _ in cols)}, ?)
        ON CONFLICT(job_id, page_num) DO UPDATE SET
            {', '.join(f'{c} = excluded.{c}' for c in cols)},
            updated_at = excluded.updated_at
    """, (job_id, page_num, *(fields[c] for c in cols), now))
    conn.commit()
    conn.close()


def get_page(job_id: str, page_num: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ocr_pages WHERE job_id = ? AND page_num = ?", (job_id, page_num))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_pages(job_id: str, page_from: Optional[int] = None, page_to: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM ocr_pages WHERE job_id = ?"
    params: list = [job_id]
    if page_from is not None:
        query += " AND page_num >= ?"
        params.append(page_from)
    if page_to is not None:
        query += " AND page_num <= ?"
        params.append(page_to)
    query += " ORDER BY page_num"
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    for r in rows:
        # Legacy-shaped keys for existing consumers (ViewerPanel.jsx, download-result, tests).
        r['page'] = r['page_num']
    return rows


def get_last_completed_page_before(job_id: str, page_num: int, max_gap: int = 1) -> Optional[Dict[str, Any]]:
    """Nearest lower-numbered page already in a terminal, text-bearing state — used
    to opportunistically source the rolling context primer. Never waits; returns
    None immediately if the true immediate predecessor (within max_gap) isn't ready."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM ocr_pages
        WHERE job_id = ? AND page_num < ? AND status IN ('success', 'review')
              AND text IS NOT NULL AND text != ''
        ORDER BY page_num DESC LIMIT 1
    """, (job_id, page_num))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    row = dict(row)
    if page_num - row['page_num'] > max_gap:
        return None
    return row


def count_pages_by_status(job_id: str) -> Dict[str, int]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, COUNT(*) FROM ocr_pages WHERE job_id = ? GROUP BY status", (job_id,))
    result = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return result


def list_review_pages(job_id: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    if job_id:
        cursor.execute("""
            SELECT * FROM ocr_pages WHERE job_id = ? AND needs_review = 1
            ORDER BY confidence ASC, page_num ASC LIMIT ? OFFSET ?
        """, (job_id, limit, offset))
    else:
        cursor.execute("""
            SELECT * FROM ocr_pages WHERE needs_review = 1
            ORDER BY confidence ASC, page_num ASC LIMIT ? OFFSET ?
        """, (limit, offset))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def reset_stuck_processing_pages(job_id: str):
    """On resume after a crash, any page left 'processing' was interrupted mid-attempt
    — reset to 'pending' so it gets redone rather than staying stuck forever."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute("""
        UPDATE ocr_pages SET status = 'pending', updated_at = ?
        WHERE job_id = ? AND status = 'processing'
    """, (now, job_id))
    conn.commit()
    conn.close()


def record_page_attempt(job_id: str, page_num: int, attempt_no: int, **fields) -> int:
    allowed = {
        "escalation_level", "engine", "scale", "tiling", "base_size", "batch_id",
        "timeout", "finished_at", "duration_ms", "outcome", "error", "confidence",
        "signals", "text_len", "text",
    }
    cols = ["job_id", "page_num", "attempt_no"] + [k for k in fields if k in allowed]
    values = [job_id, page_num, attempt_no] + [fields[k] for k in fields if k in allowed]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        INSERT INTO ocr_page_attempts ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})
    """, values)
    attempt_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return attempt_id


def get_page_attempts(job_id: str, page_num: int) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM ocr_page_attempts WHERE job_id = ? AND page_num = ? ORDER BY attempt_no
    """, (job_id, page_num))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# ─── API keys (integration/S2S credentials) ───────────────────────────────────

def create_api_key(name: str, role: str = "user") -> Dict[str, str]:
    """Creates a new API key. Returns the raw key ONCE — it is never stored or retrievable again."""
    import hashlib
    key_id = str(uuid.uuid4())
    raw_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO api_keys (key_id, name, key_hash, role, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (key_id, name, key_hash, role, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return {"key_id": key_id, "name": name, "role": role, "raw_key": raw_key}


def list_api_keys() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT key_id, name, role, created_at, revoked_at, last_used_at
        FROM api_keys ORDER BY created_at DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def revoke_api_key(key_id: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE api_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL
    """, (datetime.utcnow().isoformat(), key_id))
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def find_active_api_key_by_hash(key_hash: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT key_id, name, key_hash, role FROM api_keys
        WHERE key_hash = ? AND revoked_at IS NULL
    """, (key_hash,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def touch_api_key_last_used(key_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE api_keys SET last_used_at = ? WHERE key_id = ?
    """, (datetime.utcnow().isoformat(), key_id))
    conn.commit()
    conn.close()
