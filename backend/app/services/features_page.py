""""加工输入"页：参数加工方式、时滞分析、组合量试算、加工结果。"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from ..core import calib, lag, pipeline, regime
from ..core import features as F
from . import context as cx
from . import regime_page
from . import spec as sp

_lagcache = cx.LRU(64)


def view(sc) -> dict:
    fc = sp.features_cfg(sc)
    rows = {r["tag"]: r for r in cx.tag_rows(sc.furnace)}
    refs = lag.load_refs(sc.furnace)
    params = []
    for t in sc.config["data"]["tags"]:
        r = rows[t]
        ref = next(({"v": lag.eq_lag(x["map"][t]["dead_time"], x["map"][t]["tau"]), "src": x["type"]}
                    for x in refs if t in x["map"]), None)
        params.append({"tag": t, "name": r["name"], "unit": r["unit"], "spec": sp.param_spec(sc, t),
                       "lag": fc["lags"].get(t), "ref": ref})
    spec = sp.build_spec(sc)
    return {"params": params, "combos": fc["combos"], "columns": pipeline.columns(spec),
            "tag_names": spec.names, "ops": F.OPS, "events": F.EVENTS, "code": fc.get("code"),
            "proxies": proxies(sc), "target_continuous": target_continuous(sc)}


# ---------------------------------------------------------------- 组合量试算
def _down(v: np.ndarray, i0: int, i1: int, buckets=600):
    seg = v[i0:i1]
    n = len(seg)
    step = max(1, int(np.ceil(n / buckets)))
    pad = int(np.ceil(n / step)) * step - n
    vv = np.concatenate([seg, np.full(pad, np.nan)]).reshape(-1, step)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = np.nanmean(vv, 1)
    return [None if not np.isfinite(x) else round(float(x), 5) for x in m], step


def combo_preview(sc, combo: dict) -> dict:
    spec = sp.build_spec(sc)
    idx = cx.index(sc.furnace)
    d = sc.config["data"]
    i0 = int(idx.searchsorted(pd.Timestamp(d["start"])))
    i1 = int(idx.searchsorted(pd.Timestamp(d["end"]) + pd.Timedelta(days=1)))
    msg = ""
    if combo["kind"] == "script":
        return {"t": [], "v": [], "msg": "Demo 版本不执行脚本。脚本已可保存，状态为\"待审核\"。", "script": True}
    if combo["kind"] == "formula":
        names = {spec.names[t]: t for t in spec.tags}
        vals = {}
        tree_names = F.parse_formula(combo.get("expr", ""))[1]
        for n in tree_names:
            if n not in names:
                raise F.ExprError(f"\"{n}\"不在\"选数据\"已选参数中")
            t = names[n]
            vals[n] = cx.quality_of(sc.furnace, t, spec.rules[t]).value
        v, bad = F.eval_formula(combo["expr"], vals, combo.get("zero", "invalid"))
        msg = (f"所选时段内 {int((~np.isfinite(v[i0:i1])).sum()):,} 个点无效（其中除数接近零或超出定义域 {bad:,} 个"
               + ("，已沿用上一个有效值" if combo.get("zero") == "hold" else "，已标为无效") + "）") if bad else "试算完成，没有除数为零的点"
    else:
        lab = sp.rule_labels(sc, causal=True)
        x = None
        if combo.get("op") in ("cum", "ema"):
            t = combo.get("param")
            if not t:
                raise F.ExprError("请选择参数")
            x = cx.quality_of(sc.furnace, t, spec.rules.get(t) or cx.rules_for({}, sc.furnace, t)).value
        tmp = pipeline.Spec(**{**spec.__dict__})
        v = pipeline.eval_combo_op(combo, lab, {combo.get("param"): x} if x is not None else {}, idx, tmp)
        msg = "试算完成"
        if combo.get("event") == "sw" and not (lab == "sw").any():
            msg = "没有\"原料切换\"的判定规则或没有判出原料切换，结果为空；请先在\"分工况\"中设置"
    vv, step = _down(v, i0, i1)
    labf, _ = sp.final_labels(sc)
    ev = []
    for a, b, w in regime.runs_of(labf[i0:i1]):
        if w in ("burn", "stop", "sw"):
            ev.append([idx[i0 + a].strftime("%Y-%m-%d %H:%M"), idx[i0 + b - 1].strftime("%Y-%m-%d %H:%M"), w])
    fin = np.isfinite(v[i0:i1])
    return {"t": [x.strftime("%Y-%m-%d %H:%M") for x in idx[i0:i1:step]], "v": vv, "step_minutes": step, "events": ev,
            "msg": msg, "valid_pct": round(float(fin.mean() * 100), 1),
            "stats": {"min": _r(np.nanmin(v[i0:i1])) if fin.any() else None,
                      "max": _r(np.nanmax(v[i0:i1])) if fin.any() else None}}


def _r(x):
    return round(float(x), 4)


# ---------------------------------------------------------------- 时滞分析
def target_continuous(sc) -> bool:
    tt = cx.target_tag(sc.furnace)
    use = sc.config["data"].get("calib_use", {})
    return bool(tt) and use.get(f"tc:{tt}", True)


def proxies(sc) -> list[dict]:
    out = []
    tt = cx.target_tag(sc.furnace)
    if tt:
        out.append({"tag": tt, "name": "炉管壁温 1# 管热电偶（连续）", "tc": True})
    for n, label in (("出口温度", "出口温度 COT"), ("炉膛温度", "炉膛温度")):
        t = cx.tag_by_name(sc.furnace, n)
        if t:
            out.append({"tag": t, "name": label, "tc": False})
    return out


def series_for(sc, tag: str, tc: bool) -> np.ndarray:
    if tc:
        cr = sc.config["rules"].get("__calib__") or calib.calib_rules_default(sc.config["target"])
        return cx.quality_of(sc.furnace, tag, calib.tc_rules(cr)).value
    return cx.quality_of(sc.furnace, tag, cx.rules_for(sc.config["rules"], sc.furnace, tag, sc.template_id)).value


def lag_analysis(sc, tag: str, ytag: str) -> dict:
    tc = ytag == cx.target_tag(sc.furnace)
    lab, _ = sp.final_labels(sc)
    inc = sp.included_ids(sc)
    key = (sc.furnace, tag, ytag, sp.h([sorted(inc), sc.config["data"], sp.regime_cfg(sc)["rules"],
                                         sp.regime_cfg(sc)["manual"], cx.rules_for(sc.config["rules"], sc.furnace, tag),
                                         sc.config["rules"].get("__calib__")]))
    hit = _lagcache.get(key)
    if hit is not None:
        return hit
    mask = regime_page.period(sc) & np.isin(lab, list(inc))
    x = series_for(sc, tag, False)
    y = series_for(sc, ytag, tc)
    res = lag.xcorr(x, y, mask)
    res["included"] = [c["name"] for c in sp.regime_cfg(sc)["classes"] if c.get("include")]
    res["weak"] = res["r_peak"] is not None and abs(res["r_peak"]) < 0.4
    return _lagcache.put(key, res)
