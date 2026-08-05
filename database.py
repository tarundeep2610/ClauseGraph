import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "ocr_jobs.db")

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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_db_connection():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
    total_pages: int = 1
) -> Dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute("""
        INSERT INTO ocr_jobs (
            job_id, filename, file_path, file_type, engine, scale, tiling,
            prompt_mode, custom_prompt, status, total_pages, processed_pages,
            pages_data, error_message, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0, '[]', '', ?, ?)
    """, (
        job_id, filename, file_path, file_type, engine, scale, int(tiling),
        prompt_mode, custom_prompt or '', total_pages, now, now
    ))
    conn.commit()
    conn.close()
    return get_job(job_id)

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ocr_jobs WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    job = dict(row)
    job['tiling'] = bool(job['tiling'])
    try:
        job['pages_data'] = json.loads(job['pages_data'])
    except Exception:
        job['pages_data'] = []
    return job

def list_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ocr_jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    jobs = []
    for r in rows:
        j = dict(r)
        j['tiling'] = bool(j['tiling'])
        try:
            j['pages_data'] = json.loads(j['pages_data'])
        except Exception:
            j['pages_data'] = []
        jobs.append(j)
    return jobs

def update_job_progress(job_id: str, processed_pages: int, pages_data: List[Dict[str, Any]], status: str = "processing"):
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
    total_pages = len(pages_data) if pages_data else 1
    cursor.execute("""
        UPDATE ocr_jobs
        SET status = 'completed', processed_pages = ?, pages_data = ?, updated_at = ?
        WHERE job_id = ?
    """, (total_pages, json.dumps(pages_data), now, job_id))
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

def delete_job(job_id: str) -> bool:
    job = get_job(job_id)
    if not job:
        return False
    if os.path.exists(job['file_path']):
        try:
            os.remove(job['file_path'])
        except Exception as e:
            print(f"Warning: Failed to remove file {job['file_path']}: {e}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ocr_jobs WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()
    return True
