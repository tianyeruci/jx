#!/usr/bin/env python3
"""Narrow local deployment authorization scopes and one-use command receipts."""
import argparse
import contextlib
import datetime
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".codex" / "state" / "deployment-scope.json"
ALLOWED_ACTIONS = {"deploy", "restart", "replace", "reset-project-data", "readback"}
RECEIPT_STATES = {"issued", "executing", "success", "failure", "unknown_pending_readback", "cancelled"}
PROTECTED_ACTIONS = (
    r"(^|[;&|\n]\s*)git\s+push\b",
    r"(^|[;&|\n]\s*)gh\s+pr\s+merge\b",
    r"(^|[;&|\n]\s*)(npm|pnpm)\s+publish\b",
    r"(^|[;&|\n]\s*)yarn\s+(npm\s+)?publish\b",
    r"(^|[;&|\n]\s*)cargo\s+publish\b",
    r"(^|[;&|\n]\s*)twine\s+upload\b",
    r"(^|[;&|\n]\s*)dotnet\s+nuget\s+push\b",
    r"(^|[;&|\n]\s*)terraform\s+(apply|destroy)\b",
    r"(^|[;&|\n]\s*)kubectl\s+(apply|delete|replace|patch)\b",
    r"(^|[;&|\n]\s*)helm\s+(install|upgrade|uninstall|rollback)\b",
    r"(^|[;&|\n]\s*)vercel\b.*(--prod|--production)\b",
    r"(^|[;&|\n]\s*)netlify\s+deploy\b.*--prod\b",
    r"(^|[;&|\n]\s*)fly\s+deploy\b",
)


def _read():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "scopes": [], "receipts": []}
    except (OSError, ValueError) as exc:
        raise ValueError(f"deployment scope state is unreadable; refusing to reset it: {exc}") from exc
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or not isinstance(data.get("scopes"), list) or not isinstance(data.get("receipts"), list)):
        raise ValueError("deployment scope state has an unsupported or malformed schema; refusing to reset it")
    _validate_state(data)
    return data


def _validate_state(data):
    scopes = {}
    for item in data["scopes"]:
        required = {"scope_id", "authorization_ref", "endpoint", "project", "environment",
                    "actions", "evidence", "project_manifest", "created_at"}
        if (not isinstance(item, dict) or not required.issubset(item)
                or not all(isinstance(item[k], str) and item[k].strip()
                           for k in ("scope_id", "authorization_ref", "endpoint", "project", "created_at"))
                or not isinstance(item.get("environment"), str)
                or item.get("environment") not in {"test", "prod"}
                or not isinstance(item.get("project_manifest"), dict)
                or item.get("project_manifest", {}).get("project") != item.get("project")
                or not all(isinstance(item.get("project_manifest", {}).get(k), str)
                           for k in ("manifest_path", "sha256", "remote_root"))
                or not isinstance(item.get("actions"), list) or not item["actions"]
                or not all(isinstance(action, str) for action in item["actions"])
                or set(item["actions"]) - ALLOWED_ACTIONS or not isinstance(item.get("evidence"), dict)
                or item["scope_id"] in scopes):
            raise ValueError("deployment scope state contains an invalid scope record; refusing to reset it")
        scopes[item["scope_id"]] = item
    seen_receipts = set()
    endpoint_envs = {}
    for item in scopes.values():
        existing = endpoint_envs.setdefault(item["endpoint"], item["environment"])
        if existing != item["environment"]:
            raise ValueError("deployment scope state assigns multiple environments to one endpoint")
    for item in data["receipts"]:
        required = {"receipt_id", "scope_id", "endpoint", "project", "environment", "actions",
                    "command", "command_sha256", "confirmed_by_user", "consumed", "status", "issued_at"}
        required.update({"semantic", "adapter_evidence", "project_manifest_sha256"})
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("deployment scope state contains an incomplete receipt; refusing to reset it")
        string_fields = ("receipt_id", "scope_id", "endpoint", "project", "environment",
                         "command", "command_sha256", "issued_at", "status")
        if (not all(isinstance(item[k], str) and item[k].strip() for k in string_fields)
                or not isinstance(item.get("actions"), list) or not item["actions"]
                or not all(isinstance(action, str) for action in item["actions"])
                or not isinstance(item.get("consumed"), bool)):
            raise ValueError("deployment scope state contains an invalid receipt record; refusing to reset it")
        scope = scopes.get(item["scope_id"])
        status = item.get("status")
        if (not scope or not all(isinstance(item[k], str) and item[k].strip()
                                 for k in ("receipt_id", "endpoint", "project", "environment",
                                           "command", "command_sha256", "issued_at"))
                or item["receipt_id"] in seen_receipts or status not in RECEIPT_STATES
                or item.get("confirmed_by_user") is not False
                or (status in {"issued", "cancelled"}) != (item["consumed"] is False)
                or item.get("endpoint") != scope["endpoint"] or item.get("project") != scope["project"]
                or item.get("environment") != scope["environment"]
                or item.get("project_manifest_sha256") != scope["project_manifest"].get("sha256")
                or not isinstance(item.get("semantic"), dict)
                or not isinstance(item.get("adapter_evidence"), dict)
                or set(item["actions"]) - set(scope["actions"])
                or hashlib.sha256(item["command"].encode("utf-8")).hexdigest() != item["command_sha256"]):
            raise ValueError("deployment scope state contains an invalid receipt record; refusing to reset it")
        seen_receipts.add(item["receipt_id"])


