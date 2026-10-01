from pathlib import Path
import hashlib
import fnmatch
import json
import os
import re
import subprocess
from typing import Iterable

GOVERNANCE_VERSION = "2.4.0"


def run_git(root, args, check=False, text=False):
    p = subprocess.run(
        ["git", *args],
        cwd=Path(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )
    if check and p.returncode != 0:
        err = p.stderr if text else p.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git {' '.join(args)} failed: {err.strip()}")
    return p


def find_root(start=None):
    """Find a governed repository from Git or the .codex/governance marker."""
    start = Path(start or os.getcwd()).resolve()
    try:
        p = run_git(start, ["rev-parse", "--show-toplevel"], text=True)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip()).resolve()
    except (OSError, RuntimeError):
        pass
    for candidate in (start, *start.parents):
        if (candidate / ".codex" / "governance" / "project.json").exists():
            return candidate
    return start


def root_from_governance_file(file_path):
    """Resolve repo root from repo/.codex/governance/<file>."""
    p = Path(file_path).resolve()
    try:
        root = p.parents[2]
    except IndexError:
        return find_root(p.parent)
    if (root / ".codex" / "governance").exists():
        return root
    return find_root(p.parent)


def is_git_repo(root):
    try:
        return run_git(root, ["rev-parse", "--is-inside-work-tree"], text=True).returncode == 0
    except OSError:
        return False


def read_json(path, default=None, strict=False):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if strict:
            raise
        return default
    except json.JSONDecodeError:
        if strict:
            raise
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def hash_file(path):
    h = hashlib.sha256()
    p = Path(path)
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _nul_paths(data: bytes):
    return [x.decode("utf-8", errors="surrogateescape") for x in data.split(b"\0") if x]


def _changed_paths(root):
    root = Path(root)
    paths = set()
    if not is_git_repo(root):
        for p in root.rglob("*"):
            if p.is_file():
                paths.add(p.relative_to(root).as_posix())
        return paths
    commands = [
        ["diff", "--name-only", "-z", "--no-ext-diff"],
        ["diff", "--cached", "--name-only", "-z", "--no-ext-diff"],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ]
    for args in commands:
        p = run_git(root, args)
        if p.returncode == 0:
            paths.update(_nul_paths(p.stdout))
    # Deleted files and mode-only changes are represented in status even if content is absent.
    p = run_git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    status = p.stdout if p.returncode == 0 else b""
    return paths, status


def _scope_includes(rel: str, scope: str):
    rel = rel.replace("\\", "/")
    parts = tuple(x for x in rel.split("/") if x)
    generated_dirs = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", "node_modules", ".next", ".nuxt", ".svelte-kit", ".gradle", "target"}
    generated_names = {".coverage", "coverage.xml", ".DS_Store", "Thumbs.db"}
    generated_suffixes = (".pyc", ".pyo", ".class", ".o")
    if any(part in generated_dirs for part in parts) or (parts and parts[-1] in generated_names) or rel.endswith(generated_suffixes):
        return False
    # Isolated test runs intentionally use a root-level, named basetemp.  Keep
    # them out of source/implementation fingerprints so runtime output cannot
    # become a product module, but bind the original evidence to quality/review
    # fingerprints for traceability.
    pytest_run_dir = bool(
        parts
        and (
            re.fullmatch(r"\.pytest-(?:terra|canonical)-[A-Za-z0-9._-]+", parts[0])
            or (
                len(parts) >= 2
                and parts[0] == "tmp"
                and re.fullmatch(r"pytest-[A-Za-z0-9._-]+", parts[1])
            )
        )
    )
    if pytest_run_dir and scope not in {"quality", "review"}:
        return False
    always_excluded = (
        ".codex/state/",
        ".codex/governance/__pycache__/",
        ".codex/hooks/__pycache__/",
    )
    if rel.startswith(always_excluded):
        return False
    if rel in {".codex/governance/repo-facts.json", "docs/PROJECT_STATUS.md"}:
        return False
    if rel.endswith("/feature.json") and rel.startswith("docs/features/"):
        return False
    if scope in {"quality", "implementation", "source"}:
        if rel.startswith("docs/"):
            return False
    if scope == "implementation" and (rel.startswith(".codex/") or rel in {"AGENTS.md", ".gitignore"}):
        # Product/module ownership excludes the governance control plane. Governance config
        # has separate approval/config fingerprints and must not become a business module.
        return False
    if scope == "source":
        # Pre-Implementation Gate baseline: governance/instruction bookkeeping is allowed
        # during requirements/architecture planning, but production source/tests/project
        # configuration must not change before the implementation contract is approved.
        if rel.startswith(".codex/") or rel in {"AGENTS.md", ".gitignore"}:
            return False
    return True


