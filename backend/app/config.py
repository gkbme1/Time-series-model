"""路径与全局配置。可用环境变量覆盖，便于测试。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("TSM_DATA_DIR", ROOT / "data"))
VAR_DIR = Path(os.environ.get("TSM_VAR_DIR", ROOT / "var"))
FRONTEND_DIST = Path(os.environ.get("TSM_FRONTEND_DIST", ROOT / "frontend" / "dist"))

# Demo 不做登录，审批人与操作人用固定名称
DEFAULT_USER = "赵工"
APPROVERS = ["李工（工艺）", "王工（负责人）"]


def ensure_dirs() -> None:
    for d in (VAR_DIR, VAR_DIR / "datasets", VAR_DIR / "imports", VAR_DIR / "models", VAR_DIR / "outputs"):
        d.mkdir(parents=True, exist_ok=True)
