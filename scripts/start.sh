#!/usr/bin/env bash
# 一键启动：安装依赖 → （如缺少）生成示例数据 → 构建前端 → 启动服务（http://127.0.0.1:8000）
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
if [ ! -x backend/.venv/bin/python ]; then
  "$PY" -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -f data/timeseries/BA-101.parquet ]; then
  backend/.venv/bin/python scripts/gen_demo_data.py
fi
if [ ! -f frontend/dist/index.html ] || [ "${REBUILD:-0}" = "1" ]; then
  (cd frontend && npm install --no-audit --no-fund && npm run build)
fi
cd backend
exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
