#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
base=/home/admin/ai-management-ys
candidate=${1:?candidate required}
wheel_sha=${2:?wheel sha required}
[[ "$candidate" =~ ^ys-[0-9a-f]{16}$ && "$wheel_sha" =~ ^[0-9a-f]{64}$ ]] || exit 10
new="$base/releases/$candidate"
old="$base/releases/ys-0d50839c7a838474"
[[ "$(readlink -f "$base/current")" == "$old" && -d "$new" && -f "$old/.env.ys" ]] || exit 11
cd "$new"
python3 - <<'PY'
import hashlib,json
from pathlib import Path
p=json.loads(Path('candidate.json').read_text())
for name,expected in p['files'].items():
 f=Path(name).resolve()
 if not f.is_relative_to(Path.cwd()) or hashlib.sha256(f.read_bytes()).hexdigest()!=expected:
  raise SystemExit('candidate file verification failed')
print('candidate_files_verified')
PY
python3 ys-readonly-preflight.py > pre-update.json
python3 - <<'PY'
import json
p=json.load(open('pre-update.json')); r=p['runtime']
assert r['ok']; r=r['result']
assert r['database_identity']==['ai_ys','ai_ys'] and r['migration_head']=='o2p3q4r5s6t7'
assert all(v==0 for v in r['table_counts'].values())
assert r['runtime_key_placeholder'] and not r['batch_enabled'] and not r['course_enabled']
assert r['redis']['effective_db']==6 and r['redis']['all_list_entries']==0
assert r['redis']['unacked']==0 and r['redis']['unacked_index']==0
assert all(x['response_received'] and x['task_count']==0 and x['responding_nodes']==4 for x in r['celery'].values())
assert p['disk_env']['mode']=='0o600' and p['disk_env']['key_nonempty'] and not p['disk_env']['key_placeholder']
assert all(x['running'] for x in p['containers'])
print('pre_update_scope_and_idle_verified')
PY
install -m 600 "$old/.env.ys" "$new/.env.ys"
python3 - <<'PY'
import json,subprocess
from pathlib import Path
services=['api','worker','model-realtime-worker','publication-worker','ocr-worker','ui','scheduler']
names=['ai-management-ys-ai-ys-'+s+'-1' for s in services]
items=json.loads(subprocess.check_output(['docker','inspect',*names]))
out={'services':{}}
original={}
for x,s in zip(items,services):
 values=x['Config'].get('Env',[])
 assert all('\n' not in v and '\r' not in v for v in values)
 original['ai-ys-'+s]={v.split('=',1)[0]:v.split('=',1)[1] for v in values if '=' in v}
 out['services']['ai-ys-'+s]={'image':'jx-now-ai-ys:ys-0d50839c7a838474','env_file':[],'environment':[v.replace('$','$$') for v in values]}
