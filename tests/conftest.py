from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ["APP_ENV"] = "test"
_configured_test_database = os.environ.get("TEST_DATABASE_PATH", "").strip()
if _configured_test_database:
    TEST_DATABASE_PATH = Path(_configured_test_database)
else:
    # A timed-out pytest process can retain SQLite's journal/lock.  Keep an
    # explicit override for debugging, but isolate the normal test session so
    # sequential or accidentally overlapping runners never share one file.
    TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / f"jx-now-test-ai-management-{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/15"
os.environ["INTERNAL_API_TOKEN"] = "test-internal-token"
os.environ["ADMIN_API_TOKEN"] = "test-admin-token"
os.environ["USER_HMAC_SECRET"] = "test-user-secret-with-enough-entropy"
os.environ["DIFY_BUSINESS_APP_API_KEY"] = "test-business-key"
os.environ["DIFY_MOMENTS_APP_API_KEY"] = "test-moments-key"
os.environ["DIFY_WRITING_APP_API_KEY"] = "test-writing-key"
os.environ["DIFY_SUMMARY_PROFILE_APP_API_KEY"] = "test-summary-key"
os.environ["DIFY_KNOWLEDGE_API_KEY"] = "test-knowledge-key"


@pytest.fixture()
def service_db():
    from ai_management import models  # noqa: F401
    from ai_management.cache import cache
    from ai_management.db import Base, SessionLocal, engine

    cache._client = False
    cache._memory.clear()
    cache._pending_redis_sync.clear()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        yield SessionLocal
    finally:
        Base.metadata.drop_all(bind=engine)
        cache._memory.clear()
        cache._pending_redis_sync.clear()


def pytest_sessionfinish(session, exitstatus):
    from ai_management.db import engine

    engine.dispose()
    TEST_DATABASE_PATH.unlink(missing_ok=True)
