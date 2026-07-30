from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "jx_release_data.py").read_text(encoding="utf-8")


def test_cleanup_has_no_name_regex_as_dify_delete_authority():
    assert "TEST_MARKER" not in SOURCE
    assert "re.compile" not in SOURCE
    assert "manifest" in SOURCE
    assert "document_id" in SOURCE


def test_cleanup_requires_a_verified_plan_before_writes():
    assert "plan-clean" in SOURCE
    assert "plan_fingerprint" in SOURCE
    assert "candidate" in SOURCE


def test_cleanup_has_redis_exclusivity_guard():
    assert "redis_db" in SOURCE
    assert "exclusive" in SOURCE.lower()
    assert "flushdb" in SOURCE
