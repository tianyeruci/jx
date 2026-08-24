#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

payload = {}
try:
    payload = json.load(sys.stdin)
except Exception:
    payload = {}
root = Path(payload.get("cwd") or Path.cwd())
try:
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode == 0 and p.stdout.strip():
        root = Path(p.stdout.strip())
except Exception:
    pass
state_path = root / ".codex" / "state" / "active-work.json"
if not state_path.exists():
    sys.exit(0)
try:
    s = json.loads(state_path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

fid = s.get("feature_id")
parts = [
    f"Active engineering work: {s.get('kind')} / {s.get('risk')} / phase={s.get('phase')}.",
    f"Title: {s.get('title')}.",
]
if fid:
    parts.append(f"Feature: {fid}. Read docs/features/{fid}/SPEC.md and IMPLEMENTATION.md as applicable.")
parts.append("Architecture source: docs/architecture/PROFILE.md.")
gov_py = root / ".codex" / "governance" / "gov.py"
if gov_py.exists():
    subprocess.run([sys.executable, str(gov_py), "render-status"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    nxt = subprocess.run([sys.executable, str(gov_py), "next-step"], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if nxt.returncode == 0 and nxt.stdout.strip():
        parts.append("Workflow guidance: " + nxt.stdout.strip())
owner = s.get("owner_session_id")
sid = payload.get("session_id")
if owner and sid and owner != sid and s.get("phase") in {"implementation", "review", "release"}:
    parts.append("WARNING: another Codex session owns writes in this checkout; use a separate worktree for parallel write work.")
if s.get("concurrency_conflict"):
    parts.append("WARNING: a cross-session write conflict was detected; inspect the diff and resolve the concurrency gate before completion.")
out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": " ".join(parts)}}
print(json.dumps(out, ensure_ascii=False))
