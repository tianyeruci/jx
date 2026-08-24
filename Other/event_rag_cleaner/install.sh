#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/opt/ai-management/event_rag_cleaner}"
PYTHON="${PYTHON:-python3}"

mkdir -p "$ROOT"
echo "请先将压缩包内容解压到: $ROOT"
cd "$ROOT"

$PYTHON -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

echo "安装完成。下一步编辑 $ROOT/.env，然后运行："
echo "source $ROOT/.venv/bin/activate"
echo "python $ROOT/clean_events.py --base-dir /opt/ai-management/tmp/活动"
