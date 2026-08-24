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

gov_py = root / ".codex" / "governance" / "gov.py"
state_path = root / ".codex" / "state" / "active-work.json"
if not gov_py.exists() or not state_path.exists():
    print(json.dumps({"continue": True}))
    sys.exit(0)
try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except Exception:
    print(json.dumps({"continue": True}))
    sys.exit(0)
if state.get("phase") not in {"implementation", "review", "release"}:
    print(json.dumps({"continue": True}))
    sys.exit(0)

p = subprocess.run([sys.executable, str(gov_py), "gate-status", "--json"], cwd=root,
                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    result = json.loads(p.stdout) if p.stdout.strip() else {"ready": False, "reasons": [p.stderr.strip() or "Gate status failed."]}
except Exception:
    result = {"ready": False, "reasons": [p.stderr.strip() or p.stdout.strip() or "Gate status returned invalid output."]}

if result.get("ready"):
    print(json.dumps({"continue": True}))
    sys.exit(0)
reason = " ".join(result.get("reasons") or ["Engineering completion gates are not satisfied."])
if payload.get("stop_hook_active"):
    # Prevent an infinite Stop->continuation loop. Surface the unresolved blocker and let the
    # turn end after one automatic continuation attempt.
    print(json.dumps({"continue": True, "systemMessage": "Governance still has unresolved completion gates: " + reason}, ensure_ascii=False))
else:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