def _write(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, STATE)
    finally:
        tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def _state_lock():
    lock_path = STATE.with_name(STATE.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _serialized(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _state_lock():
            return func(*args, **kwargs)
    return wrapper


def _sha(path):
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def _remote_path(path, root, label):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"{label} must be an absolute remote path")
    pure = PurePosixPath(path)
    root_path = PurePosixPath(root)
    if ".." in pure.parts or not pure.is_relative_to(root_path):
        raise ValueError(f"{label} must stay within the manifest project root")
    return pure.as_posix()


def _verify_artifact(item, root, label):
    if not isinstance(item, dict):
        raise ValueError(f"manifest {label} evidence must be an object")
    remote_path = _remote_path(item.get("remote_path"), root, label)
    remote_hash = str(item.get("remote_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", remote_hash) or item.get("verified_readable") is not True:
        raise ValueError(f"manifest {label} must include a readable remote SHA-256 receipt")
    evidence_path = item.get("evidence_path")
    evidence_hash = _sha(evidence_path) if evidence_path else None
    if not evidence_hash or evidence_hash.lower() != str(item.get("evidence_sha256") or "").lower():
        raise ValueError(f"manifest {label} local evidence file is missing or hash-mismatched")
    evidence_file = Path(evidence_path)
    if not evidence_file.is_absolute():
        evidence_file = ROOT / evidence_file
    try:
        evidence_text = evidence_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"manifest {label} evidence file is not readable text: {exc}") from exc
    if remote_path not in evidence_text or remote_hash not in evidence_text.lower():
        raise ValueError(f"manifest {label} evidence does not contain its remote path and SHA-256")
    result = {"remote_path": remote_path, "remote_sha256": remote_hash,
              "evidence_path": str(Path(evidence_path).resolve()), "evidence_sha256": evidence_hash}
    return result


def _load_project_manifest(path, endpoint, project):
    manifest_path = Path(path)
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"project manifest is missing or unreadable: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("project manifest schema is unsupported")
    if (not isinstance(endpoint, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]+", endpoint)
            or manifest.get("endpoint") != endpoint or manifest.get("project") != project
            or manifest.get("compose_project") != project):
        raise ValueError("project manifest endpoint/project/Compose name does not match scope")
    if not isinstance(project, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise ValueError("project name is not a supported Compose identifier")
    root = manifest.get("remote_root")
    if not isinstance(root, str) or not root.startswith("/") or ".." in PurePosixPath(root).parts:
        raise ValueError("project manifest remote_root must be an absolute normalized path")
    actions = manifest.get("actions")
    environments = manifest.get("environments")
    if (not isinstance(actions, list) or not actions
            or not all(isinstance(x, str) and x in ALLOWED_ACTIONS for x in actions)
            or not isinstance(environments, list) or not environments
            or not all(x in {"test", "prod"} for x in environments)):
        raise ValueError("project manifest must declare supported actions and environments")
    compose_files = manifest.get("compose_files")
    if not isinstance(compose_files, list) or not compose_files:
        raise ValueError("project manifest must list readable Compose files")
    checked_compose = [_verify_artifact(x, root, "Compose file") for x in compose_files]
    if len({x["remote_path"] for x in checked_compose}) != len(checked_compose):
        raise ValueError("project manifest contains duplicate Compose paths")
    services = manifest.get("services")
    volumes = manifest.get("volumes", [])
    profiles = manifest.get("profiles", [])
    test_build_migration_services = manifest.get("test_build_migration_services", [])
    if (not isinstance(services, list) or not services
            or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in services)
            or not isinstance(volumes, list)
            or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in volumes)
            or not isinstance(profiles, list)
            or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in profiles)):
        raise ValueError("project manifest services/profiles are malformed")
    if (not isinstance(test_build_migration_services, list)
            or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", x)
                       for x in test_build_migration_services)
            or len(set(test_build_migration_services)) != len(test_build_migration_services)
            or set(test_build_migration_services) - set(services)):
        raise ValueError("project manifest test build/migration services are malformed or undeclared")
    if test_build_migration_services and (
            "test" not in environments or "deploy" not in actions):
        raise ValueError("project manifest test build/migration opt-in requires test environment and deploy action")
    if manifest.get("scripts"):
        raise ValueError("script deployment is unsupported; use a manifest-bound direct Compose command")
    return {
        "manifest_path": str(manifest_path.resolve()),
        "sha256": _sha(manifest_path),
        "manifest": manifest,
        "endpoint": endpoint,
        "project": project,
        "remote_root": root,
        "actions": sorted(set(actions)),
        "environments": sorted(set(environments)),
        "compose_files": checked_compose,
        "services": sorted(set(services)),
        "test_build_migration_services": sorted(set(test_build_migration_services)),
        "volumes": sorted(set(volumes)),
        "profiles": sorted(set(profiles)),
    }


