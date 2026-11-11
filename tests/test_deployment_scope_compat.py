import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SCOPE_FILE = Path(__file__).resolve().parents[1] / ".codex" / "governance" / "deployment_scope.py"
_SCOPE_SPEC = importlib.util.spec_from_file_location("deployment_scope_test_subject", _SCOPE_FILE)
scope = importlib.util.module_from_spec(_SCOPE_SPEC)
_SCOPE_SPEC.loader.exec_module(scope)


ENDPOINT = "synthetic-ys"
PROJECT = "synthetic-ai-management-ys"
REMOTE_ROOT = "/srv/ai-management-ys"
COMPOSE_PATH = f"{REMOTE_ROOT}/compose.yml"


@pytest.fixture
def scope_state(tmp_path, monkeypatch):
    state_path = tmp_path / "state" / "deployment-scope.json"
    monkeypatch.setattr(scope, "STATE", state_path)
    return tmp_path


def _manifest(tmp_path, *, endpoint=ENDPOINT, project=PROJECT, environments=None):
    remote_hash = hashlib.sha256(b"synthetic compose artifact").hexdigest()
    compose_evidence = tmp_path / "compose-readback.txt"
    compose_evidence.write_text(f"{COMPOSE_PATH} {remote_hash}\n", encoding="utf-8")
    compose = {
        "remote_path": COMPOSE_PATH,
        "remote_sha256": remote_hash,
        "verified_readable": True,
        "evidence_path": str(compose_evidence),
        "evidence_sha256": hashlib.sha256(compose_evidence.read_bytes()).hexdigest(),
    }
    manifest = {
        "schema_version": 1,
        "endpoint": endpoint,
        "project": project,
        "compose_project": project,
        "remote_root": REMOTE_ROOT,
        "actions": ["deploy", "restart", "replace", "reset-project-data", "readback"],
        "environments": environments or ["test", "prod"],
        "compose_files": [compose],
        "services": ["api", "worker", "test-ui"],
        "volumes": [f"{project}_db"],
        "profiles": ["test"],
        "scripts": [],
    }
    path = tmp_path / "project-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest


def _prod_evidence(tmp_path):
    proof = tmp_path / "prod-readback.txt"
    proof.write_text("synthetic remote backup/config/candidate/restore receipt", encoding="utf-8")
    digest = hashlib.sha256(proof.read_bytes()).hexdigest()
    shared = {
        "remote_reference": "synthetic://remote/artifact",
        "verified_readable": True,
        "available": True,
        "evidence_path": str(proof),
        "sha256": digest,
    }
    path = tmp_path / "prod-evidence.json"
    path.write_text(json.dumps({
        "endpoint": ENDPOINT,
        "project": PROJECT,
        "candidate": "synthetic-candidate",
        "previous_version": "synthetic-previous",
        "backup": {**shared, "endpoint": ENDPOINT, "project": PROJECT},
        "previous_config": shared,
        "previous_artifact": shared,
        "candidate_artifact": shared,
        "restore_tool": shared,
    }), encoding="utf-8")
    return path


def _create(manifest_path, *, environment="test", actions=None, evidence=None, endpoint=ENDPOINT,
            project=PROJECT):
    args = argparse.Namespace(
        authorization_ref="synthetic-test-reference",
        endpoint=endpoint,
        project=project,
        project_manifest=str(manifest_path),
        environment=environment,
        action=actions or ["deploy", "restart", "replace", "reset-project-data", "readback"],
        evidence_manifest=str(evidence) if evidence else None,
    )
    scope.create_scope(args)
    return scope._read()["scopes"][-1]["scope_id"]


def _command(*, endpoint=ENDPOINT, project=PROJECT, compose=COMPOSE_PATH, operation="up -d api"):
    remote = (f"cd {REMOTE_ROOT} && docker compose -p {project} -f {compose} {operation}")
    return f'ssh {endpoint} "{remote}"'


def _issue(scope_id, command, *, endpoint=ENDPOINT, project=PROJECT, environment="test",
           actions=None):
    args = argparse.Namespace(
        scope_id=scope_id,
        command=command,
        endpoint=endpoint,
        project=project,
        environment=environment,
        action=actions or ["deploy"],
    )
    scope.issue_receipt(args)
    return scope._read()["receipts"][-1]["receipt_id"]