Path('rollback.compose.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
Path('rollback.compose.json').chmod(0o600)
Path('rollback.original-env.json').write_text(json.dumps(original,sort_keys=True)+'\n')
Path('rollback.original-env.json').chmod(0o600)
shared=['docker-api-1','docker-worker-1','docker-db_postgres-1','docker-redis-1']
items=json.loads(subprocess.check_output(['docker','inspect',*shared]))
Path('rollback.shared-images.json').write_text(json.dumps({x['Name'].lstrip('/'):x['Image'] for x in items},sort_keys=True)+'\n')
Path('rollback.shared-images.json').chmod(0o600)
PY
mkdir -p acceptance-input acceptance-staging
export AI_YS_IMAGE="jx-now-ai-ys:$candidate" AI_YS_OCR_IMAGE="jx-now-ai-ys:$candidate" AI_YS_UI_IMAGE="jx-now-ai-ys:$candidate"
export AI_YS_ENV_FILE="$new/.env.ys" AI_YS_OCR_MODELS_DIR="$old/ocr-models"
export AI_YS_ACCEPTANCE_INPUT_DIR="$new/acceptance-input" AI_YS_ACCEPTANCE_STAGING_DIR="$new/acceptance-staging"
docker compose -f docker-compose.ys.yml config --quiet
docker build -t "$AI_YS_IMAGE" --build-arg "CANDIDATE_ID=$candidate" --build-arg "WHEEL_SHA256=$wheel_sha" -f Dockerfile.ys .
docker image inspect "$AI_YS_IMAGE" --format '{{.Id}}' > candidate-image-id.txt
docker run --rm --network docker_default --env-file "$new/.env.ys" -e APP_ENV=test -e CLEANING_ACCEPTANCE_BUDGET_ENABLED=true -e CLEANING_DAILY_BUDGET_YUAN=4 -e JX_ACCEPTANCE_CLEANING_BUDGET_MAX_YUAN=5 -e DIFY_COURSE_DATASET_ENABLED=false "$AI_YS_IMAGE" python -c 'from ai_management.config import get_settings; from ai_management.db import SessionLocal; from sqlalchemy import text; s=get_settings(); assert not s.bailian_batch_enabled and not s.dify_course_dataset_enabled; db=SessionLocal(); assert db.execute(text("select current_database()")).scalar()=="ai_ys"; assert db.execute(text("select version_num from alembic_version")).scalar()=="o2p3q4r5s6t7"; db.close(); print("candidate_config_and_existing_schema_verified")'
changed=0
rollback() {
 code=$?
 if [[ "$changed" == 1 ]]; then
  rollback_failed=0
  export AI_YS_IMAGE=jx-now-ai-ys:ys-0d50839c7a838474 AI_YS_OCR_IMAGE=jx-now-ai-ys:ys-0d50839c7a838474 AI_YS_UI_IMAGE=jx-now-ai-ys:ys-0d50839c7a838474
  export AI_YS_ENV_FILE="$old/.env.ys" AI_YS_OCR_MODELS_DIR="$old/ocr-models"
  docker compose -f "$old/docker-compose.ys.yml" stop ai-ys-api ai-ys-worker ai-ys-model-realtime-worker ai-ys-publication-worker ai-ys-ocr-worker ai-ys-ui ai-ys-scheduler || rollback_failed=1
  ln -s "$old" "$base/current.rollback"
  mv -Tf "$base/current.rollback" "$base/current"
  docker compose -f "$old/docker-compose.ys.yml" -f "$new/rollback.compose.json" config --quiet || rollback_failed=1
  docker compose -f "$old/docker-compose.ys.yml" -f "$new/rollback.compose.json" up -d --no-deps --force-recreate ai-ys-api ai-ys-worker ai-ys-model-realtime-worker ai-ys-publication-worker ai-ys-ocr-worker ai-ys-ui ai-ys-scheduler || rollback_failed=1
  python3 - <<'PY' || rollback_failed=1
import json,subprocess
old='sha256:cd6b184b22ea3182a8ad119affee088b89c143dc1d4436c978fc8b206042f4a3'
names=['ai-management-ys-ai-ys-'+s+'-1' for s in ['api','worker','model-realtime-worker','publication-worker','ocr-worker','ui','scheduler']]
x=json.loads(subprocess.check_output(['docker','inspect',*names])); assert all(i['State']['Running'] and i['Image']==old for i in x)
shared=json.load(open('rollback.shared-images.json')); now=json.loads(subprocess.check_output(['docker','inspect',*shared])); assert {i['Name'].lstrip('/'):i['Image'] for i in now}==shared
assert __import__('os').path.realpath('/home/admin/ai-management-ys/current')=='/home/admin/ai-management-ys/releases/ys-0d50839c7a838474'
print('rollback_old_image_set_verified')
PY
  python3 - <<'PY' || rollback_failed=1
import json,urllib.request,time
for _ in range(60):
 try:
  assert json.load(urllib.request.urlopen('http://127.0.0.1:18080/health/ready',timeout=3)).get('status')=='ready'
  assert urllib.request.urlopen('http://127.0.0.1:18501/ai-management-test/',timeout=3).status==200
  break
 except Exception: time.sleep(2)
else: raise SystemExit('rollback readiness failed')
print('rollback_ready_ui_verified')
PY
  if [[ "$rollback_failed" == 1 ]]; then printf 'rollback_failed\n' >&2; exit 90; fi
  printf 'rollback_attempted_with_original_runtime_config_and_readiness_verified\n'
 fi
 exit "$code"
}
trap rollback ERR
changed=1
# Isolate API/UI/scheduler first; confirm all four worker containers remain
# present and running before stopping them, then inspect DB/Redis directly.
docker compose -f docker-compose.ys.yml stop ai-ys-api ai-ys-ui ai-ys-scheduler
export AI_YS_IMAGE=jx-now-ai-ys:ys-0d50839c7a838474 AI_YS_OCR_IMAGE=jx-now-ai-ys:ys-0d50839c7a838474 AI_YS_UI_IMAGE=jx-now-ai-ys:ys-0d50839c7a838474
export AI_YS_ENV_FILE="$old/.env.ys" AI_YS_OCR_MODELS_DIR="$old/ocr-models" AI_YS_ACCEPTANCE_INPUT_DIR="$new/acceptance-input" AI_YS_ACCEPTANCE_STAGING_DIR="$new/acceptance-staging"
docker compose -f "$old/docker-compose.ys.yml" -f "$new/rollback.compose.json" config --format json | python3 -c 'import json,sys; x=json.load(sys.stdin); o=json.load(open("rollback.original-env.json")); assert all(x["services"][s].get("environment",{})==v for s,v in o.items()); json.dump({"config_roundtrip":True},open("rollback-config-roundtrip.json","w")); print("old_compose_environment_roundtrip_verified")'
python3 - <<'PY'
import json,subprocess
names=['ai-management-ys-ai-ys-'+s+'-1' for s in ['worker','model-realtime-worker','publication-worker','ocr-worker']]
items=json.loads(subprocess.check_output(['docker','inspect',*names]))
host={s:x['Config']['Hostname'] for s,x in zip(['worker','realtime','publication','ocr'],items)}
expected={f'celery@{host["worker"]}',f'celery@{host["realtime"]}',f'celery@{host["publication"]}',f'ai-ocr-ys@{host["ocr"]}'}
code='import json,sys; from ai_management.tasks import celery_app; expected=set(json.loads(sys.stdin.read())); i=celery_app.control.inspect(timeout=5); print(json.dumps({"expected":sorted(expected),"active":i.active() or {},"reserved":i.reserved() or {},"scheduled":i.scheduled() or {}}))'
out=subprocess.check_output(['docker','exec','-i','ai-management-ys-ai-ys-worker-1','python','-c',code],input=json.dumps(sorted(expected)),text=True); x=json.loads(out)
sets=[set(v) for k,v in x.items() if k in ('active','reserved','scheduled')]; assert all(s==expected for s in sets)
assert all(all(not tasks for tasks in x[k].values()) for k in ('active','reserved','scheduled'))
json.dump({'celery_inspect':True,'expected_nodes':sorted(expected),'active_empty':True,'reserved_empty':True,'scheduled_empty':True},open('celery-isolation-check.json','w'))
print('celery_four_node_isolation_verified')
PY
docker compose -f docker-compose.ys.yml stop ai-ys-worker ai-ys-model-realtime-worker ai-ys-publication-worker ai-ys-ocr-worker
docker exec docker-db_postgres-1 psql -U postgres -d ai_ys -Atqc "select coalesce(sum(n),0) from (select count(*) n from business_conversation union all select count(*) from business_message union all select count(*) from cleaning_batch union all select count(*) from cleaning_file union all select count(*) from cleaning_document union all select count(*) from dify_publication_shard union all select count(*) from external_deletion_task union all select count(*) from model_cleaning_task union all select count(*) from tool_conversation union all select count(*) from tool_message) q" | grep -qx 0
for q in ai_cleaning ai_default ai_model_batch ai_model_realtime ai_ocr ai_publication; do docker exec docker-redis-1 redis-cli -n 6 llen "$q" | grep -qx 0; done
docker exec docker-redis-1 redis-cli -n 6 hlen unacked | grep -qx 0
docker exec docker-redis-1 redis-cli -n 6 zcard unacked_index | grep -qx 0
# The read-only old-compose roundtrip above intentionally switches exports;
# restore candidate image/env values immediately before candidate recreation.
export AI_YS_IMAGE="jx-now-ai-ys:$candidate" AI_YS_OCR_IMAGE="jx-now-ai-ys:$candidate" AI_YS_UI_IMAGE="jx-now-ai-ys:$candidate"
export AI_YS_ENV_FILE="$new/.env.ys" AI_YS_OCR_MODELS_DIR="$old/ocr-models"
export AI_YS_ACCEPTANCE_INPUT_DIR="$new/acceptance-input" AI_YS_ACCEPTANCE_STAGING_DIR="$new/acceptance-staging"
docker compose -f docker-compose.ys.yml stop ai-ys-scheduler ai-ys-model-realtime-worker
docker compose -f docker-compose.ys.yml up -d --no-deps --force-recreate ai-ys-api ai-ys-worker ai-ys-model-realtime-worker ai-ys-publication-worker ai-ys-ocr-worker ai-ys-ui
python3 - <<'PY'
import time,urllib.request,json
for attempt in range(60):
 try:
  r=json.load(urllib.request.urlopen('http://127.0.0.1:18080/health/ready',timeout=3))
  assert r.get('status')=='ready'
  assert urllib.request.urlopen('http://127.0.0.1:18501/ai-management-test/',timeout=3).status==200
  break
 except Exception:
  if attempt==59: raise SystemExit('candidate readiness verification failed')
  time.sleep(2)
print('candidate_ready_and_ui_verified')
PY
docker exec -u 10001 ai-management-ys-ai-ys-api-1 python -c 'import os,tempfile; f=tempfile.NamedTemporaryFile(mode="wb",dir="/app/var/audit",prefix=".probe.",delete=False); p=f.name; f.write(b"audit-volume-probe"); f.flush(); os.fsync(f.fileno()); f.close(); assert open(p,"rb").read()==b"audit-volume-probe"; os.unlink(p); print("audit_volume_appuser_verified")'
python3 ys-readonly-preflight.py > post-update-before-scheduler.json
python3 - <<'PY'
import json
r=json.load(open('post-update-before-scheduler.json'))['runtime']['result']
assert all(v==0 for v in r['table_counts'].values())
assert r['redis']['all_list_entries']==r['redis']['unacked']==r['redis']['unacked_index']==0
assert not r['runtime_key_placeholder'] and not r['batch_enabled'] and not r['course_enabled']
assert r['audit']['directory_present'] and not r['audit']['campaign_ledger_present']
PY
docker compose -f docker-compose.ys.yml up -d --no-deps --force-recreate ai-ys-scheduler
python3 - <<'PY'
import json,subprocess
from pathlib import Path
pre=json.load(open('pre-update.json'))
names=[x['name'] for x in pre['containers'] if x['name'].startswith('docker-')]
current=json.loads(subprocess.check_output(['docker','inspect',*names]))
prior={x['name']:x for x in pre['containers']}
assert all(x['State']['Running'] and x['Image']==prior[x['Name'].lstrip('/')]['image_id'] for x in current)
expected=Path('candidate-image-id.txt').read_text().strip()
services=['api','worker','model-realtime-worker','publication-worker','ocr-worker','ui','scheduler']
containers=json.loads(subprocess.check_output(['docker','inspect',*['ai-management-ys-ai-ys-'+s+'-1' for s in services]]))
diag=[{'name':x['Name'].lstrip('/'),'running':x['State']['Running'],'image':x['Image']} for x in containers]
Path('final-services.json').write_text(json.dumps(diag,sort_keys=True,indent=2)+'\n')
assert all(x['running'] and x['image']==expected for x in diag)
Path('post-images.json').write_text(json.dumps({'candidate_image_id':expected,'default_services':7,'worker_nodes':4,'shared_images_unchanged':True},indent=2)+'\n')
print('seven_services_same_image_and_shared_services_verified')
PY
ln -s "$new" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
trap - ERR
printf 'ys_candidate_updated_no_paid_calls_requested\n'
