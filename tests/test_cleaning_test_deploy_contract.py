from __future__ import annotations

import hashlib
import inspect
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from scripts import generate_project_index  # noqa: E402

APP_IMAGE_ID = "sha256:" + "a" * 64
UI_IMAGE_ID = "sha256:" + "b" * 64
OCR_IMAGE_ID = "sha256:" + "c" * 64


def _bash_command(*args: str) -> list[str]:
    """Run shell-contract tests in a POSIX shell that can read this worktree.

    On Windows, ``bash`` can resolve to the legacy WSL launcher.  That process
    does not provide the same relative-path behavior as the Git Bash used for
    the repository's local shell contracts and can leave an unbounded WSL
    child behind when a fake Compose command fails.  Prefer Git Bash there;
    other platforms retain their normal PATH-resolved Bash.
    """
    if os.name == "nt":
        git_bash = (
            Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "Git" / "bin" / "bash.exe"
        )
        if not git_bash.is_file():
            pytest.skip("Windows shell-contract tests require Git Bash")
        return [str(git_bash), *args]
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("shell-contract tests require Bash")
    return [bash, *args]


def _short_hostname() -> str:
    """Return the Linux ``hostname -s`` value simulated by this local test."""
    if os.name == "nt":
        return os.environ.get("COMPUTERNAME", socket.gethostname()).split(".", maxsplit=1)[0]
    return subprocess.run(
        _bash_command("-c", "hostname -s"), text=True, capture_output=True, check=True
    ).stdout.strip()


def _bash_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if len(value) >= 2 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


def _governed_worktree_fingerprint() -> str:
    # The shell contract replaces the runtime calculator with this fixed,
    # independently supplied current value.  Production still calculates the
    # review-scope fingerprint itself; the unit test must not scan the very
    # large untracked evidence tree through a nested Windows Git process.
    return "e" * 64


def _current_wheel_sha256() -> str:
    return hashlib.sha256(
        (ROOT / "dist" / "jx_now_ai_management-0.2.1-py3-none-any.whl").read_bytes()
    ).hexdigest()


def test_cleaning_test_compose_isolated_contract():
    compose = (ROOT / "docker-compose.cleaning-test.yml").read_text(encoding="utf-8")
    ui = (ROOT / "docker-compose.cleaning-test-ui.yml").read_text(encoding="utf-8")
    assert "name: ai-management-cleaning-test" in compose
    assert "ai-management-cleaning-test-api" in compose
    assert "dify_backend:" in compose
    assert "name: docker_default" in compose
    assert "127.0.0.1:18080:8000" in compose
    assert "ai_management_cleaning_test_uploads" in compose
    assert "DIFY_GENERAL_DATASET_ID: 19206ce0-80a4-4a93-aa86-63e3385d2b2a" in compose
    assert "DIFY_COURSE_DATASET_ID: 19206ce0-80a4-4a93-aa86-63e3385d2b2a" in compose
    assert "CLEANING_TEST_DATABASE_URL" in compose
    assert "CLEANING_TEST_REDIS_URL" in compose
    assert "CLEANING_TEST_ISOLATION_ID" in compose
    assert "CLEANING_TEST_DATABASE_NAME: ai_management_cleaning_test" in compose
    assert "CLEANING_TEST_DATABASE_USER: ai_management_cleaning_test" in compose
    assert "CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED" in compose
    assert "BAILIAN_BATCH_ENABLED: ${CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED:-false}" in compose
    assert "model-batch-worker-test" in compose
    assert 'profiles: ["batch"]' in compose
    assert "ocr-worker-test" in compose
    assert 'profiles: ["ocr"]' in compose
    assert "AI_CLEANING_TEST_OCR_IMAGE" in compose

    assert "-Q ai_ocr" in compose
    ocr_match = re.search(
        r"^  ocr-worker-test:\n(?P<body>.*?)(?=^  \S|^volumes:)", compose, re.M | re.S
    )
    assert ocr_match is not None
    assert "mem_limit: 3g" in ocr_match.group("body")
    assert "memswap_limit: 3g" in ocr_match.group("body")
    assert 'cpus: "2.0"' in ocr_match.group("body")
    assert "pids_limit: 256" in ocr_match.group("body")
    assert 'restart: "no"' in ocr_match.group("body")
    assert "tmpfs:" in ocr_match.group("body")
    assert "/tmp:rw,nosuid,nodev,size=512m,mode=1777" in ocr_match.group("body")
    for service in (
        "api-cleaning-test",
        "cleaning-worker-test",
        "publication-worker-test",
        "ocr-worker-test",
    ):
        match = re.search(rf"^  {service}:\n(?P<body>.*?)(?=^  \S|^volumes:)", compose, re.M | re.S)
        assert match is not None, service
        assert 'user: "0:0"' in match.group("body"), service
    for forbidden in ("scheduler", "postgres", "redis", "nginx"):
        assert forbidden not in compose
    assert "127.0.0.1:18501:8501" in ui
    assert "http://ai-management-cleaning-test-api:8000" in ui
    assert "docker_default" not in ui
    assert "ai-management-test-ui" not in ui
    assert "ai-management-api" not in ui
    assert "env_file: ${CLEANING_TEST_ENV_FILE:-.env.cleaning-test}" in compose
    assert "env_file: ${CLEANING_TEST_ENV_FILE:-.env.cleaning-test}" in ui
    # One canonical YAML mapping keeps the values used by every worker in
    # lockstep.  Repeating these values per service lets a later edit leave
    # one queue consumer outside the isolated boundary.
    assert "x-cleaning-test-runtime: &cleaning_test_runtime" in compose
    assert compose.count("CLEANING_GENERAL_ONLY:") == 1
    assert compose.count("\n    CLEANING_TEST_ISOLATION_ID:") == 1
    parsed = yaml.safe_load(compose)
    for service in (
        "api-cleaning-test",
        "cleaning-worker-test",
        "model-realtime-worker-test",
        "model-batch-worker-test",
        "publication-worker-test",
        "ocr-worker-test",
    ):
        environment = parsed["services"][service]["environment"]
        assert environment["APP_ENV"] == "test"
        assert environment["CLEANING_GENERAL_ONLY"] == "true"
        assert (
            environment["CLEANING_TEST_ISOLATION_ID"]
            == "${CLEANING_TEST_ISOLATION_ID:?set CLEANING_TEST_ISOLATION_ID}"
        )
        assert (
            environment["DATABASE_URL"]
            == "${CLEANING_TEST_DATABASE_URL:?set CLEANING_TEST_DATABASE_URL}"
        )
        assert environment["REDIS_URL"] == "${CLEANING_TEST_REDIS_URL:?set CLEANING_TEST_REDIS_URL}"


