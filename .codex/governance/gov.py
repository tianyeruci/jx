#!/usr/bin/env python3
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

from govlib import (
    GOVERNANCE_VERSION,
    active_path,
    changed_paths,
    find_root,
    git_fingerprint,
    hash_file,
    is_git_repo,
    matching_modules,
    module_path_patterns,
    related_features_for_paths,
    unmapped_paths,
    quality_path,
    read_json,
    release_approval_path,
    root_from_governance_file,
    write_json,
)

ROOT = root_from_governance_file(__file__)
GOV = ROOT / ".codex" / "governance"
DOCS = ROOT / "docs"
TEMPLATES = GOV / "templates"
STATE = ROOT / ".codex" / "state"
ARCH = DOCS / "architecture" / "PROFILE.md"
PHASES = ["discovery", "requirements", "prototype", "architecture", "planning", "implementation", "review", "release", "blocked", "done", "abandoned"]
KINDS = ["greenfield", "feature", "bug", "engineering-change", "maintenance", "release", "trivial"]
RISKS = ["trivial", "standard", "high"]
QUALITY_PHASES = ["format", "lint", "typecheck", "build", "unit", "integration", "e2e"]


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def slugify(s):
    s = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", s.strip()).strip("-").lower()
    return s[:48] or "feature"


def deep_merge(defaults, current):
    if not isinstance(defaults, dict) or not isinstance(current, dict):
        return current
    out = dict(defaults)
    for k, v in current.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_project(strict=True):
    path = GOV / "project.json"
    data = read_json(path, None, strict=strict)
    if data is None:
        raise SystemExit(f"Missing {path}. Run the governance bootstrap first.")
    defaults = {
        "schema_version": 4,
        "governance_version": GOVERNANCE_VERSION,
        "project": {"name": "uninitialized", "application_shape": "unknown", "runtime_mode": "windows-native", "architecture_level": "uninitialized"},
        "quality": {"default_timeout_seconds": 600, "commands": {k: [] for k in QUALITY_PHASES}},
        "review": {"standard_independent_review": True, "high_risk_sol_review": True},
        "workflow": {"one_write_owner_per_checkout": True, "auto_git_init": True, "auto_branch_standard_high": True},
        "documentation": {"language": "zh-CN", "single_current_state_file": True},
    }
    merged = deep_merge(defaults, data)
    merged["schema_version"] = 4
    merged["governance_version"] = GOVERNANCE_VERSION
    return merged


def save_project(data):
    data["schema_version"] = 4
    data["governance_version"] = GOVERNANCE_VERSION
    write_json(GOV / "project.json", data)


def fill_template(path, **kwargs):
    text = Path(path).read_text(encoding="utf-8")
    for k, v in kwargs.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def feature_dir(fid):
    return DOCS / "features" / fid


def feature_json(fid):
    return feature_dir(fid) / "feature.json"


def feature_defaults(fid="", title="", kind="feature", risk="standard"):
    return {
        "schema_version": 4,
        "id": fid,
        "title": title,
        "kind": kind,
        "risk": risk,
        "status": "draft",
        "retired": False,
        "retired_status": None,
        "retired_at": None,
        "retire_reason": None,
        "superseded_by": [],
        "requirements_approved": False,
        "requirements_fingerprint": None,
        "requirements_approved_at": None,
        "implementation_approved": False,
        "implementation_fingerprint": None,
        "architecture_fingerprint": None,
        "quality_config_fingerprint": None,
        "feature_validation_fingerprint": None,
        "module_plan_fingerprint": None,
        "implementation_approved_at": None,
        "ready_for_luna": False,
        "prototype": {"decision": "pending", "status": "not_started", "path": None, "based_on_requirements_fingerprint": None, "artifact_fingerprint": None},
        "review_required": risk != "trivial",
        "review_status": "pending",
        "review_fingerprint": None,
        "reviewed_at": None,
        "modules": [],
        "validation_commands": [],
        "documentation": {
            "status": "current",
            "last_code_change_at": None,
            "last_synced_at": None,
            "last_sync_summary": None,
            "pending_changed_paths": [],
            "changelog_fingerprint": None,
        },
    }


def migrate_feature_meta(meta, fid=None):
    if not isinstance(meta, dict):
        meta = {}
    fid = fid or meta.get("id") or ""
    merged = deep_merge(feature_defaults(fid, meta.get("title", ""), meta.get("kind", "feature"), meta.get("risk", "standard")), meta)
    merged["schema_version"] = 4
    merged["id"] = fid
    if merged.get("implementation_approved") and not merged.get("implementation_fingerprint"):
        # v2 approval booleans were not tied to document content. Fail closed after migration.
        merged["implementation_approved"] = False
        merged["ready_for_luna"] = False
        merged["migration_note"] = "v2 implementation approval was invalidated because it was not content-bound. Re-approve with the current content-bound gate."
    if merged.get("requirements_approved") and not merged.get("requirements_fingerprint"):
        merged["requirements_approved"] = False
        merged["ready_for_luna"] = False
        merged["migration_note"] = "v2 requirements approval was invalidated because it was not content-bound. Re-approve with the current content-bound gate."
    return merged


def load_feature(fid, required=True):
    path = feature_json(fid)
    meta = read_json(path, None, strict=False)
    if meta is None:
        if required:
            raise SystemExit(f"Missing feature metadata: {path}")
        return None
    return migrate_feature_meta(meta, fid)


def save_feature(fid, meta, render=True):
    meta = migrate_feature_meta(meta, fid)
    meta["updated_at"] = now()
    write_json(feature_json(fid), meta)
    if render:
        render_status()


def active_state(required=True):
    s = read_json(active_path(ROOT), None, strict=False)
    if s is None and required:
        raise SystemExit("No active work. Run gov start-work first.")
    return s or {}


def save_active(s):
    s["updated_at"] = now()
    write_json(active_path(ROOT), s)


def content_hash(path):
    p = Path(path)
    return hash_file(p) if p.exists() else None


def section_text(text, heading_contains):
    lines = text.splitlines()
    start = None
    level = None
    needle = heading_contains.lower()
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if m and needle in m.group(2).lower():
            start = i + 1
            level = len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        m = re.match(r"^(#{1,6})\s+", lines[j].strip())
        if m and len(m.group(1)) <= level:
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def open_questions_resolved(text):
    sec = section_text(text, "Open Questions")
    if sec is None:
        return False
    normalized = [re.sub(r"^[\-*+]\s*", "", x.strip()).strip("` ").lower() for x in sec.splitlines() if x.strip()]
    normalized = [x for x in normalized if not x.startswith("<!--")]
    return bool(normalized) and all(x in {"none", "无", "无。", "n/a", "not applicable"} for x in normalized)


def replace_marker_block(text, begin, end, replacement):
    a = text.find(begin)
    b = text.find(end)
    block = f"{begin}\n{replacement.strip()}\n{end}"
    if a >= 0 and b >= a:
        b += len(end)
        return text[:a] + block + text[b:]
    return text.rstrip() + "\n\n" + block + "\n"


def ensure_no_governance_placeholders(text, label):
    # Never treat ordinary business text such as “TODO app” as an unfinished template.
    # Only governance-owned sentinel markers or unresolved template variables block approval.
    if "[GOV-TODO]" in text or re.search(r"\{\{[A-Z0-9_-]+\}\}", text):
        raise SystemExit(f"{label} still contains governance template placeholders; resolve them before approval.")


def ids_in_section(text, heading, prefix):
    sec = section_text(text, heading)
    if sec is None:
        return set()
    return set(re.findall(rf"\b{re.escape(prefix)}-[A-Za-z0-9._-]+\b", sec))


def requirements_contract_reasons(text):
    reasons = []
    required = [
        "Goal", "Non-goals", "Actors and Scenarios", "Functional Requirements",
        "Acceptance Criteria", "Model-generated Test Intent", "Artifact Plan", "Open Questions",
    ]
    for heading in required:
        sec = section_text(text, heading)
        if sec is None or not re.sub(r"<!--.*?-->", "", sec, flags=re.S).strip():
            reasons.append(f"missing or empty required section: {heading}")
    acceptance = ids_in_section(text, "Acceptance Criteria", "AC")
    if not acceptance:
        reasons.append("Acceptance Criteria must contain at least one stable AC-* id")
    test_intent = ids_in_section(text, "Model-generated Test Intent", "AC")
    missing = sorted(acceptance - test_intent)
    if missing:
        reasons.append("Model-generated Test Intent does not cover acceptance id(s): " + ", ".join(missing))
    artifact = section_text(text, "Artifact Plan") or ""
    for item in ("Requirement Spec", "Adaptive Prototype", "Implementation Spec"):
        if item.lower() not in artifact.lower():
            reasons.append(f"Artifact Plan must explicitly classify {item}")
    return reasons


def implementation_contract_reasons(text, spec_text, meta):
    reasons = []
    required = [
        "Goal / Non-goals Reference", "Repository Evidence", "Design Decision",
        "Modules and Ownership", "Dependency Direction", "Reuse Decisions",
        "Contract Impact", "Vertical Implementation Slices", "Canonical Quality Commands",
        "Escalation Triggers", "Open Questions",
    ]
    for heading in required:
        sec = section_text(text, heading)
        if sec is None or not re.sub(r"<!--.*?-->", "", sec, flags=re.S).strip():
            reasons.append(f"missing or empty required section: {heading}")
    acceptance = ids_in_section(spec_text, "Acceptance Criteria", "AC")
    slices = section_text(text, "Vertical Implementation Slices") or ""
    slice_ids = set(re.findall(r"\bAC-[A-Za-z0-9._-]+\b", slices))
    missing_slice = sorted(acceptance - slice_ids)
    if missing_slice:
        reasons.append("Vertical Implementation Slices do not trace acceptance id(s): " + ", ".join(missing_slice))
    low = slices.lower()
    if "seam" not in low:
        reasons.append("Vertical Implementation Slices must define a stable test/public seam")
    if "tdd" not in low:
        reasons.append("Vertical Implementation Slices must record the TDD yes/no decision")
    modules = [m for m in (meta or {}).get("modules") or [] if isinstance(m, dict) and not m.get("optional", False)]
    if modules and acceptance:
        unknown = sorted({str(ac) for m in modules for ac in (m.get("acceptance_ids") or []) if str(ac) not in acceptance})
        if unknown:
            reasons.append("module ownership references unknown acceptance id(s): " + ", ".join(unknown))
        without = [str(m.get("id")) for m in modules if not (m.get("acceptance_ids") or [])]
        if without:
            reasons.append("required functional module(s) have no Acceptance ID mapping: " + ", ".join(without))
        covered = {str(ac) for m in modules for ac in (m.get("acceptance_ids") or [])}
        uncovered = sorted(acceptance - covered)
        if uncovered:
            reasons.append("Acceptance Criteria are not owned by any required module: " + ", ".join(uncovered))
    return reasons


def feature_changelog_fingerprint(meta):
    if not meta or not meta.get("id"):
        return None
    p = feature_dir(meta["id"]) / "CHANGELOG.md"
    return content_hash(p)


def refresh_feature_documentation(meta, status=None, changed=None, summary=None):
    doc = dict(meta.get("documentation") or {})
    doc.setdefault("status", "current")
    doc.setdefault("last_code_change_at", None)
    doc.setdefault("last_synced_at", None)
    doc.setdefault("last_sync_summary", None)
    doc.setdefault("pending_changed_paths", [])
    doc.setdefault("changelog_fingerprint", feature_changelog_fingerprint(meta))
    if status is not None:
        doc["status"] = status
    if changed is not None:
        doc["pending_changed_paths"] = sorted(dict.fromkeys(changed))
    if summary is not None:
        doc["last_sync_summary"] = summary
    meta["documentation"] = doc
    return doc


def requirements_current(meta):
    path = feature_dir(meta["id"]) / "SPEC.md"
    return bool(meta.get("requirements_approved") and meta.get("requirements_fingerprint") and content_hash(path) == meta.get("requirements_fingerprint"))


def architecture_ready():
    if not ARCH.exists():
        return False
    t = ARCH.read_text(encoding="utf-8", errors="ignore")
    if "UNINITIALIZED" in t or "[GOV-TODO]" in t or not required_architecture_headings_present(t):
        return False
    project = load_project()
    meta = project.get("project") or {}
    return str(meta.get("architecture_level") or "").upper() in {"L1","L2","L3","L4"} and str(meta.get("application_shape") or "").strip().lower() not in {"","unknown","uninitialized","todo"}