@_serialized
def create_scope(args):
    if not args.authorization_ref.strip() or not args.endpoint.strip() or not args.project.strip():
        raise ValueError("authorization-ref, endpoint and project are required")
    if args.environment not in {"test", "prod"}:
        raise ValueError("environment must be test or prod")
    project_manifest = _load_project_manifest(args.project_manifest, args.endpoint.strip(), args.project.strip())
    if args.environment not in project_manifest["environments"]:
        raise ValueError("project manifest does not authorize this environment")
    actions = sorted(set(args.action or []))
    if not actions or set(actions) - set(project_manifest["actions"]):
        raise ValueError("unsupported or empty action list")
    doc = _read()
    for existing in doc["scopes"]:
        if existing.get("endpoint") == args.endpoint.strip() and existing.get("environment") != args.environment:
            raise ValueError("endpoint is already bound to another environment; one server supports one environment")
    if args.environment == "prod":
        if "reset-project-data" in actions:
            raise ValueError("prod scope cannot include reset-project-data")
        evidence = load_prod_evidence(args.evidence_manifest, args.endpoint.strip(), args.project.strip())
    else:
        evidence = {}
    for prior in doc.get("receipts", []):
        prior_scope = next((x for x in doc["scopes"] if x.get("scope_id") == prior.get("scope_id")), None)
        if (prior_scope and (prior_scope["endpoint"], prior_scope["project"])
                == (args.endpoint.strip(), args.project.strip())
                and prior.get("status") in {"issued", "executing", "unknown_pending_readback"}):
            raise ValueError("endpoint/project has unresolved prior receipt; record result/readback first")
    scope = {"scope_id": str(uuid.uuid4()), "authorization_ref": args.authorization_ref.strip(),
             "endpoint": args.endpoint.strip(), "project": args.project.strip(),
             "environment": args.environment, "actions": actions, "evidence": evidence,
             "project_manifest": {k: project_manifest[k] for k in ("manifest_path", "sha256", "remote_root",
                                                                     "project")},
             "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    doc["scopes"].append(scope)
    _write(doc)
    print(scope["scope_id"])


def _is_single_remote_compose_read(command):
    try:
        outer = shlex.split(command, posix=True)
        if len(outer) != 3 or outer[0].lower() != "ssh":
            return False
        remote = shlex.split(outer[2], posix=True)
    except ValueError:
        return False
    if (not remote or "\n" in outer[2]
            or any(token != "&&" and any(c in token for c in "&;|><") for token in remote)):
        return False
    if "&&" in remote:
        if remote.count("&&") != 1:
            return False
        split = remote.index("&&")
        if len(remote[:split]) != 2 or remote[0] != "cd" or not remote[1].startswith("/"):
            return False
        remote = remote[split + 1:]
    if remote[:2] != ["docker", "compose"]:
        return False
    i = 2
    while i < len(remote):
        if remote[i] in {"-p", "--project-name", "-f", "--file", "--project-directory",
                         "--profile", "--env-file"}:
            i += 2
            continue
        if remote[i].startswith(("-p=", "--project-name=", "-f=", "--file=",
                                 "--project-directory=", "--profile=", "--env-file=")):
            i += 1
            continue
        if remote[i].startswith("-"):
            return False
        subcommand = remote[i]
        tail = remote[i + 1:]
        if subcommand not in {"ps", "config"}:
            return False
        boolean_flags = {
            "ps": {"--all", "-a", "--quiet", "-q", "--no-trunc", "--orphans"},
            "config": {"--quiet", "-q", "--services", "--volumes", "--resolve-image-digests",
                       "--no-interpolate", "--no-env-resolution", "--profiles", "--models",
                       "--images", "--environment", "--variables"},
        }[subcommand]
        valued_flags = {
            "ps": {"--format", "--filter"},
            "config": {"--format", "--hash"},
        }[subcommand]
        pos = 0
        while pos < len(tail):
            token = tail[pos]
            if "=" in token and token.startswith("--"):
                key, value = token.split("=", 1)
                if key not in valued_flags or not value:
                    return False
                pos += 1
            elif token in valued_flags:
                if pos + 1 >= len(tail) or not tail[pos + 1] or tail[pos + 1].startswith("-"):
                    return False
                pos += 2
            elif token in boolean_flags:
                pos += 1
            elif subcommand == "ps" and re.fullmatch(r"[A-Za-z0-9_.-]+", token):
                pos += 1
            else:
                return False
        return True
    return False


def looks_like_deployment_command(command):
    text = str(command or "")
    if _is_single_remote_compose_read(text):
        return False
    return bool(re.search(
        r"(?is)(?:\bssh\b.*(?:docker\s+compose|docker\s+volume\s+rm|systemctl\s+(?:start|stop|restart)"
        r"|\b(?:bash|sh)\b.*(?:deploy|release|execute_ys_candidate).*)"
        r"|docker\s+compose\b.*\b(?:up|down|restart|start|stop|rm)\b"
        r"|docker\s+volume\s+rm\b"
        r"|(?:jx_quick_deploy\.sh|execute_ys_candidate\.py)(?:\s|$))", text,
    ))


def _load_scope_manifest(scope):
    ref = scope.get("project_manifest") or {}
    current = _load_project_manifest(ref.get("manifest_path", ""), scope["endpoint"], scope["project"])
    if (current["sha256"] != ref.get("sha256") or current["remote_root"] != ref.get("remote_root")
            or current["project"] != ref.get("project")):
        raise ValueError("project manifest changed after scope creation")
    if scope["environment"] not in current["environments"]:
        raise ValueError("project manifest does not include the scoped environment")
    if set(scope["actions"]) - set(current["actions"]):
        raise ValueError("project manifest does not include the scoped actions")
    return current


def _remote_command(command, endpoint, manifest):
    try:
        outer = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError(f"unsupported shell quoting: {exc}") from exc
    if len(outer) != 3 or outer[0].lower() != "ssh":
        raise ValueError("only a single quoted direct SSH command is eligible for this adapter")
    if outer[1] != endpoint:
        raise ValueError("SSH host does not match the scoped endpoint")
    try:
        remote = shlex.split(outer[2], posix=True)
    except ValueError as exc:
        raise ValueError(f"unsupported remote shell quoting: {exc}") from exc
    if not remote or any(op in remote for op in (";", "|", "||")) or "\n" in outer[2]:
        raise ValueError("complex remote shell syntax is not eligible for automatic scope")
    root = manifest["remote_root"]
    cwd_verified = False
    if "&&" in remote:
        if remote.count("&&") != 1:
            raise ValueError("only one canonical cd && deployment command is supported")
        split = remote.index("&&")
        if remote[:split] != ["cd", root]:
            raise ValueError("remote working directory does not match the project manifest")
        cwd_verified = True
        remote = remote[split + 1:]
    return remote, cwd_verified


def _remote_compose_file(value, root):
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError("Compose file path cannot escape the project root")
    if not path.is_absolute():
        path = PurePosixPath(root) / path
    return path.as_posix()


def _parse_compose(remote, scope, manifest, cwd_verified):
    project = scope["project"]
    root = manifest["remote_root"]
    if remote[:2] == ["docker", "volume"]:
        if remote[2:3] != ["rm"]:
            raise ValueError("only explicit docker volume rm for this project is supported")
        names = [x for x in remote[3:] if x not in {"-f", "--force"}]
        if scope["environment"] == "prod":
            raise ValueError("production scope cannot remove project volumes")
        if not names or set(names) - set(manifest["volumes"]):
            raise ValueError("volume rm is unsupported unless every volume is explicitly listed in the project manifest")
        return {"endpoint": scope["endpoint"], "project": project,
                "actions": ["reset-project-data"], "entrypoint": "docker-volume-rm"}
    if remote[:2] != ["docker", "compose"]:
        raise ValueError("unsupported remote deployment command")
    compose_project = None
    files = []
    profile_values = []
    project_directory_seen = False
    i = 2
    subcommand = None
    while i < len(remote):
        token = remote[i]
        if token in {"-p", "--project-name", "-f", "--file", "--profile", "--project-directory"}:
            if i + 1 >= len(remote):
                raise ValueError(f"missing value for Compose option {token}")
            value = remote[i + 1]
            if token in {"-p", "--project-name"}:
                if compose_project is not None:
                    raise ValueError("duplicate Compose project option")
                compose_project = value
            elif token in {"-f", "--file"}:
                files.append(_remote_compose_file(value, root))
            elif token == "--profile":
                profile_values.append(value)
            elif token == "--project-directory":
                if value != root:
                    raise ValueError("Compose project directory does not match the project manifest")
                project_directory_seen = True
            i += 2
            continue
        if token.startswith(("--project-name=", "--file=", "--profile=", "--project-directory=")):
            key, value = token.split("=", 1)
            token = {"--project-name": "-p", "--file": "-f",
                     "--profile": "--profile", "--project-directory": "--project-directory"}[key]
            remote = remote[:i] + [token, value] + remote[i + 1:]
            continue
        if token.startswith("-"):
            raise ValueError(f"unsupported Compose option: {token}")
        subcommand = token
        i += 1
        break
    if compose_project != project:
        raise ValueError("Compose -p/--project-name must exactly match the scoped project")
    if not project_directory_seen and not cwd_verified:
        raise ValueError("Compose project root must be explicit in the command or manifest-bound working directory")
    declared_files = {x["remote_path"] for x in manifest["compose_files"]}
    if not files or len(set(files)) != len(files) or set(files) - declared_files:
        raise ValueError("Compose -f files are not exactly bound by the project manifest")
    if set(profile_values) - set(manifest["profiles"]):
        raise ValueError("Compose profile is not declared in the project manifest")
    if profile_values and scope["environment"] not in manifest["environments"]:
        raise ValueError("Compose profile is not allowed in this environment")
    tail = remote[i:]
    if subcommand in {"build", "run"}:
        if scope["environment"] != "test":
            raise ValueError("Compose build and migration commands are test environment only")
        test_services = manifest["test_build_migration_services"]
        if not test_services:
            raise ValueError("Compose build and migration require an explicitly listed test service")
        if subcommand == "build":
            if len(tail) != 1 or tail[0] not in test_services:
                raise ValueError("Compose build command must exactly name an explicitly listed test service")
            services = tail
        else:
            migration_suffix = ["python", "-m", "alembic", "upgrade", "head"]
            if len(tail) != 10 or tail[:4] != ["--rm", "--no-deps", "--pull", "never"]:
                raise ValueError("Compose migration must use the exact manifest-bound test command")
            service = tail[4]
            if service not in test_services:
                raise ValueError("Compose migration service must be explicitly listed for test deployment")
            if tail[5:] != migration_suffix:
                raise ValueError("Compose migration must use the exact manifest-bound test command")
            services = [service]
        return {"endpoint": scope["endpoint"], "project": project, "actions": ["deploy"],
                "entrypoint": "docker-compose", "compose_files": sorted(files),
                "services": sorted(services)}
    if subcommand not in {"up", "down", "restart"}:
        raise ValueError("only Compose up/down/restart and opted-in test build/migration are supported")
    allowed_flags = {
        "up": {"-d", "--detach", "--build", "--no-build", "--force-recreate",
               "--no-deps", "--remove-orphans", "--pull"},
        "restart": {"--no-deps"},
        "down": {"-v", "--volumes", "--remove-orphans"},
    }[subcommand]
    services = []
    deletes_volumes = False
    pos = 0
    while pos < len(tail):
        token = tail[pos]
        if token in {"-v", "--volumes"}:
            if subcommand != "down":
                raise ValueError("volume deletion flag is only supported on Compose down")
            deletes_volumes = True
            pos += 1
        elif token in allowed_flags:
            if token == "--pull":
                if pos + 1 >= len(tail) or tail[pos + 1] not in {"always", "missing", "never"}:
                    raise ValueError("unsupported Compose pull policy")
                pos += 2
            else:
                pos += 1
        elif token.startswith("-"):
            raise ValueError(f"unsupported Compose {subcommand} option: {token}")
        else:
            services.append(token)
            pos += 1
    if set(services) - set(manifest["services"]):
        raise ValueError("Compose command names services not listed in the project manifest")
    if subcommand == "up":
        action = "deploy"
    elif subcommand == "restart":
        action = "restart"
    else:
        action = "reset-project-data" if deletes_volumes else "replace"
    if scope["environment"] == "prod" and action == "reset-project-data":
        raise ValueError("production scope cannot down --volumes or remove project volumes")
    return {"endpoint": scope["endpoint"], "project": project, "actions": [action],
            "entrypoint": "docker-compose", "compose_files": sorted(files), "services": sorted(services)}


def _inspect_command(command, scope, manifest):
    remote, cwd_verified = _remote_command(command, scope["endpoint"], manifest)
    if remote[:2] in (["docker", "compose"], ["docker", "volume"]):
        semantic = _parse_compose(remote, scope, manifest, cwd_verified)
        evidence = {"manifest_path": scope["project_manifest"]["manifest_path"],
                    "manifest_sha256": scope["project_manifest"]["sha256"]}
        return semantic, evidence
    if any(Path(token).name in {"execute_ys_candidate.py", "jx_quick_deploy.sh"} for token in remote):
        raise ValueError("deployment scripts are unsupported by this adapter")
    if remote and remote[0] in {"bash", "sh", "python", "python3"}:
        raise ValueError("script and interpreter commands are unsupported; use direct Compose")
    raise ValueError("unknown command shape; automatic scope is refused")


def inspect_command(command, scope):
    manifest = _load_scope_manifest(scope)
    semantic, evidence = _inspect_command(command, scope, manifest)
    return semantic, evidence


def _validate_receipt_semantics(scope, receipt):
    semantic, evidence = inspect_command(receipt["command"], scope)
    if (semantic != receipt.get("semantic") or evidence != receipt.get("adapter_evidence")
            or semantic["actions"] != receipt.get("actions")):
        raise ValueError("receipt no longer matches the manifest-bound command semantics")
    return semantic


@_serialized
def issue_receipt(args):
    doc = _read()
    scope = next((x for x in doc["scopes"] if x.get("scope_id") == args.scope_id), None)
    if scope is None:
        raise ValueError("unknown deployment scope")
    if (args.endpoint != scope["endpoint"] or args.project != scope["project"]
            or args.environment != scope["environment"]):
        raise ValueError("receipt target/environment is outside the scope")
    actions = sorted(set(args.action or []))
    if not actions or set(actions) - set(scope["actions"]):
        raise ValueError("receipt action is outside the scope")
    for prior in doc.get("receipts", []):
        prior_scope = next((x for x in doc["scopes"] if x.get("scope_id") == prior.get("scope_id")), None)
        if (prior_scope and (prior_scope["endpoint"], prior_scope["project"])
                == (scope["endpoint"], scope["project"])
                and prior.get("status") in {"issued", "executing", "unknown_pending_readback"}):
            raise ValueError("endpoint/project has unresolved prior receipt; record result/readback first")
    command = args.command.strip()
    if not command:
        raise ValueError("command cannot be empty")
    if any(re.search(pattern, command, re.I) for pattern in PROTECTED_ACTIONS):
        raise ValueError("deployment scope cannot authorize an independently protected release/publish action")
    if re.search(r"(?i)^\s*(?:&\s*)?(?:docker\s+compose\s+up|docker\s+run|uvicorn|fastapi\s+dev)\b", command):
        raise ValueError("local project service or second environment commands are forbidden")
    semantic, adapter_evidence = inspect_command(command, scope)
    if semantic["actions"] != actions:
        raise ValueError(f"declared receipt actions {actions} do not match command actions {semantic['actions']}")
    if scope["environment"] == "prod" and "reset-project-data" in semantic["actions"]:
        raise ValueError("production scope cannot reset project data or delete Compose volumes")
    receipt = {"receipt_id": str(uuid.uuid4()), "scope_id": scope["scope_id"],
               "endpoint": scope["endpoint"], "project": scope["project"],
               "environment": scope["environment"], "actions": actions,
               "command": command, "semantic": semantic, "adapter_evidence": adapter_evidence,
               "project_manifest_sha256": scope["project_manifest"]["sha256"],
               "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
               "confirmed_by_user": False, "consumed": False, "status": "issued",
               "issued_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    doc["receipts"].append(receipt)
    _write(doc)
    print(receipt["receipt_id"])


def consume(command):
    with _state_lock():
        return _consume(command)


def _consume(command):
    """Consume one exact receipt and return (protected, allowed, reason)."""
    digest = hashlib.sha256(command.strip().encode("utf-8")).hexdigest()
    doc = _read()
    matches = [r for r in doc["receipts"] if r["status"] == "issued" and
               r["command_sha256"] == digest and not r["consumed"]]
    if not matches:
        return False, False, ""
    if len(matches) != 1:
        return True, False, "ambiguous exact-command receipts; consume by receipt id"
    return _consume_selected(doc, matches[0])


def _consume_by_receipt_id(receipt_id):
    doc = _read()
    receipt = next((r for r in doc["receipts"] if r["receipt_id"] == receipt_id), None)
    if not receipt or receipt["status"] != "issued" or receipt["consumed"]:
        return False, False, "receipt is missing, already consumed or awaiting result/readback"
    return _consume_selected(doc, receipt)


def _consume_selected(doc, receipt):
    scope = next((s for s in doc["scopes"] if s["scope_id"] == receipt["scope_id"]), None)
    if not scope or receipt["confirmed_by_user"] is not False:
        return True, False, "invalid deployment scope receipt"
    if (receipt["endpoint"] != scope["endpoint"] or receipt["project"] != scope["project"]
            or receipt["environment"] != scope["environment"]
            or receipt["project_manifest_sha256"] != scope["project_manifest"]["sha256"]
            or set(receipt["actions"]) - set(scope["actions"])):
        return True, False, "receipt target/action is outside the scope"
    try:
        _validate_receipt_semantics(scope, receipt)
        if scope["environment"] == "prod":
            if "reset-project-data" in receipt["actions"] or not validate_evidence(scope["evidence"], scope):
                return True, False, "prod receipt evidence is missing or changed"
    except ValueError as exc:
        return True, False, str(exc)
    receipt["consumed"] = True
    receipt["status"] = "executing"
    receipt["consumed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _write(doc)
    return True, True, "deployment scope receipt consumed"


def load_prod_evidence(path, endpoint, project):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise ValueError("prod requires a readable evidence manifest JSON with same-target backup")
    if not isinstance(data, dict) or data.get("endpoint") != endpoint or data.get("project") != project:
        raise ValueError("prod evidence manifest target/project does not match scope")
    if not all(isinstance(data.get(k), str) and data[k].strip() for k in ("candidate", "previous_version")):
        raise ValueError("prod evidence manifest must name candidate and previous_version")
    for key in ("backup", "previous_config", "previous_artifact", "candidate_artifact", "restore_tool"):
        item = data.get(key)
        label = "same-target backup" if key == "backup" else key
        if not isinstance(item, dict) or not item.get("remote_reference"):
            raise ValueError(f"prod evidence manifest requires a remote reference for {label}")
        proof_valid = item.get("available") is True if key == "restore_tool" else item.get("verified_readable") is True
        if not proof_valid or not item.get("evidence_path") or not item.get("sha256"):
            raise ValueError(f"prod evidence manifest requires verified {label} receipt evidence and sha256")
        actual = _sha(item["evidence_path"])
        if not actual or actual.lower() != str(item["sha256"]).lower():
            raise ValueError(f"prod sanitized receipt evidence is missing, unreadable or hash-mismatched: {key}")
    if data["backup"].get("endpoint") != endpoint or data["backup"].get("project") != project:
        raise ValueError("backup evidence must bind the same endpoint and project")
    return {"manifest_path": str(path), "manifest_sha256": _sha(path), "manifest": data}


def validate_evidence(evidence, scope):
    if not evidence:
        return False
    current = load_prod_evidence(evidence.get("manifest_path", ""),
                                 scope.get("endpoint", ""), scope.get("project", ""))
    return current.get("manifest_sha256") == evidence.get("manifest_sha256")


@_serialized
def record_result(args):
    doc = _read()
    receipt = next((x for x in doc.get("receipts", []) if x.get("receipt_id") == args.receipt_id), None)
    if not receipt or receipt.get("status") not in {"executing", "unknown_pending_readback"}:
        raise ValueError("receipt is not awaiting a result")
    if args.result not in {"success", "failure", "unknown"}:
        raise ValueError("result must be success, failure or unknown")
    readback_digest = _sha(args.readback_file) if args.readback_file else None
    if not readback_digest:
        raise ValueError("record-result requires a readable readback file")
    if receipt.get("status") == "unknown_pending_readback" and not readback_digest:
        raise ValueError("unknown execution state requires readable readback evidence before resolution")
    if args.result == "unknown":
        if not readback_digest:
            receipt["status"] = "unknown_pending_readback"
            _write(doc)
            raise ValueError("unknown result requires a readable readback file before retry")
        receipt["status"] = "unknown_pending_readback"
        receipt["readback_path"] = args.readback_file
        receipt["readback_sha256"] = readback_digest
    else:
        receipt["status"] = args.result
        if readback_digest:
            receipt["readback_path"] = args.readback_file
            receipt["readback_sha256"] = readback_digest
    receipt["result_recorded_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _write(doc)
    print(receipt["status"])


def run_receipt(args):
    with _state_lock():
        doc = _read()
        receipt = next((x for x in doc.get("receipts", []) if x.get("receipt_id") == args.receipt_id), None)
        if not receipt or receipt.get("status") != "issued" or receipt.get("consumed"):
            raise ValueError("receipt is missing, already consumed or awaiting result/readback")
        command = receipt.get("command")
        if (not isinstance(command, str) or not command.strip()
                or hashlib.sha256(command.encode("utf-8")).hexdigest() != receipt.get("command_sha256")):
            raise ValueError("receipt command is missing or hash-mismatched")
        protected, allowed, reason = _consume_by_receipt_id(args.receipt_id)
        if not protected or not allowed:
            raise ValueError(reason or "receipt could not be consumed")
    return _run_command(command)


def _run_command(command):
    if os.name == "nt":
        shell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
        if not shell:
            raise ValueError("PowerShell is unavailable; refusing to run this PowerShell command through another shell")
        result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                                cwd=ROOT, check=False)
    else:
        result = subprocess.run(command, cwd=ROOT, shell=True, check=False)
    print(f"command_exit_code={result.returncode}; receipt remains executing until record-result with readback")
    return result.returncode


@_serialized
def cancel_receipt(args):
    doc = _read()
    receipt = next((x for x in doc["receipts"] if x["receipt_id"] == args.receipt_id), None)
    if not receipt or receipt["status"] != "issued" or receipt["consumed"]:
        raise ValueError("only an unconsumed issued receipt can be cancelled")
    receipt["status"] = "cancelled"
    receipt["cancelled_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _write(doc)
    print("cancelled")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)
    create = sub.add_parser("create-scope")
    create.add_argument("--authorization-ref", required=True)
    create.add_argument("--endpoint", required=True)
    create.add_argument("--project", required=True)
    create.add_argument("--project-manifest", required=True)
    create.add_argument("--environment", default="test", choices=["test", "prod"])
    create.add_argument("--action", action="append", required=True)
    create.add_argument("--evidence-manifest")
    create.set_defaults(func=create_scope)
    issue = sub.add_parser("issue-receipt")
    issue.add_argument("--scope-id", required=True)
    issue.add_argument("--command", required=True)
    issue.add_argument("--endpoint", required=True)
    issue.add_argument("--project", required=True)
    issue.add_argument("--environment", required=True, choices=["test", "prod"])
    issue.add_argument("--action", action="append", required=True)
    issue.set_defaults(func=issue_receipt)
    result = sub.add_parser("record-result")
    result.add_argument("--receipt-id", required=True)
    result.add_argument("--result", required=True, choices=["success", "failure", "unknown"])
    result.add_argument("--readback-file")
    result.set_defaults(func=record_result)
    run = sub.add_parser("run")
    run.add_argument("--receipt-id", required=True)
    run.set_defaults(func=run_receipt)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("--receipt-id", required=True)
    cancel.set_defaults(func=cancel_receipt)
    args = parser.parse_args()
    try:
        result = args.func(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
