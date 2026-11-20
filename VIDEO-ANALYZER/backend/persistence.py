"""SQLite persistence for jobs and per-user request quotas.

Use a persistent mounted disk for DB_PATH, UPLOAD_DIR and RESULTS_DIR in production.
"""
from __future__ import annotations
import json, os, sqlite3, time
from pathlib import Path
from typing import Any
from . import config

DB_PATH = Path(os.getenv('DB_PATH', str(config.RESULTS_DIR / 'video_analyzer.sqlite3')))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def connect():
    db = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, status TEXT NOT NULL,
            payload TEXT NOT NULL, created_epoch REAL NOT NULL, updated_epoch REAL NOT NULL
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id, created_epoch DESC)')
        db.execute('''CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL, created_epoch REAL NOT NULL
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_requests_owner_time ON requests(owner_id, created_epoch)')


def save_job(job_id: str, job: dict[str, Any]):
    now = time.time()
    owner = str(job.get('owner_id') or 'local')
    with connect() as db:
        db.execute('''INSERT INTO jobs(job_id,owner_id,status,payload,created_epoch,updated_epoch)
          VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
          owner_id=excluded.owner_id,status=excluded.status,payload=excluded.payload,updated_epoch=excluded.updated_epoch''',
          (job_id, owner, str(job.get('status','queued')), json.dumps(job, ensure_ascii=False),
           float(job.get('_created_epoch', now)), now))


def load_jobs() -> dict[str, dict[str, Any]]:
    with connect() as db:
        rows = db.execute('SELECT job_id,payload FROM jobs').fetchall()
    result = {}
    for row in rows:
        try: result[row['job_id']] = json.loads(row['payload'])
        except (ValueError, TypeError): continue
    return result


def delete_job(job_id: str):
    with connect() as db: db.execute('DELETE FROM jobs WHERE job_id=?', (job_id,))


def reserve_request(owner_id: str, limit: int, window_seconds: int = 3600) -> bool:
    now = time.time()
    with connect() as db:
        db.execute('DELETE FROM requests WHERE created_epoch < ?', (now-window_seconds,))
        count = db.execute('SELECT COUNT(*) FROM requests WHERE owner_id=? AND created_epoch>=?',
                           (owner_id, now-window_seconds)).fetchone()[0]
        if count >= limit: return False
        db.execute('INSERT INTO requests(owner_id,created_epoch) VALUES(?,?)', (owner_id, now))
        return True


init_db()