def test_ys_acceptance_runner_is_one_off_and_shares_audit_and_read_only_inputs():
    compose = (ROOT / "docker-compose.ys.yml").read_text(encoding="utf-8")
    assert "ai-ys-acceptance-runner:" in compose
    assert "profiles: [acceptance]" in compose
    assert "ai_ys_acceptance_audit:/app/var/audit" in compose
    assert (
        "${AI_YS_ACCEPTANCE_INPUT_DIR:?set AI_YS_ACCEPTANCE_INPUT_DIR}:/app/acceptance-input:ro"
        in compose
    )
    assert (
        "${AI_YS_ACCEPTANCE_STAGING_DIR:?set AI_YS_ACCEPTANCE_STAGING_DIR}:/app/acceptance-staging:ro"
        in compose
    )
    assert (
        "ACCEPTANCE_CHAT_EXPECTATIONS_JSON: /app/acceptance-staging/chat-expectations.json"
        in compose
    )
    assert (
        "JX_ACCEPTANCE_CAMPAIGN_LEDGER_PATH: /app/var/audit/acceptance-campaign-ledger.json"
        in compose
    )
    assert "ai-ys-acceptance-runner" not in compose.split("volumes:", 1)[0].split("services:", 1)[0]


def test_cleaning_test_image_builds_overlay_only_the_current_wheel():
    for name in (
        "Dockerfile.cleaning-test",
        "Dockerfile.cleaning-test-ui",
        "Dockerfile.cleaning-test-ocr",
    ):
        dockerfile = (ROOT / name).read_text(encoding="utf-8")
        assert "ARG BASE_IMAGE" in dockerfile
        assert "FROM ${BASE_IMAGE}" in dockerfile
        assert "jx_now_ai_management-0.2.1-py3-none-any.whl" in dockerfile
        assert "--no-deps --force-reinstall" in dockerfile
        assert "COPY ." not in dockerfile
        assert "org.jxnow.cleaning-test.worktree-fingerprint" in dockerfile
        assert "org.jxnow.cleaning-test.wheel-sha256" in dockerfile
        assert (
            'actual_wheel_sha256="$(sha256sum /tmp/jx_now_ai_management-0.2.1-py3-none-any.whl'
            in dockerfile
        )
        assert 'test "$actual_wheel_sha256" = "$CLEANING_TEST_WHEEL_SHA256"' in dockerfile
    ui_dockerfile = (ROOT / "Dockerfile.cleaning-test-ui").read_text(encoding="utf-8")
    assert "ai_management.test_ui" in ui_dockerfile
    assert ui_dockerfile.rstrip().endswith("USER root")


def test_cleaning_test_assets_have_local_shell_and_compose_syntax() -> None:
    """Parse-only validation: it never contacts a Docker daemon or starts a service."""
    check = subprocess.run(
        _bash_command("-n", "scripts/jx_cleaning_test_switch.sh"),
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr
    for path in (
        ROOT / "docker-compose.cleaning-test.yml",
        ROOT / "docker-compose.cleaning-test-ui.yml",
    ):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert parsed["name"] == "ai-management-cleaning-test"
        assert isinstance(parsed["services"], dict) and parsed["services"]


def test_project_index_collects_every_isolated_cleaning_service() -> None:
    """Profiled OCR/Batch services remain discoverable without running Compose."""

    indexed = set(generate_project_index.collect_compose_services(ROOT))
    for filename in (
        "docker-compose.cleaning-test.yml",
        "docker-compose.cleaning-test-ui.yml",
    ):
        services = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))["services"]
        for name, service in services.items():
            command = service.get("command", "")
            if isinstance(command, list):
                command = " ".join(str(item) for item in command)
            assert (name, filename, str(command)) in indexed


def test_cleaning_test_switch_script_never_touches_the_formal_stack():
    script = (ROOT / "scripts/jx_cleaning_test_switch.sh").read_text(encoding="utf-8")
    assert "ai-management stack" in script
    assert "FORMAL_TEST_UI" not in script
    assert "docker compose -p ai-management " not in script
    assert "--env-file" in script
    assert "CLEANING_TEST_DATABASE_NAME" in script
    assert "CLEANING_TEST_DATABASE_USER" in script
    assert "CLEANING_TEST_REDIS_URL" in script
    assert "CLEANING_TEST_ISOLATION_ID" in script
    assert "CLEANING_TEST_DATASET_ID" in script
    assert "FORMAL_DATABASE_URL" in script
    assert "*/6" in script
    assert '"$test_redis_url" != *\\?*' in script
    assert "require_runtime_preflight_manifest" in script
    assert "CLEANING_TEST_PREFLIGHT_MANIFEST" in script
    assert "manifest_version=1" in script
    assert "manifest_required_keys" in script
    assert "database_host database_port database_name database_user database_url_sha256" in script
    assert "database_dsn_identity" in script
    assert "manifest_unknown_or_duplicate_key" in script
    assert "manifest_max_age_seconds" in script
    assert 'queue_backlog]}" == "0"' in script
    assert "spend_settled_yuan" in script
    assert "spend_reserved_yuan" in script
    assert 'budget_allows_start]}" == "true"' in script
    assert 'compose_project]}" == "ai-management-cleaning-test"' in script
    assert 'database_name]}" == "ai_management_cleaning_test"' in script
    assert 'database_user]}" == "ai_management_cleaning_test"' in script
    assert 'dataset_id]}" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a"' in script
    assert 'api_bind]}" == "127.0.0.1:18080"' in script
    assert 'ui_bind]}" == "127.0.0.1:18501"' in script
    for required_evidence in (
        'redis_effective_db]}" == "6"',
        'redis_db6_ownership]}" == "exclusive"',
        'queue_ownership]}" == "exclusive"',
        'formal_stack_health]}" == "healthy"',
    ):
        assert required_evidence in script
    verify_started = re.search(r"^verify_started\(\) \{(?P<body>.*?)^\}", script, re.M | re.S)
    assert verify_started is not None
    verify_started_body = verify_started.group("body")
    assert '"/dev/tcp/127.0.0.1/$1"' in script
    assert "service_container api-cleaning-test" in script
    for worker in (
        "cleaning-worker-test",
        "model-realtime-worker-test",
        "publication-worker-test",
    ):
        assert f"{worker}" in verify_started_body
        assert 'ps --status running -q "$worker"' in verify_started_body
    assert '"${COMPOSE[@]}" port api-cleaning-test 8000' in script
    assert '"${COMPOSE[@]}" port test-ui-cleaning 8501' in script
    assert "127.0.0.1:18501" in script
    assert "ocr-start" in script
    assert "ocr-stop" in script
    assert "verify_ocr_started" in script
    assert "--status running -q ocr-worker-test" in script
    assert "CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED" in script
    assert "model-batch-worker-test" in script
    assert "down -v" not in script
    assert "docker volume rm" not in script
    assert "compose_service_names" in script
    assert 'grep -Fxq "$forbidden_service"' in script
    assert "CLEANING_TEST_COMPOSE_UNSET_KEYS" in script
    assert "wait_for_http" in script
    assert "verify_formal_stack_health_after_start" in script


