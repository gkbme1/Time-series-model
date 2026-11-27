#!/usr/bin/env bash
# 一键启动：安装 / 更新依赖 → 构建前端（每次启动都重新构建，拉取的代码改动总能生效）→ 启动服务
# 选项：PORT=8080 换端口；SKIP_BUILD=1 跳过前端构建
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
VENV=backend/.venv
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
fi
if ! cmp -s backend/requirements.txt "$VENV/requirements.installed"; then
  "$VENV/bin/pip" install -q -r backend/requirements.txt
  cp backend/requirements.txt "$VENV/requirements.installed"
fi
if [ ! -f data/timeseries/BA-101.parquet ]; then
  "$VENV/bin/python" scripts/gen_demo_data.py
fi
if [ "${SKIP_BUILD:-0}" != "1" ] || [ ! -f frontend/dist/index.html ]; then
  (cd frontend
   if ! cmp -s package-lock.json node_modules/package-lock.installed; then
     npm install --no-audit --no-fund && cp package-lock.json node_modules/package-lock.installed
   fi
   npm run build)
fi
cd backend
exec .venv/bin/python -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --no-use-colors
