#!/usr/bin/env python3
import datetime
import json
import subprocess
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
    from govlib import read_json, write_json, active_path
except Exception:
    sys.exit(0)

state = read_json(active_path(root), {}) or {}
session_id = payload.get("session_id")
if not state or not session_id:
    sys.exit(0)

# SessionEnd belongs to the main thread. Release only this session's write lease; the work
# itself remains active so a later session can safely resume and claim ownership on first write.
if state.get("owner_session_id") == session_id:
    state["owner_session_id"] = None
    state["owner_released_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    state["owner_release_reason"] = "session_end"
    state["updated_at"] = state["owner_released_at"]
    write_json(active_path(root), state)
