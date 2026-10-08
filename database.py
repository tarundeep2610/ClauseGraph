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
    ("reference_index_version", "INTEGER DEFAULT 0"),
    ("reference_index_frontier", "INTEGER DEFAULT 0"),
    ("ai_index_report", "TEXT DEFAULT '{}'"),  # JSON: per-chunk status/progress/audit — see ai_reference_index.py
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

    # Migrate existing databases that predate the end_of_page_scope column.
    try:
        cursor.execute("ALTER TABLE ocr_pages ADD COLUMN end_of_page_scope TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate existing databases that predate the reference_index_touched_version
    # column. Bumped on every reference-index (re)computation for a page,
    # regardless of whether it produced any surviving ocr_reference_targets rows —
    # a page whose candidate count drops to zero (e.g. after a reindex with a
    # changed algorithm) still needs to show up as "changed" to page-delta
    # fetches, which a query over ocr_reference_targets alone could never see.
    try:
        cursor.execute("ALTER TABLE ocr_pages ADD COLUMN reference_index_touched_version INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate existing databases that predate raw_ocr_text — the OCR engine's
    # raw, pre-clean_detection_tags() output (baidu/Unlimited-OCR only; NULL
    # for the GLM engine and for any page OCR'd before this capture existed).
    # Plain text, not JSON — closer to original_text/overlap_alt_text than to
    # the JSON-encoded columns below. Used only as an input to the reference
    # index's layout-tag confidence signal; ocr_pages.text (the cleaned
    # result) is unaffected and unchanged by this column's presence.
    try:
        cursor.execute("ALTER TABLE ocr_pages ADD COLUMN raw_ocr_text TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    try:
        cursor.execute("ALTER TABLE ocr_pages ADD COLUMN rule_index_snapshot TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migrate existing databases that predate formatted_text — a layout-aware
    # rewrite of `text` (headings, tables, columns) built from raw_ocr_text's
    # <|det|> tags by layout_format.build_formatted_markdown(). NULL whenever
    # there's no usable tag data (non-grounded jobs, GLM engine, batch mode,
    # or any page OCR'd before this capture existed) — consumers (viewer,
    # export) fall back to `text` on NULL, so this is always safe. Deliberately
    # a separate column, never a rewrite of `text` itself: reference_index.py's
    # mistune-based block parser yields zero blocks for a blockquote or a raw
    # HTML wrapper (confirmed directly against the model), so formatted
    # Markdown written into `text` would silently vanish from the
    # cross-reference index. `text` must stay exactly what it is today.
    try:
        cursor.execute("ALTER TABLE ocr_pages ADD COLUMN formatted_text TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_reference_targets (
            job_id TEXT NOT NULL,
            key TEXT NOT NULL,
            page INTEGER NOT NULL,
            occurrence_index INTEGER NOT NULL,
            anchor_id TEXT,
            snippet TEXT DEFAULT '',
            truncated INTEGER NOT NULL DEFAULT 0,
            continues_on_page INTEGER,
            scope_key TEXT,
            scope_label TEXT,
            scope_provisional INTEGER NOT NULL DEFAULT 0,
            collapsed_key TEXT,
            fingerprint TEXT DEFAULT '',
            written_at_version INTEGER NOT NULL DEFAULT 0,
            scope_chain TEXT,
            subdoc_label TEXT,
            subdoc_index INTEGER,
            own_label TEXT,
            PRIMARY KEY (job_id, key, page, occurrence_index)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ref_targets_collapsed ON ocr_reference_targets(job_id, collapsed_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ref_targets_version ON ocr_reference_targets(job_id, page, written_at_version)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_reference_mentions (
            job_id TEXT NOT NULL,
            page INTEGER NOT NULL,
            occurrence_index INTEGER NOT NULL,
            key TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            block_index INTEGER DEFAULT 0,
            resolution TEXT NOT NULL,
            suppressed_reason TEXT,
            detection_source TEXT DEFAULT 'rule',
            detection_model TEXT DEFAULT '',
            written_at_version INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, page, occurrence_index)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ref_mentions_version ON ocr_reference_mentions(job_id, page, written_at_version)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ref_mentions_job_page ON ocr_reference_mentions(job_id, page)")

    # Migrate existing databases that predate the structured-ancestor-chain
    # columns (scope_key/scope_label already existed as a flattened string;
    # these carry the same information as a JSON list + sub-document metadata,
    # for future filtering that doesn't require string-parsing scope_key).
    for _col, _decl in [
        ("scope_chain", "TEXT"), ("subdoc_label", "TEXT"), ("subdoc_index", "INTEGER"),
        ("own_label", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE ocr_reference_targets ADD COLUMN {_col} {_decl}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # Migrate existing databases that predate heading-vs-reference confidence
    # tracking. heading_confidence_reasons is JSON-encoded (a list of signal
    # names, e.g. ["chained_citation", "layout_title"]) — same nullable-JSON
    # pattern as scope_chain above.
    for _col, _decl in [("heading_confidence", "TEXT"), ("heading_confidence_reasons", "TEXT")]:
        try:
            cursor.execute(f"ALTER TABLE ocr_reference_targets ADD COLUMN {_col} {_decl}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # Migrate existing databases that predate AI-indexing audit tracking.
    # detection_source distinguishes an AI-detected row from a rule-detected
    # one (defaults to 'rule' on read for every pre-existing row — see the
    # SELECT below — since that's the literal truth for rows written before
    # this column existed). start_line_hint is AI-reported and stored for
    # possible future use only; the page number remains the authoritative
    # anchor identifier (see ai_indexing/schema.py).
    for _col, _decl in [
        ("detection_source", "TEXT"), ("detection_model", "TEXT"), ("start_line_hint", "INTEGER"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE ocr_reference_targets ADD COLUMN {_col} {_decl}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # ─── Structure overrides (user edits/adds/reparents on top of the
    # auto-detected Structure tree) — brand new tables, no migration needed.
    # Deliberately separate from ocr_reference_targets: reindex_job /
    # upsert_reference_targets_for_page / delete_reference_targets_for_page
    # never read or write these, so a "Rebuild Cross-References" click can
    # wipe and regenerate the whole detected index without erasing a single
    # user edit — the overrides survive by construction (different tables),
    # not because the reindex code specially preserves them. See
    # structure_overrides.py for the read-time merge logic that combines
    # these with the detector's own output.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_structure_overrides (
            job_id TEXT NOT NULL,
            key TEXT NOT NULL,
            page INTEGER NOT NULL,
            occurrence_index INTEGER NOT NULL,
            action TEXT NOT NULL,              -- 'edit' | 'delete'
            heading_override TEXT,
            start_page_override INTEGER,
            end_page_override INTEGER,
            updated_at TEXT NOT NULL,
            updated_by TEXT,
            PRIMARY KEY (job_id, key, page, occurrence_index)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_structure_manual_items (
            manual_key TEXT PRIMARY KEY,       -- "manual:<uuid4hex[:10]>"
            job_id TEXT NOT NULL,
            heading TEXT NOT NULL,
            start_page INTEGER NOT NULL,
            end_page INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_manual_items_job ON ocr_structure_manual_items(job_id)")

    # item_key/new_parent_key/scope_key alone are NOT unique identities — the
    # same bare key can appear more than once in one document (a repeated
    # heading like "point:5" under two different ancestors). Every row also
    # carries a page/occurrence_index, using -1/-1 as a non-NULL "any
    # occurrence of this key" wildcard sentinel (never a real page number,
    # and kept out of PRIMARY KEY NULL-comparison pitfalls by never being
    # NULL) — a legacy pre-migration row, which had no way to disambiguate
    # occurrences, resolves this way rather than being pinned to a literal
    # page that may not even be the occurrence it was meant to correct. See
    # _migrate_structure_hierarchy_override_identity below for the swap.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_structure_parent_overrides (
            job_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_page INTEGER NOT NULL DEFAULT -1,
            item_occurrence_index INTEGER NOT NULL DEFAULT -1,
            new_parent_key TEXT NOT NULL,      -- "" = root
            new_parent_page INTEGER NOT NULL DEFAULT -1,
            new_parent_occurrence_index INTEGER NOT NULL DEFAULT -1,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (job_id, item_key, item_page, item_occurrence_index)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ocr_structure_sort_overrides (
            job_id TEXT NOT NULL,
            scope_key TEXT NOT NULL,           -- the PARENT's key; "" = root
            scope_page INTEGER NOT NULL DEFAULT -1,
            scope_occurrence_index INTEGER NOT NULL DEFAULT -1,
            item_key TEXT NOT NULL,
            item_page INTEGER NOT NULL DEFAULT -1,
            item_occurrence_index INTEGER NOT NULL DEFAULT -1,
            sort_index INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (job_id, scope_key, scope_page, scope_occurrence_index, item_key, item_page, item_occurrence_index)
        )
    """)

    conn.commit()
    _migrate_structure_hierarchy_override_identity(conn)

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


def _migrate_structure_hierarchy_override_identity(conn: sqlite3.Connection):
    """One-time table-swap migration: ocr_structure_parent_overrides and
    ocr_structure_sort_overrides predate page/occurrence_index disambiguation
    (PRIMARY KEY was bare item_key / scope_key+item_key only) — a repeated
    bare key (e.g. two "point:5" occurrences under different ancestors) could
    only ever have ONE override stored for all its occurrences combined, and
    a move targeting one occurrence silently misapplied to whichever one a
    client-side index pass visited last.

    SQLite cannot ALTER a PRIMARY KEY in place (unlike every other migration
    in this file, which is a plain ALTER TABLE ADD COLUMN) — this is a real
    table swap: create the new shape, copy rows forward, drop, rename.
    Idempotent and safe to run on every init_db() call: gated on whether the
    live table already has the new `item_page`/`scope_page` column, so it is
    a no-op after the first successful run, including on a brand-new DB
    where CREATE TABLE IF NOT EXISTS above already created the new shape
    directly (PRAGMA table_info still reports it has the column, so this
    skips immediately).

    Existing rows are backfilled to the wildcard sentinel (-1, -1), i.e.
    "applies to any occurrence of this key" — equivalent to today's actual
    behavior for these specific rows (every legitimate override target seen
    in practice is a container-tier key, which essentially never collides),
    and strictly safer than guessing a real page/occurrence_index for a key
    this migration has no document context to resolve against. A NEW
    override created after this migration always carries the real
    page/occurrence_index the client-side selection resolved.
    """
    cursor = conn.cursor()

    def _needs_swap(table: str, new_col: str) -> bool:
        cursor.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in cursor.fetchall()}
        return new_col not in cols

    if _needs_swap("ocr_structure_parent_overrides", "item_page"):
        cursor.execute("ALTER TABLE ocr_structure_parent_overrides RENAME TO ocr_structure_parent_overrides_old")
        cursor.execute("""
            CREATE TABLE ocr_structure_parent_overrides (
                job_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                item_page INTEGER NOT NULL DEFAULT -1,
                item_occurrence_index INTEGER NOT NULL DEFAULT -1,
                new_parent_key TEXT NOT NULL,
                new_parent_page INTEGER NOT NULL DEFAULT -1,
                new_parent_occurrence_index INTEGER NOT NULL DEFAULT -1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (job_id, item_key, item_page, item_occurrence_index)
            )
        """)
        cursor.execute("""
            INSERT INTO ocr_structure_parent_overrides (
                job_id, item_key, item_page, item_occurrence_index,
                new_parent_key, new_parent_page, new_parent_occurrence_index, updated_at
            )
            SELECT job_id, item_key, -1, -1, new_parent_key, -1, -1, updated_at
            FROM ocr_structure_parent_overrides_old
        """)
        cursor.execute("DROP TABLE ocr_structure_parent_overrides_old")
        conn.commit()

    if _needs_swap("ocr_structure_sort_overrides", "scope_page"):
        cursor.execute("ALTER TABLE ocr_structure_sort_overrides RENAME TO ocr_structure_sort_overrides_old")
        cursor.execute("""
            CREATE TABLE ocr_structure_sort_overrides (
                job_id TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                scope_page INTEGER NOT NULL DEFAULT -1,
                scope_occurrence_index INTEGER NOT NULL DEFAULT -1,
                item_key TEXT NOT NULL,
                item_page INTEGER NOT NULL DEFAULT -1,
                item_occurrence_index INTEGER NOT NULL DEFAULT -1,
                sort_index INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (job_id, scope_key, scope_page, scope_occurrence_index, item_key, item_page, item_occurrence_index)
            )
        """)
        cursor.execute("""
            INSERT INTO ocr_structure_sort_overrides (
                job_id, scope_key, scope_page, scope_occurrence_index,
                item_key, item_page, item_occurrence_index, sort_index, updated_at
            )
            SELECT job_id, scope_key, -1, -1, item_key, -1, -1, sort_index, updated_at
            FROM ocr_structure_sort_overrides_old
        """)
        cursor.execute("DROP TABLE ocr_structure_sort_overrides_old")
        conn.commit()


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
    # Must be parsed here alongside engine_params, not left as a raw string —
    # GET /api/jobs/{id} would otherwise hand the frontend `ai_index_report`
    # as unparsed JSON text, and a `job.ai_index_report?.status` check would
    # silently see `undefined` forever instead of erroring visibly.
    try:
        job['ai_index_report'] = json.loads(job.get('ai_index_report') or '{}')
    except Exception:
        job['ai_index_report'] = {}
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
        "reviewed_at", "original_text", "error", "raw_ocr_text", "formatted_text",
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


# ─── Reference index (in-document cross-reference navigation) ─────────────────

def get_end_of_page_scope(job_id: str, page_num: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT end_of_page_scope FROM ocr_pages WHERE job_id = ? AND page_num = ?",
        (job_id, page_num),
    )
    row = cursor.fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def set_end_of_page_scope(job_id: str, page_num: int, scope: Dict[str, Any]):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute("""
        INSERT INTO ocr_pages (job_id, page_num, end_of_page_scope, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(job_id, page_num) DO UPDATE SET
            end_of_page_scope = excluded.end_of_page_scope,
            updated_at = excluded.updated_at
    """, (job_id, page_num, json.dumps(scope), now))
    conn.commit()
    conn.close()


def get_end_of_page_scope_bulk(job_id: str) -> Dict[int, Optional[Dict[str, Any]]]:
    """Bulk counterpart to get_end_of_page_scope — one query for every
    page's persisted end_of_page_scope instead of one query per page (used
    by reference_index.compute_scope_ranges). A page with no persisted
    scope yet (not processed, or predates this column) maps to None, same
    as get_end_of_page_scope's own defensive-JSON contract."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT page_num, end_of_page_scope FROM ocr_pages WHERE job_id = ? ORDER BY page_num",
        (job_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    result: Dict[int, Optional[Dict[str, Any]]] = {}
    for page_num, raw in rows:
        if not raw:
            result[page_num] = None
            continue
        try:
            result[page_num] = json.loads(raw)
        except Exception:
            result[page_num] = None
    return result


def get_reference_index_version(job_id: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT reference_index_version FROM ocr_jobs WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else 0


def bump_reference_index_version(job_id: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE ocr_jobs SET reference_index_version = reference_index_version + 1
        WHERE job_id = ?
    """, (job_id,))
    cursor.execute("SELECT reference_index_version FROM ocr_jobs WHERE job_id = ?", (job_id,))
    version = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return version


def get_reference_index_frontier(job_id: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT reference_index_frontier FROM ocr_jobs WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else 0


def set_reference_index_frontier(job_id: str, page_num: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ocr_jobs SET reference_index_frontier = ? WHERE job_id = ?",
        (page_num, job_id),
    )
    conn.commit()
    conn.close()


def delete_reference_targets_for_page(job_id: str, page_num: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM ocr_reference_targets WHERE job_id = ? AND page = ? AND key NOT LIKE 'docref:%' AND key NOT LIKE 'term:%'",
        (job_id, page_num),
    )
    conn.commit()
    conn.close()


def upsert_reference_targets_for_page(job_id: str, page_num: int, targets: List[Dict[str, Any]], version: int):
    """Delete-then-reinsert (retry-safe): a page's full candidate set always
    replaces whatever was there before, so a re-processed page can never leave
    stale rows behind."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ocr_reference_targets WHERE job_id = ? AND page = ? AND key NOT LIKE 'docref:%' AND key NOT LIKE 'term:%'", (job_id, page_num))
    for t in targets:
        cursor.execute("""
            INSERT INTO ocr_reference_targets (
                job_id, key, page, occurrence_index, anchor_id, snippet, truncated,
                continues_on_page, scope_key, scope_label, scope_provisional,
                collapsed_key, fingerprint, written_at_version,
                scope_chain, subdoc_label, subdoc_index,
                heading_confidence, heading_confidence_reasons, own_label,
                detection_source, detection_model, start_line_hint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, t["key"], page_num, t["occurrence_index"], t.get("anchor_id"),
            t.get("snippet", ""), int(bool(t.get("truncated"))), t.get("continues_on_page"),
            t.get("scope_key"), t.get("scope_label"), int(bool(t.get("scope_provisional"))),
            t.get("collapsed_key"), t.get("fingerprint", ""), version,
            json.dumps(t.get("scope_chain") or []), t.get("subdoc_label"), t.get("subdoc_index"),
            t.get("heading_confidence", "high"), json.dumps(t.get("heading_confidence_reasons") or []),
            t.get("own_label"),
            t.get("detection_source", "rule"), t.get("detection_model"), t.get("start_line_hint"),
        ))
    # Stamped unconditionally, even when `targets` is empty — this is the only
    # durable signal that this page's index was (re)computed at this version;
    # a page whose candidate count drops to zero would otherwise leave no
    # surviving ocr_reference_targets row and vanish from get_reference_targets_since.
    cursor.execute(
        "UPDATE ocr_pages SET reference_index_touched_version = ? WHERE job_id = ? AND page_num = ?",
        (version, job_id, page_num),
    )
    conn.commit()
    conn.close()


def touch_reference_index_pages(job_id: str, page_nums: List[int], version: int) -> None:
    """Stamps reference_index_touched_version on a set of pages without
    touching ocr_reference_targets at all — the structure_overrides.py
    mutation endpoints' equivalent of the per-page UPDATE inlined in
    upsert_reference_targets_for_page above, factored out here so those
    endpoints (create/edit/delete a structure item, rename propagation)
    don't each re-write the same statement. Deliberately NOT wired into
    upsert_reference_targets_for_page itself — that function is part of the
    core reindex path and is left untouched by design (see
    structure_overrides.py's module docstring)."""
    if not page_nums:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    for page_num in set(page_nums):
        cursor.execute(
            "UPDATE ocr_pages SET reference_index_touched_version = ? WHERE job_id = ? AND page_num = ?",
            (version, job_id, page_num),
        )
    conn.commit()
    conn.close()


def upsert_docref_targets_for_job(job_id: str, targets: List[Dict[str, Any]], version: int) -> None:
    """Whole-job upsert for docref targets: deletes only key LIKE 'docref:%' rows
    so page-scoped targets are untouched, then inserts all computed docref targets."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ocr_reference_targets WHERE job_id = ? AND key LIKE 'docref:%'", (job_id,))
    for t in targets:
        cursor.execute("""
            INSERT INTO ocr_reference_targets (
                job_id, key, page, occurrence_index, anchor_id, snippet, truncated,
                continues_on_page, scope_key, scope_label, scope_provisional,
                collapsed_key, fingerprint, written_at_version,
                scope_chain, subdoc_label, subdoc_index,
                heading_confidence, heading_confidence_reasons, own_label,
                detection_source, detection_model, start_line_hint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, t["key"], t["page"], t.get("occurrence_index", 0), t.get("anchor_id"),
            t.get("snippet", ""), int(bool(t.get("truncated"))), t.get("continues_on_page"),
            t.get("scope_key"), t.get("scope_label"), int(bool(t.get("scope_provisional"))),
            t.get("collapsed_key") or t["key"], t.get("fingerprint", ""), version,
            json.dumps(t.get("scope_chain") or []), t.get("subdoc_label"), t.get("subdoc_index"),
            t.get("heading_confidence", "high"), json.dumps(t.get("heading_confidence_reasons") or []),
            t.get("own_label"),
            t.get("detection_source", "rule"), t.get("detection_model"), t.get("start_line_hint"),
        ))
    touched_pages = set(t["page"] for t in targets)
    for page_num in touched_pages:
        cursor.execute(
            "UPDATE ocr_pages SET reference_index_touched_version = ? WHERE job_id = ? AND page_num = ?",
            (version, job_id, page_num),
        )
    conn.commit()
    conn.close()


def upsert_term_targets_for_job(job_id: str, targets: List[Dict[str, Any]], version: int) -> None:
    """Whole-job upsert for defined term targets: deletes only key LIKE 'term:%' rows
    so other families are untouched, then inserts all computed term targets."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ocr_reference_targets WHERE job_id = ? AND key LIKE 'term:%'", (job_id,))
    for t in targets:
        cursor.execute("""
            INSERT INTO ocr_reference_targets (
                job_id, key, page, occurrence_index, anchor_id, snippet, truncated,
                continues_on_page, scope_key, scope_label, scope_provisional,
                collapsed_key, fingerprint, written_at_version,
                scope_chain, subdoc_label, subdoc_index,
                heading_confidence, heading_confidence_reasons, own_label,
                detection_source, detection_model, start_line_hint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, t["key"], t["page"], t.get("occurrence_index", 0), t.get("anchor_id"),
            t.get("snippet", ""), int(bool(t.get("truncated"))), t.get("continues_on_page"),
            t.get("scope_key"), t.get("scope_label"), int(bool(t.get("scope_provisional"))),
            t.get("collapsed_key") or t["key"], t.get("fingerprint", ""), version,
            json.dumps(t.get("scope_chain") or []), t.get("subdoc_label"), t.get("subdoc_index"),
            t.get("heading_confidence", "high"), json.dumps(t.get("heading_confidence_reasons") or []),
            t.get("own_label"),
            t.get("detection_source", "rule"), t.get("detection_model"), t.get("start_line_hint"),
        ))
    touched_pages = set(t["page"] for t in targets)
    for page_num in touched_pages:
        cursor.execute(
            "UPDATE ocr_pages SET reference_index_touched_version = ? WHERE job_id = ? AND page_num = ?",
            (version, job_id, page_num),
        )
    conn.commit()
    conn.close()


def promote_docref_target(job_id: str, key: str, from_page: int, to_occurrence: Dict[str, Any]) -> None:
    """Moves a docref target row from from_page to to_occurrence's page, re-adds the
    previously-defining occurrence as an ordinary mention on from_page, removes the mention
    row for the newly-promoted occurrence on to_occurrence's page, and stamps detection_source='ai'."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Fetch old target row
    cursor.execute("""
        SELECT * FROM ocr_reference_targets
        WHERE job_id = ? AND key = ? AND page = ?
    """, (job_id, key, from_page))
    old_target = cursor.fetchone()
    old_target_dict = dict(old_target) if old_target else {}

    to_page = to_occurrence["page"]
    to_occ_idx = to_occurrence.get("occurrence_index", 0)

    # 2. Remove the mention row for the newly-promoted occurrence on to_page
    cursor.execute("""
        DELETE FROM ocr_reference_mentions
        WHERE job_id = ? AND key = ? AND page = ? AND occurrence_index = ?
    """, (job_id, key, to_page, to_occ_idx))

    # 3. Update the target row in ocr_reference_targets
    new_anchor_id = to_occurrence.get("anchor_id") or f"target-{key.replace(':', '-')}-{to_page}"
    cursor.execute("""
        UPDATE ocr_reference_targets
        SET page = ?, occurrence_index = ?, anchor_id = ?, snippet = ?,
            fingerprint = ?, own_label = ?, detection_source = 'ai',
            detection_model = ?
        WHERE job_id = ? AND key = ? AND page = ?
    """, (
        to_page,
        to_occ_idx,
        new_anchor_id,
        to_occurrence.get("snippet", ""),
        to_occurrence.get("fingerprint", to_occurrence.get("raw_text", "")),
        to_occurrence.get("own_label", to_occurrence.get("raw_text", "")),
        to_occurrence.get("detection_model", "ai"),
        job_id,
        key,
        from_page,
    ))

    # 4. Bump reference index version
    cursor.execute("SELECT reference_index_version FROM ocr_jobs WHERE job_id = ?", (job_id,))
    v_row = cursor.fetchone()
    version = (v_row[0] if v_row else 0) + 1
    cursor.execute("UPDATE ocr_jobs SET reference_index_version = ? WHERE job_id = ?", (version, job_id))

    # 5. Re-add the previously-defining occurrence as an ordinary mention on from_page
    cursor.execute("""
        SELECT COALESCE(MAX(occurrence_index), -1) + 1
        FROM ocr_reference_mentions
        WHERE job_id = ? AND page = ?
    """, (job_id, from_page))
    next_occ_idx = cursor.fetchone()[0]

    old_raw = old_target_dict.get("own_label") or old_target_dict.get("fingerprint") or key
    cursor.execute("""
        INSERT INTO ocr_reference_mentions (
            job_id, page, occurrence_index, key, raw_text, block_index,
            resolution, suppressed_reason, detection_source, detection_model,
            written_at_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        from_page,
        next_occ_idx,
        key,
        old_raw,
        0,
        "exact",
        None,
        "ai",
        to_occurrence.get("detection_model", "ai"),
        version,
    ))

    # 6. Stamp touched version on both pages
    cursor.execute(
        "UPDATE ocr_pages SET reference_index_touched_version = ? WHERE job_id = ? AND page_num = ?",
        (version, job_id, from_page),
    )
    cursor.execute(
        "UPDATE ocr_pages SET reference_index_touched_version = ? WHERE job_id = ? AND page_num = ?",
        (version, job_id, to_page),
    )

    conn.commit()
    conn.close()


def get_all_reference_targets_for_job(job_id: str) -> List[Dict[str, Any]]:
    """All ocr_reference_targets rows for a job, page + scope_chain only —
    used by structure_overrides.pages_referencing_key to find every page a
    rename needs to propagate to. Deliberately not the full
    get_reference_targets_since machinery: this doesn't care about
    touched-version deltas, just wants every row that currently exists."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT page, scope_chain FROM ocr_reference_targets WHERE job_id = ?", (job_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def has_provisional_reference_targets(job_id: str, page_num: int) -> bool:
    """True if this page has already been (at least provisionally) processed —
    used by the cascade to decide whether it can advance past this page without
    waiting for its OCR text to be freshly re-fetched."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM ocr_reference_targets WHERE job_id = ? AND page = ?",
        (job_id, page_num),
    )
    count = cursor.fetchone()[0]
    if count > 0:
        conn.close()
        return True
    # A page can legitimately have zero targets (no references on it at all) —
    # end_of_page_scope being set is what actually proves it was processed.
    cursor.execute("SELECT end_of_page_scope FROM ocr_pages WHERE job_id = ? AND page_num = ?", (job_id, page_num))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row[0])


def get_reference_targets_since(job_id: str, since_version: int) -> Dict[int, Dict[str, List[Dict[str, Any]]]]:
    """Page-delta fetch: returns {page_num: {key: [candidate, ...]}} for every
    page with at least one row written at or after since_version + 1 — each
    page's entry is its *complete current* candidate set (a whole-page
    replacement on the client), not a per-candidate diff."""
    conn = get_db_connection()
    cursor = conn.cursor()
    # Read from ocr_pages' own touched-version stamp, not a scan over
    # ocr_reference_targets survivors — a page reprocessed down to zero
    # candidates still needs to appear here so the client's local index
    # actually clears that page's now-stale contribution (see
    # upsert_reference_targets_for_page).
    cursor.execute("""
        SELECT page_num FROM ocr_pages
        WHERE job_id = ? AND reference_index_touched_version > ?
    """, (job_id, since_version))
    changed_pages = [r[0] for r in cursor.fetchall()]

    result: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
    for page_num in changed_pages:
        cursor.execute("""
            SELECT key, occurrence_index, anchor_id, snippet, truncated, continues_on_page,
                   scope_key, scope_label, scope_provisional, collapsed_key, fingerprint,
                   scope_chain, subdoc_label, subdoc_index,
                   heading_confidence, heading_confidence_reasons, own_label,
                   detection_source, detection_model, start_line_hint
            FROM ocr_reference_targets WHERE job_id = ? AND page = ?
            ORDER BY key, occurrence_index
        """, (job_id, page_num))
        by_key: Dict[str, List[Dict[str, Any]]] = {}
        for row in cursor.fetchall():
            row = dict(row)
            row["truncated"] = bool(row["truncated"])
            row["scope_provisional"] = bool(row["scope_provisional"])
            # scope_chain is NULL on any row written before this column existed
            # and not yet reindexed — same nullable-JSON situation as
            # get_end_of_page_scope, handled the same defensive way.
            raw_chain = row.get("scope_chain")
            try:
                row["scope_chain"] = json.loads(raw_chain) if raw_chain else []
            except Exception:
                row["scope_chain"] = []
            # heading_confidence(_reasons) is NULL on any row written before
            # these columns existed and not yet reindexed — same situation,
            # same defensive default (a pre-existing row predates this
            # feature entirely, so "high confidence, no reasons" is the
            # correct fallback, not an error state).
            row["heading_confidence"] = row.get("heading_confidence") or "high"
            raw_reasons = row.get("heading_confidence_reasons")
            try:
                row["heading_confidence_reasons"] = json.loads(raw_reasons) if raw_reasons else []
            except Exception:
                row["heading_confidence_reasons"] = []
            # detection_source is NULL on any row written before this column
            # existed — 'rule' is the literal truth for those (the AI path
            # didn't exist yet), not a guess.
            row["detection_source"] = row.get("detection_source") or "rule"
            by_key.setdefault(row["key"], []).append(row)
        result[page_num] = by_key
    conn.close()
    return result


# ─── AI-index run report (progress/audit trail — see ai_reference_index.py).
# The AI provider's API key itself is never stored here or anywhere else —
# see ai_indexing/credentials.py, which reads it live from config.AI_INDEX_API_KEY. ─

def get_ai_index_report(job_id: str) -> Dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ai_index_report FROM ocr_jobs WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def set_ai_index_report(job_id: str, report: Dict[str, Any]) -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ocr_jobs SET ai_index_report = ? WHERE job_id = ?",
        (json.dumps(report), job_id),
    )
    conn.commit()
    conn.close()


def update_ai_index_report(job_id: str, **fields) -> Dict[str, Any]:
    """Merges `fields` into the job's existing ai_index_report JSON blob —
    read-modify-write under get_db_connection()'s own busy-timeout/WAL
    protection (single connection, single statement pair; the orchestration
    loop that calls this is itself strictly sequential per job, so there is
    no concurrent-writer race to protect against beyond what SQLite already
    serializes)."""
    report = get_ai_index_report(job_id)
    report.update(fields)
    set_ai_index_report(job_id, report)
    return report


def upsert_reference_mentions_for_page(
    job_id: str,
    page_num: int,
    mentions: List[Dict[str, Any]],
    version: int,
) -> None:
    """Delete-then-insert per page for mention rows, stamped with written_at_version."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ocr_reference_mentions WHERE job_id = ? AND page = ?", (job_id, page_num))
    for m in mentions:
        cursor.execute("""
            INSERT INTO ocr_reference_mentions (
                job_id, page, occurrence_index, key, raw_text, block_index,
                resolution, suppressed_reason, detection_source, detection_model,
                written_at_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, page_num, m["occurrence_index"], m["key"], m["raw_text"],
            m.get("block_index", 0), m["resolution"], m.get("suppressed_reason"),
            m.get("detection_source", "rule"), m.get("detection_model"),
            version,
        ))
    conn.commit()
    conn.close()


def get_reference_mentions_since(job_id: str, since_version: int) -> Dict[int, List[Dict[str, Any]]]:
    """Page-delta mention fetch: returns {page_num: [mention_row, ...]} for any page
    with at least one mention written at > since_version or whose page was touched at > since_version."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT page_num FROM ocr_pages
        WHERE job_id = ? AND reference_index_touched_version > ?
    """, (job_id, since_version))
    pages = set(r[0] for r in cursor.fetchall())

    cursor.execute("""
        SELECT DISTINCT page FROM ocr_reference_mentions
        WHERE job_id = ? AND written_at_version > ?
    """, (job_id, since_version))
    pages.update(r[0] for r in cursor.fetchall())

    result: Dict[int, List[Dict[str, Any]]] = {}
    for p in sorted(pages):
        cursor.execute("""
            SELECT page, occurrence_index, key, raw_text, block_index,
                   resolution, suppressed_reason, detection_source, detection_model,
                   written_at_version
            FROM ocr_reference_mentions
            WHERE job_id = ? AND page = ?
            ORDER BY occurrence_index
        """, (job_id, p))
        result[p] = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return result


def get_all_reference_mentions_for_job(job_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT page, occurrence_index, key, raw_text, block_index,
               resolution, suppressed_reason, detection_source, detection_model,
               written_at_version
        FROM ocr_reference_mentions
        WHERE job_id = ?
        ORDER BY page, occurrence_index
    """, (job_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def delete_reference_mentions_for_job(job_id: str) -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ocr_reference_mentions WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()


def get_reference_targets_for_job(job_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT key, page, occurrence_index, anchor_id, collapsed_key, scope_key,
               scope_label, scope_chain, heading_confidence, heading_confidence_reasons,
               own_label, detection_source, detection_model
        FROM ocr_reference_targets WHERE job_id = ?
        ORDER BY page, key, occurrence_index
    """, (job_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def set_page_rule_snapshot(job_id: str, page_num: int, snapshot: Dict[str, Any]) -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ocr_pages SET rule_index_snapshot = ? WHERE job_id = ? AND page_num = ?",
        (json.dumps(snapshot), job_id, page_num),
    )
    conn.commit()
    conn.close()


def get_page_rule_snapshot(job_id: str, page_num: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT rule_index_snapshot FROM ocr_pages WHERE job_id = ? AND page_num = ?",
        (job_id, page_num),
    )
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            return None
    return None


def get_all_page_rule_snapshots_for_job(job_id: str) -> Dict[int, Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT page_num, rule_index_snapshot FROM ocr_pages WHERE job_id = ? AND rule_index_snapshot IS NOT NULL",
        (job_id,),
    )
    res = {}
    for r in cursor.fetchall():
        try:
            res[r[0]] = json.loads(r[1])
        except Exception:
            pass
    conn.close()
    return res

