import asyncio
import os
import sys
import time
import tempfile

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="ocr_test_data_")
os.environ["DEV_MODE"] = "true"
os.environ["DEV_SECRET"] = "test-dev-secret-for-pytest-only"
os.environ["DATA_DIR"] = _TEST_DATA_DIR
os.environ["AUTH_AUTHORITY"] = "https://auth.example.test"
os.environ["PROJECT_ID"] = "test-project"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

import config
import database

database.init_db()

import main as main_module  # noqa: E402  (import after env vars are set)


@pytest.fixture(autouse=True)
def _prevent_real_shutdown(monkeypatch):
    """POST /api/shutdown schedules a real SIGINT — never let that actually fire in tests."""
    monkeypatch.setattr(main_module.signal, "raise_signal", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _reset_task_queue_async_primitives():
    """task_queue.py holds a few module-level asyncio.Lock/Semaphore objects,
    created once at import time. Each binds permanently to whichever event loop
    first uses it — but pytest-asyncio (asyncio_mode=strict) gives every async
    test its own fresh event loop, so any test after the first one to actually
    exercise these (e.g. via _render_page) fails with "bound to a different
    event loop". Reassigning fresh objects before each test avoids that; a
    freshly-constructed Lock/Semaphore doesn't bind to a loop until first used,
    so this is safe even for non-async tests."""
    import task_queue
    task_queue._gpu_semaphore = asyncio.Semaphore(max(1, config.OCR_JOB_CONCURRENCY))
    task_queue._escalation_share_semaphore = asyncio.Semaphore(max(1, config.OCR_ESCALATION_CONCURRENCY_SHARE))
    task_queue._render_lock = asyncio.Lock()


@pytest.fixture(autouse=True)
def _reset_reference_index_async_primitives():
    """Same per-event-loop-binding issue as above, for reference_index.py's
    module-level per-job asyncio.Lock cache."""
    import reference_index
    reference_index._frontier_locks = {}
    reference_index._active_reindex_jobs = set()
    reference_index._scope_ranges_cache = {}


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """main._rate_buckets is a module-level dict keyed by rate_limit_key
    (effectively per-user), accumulating submission timestamps across the
    whole test session since nothing ever clears it. Tests share the same
    dev admin/user tokens (see admin_token/user_token above), so without this
    reset, enough rate-limited calls across unrelated test files eventually
    tips a later, unrelated test over RATE_LIMIT_MAX and it starts getting
    429s instead of the status code it actually meant to test."""
    main_module._rate_buckets = {}


@pytest.fixture()
def client():
    with TestClient(main_module.app) as c:
        yield c


def _make_dev_token(user_id: str, roles: list[str]) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "roles": roles,
        "project_id": config.PROJECT_ID,
        "iat": now,
        "exp": now + 3600,
    }
    return jose_jwt.encode(payload, config.DEV_SECRET, algorithm="HS256")


@pytest.fixture()
def admin_token():
    return _make_dev_token("dev-admin-001", ["admin"])


@pytest.fixture()
def user_token():
    return _make_dev_token("dev-user-001", ["user"])


@pytest.fixture()
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture()
def admin_api_key():
    result = database.create_api_key(name="test-admin-key", role="admin")
    return result["raw_key"], result["key_id"]


@pytest.fixture()
def user_api_key():
    result = database.create_api_key(name="test-user-key", role="user")
    return result["raw_key"], result["key_id"]
