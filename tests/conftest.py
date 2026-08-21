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
