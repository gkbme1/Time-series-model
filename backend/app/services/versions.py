"""配置版本（WC-vN 工况方案、F-vN 输入量配置）与场景阶段推进。"""
from __future__ import annotations

import copy
import json

from sqlalchemy import func, select

from ..store import db
from .scenarios import STAGES

STATUS = {"选数据": "数据准备", "分工况": "数据准备", "加工输入": "特征工程", "训练模型": "特征工程",
          "检验效果": "验证中", "上线投用": "在线运行", "在线校核": "在线运行", "跟踪改进": "在线运行"}


def latest(s, sid: int, kind: str):
    return s.scalars(select(db.ConfigVersion).where(db.ConfigVersion.scenario_id == sid, db.ConfigVersion.kind == kind)
                     .order_by(db.ConfigVersion.id.desc())).first()


def ensure(s, sc: db.Scenario, kind: str, content: dict, note: str = "") -> tuple[str, bool]:
    """内容与最新版本相同则复用，否则保存新版本。返回 (编号, 是否新建)。"""
    last = latest(s, sc.id, kind)
    if last is not None and json.dumps(last.content, sort_keys=True, default=str) == json.dumps(content, sort_keys=True, default=str):
        return last.code, False
    n = s.scalar(select(func.count()).select_from(db.ConfigVersion)
                 .where(db.ConfigVersion.scenario_id == sc.id, db.ConfigVersion.kind == kind)) or 0
    code = f"{kind}-v{n + 1}"
    s.add(db.ConfigVersion(scenario_id=sc.id, kind=kind, code=code, content=copy.deepcopy(content), note=note))
    return code, True


def dirty(s, sc: db.Scenario, kind: str, content: dict) -> tuple[str | None, bool, str]:
    last = latest(s, sc.id, kind)
    n = s.scalar(select(func.count()).select_from(db.ConfigVersion)
                 .where(db.ConfigVersion.scenario_id == sc.id, db.ConfigVersion.kind == kind)) or 0
    if last is None:
        return None, True, f"{kind}-v1"
    same = json.dumps(last.content, sort_keys=True, default=str) == json.dumps(content, sort_keys=True, default=str)
    return last.code, not same, f"{kind}-v{n + 1}"


def advance(sc: db.Scenario, stage: str):
    """场景阶段只前进不后退。"""
    if STAGES.index(stage) > STAGES.index(sc.stage if sc.stage in STAGES else "选数据"):
        sc.stage = stage
        sc.status = STATUS.get(stage, sc.status)


def regime_content(rg: dict) -> dict:
    return {k: rg.get(k) for k in ("classes", "rules", "manual", "auto", "cycle_mode", "cycle0_start")}


def features_content(fc: dict) -> dict:
    return {k: fc.get(k) for k in ("params", "lags", "combos")}