def test_compose_is_pinned_to_manifest_image_ids_not_mutable_tags():
    script = (ROOT / "scripts" / "jx_cleaning_test_switch.sh").read_text(encoding="utf-8")
    harness = (
        "source scripts/jx_cleaning_test_switch.sh; "
        "EXPECTED_APP_IMAGE_ID=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; "
        "EXPECTED_UI_IMAGE_ID=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; "
        "EXPECTED_OCR_IMAGE_ID=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc; "
        "pin_compose_images; printf '%s\\n' \"${COMPOSE[*]}\""
    )
    result = subprocess.run(
        _bash_command("-c", harness),
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "AI_CLEANING_TEST_IMAGE=sha256:" in result.stdout
    assert "AI_CLEANING_TEST_UI_IMAGE=sha256:" in result.stdout
    assert "AI_CLEANING_TEST_OCR_IMAGE=sha256:" in result.stdout
    assert "AI_CLEANING_TEST_IMAGE=jx-now-ai-management:cleaning-test" not in result.stdout
    assert "cleanup_failed_core_start" in script
    assert "--profile ocr stop ocr-worker-test" in script
    start_body = re.search(r"^start\(\) \{(?P<body>.*?)^\}", script, re.M | re.S)
    assert start_body is not None
    assert "cleanup_failed_core_start" in start_body.group("body")
    assert "verify_formal_stack_health_after_start" in start_body.group("body")
    assert (
        start_body.group("body")
        .lstrip()
        .startswith("preflight\n  require_runtime_preflight_manifest\n")
    )
    ocr_start = re.search(r"^ocr_start\(\) \{(?P<body>.*?)^\}", script, re.M | re.S)
    assert ocr_start is not None
    assert "verify_started" in ocr_start.group("body")
    assert "verify_formal_stack_health_after_start" in ocr_start.group("body")
    assert "cleanup_failed_ocr_start" in ocr_start.group("body")
    assert (
        ocr_start.group("body")
        .lstrip()
        .startswith("preflight\n  require_runtime_preflight_manifest\n")
    )


def test_formal_health_rejects_any_isolated_port_alias_and_bypasses_proxy():
    script = (ROOT / "scripts" / "jx_cleaning_test_switch.sh").read_text(encoding="utf-8")
    assert "--noproxy '*'" in script
    for origin in (
        "http://127.1:018080",
        "http://2130706433:018080",
        "http://[::ffff:127.0.0.1]:018080",
        "http://example.test:18501",
    ):
        result = subprocess.run(
            _bash_command(
                "-c",
                f"source scripts/jx_cleaning_test_switch.sh; validate_formal_origin {shlex.quote(origin)}",
            ),
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0, origin


def test_health_json_python_selection_is_platform_aware():
    harness = (
        "source scripts/jx_cleaning_test_switch.sh; "
        "OSTYPE=msys; health_json_python; printf '\\n'; "
        "OSTYPE=linux-gnu; health_json_python; printf '\\n'; "
        "CLEANING_TEST_HEALTH_JSON_PYTHON=custom-python; health_json_python"
    )
    result = subprocess.run(
        _bash_command("-c", harness),
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "python\npython3\ncustom-python"


def test_preflight_accepts_exact_isolated_batch_service_name(tmp_path):
    """Run the real local preflight without a Docker daemon or any service."""
    # Canonical Quality may put pytest's basetemp on a different Windows
    # drive.  The sourced Bash contract intentionally receives workspace-
    # relative paths, so keep this fake artifact under the repository tmp.
    tmp_path = Path(tempfile.mkdtemp(prefix="preflight-contract-", dir=ROOT / "tmp"))
    env_file = tmp_path / "isolated.env"
    env_file.write_text(
        "\n".join(
            (
                "CLEANING_GENERAL_ONLY=true",
                "DIFY_GENERAL_DATASET_ID=19206ce0-80a4-4a93-aa86-63e3385d2b2a",
                "DIFY_COURSE_DATASET_ID=19206ce0-80a4-4a93-aa86-63e3385d2b2a",
                "CLEANING_TEST_DATASET_ID=19206ce0-80a4-4a93-aa86-63e3385d2b2a",
                "CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED=false",
                "CLEANING_TEST_DATABASE_URL=postgresql://ai_management_cleaning_test:secret@postgres:5432/ai_management_cleaning_test",
                "CLEANING_TEST_DATABASE_NAME=ai_management_cleaning_test",
                "CLEANING_TEST_DATABASE_USER=ai_management_cleaning_test",
                "CLEANING_TEST_REDIS_URL=redis://:secret@isolated-redis:6379/6",
                "CLEANING_TEST_ISOLATION_ID=ai-management-cleaning-test",
                "CLEANING_TEST_FORMAL_HEALTH_URL=http://127.0.0.1:8000",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            *_bash_command(
                "-c",
                f"export CLEANING_TEST_ENV_FILE={shlex.quote(env_file.relative_to(ROOT).as_posix())}; "
                "export CLEANING_TEST_CONFIRM=preflight; "
                "source scripts/jx_cleaning_test_switch.sh; "
                "is_port_open() { return 1; }; preflight",
            ),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "CLEANING_TEST_CONFIRM": "preflight",
            "CLEANING_TEST_ENV_FILE": env_file.relative_to(ROOT).as_posix(),
        },
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    env_file.unlink()
    tmp_path.rmdir()


def test_env_value_rejects_duplicate_service_affecting_keys(tmp_path):
    """The reviewed env file, manifest and effective Compose values must stay one-to-one."""
    env_file = tmp_path / "isolated.env"
    env_file.write_text(
        "AI_CLEANING_TEST_IMAGE=first\nAI_CLEANING_TEST_IMAGE=second\n", encoding="utf-8"
    )
    result = subprocess.run(
        _bash_command(
            "-c",
            f"export CLEANING_TEST_ENV_FILE={shlex.quote(env_file.as_posix())}; "
            "source scripts/jx_cleaning_test_switch.sh; "
            "env_value AI_CLEANING_TEST_IMAGE",
        ),
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "duplicate" in result.stderr


def test_start_with_batch_disabled_stops_only_isolated_batch_worker(tmp_path):
    """A true-to-false switch must not leave the optional Batch worker running."""
    tmp_path = Path(tempfile.mkdtemp(prefix="batch-switch-contract-", dir=ROOT / "tmp"))
    calls = tmp_path / "compose-calls.log"
    fake_compose = tmp_path / "fake-compose.sh"
    fake_compose.write_bytes(
        (
            'printf \'%s\\n\' "$*" >> "$CALLS"\n'
            'case "$*" in\n'
            "  *'ps -q api-cleaning-test'*) printf api ;;\n"
            "  *'ps -q test-ui-cleaning'*) printf ui ;;\n"
            "  *'ps -q cleaning-worker-test'*) printf worker ;;\n"
            "  *'ps -q model-realtime-worker-test'*) printf worker ;;\n"
            "  *'ps -q publication-worker-test'*) printf worker ;;\n"
            "  *'ps --status running -q cleaning-worker-test'*) printf worker ;;\n"
            "  *'ps --status running -q model-realtime-worker-test'*) printf worker ;;\n"
            "  *'ps --status running -q publication-worker-test'*) printf worker ;;\n"
            "  *'port api-cleaning-test 8000'*) printf '127.0.0.1:18080\\n' ;;\n"
            "  *'port test-ui-cleaning 8501'*) printf '127.0.0.1:18501\\n' ;;\n"
            "  *) : ;;\n"
            "esac\n"
        ).encode("utf-8")
    )
    harness = (
        "source scripts/jx_cleaning_test_switch.sh; "
        "preflight() { :; }; require_runtime_preflight_manifest() { :; }; "
        "wait_for_http() { :; }; verify_formal_stack_health_after_start() { :; }; "
        "env_value() { printf false; }; "
        f"export CALLS={shlex.quote(calls.relative_to(ROOT).as_posix())}; "
        f"COMPOSE=(bash {shlex.quote(fake_compose.relative_to(ROOT).as_posix())}); start"
    )
    try:
        result = subprocess.run(
            _bash_command("-c", harness),
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        logged = calls.read_text(encoding="utf-8")
        assert "--profile batch stop model-batch-worker-test" in logged
        up_calls = [line for line in logged.splitlines() if "up -d" in line]
        assert up_calls == [
            "up -d api-cleaning-test cleaning-worker-test model-realtime-worker-test publication-worker-test test-ui-cleaning"
        ]
    finally:
        calls.unlink(missing_ok=True)
        fake_compose.unlink(missing_ok=True)
        tmp_path.rmdir()


def test_start_failure_stops_only_the_isolated_core_services(tmp_path):
    """A failed isolated up must not leave a partial worker set running."""
    tmp_path = Path(tempfile.mkdtemp(prefix="start-compensation-contract-", dir=ROOT / "tmp"))
    calls = tmp_path / "compose-calls.log"
    fake_compose = tmp_path / "fake-compose.sh"
    fake_compose.write_bytes(
        b'printf "%s\\n" "$*" >> "$CALLS"\ncase "$*" in *"up -d "*) exit 1;; esac\n',
    )
    harness = (
        "source scripts/jx_cleaning_test_switch.sh; "
        "preflight() { :; }; require_runtime_preflight_manifest() { :; }; "
        "env_value() { printf false; }; "
        f"export CALLS={shlex.quote(calls.relative_to(ROOT).as_posix())}; "
        f"COMPOSE=(bash {shlex.quote(fake_compose.relative_to(ROOT).as_posix())}); start"
    )
    try:
        result = subprocess.run(
            _bash_command("-c", harness),
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        logged = calls.read_text(encoding="utf-8").splitlines()
        assert logged[-1] == (
            "--profile batch stop api-cleaning-test cleaning-worker-test "
            "model-realtime-worker-test model-batch-worker-test publication-worker-test "
            "test-ui-cleaning"
        )
        assert any(line.startswith("up -d api-cleaning-test") for line in logged)
    finally:
        calls.unlink(missing_ok=True)
        fake_compose.unlink(missing_ok=True)
        tmp_path.rmdir()


def test_ocr_start_post_health_failure_stops_only_the_isolated_ocr_worker(tmp_path):
    """OCR activation must re-check the formal read-only health witness and compensate itself."""
    calls = tmp_path / "compose-calls.log"
    fake_compose = tmp_path / "fake-compose.sh"
    fake_compose.write_text('printf "%s\\n" "$*" >> "$CALLS"\nexit 0\n', encoding="utf-8")
    harness = (
        "source scripts/jx_cleaning_test_switch.sh; "
        "preflight() { :; }; require_runtime_preflight_manifest() { :; }; "
        "verify_started() { :; }; verify_ocr_started() { :; }; "
        "verify_formal_stack_health_after_start() { return 1; }; "
        f"export CALLS={shlex.quote(calls.as_posix())}; "
        f"COMPOSE=(bash {shlex.quote(fake_compose.as_posix())}); ocr_start"
    )
    result = subprocess.run(
        _bash_command("-c", harness),
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "--profile ocr up -d ocr-worker-test",
        "--profile ocr stop ocr-worker-test",
    ]


def test_generic_retry_applies_general_only_boundary_before_retry_mutation():
    """The legacy generic retry route cannot re-enter a non-general batch."""
    from ai_management import cleaning

    guard = inspect.getsource(cleaning._reject_non_general_cleaning_test_batch_write)
    assert "settings.cleaning_general_only" in guard
    assert 'batch.knowledge_base_code != "general"' in guard
    for retry in (cleaning.retry_cleaning_file, cleaning.retry_cleaning_item):
        source = inspect.getsource(retry)
        assert "_reject_non_general_cleaning_test_batch_write" in source
        assert source.index("_reject_non_general_cleaning_test_batch_write") < source.index(
            'row.status = "pending"'
        )


def test_generic_retry_rejects_course_batch_in_general_only_profile(service_db, monkeypatch):
    """Exercise the legacy batch-ID-only route with a local SQLite batch."""
    from ai_management import cleaning
    from ai_management.errors import ServiceError
    from ai_management.models import CleaningBatch

    class TestSettings:
        cleaning_general_only = True

    monkeypatch.setattr(cleaning, "get_settings", lambda: TestSettings())
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_route_catalog",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="failed",
        )
        session.add(batch)
        session.commit()
        with pytest.raises(ServiceError) as caught:
            cleaning.retry_cleaning_file(batch.id, "guessed-file-id", session)
    assert caught.value.code == "STRATEGY_DISABLED"
    assert caught.value.status_code == 403


def _runtime_manifest(
    *, checked_at: datetime | None = None, batch_enabled: str = "false"
) -> dict[str, str]:
    redis_url = "redis://:redacted@isolated-redis:6379/6"
    database_url = (
        "postgresql://ai_management_cleaning_test:redacted@isolated-postgres:5432/"
        "ai_management_cleaning_test"
    )
    env_text = (
        "CLEANING_TEST_DATABASE_URL=postgresql://ai_management_cleaning_test:redacted@"
        "isolated-postgres:5432/ai_management_cleaning_test\n"
        "CLEANING_TEST_REDIS_URL=redis://:redacted@isolated-redis:6379/6\n"
        "CLEANING_TEST_FORMAL_HEALTH_URL=http://formal-api.internal\n"
        f"CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED={batch_enabled}\n"
        f"BAILIAN_BATCH_ENABLED={batch_enabled}\n"
    )
    source_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout.strip()
    return {
        "manifest_version": "1",
        "checked_at_utc": (checked_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": _short_hostname(),
        "isolation_id": "ai-management-cleaning-test",
        "compose_project": "ai-management-cleaning-test",
        "database_host": "isolated-postgres",
        "database_port": "5432",
        "database_name": "ai_management_cleaning_test",
        "database_user": "ai_management_cleaning_test",
        "database_url_sha256": hashlib.sha256(database_url.encode()).hexdigest(),
        "redis_effective_db": "6",
        "redis_url_sha256": hashlib.sha256(redis_url.encode()).hexdigest(),
        "dataset_id": "19206ce0-80a4-4a93-aa86-63e3385d2b2a",
        "api_bind": "127.0.0.1:18080",
        "ui_bind": "127.0.0.1:18501",
        "general_only": "true",
        "batch_enabled": batch_enabled,
        "app_image": "jx-now-ai-management:cleaning-test",
        "ui_image": "jx-now-ai-management-ui:cleaning-test",
        "ocr_image": "jx-now-ai-management-ocr:cleaning-test",
        "app_image_id": APP_IMAGE_ID,
        "ui_image_id": UI_IMAGE_ID,
        "ocr_image_id": OCR_IMAGE_ID,
        "source_revision": source_revision,
        "worktree_fingerprint": _governed_worktree_fingerprint(),
        "wheel_sha256": _current_wheel_sha256(),
        "release_bundle_sha256": _canonical_bundle_sha256(ROOT),
        "env_file_sha256": hashlib.sha256(env_text.encode()).hexdigest(),
        "redis_db6_ownership": "exclusive",
        "queue_ownership": "exclusive",
        "queue_backlog": "0",
        "spend_settled_yuan": "0",
        "spend_reserved_yuan": "0",
        "daily_settled_yuan": "0",
        "daily_reserved_yuan": "0",
        "next_action_max_yuan": "0.1",
        "budget_allows_start": "true",
        "formal_stack_health": "healthy",
        "formal_health_url_sha256": hashlib.sha256(b"http://formal-api.internal").hexdigest(),
        "isolated_resources_ownership": "confirmed",
        "old_processing_count": "1",
        "old_processing_recovery": "disabled",
        "old_processing_batch_state": "processing",
        "old_processing_file_state": "processing/publishing",
        "old_processing_document_count": "2",
        "old_processing_documents_state": "succeeded",
        "old_processing_remote_ids": "present",
        "old_processing_source": "present_sha256_match",
        "old_processing_worker_scan": "disabled",
        "isolated_scheduler": "absent",
    }


def _canonical_bundle_sha256(
    root: Path,
    image_ids: tuple[str, str, str] = (APP_IMAGE_ID, UI_IMAGE_ID, OCR_IMAGE_ID),
) -> str:
    files = (
        "scripts/jx_cleaning_test_switch.sh",
        "docker-compose.cleaning-test.yml",
        "docker-compose.cleaning-test-ui.yml",
        "Dockerfile.cleaning-test",
        "Dockerfile.cleaning-test-ui",
        "Dockerfile.cleaning-test-ocr",
        "dist/jx_now_ai_management-0.2.1-py3-none-any.whl",
    )
    formal_url = "http://formal-api.internal"
    lines = [
        f"formal_health_url_sha256={hashlib.sha256(formal_url.encode()).hexdigest()}",
        f"approved_app_image_id={image_ids[0]}",
        f"approved_ui_image_id={image_ids[1]}",
        f"approved_ocr_image_id={image_ids[2]}",
    ]
    for name in files:
        lines.append(f"{name}={hashlib.sha256((root / name).read_bytes()).hexdigest()}")
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def _run_manifest_contract(
    tmp_path: Path,
    manifest: dict[str, str],
    *,
    batch_enabled: str = "false",
    attestation_override: dict[str, str] | None = None,
    remote_layout: bool = False,
    wheel_bytes: bytes | None = None,
    approved_image_ids: tuple[str, str, str] = (APP_IMAGE_ID, UI_IMAGE_ID, OCR_IMAGE_ID),
    docker_image_ids: tuple[str, str, str] | None = None,
    bundle_sha256: str | None = None,
    paid: bool = False,
) -> subprocess.CompletedProcess[str]:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="manifest-contract-") as temp_dir:
        contract_dir = Path(temp_dir)
        env_file = contract_dir / "isolated.env"
        env_text = (
            "CLEANING_TEST_DATABASE_URL=postgresql://ai_management_cleaning_test:redacted@"
            "isolated-postgres:5432/ai_management_cleaning_test\n"
            "CLEANING_TEST_REDIS_URL=redis://:redacted@isolated-redis:6379/6\n"
            "CLEANING_TEST_FORMAL_HEALTH_URL=http://formal-api.internal\n"
            f"CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED={batch_enabled}\n"
            f"BAILIAN_BATCH_ENABLED={batch_enabled}\n"
        )
        env_file.write_text(env_text, encoding="utf-8")
        manifest["env_file_sha256"] = hashlib.sha256(env_file.read_bytes()).hexdigest()
        manifest_path = contract_dir / "preflight.manifest"
        manifest_path.write_text(
            "\n".join(f"{key}={value}" for key, value in manifest.items()) + "\n",
            encoding="utf-8",
        )
        attestation_path = contract_dir / "candidate.attestation"
        attestation = {**manifest, **(attestation_override or {})}
        attestation_path.write_text(
            "\n".join(
                f"{key}={attestation[key]}"
                for key in (
                    "source_revision",
                    "worktree_fingerprint",
                    "wheel_sha256",
                    "release_bundle_sha256",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        wheel_path = contract_dir / "dist" / "jx_now_ai_management-0.2.1-py3-none-any.whl"
        wheel_path.parent.mkdir()
        wheel_content = wheel_bytes or b"reviewed-wheel"
        wheel_path.write_bytes(wheel_content)
        if remote_layout:
            for relative in (
                "scripts/jx_cleaning_test_switch.sh",
                "docker-compose.cleaning-test.yml",
                "docker-compose.cleaning-test-ui.yml",
                "Dockerfile.cleaning-test",
                "Dockerfile.cleaning-test-ui",
                "Dockerfile.cleaning-test-ocr",
            ):
                target = contract_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
        if remote_layout and wheel_bytes is None:
            manifest["wheel_sha256"] = hashlib.sha256(wheel_content).hexdigest()
        if remote_layout:
            manifest["release_bundle_sha256"] = _canonical_bundle_sha256(
                contract_dir, approved_image_ids
            )
            manifest_path.write_text(
                "\n".join(f"{key}={value}" for key, value in manifest.items()) + "\n",
                encoding="utf-8",
            )
            attestation_path.write_text(
                "\n".join(
                    f"{key}={manifest[key]}"
                    for key in (
                        "source_revision",
                        "worktree_fingerprint",
                        "wheel_sha256",
                        "release_bundle_sha256",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
        expected_worktree = (
            _governed_worktree_fingerprint()
            if not remote_layout
            else manifest["worktree_fingerprint"]
        )
        expected_wheel = (
            hashlib.sha256(wheel_content).hexdigest() if remote_layout else _current_wheel_sha256()
        )
        expected_revision = shlex.quote(manifest["source_revision"])
        provenance_setup = ""
        if not remote_layout:
            provenance_setup = (
                'git() { [[ "$1 $2" == "rev-parse HEAD" ]] || return 93; '
                f"printf '%s\\n' {expected_revision}; }}; "
                + f"current_governed_worktree_fingerprint() {{ printf '%s\\n' {expected_worktree}; }}; "
                + f"current_test_wheel_sha256() {{ printf '%s\\n' {expected_wheel}; }}; "
            )
        wheel_setup = (
            "; export CLEANING_TEST_WHEEL=" + shlex.quote(wheel_path.as_posix()) + "; "
            if remote_layout
            else "; "
        )
        shell_setup = (
            provenance_setup + 'docker() { [[ "$1 $2" == "image inspect" ]] || return 91; '
            'case "$4" in '
            f"*worktree-fingerprint*) printf '%s\\n' {expected_worktree}; return 0 ;; "
            f"*wheel-sha256*) printf '%s\\n' {expected_wheel}; return 0 ;; "
            "esac; "
            'case "$5" in '
            f"jx-now-ai-management:cleaning-test) printf '%s\\n' {(docker_image_ids or approved_image_ids)[0]} ;; "
            f"jx-now-ai-management-ui:cleaning-test) printf '%s\\n' {(docker_image_ids or approved_image_ids)[1]} ;; "
            f"jx-now-ai-management-ocr:cleaning-test) printf '%s\\n' {(docker_image_ids or approved_image_ids)[2]} ;; "
            "*) return 92 ;; esac; }; export CLEANING_TEST_ENV_FILE="
            + shlex.quote(env_file.as_posix())
            + "; export CLEANING_TEST_HEALTH_JSON_PYTHON=python"
            + "; export CLEANING_TEST_PREFLIGHT_MANIFEST="
            + shlex.quote(manifest_path.as_posix())
            + "; export CLEANING_TEST_ATTESTATION="
            + shlex.quote(attestation_path.as_posix())
            + "; export CLEANING_TEST_RELEASE_BUNDLE_SHA256="
            + shlex.quote(bundle_sha256 or manifest["release_bundle_sha256"])
            + "; export CLEANING_TEST_APPROVED_APP_IMAGE_ID="
            + shlex.quote(approved_image_ids[0])
            + "; export CLEANING_TEST_APPROVED_UI_IMAGE_ID="
            + shlex.quote(approved_image_ids[1])
            + "; export CLEANING_TEST_APPROVED_OCR_IMAGE_ID="
            + shlex.quote(approved_image_ids[2])
            + wheel_setup
            + "source "
            + shlex.quote((ROOT / "scripts/jx_cleaning_test_switch.sh").as_posix())
            + "; "
            + f"hostname() {{ printf '%s\\n' {shlex.quote(manifest['host'])}; }}; "
            + "require_runtime_preflight_manifest"
            + ("; require_paid_runtime_budget" if paid else "")
        )
        if remote_layout:
            shell_setup = (
                "if git rev-parse HEAD >/dev/null 2>&1; then exit 93; fi; "
                "[[ ! -e .codex ]] || exit 94; " + shell_setup
            )
        return subprocess.run(
            _bash_command("-c", shell_setup),
            cwd=contract_dir if remote_layout else ROOT,
            env={
                **os.environ,
                "CLEANING_TEST_GOVERNANCE_PYTHON": str(ROOT / ".venv" / "Scripts" / "python.exe"),
            },
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )


def test_cleaning_test_runtime_manifest_accepts_only_current_complete_binding(tmp_path):
    assert _run_manifest_contract(tmp_path, _runtime_manifest()).returncode == 0


def test_cleaning_test_runtime_manifest_allows_unknown_costs_for_no_provider_start(tmp_path):
    manifest = _runtime_manifest()
    for key in (
        "spend_settled_yuan",
        "spend_reserved_yuan",
        "daily_settled_yuan",
        "daily_reserved_yuan",
        "next_action_max_yuan",
    ):
        manifest[key] = "unknown"
    manifest["budget_allows_start"] = "unknown"
    assert _run_manifest_contract(tmp_path, manifest).returncode == 0


def test_cleaning_test_paid_budget_rejects_unknown_costs(tmp_path):
    manifest = _runtime_manifest()
    manifest["spend_reserved_yuan"] = "unknown"
    manifest["budget_allows_start"] = "unknown"
    result = _run_manifest_contract(tmp_path, manifest, paid=True)
    assert result.returncode != 0
    assert "paid_budget" in result.stderr


def test_cleaning_test_runtime_manifest_accepts_explicit_isolated_batch_opt_in(tmp_path):
    manifest = _runtime_manifest(batch_enabled="true")
    assert _run_manifest_contract(tmp_path, manifest, batch_enabled="true").returncode == 0


def test_cleaning_test_runtime_manifest_accepts_remote_layout_without_git_or_governance(tmp_path):
    manifest = _runtime_manifest()
    assert _run_manifest_contract(tmp_path, manifest, remote_layout=True).returncode == 0


def test_cleaning_test_runtime_manifest_rejects_tampered_candidate_attestation(tmp_path):
    manifest = _runtime_manifest()
    assert (
        _run_manifest_contract(
            tmp_path,
            manifest,
            attestation_override={"worktree_fingerprint": "f" * 64},
        ).returncode
        != 0
    )
    assert (
        _run_manifest_contract(
            tmp_path,
            manifest,
            attestation_override={"wheel_sha256": "e" * 64},
        ).returncode
        != 0
    )


def test_cleaning_test_runtime_manifest_rejects_tampered_uploaded_wheel(tmp_path):
    manifest = _runtime_manifest()
    assert (
        _run_manifest_contract(
            tmp_path,
            manifest,
            remote_layout=True,
            wheel_bytes=b"tampered-wheel",
        ).returncode
        != 0
    )


@pytest.mark.parametrize(
    ("field", "replacement_id", "docker_ids", "error"),
    [
        (
            "app_image_id",
            "sha256:" + "d" * 64,
            ("sha256:" + "d" * 64, UI_IMAGE_ID, OCR_IMAGE_ID),
            "manifest_app_image_not_release_approved",
        ),
        (
            "ui_image_id",
            "sha256:" + "e" * 64,
            (APP_IMAGE_ID, "sha256:" + "e" * 64, OCR_IMAGE_ID),
            "manifest_ui_image_not_release_approved",
        ),
        (
            "ocr_image_id",
            "sha256:" + "f" * 64,
            (APP_IMAGE_ID, UI_IMAGE_ID, "sha256:" + "f" * 64),
            "manifest_ocr_image_not_release_approved",
        ),
    ],
)
def test_runtime_manifest_rejects_coordinated_image_replacement(
    tmp_path, field, replacement_id, docker_ids, error
):
    manifest = _runtime_manifest()
    manifest[field] = replacement_id
    result = _run_manifest_contract(
        tmp_path,
        manifest,
        approved_image_ids=(APP_IMAGE_ID, UI_IMAGE_ID, OCR_IMAGE_ID),
        docker_image_ids=docker_ids,
        bundle_sha256=_canonical_bundle_sha256(ROOT),
    )
    assert result.returncode != 0
    assert error in result.stderr


def test_runtime_manifest_rejects_attestation_bundle_replacement(tmp_path):
    manifest = _runtime_manifest()
    result = _run_manifest_contract(
        tmp_path,
        manifest,
        attestation_override={"release_bundle_sha256": "b" * 64},
        approved_image_ids=(APP_IMAGE_ID, UI_IMAGE_ID, OCR_IMAGE_ID),
        docker_image_ids=(APP_IMAGE_ID, UI_IMAGE_ID, OCR_IMAGE_ID),
        bundle_sha256=_canonical_bundle_sha256(ROOT),
    )
    assert result.returncode != 0
    assert "attestation_release_bundle_sha256_mismatch" in result.stderr


def test_runtime_manifest_rejects_canonical_bundle_replacement(tmp_path):
    manifest = _runtime_manifest()
    replacement_ids = ("sha256:" + "d" * 64, "sha256:" + "e" * 64, "sha256:" + "f" * 64)
    manifest["app_image_id"], manifest["ui_image_id"], manifest["ocr_image_id"] = replacement_ids
    approved_bundle = _canonical_bundle_sha256(ROOT)
    result = _run_manifest_contract(
        tmp_path,
        manifest,
        approved_image_ids=replacement_ids,
        docker_image_ids=replacement_ids,
        bundle_sha256=approved_bundle,
    )
    assert result.returncode != 0
    assert "canonical_bundle_sha256_mismatch" in result.stderr


def test_cleaning_test_runtime_manifest_rejects_retagged_image_content(tmp_path):
    manifest = _runtime_manifest()
    manifest["app_image_id"] = "sha256:" + "d" * 64
    assert _run_manifest_contract(tmp_path, manifest).returncode != 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_host", "other-postgres"),
        ("database_port", "5433"),
        ("database_user", "other_role"),
        ("database_name", "other_database"),
        ("database_url_sha256", "f" * 64),
    ],
)
def test_cleaning_test_runtime_manifest_rejects_database_dsn_binding_drift(tmp_path, field, value):
    """The manifest must bind every non-secret DSN identity component and digest."""
    manifest = _runtime_manifest()
    manifest[field] = value
    assert _run_manifest_contract(tmp_path, manifest).returncode != 0


@pytest.mark.parametrize("field", ["worktree_fingerprint", "wheel_sha256"])
def test_cleaning_test_runtime_manifest_rejects_image_or_current_content_drift(tmp_path, field):
    manifest = _runtime_manifest()
    manifest[field] = "f" * 64
    assert _run_manifest_contract(tmp_path, manifest).returncode != 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "checked_at_utc",
            (datetime.now(timezone.utc) - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        (
            "checked_at_utc",
            "future-at-execution",
        ),
        ("compose_project", "other-project"),
        ("database_name", "other_database"),
        ("redis_effective_db", "5"),
        ("dataset_id", "other-dataset"),
        ("queue_backlog", "1"),
        ("queue_ownership", "unknown"),
        ("formal_stack_health", "unhealthy"),
    ],
)
def test_cleaning_test_runtime_manifest_rejects_unknown_or_unsafe_state(tmp_path, field, value):
    manifest = _runtime_manifest()
    if value == "future-at-execution":
        value = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest[field] = value
    assert _run_manifest_contract(tmp_path, manifest).returncode != 0


def test_cleaning_test_runtime_manifest_rejects_paid_budget_when_start_flag_is_false(tmp_path):
    manifest = _runtime_manifest()
    manifest["budget_allows_start"] = "false"
    result = _run_manifest_contract(tmp_path, manifest, paid=True)
    assert result.returncode != 0
    assert "paid_budget" in result.stderr


def _cleaning_test_settings_kwargs() -> dict[str, object]:
    return {
        "app_env": "test",
        "cleaning_general_only": True,
        "cleaning_test_dataset_id": "19206ce0-80a4-4a93-aa86-63e3385d2b2a",
        "dify_general_dataset_id": "19206ce0-80a4-4a93-aa86-63e3385d2b2a",
        "cleaning_test_isolation_id": "ai-management-cleaning-test",
        "cleaning_test_database_name": "ai_management_cleaning_test",
        "cleaning_test_database_user": "ai_management_cleaning_test",
        "database_url": "postgresql://ai_management_cleaning_test:secret@postgres:5432/ai_management_cleaning_test",
        "redis_url": "redis://:secret@redis:6379/6",
        "cleaning_test_redis_db": 6,
        "bailian_batch_enabled": False,
        "cleaning_test_batch_acceptance_enabled": False,
        "cleaning_daily_budget_yuan": 4.0,
        "acceptance_cleaning_budget_max_yuan": 5.0,
    }


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"app_env": "development"}, "APP_ENV=test"),
        ({"cleaning_test_isolation_id": "wrong"}, "fixed test isolation ID"),
        ({"cleaning_test_database_name": "ai_management"}, "dedicated test database"),
        ({"cleaning_test_database_user": "ai_management"}, "dedicated test database"),
        (
            {"database_url": "postgresql://ai_management:secret@postgres:5432/ai_management"},
            "dedicated test database",
        ),
        ({"redis_url": "redis://:secret@redis:6379/5"}, "Redis DB 6"),
        ({"redis_url": "redis://:secret@redis:6379/6?db=5"}, "Redis DB 6"),
        ({"cleaning_test_redis_db": 5}, "Redis DB 6"),
        ({"bailian_batch_enabled": True}, "requires explicit Batch acceptance"),
        ({"cleaning_test_batch_acceptance_enabled": True}, "requires explicit Batch acceptance"),
        ({"cleaning_daily_budget_yuan": 4.1}, "日预算不能超过 4 元"),
        ({"acceptance_cleaning_budget_max_yuan": 5.1}, "累计模型预算不能超过 5 元"),
    ],
)
def test_general_only_settings_fail_closed_for_isolation_or_budget_drift(override, message):
    from ai_management.config import Settings

    values = _cleaning_test_settings_kwargs()
    values.update(override)
    with pytest.raises(ValueError, match=message):
        Settings(_env_file=None, **values)


def test_general_only_settings_accept_exact_isolated_boundary():
    from ai_management.config import Settings

    settings = Settings(_env_file=None, **_cleaning_test_settings_kwargs())
    assert settings.cleaning_test_database_name == "ai_management_cleaning_test"
    assert settings.cleaning_test_database_user == "ai_management_cleaning_test"


def test_general_only_settings_accepts_only_explicit_matched_batch_opt_in():
    from ai_management.config import Settings

    values = _cleaning_test_settings_kwargs()
    values.update(
        bailian_batch_enabled=True,
        cleaning_test_batch_acceptance_enabled=True,
    )
    settings = Settings(_env_file=None, **values)
    assert settings.bailian_batch_enabled is True


def test_all_knowledge_write_endpoints_call_dataset_guard():
    from ai_management.api.routers import knowledge_admin

    required = (
        "upload_batch",
        "approve_model_task",
        "retry_model_contract_item_route",
        "cancel_model_task",
        "retry_publication_shard",
        "replace_publication_shard",
        "import_catalog_review_workbook",
        "retry_upload_file",
        "retry_upload_item",
        "review_upload_item",
        "set_document_status",
        "delete_document",
        "delete_uploaded_source",
    )
    for name in required:
        source = inspect.getsource(getattr(knowledge_admin, name))
        assert "_dataset_id(kb_code)" in source, name


def test_general_only_rejects_real_non_general_write_requests(service_db, monkeypatch):
    from fastapi.testclient import TestClient

    from ai_management.api.routers import knowledge_admin
    from ai_management.main import app

    class TestSettings:
        cleaning_general_only = True
        cleaning_test_dataset_id = "19206ce0-80a4-4a93-aa86-63e3385d2b2a"
        dify_general_dataset_id = "19206ce0-80a4-4a93-aa86-63e3385d2b2a"

    monkeypatch.setattr(knowledge_admin, "get_settings", lambda: TestSettings())
    headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    prefix = "/internal/v1/admin/knowledge-bases/course"
    with TestClient(app) as client:
        responses = [
            client.post(
                f"{prefix}/upload-batches",
                headers=headers,
                data={"strategy_code": "course_route_catalog"},
                files=[("files", ("course.md", b"course", "text/markdown"))],
            ),
            client.patch(f"{prefix}/documents/doc/status", headers=headers, json={"enabled": True}),
            client.request(
                "DELETE",
                f"{prefix}/documents/doc",
                headers=headers,
                json={"confirm_name": "doc"},
            ),
            client.request(
                "DELETE",
                f"{prefix}/sources/file",
                headers=headers,
                json={"confirm_name": "file"},
            ),
            client.post(f"{prefix}/upload-batches/b/files/f/retry", headers=headers),
            client.post(
                f"{prefix}/upload-batches/b/files/f/items/i/review-decisions",
                headers=headers,
                data={
                    "action": "approve",
                    "expected_revision": "0",
                    "reason": "blocked",
                    "metadata_patch": "{}",
                },
            ),
        ]
    assert [response.status_code for response in responses] == [403] * len(responses)
