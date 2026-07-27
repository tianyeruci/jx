from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_legacy_celery_entrypoint_reexports_registered_tasks():
    from ai_management import tasks as legacy
    from ai_management.workers.celery_app import celery_app
    from ai_management.workers.tasks.cleaning import process_cleaning_file
    from ai_management.workers.tasks.maintenance import nightly_maintenance
    from ai_management.workers.tasks.summary import summarize_conversation

    assert legacy.celery_app is celery_app
    assert legacy.process_cleaning_file is process_cleaning_file
    assert legacy.nightly_maintenance is nightly_maintenance
    assert legacy.summarize_conversation is summarize_conversation
    assert summarize_conversation.name == "ai_management.summarize_conversation"
    assert process_cleaning_file.name == "ai_management.process_cleaning_file"
    assert nightly_maintenance.name == "ai_management.nightly_maintenance"
    assert celery_app.conf.beat_schedule["nightly-maintenance"]["task"] == (
        "ai_management.nightly_maintenance"
    )
    assert celery_app.conf.worker_hijack_root_logger is False


def test_system_event_log_cleanup_uses_configured_retention(monkeypatch):
    from ai_management.workers.tasks import maintenance

    class FakeSession:
        def __init__(self):
            self.statement = None
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement):
            self.statement = statement

        def commit(self):
            self.committed = True

    fake_session = FakeSession()
    monkeypatch.setattr(maintenance.settings, "system_log_retention_days", 30)
    monkeypatch.setattr(maintenance, "SessionLocal", lambda: fake_session)
    before = datetime.now(timezone.utc) - timedelta(days=30, seconds=1)
    maintenance._cleanup_system_event_logs()
    after = datetime.now(timezone.utc) - timedelta(days=30) + timedelta(seconds=1)

    assert fake_session.committed is True
    assert fake_session.statement is not None
    params = fake_session.statement.compile().params
    cutoff = next(value for value in params.values() if isinstance(value, datetime))
    assert before <= cutoff <= after
