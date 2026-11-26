"""项目、场景的创建与初始化（含 Demo 预置项目）。"""
from __future__ import annotations

import copy

import pandas as pd
from sqlalchemy import func, select

from ..core import templates
from ..store import db
from . import context as cx

STAGES = ["选数据", "分工况", "加工输入", "训练模型", "检验效果", "上线投用", "在线校核", "跟踪改进"]
DEFAULT_END = "2026-10-31"  # 11 月数据留给上线回放


def furnaces() -> list[dict]:
    t = cx.src().tag_table()
    out = []
    for f in t["furnace"].unique():
        dev = t[(t["furnace"] == f) & (t["device"].str.contains("裂解炉"))]["device"].iloc[0]
        a, b = cx.src().span(f)
        train_to = min(pd.Timestamp(DEFAULT_END), b.normalize())
        out.append({"unit": f, "name": dev, "from": f"{a:%Y-%m-%d}", "to": f"{b:%Y-%m-%d}",
                    "train_to": f"{train_to:%Y-%m-%d}"})
    return out


def default_target(tpl: dict, furnace: str, stype: str) -> dict:
    k = int(furnace.split("-")[1]) - 100
    if stype == "预测":
        return {"name": "炉膛出口温度", "type": "fc", "horizons": [15], "unit": "℃", "lo": 750, "hi": 900,
                "pick": "avg", "output_tag": f"TI{k}041_FC"}
    t = copy.deepcopy(tpl.get("target") or {"name": "估算目标", "type": "soft", "unit": "", "lo": 0, "hi": 100,
                                             "pick": "avg", "output_tag": ""})
    t["output_tag"] = t.get("output_tag", "").format(k=k)
    t.setdefault("horizons", [])
    return t


def new_config(template_id: str, furnace: str, stype: str) -> dict:
    tpl = templates.all_templates().get(template_id, {})
    target = default_target(tpl, furnace, stype)
    a, b = cx.src().span(furnace)
    end = DEFAULT_END if pd.Timestamp(DEFAULT_END) <= b else f"{b:%Y-%m-%d}"
    rec_names = tpl.get("recommended", [])
    tags = [r["tag"] for r in cx.tag_rows(furnace) if r["name"] in rec_names and not int(r["is_target"])]
    srcs = cx.calib_sources(furnace, [])
    return {
        "target": target,
        "data": {"tags": tags, "start": f"{a:%Y-%m-%d}", "end": end, "excludes": [],
                 "calib_use": {s["id"]: s["default_use"] for s in srcs}, "preview": tags[:3]},
        "rules": cx.default_rules(template_id, furnace, target),
    }


def next_code(s, model, scenario_id: int, prefix: str) -> str:
    n = s.scalar(select(func.count()).select_from(model).where(model.scenario_id == scenario_id)) or 0
    return f"{prefix}{n + 1}"


def create_scenario(project_id: int, name: str, template_id: str, furnace: str, stype: str) -> db.Scenario:
    with db.session() as s:
        sc = db.Scenario(project_id=project_id, name=name, type=stype, template_id=template_id, furnace=furnace,
                         config=new_config(template_id, furnace, stype))
        s.add(sc)
        s.flush()
        rv = db.RuleVersion(scenario_id=sc.id, code="R-v1", rules=copy.deepcopy(sc.config["rules"]),
                            note="按场景模板生成")
        s.add(rv)
        sc.config["rules_code"] = "R-v1"
        db.touch_json(sc)
        return sc


def seed():
    """首次启动时预置 Demo 项目和 1 号炉炉管壁温场景。"""
    with db.session() as s:
        if s.scalar(select(func.count()).select_from(db.Project)):
            return
        p = db.Project(name="乙烯装置裂解炉智能建模", device="乙烯装置 · 裂解区", opened_at=db.now())
        s.add(p)
        s.flush()
        pid = p.id
    sc = create_scenario(pid, "1号炉 炉管壁温软测量", "cracking_tmt", "BA-101", "软测量")
    with db.session() as s:
        sc = s.get(db.Scenario, sc.id)
        from .spec import regime_cfg
        # Demo 预置：上一次烧焦在数据起点之前结束（来自运行记录），第一个周期起点早于数据起点
        regime_cfg(sc)["cycle0_start"] = "2025-12-13 00:00"
        sc.config["data"]["excludes"] = [
            {"start": "2026-03-08", "end": "2026-03-21", "reason": "停炉检修"},
            {"start": "2026-06-14", "end": "2026-06-16", "reason": "DCS 升级，数据中断"},
        ]
        db.touch_json(sc)


def scenario_dict(sc: db.Scenario, with_config=True) -> dict:
    tpl = templates.all_templates().get(sc.template_id, {})
    d = {"id": sc.id, "project_id": sc.project_id, "name": sc.name, "type": sc.type, "template_id": sc.template_id,
         "template_name": tpl.get("name", sc.template_id), "furnace": sc.furnace, "stage": sc.stage,
         "status": sc.status, "model": sc.config.get("online_model") or "—",
         "target": sc.config.get("target"), "created_at": f"{sc.created_at:%Y-%m-%d %H:%M}"}
    if with_config:
        d["config"] = sc.config
    return d
