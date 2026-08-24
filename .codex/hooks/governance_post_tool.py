#!/usr/bin/env python3
import datetime, hashlib, json, subprocess, sys
from pathlib import Path
try: payload=json.load(sys.stdin)
except Exception: sys.exit(0)
root=Path(payload.get('cwd') or Path.cwd())
try:
    p=subprocess.run(['git','rev-parse','--show-toplevel'],cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode==0 and p.stdout.strip(): root=Path(p.stdout.strip())
except Exception: pass
gov=root/'.codex'/'governance'; sys.path.insert(0,str(gov))
try:
    from govlib import read_json,write_json,git_fingerprint,changed_paths,active_path,release_approval_path,related_features_for_paths,matching_modules
except Exception: sys.exit(0)
state=read_json(active_path(root),{}) or {}
if not state: sys.exit(0)
now=datetime.datetime.now().astimezone().isoformat(timespec='seconds'); session_id=payload.get('session_id'); message=None
current=git_fingerprint(root,scope='implementation'); previous=state.get('last_observed_code_fingerprint')
if previous is None: state['last_observed_code_fingerprint']=current
elif current!=previous:
    owner=state.get('owner_session_id')
    if session_id and not owner and state.get('phase') in {'implementation','review','release'}:
        state['owner_session_id']=session_id; state['owner_claimed_at']=now; owner=session_id
    if owner and session_id and owner!=session_id:
        state['concurrency_conflict']=True; state['concurrency_conflict_session']=session_id; state['concurrency_conflict_at']=now
    if state.get('phase') in {'implementation','review','release'}:
        paths=changed_paths(root,scope='implementation'); related=related_features_for_paths(root,paths); active_fid=state.get('feature_id')
        if active_fid and active_fid not in related and (root/'docs'/'features'/active_fid/'feature.json').exists(): related[active_fid]=paths
        state['docs_dirty']=bool(state.get('docs_required',True)); state['pending_changed_paths']=paths
        state['affected_feature_ids']=sorted(related); state['feature_pending_paths']={k:v for k,v in related.items()}
        state['work_touched_paths']=sorted(set(state.get('work_touched_paths') or [])|set(paths))
        work_map={k:list(v) for k,v in (state.get('work_feature_paths') or {}).items()}
        for k,v in related.items(): work_map[k]=sorted(set(work_map.get(k,[]))|set(v))
        state['work_feature_paths']=work_map; state['synced_feature_ids']=[]; state['docs_cycle_fingerprint']=current
        for fid,matched in related.items():
            mp=root/'docs'/'features'/fid/'feature.json'; meta=read_json(mp,{}) or {}; doc=dict(meta.get('documentation') or {})
            doc['status']='stale'; doc['last_code_change_at']=now; doc['pending_changed_paths']=matched; meta['documentation']=doc
            mods=matching_modules(meta,matched)
            for module in meta.get('modules') or []:
                if isinstance(module,dict) and str(module.get('id')) in mods and not module.get('optional',False) and module.get('status') in {'not_started','verified','done'}:
                    module['status']='in_progress'; module['updated_at']=now
            meta['updated_at']=now; write_json(mp,meta)
        gp=root/'.codex'/'governance'/'gov.py'
        if gp.exists(): subprocess.run([sys.executable,str(gp),'render-status'],cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        message='Implementation changed. Governance marked related feature documentation stale using declared module-path ownership. Synchronize every affected feature before Quality/Review.'
    state['last_observed_code_fingerprint']=current
state['updated_at']=now; write_json(active_path(root),state)
tool=str(payload.get('tool_name') or ''); ti=payload.get('tool_input') or {}; command=str(ti.get('command') or '') if isinstance(ti,dict) else ''
if tool=='Bash' and command:
    approval=read_json(release_approval_path(root),{}) or {}; digest=hashlib.sha256(command.strip().encode()).hexdigest()
    if approval.get('command_sha256')==digest and approval.get('consumed') is not True and approval.get('release_fingerprint')==git_fingerprint(root,scope='review'):
        approval['consumed']=True; approval['consumed_at']=now; write_json(release_approval_path(root),approval)
if message: print(json.dumps({'systemMessage':message},ensure_ascii=False))
