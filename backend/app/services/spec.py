"""场景配置 → 计算用的规格：工况方案、加工输入配置的默认值，最终工况标签，整段输入列（带缓存）。"""
from __future__ import annotations

import copy
import hashlib
import json

import numpy as np
import pandas as pd

from ..core import features as F
from ..core import pipeline, regime, templates
from . import context as cx

_fcache = cx.LRU(6)
_lcache = cx.LRU(16)

DEFAULT_AUTO_KEYS = ["出口温度", "燃料气流量", "原料流量", "原料密度"]
DEFAULT_SHOW = ["出口温度", "原料流量", "燃料气流量"]


def _tpl(sc) -> dict:
    return templates.all_templates().get(sc.template_id, {})


def h(obj) -> str:
    return hashlib.md5(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- 工况方案
def regime_cfg(sc) -> dict:
    """场景的工况方案（不存在时按模板补齐默认值，并写回 config）。"""
    rg = sc.config.setdefault("regime", {})
    tpl = _tpl(sc)
    rg.setdefault("classes", copy.deepcopy(tpl.get("regime_classes") or
                                           [{"id": "norm", "name": "正常稳定", "color": "#0d7680", "include": True}]))
    rg.setdefault("rules", copy.deepcopy(tpl.get("regime_rules", [])))
    rg.setdefault("manual", [])
    rg.setdefault("auto", [])
    names = {r["name"] for r in cx.tag_rows(sc.furnace)}
    rg.setdefault("auto_keys", [n for n in DEFAULT_AUTO_KEYS if n in names])
    rg.setdefault("show", [n for n in DEFAULT_SHOW if n in names])
    rg.setdefault("sens", "mid")
    rg.setdefault("cycle_mode", "both")
    rg.setdefault("cycle0_start", None)
    return rg


def rule_labels(sc, causal=False) -> np.ndarray:
    rg = regime_cfg(sc)
    return cx.regime_labels(sc.furnace, rg["rules"], sc.config["rules"], sc.template_id, causal=causal)


def final_labels(sc) -> tuple[np.ndarray, np.ndarray]:
    """最终工况（离线，用于选训练数据和分组统计）：手动 > 已确认的自动发现 > 规则。"""
    rg = regime_cfg(sc)
    key = (sc.furnace, h([rg["rules"], [a for a in rg["auto"] if a.get("st") == "ok"], rg["manual"]]),
           h({t: cx.core_rules(r) for t, r in sc.config["rules"].items() if t != "__calib__"}))
    hit = _lcache.get(key)
    if hit is not None:
        return hit
    lab, src = regime.combine(rule_labels(sc), cx.index(sc.furnace), rg["auto"], rg["manual"])
    return _lcache.put(key, (lab, src))


def included_ids(sc) -> set[str]:
    return {c["id"] for c in regime_cfg(sc)["classes"] if c.get("include")}


def cycles(sc) -> list[dict]:
    rg = regime_cfg(sc)
    lab, _ = final_labels(sc)
    idx = cx.index(sc.furnace)
    end = pd.Timestamp(sc.config["data"]["end"]) + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)
    return regime.cycles(lab, idx, rg["cycle_mode"], rg.get("cycle0_start"), min(end, idx[-1]))


# ---------------------------------------------------------------- 加工输入配置
def features_cfg(sc) -> dict:
    fc = sc.config.setdefault("features", {})
    tpl = _tpl(sc)
    fc.setdefault("params", {})
    fc.setdefault("lags", {})
    if "combos" not in fc:
        n2t = {r["name"]: r["tag"] for r in cx.tag_rows(sc.furnace)}
        combos = []
        for c in tpl.get("combos", []):
            c = copy.deepcopy(c)
            c["src"] = "模板"
            if c.get("param") in n2t:
                c["param"] = n2t[c["param"]]
            combos.append(c)
        fc["combos"] = combos
    return fc


def param_spec(sc, tag: str) -> dict:
    fc = features_cfg(sc)
    if tag in fc["params"]:
        return fc["params"][tag]
    name = cx.tag_row(sc.furnace, tag)["name"]
    return F.default_param_spec(name, _tpl(sc).get("features_default", {}).get(name))


def build_spec(sc) -> pipeline.Spec:
    rows = cx.tag_rows(sc.furnace)
    rg = regime_cfg(sc)
    fc = features_cfg(sc)
    tags = sc.config["data"]["tags"]
    rules = {r["tag"]: cx.rules_for(sc.config["rules"], sc.furnace, r["tag"], sc.template_id)
             for r in rows if not int(r["is_target"])}
    return pipeline.Spec(
        tags=list(tags), names={r["tag"]: r["name"] for r in rows}, units={r["tag"]: r["unit"] for r in rows},
        rules=rules, params={t: param_spec(sc, t) for t in tags}, lags=fc["lags"], combos=fc["combos"],
        regime_rules=rg["rules"], cycle_mode=rg["cycle_mode"], cycle_anchor=rg.get("cycle0_start"))


def _cached_quality(furnace):
    def f(tag, raw, rules):
        return cx.quality_of(furnace, tag, rules).value
    return f


def features_full(sc) -> tuple[pd.DataFrame, np.ndarray]:
    """整段历史上的输入列（训练表从这里按校核时刻取行）。"""
    spec = build_spec(sc)
    key = (sc.furnace, h(spec.__dict__))
    hit = _fcache.get(key)
    if hit is not None:
        return hit
    tags = pipeline.required_tags(spec)
    raw = cx.src().read(sc.furnace, tags)
    return _fcache.put(key, pipeline.build(raw, spec, _cached_quality(sc.furnace)))