def quality_config_fingerprint():
    project = load_project()
    payload = json.dumps(project.get("quality") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def feature_validation_fingerprint(meta):
    payload = json.dumps(meta.get("validation_commands") or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def module_plan_fingerprint(meta):
    modules=[]
    for m in (meta or {}).get("modules") or []:
        if not isinstance(m,dict): continue
        modules.append({
            "id":m.get("id"), "title":m.get("title"),
            "paths":sorted(str(x).replace("\\","/") for x in (m.get("paths") or [])),
            "acceptance_ids":sorted(str(x) for x in (m.get("acceptance_ids") or [])),
            "optional":bool(m.get("optional",False)),
        })
    payload=json.dumps(sorted(modules,key=lambda x:str(x.get("id"))),ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def required_architecture_headings_present(text):
    required=["Stack / Application Shape","Complexity Level","Module Topology","Dependency Rules","Test Architecture","Canonical Quality Commands"]
    return all(section_text(text,h) is not None for h in required)


def register_architecture(level, application_shape, runtime_mode=None):
    project=load_project(); project.setdefault("project",{})["architecture_level"]=level; project["project"]["application_shape"]=application_shape.strip()
    if runtime_mode: project["project"]["runtime_mode"]=runtime_mode
    save_project(project); return project


def contract_modules_ready(meta):
    modules=[m for m in (meta or {}).get("modules") or [] if isinstance(m,dict)]
    if not modules: return False,"no product/implementation modules are registered"
    seen=set(); dup=set(); missing=[]
    for m in modules:
        mid=str(m.get("id") or "").strip()
        if not mid: missing.append("<missing-id>"); continue
        if mid in seen: dup.add(mid)
        seen.add(mid)
        if not m.get("optional",False) and not (m.get("paths") or []): missing.append(mid)
    if dup: return False,"duplicate module ids: "+", ".join(sorted(dup))
    if missing: return False,"non-optional modules without owned path patterns: "+", ".join(missing)
    return True,""


def refresh_module_progress(meta, paths, to_status="in_progress"):
    matches=matching_modules(meta,paths)
    if not matches: return False,matches
    changed=False; modules=[]
    for module in meta.get("modules") or []:
        if not isinstance(module,dict): continue
        m=dict(module); module_changed=False
        if str(m.get("id")) in matches and not m.get("optional",False):
            current=m.get("status","not_started")
            if to_status=="in_progress" and current in {"not_started","verified","done"}: m["status"]="in_progress"; module_changed=True
            elif to_status=="verified" and current=="in_progress": m["status"]="verified"; module_changed=True
            elif to_status=="done" and current=="verified": m["status"]="done"; module_changed=True
            if module_changed: m["updated_at"]=now(); changed=True
        modules.append(m)
    if changed: meta["modules"]=modules
    return changed,matches


def has_configured_quality(meta=None):
    project = load_project()
    commands = ((project.get("quality") or {}).get("commands") or {})
    if any(commands.get(phase) for phase in QUALITY_PHASES):
        return True
    return bool(meta and (meta.get("validation_commands") or []))


def implementation_current(meta):
    path=feature_dir(meta["id"])/"IMPLEMENTATION.md"
    return bool(
        meta.get("implementation_approved") and meta.get("ready_for_luna") and meta.get("implementation_fingerprint")
        and content_hash(path)==meta.get("implementation_fingerprint")
        and meta.get("architecture_fingerprint") and content_hash(ARCH)==meta.get("architecture_fingerprint")
        and meta.get("quality_config_fingerprint")==quality_config_fingerprint()
        and meta.get("feature_validation_fingerprint")==feature_validation_fingerprint(meta)
        and meta.get("module_plan_fingerprint")==module_plan_fingerprint(meta)
        and requirements_current(meta)
    )

def artifact_fingerprint(path_value):
    if not path_value:
        return None
    p = (ROOT / path_value).resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError:
        raise SystemExit(f"Prototype path escapes repository: {path_value}")
    if not p.exists():
        return None
    h = hashlib.sha256()
    if p.is_file():
        h.update(p.relative_to(ROOT).as_posix().encode("utf-8")); h.update(b"\0")
        h.update(hash_file(p).encode("ascii"))
        return h.hexdigest()
    for child in sorted(x for x in p.rglob("*") if x.is_file()):
        rel = child.relative_to(ROOT).as_posix()
        if "/.git/" in "/" + rel or rel.startswith(".codex/state/"):
            continue
        h.update(rel.encode("utf-8")); h.update(b"\0"); h.update(hash_file(child).encode("ascii")); h.update(b"\0")
    return h.hexdigest()


def prototype_gate_ready(meta):
    """Return whether the optional prototype decision is resolved for the current requirements."""
    if not meta or not requirements_current(meta):
        return False
    proto = meta.get("prototype") or {}
    if proto.get("based_on_requirements_fingerprint") != meta.get("requirements_fingerprint"):
        return False
    decision = proto.get("decision")
    status = proto.get("status")
    if decision == "generate":
        if status == "superseded":
            return True
        if status != "approved" or not proto.get("path") or not proto.get("artifact_fingerprint"):
            return False
        return artifact_fingerprint(proto.get("path")) == proto.get("artifact_fingerprint")
    if decision in {"skip", "not-needed"}:
        return status == "skipped"
    return False

def requires_contract(kind, risk):
    return risk in {"standard", "high"} and kind in {"greenfield", "feature", "engineering-change", "maintenance"}


def review_required_for(kind, risk, project):
    if risk == "trivial":
        return False
    if risk == "high":
        # High-risk review is a governance invariant, not a project-level preference.
        return True
    return bool((project.get("review") or {}).get("standard_independent_review", True))


def review_waiver_reasons(state, events=None, current=None):
    """Validate the explicit, current-work review waiver, if any."""
    waiver = state.get("review_waiver") or {}
    if not waiver:
        return ["required independent review is missing or stale for the current diff"]
    current = current or git_fingerprint(ROOT, scope="review")
    reasons = []
    if waiver.get("work_id") != state.get("work_id"):
        reasons.append("review waiver does not match the active work id")
    if waiver.get("work_branch") != state.get("work_branch"):
        reasons.append("review waiver does not match the active work branch")
    if waiver.get("review_fingerprint") != current:
        reasons.append("review waiver is stale for the current diff")
    if waiver.get("confirmed_by_user") is not True:
        reasons.append("review waiver lacks explicit user confirmation")
    if not str(waiver.get("reason") or "").strip():
        reasons.append("review waiver reason is missing")
    events = _load_gate_events() if events is None else events
    matching = [event for event in events if event.get("event_type") == "review_waiver"
                and event.get("work_id") == state.get("work_id")
                and event.get("work_branch") == state.get("work_branch")
                and event.get("review_fingerprint") == waiver.get("review_fingerprint")
                and event.get("reason") == waiver.get("reason")
                and event.get("recorded_at") == waiver.get("recorded_at")]
    if not matching:
        reasons.append("review waiver has no matching committed gate event")
    elif any(_seal_gate_event(event).get("event_digest") != event.get("event_digest") for event in matching):
        reasons.append("matching review waiver gate event digest does not match")
    elif any(event.get("confirmed_by_user") is not True for event in matching):
        reasons.append("matching review waiver gate event lacks explicit user confirmation")
    return reasons


def approval_reasons(state, meta=None):
    reasons = []
    if requires_contract(state.get("kind"), state.get("risk")):
        if not meta:
            reasons.append("a feature/engineering metadata record is required")
        else:
            if not requirements_current(meta):
                reasons.append("requirements are not approved or SPEC.md changed after approval")
            if state.get("kind") in {"greenfield", "feature"} and not prototype_gate_ready(meta):
                reasons.append("prototype decision is unresolved; explicitly generate+approve it, skip it, or mark it not-needed")
            if not architecture_ready():
                reasons.append("docs/architecture/PROFILE.md is not initialized")
            if not implementation_current(meta):
                reasons.append("implementation contract is not approved/current for the current SPEC and architecture")
    return reasons


def module_completion_reasons(state, meta):
    if not meta or state.get("risk") not in {"standard","high"} or state.get("kind") not in {"greenfield","feature"}: return []
    modules=[m for m in meta.get("modules") or [] if isinstance(m,dict) and not m.get("optional",False)]
    if not modules: return ["no required feature modules are registered"]
    incomplete=[str(m.get("id")) for m in modules if m.get("status") not in {"verified","done"}]
    return ["required feature modules are not verified/done: "+", ".join(incomplete)] if incomplete else []


def ownership_reasons(state, meta):
    if not meta or state.get("risk") not in {"standard","high"} or state.get("kind") not in {"greenfield","feature","bug"}: return []
    paths=changed_paths(ROOT,scope="implementation")
    if not paths: return []
    enforce=bool(meta.get("module_plan_fingerprint")) or state.get("kind") in {"greenfield","feature"}
    if not enforce: return []
    missing=unmapped_paths(ROOT,paths)
    if missing:
        preview=", ".join(missing[:8])+(" ..." if len(missing)>8 else "")
        return ["implementation paths are not mapped to any feature module ownership pattern: "+preview+"; update the approved module plan instead of silently orphaning code"]
    return []


def affected_feature_approval_reasons(state, primary_fid):
    """Check approval freshness for every current Feature touched by this work cycle.

    ``feature_id`` is the task's primary feature, while ``affected_feature_ids`` also
    contains current Features whose owned paths were changed through shared modules.
    Their requirements and implementation approvals are independent gates. Retired
    records remain historical and must not block a later cycle.
    """
    ids = list(state.get("affected_feature_ids") or [])
    if primary_fid and primary_fid not in ids:
        ids.insert(0, primary_fid)
    reasons = []
    for fid in ids:
        if not fid or fid == primary_fid:
            continue
        meta = load_feature(fid, required=False)
        if not meta:
            reasons.append(f"affected feature {fid}: feature metadata is missing")
            continue
        if meta.get("retired") or meta.get("status") in {"archived", "superseded"}:
            continue
        if not requires_contract(meta.get("kind"), meta.get("risk")):
            continue
        feature_state = dict(state)
        feature_state.update({"feature_id": fid, "kind": meta.get("kind"), "risk": meta.get("risk")})
        for reason in approval_reasons(feature_state, meta):
            reasons.append(f"affected feature {fid}: {reason}")
    return reasons


def gate_status(phase_override=None):
    project=load_project(); state=active_state(required=False)
    if not state: return {"ready":True,"reasons":[]}
    reasons=[]; fid=state.get("feature_id"); meta=load_feature(fid,required=False) if fid else None; phase=phase_override or state.get("phase")
    if phase in {"implementation","review","release"}:
        expected=state.get("work_branch"); actual=current_branch()
        if expected and actual!=expected: reasons.append(f"active work belongs to branch {expected!r}, but the checkout is currently on {actual!r}; switch back or finish/abandon the task explicitly")
        reasons.extend(approval_reasons(state, meta))
        try:
            reasons.extend(approval_event_reasons(state, meta))
        except SystemExit as exc:
            reasons.append(f"governance gate event log is invalid: {exc}")
        reasons.extend(affected_feature_approval_reasons(state, fid))
        reasons.extend(ownership_reasons(state, meta))
        if phase in {"review","release"}: reasons.extend(module_completion_reasons(state,meta))
        if state.get("concurrency_conflict"): reasons.append("a cross-session write conflict is unresolved; inspect the diff and run gov resolve-concurrency")
        current_impl=git_fingerprint(ROOT,scope="implementation")
        if state.get("docs_required",True) and (state.get("docs_dirty") or state.get("docs_sync_fingerprint")!=current_impl): reasons.append("implementation changes are not synchronized to the relevant current-state docs/changelog; update them and run gov sync-docs")
        if state.get("quality_required",True):
            q=read_json(quality_path(ROOT),{}) or {}; current=git_fingerprint(ROOT,scope="quality")
            if q.get("status")!="passed" or q.get("fingerprint")!=current: reasons.append("quality gate is missing, failed, or stale for the current implementation diff")
        if state.get("review_required"):
            current=git_fingerprint(ROOT,scope="review")
            if state.get("review_status")=="waived":
                reasons.extend(review_waiver_reasons(state, current=current))
            elif state.get("review_status")!="passed" or state.get("review_fingerprint")!=current:
                reasons.append("required independent review is missing or stale for the current diff")
    return {"ready":not reasons,"reasons":reasons,"work_id":state.get("work_id")}

def effective_feature_status(meta):
    raw=meta.get("status","unknown")
    if meta.get("retired") or raw in {"archived","superseded"}: return meta.get("retired_status") or raw
    if raw in {"approved", "done"} and not (meta.get("requirements_approved") and meta.get("implementation_approved")):
        return "stale_approval"
    if meta.get("requirements_approved") and not requirements_current(meta): return "stale_requirements"
    if meta.get("implementation_approved") and not implementation_current(meta): return "stale_implementation"
    if raw in {"done","blocked"}: return raw
    return raw

def render_status():
    base=DOCS/"features"; active_rows=[]; retired_rows=[]; module_sections=[]; active=active_state(required=False)
    current_impl=git_fingerprint(ROOT,scope="implementation") if active and active.get("phase") in {"implementation","review","release"} else None
    if base.exists():
        for meta_path in sorted(base.glob("*/feature.json")):
            m=migrate_feature_meta(read_json(meta_path,{}) or {},meta_path.parent.name)
            modules=[x for x in (m.get("modules") or []) if isinstance(x,dict)]
            done=sum(1 for x in modules if x.get("status")=="done" or x.get("optional",False)); progress=f"{done}/{len(modules)}" if modules else "-"
            doc=refresh_feature_documentation(m); docs_state=doc.get("status","current")
            if active and m.get("id") in set(active.get("affected_feature_ids") or [active.get("feature_id")]) and current_impl is not None:
                if active.get("docs_dirty") or active.get("docs_sync_fingerprint")!=current_impl: docs_state="stale"
            row=(m.get("id",meta_path.parent.name),m.get("title",""),m.get("kind","feature"),effective_feature_status(m),m.get("risk",""),progress,docs_state,m.get("updated_at",""))
            if m.get("retired") or m.get("status") in {"archived","superseded"}: retired_rows.append((*row,", ".join(m.get("superseded_by") or []),m.get("retire_reason") or ""))
            else:
                active_rows.append(row)
                if modules:
                    lines=[f"### {m.get('id')} — {m.get('title','')}","","| Module | Status | Acceptance | Last change | Owned paths |","|---|---|---|---|---|"]
                    for module in modules:
                        lines.append("| "+" | ".join([str(module.get("id","")).replace("|","\\|"),str(module.get("status","not_started")).replace("|","\\|"),", ".join(module.get("acceptance_ids") or []) or "-",str(module.get("last_change_summary") or "-").replace("|","\\|"),"<br>".join(f"`{x}`" for x in (module.get("paths") or [])) or "-"])+" |")
                    module_sections.append("\n".join(lines))
    lines=["# Project Status","","> GENERATED FILE — do not edit manually. Source: `docs/features/*/feature.json` plus current approval/documentation/module state.","","## Active Features","","| Feature | Title | Kind | Status | Risk | Module progress | Docs | Updated |","|---|---|---|---|---|---|---|---|"]
    if active_rows:
        lines += ["| "+" | ".join(str(x).replace("|","\\|") for x in r)+" |" for r in active_rows]
    else: lines.append("| - | No active feature metadata yet | - | - | - | - | - | - |")
    if retired_rows:
        lines += ["","## Retired / Superseded Features","","| Feature | Title | Kind | Status | Risk | Module progress | Docs | Updated | Replaced by | Reason |","|---|---|---|---|---|---|---|---|---|---|"]
        lines += ["| "+" | ".join(str(x).replace("|","\\|") for x in r)+" |" for r in retired_rows]
    lines += ["","## Functional Module Progress",""]
    lines.append("\n\n".join(module_sections) if module_sections else "No functional modules are registered yet. Standard/High feature implementation approval requires module ownership mapping.")
    lines += ["","## Status","","- `draft`: requirements/planning not approved","- `requirements_approved`: current requirements approved; implementation plan not yet approved","- `approved`: implementation contract approved","- `in_progress`: implementation active","- `stale_requirements`: SPEC changed after its recorded approval","- `stale_implementation`: implementation/architecture/quality/module contract changed after approval","- `blocked`: material issue pending","- `done`: completed and validated","- `superseded`: retained history; replaced by newer feature records","- `archived`: retained history; no longer current product scope","","## Module lifecycle","","- `not_started`: planned in the approved module map","- `in_progress`: matching owned implementation path changed","- `verified`: current Quality Gate passed after the module change","- `done`: task completed with current docs/quality/review gates","- `blocked`: implementation cannot currently proceed","","## Documentation","","- `current`: semantic docs/changelog are synchronized with the recorded implementation diff","- `stale`: code/config/test changes were detected after the last documentation synchronization"]
    DOCS.mkdir(parents=True,exist_ok=True); (DOCS/"PROJECT_STATUS.md").write_text("\n".join(lines)+"\n",encoding="utf-8"); return DOCS/"PROJECT_STATUS.md"

def cmd_repo_facts(args):
    facts = {
        "generated_at": now(), "root": str(ROOT), "languages": [], "frameworks": [],
        "package_managers": [], "signals": [], "packages": [], "candidate_quality_commands": {},
        "note": "Candidates are heuristic only. Verify before copying them into project.json."
    }
    def add(key, value):
        if value not in facts[key]: facts[key].append(value)
    def txt(p):
        try: return p.read_text(encoding="utf-8", errors="ignore")
        except OSError: return ""
    excluded = {".git", "node_modules", ".venv", "venv", "dist", "build", "target", ".next", ".idea", ".vs", "coverage", ".cache"}
    manifests = []
    deploy_signals = []
    wanted = {"package.json", "pyproject.toml", "requirements.txt", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "Cargo.toml"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        d = Path(dirpath)
        try: rel_dir_parts = d.relative_to(ROOT).parts
        except ValueError: rel_dir_parts = ()
        depth = len(rel_dir_parts)
        dirnames[:] = [name for name in dirnames if name not in excluded and depth < 4]
        if depth > 4: continue
        for name in filenames:
            p = d / name
            if name in wanted or p.suffix == ".csproj": manifests.append(p)
            if name in {"Dockerfile", "docker-compose.yml", "compose.yaml"}: deploy_signals.append(p)
    seen_pkg_dirs = set()
    for p in sorted(manifests):
        rel_dir = p.parent.relative_to(ROOT).as_posix() or "."
        if rel_dir not in seen_pkg_dirs:
            facts["packages"].append({"path": rel_dir, "manifests": []}); seen_pkg_dirs.add(rel_dir)
        next(x for x in facts["packages"] if x["path"] == rel_dir)["manifests"].append(p.name)
        t = txt(p)
        if p.name == "package.json":
            add("languages", "JavaScript/TypeScript"); add("signals", str(p.relative_to(ROOT)))
            try: data = json.loads(t)
            except Exception: data = {}
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            for k, name in {"next":"Next.js", "@nestjs/core":"NestJS", "express":"Express", "fastify":"Fastify", "react":"React", "vue":"Vue", "svelte":"Svelte"}.items():
                if k in deps: add("frameworks", name)
            pd = p.parent
            if (pd / "pnpm-lock.yaml").exists() or (ROOT / "pnpm-lock.yaml").exists(): pm="pnpm"; add("package_managers","pnpm")
            elif (pd / "yarn.lock").exists() or (ROOT / "yarn.lock").exists(): pm="yarn"; add("package_managers","Yarn")
            elif (pd / "bun.lock").exists() or (pd / "bun.lockb").exists() or (ROOT / "bun.lock").exists(): pm="bun"; add("package_managers","Bun")
            else: pm="npm"; add("package_managers","npm")
            scripts=data.get("scripts") or {}
            mapping={"format":["format:check","fmt:check"],"lint":["lint"],"typecheck":["typecheck","type-check"],"build":["build"],"unit":["test:unit","test"],"integration":["test:integration","test:integration:ci"],"e2e":["test:e2e","e2e"]}
            for phase,names in mapping.items():
                for n in names:
                    if n in scripts:
                        facts["candidate_quality_commands"].setdefault(phase, []).append({"command":f"{pm} run {n}","cwd":rel_dir}); break
        elif p.name in {"pyproject.toml", "requirements.txt"}:
            add("languages","Python"); add("signals",str(p.relative_to(ROOT))); low=t.lower()
            for needle,name in [("fastapi","FastAPI"),("django","Django"),("flask","Flask")]:
                if needle in low: add("frameworks",name)
            if p.name=="pyproject.toml":
                if (p.parent/"uv.lock").exists() or "[tool.uv" in low: add("package_managers","uv")
                elif "[tool.poetry" in low: add("package_managers","Poetry")
            if "ruff" in low:
                facts["candidate_quality_commands"].setdefault("format",[]).append({"command":"ruff format --check .","cwd":rel_dir})
                facts["candidate_quality_commands"].setdefault("lint",[]).append({"command":"ruff check .","cwd":rel_dir})
            if "mypy" in low: facts["candidate_quality_commands"].setdefault("typecheck",[]).append({"command":"mypy .","cwd":rel_dir})
            if "pytest" in low or (p.parent/"pytest.ini").exists(): facts["candidate_quality_commands"].setdefault("unit",[]).append({"command":"pytest","cwd":rel_dir})
        elif p.name == "pom.xml":
            add("languages","Java/JVM"); add("package_managers","Maven"); add("signals",str(p.relative_to(ROOT)))
            if "spring-boot" in t or "org.springframework.boot" in t: add("frameworks","Spring Boot")
            mvn = ".\\mvnw.cmd" if os.name=="nt" and (p.parent/"mvnw.cmd").exists() else ("./mvnw" if (p.parent/"mvnw").exists() else "mvn")
            facts["candidate_quality_commands"].setdefault("unit",[]).append({"command":f"{mvn} test","cwd":rel_dir})
            facts["candidate_quality_commands"].setdefault("build",[]).append({"command":f"{mvn} verify","cwd":rel_dir})
        elif p.name in {"build.gradle", "build.gradle.kts"}:
            add("languages","Java/Kotlin/JVM"); add("package_managers","Gradle"); add("signals",str(p.relative_to(ROOT)))
            if "org.springframework.boot" in t: add("frameworks","Spring Boot")
            grad = ".\\gradlew.bat" if os.name=="nt" and (p.parent/"gradlew.bat").exists() else ("./gradlew" if (p.parent/"gradlew").exists() else "gradle")
            facts["candidate_quality_commands"].setdefault("unit",[]).append({"command":f"{grad} test","cwd":rel_dir})
            facts["candidate_quality_commands"].setdefault("build",[]).append({"command":f"{grad} check","cwd":rel_dir})
        elif p.name == "go.mod":
            add("languages","Go"); add("package_managers","Go modules"); add("signals",str(p.relative_to(ROOT)))
            for needle,name in [("github.com/gin-gonic/gin","Gin"),("github.com/go-chi/chi","Chi"),("github.com/labstack/echo","Echo"),("github.com/gofiber/fiber","Fiber")]:
                if needle in t: add("frameworks",name)
            facts["candidate_quality_commands"].setdefault("lint",[]).append({"command":"go vet ./...","cwd":rel_dir})
            facts["candidate_quality_commands"].setdefault("unit",[]).append({"command":"go test ./...","cwd":rel_dir})
        elif p.name == "Cargo.toml":
            add("languages","Rust"); add("package_managers","Cargo"); add("signals",str(p.relative_to(ROOT)))
            for needle,name in [("axum","Axum"),("actix-web","Actix Web"),("rocket","Rocket")]:
                if needle in t: add("frameworks",name)
            for phase,command in [("format","cargo fmt --check"),("lint","cargo clippy --all-targets --all-features -- -D warnings"),("build","cargo build"),("unit","cargo test")]:
                facts["candidate_quality_commands"].setdefault(phase,[]).append({"command":command,"cwd":rel_dir})
        elif p.suffix == ".csproj":
            add("languages","C#/.NET"); add("package_managers","NuGet"); add("signals",str(p.relative_to(ROOT)))
            if "Microsoft.NET.Sdk.Web" in t: add("frameworks","ASP.NET Core")
    for p in deploy_signals:
        add("signals", str(p.relative_to(ROOT)))
    out = GOV / "repo-facts.json"
    write_json(out, facts)
    project = load_project()
    if (project.get("project") or {}).get("name") in {None, "", "uninitialized", "TODO"}:
        project.setdefault("project", {})["name"] = ROOT.name
        save_project(project)
    print(json.dumps(facts, ensure_ascii=False, indent=2))


def cmd_new_feature(args):
    fid=args.id or f"F-{datetime.datetime.now():%Y%m%d}-{slugify(args.title)}"
    existing=load_feature(fid,required=False) if feature_json(fid).exists() else None
    if existing:
        if existing.get("retired") or existing.get("status") in {"archived","superseded"}: raise SystemExit(f"Feature id {fid} is retired and immutable. Create a new stable feature id instead of reusing historical identity.")
        mismatches=[]
        if existing.get("title")!=args.title: mismatches.append("title")
        if existing.get("kind")!=args.kind: mismatches.append("kind")
        if existing.get("risk")!=args.risk: mismatches.append("risk")
        if mismatches: raise SystemExit(f"Feature id {fid} already exists with different {', '.join(mismatches)}. Use the existing record or choose a new id.")
    d=feature_dir(fid); d.mkdir(parents=True,exist_ok=True); vals={"ID":fid,"TITLE":args.title,"RISK":args.risk,"KIND":args.kind}
    for name in ("SPEC.md","IMPLEMENTATION.md","CHANGELOG.md"):
        dest=d/name
        if not dest.exists(): dest.write_text(fill_template(TEMPLATES/"feature"/name,**vals),encoding="utf-8")
    mp=d/"feature.json"
    if not mp.exists():
        meta=json.loads(fill_template(TEMPLATES/"feature"/"feature.json",**vals)); meta["created_at"]=now(); meta["updated_at"]=now()
        if args.risk=="trivial": meta["review_required"]=False
        doc=dict(meta.get("documentation") or {}); doc["changelog_fingerprint"]=content_hash(d/"CHANGELOG.md"); meta["documentation"]=doc; write_json(mp,meta)
    else: save_feature(fid,load_feature(fid),render=False)
    render_status(); print(fid)

def cmd_approve_requirements(args):
    if not args.confirmed_by_user:
        raise SystemExit("Requirements approval is a human gate. Re-run only after explicit user approval with --confirmed-by-user.")
    fid = args.feature_id or active_state().get("feature_id")
    if not fid: raise SystemExit("Feature id required.")
    meta = load_feature(fid); p = feature_dir(fid) / "SPEC.md"
    text = p.read_text(encoding="utf-8")
    ensure_no_governance_placeholders(text, "SPEC.md")
    req_reasons = requirements_contract_reasons(text)
    if req_reasons:
        raise SystemExit("SPEC.md is not approval-ready: " + "; ".join(req_reasons))
    if not open_questions_resolved(text): raise SystemExit("SPEC.md Open Questions must be None before approval.")
    replacement = """## 14. Approval\n- Material questions resolved: YES\n- Acceptance/Test Intent approved: YES\n- Requirements approved: YES"""
    text = replace_marker_block(text, "<!-- gov:requirements-approval:begin -->", "<!-- gov:requirements-approval:end -->", replacement)
    p.write_text(text, encoding="utf-8")
    prior_impl = {
        "implementation_approved": bool(meta.get("implementation_approved")),
        "implementation_approved_at": meta.get("implementation_approved_at"),
        "implementation_fingerprint": meta.get("implementation_fingerprint"),
        "architecture_fingerprint": meta.get("architecture_fingerprint"),
    }
    meta.setdefault("implementation_approval_lineage", [])
    if prior_impl["implementation_approved"] or prior_impl["implementation_approved_at"]:
        meta["implementation_approval_lineage"].append({
            "event_type": "implementation_approval_invalidated_by_requirements_reapproval",
            "at": now(),
            "work_id": (active_state(required=False) or {}).get("work_id"),
            "feature_id": fid,
            **prior_impl,
        })
    meta["requirements_approved"] = True; meta["requirements_fingerprint"] = content_hash(p); meta["requirements_approved_at"] = now()
    # Any new requirements approval invalidates the downstream implementation/review chain.
    meta["implementation_approved"] = False; meta["ready_for_luna"] = False; meta["implementation_fingerprint"] = None; meta["architecture_fingerprint"] = None; meta["quality_config_fingerprint"] = None; meta["feature_validation_fingerprint"] = None; meta["module_plan_fingerprint"] = None
    meta["review_status"] = "pending"; meta["review_fingerprint"] = None
    meta["status"] = "requirements_approved"
    save_feature(fid, meta); print(meta["requirements_fingerprint"])


def cmd_set_prototype(args):
    fid = args.feature_id or active_state().get("feature_id")
    if not fid: raise SystemExit("Feature id required.")
    meta = load_feature(fid)
    if not requirements_current(meta):
        raise SystemExit("Resolve and approve current requirements before recording a prototype decision.")
    if args.decision in {"generate", "skip"} and not args.confirmed_by_user:
        raise SystemExit("Prototype generate/skip is a human decision. Re-run after explicit user approval with --confirmed-by-user.")
    allowed = {
        "generate": {"not_started", "in_progress", "approved", "superseded"},
        "skip": {"skipped"},
        "not-needed": {"skipped"},
    }
    if args.status not in allowed[args.decision]:
        raise SystemExit(f"Invalid prototype state: decision={args.decision} cannot use status={args.status}.")
    if args.status == "approved" and not args.confirmed_by_user:
        raise SystemExit("Prototype approval is a human gate. Re-run after explicit user approval with --confirmed-by-user.")
    prior = meta.get("prototype") or {}
    path_value = args.path if args.path is not None else prior.get("path")
    artifact_fp = prior.get("artifact_fingerprint")
    if args.decision == "generate":
        if path_value:
            proto_root = (feature_dir(fid) / "prototype").resolve()
            candidate = (ROOT / path_value).resolve()
            try:
                candidate.relative_to(proto_root)
            except ValueError:
                raise SystemExit(
                    f"Generated prototype artifacts must live under docs/features/{fid}/prototype/ so pre-implementation prototype writes remain separate from production source."
                )
    if args.decision == "generate" and args.status == "approved":
        if not path_value:
            raise SystemExit("An approved generated prototype must have --path so its reviewed artifact can be fingerprinted.")
        artifact_fp = artifact_fingerprint(path_value)
        if not artifact_fp:
            raise SystemExit(f"Prototype artifact does not exist: {path_value}")
    elif args.decision != "generate":
        path_value = None; artifact_fp = None
    meta["prototype"] = {
        "decision": args.decision, "status": args.status, "path": path_value,
        "based_on_requirements_fingerprint": meta.get("requirements_fingerprint"),
        "artifact_fingerprint": artifact_fp,
    }
    # A pre-production prototype decision change invalidates an already-approved implementation plan.
    if meta.get("implementation_approved") and args.status != "superseded":
        meta["implementation_approved"] = False
        meta["ready_for_luna"] = False
        meta["implementation_fingerprint"] = None
        meta["architecture_fingerprint"] = None
        meta["quality_config_fingerprint"] = None
        meta["feature_validation_fingerprint"] = None; meta["module_plan_fingerprint"] = None
    save_feature(fid, meta)
    print(json.dumps(meta["prototype"], ensure_ascii=False))

def _gate_events_path():
    return STATE / "gate-events.jsonl"


def _seal_gate_event(event):
    payload = {key: value for key, value in event.items() if key not in {"event_id", "event_digest"}}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    sealed = dict(payload)
    sealed["event_digest"] = digest
    sealed["event_id"] = "gate-" + digest[:20]
    return sealed


def _write_gate_events(events):
    path = _gate_events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in events)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _load_gate_events():
    path = _gate_events_path()
    if not path.exists():
        return []
    events = []
    needs_seal = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            raise SystemExit(f"Malformed governance gate event log: {path}")
        if isinstance(item, dict):
            sealed = _seal_gate_event(item) if not item.get("event_id") or not item.get("event_digest") else item
            needs_seal = needs_seal or sealed != item
            events.append(sealed)
        else:
            raise SystemExit(f"Malformed governance gate event log: {path}")
    if needs_seal:
        _write_gate_events(events)
    return events


def _append_gate_event(event):
    events = _load_gate_events()
    events.append(_seal_gate_event(event))
    _write_gate_events(events)


def approval_event_reasons(state, meta, events=None):
    if not meta or not meta.get("implementation_approved"):
        return []
    events = _load_gate_events() if events is None else events
    fid = str(meta.get("id") or state.get("feature_id") or "")
    candidates = [event for event in events if event.get("event_type") in {"implementation_approval", "continuation_implementation_reapproval"}]
    matching = []
    for event in candidates:
        expected = {
            "work_id": state.get("work_id"),
            "feature_id": fid,
            "work_branch": state.get("work_branch"),
            "original_source_baseline": state.get("preimplementation_source_fingerprint"),
            "requirements_fingerprint": meta.get("requirements_fingerprint"),
            "implementation_fingerprint": meta.get("implementation_fingerprint"),
            "architecture_fingerprint": meta.get("architecture_fingerprint"),
        }
        if all(event.get(key) == value for key, value in expected.items()):
            matching.append(event)
    if not matching:
        return ["implementation approval has no matching committed gate event"]
    reasons = []
    for event in matching:
        if not event.get("event_id") or not event.get("event_digest"):
            reasons.append("matching implementation approval gate event is missing id/digest")
            continue
        if _seal_gate_event(event).get("event_digest") != event.get("event_digest"):
            reasons.append("matching implementation approval gate event digest does not match")
    return reasons


def continuation_reapproval_reasons(state, meta, branch, events, reason=""):
    reasons = []
    if not str(reason or "").strip():
        reasons.append("continuation reapproval reason is required")
    if not state:
        reasons.append("active work is required")
        return reasons
    fid = str(meta.get("id") or "")
    if state.get("feature_id") != fid:
        reasons.append("active work feature does not match the requested feature")
    if not state.get("work_id"):
        reasons.append("active work id is missing")
    if not state.get("work_branch") or branch != state.get("work_branch"):
        reasons.append("current branch does not match the active work branch")
    if not state.get("preimplementation_source_fingerprint"):
        reasons.append("original source baseline is missing")
    if not meta.get("requirements_approved"):
        reasons.append("current requirements approval is missing")
    if meta.get("implementation_approved"):
        reasons.append("implementation approval is already current")
    impl_at = meta.get("implementation_approved_at")
    req_at = meta.get("requirements_approved_at")
    started_at = state.get("started_at")
    prior_continuations = [
        event for event in events or []
        if event.get("event_type") == "continuation_implementation_reapproval"
        and event.get("work_id") == state.get("work_id")
        and event.get("feature_id") == fid
        and event.get("work_branch") == state.get("work_branch")
    ]
    if any(event.get("requirements_fingerprint") == meta.get("requirements_fingerprint")
           for event in prior_continuations):
        reasons.append("continuation reapproval was already used for this work/feature/branch/requirements contract")
    # A requirements reapproval deliberately invalidates the old downstream approval.
    # Permit one new continuation event for the new SPEC contract while preserving the
    # original source baseline; the event itself binds the new contract fingerprints.
    if not prior_continuations:
        try:
            started = datetime.datetime.fromisoformat(str(started_at))
            implementation = datetime.datetime.fromisoformat(str(impl_at))
            requirements = datetime.datetime.fromisoformat(str(req_at))
            if not (started <= implementation < requirements):
                reasons.append("legacy approval timestamps are not ordered as started_at <= implementation_approved_at < requirements_approved_at")
        except (TypeError, ValueError):
            reasons.append("legacy approval timestamps are missing or invalid")
    return reasons


def _implementation_contract_approval(meta, fid, active, mode, reason=None):
    ip = feature_dir(fid) / "IMPLEMENTATION.md"
    prior_meta = json.loads(json.dumps(meta, ensure_ascii=False))
    text = ip.read_text(encoding="utf-8")
    prior_text = text
    ensure_no_governance_placeholders(text, "IMPLEMENTATION.md")
    spec_text = (feature_dir(fid) / "SPEC.md").read_text(encoding="utf-8")
    impl_reasons = implementation_contract_reasons(text, spec_text, meta)
    if impl_reasons:
        raise SystemExit("IMPLEMENTATION.md is not approval-ready: " + "; ".join(impl_reasons))
    if not open_questions_resolved(text):
        raise SystemExit("IMPLEMENTATION.md Open Questions must be None before approval.")
    replacement = """## 13. Approval
- Requirements approved and current: YES
- Architecture ready: YES
- Implementation plan approved: YES
- Ready for Luna: YES"""
    text = replace_marker_block(text, "<!-- gov:implementation-approval:begin -->", "<!-- gov:implementation-approval:end -->", replacement)
    ip.write_text(text, encoding="utf-8")
    prior_impl_at = meta.get("implementation_approved_at")
    meta["implementation_approved"] = True
    meta["ready_for_luna"] = True
    meta["implementation_fingerprint"] = content_hash(ip)
    meta["architecture_fingerprint"] = content_hash(ARCH)
    meta["quality_config_fingerprint"] = quality_config_fingerprint()
    meta["feature_validation_fingerprint"] = feature_validation_fingerprint(meta)
    meta["module_plan_fingerprint"] = module_plan_fingerprint(meta)
    meta["implementation_approved_at"] = now()
    meta["status"] = "approved"
    event = {
        "event_type": "continuation_implementation_reapproval" if mode == "continuation" else "implementation_approval",
        "approval_mode": mode,
        "at": now(),
        "work_id": active.get("work_id") if active else None,
        "feature_id": fid,
        "work_branch": active.get("work_branch") if active else current_branch(),
        "original_source_baseline": active.get("preimplementation_source_fingerprint") if active else None,
        "current_source_fingerprint": git_fingerprint(ROOT, scope="source"),
        "prior_implementation_approved_at": prior_impl_at,
        "requirements_fingerprint": meta.get("requirements_fingerprint"),
        "implementation_fingerprint": meta.get("implementation_fingerprint"),
        "architecture_fingerprint": meta.get("architecture_fingerprint"),
        "quality_config_fingerprint": meta.get("quality_config_fingerprint"),
        "feature_validation_fingerprint": meta.get("feature_validation_fingerprint"),
        "module_plan_fingerprint": meta.get("module_plan_fingerprint"),
    }
    if reason:
        event["reason"] = reason.strip()
    try:
        save_feature(fid, meta)
        _append_gate_event(event)
    except Exception as exc:
        ip.write_text(prior_text, encoding="utf-8")
        write_json(feature_json(fid), prior_meta)
        render_status()
        raise SystemExit(f"Implementation approval transaction rolled back because gate event append failed: {exc}") from exc
    print(meta["implementation_fingerprint"])


def cmd_continuation_reapproval(args):
    if not args.confirmed_by_user:
        raise SystemExit("Continuation implementation reapproval is a human gate. Re-run only after explicit user approval with --confirmed-by-user.")
    active = active_state()
    fid = args.feature_id or active.get("feature_id")
    if not fid:
        raise SystemExit("Feature id required.")
    meta = load_feature(fid)
    branch = current_branch()
    reasons = continuation_reapproval_reasons(active, meta, branch, _load_gate_events(), args.reason)
    if reasons:
        raise SystemExit("Cannot record continuation implementation reapproval: " + "; ".join(reasons))
    if not requirements_current(meta):
        raise SystemExit("Requirements are not approved/current; approve SPEC.md first.")
    if meta.get("kind") == "feature" and not prototype_gate_ready(meta):
        raise SystemExit("Prototype decision is unresolved.")
    if not architecture_ready():
        raise SystemExit("Architecture profile is uninitialized/incomplete or project architecture metadata has not been registered with gov set-architecture.")
    if not has_configured_quality(meta):
        raise SystemExit("No verified quality/validation command is configured.")
    if active.get("kind") in {"greenfield", "feature"}:
        ok, detail = contract_modules_ready(meta)
        if not ok:
            raise SystemExit("Implementation approval requires explicit product/module ownership mapping: " + detail)
    _implementation_contract_approval(meta, fid, active, "continuation", args.reason)


def cmd_approve_implementation(args):
    if not args.confirmed_by_user: raise SystemExit("Implementation approval is a human gate. Re-run only after explicit user approval with --confirmed-by-user.")
    fid=args.feature_id or active_state().get("feature_id")
    if not fid: raise SystemExit("Feature id required.")
    meta=load_feature(fid)
    if not requirements_current(meta): raise SystemExit("Requirements are not approved/current; approve SPEC.md first.")
    if meta.get("kind")=="feature" and not prototype_gate_ready(meta): raise SystemExit("Prototype decision is unresolved. Record generate+approved, skip+skipped, or not-needed+skipped before implementation approval.")
    if not architecture_ready(): raise SystemExit("Architecture profile is uninitialized/incomplete or project architecture metadata has not been registered with gov set-architecture.")
    if not has_configured_quality(meta): raise SystemExit("No verified quality/validation command is configured. Add canonical project checks with gov set-quality or focused checks with gov set-feature-validation before approval.")
    active=active_state(required=False); active_kind=active.get("kind") if active and active.get("feature_id")==fid else meta.get("kind")
    if active_kind in {"greenfield","feature"}:
        ok,detail=contract_modules_ready(meta)
        if not ok: raise SystemExit("Implementation approval requires explicit product/module ownership mapping before coding: "+detail+". Register each planned module with gov set-module --id ... --status not_started --path <glob> [--acceptance AC-*].")
    # If a semantically identical implementation approval was invalidated by a
    # no-op prototype bookkeeping write, restore only from its sealed event.
    if active and active.get("feature_id") == fid and not meta.get("implementation_approved"):
        impl_fp = content_hash(feature_dir(fid) / "IMPLEMENTATION.md")
        arch_fp = content_hash(ARCH)
        for event in reversed(_load_gate_events()):
            if (event.get("event_type") in {"implementation_approval", "continuation_implementation_reapproval"}
                    and event.get("work_id") == active.get("work_id")
                    and event.get("feature_id") == fid
                    and event.get("work_branch") == active.get("work_branch")
                    and event.get("original_source_baseline") == active.get("preimplementation_source_fingerprint")
                    and event.get("requirements_fingerprint") == meta.get("requirements_fingerprint")
                    and event.get("implementation_fingerprint") == impl_fp
                    and event.get("architecture_fingerprint") == arch_fp):
                meta["implementation_approved"] = True
                meta["ready_for_luna"] = True
                meta["implementation_fingerprint"] = impl_fp
                meta["architecture_fingerprint"] = arch_fp
                meta["quality_config_fingerprint"] = quality_config_fingerprint()
                meta["feature_validation_fingerprint"] = feature_validation_fingerprint(meta)
                meta["module_plan_fingerprint"] = module_plan_fingerprint(meta)
                meta["status"] = "approved"
                save_feature(fid, meta)
                print(impl_fp)
                return
    if active and active.get("feature_id")==fid and active.get("risk") in {"standard","high"}:
        baseline=active.get("preimplementation_source_fingerprint"); current=git_fingerprint(ROOT,scope="source")
        if baseline and current!=baseline and not meta.get("implementation_approved"):
            raise SystemExit("Production source/tests/project configuration changed after this Standard/High task started but before the Implementation Gate. Revert or isolate those pre-approval code changes, then approve the implementation contract before coding.")
    _implementation_contract_approval(meta, fid, active, "initial")

def current_branch():
    if not is_git_repo(ROOT):
        return None
    p = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.stdout.strip() if p.returncode == 0 else None




def changed_working_paths():
    if not is_git_repo(ROOT):
        return []
    paths = set()
    # Use NUL-delimited raw path output. Line-oriented Git output may quote/escape non-ASCII
    # paths according to core.quotePath, which breaks governance-path classification.
    for args in (["diff", "--name-only", "-z", "--no-ext-diff"], ["diff", "--cached", "--name-only", "-z", "--no-ext-diff"], ["ls-files", "--others", "--exclude-standard", "-z"]):
        p = subprocess.run(["git", *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode == 0:
            for raw in p.stdout.split(b"\0"):
                if raw:
                    paths.add(raw.decode("utf-8", errors="surrogateescape").replace("\\", "/"))
    return sorted(paths)


def is_governance_bootstrap_path(rel):
    rel = rel.replace("\\", "/")
    if rel.startswith(".codex/"):
        return True
    if rel in {"AGENTS.md", ".gitignore", "docs/PROJECT_STATUS.md", "docs/architecture/PROFILE.md"}:
        return True
    if rel.startswith("docs/features/") or rel.startswith("docs/ops/") or rel.startswith("docs/incidents/") or rel.startswith("docs/adr/"):
        return True
    return False

def maybe_prepare_work_branch(kind, risk, work_id, project):
    """Create a local Codex work branch for Standard/High work when starting from a shared branch."""
    if risk not in {"standard", "high"} or kind == "release":
        return current_branch()
    if not ((project.get("workflow") or {}).get("auto_branch_standard_high", True)):
        return current_branch()
    branch = current_branch()
    shared = {"main", "master", "trunk", "develop", "development"}
    if branch and branch not in shared:
        return branch
    # An unborn Git repository has no ref for its initial branch yet. Creating codex/... now
    # would leave no real `main` ref to switch back to. Keep the initial branch for the first
    # greenfield task; once HEAD exists, later Standard/High tasks get isolated branches.
    head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if head.returncode != 0:
        return branch
    dirty_business = [p for p in changed_working_paths() if not is_governance_bootstrap_path(p)]
    if dirty_business:
        preview = ", ".join(dirty_business[:8]) + (" ..." if len(dirty_business) > 8 else "")
        raise SystemExit(
            "Refusing to create an automatic Standard/High work branch from a shared branch while pre-existing non-governance changes are present: "
            + preview
            + ". Commit/stash them, move to an existing dedicated branch, or use a separate Codex worktree first."
        )
    base = re.sub(r"[^a-z0-9._-]+", "-", work_id.lower()).strip("-.") or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = f"codex/{base}"
    existing = subprocess.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"], cwd=ROOT).returncode == 0
    n = 2
    while existing:
        candidate = f"codex/{base}-{n}"; n += 1
        existing = subprocess.run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"], cwd=ROOT).returncode == 0
    p = subprocess.run(["git", "switch", "-c", candidate], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        p = subprocess.run(["git", "checkout", "-b", candidate], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise SystemExit(
            "Could not create the local Standard/High work branch. Resolve the current Git state or start from an existing dedicated feature branch. "
            + p.stderr.strip()
        )
    return candidate


def cmd_set_architecture(args):
    if not ARCH.exists(): raise SystemExit("Missing docs/architecture/PROFILE.md; run bootstrap first.")
    text=ARCH.read_text(encoding="utf-8",errors="ignore"); ensure_no_governance_placeholders(text,"Architecture Profile")
    if "UNINITIALIZED" in text or not required_architecture_headings_present(text): raise SystemExit("Architecture Profile is not ready: provide required Stack/Complexity/Topology/Dependency/Test/Quality sections before registering it.")
    project=register_architecture(args.level,args.application_shape,args.runtime_mode); render_status(); print(json.dumps(project.get("project") or {},ensure_ascii=False))


def cmd_start_work(args):
    existing = active_state(required=False)
    if existing and existing.get("phase") not in {"done", "abandoned"}:
        raise SystemExit(
            f"Active work {existing.get('work_id')} is still in phase {existing.get('phase')}. "
            "Finish it, use a separate worktree, or run gov abandon-work --reason ... before starting another task in this checkout."
        )
    project = load_project(); meta = load_feature(args.feature_id, required=False) if args.feature_id else None
    risk_rank = {"trivial": 0, "standard": 1, "high": 2}
    meta_risk = meta.get("risk") if meta else None
    effective_risk = args.risk
    if meta_risk in risk_rank and risk_rank[meta_risk] > risk_rank[effective_risk]:
        effective_risk = meta_risk
    work_id = args.work_id or f"W-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    if args.phase == "implementation":
        preflight = {"kind": args.kind, "risk": effective_risk}
        reasons = approval_reasons(preflight, meta)
        if reasons: raise SystemExit("Cannot start implementation: " + "; ".join(reasons))
    work_branch = maybe_prepare_work_branch(args.kind, effective_risk, work_id, project)
    state = {
        "schema_version": 2,
        "work_id": work_id,
        "work_branch": work_branch,
        "kind": args.kind, "risk": effective_risk, "title": args.title, "feature_id": args.feature_id,
        "phase": args.phase, "docs_dirty": False, "docs_sync_reason": None,
        # Semantic docs are required when work is bound to a canonical Feature, or for a standalone Release record.
        # Do not manufacture a no-op documentation gate for an unbound Bug merely because its risk is Standard/High.
        "docs_required": bool(args.feature_id) or args.kind == "release",
        "docs_sync_fingerprint": git_fingerprint(ROOT, scope="implementation") if args.phase == "implementation" else None,
        "quality_required": True,
        "review_required": review_required_for(args.kind, effective_risk, project),
        "review_status": "pending", "review_fingerprint": None,
        "owner_session_id": None, "concurrency_conflict": False,
        "preimplementation_source_fingerprint": git_fingerprint(ROOT, scope="source"),
        "last_observed_code_fingerprint": git_fingerprint(ROOT, scope="implementation"),
        "semantic_docs_fingerprint": feature_changelog_fingerprint(meta) if meta else None,
        "pending_changed_paths": [],
        "affected_feature_ids": [],
        "feature_pending_paths": {},
        "work_touched_paths": [],
        "work_feature_paths": {},
        "synced_feature_ids": [],
        "docs_cycle_fingerprint": git_fingerprint(ROOT, scope="implementation") if args.phase == "implementation" else None,
        "started_at": now(), "updated_at": now(),
    }
    save_active(state)
    if meta:
        meta["status"] = "in_progress" if args.phase == "implementation" else meta.get("status", "draft")
        meta["review_required"] = state["review_required"]; save_feature(args.feature_id, meta)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_capture_preimplementation_baseline(args):
    """Record a dirty-worktree path baseline without changing user files."""
    s = active_state()
    if s.get("phase") not in {"implementation", "review", "release"}:
        raise SystemExit("A pre-implementation baseline can only be captured for active implementation work.")
    s["preimplementation_paths"] = changed_paths(ROOT, scope="implementation", include_preimplementation=True)
    # Keep the fingerprint aligned with the captured shared-checkout baseline.  Without
    # this refresh, approval compares against the task-start fingerprint and mistakes
    # legitimate pre-existing source changes for changes introduced before the gate.
    s["preimplementation_source_fingerprint"] = git_fingerprint(ROOT, scope="source")
    s["preimplementation_paths_captured_at"] = now()
    save_active(s)
    print(f"captured {len(s['preimplementation_paths'])} pre-existing implementation paths")


def cmd_set_phase(args):
    s = active_state(); target = args.phase
    if target == "done": raise SystemExit("Use gov complete to enter done; direct phase bypass is not allowed.")
    if target == "abandoned": raise SystemExit("Use gov abandon-work --reason ... to abandon active work explicitly.")
    if target == "implementation":
        expected_branch = s.get("work_branch")
        if expected_branch and current_branch() != expected_branch:
            raise SystemExit(f"Cannot enter implementation: active work belongs to branch {expected_branch!r}, current branch is {current_branch()!r}.")
        fid = s.get("feature_id"); meta = load_feature(fid, required=False) if fid else None
        reasons = approval_reasons(s, meta)
        if reasons: raise SystemExit("Cannot enter implementation: " + "; ".join(reasons))
        if meta:
            meta["status"] = "in_progress"; save_feature(fid, meta)
        s["docs_dirty"] = False
        s["docs_sync_fingerprint"] = git_fingerprint(ROOT, scope="implementation")
        s["semantic_docs_fingerprint"] = feature_changelog_fingerprint(meta) if meta else None
        s["pending_changed_paths"] = []
        s["affected_feature_ids"] = []
        s["feature_pending_paths"] = {}
        s["work_touched_paths"] = []
        s["work_feature_paths"] = {}
        s["synced_feature_ids"] = []
        s["docs_cycle_fingerprint"] = git_fingerprint(ROOT, scope="implementation")
        if meta:
            doc = refresh_feature_documentation(meta, status="current", changed=[])
            doc["changelog_fingerprint"] = feature_changelog_fingerprint(meta)
            save_feature(fid, meta)
    if target == "review":
        if s.get("phase") not in {"implementation", "review"}:
            raise SystemExit("Review phase can only be entered from implementation/review; do not skip the implementation phase.")
        g = gate_status(phase_override="review")
        # The review itself is not expected to have passed before entering review, but every
        # upstream gate (approvals, docs, quality, concurrency) must be current.
        non_review = [r for r in g["reasons"] if "review" not in r.lower()]
        if non_review:
            raise SystemExit("Cannot enter review: " + "; ".join(non_review))
    if target == "release":
        if s.get("phase") not in {"implementation", "review", "release"}:
            raise SystemExit("Release phase can only be entered after implementation (and review when required).")
        g = gate_status(phase_override="release")
        if not g["ready"]:
            raise SystemExit("Cannot enter release: " + "; ".join(g["reasons"]))
    s["phase"] = target; save_active(s); print(target)


def cmd_set_module(args):
    if args.status not in {"not_started","blocked"}:
        raise SystemExit("Module status in_progress/verified/done is lifecycle-managed. set-module may only declare/reset not_started or record blocked; code changes, Quality, and complete advance validated progress.")
    fid=args.feature_id or active_state().get("feature_id")
    if not fid: raise SystemExit("Feature id required.")
    meta=load_feature(fid); modules=[m for m in meta.get("modules",[]) if isinstance(m,dict)]; found=next((m for m in modules if m.get("id")==args.module_id),None)
    if not found:
        found={"id":args.module_id,"title":args.title or args.module_id,"status":args.status,"paths":[],"acceptance_ids":[],"optional":bool(args.optional)}; modules.append(found)
    else:
        if args.title: found["title"]=args.title
        found["status"]=args.status
        if args.optional: found["optional"]=True
    for value in args.path or []:
        value=value.replace("\\","/").strip().lstrip("./")
        if value and value not in found.setdefault("paths",[]): found["paths"].append(value)
    for value in args.acceptance or []:
        value=value.strip()
        if value and value not in found.setdefault("acceptance_ids",[]): found["acceptance_ids"].append(value)
    found["updated_at"]=now(); meta["modules"]=modules; save_feature(fid,meta); print(json.dumps(found,ensure_ascii=False))

def cmd_set_feature_status(args):
    fid = args.feature_id or active_state().get("feature_id")
    if not fid: raise SystemExit("Feature id required.")
    meta = load_feature(fid)
    if args.status == "done":
        raise SystemExit("Use gov complete to mark a feature done; direct status bypass is not allowed.")
    if args.status == "requirements_approved" and not requirements_current(meta):
        raise SystemExit("Cannot set requirements_approved: SPEC approval is missing or stale.")
    if args.status == "approved" and not implementation_current(meta):
        raise SystemExit("Cannot set approved: implementation approval is missing or stale.")
    if args.status == "in_progress":
        s = active_state(required=False)
        if s.get("feature_id") != fid or s.get("phase") not in {"implementation", "review", "release"}:
            raise SystemExit("Cannot set in_progress outside active implementation/review/release work for this feature.")
    meta["status"] = args.status; save_feature(fid, meta); print(args.status)

def _rewrite_first_heading(path, new_heading):
    path=Path(path)
    if not path.exists(): return False
    lines=path.read_text(encoding="utf-8").splitlines()
    for i,line in enumerate(lines):
        if line.startswith("# "):
            lines[i]=new_heading
            path.write_text("\n".join(lines)+"\n",encoding="utf-8")
            return True
    return False


def cmd_set_feature_title(args):
    if not args.confirmed_by_user: raise SystemExit("Feature title rename is a product/documentation change. Re-run after explicit user approval with --confirmed-by-user.")
    fid=args.feature_id or active_state().get("feature_id")
    if not fid: raise SystemExit("Feature id required.")
    meta=load_feature(fid); old_title=str(meta.get("title") or fid); new_title=args.title.strip()
    if not new_title: raise SystemExit("Feature title cannot be empty.")
    if old_title==new_title:
        print(new_title); return
    base=feature_dir(fid)
    _rewrite_first_heading(base/"SPEC.md",f"# {new_title} — Requirement Spec")
    _rewrite_first_heading(base/"IMPLEMENTATION.md",f"# {new_title} — Implementation Spec")
    _rewrite_first_heading(base/"CHANGELOG.md",f"# {new_title} — Change Log")
    meta["title"]=new_title; meta["title_updated_at"]=now()
    save_feature(fid,meta,render=False)
    append_changelog(fid,f"Feature renamed: {old_title} → {new_title}",before=old_title,after=new_title,notes="Canonical feature identity/ID is unchanged.")
    doc=refresh_feature_documentation(meta,status="current",changed=[],summary=f"Feature renamed: {old_title} → {new_title}")
    doc["last_synced_at"]=now(); doc["changelog_fingerprint"]=feature_changelog_fingerprint(meta)
    save_feature(fid,meta); print(meta["title"])


def cmd_retire_feature(args):
    if not args.confirmed_by_user: raise SystemExit("Retiring/superseding a feature is a human current-state gate. Re-run after explicit user approval with --confirmed-by-user.")
    fid=args.feature_id; meta=load_feature(fid); active=active_state(required=False)
    if active.get("feature_id")==fid and active.get("phase") not in {"done","abandoned"}: raise SystemExit("Cannot retire the feature while it has non-terminal active work in this checkout.")
    replacements=[x for x in (args.replacement or []) if x and x!=fid]
    if args.status=="superseded" and not replacements: raise SystemExit("A superseded feature must name at least one --replacement feature id.")
    for rid in replacements:
        if not feature_json(rid).exists(): raise SystemExit(f"Replacement feature does not exist: {rid}")
    previous_status=meta.get("status")
    meta.update({"retired":True,"retired_status":args.status,"status":args.status,"retired_at":now(),"retire_reason":args.reason,"superseded_by":replacements})
    save_feature(fid,meta,render=False)
    note=args.reason + (("; replacement: "+", ".join(replacements)) if replacements else "")
    append_changelog(fid,f"Feature {args.status}: {note}",before=str(previous_status),after=args.status,notes=note)
    doc=refresh_feature_documentation(meta,status="current",changed=[],summary=f"Feature {args.status}: {args.reason}"); doc["last_synced_at"]=now(); doc["changelog_fingerprint"]=feature_changelog_fingerprint(meta)
    save_feature(fid,meta); print(json.dumps({"feature_id":fid,"status":args.status,"replacement":replacements},ensure_ascii=False))


def cmd_mark_review(args):
    s = active_state()
    if args.status == "passed":
        if s.get("phase") not in {"review", "release"}:
            raise SystemExit("A passing review can only be recorded in review/release phase after current quality/docs gates are satisfied.")
        g = gate_status()
        # gate_status will report review itself as missing; all other blockers must already be clear.
        non_review = [r for r in g["reasons"] if "review" not in r.lower()]
        if non_review:
            raise SystemExit("Cannot record passing review: " + "; ".join(non_review))
    fid = s.get("feature_id"); current = git_fingerprint(ROOT, scope="review")
    s["review_status"] = args.status
    s["review_fingerprint"] = current if args.status == "passed" else None
    s["reviewed_at"] = now() if args.status == "passed" else None
    save_active(s)
    if fid and feature_json(fid).exists():
        meta = load_feature(fid); meta["review_status"] = args.status; meta["review_fingerprint"] = s["review_fingerprint"]; meta["reviewed_at"] = s["reviewed_at"]
        save_feature(fid, meta)
    print(args.status)


def cmd_waive_review(args):
    s = active_state()
    if s.get("phase") not in {"implementation", "review", "release"}:
        raise SystemExit("A review waiver can only be recorded for active implementation/review/release work.")
    if not args.confirmed_by_user:
        raise SystemExit("Review waiver requires --confirmed-by-user.")
    if s.get("review_status") == "waived" and s.get("review_waiver"):
        raise SystemExit("A review waiver can only be recorded once for the active work; it expires when the diff changes.")
    reason = str(args.reason or "").strip()
    if not reason:
        raise SystemExit("Review waiver requires a non-empty --reason.")
    current = git_fingerprint(ROOT, scope="review")
    event = {
        "event_type": "review_waiver",
        "work_id": s.get("work_id"), "work_branch": s.get("work_branch"),
        "feature_id": s.get("feature_id"), "review_fingerprint": current,
        "reason": reason, "confirmed_by_user": True, "recorded_at": now(),
    }
    previous = dict(s)
    s["review_status"] = "waived"
    s["review_fingerprint"] = None
    s["reviewed_at"] = None
    s["review_waiver"] = {
        "work_id": s.get("work_id"), "work_branch": s.get("work_branch"),
        "review_fingerprint": current, "reason": reason,
        "confirmed_by_user": True, "recorded_at": event["recorded_at"],
    }
    try:
        _append_gate_event(event)
        save_active(s)
    except Exception as exc:
        save_active(previous)
        raise SystemExit(f"Review waiver was rolled back: {exc}")
    print(json.dumps(s["review_waiver"], ensure_ascii=False))

def cmd_resolve_concurrency(args):
    s = active_state()
    if not s.get("concurrency_conflict"):
        print("no concurrency conflict"); return
    s["concurrency_conflict"] = False; s["concurrency_resolution"] = args.reason; s["concurrency_resolved_at"] = now(); s.pop("concurrency_conflict_session", None)
    s["owner_session_id"] = None; s["last_observed_code_fingerprint"] = git_fingerprint(ROOT, scope="implementation")
    save_active(s); print("concurrency conflict resolved; next active session will claim write ownership")


def affected_module_labels(meta, changed):
    if not meta or not changed:
        return []
    ids = matching_modules(meta, changed)
    labels = []
    for module in meta.get("modules") or []:
        if not isinstance(module, dict) or str(module.get("id")) not in ids:
            continue
        mid = str(module.get("id"))
        title = str(module.get("title") or mid)
        labels.append(f"{mid} — {title}" if title != mid else mid)
    return labels


def annotate_module_change(meta, changed, summary):
    if not meta or not changed:
        return
    ids = matching_modules(meta, changed)
    if not ids:
        return
    stamp = now()
    modules = []
    for module in meta.get("modules") or []:
        if not isinstance(module, dict):
            continue
        m = dict(module)
        if str(m.get("id")) in ids:
            m["last_change_summary"] = summary
            m["last_changed_at"] = stamp
        modules.append(m)
    meta["modules"] = modules


def append_changelog(fid, summary, before=None, after=None, notes=None, tests=None, changed=None):
    p = feature_dir(fid) / "CHANGELOG.md"
    if not p.exists():
        raise SystemExit(f"Missing {p}")
    meta = load_feature(fid, required=False)
    changed = list(changed or [])
    module_labels = affected_module_labels(meta, changed)
    module_lines = "\n".join(f"  - `{x}`" for x in module_labels) or "  - N/A"
    changed_lines = "\n".join(f"  - `{x}`" for x in changed) or "  - N/A"
    entry = f"""
## {now()} — {summary}

- **Purpose:** {summary}
- **Affected modules:**
{module_lines}
- **Before:** {before or 'N/A'}
- **After:** {after or 'N/A'}
- **Impact / Notes:** {notes or 'None'}
- **Validation:** {tests or 'Pending'}
- **Changed paths:**
{changed_lines}
"""
    with p.open("a", encoding="utf-8") as f:
        f.write(entry)
    return p


def cmd_add_change(args):
    fid = args.feature_id or active_state().get("feature_id")
    if not fid:
        raise SystemExit("Feature id required.")
    state = active_state(required=False)
    changed = state.get("pending_changed_paths") if state else None
    p = append_changelog(fid, args.summary, args.before, args.after, args.notes, args.tests, changed)
    meta = load_feature(fid, required=False)
    if meta:
        annotate_module_change(meta, changed or [], args.summary)
        save_feature(fid, meta, render=False)
        render_status()
    print(str(p))


def _refresh_doc_cycle_from_current_diff(state):
    current=git_fingerprint(ROOT,scope="implementation")
    baseline=state.get("docs_cycle_fingerprint") or state.get("docs_sync_fingerprint")
    if baseline!=current:
        paths=changed_paths(ROOT,scope="implementation")
        related=related_features_for_paths(ROOT,paths)
        active_fid=state.get("feature_id")
        if active_fid and active_fid not in related and feature_json(active_fid).exists(): related[active_fid]=paths
        state["docs_dirty"]=bool(state.get("docs_required",True))
        state["pending_changed_paths"]=paths
        state["affected_feature_ids"]=sorted(related)
        state["feature_pending_paths"]={k:v for k,v in related.items()}
        state["work_touched_paths"]=sorted(set(state.get("work_touched_paths") or [])|set(paths))
        work_map={k:list(v) for k,v in (state.get("work_feature_paths") or {}).items()}
        for k,v in related.items(): work_map[k]=sorted(set(work_map.get(k,[]))|set(v))
        state["work_feature_paths"]=work_map
        state["synced_feature_ids"]=[]
        state["docs_cycle_fingerprint"]=current
        ts=now()
        for fid,matched in related.items():
            meta=load_feature(fid,required=False)
            if not meta: continue
            doc=refresh_feature_documentation(meta,status="stale",changed=matched); doc["last_code_change_at"]=ts; save_feature(fid,meta,render=False)
        save_active(state); render_status()
    return current


def _repair_legacy_implementation_lineage(meta):
    """Normalize the one legacy continuation record without changing approval state."""
    changed = False
    lineage = meta.get("implementation_approval_lineage") or []
    for item in lineage:
        if not isinstance(item, dict) or item.get("event_type") != "implementation_approval_invalidated_by_requirements_reapproval":
            continue
        was_missing_fingerprint = item.get("implementation_fingerprint") is None
        if was_missing_fingerprint and meta.get("implementation_fingerprint"):
            item["implementation_fingerprint"] = meta.get("implementation_fingerprint")
            item["architecture_fingerprint"] = meta.get("architecture_fingerprint")
            if meta.get("requirements_approved_at"):
                item["at"] = meta.get("requirements_approved_at")
            changed = True
        if item.get("at") == item.get("implementation_approved_at") and meta.get("requirements_approved_at"):
            item["at"] = meta.get("requirements_approved_at")
            changed = True
    if changed:
        meta["implementation_approval_lineage"] = lineage
    return changed


def cmd_sync_docs(args):
    s=active_state(); current=_refresh_doc_cycle_from_current_diff(s); s=active_state()
    affected=list(s.get("affected_feature_ids") or [])
    fid=args.feature_id or s.get("feature_id")
    if affected and fid not in affected:
        if fid==s.get("feature_id") and not feature_json(fid).exists(): pass
        else: raise SystemExit(f"Feature {fid} is not affected by the current implementation diff according to declared module ownership.")
    meta=load_feature(fid,required=False) if fid else None
    if meta and _repair_legacy_implementation_lineage(meta):
        save_feature(fid, meta, render=False)
        render_status()
        meta=load_feature(fid,required=False)
    effective_dirty=bool(s.get("docs_dirty") or (s.get("docs_required",True) and s.get("docs_sync_fingerprint")!=current))
    if effective_dirty and meta:
        if s.get("risk") in {"standard","high"} and not args.summary: raise SystemExit("Documentation synchronization refused: Standard/High affected feature work requires --summary so governance appends a semantic CHANGELOG entry for this implementation cycle.")
        changed=(s.get("feature_pending_paths") or {}).get(fid) or s.get("pending_changed_paths") or changed_paths(ROOT,scope="implementation")
        if args.summary:
            append_changelog(fid,args.summary,args.before,args.after,args.notes,args.tests,changed)
            annotate_module_change(meta, changed, args.summary)
        current_semantic=feature_changelog_fingerprint(meta); baseline=(meta.get("documentation") or {}).get("changelog_fingerprint")
        if current_semantic==baseline and not (s.get("risk")=="trivial" and args.no_semantic_change): raise SystemExit("Documentation synchronization refused: implementation changed but the target feature CHANGELOG did not change for this cycle. Use --summary or make a real semantic changelog update.")
        doc=refresh_feature_documentation(meta,status="current",changed=[],summary=args.summary or args.reason); doc["last_synced_at"]=now(); doc["changelog_fingerprint"]=current_semantic; save_feature(fid,meta)
        synced=list(s.get("synced_feature_ids") or []);
        if fid not in synced: synced.append(fid)
        s["synced_feature_ids"]=sorted(synced)
        if fid==s.get("feature_id"): s["semantic_docs_fingerprint"]=current_semantic
    elif args.no_semantic_change and s.get("risk")!="trivial": raise SystemExit("--no-semantic-change is only valid for Trivial work.")
    remaining=sorted(set(s.get("affected_feature_ids") or [])-set(s.get("synced_feature_ids") or []))
    if remaining:
        s["docs_dirty"]=True; save_active(s); render_status(); raise SystemExit("Documentation synchronization is still incomplete for related feature(s): "+", ".join(remaining)+". Run gov sync-docs --feature-id <id> --summary ... for each affected feature.")
    s["docs_dirty"]=False; s["docs_sync_reason"]=args.reason; s["docs_synced_at"]=now(); s["docs_sync_fingerprint"]=current; s["last_observed_code_fingerprint"]=current; s["pending_changed_paths"]=[]; s["feature_pending_paths"]={}; save_active(s)
    subprocess.run([sys.executable,str(Path(__file__).resolve()),"repo-facts"],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); render_status(); print("docs synchronized")

def normalize_command_spec(item, phase, default_timeout):
    if isinstance(item, str):
        return {"phase": phase, "command": item, "cwd": ".", "timeout": default_timeout, "required": True}
    if isinstance(item, dict) and item.get("command"):
        return {"phase": item.get("phase", phase), "command": str(item["command"]), "cwd": str(item.get("cwd", ".")), "timeout": int(item.get("timeout", default_timeout)), "required": bool(item.get("required", True))}
    return None


def safe_cwd(rel):
    p = (ROOT / rel).resolve()
    try: p.relative_to(ROOT.resolve())
    except ValueError: raise SystemExit(f"Quality command cwd escapes repository: {rel}")
    if not p.exists(): raise SystemExit(f"Quality command cwd does not exist: {rel}")
    return p


def run_shell(spec):
    command = spec["command"]; cwd = safe_cwd(spec.get("cwd", ".")); timeout = spec.get("timeout", 600)
    print(f"\n>>> [{spec['phase']}] ({cwd.relative_to(ROOT) if cwd != ROOT else '.'}) {command}", flush=True)
    start = time.time()
    try:
        if os.name == "nt": argv = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
        else: argv = ["bash", "-lc", command]
        p = subprocess.run(argv, cwd=cwd, timeout=timeout)
        return p.returncode, round(time.time() - start, 3), None
    except subprocess.TimeoutExpired:
        return 124, round(time.time() - start, 3), f"timed out after {timeout}s"




def reject_production_validation_command(command):
    """Keep Quality Gate validation from becoming a hidden release/deploy channel.

    These are intentionally obvious high-impact command shapes. Repository-native test scripts
    still rely on Codex sandbox/permissions and human review, but direct release commands are
    never valid Quality Gate entries.
    """
    c = command.strip()
    protected = [
        r"(?:^|[;&|\n]\s*)git\s+push\b",
        r"(?:^|[;&|\n]\s*)gh\s+pr\s+merge\b",
        r"(?:^|[;&|\n]\s*)(npm|pnpm)\s+publish\b",
        r"(?:^|[;&|\n]\s*)yarn\s+(npm\s+)?publish\b",
        r"(?:^|[;&|\n]\s*)cargo\s+publish\b",
        r"(?:^|[;&|\n]\s*)twine\s+upload\b",
        r"(?:^|[;&|\n]\s*)dotnet\s+nuget\s+push\b",
        r"(?:^|[;&|\n]\s*)terraform\s+(apply|destroy)\b",
        r"(?:^|[;&|\n]\s*)kubectl\s+(apply|delete|replace|patch|scale|rollout)\b",
        r"(?:^|[;&|\n]\s*)helm\s+(install|upgrade|uninstall|rollback)\b",
        r"(?:^|[;&|\n]\s*)vercel\b.*(--prod|--production)\b",
        r"(?:^|[;&|\n]\s*)netlify\s+deploy\b.*--prod\b",
        r"(?:^|[;&|\n]\s*)fly\s+deploy\b",
    ]
    if any(re.search(p, c, re.I) for p in protected):
        raise SystemExit(
            "Production/remote mutation commands cannot be registered or executed as Quality Gate validation. "
            "Use the explicit release workflow and native Codex permissions instead."
        )

def cmd_set_quality(args):
    reject_production_validation_command(args.command)
    project = load_project(); item = {"command": args.command, "cwd": args.cwd, "timeout": args.timeout or project["quality"].get("default_timeout_seconds", 600), "required": True}
    arr = project["quality"]["commands"].setdefault(args.phase, [])
    if args.replace: arr[:] = []
    key = (item["command"], item["cwd"])
    if not any((x if isinstance(x,str) else x.get("command"), "." if isinstance(x,str) else x.get("cwd",".")) == key for x in arr): arr.append(item)
    save_project(project); render_status(); print(json.dumps(item, ensure_ascii=False))


def cmd_set_feature_validation(args):
    reject_production_validation_command(args.command)
    fid = args.feature_id or active_state().get("feature_id")
    if not fid: raise SystemExit("Feature id required.")
    meta = load_feature(fid)
    project = load_project()
    item = {"phase": args.phase, "command": args.command, "cwd": args.cwd, "timeout": args.timeout or project["quality"].get("default_timeout_seconds", 600)}
    arr = meta.setdefault("validation_commands", [])
    if args.replace: arr[:] = []
    key = (item["phase"], item["command"], item["cwd"] )
    if not any(((x.get("phase", "feature"), x.get("command"), x.get("cwd", ".")) if isinstance(x, dict) else ("feature", x, ".")) == key for x in arr):
        arr.append(item)
    save_feature(fid, meta)
    print(json.dumps(item, ensure_ascii=False))


def _affected_module_features(state, paths):
    """Return active feature metadata whose declared modules are touched by this work cycle.

    The current feature is included even when no PostToolUse hook ran, while retired features
    stay historical and never re-enter current ownership/progress tracking.
    """
    ids = list(state.get("affected_feature_ids") or []) if state else []
    current_fid = state.get("feature_id") if state else None
    if current_fid and current_fid not in ids:
        ids.append(current_fid)
    related = related_features_for_paths(ROOT, paths)
    for fid in related:
        if fid not in ids:
            ids.append(fid)
    out=[]
    for fid in ids:
        meta=load_feature(fid,required=False)
        if not meta or meta.get("retired") or meta.get("status") in {"archived","superseded"}:
            continue
        matched=(state.get("work_feature_paths") or {}).get(fid) if state else None
        matched=matched or ((state.get("feature_pending_paths") or {}).get(fid) if state else None)
        matched=matched or related.get(fid) or paths
        out.append((fid,meta,matched))
    return out


def cmd_quality(args):
    project = load_project(); s = active_state(required=False); default_timeout = int((project.get("quality") or {}).get("default_timeout_seconds", 600))
    cycle_paths = sorted(set((s.get("work_touched_paths") or []) + changed_paths(ROOT, scope="implementation"))) if s and s.get("phase") in {"implementation", "review", "release"} else []
    affected_module_meta = _affected_module_features(s, cycle_paths) if s and cycle_paths else []
    for affected_fid, affected_meta, matched_paths in affected_module_meta:
        changed_meta, _ = refresh_module_progress(affected_meta, matched_paths, to_status="in_progress")
        if changed_meta: save_feature(affected_fid, affected_meta)
    commands = []
    for phase in QUALITY_PHASES:
        for item in ((project.get("quality") or {}).get("commands") or {}).get(phase, []) or []:
            spec = normalize_command_spec(item, phase, default_timeout)
            if spec: commands.append(spec)
    fid = s.get("feature_id") if s else None
    if fid and feature_json(fid).exists():
        meta = load_feature(fid)
        for item in meta.get("validation_commands", []) or []:
            spec = normalize_command_spec(item, "feature", default_timeout)
            if spec: commands.append(spec)
    dedup=[]; seen=set()
    for spec in commands:
        key=(spec["phase"],spec["cwd"],spec["command"])
        if key not in seen: seen.add(key); dedup.append(spec)
    commands=dedup
    if not commands:
        write_json(quality_path(ROOT), {"status":"unconfigured","at":now(),"results":[],"fingerprint":None})
        raise SystemExit("No canonical quality commands configured. Verify repo-facts and configure project.json or feature validation commands.")
    results=[]; failed_required=False; optional_failures=[]
    for spec in commands:
        reject_production_validation_command(spec["command"])
        rc,duration,error=run_shell(spec); rec={**spec,"exit_code":rc,"duration_seconds":duration}
        if error: rec["error"] = error
        results.append(rec)
        if rc != 0:
            if spec.get("required", True):
                failed_required=True; break
            optional_failures.append(rec)
    if failed_required:
        write_json(quality_path(ROOT), {"status":"failed","at":now(),"results":results,"optional_failures":optional_failures,"fingerprint":None})
        raise SystemExit(results[-1]["exit_code"] or 1)
    for affected_fid, affected_meta, matched_paths in affected_module_meta:
        verified_meta, _ = refresh_module_progress(affected_meta, matched_paths, to_status="verified")
        if verified_meta: save_feature(affected_fid, affected_meta)
    fp=git_fingerprint(ROOT, scope="quality")
    rec={"status":"passed","at":now(),"results":results,"optional_failures":optional_failures,"fingerprint":fp,"governance_version":GOVERNANCE_VERSION}
    write_json(quality_path(ROOT),rec)
    print("\nQUALITY PASSED" + (" (with optional check failures)" if optional_failures else ""))
    print(fp)


def cmd_approve_release(args):
    if not args.confirmed_by_user: raise SystemExit("Release approval is a human gate. Re-run only after explicit user approval with --confirmed-by-user.")
    s = active_state()
    if s.get("phase") != "release": raise SystemExit("Enter release phase only after quality/review/docs gates pass, then record the exact approved command.")
    g = gate_status()
    if not g["ready"]:
        raise SystemExit("Release approval refused because completion gates are stale: " + "; ".join(g["reasons"]))
    command = args.command.strip()
    digest=hashlib.sha256(command.encode("utf-8")).hexdigest()
    release_fp = git_fingerprint(ROOT, scope="review")
    rec={"command":command,"command_sha256":digest,"release_fingerprint":release_fp,"approved_at":now(),"confirmed_by_user":True,"consumed":False,"work_id":s.get("work_id")}
    write_json(release_approval_path(ROOT),rec); print(digest)

def cmd_gate_status(args):
    # Refresh the generated human-visible status before reporting gates. This provides a
    # deterministic fallback when a specialized tool changed files without a PostToolUse event:
    # the fingerprint still makes PROJECT_STATUS show Docs=stale before completion.
    render_status()
    g=gate_status()
    if args.json: print(json.dumps(g,ensure_ascii=False))
    else:
        print("READY" if g["ready"] else "BLOCKED")
        for r in g["reasons"]: print("-",r)
    if args.fail and not g["ready"]: raise SystemExit(1)


def cmd_abandon_work(args):
    if not args.confirmed_by_user:
        raise SystemExit("Abandoning active work is a human task-switch gate. Re-run after explicit user approval with --confirmed-by-user.")
    s = active_state()
    if s.get("phase") in {"done", "abandoned"}:
        raise SystemExit(f"Work is already terminal: {s.get('phase')}")
    s["phase"] = "abandoned"
    s["abandoned_at"] = now()
    s["abandon_reason"] = args.reason
    s["owner_session_id"] = None
    save_active(s)
    fid = s.get("feature_id")
    if fid and feature_json(fid).exists():
        meta = load_feature(fid)
        if meta.get("status") != "done":
            meta["status"] = "blocked"
            meta["last_abandon_reason"] = args.reason
            save_feature(fid, meta)
    print("work abandoned")


def cmd_complete(args):
    s=active_state()
    if s.get("phase") not in {"implementation","review","release"}: raise SystemExit("Cannot complete from phase %r. Finish implementation/review/release gates instead of bypassing them." % s.get("phase"))
    g=gate_status(); fid=s.get("feature_id"); meta=load_feature(fid,required=False) if fid else None; extra=module_completion_reasons(s,meta)
    if not g["ready"] or extra: raise SystemExit("Cannot complete: "+"; ".join([*g.get("reasons",[]),*extra]))

    # Complete only modules touched by this validated work cycle. This keeps shared-module
    # progress truthful without marking unrelated modules/features done.
    cycle_paths=sorted(set((s.get("work_touched_paths") or [])+changed_paths(ROOT,scope="implementation")))
    affected=_affected_module_features(s,cycle_paths)
    for affected_fid, affected_meta, matched_paths in affected:
        changed_meta,_=refresh_module_progress(affected_meta,matched_paths,to_status="done")
        modules=[]
        matched_ids=set(matching_modules(affected_meta,matched_paths))
        for module in affected_meta.get("modules") or []:
            m=dict(module) if isinstance(module,dict) else module
            if isinstance(m,dict) and m.get("id") in matched_ids and m.get("status")=="verified":
                m["status"]="done"; m["updated_at"]=now(); changed_meta=True
            modules.append(m)
        if changed_meta: affected_meta["modules"]=modules
        if affected_fid==fid:
            affected_meta["status"]="done"
        save_feature(affected_fid,affected_meta)
        if affected_fid==fid:
            meta=affected_meta

    s["phase"]="done"; s["completed_at"]=now(); s["owner_session_id"]=None; save_active(s)
    if fid and meta and not any(x[0]==fid for x in affected):
        meta["status"]="done"; save_feature(fid,meta)
    else:
        render_status()
    print("work completed")

def cmd_ensure_doc(args):
    if args.kind in {"deployment","runbook","troubleshooting"}:
        names={"deployment":"DEPLOYMENT.md","runbook":"RUNBOOK.md","troubleshooting":"TROUBLESHOOTING.md"}; dest=DOCS/"ops"/names[args.kind]; src=TEMPLATES/"ops"/names[args.kind]
        dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists(): dest.write_text(src.read_text(encoding="utf-8"),encoding="utf-8")
    elif args.kind=="incident":
        iid=args.id or f"INC-{datetime.datetime.now():%Y%m%d-%H%M}"; dest=DOCS/"incidents"/f"{iid}.md"; dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists(): dest.write_text(fill_template(TEMPLATES/"incident.md",ID=iid),encoding="utf-8")
    elif args.kind=="adr":
        aid=args.id or f"ADR-{datetime.datetime.now():%Y%m%d-%H%M}"; title=args.title or "Architecture Decision"; dest=DOCS/"adr"/f"{aid}.md"; dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists(): dest.write_text(fill_template(TEMPLATES/"adr.md",ID=aid,TITLE=title,DATE=datetime.date.today().isoformat()),encoding="utf-8")
    else: raise SystemExit("Unknown doc kind")
    print(str(dest))


def cmd_render_status(args): print(str(render_status()))


def cmd_next_step(args):
    s = active_state(required=False)
    if not s:
        print("当前：描述新项目、功能迭代或 Bug。Codex 会自动初始化并选择流程。")
        return
    fid = s.get("feature_id")
    meta = load_feature(fid, required=False) if fid else None
    phase = s.get("phase")
    if phase in {"discovery", "requirements"}:
        if meta and not requirements_current(meta):
            print("当前：审核需求与测试意图。确认无误后回复：确认需求。")
        elif meta and s.get("kind") in {"greenfield", "feature"} and not prototype_gate_ready(meta):
            print("当前：确认是否需要原型；需要时审核原型，不需要就确认跳过。")
        elif not architecture_ready() and s.get("risk") in {"standard", "high"}:
            print("当前：审核技术选型与项目架构。")
        elif meta and not implementation_current(meta):
            print("当前：审核开发方案。确认无误后回复：确认开发方案。")
        else:
            print("当前：前置条件已满足，可以进入开发。")
        return
    if phase == "prototype":
        print("当前：审核原型效果；确认后继续开发方案。")
    elif phase in {"architecture", "planning"}:
        print("当前：审核技术选型/开发方案；确认后进入自动实现。")
    elif phase == "implementation":
        print("当前：自动实现、测试并同步文档。通常无需操作，除非出现新的重大决策。")
    elif phase == "review":
        print("当前：独立代码审核与修复。通常无需操作。")
    elif phase == "release":
        print("当前：发布门禁。只有生产影响操作才需要你的明确批准。")
    elif phase == "blocked":
        print("当前：存在重大阻塞，需要处理后继续。")
    elif phase == "done":
        print("当前：任务已完成。")
    elif phase == "abandoned":
        print("当前：任务已放弃。")
    else:
        print(f"当前阶段：{phase}")


def cmd_doctor(args):
    issues=[]; warnings=[]; checks=[]
    def ck(name, ok, detail=""):
        checks.append({"name":name,"ok":bool(ok),"detail":detail})
        if not ok: issues.append(f"{name}: {detail}")
    ck("governance root", GOV.exists(), str(GOV))
    ck("git repository", is_git_repo(ROOT), "bootstrap should initialize Git for governed projects")
    try:
        project=load_project(); ck("project.json schema", project.get("schema_version") == 4, f"schema={project.get('schema_version')}")
    except Exception as e:
        project={}; ck("project.json",False,str(e))
    try:
        json.loads((ROOT/".codex/hooks.json").read_text(encoding="utf-8")); ck("hooks.json",True)
    except Exception as e: ck("hooks.json",False,str(e))
    for rel in [
        ".codex/config.toml",
        ".codex/governance/gov.py",
        ".codex/governance/gov.ps1",
        ".codex/governance/repo-facts.json",
        ".codex/hooks/governance_pre_tool.py",
        ".codex/hooks/governance_post_tool.py",
        ".codex/hooks/governance_session_start.py",
        ".codex/hooks/governance_session_end.py",
        ".codex/hooks/governance_stop_gate.py",
        ".codex/rules/governance.rules",
        "docs/PROJECT_STATUS.md",
        "docs/architecture/PROFILE.md",
    ]:
        ck(rel,(ROOT/rel).exists(),"missing" if not (ROOT/rel).exists() else "")
    qcmds=((project.get("quality") or {}).get("commands") or {}) if project else {}
    if not any(qcmds.get(k) for k in QUALITY_PHASES): warnings.append("No canonical quality commands configured yet; verify repo-facts before implementation.")
    if not architecture_ready(): warnings.append("Architecture profile is not initialized yet.")
    s=active_state(required=False)
    if s:
        g=gate_status();
        if not g["ready"]: warnings.extend(g["reasons"])
    result={"ok":not issues,"issues":issues,"warnings":warnings,"checks":checks,"root":str(ROOT),"governance_version":GOVERNANCE_VERSION}
    if args.json: print(json.dumps(result,ensure_ascii=False,indent=2))
    else:
        print("DOCTOR PASS" if result["ok"] else "DOCTOR FAIL")
        for i in issues: print("ERROR:",i)
        for w in warnings: print("WARN:",w)
    if args.fail and issues: raise SystemExit(1)


def build_parser():
    p=argparse.ArgumentParser(description=f"Codex Engineering Governance {GOVERNANCE_VERSION}")
    sp=p.add_subparsers(dest="cmd",required=True)
    x=sp.add_parser("repo-facts"); x.set_defaults(func=cmd_repo_facts)
    x=sp.add_parser("new-feature"); x.add_argument("--title",required=True); x.add_argument("--risk",choices=RISKS,default="standard"); x.add_argument("--kind",choices=["feature","bug","engineering-change","maintenance"],default="feature"); x.add_argument("--id"); x.set_defaults(func=cmd_new_feature)
    x=sp.add_parser("approve-requirements"); x.add_argument("--feature-id"); x.add_argument("--confirmed-by-user",action="store_true"); x.set_defaults(func=cmd_approve_requirements)
    x=sp.add_parser("set-prototype"); x.add_argument("--feature-id"); x.add_argument("--decision",choices=["generate","skip","not-needed"],required=True); x.add_argument("--status",choices=["not_started","in_progress","approved","superseded","skipped"],required=True); x.add_argument("--path"); x.add_argument("--confirmed-by-user",action="store_true"); x.set_defaults(func=cmd_set_prototype)
    x=sp.add_parser("approve-implementation"); x.add_argument("--feature-id"); x.add_argument("--confirmed-by-user",action="store_true"); x.set_defaults(func=cmd_approve_implementation)
    x=sp.add_parser("continuation-reapproval"); x.add_argument("--feature-id"); x.add_argument("--reason",required=True); x.add_argument("--confirmed-by-user",action="store_true"); x.set_defaults(func=cmd_continuation_reapproval)
    x=sp.add_parser("set-architecture"); x.add_argument("--level",choices=["L1","L2","L3","L4"],required=True); x.add_argument("--application-shape",required=True); x.add_argument("--runtime-mode",choices=["windows-native","wsl2","linux","macos"]); x.set_defaults(func=cmd_set_architecture)
    x=sp.add_parser("start-work"); x.add_argument("--kind",required=True,choices=KINDS); x.add_argument("--risk",choices=RISKS,default="standard"); x.add_argument("--title",required=True); x.add_argument("--feature-id"); x.add_argument("--work-id"); x.add_argument("--phase",choices=["discovery","requirements","prototype","architecture","planning","implementation","blocked"],default="requirements"); x.set_defaults(func=cmd_start_work)
    x=sp.add_parser("capture-preimplementation-baseline"); x.set_defaults(func=cmd_capture_preimplementation_baseline)
    x=sp.add_parser("set-phase"); x.add_argument("phase",choices=PHASES); x.set_defaults(func=cmd_set_phase)
    x=sp.add_parser("set-module"); x.add_argument("--feature-id"); x.add_argument("--id",dest="module_id",required=True); x.add_argument("--title"); x.add_argument("--status",choices=["not_started","blocked"],required=True); x.add_argument("--path",action="append"); x.add_argument("--acceptance",action="append"); x.add_argument("--optional",action="store_true"); x.set_defaults(func=cmd_set_module)
    x=sp.add_parser("set-feature-title"); x.add_argument("--feature-id"); x.add_argument("--title",required=True); x.add_argument("--confirmed-by-user",action="store_true"); x.set_defaults(func=cmd_set_feature_title)
    x=sp.add_parser("retire-feature"); x.add_argument("--feature-id",required=True); x.add_argument("--status",choices=["archived","superseded"],required=True); x.add_argument("--reason",required=True); x.add_argument("--replacement",action="append"); x.add_argument("--confirmed-by-user",action="store_true"); x.set_defaults(func=cmd_retire_feature)
    x=sp.add_parser("set-feature-status"); x.add_argument("--feature-id"); x.add_argument("status",choices=["draft","requirements_approved","approved","in_progress","blocked","done"]); x.set_defaults(func=cmd_set_feature_status)
    x=sp.add_parser("mark-review"); x.add_argument("status",choices=["pending","passed","failed","skipped"]); x.set_defaults(func=cmd_mark_review)
    x=sp.add_parser("waive-review"); x.add_argument("--reason",required=True); x.add_argument("--confirmed-by-user",action="store_true"); x.set_defaults(func=cmd_waive_review)
    x=sp.add_parser("resolve-concurrency"); x.add_argument("--reason",required=True); x.set_defaults(func=cmd_resolve_concurrency)
    x=sp.add_parser("add-change"); x.add_argument("--feature-id"); x.add_argument("--summary",required=True); x.add_argument("--before"); x.add_argument("--after"); x.add_argument("--notes"); x.add_argument("--tests"); x.set_defaults(func=cmd_add_change)
    x=sp.add_parser("sync-docs"); x.add_argument("--feature-id"); x.add_argument("--reason",required=True); x.add_argument("--summary"); x.add_argument("--before"); x.add_argument("--after"); x.add_argument("--notes"); x.add_argument("--tests"); x.add_argument("--no-semantic-change",action="store_true"); x.set_defaults(func=cmd_sync_docs)
    x=sp.add_parser("set-quality"); x.add_argument("--phase",choices=QUALITY_PHASES,required=True); x.add_argument("--command",required=True); x.add_argument("--cwd",default="."); x.add_argument("--timeout",type=int); x.add_argument("--replace",action="store_true"); x.set_defaults(func=cmd_set_quality)
    x=sp.add_parser("set-feature-validation"); x.add_argument("--feature-id"); x.add_argument("--phase",choices=QUALITY_PHASES + ["feature"],default="feature"); x.add_argument("--command",required=True); x.add_argument("--cwd",default="."); x.add_argument("--timeout",type=int); x.add_argument("--replace",action="store_true"); x.set_defaults(func=cmd_set_feature_validation)
    x=sp.add_parser("quality"); x.set_defaults(func=cmd_quality)
    x=sp.add_parser("approve-release"); x.add_argument("--command",required=True); x.add_argument("--confirmed-by-user",action="store_true"); x.set_defaults(func=cmd_approve_release)
    x=sp.add_parser("gate-status"); x.add_argument("--json",action="store_true"); x.add_argument("--fail",action="store_true"); x.set_defaults(func=cmd_gate_status)
    x=sp.add_parser("abandon-work"); x.add_argument("--reason",required=True); x.add_argument("--confirmed-by-user",action="store_true"); x.set_defaults(func=cmd_abandon_work)
    x=sp.add_parser("complete"); x.set_defaults(func=cmd_complete)
    x=sp.add_parser("render-status"); x.set_defaults(func=cmd_render_status)
    x=sp.add_parser("ensure-doc"); x.add_argument("kind",choices=["deployment","runbook","troubleshooting","incident","adr"]); x.add_argument("--id"); x.add_argument("--title"); x.set_defaults(func=cmd_ensure_doc)
    x=sp.add_parser("doctor"); x.add_argument("--json",action="store_true"); x.add_argument("--fail",action="store_true"); x.set_defaults(func=cmd_doctor)
    x=sp.add_parser("next-step"); x.set_defaults(func=cmd_next_step)
    return p


def main():
    args=build_parser().parse_args(); args.func(args)


if __name__ == "__main__":
    main()
