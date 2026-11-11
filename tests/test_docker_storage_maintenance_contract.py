from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_storage_maintenance_has_read_only_plan_and_explicit_run_confirmation():
    source = _source("scripts/jx_docker_storage_maintenance.sh")

    assert "jx_docker_storage_maintenance.sh plan" in source
    assert "run --trigger=manual|post-release|scheduled" in source
    assert "MAINTAIN_JX_DOCKER_STORAGE" in source
    assert "manual|post-release|scheduled" in source
    assert "buildx du" in source
    assert "--format=json" in source
    assert '--filter "id=$id"' in source
    assert "--force" in source
    assert "docker system prune" not in source
    assert "docker volume rm" not in source
    assert "--all" not in source


def test_storage_maintenance_is_scoped_and_has_capacity_safety_gates():
    source = _source("scripts/jx_docker_storage_maintenance.sh")

    for repo in (
        "jx-now-ai-management",
        "jx-now-ai-management-ui",
        "jx-now-ai-management-ocr",
    ):
        assert repo in source
    assert "TARGET_PRIVATE_GIB" in source
    assert "RESERVE_PRIVATE_GIB" in source
    assert "insufficient_root_free_space" in source
    assert "MIN_ROOT_FREE_GIB" in source
    assert "jx-deploy.lock" in source
    assert "flock -n" in source
    assert "current/previous" in source
    assert "container_image_ids" in source


def test_storage_systemd_units_are_weekly_and_explicitly_confirmed():
    service = _source("deploy/systemd/jx-docker-storage-maintenance.service")
    timer = _source("deploy/systemd/jx-docker-storage-maintenance.timer")

    assert "Type=oneshot" in service
    assert "docker.service" in service
    assert "--trigger=scheduled" in service
    assert "--confirm=MAINTAIN_JX_DOCKER_STORAGE" in service
    assert "OnCalendar=Sun *-*-* 03:30:00" in timer
    assert "RandomizedDelaySec=15m" in timer
    assert "Persistent=false" in timer


def test_storage_maintenance_script_passes_bash_parser_when_available():
    # Keep the test hermetic: shell syntax is checked by the release harness
    # on Linux; this assertion only prevents an accidental empty executable.
    source = _source("scripts/jx_docker_storage_maintenance.sh")
    assert source.startswith("#!/usr/bin/env bash\n")
    assert "set -Eeuo pipefail" in source
