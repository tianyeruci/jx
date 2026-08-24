#!/usr/bin/env python3
import hashlib
import json
import re
import subprocess
import datetime
import sys
from pathlib import Path

payload = {}
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

root = Path(payload.get("cwd") or Path.cwd())
try:
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode == 0 and p.stdout.strip():
        root = Path(p.stdout.strip())
except Exception:
    pass

gov = root / ".codex" / "governance"
sys.path.insert(0, str(gov))
try:
    from govlib import read_json, write_json, active_path, release_approval_path, git_fingerprint
except Exception:
    sys.exit(0)

state = read_json(active_path(root), {}) or {}
tool = str(payload.get("tool_name") or "")
tool_input = payload.get("tool_input") or {}
command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
session_id = payload.get("session_id")


def block(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    raise SystemExit(0)


def shell_looks_mutating(cmd):
    c = cmd.strip().lower()
    patterns = [
        r"(^|[;&|\n]\s*)(rm|del|erase|rmdir|remove-item|move-item|copy-item|new-item|set-content|add-content|out-file|tee|touch|mkdir|mv|cp)\b",
        r"(^|[;&|\n]\s*)git\s+(add|commit|checkout|switch|reset|clean|merge|rebase|cherry-pick|restore|push|tag)\b",
        r"(^|[;&|\n]\s*)(npm|pnpm|yarn|bun)\s+(install|add|remove|update|publish)\b",
        r"(^|[;&|\n]\s*)(pip|pip3|uv|poetry)\s+(install|add|remove|sync)\b",
        r"(^|[;&|\n]\s*)(dotnet)\s+(add|remove|format)\b",
        r"(^|[;&|\n]\s*)(terraform)\s+(apply|destroy|import)\b",
        r"(^|[;&|\n]\s*)(kubectl)\s+(apply|delete|replace|patch|scale|rollout)\b",
        r"(^|[;&|\n]\s*)(helm)\s+(install|upgrade|uninstall|rollback)\b",
        r"(^|[;&|\n]\s*)(alembic|prisma)\b.*\b(upgrade|migrate|deploy|reset)\b",
        r"(^|[^>])>>(?!>)|(^|[^>])>(?!>)",
        r"\bsed\s+-i\b",
    ]
    return any(re.search(p, c, re.I) for p in patterns)


def tool_likely_mutating():
    if tool == "apply_patch":
        return True
    if tool == "Bash":
        return shell_looks_mutating(command)
    low = tool.lower()
    return any(k in low for k in ("write", "edit", "create", "delete", "remove", "update", "patch", "move", "rename"))


def _norm_rel(value):
    try:
        p = Path(str(value))
        if p.is_absolute():
            p = p.resolve().relative_to(root.resolve())
        return p.as_posix().lstrip("./")
    except Exception:
        return None


def mutation_paths():
    paths = []
    if tool == "apply_patch" and command:
        for m in re.finditer(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", command, re.M):
            rel = _norm_rel(m.group(1).strip())
            if rel: paths.append(rel)
    if isinstance(tool_input, dict):
        def walk(obj, key=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    lk = str(k).lower()
                    if lk in {"path", "file", "file_path", "filepath", "destination", "dest", "target_path"} and isinstance(v, str):
                        rel = _norm_rel(v)
                        if rel: paths.append(rel)
                    else:
                        walk(v, lk)
            elif isinstance(obj, list):
                for v in obj: walk(v, key)
        walk(tool_input)
    return sorted(set(paths))


def preimplementation_path_allowed(rel):
    rel = rel.replace("\\", "/")
    return (
        rel.startswith("docs/")
        or rel.startswith(".codex/")
        or rel in {"AGENTS.md", ".gitignore"}
    )


def is_governance_cli(cmd):
    c = cmd.strip().lower().replace("\\", "/")
    if any(x in c for x in (";", "&&", "||", "\n")):
        return False
    return ".codex/governance/gov.py" in c or ".codex/governance/gov.ps1" in c

# Standard/High work has explicit human gates. Before implementation, allow requirements/architecture
# docs and governance bookkeeping, but do not let normal local write tools start production coding early.
preimplementation = state.get("phase") in {"discovery", "requirements", "prototype", "architecture", "planning", "blocked"}
if state and state.get("risk") in {"standard", "high"} and preimplementation and tool_likely_mutating():
    if tool == "Bash":
        if not is_governance_cli(command):
            block(
                "Standard/High work cannot run a mutating shell command before the Implementation Gate. "
                "Use read-only discovery, edit approved governance/prototype artifacts through their document paths, "
                "or complete the requirements/architecture/implementation approval and enter implementation first."
            )
    else:
        paths = mutation_paths()
        if not paths or any(not preimplementation_path_allowed(x) for x in paths):
            block(
                "Standard/High work cannot modify production/project files before the Implementation Gate. "
                "Before implementation, writes are limited to docs/, .codex/, AGENTS.md, and .gitignore. "
                "Keep disposable prototypes under the active feature docs/prototype area."
            )

# One write owner per checkout. Read-only tool calls must never claim the write lease.
# Active work is also branch-bound because ignored local state survives manual branch switches.
mutating = tool_likely_mutating()
active_write_phase = state.get("phase") in {"implementation", "review", "release"}
if state and mutating and active_write_phase and state.get("work_branch"):
    p = subprocess.run(["git", "branch", "--show-current"], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    actual_branch = p.stdout.strip() if p.returncode == 0 else None
    if actual_branch != state.get("work_branch"):
        block(
            f"Active work belongs to branch {state.get('work_branch')!r}, but this checkout is on {actual_branch!r}. "
            "Switch back to the active work branch, or finish/abandon that task before writing here."
        )

# Claim immediately before the first likely mutation so two main sessions cannot both begin
# writing just because neither has produced a diff yet. Subagents share the parent session id.
owner = state.get("owner_session_id")
if state and session_id and active_write_phase and mutating:
    if owner and owner != session_id:
        block(
            "This checkout already has an active write owner in another Codex session. "
            "Use a separate worktree or finish/resolve the existing work before mutating this checkout."
        )
    if not owner:
        state["owner_session_id"] = session_id
        state["owner_claimed_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        write_json(active_path(root), state)

# Exact-command release gate for common remote/production-impacting Bash actions.
protected_patterns = [
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
]
if tool == "Bash" and command and any(re.search(p, command, re.I) for p in protected_patterns):
    approval = read_json(release_approval_path(root), {}) or {}
    digest = hashlib.sha256(command.strip().encode("utf-8")).hexdigest()
    current_release_fp = git_fingerprint(root, scope="review")
    if (
        approval.get("command_sha256") != digest
        or approval.get("consumed") is True
        or approval.get("release_fingerprint") != current_release_fp
    ):
        block(
            "Protected release/remote command requires explicit user approval for this exact command and the current reviewed diff. "
            "After all completion gates are current and the user approves it, record the approval with: "
            f"gov approve-release --command {json.dumps(command)} --confirmed-by-user, then retry."
        )