def test_manifest_bound_remote_compose_command_passes(scope_state, tmp_path):
    manifest, _ = _manifest(tmp_path)
    scope_id = _create(manifest)
    command = _command()
    _issue(scope_id, command)
    assert scope.consume(command) == (True, True, "deployment scope receipt consumed")


@pytest.mark.parametrize(
    "operation",
    [
        "build api",
        "run --rm --no-deps --pull never api python -m alembic upgrade head",
    ],
)
def test_test_scope_allows_manifest_bound_candidate_build_and_migration(
    scope_state, tmp_path, operation
):
    manifest_path, manifest = _manifest(tmp_path)
    manifest["test_build_migration_services"] = ["api"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    scope_id = _create(manifest_path, environment="test", actions=["deploy"])

    command = _command(operation=operation)
    receipt_id = _issue(scope_id, command, environment="test", actions=["deploy"])

    receipt = scope._read()["receipts"][-1]
    assert receipt["receipt_id"] == receipt_id
    assert receipt["semantic"] == {
        "endpoint": ENDPOINT,
        "project": PROJECT,
        "actions": ["deploy"],
        "entrypoint": "docker-compose",
        "compose_files": [COMPOSE_PATH],
        "services": ["api"],
    }
    scope.cancel_receipt(argparse.Namespace(receipt_id=receipt_id))


@pytest.mark.parametrize(
    "operation,environment,opt_in,match",
    [
        ("build api", "test", None, "explicitly listed"),
        (
            "run --rm --no-deps --pull never api python -m alembic upgrade head",
            "test",
            None,
            "explicitly listed",
        ),
        ("build worker", "test", ["api"], "explicitly listed"),
        (
            "run --rm --no-deps --pull never api python -m alembic upgrade head extra",
            "test",
            ["api"],
            "exact manifest-bound",
        ),
        (
            "run --rm --no-deps --pull never worker python -m alembic upgrade head",
            "test",
            ["api"],
            "explicitly listed",
        ),
        (
            "run --rm --no-deps --pull never api sh -c alembic upgrade head",
            "test",
            ["api"],
            "exact manifest-bound",
        ),
        ("build api", "prod", ["api"], "test environment"),
        (
            "run --rm --no-deps --pull never api python -m alembic upgrade head",
            "prod",
            ["api"],
            "test environment",
        ),
    ],
)
def test_candidate_build_and_migration_reject_unscoped_commands(
    scope_state, tmp_path, operation, environment, opt_in, match
):
    manifest_path, manifest = _manifest(tmp_path)
    if opt_in is not None:
        manifest["test_build_migration_services"] = opt_in
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if environment == "prod":
        prod_evidence = _prod_evidence(tmp_path)
    else:
        prod_evidence = None
    scope_id = _create(
        manifest_path,
        environment=environment,
        actions=["deploy"],
        evidence=prod_evidence,
    )

    with pytest.raises(ValueError, match=match):
        _issue(scope_id, _command(operation=operation), environment=environment, actions=["deploy"])


@pytest.mark.parametrize("change", ["host", "compose_project", "compose_file"])
def test_remote_target_and_compose_files_must_match_manifest(scope_state, tmp_path, change):
    manifest, _ = _manifest(tmp_path)
    scope_id = _create(manifest)
    command = _command()
    if change == "host":
        command = _command(endpoint="other-host")
    elif change == "compose_project":
        command = _command(project="unrelated-project")
    else:
        command = _command(compose=f"{REMOTE_ROOT}/unlisted-compose.yml")
    with pytest.raises(ValueError):
        _issue(scope_id, command)


def test_action_is_inferred_and_volume_deletion_requires_test_reset_scope(scope_state, tmp_path):
    manifest, _ = _manifest(tmp_path)
    deploy_scope = _create(manifest, actions=["deploy"])
    destructive = _command(operation="down --volumes")
    with pytest.raises(ValueError, match="do not match"):
        _issue(deploy_scope, destructive, actions=["deploy"])

    reset_scope = _create(manifest, actions=["reset-project-data"])
    receipt_id = _issue(reset_scope, destructive, actions=["reset-project-data"])
    assert scope._read()["receipts"][-1]["receipt_id"] == receipt_id
    scope.cancel_receipt(argparse.Namespace(receipt_id=receipt_id))
    volume_rm = f'ssh {ENDPOINT} "docker volume rm -f {PROJECT}_db"'
    volume_id = _issue(reset_scope, volume_rm, actions=["reset-project-data"])
    scope.cancel_receipt(argparse.Namespace(receipt_id=volume_id))
    wrong_volume = f'ssh {ENDPOINT} "docker volume rm unrelated_db"'
    with pytest.raises(ValueError, match="project manifest"):
        _issue(reset_scope, wrong_volume, actions=["reset-project-data"])


def test_production_cannot_authorize_volume_reset(scope_state, tmp_path):
    manifest, _ = _manifest(tmp_path)
    with pytest.raises(ValueError, match="cannot include reset"):
        _create(manifest, environment="prod", actions=["deploy", "reset-project-data"],
                evidence=_prod_evidence(tmp_path))
    prod_scope = _create(manifest, environment="prod", actions=["deploy"],
                         evidence=_prod_evidence(tmp_path))
    with pytest.raises(ValueError):
        _issue(prod_scope, _command(operation="down -v"), environment="prod", actions=["deploy"])


def test_deployment_scripts_are_rejected_without_widening_scope(scope_state, tmp_path):
    manifest, _ = _manifest(tmp_path)
    scope_id = _create(manifest, actions=["deploy"])
    script_path = f"{REMOTE_ROOT}/scripts/jx_quick_deploy.sh"
    with pytest.raises(ValueError, match="unsupported"):
        _issue(scope_id, f'ssh {ENDPOINT} "bash {script_path} --keep-data"',
               actions=["deploy"])
    with pytest.raises(ValueError, match="unsupported"):
        _issue(scope_id, f'ssh {ENDPOINT} "python execute_ys_candidate.py"',
               actions=["deploy"])


def test_hook_recognizes_real_compose_flags_and_script_entrypoint_without_running_them(tmp_path):
    checkout = _temporary_checkout(tmp_path / "hook-checkout")
    hook = checkout / ".codex/hooks/governance_pre_tool.py"
    commands = [
        _command(),
        f'ssh {ENDPOINT} "bash {REMOTE_ROOT}/scripts/jx_quick_deploy.sh --keep-data"',
    ]
    for command in commands:
        payload = json.dumps({"cwd": str(checkout), "tool_name": "exec_command",
                              "tool_input": {"cmd": command}})
        response = subprocess.run([sys.executable, str(hook)], cwd=checkout, input=payload,
                                  capture_output=True, text=True, check=True)
        assert '"permissionDecision": "deny"' in response.stdout
    mixed = subprocess.run([sys.executable, str(hook)], cwd=checkout,
        input=json.dumps({"cwd": str(checkout), "tool_name": "exec_command",
                          "tool_input": {"cmd": commands[0] + " && git push"}}),
        capture_output=True, text=True, check=True)
    assert "Protected release/remote command" in mixed.stdout
    local = subprocess.run([sys.executable, str(hook)], cwd=checkout,
        input=json.dumps({"cwd": str(checkout), "tool_name": "exec_command",
                          "tool_input": {"cmd": "docker compose -p synthetic-project "
                                                "-f /tmp/compose.yml up -d"}}),
        capture_output=True, text=True, check=True)
    assert "Local project service" in local.stdout


def test_hook_allows_single_readonly_compose_preflight_but_blocks_a_write_chain(tmp_path):
    checkout = _temporary_checkout(tmp_path / "readonly-checkout")
    hook = checkout / ".codex/hooks/governance_pre_tool.py"
    read_commands = [
        f'ssh {ENDPOINT} "cd {REMOTE_ROOT} && docker compose -p {PROJECT} -f {COMPOSE_PATH} ps"',
        f'ssh {ENDPOINT} "docker compose --project-directory {REMOTE_ROOT} -p {PROJECT} '
        f'-f {COMPOSE_PATH} config --services"',
    ]
    for command in read_commands:
        result = subprocess.run([sys.executable, str(hook)], cwd=checkout,
            input=json.dumps({"cwd": str(checkout), "tool_name": "exec_command",
                              "tool_input": {"cmd": command}}),
            capture_output=True, text=True, check=True)
        assert "permissionDecision" not in result.stdout
    mixed_commands = [
        read_commands[0][:-1] + f" && docker compose -p {PROJECT} -f {COMPOSE_PATH} up -d\"",
        read_commands[0][:-1] + f" &&docker compose -p {PROJECT} -f {COMPOSE_PATH} up -d\"",
        f'ssh {ENDPOINT} "docker compose --project-directory {REMOTE_ROOT} -p {PROJECT} '
        f'-f {COMPOSE_PATH} config --output={REMOTE_ROOT}/changed.yml"',
    ]
    for command in mixed_commands:
        result = subprocess.run([sys.executable, str(hook)], cwd=checkout,
            input=json.dumps({"cwd": str(checkout), "tool_name": "exec_command",
                              "tool_input": {"cmd": command}}),
            capture_output=True, text=True, check=True)
        assert '"permissionDecision": "deny"' in result.stdout


def test_hook_hash_path_rejects_mixed_git_push_before_consuming(scope_state, tmp_path):
    manifest, _ = _manifest(tmp_path)
    scope_id = _create(manifest, actions=["deploy"])
    command = _command() + " && git push"
    with pytest.raises(ValueError, match="protected release"):
        _issue(scope_id, command, actions=["deploy"])


def test_duplicate_hash_hook_is_ambiguous_but_run_consumes_selected_receipt_id(scope_state, tmp_path,
                                                                                monkeypatch):
    manifest, _ = _manifest(tmp_path)
    scope_id = _create(manifest, actions=["deploy"])
    command = _command()
    first_id = _issue(scope_id, command)
    state = scope._read()
    duplicate = dict(state["receipts"][0], receipt_id="synthetic-second-receipt")
    state["receipts"].append(duplicate)
    scope._write(state)
    assert scope.consume(command) == (True, False, "ambiguous exact-command receipts; consume by receipt id")
    executed = []
    monkeypatch.setattr(scope, "_run_command", lambda cmd: executed.append(cmd) or 0)
    assert scope.run_receipt(argparse.Namespace(receipt_id=duplicate["receipt_id"])) == 0
    receipts = scope._read()["receipts"]
    assert receipts[0]["receipt_id"] == first_id and receipts[0]["status"] == "issued"
    assert receipts[1]["status"] == "executing"
    assert executed == [command]


def test_cancelled_unconsumed_receipt_does_not_lock_corrected_manifest(scope_state, tmp_path):
    manifest, _ = _manifest(tmp_path)
    scope_id = _create(manifest, actions=["deploy"])
    receipt_id = _issue(scope_id, _command(), actions=["deploy"])
    scope.cancel_receipt(argparse.Namespace(receipt_id=receipt_id))
    assert scope._read()["receipts"][0]["status"] == "cancelled"
    revised_manifest, _ = _manifest(tmp_path, project="synthetic-project-v2")
    revised = _create(revised_manifest, endpoint=ENDPOINT, project="synthetic-project-v2",
                      actions=["deploy"])
    assert revised != scope_id
    with pytest.raises(ValueError, match="only an unconsumed"):
        scope.cancel_receipt(argparse.Namespace(receipt_id=receipt_id))


def test_unknown_result_stays_blocked_until_readback_records_a_known_outcome(scope_state, tmp_path):
    manifest, _ = _manifest(tmp_path)
    scope_id = _create(manifest, actions=["deploy"])
    receipt_id = _issue(scope_id, _command(), actions=["deploy"])
    assert scope.consume(_command())[1] is True
    readback = tmp_path / "readback.txt"
    readback.write_text("synthetic observed candidate state", encoding="utf-8")
    scope.record_result(argparse.Namespace(receipt_id=receipt_id, result="unknown",
                                           readback_file=str(readback)))
    with pytest.raises(ValueError, match="unresolved prior"):
        _issue(scope_id, _command(), actions=["deploy"])
    scope.record_result(argparse.Namespace(receipt_id=receipt_id, result="success",
                                           readback_file=str(readback)))
    _issue(scope_id, _command(), actions=["deploy"])


def test_cross_process_state_lock_serializes_issue_updates(tmp_path):
    checkout = _temporary_checkout(tmp_path / "lock-checkout")
    cli = checkout / ".codex/governance/deployment_scope.py"
    manifest, _ = _manifest(tmp_path)
    created = subprocess.run([sys.executable, str(cli), "create-scope",
        "--authorization-ref", "synthetic-lock-test", "--endpoint", ENDPOINT,
        "--project", PROJECT, "--project-manifest", str(manifest),
        "--action", "deploy"], cwd=checkout, capture_output=True, text=True, check=True)
    scope_id = created.stdout.strip()
    issue_args = [sys.executable, str(cli), "issue-receipt", "--scope-id", scope_id,
        "--command", _command(), "--endpoint", ENDPOINT, "--project", PROJECT,
        "--environment", "test", "--action", "deploy"]
    subprocess.run(issue_args, cwd=checkout, capture_output=True, text=True, check=True)
    marker = checkout / "lock-held"
    lock_script = (
        "import sys,time; sys.path.insert(0, '.codex/governance'); "
        "import deployment_scope as d; "
        "held=d._state_lock(); held.__enter__(); "
        "open('lock-held','w').write('ready'); time.sleep(.5); held.__exit__(None,None,None)"
    )
    holder = subprocess.Popen([sys.executable, "-c", lock_script], cwd=checkout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert marker.exists()
    started = time.monotonic()
    competing = subprocess.run(issue_args, cwd=checkout, capture_output=True, text=True)
    elapsed = time.monotonic() - started
    holder.communicate(timeout=5)
    assert competing.returncode == 2
    assert "unresolved prior receipt" in competing.stderr
    assert elapsed >= .35


@pytest.mark.parametrize("damage", ["missing-status", "unknown-status", "consumed-mismatch", "orphan-scope"])
def test_corrupt_receipt_record_fails_closed(scope_state, tmp_path, damage):
    manifest, _ = _manifest(tmp_path)
    scope_id = _create(manifest, actions=["deploy"])
    _issue(scope_id, _command(), actions=["deploy"])
    state = scope._read()
    receipt = state["receipts"][0]
    if damage == "missing-status":
        receipt.pop("status")
    elif damage == "unknown-status":
        receipt["status"] = "mystery"
    elif damage == "consumed-mismatch":
        receipt["consumed"] = True
    else:
        receipt["scope_id"] = "absent"
    scope._write(state)
    with pytest.raises(ValueError, match="incomplete|invalid"):
        scope.create_scope(argparse.Namespace(authorization_ref="x", endpoint=ENDPOINT,
            project=PROJECT, project_manifest=str(manifest), environment="test",
            action=["deploy"], evidence_manifest=None))


def test_windows_runner_uses_native_powershell_for_safe_local_syntax(monkeypatch, tmp_path):
    if sys.platform != "win32":
        pytest.skip("PowerShell launch behavior is Windows-specific")
    calls = []
    monkeypatch.setattr(scope.shutil, "which", lambda name: "powershell.exe")
    monkeypatch.setattr(scope.subprocess, "run",
                        lambda argv, **kwargs: calls.append((argv, kwargs)) or argparse.Namespace(returncode=0))
    assert scope._run_command("$env:SCOPE_TEST='ok'; Write-Output 'synthetic'") == 0
    assert calls[0][0][0] == "powershell.exe"
    assert calls[0][0][1:3] == ["-NoProfile", "-NonInteractive"]


def _temporary_checkout(path):
    root = Path(path)
    for rel in (".codex/governance", ".codex/hooks", ".codex/state"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1]
    for rel in (".codex/governance/deployment_scope.py", ".codex/governance/govlib.py",
                ".codex/hooks/governance_pre_tool.py"):
        shutil.copy2(source / rel, root / rel)
    return root