def _preimplementation_paths(root):
    """Return paths present before the active implementation work began.

    A shared checkout may legitimately contain user changes from an earlier task.
    Governance must not attribute those paths to the current feature, while still
    treating paths created or changed during the current work cycle as owned code.
    """
    state_path = Path(root).resolve() / ".codex" / "state" / "active-work.json"
    try:
        state = read_json(state_path, {}, strict=False) or {}
    except (OSError, ValueError, TypeError):
        return set()
    return {str(path).replace("\\", "/") for path in (state.get("preimplementation_paths") or [])}


def changed_paths(root, scope="implementation", include_preimplementation=False):
    """Return stable repo-relative paths currently changed in the selected governance scope."""
    root = Path(root).resolve()
    changed = _changed_paths(root)
    if isinstance(changed, tuple):
        paths, _status = changed
    else:
        paths = changed
    if not include_preimplementation:
        paths -= _preimplementation_paths(root)
    return sorted(p for p in paths if _scope_includes(p, scope))


def _worktree_index_snapshot(root):
    """Return a Git-like current content snapshot independent of commit metadata.

    Clean/staged tracked files reuse index mode/blob identities. Unstaged and untracked
    files are hashed through `git hash-object`, so Git attributes/autocrlf normalization
    matches the blob that `git add` would produce. Unstaged deletions are represented by
    absence, exactly as they will be after staging/commit.
    """
    root = Path(root).resolve()
    index_entries = {}
    p = run_git(root, ["ls-files", "-s", "-z"])
    if p.returncode == 0:
        for raw in p.stdout.split(b"\0"):
            if not raw or b"\t" not in raw:
                continue
            meta_raw, path_raw = raw.split(b"\t", 1)
            rel = path_raw.decode("utf-8", errors="surrogateescape")
            meta = meta_raw.decode("ascii", errors="replace")
            parts = meta.split()
            if len(parts) >= 3:
                index_entries.setdefault(rel, []).append((parts[0], parts[1], parts[2]))
            else:
                index_entries.setdefault(rel, []).append(("?", meta, "?"))

    p = run_git(root, ["diff", "--name-only", "-z", "--no-ext-diff"])
    unstaged = set(_nul_paths(p.stdout)) if p.returncode == 0 else set()
    p = run_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    untracked = set(_nul_paths(p.stdout)) if p.returncode == 0 else set()

    # Resolve whether executable mode is meaningful for this repository. Git on Windows
    # commonly has core.filemode=false, so executable bits from the host filesystem must
    # not manufacture a different index identity there.
    p = run_git(root, ["config", "--bool", "core.filemode"], text=True)
    filemode = p.returncode == 0 and p.stdout.strip().lower() == "true"

    dynamic = sorted(
        rel for rel in (unstaged | untracked)
        if os.path.lexists(root / rel) and not (root / rel).is_dir()
    )
    blob_ids = {}
    if dynamic:
        # --stdin-paths applies Git's normal path-aware clean filters and returns hashes
        # in input order. Newlines in file names are legal but exceptionally rare; fall
        # back to one path-aware invocation for those names instead of corrupting framing.
        normal = [rel for rel in dynamic if "\n" not in rel and "\r" not in rel]
        if normal:
            proc = subprocess.run(
                ["git", "hash-object", "--stdin-paths"],
                cwd=root,
                input=("\n".join(normal) + "\n").encode("utf-8", errors="surrogateescape"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if proc.returncode != 0:
                raise RuntimeError("git hash-object --stdin-paths failed: " + proc.stderr.decode("utf-8", errors="replace").strip())
            hashes = proc.stdout.decode("ascii", errors="replace").splitlines()
            if len(hashes) != len(normal):
                raise RuntimeError("git hash-object returned an unexpected number of hashes")
            blob_ids.update(zip(normal, hashes))
        for rel in (x for x in dynamic if x not in blob_ids):
            proc = run_git(root, ["hash-object", "--path", rel, rel], text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"git hash-object failed for {rel}: {proc.stderr.strip()}")
            blob_ids[rel] = proc.stdout.strip()

    def current_mode(rel):
        path = root / rel
        if path.is_symlink():
            return "120000"
        if filemode:
            try:
                if path.stat().st_mode & 0o111:
                    return "100755"
            except OSError:
                pass
        return "100644"

    snapshot = {}
    # Index entries are already normalized Git identities. Conflict stages are preserved
    # as a tuple so unresolved merges cannot appear identical to a clean file.
    for rel, metas in index_entries.items():
        if rel in unstaged:
            if rel in blob_ids:
                snapshot[rel] = (current_mode(rel), blob_ids[rel], "content")
            # Missing unstaged path = current deletion; omit it from the current tree.
            continue
        stage0 = [m for m in metas if len(m) >= 3 and m[2] == "0"]
        if len(stage0) == 1 and len(metas) == 1:
            mode, blob, _stage = stage0[0]
            snapshot[rel] = (mode, blob, "content")
        else:
            snapshot[rel] = ("conflict", *[" ".join(m) for m in sorted(metas)])

    for rel in untracked:
        if rel in blob_ids:
            snapshot[rel] = (current_mode(rel), blob_ids[rel], "content")
    return snapshot



def git_fingerprint(root, scope="review"):
    """Fingerprint current governed content, stable across content-preserving commits.

    Local state/generated metadata are excluded by `_scope_includes`. The Git commit id
    is intentionally *not* included: committing an already-validated worktree must not
    make documentation/quality/review stale by itself. Branch binding and exact release
    gates protect task/release identity separately.
    """
    root = Path(root).resolve()
    h = hashlib.sha256()
    h.update(f"scope:{scope}\0".encode())

    if scope in {"quality", "review"}:
        # feature.json and PROJECT_STATUS.md are generated/control-plane files and
        # are excluded from the ordinary path walk below. Bind only the semantic
        # Feature projection here so approval, module, and documentation changes
        # stale the gate without making review self-writing fields self-invalidating.
        projection = feature_governance_projection(root)
        h.update(b"feature-governance-projection\0")
        h.update(projection.encode("utf-8"))
        h.update(b"\0")

    if is_git_repo(root):
        snapshot = _worktree_index_snapshot(root)
        for rel in sorted(p for p in snapshot if _scope_includes(p, scope)):
            h.update(rel.encode("utf-8", errors="surrogateescape")); h.update(b"\0")
            identity = snapshot[rel]
            for part in identity:
                h.update(str(part).encode("utf-8", errors="surrogateescape")); h.update(b"\0")
            h.update(b"\0")
        return h.hexdigest()

    # Fallback for explicitly non-Git governed folders. Bootstrap normally initializes Git.
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if not _scope_includes(rel, scope):
            continue
        h.update(rel.encode("utf-8", errors="surrogateescape")); h.update(b"\0")
        try:
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


def feature_governance_projection(root):
    """Return stable semantic Feature metadata for quality/review fingerprints."""
    rows = []
    for path in feature_metadata_files(root):
        meta = read_json(path, {}, strict=False) or {}
        modules = []
        for module in meta.get("modules") or []:
            if not isinstance(module, dict):
                continue
            modules.append({key: module.get(key) for key in (
                "id", "title", "status", "paths", "acceptance_ids", "optional",
                "last_change_summary", "last_changed_at",
            )})
        doc = meta.get("documentation") or {}
        rows.append({
            "id": meta.get("id") or path.parent.name,
            "title": meta.get("title"),
            "kind": meta.get("kind"),
            "risk": meta.get("risk"),
            "status": meta.get("status"),
            "retired": bool(meta.get("retired")),
            "retired_status": meta.get("retired_status"),
            "superseded_by": meta.get("superseded_by") or [],
            "requirements_approved": bool(meta.get("requirements_approved")),
            "requirements_fingerprint": meta.get("requirements_fingerprint"),
            "implementation_approved": bool(meta.get("implementation_approved")),
            "implementation_fingerprint": meta.get("implementation_fingerprint"),
            "architecture_fingerprint": meta.get("architecture_fingerprint"),
            "quality_config_fingerprint": meta.get("quality_config_fingerprint"),
            "feature_validation_fingerprint": meta.get("feature_validation_fingerprint"),
            "module_plan_fingerprint": meta.get("module_plan_fingerprint"),
            "ready_for_luna": bool(meta.get("ready_for_luna")),
            "prototype": meta.get("prototype") or {},
            "modules": sorted(modules, key=lambda item: str(item.get("id"))),
            "documentation": {
                "status": doc.get("status"),
                "pending_changed_paths": sorted(doc.get("pending_changed_paths") or []),
                "last_sync_summary": doc.get("last_sync_summary"),
                "changelog_fingerprint": doc.get("changelog_fingerprint"),
            },
        })
    return json.dumps(sorted(rows, key=lambda item: str(item.get("id"))), ensure_ascii=False, sort_keys=True, separators=(",", ":"))



def feature_metadata_files(root):
    base = Path(root) / "docs" / "features"
    return sorted(base.glob("*/feature.json")) if base.exists() else []


def module_path_patterns(meta):
    patterns = []
    for module in (meta or {}).get("modules") or []:
        if not isinstance(module, dict):
            continue
        for value in module.get("paths") or []:
            value = str(value).replace("\\", "/").strip().lstrip("./")
            if value and value not in patterns:
                patterns.append(value)
    return patterns


def path_matches_pattern(rel, pattern):
    rel = str(rel).replace("\\", "/").lstrip("./")
    pattern = str(pattern).replace("\\", "/").strip().lstrip("./")
    if not pattern:
        return False
    if pattern.endswith("/"):
        return rel.startswith(pattern)
    if not any(ch in pattern for ch in "*?["):
        return rel == pattern or rel.startswith(pattern.rstrip("/") + "/")
    return fnmatch.fnmatchcase(rel, pattern)


def matching_modules(meta, paths):
    result = {}
    for module in (meta or {}).get("modules") or []:
        if not isinstance(module, dict) or not module.get("id"):
            continue
        matched = [rel for rel in (paths or []) if any(path_matches_pattern(rel, pat) for pat in module.get("paths") or [])]
        if matched:
            result[str(module["id"])] = sorted(dict.fromkeys(matched))
    return result


def related_features_for_paths(root, paths):
    """Return active feature-id -> matching paths from declared module ownership.

    Archived/superseded records remain historical documentation, but no longer own current
    implementation paths; replacement/current features must declare ownership explicitly.
    """
    related = {}
    for meta_path in feature_metadata_files(root):
        meta = read_json(meta_path, {}) or {}
        if meta.get("retired") or meta.get("status") in {"archived", "superseded"}:
            continue
        fid = str(meta.get("id") or meta_path.parent.name)
        matched = [rel for rel in (paths or []) if any(path_matches_pattern(rel, pat) for pat in module_path_patterns(meta))]
        if matched:
            related[fid] = sorted(dict.fromkeys(matched))
    return related


def unmapped_paths(root, paths):
    related = related_features_for_paths(root, paths)
    mapped = {p for values in related.values() for p in values}
    return sorted(p for p in (paths or []) if p not in mapped)

def active_path(root):
    return Path(root) / ".codex" / "state" / "active-work.json"


def quality_path(root):
    return Path(root) / ".codex" / "state" / "quality.json"


def release_approval_path(root):
    return Path(root) / ".codex" / "state" / "release-approval.json"
