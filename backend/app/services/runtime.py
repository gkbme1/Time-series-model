"""上线运行：按实例周期推理，写入输出表。

每个周期（默认 60 秒模拟时间）：
  取输入（ReplayDataSource，最多读到回放时钟）→ 数据质量检查 → 计算加工输入
  → 适用性判断（不适用时不输出，记录原因）→ 推理 → 写输出表（值、P10、P90、适用性、输入质量、耗时）

计算链与训练完全相同：core/pipeline.build()，只是输入是"当前时刻往前一段历史"。
模型、特征定义、处理规则、工况方案、适用范围都取自发布包（package.json），与训练时一致。
"""
from __future__ import annotations

import json
import pickle
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from ..core import applicability as AP
from ..core import calib, pipeline, regime
from ..core import features as F
from ..store import db
from . import context as cx
from .replay import clock, replay_source

BASE_LOOKBACK_MIN = 7 * 1440
_pkg_cache: dict[str, tuple] = {}
_run_lock = threading.Lock()
_busy: set[int] = set()


# ----------------------------------------------------------------------------- 发布包
def load_package(c: db.Candidate):
    if c.package not in _pkg_cache:
        d = Path(c.package)
        pkg = json.loads((d / "package.json").read_text("utf-8"))
        with open(d / "model.pkl", "rb") as f:
            model = pickle.load(f)
        _pkg_cache[c.package] = (pkg, model)
    return _pkg_cache[c.package]


def needed_names(pkg: dict) -> list[str]:
    """模型需要的参数名：输入参数 + 工况判定参数 + 组合量参数。"""
    src_rows = {r["tag"]: r["name"] for r in cx.tag_rows(pkg["furnace"])}
    out = [src_rows[t] for t in pkg["tags"]]
    for p in regime.needed_params(pkg["regime"]["rules"]):
        if p not in out:
            out.append(p)
    for c in pkg["features"]["combos"]:
        if c.get("on") and c.get("param") and src_rows.get(c["param"]) not in out:
            out.append(src_rows[c["param"]])
    return out


def map_tags(pkg: dict, furnace: str) -> tuple[dict[str, str], list[dict]]:
    """按参数名把模型位号映射到目标炉的位号。返回 (映射, 映射表)。"""
    src = {r["name"]: r["tag"] for r in cx.tag_rows(pkg["furnace"])}
    dst = {r["name"]: r["tag"] for r in cx.tag_rows(furnace)}
    m, rows = {}, []
    for n in needed_names(pkg):
        s_tag, d_tag = src.get(n), dst.get(n)
        rows.append({"name": n, "src": s_tag, "dst": d_tag, "ok": bool(d_tag), "input": s_tag in pkg["tags"]})
        if s_tag and d_tag:
            m[s_tag] = d_tag
    return m, rows


def spec_from_package(pkg: dict, furnace: str, tag_map: dict[str, str], anchor: str | None) -> pipeline.Spec:
    rows = cx.tag_rows(furnace)
    names = {r["tag"]: r["name"] for r in rows}
    units = {r["tag"]: r["unit"] for r in rows}
    rules = {}
    for r in rows:
        if int(r["is_target"]):
            continue
        src = next((s for s, d in tag_map.items() if d == r["tag"]), None)
        rules[r["tag"]] = pkg["rules"].get(src) if src and src in pkg["rules"] else cx.rules_for({}, furnace, r["tag"])
    fe = pkg["features"]
    combos = []
    for c in fe["combos"]:
        c = dict(c)
        if c.get("param"):
            c["param"] = tag_map.get(c["param"], c["param"])
        combos.append(c)
    return pipeline.Spec(
        tags=[tag_map[t] for t in pkg["tags"]], names=names, units=units, rules=rules,
        params={tag_map[t]: v for t, v in fe["params"].items() if t in tag_map},
        lags={tag_map[t]: v for t, v in fe["lags"].items() if t in tag_map}, combos=combos,
        regime_rules=pkg["regime"]["rules"], cycle_mode=pkg["regime"].get("cycle_mode", "both"), cycle_anchor=anchor)


def lookback_min(spec: pipeline.Spec) -> int:
    lb = BASE_LOOKBACK_MIN
    for c in spec.combos:
        if c.get("on") and c.get("op") == "ema":
            lb = max(lb, int(float(c.get("tau", 3)) * 5 * 1440))  # 指数平滑取 5 倍时间常数的历史
    return lb


def op_events(spec: pipeline.Spec) -> set[str]:
    """组合量用到的事件（周期起点总是需要，用于适用范围的"重新投料后"规则）。"""
    return {"cycle"} | {c.get("event", "cycle") for c in spec.combos if c.get("on") and c.get("kind") == "op" and c.get("op") != "ema"}


def last_ends(labels: np.ndarray, index: pd.DatetimeIndex, spec: pipeline.Spec, events) -> dict[str, str]:
    """每类事件最近一次结束的时刻（事件结束后的第一分钟）。"""
    out = {}
    for ev in events:
        m = F.event_marks(labels, ev, spec.cycle_mode)
        ends = np.flatnonzero(m[:-1] & ~m[1:]) + 1
        if len(ends):
            out[ev] = f"{index[ends[-1]]:%Y-%m-%d %H:%M}"
    return out


def find_anchors(furnace: str, spec: pipeline.Spec, before: pd.Timestamp, default_cycle: str | None) -> dict[str, str]:
    """before 之前各类事件最近一次结束的时刻。用实时判定的工况，只读 before 之前的数据。"""
    src = replay_source()
    tags = [t for t in pipeline.required_tags(spec) if spec.names[t] in regime.needed_params(spec.regime_rules)]
    raw = src.read(furnace, tags, None, before)
    vals = {spec.names[t]: pipeline.default_quality(t, raw[t].to_numpy(), spec.rules.get(t, {})) for t in tags}
    lab = regime.evaluate(spec.regime_rules, vals, len(raw), causal=True)
    out = last_ends(lab, raw.index, spec, op_events(spec))
    if "cycle" not in out and default_cycle:
        out["cycle"] = default_cycle
    return out


def find_anchor(furnace, spec, before, default):
    return find_anchors(furnace, spec, before, default).get("cycle")


# ----------------------------------------------------------------------------- 推理
def window_build(spec: pipeline.Spec, furnace: str, times: list[pd.Timestamp], anchors: dict | None):
    """上线时的取数与计算：取"最早时刻往前一段历史"到最晚时刻的原始数据（经 ReplayDataSource，
    不能晚于回放时钟），调用与训练相同的 pipeline.build()。
    anchors：各类事件在窗口之前最近一次结束的时刻（实例创建时求一次，运行中不断更新）。
    返回 (输入列, 实时工况, 原始数据, 窗口起点之前的锚点)。"""
    anchors = dict(anchors or {})
    t_last = max(times)
    start = min(times) - pd.Timedelta(minutes=lookback_min(spec))
    for c in spec.combos:  # 累计量需要从该事件上次结束起的全部历史
        if c.get("on") and c.get("op") == "cum" and anchors.get(c.get("event", "cycle")):
            start = min(start, pd.Timestamp(anchors[c.get("event", "cycle")]))
    # 锚点早于窗口起点时作为"窗口之前的事件结束时刻"传入；在窗口内时，事件会在窗口里被识别出来
    before = {k: v for k, v in anchors.items() if v and pd.Timestamp(v) <= start}
    sp_ = pipeline.Spec(**{**spec.__dict__, "cycle_anchor": before.get("cycle"),
                           "event_anchors": {k: v for k, v in before.items() if k != "cycle"}})
    raw = replay_source().read(furnace, pipeline.required_tags(spec), start, t_last)
    X, rt = pipeline.build(raw, sp_)
    return X, rt, raw, before


def infer(pkg, model, spec: pipeline.Spec, furnace: str, times: list[pd.Timestamp], anchors: dict | None):
    """在若干时刻上推理（这些时刻都不晚于回放时钟）。返回逐行结果和更新后的事件锚点。"""
    t0 = time.perf_counter()
    X, rt, raw, before = window_build(spec, furnace, times, anchors)
    pos = X.index.get_indexer(pd.DatetimeIndex(times))
    rows = X.iloc[pos].reset_index(drop=True)
    reg = rt[pos]
    appl = pkg["applicability"]
    aux = aux_for(appl, spec, raw, rt, pos, before.get("cycle"))
    ok, cat, det = AP.evaluate(appl, rows, reg, model.columns, aux)
    p, lo, hi = model.predict(rows, reg)
    no_out = ~np.isfinite(p) & ok
    cat[no_out], det[no_out] = "regime", "当前工况没有对应的模型"
    ok &= np.isfinite(p)
    ms = (time.perf_counter() - t0) * 1000 / max(1, len(times))
    # 输入质量：哪些参数此刻无效
    col_src = {c["col"]: c.get("tag") for c in pipeline.columns(spec) if c.get("tag")}
    qual = []
    A = rows[model.columns]
    for i in range(len(rows)):
        bad = sorted({spec.names[col_src[c]] for c in model.columns if c in col_src and not np.isfinite(A.iloc[i][c])})
        qual.append("正常" if not bad else "异常：" + "、".join(bad)[:150])
    # 更新事件锚点：窗口内各类事件最后一次结束（只取不晚于最晚推理时刻的部分）
    new_anchors = {**(anchors or {}), **last_ends(rt, raw.index, spec, op_events(spec))}
    vars_ = [v["col"] for v in appl["vars"]]
    out = []
    for i, t in enumerate(times):
        out.append({"t": f"{t:%Y-%m-%d %H:%M}", "value": _f(p[i]) if ok[i] else None, "p10": _f(lo[i]) if ok[i] else None,
                    "p90": _f(hi[i]) if ok[i] else None, "applicable": bool(ok[i]), "reason_cat": cat[i], "reason": det[i][:200],
                    "quality": qual[i], "regime": str(reg[i]), "latency_ms": max(1, int(round(ms))),
                    "inputs": {c: _f(rows.iloc[i][c]) for c in vars_}})
    return out, new_anchors


def _f(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 3)


def aux_for(appl, spec, raw, rt, pos, anchor):
    out = {}
    in_ev = F.event_marks(rt, "cycle", spec.cycle_mode)
    off = (raw.index[0] - pd.Timestamp(anchor)).total_seconds() / 60 if anchor else None
    out["since_h"] = F.eval_op("ago", in_ev, None, start_offset_min=off if off and off > 0 else None)[pos]
    for i, r in enumerate(appl["rules"]):
        if r["type"] == "rate" and r.get("tag"):
            tag = r["tag"] if r["tag"] in raw else next((t for t in raw.columns if spec.names.get(t) == cx_name(r["tag"])), None)
            if tag is None:
                continue
            x = pipeline.default_quality(tag, raw[tag].to_numpy(), spec.rules.get(tag, {}))
            m = int(r.get("minutes", 5))
            prev = np.concatenate([np.full(m, np.nan), x[:-m]])
            with np.errstate(all="ignore"):
                out[f"rate:{i}"] = (np.abs(x - prev) / np.abs(prev) * 100)[pos]
    return out


def cx_name(tag: str) -> str | None:
    for r in cx.src().tag_table().to_dict("records"):
        if r["tag"] == tag:
            return r["name"]
    return None


def instance_ctx(s, inst: db.Instance):
    c = s.get(db.Candidate, inst.cand_id)
    pkg, model = load_package(c)
    spec = spec_from_package(pkg, inst.furnace, inst.tag_map, None)
    return pkg, model, spec


def write_outputs(s, inst_id: int, rows: list[dict], shadow: bool, backfill=False):
    have = set(s.scalars(select(db.Output.t).where(db.Output.instance_id == inst_id,
                                                   db.Output.t.in_([r["t"] for r in rows]))))
    for r in rows:
        if r["t"] in have:
            continue
        s.add(db.Output(instance_id=inst_id, shadow=shadow, backfill=backfill, **r))


def step(inst_id: int, until: pd.Timestamp, cap: int = 720):
    """把实例推进到 until（模拟时刻）：补齐所有到期的周期。"""
    with _run_lock:
        if inst_id in _busy:
            return
        _busy.add(inst_id)
    try:
        with db.session() as s:
            inst = s.get(db.Instance, inst_id)
            if inst is None or inst.mode == "stopped":
                return
            st = dict(inst.state or {})
            last = pd.Timestamp(st.get("last_t") or inst.created_sim)
            per = max(1, inst.period_s // 60)
            due = list(pd.date_range(last + pd.Timedelta(minutes=per), until, freq=f"{per}min"))[:cap]
            if not due:
                return
            pkg, model, spec = instance_ctx(s, inst)
            rows, anchors = infer(pkg, model, spec, inst.furnace, due, st.get("anchors"))
            write_outputs(s, inst.id, rows, inst.mode == "shadow")
            st.update({"last_t": rows[-1]["t"], "anchors": anchors, "last": rows[-1]})
            inst.state = st
    finally:
        with _run_lock:
            _busy.discard(inst_id)


def tick():
    """回放时钟每次前进后调用：推进所有运行中的实例。"""
    now = clock.now_min()
    with db.session() as s:
        ids = list(s.scalars(select(db.Instance.id).where(db.Instance.mode != "stopped")))
    for i in ids:
        step(i, now)


def backfill(s, inst: db.Instance, times: list[pd.Timestamp]) -> list[dict]:
    """校核值晚到、或在实例创建之前的测量时刻：用同一条计算链补算该时刻的估计值。"""
    now = clock.now_min()
    times = sorted({t for t in times if t <= now})
    if not times:
        return []
    pkg, model, spec = instance_ctx(s, inst)
    home = inst.furnace == pkg["furnace"]
    anchors = find_anchors(inst.furnace, spec, min(times) - pd.Timedelta(minutes=lookback_min(spec)),
                           pkg["regime"].get("cycle0_start") if home else None)
    out = []
    for k in range(0, len(times), 200):
        rows, _ = infer(pkg, model, spec, inst.furnace, times[k:k + 200], anchors)
        write_outputs(s, inst.id, rows, inst.mode == "shadow", backfill=True)
        out += rows
    return out


# ----------------------------------------------------------------------------- 新增实例、跨炉检验
def cross_check(pkg, model, furnace: str, tag_map: dict, home_mae: float | None) -> dict:
    """用该炉最近 3 个月数据做一次跨炉检验：同一条计算链 + 该炉的校核数据（巡检红外，按时刻平均）。"""
    now = clock.now_min()
    t0 = now - pd.Timedelta(days=91)
    spec = spec_from_package(pkg, furnace, tag_map, None)
    anchors = find_anchors(furnace, spec, t0 - pd.Timedelta(minutes=lookback_min(spec)), None)
    src = [m for m in calib.manifest() if m["furnace"] == furnace and m.get("file") and m.get("role") != "online"]
    crules = pkg["rules"].get("__calib__") or calib.calib_rules_default(pkg["target"])
    recs = pd.concat([calib.file_records(m["file"]) for m in src]) if src else pd.DataFrame(columns=calib.COLS)
    recs = recs[(recs["measured_at"] >= t0) & (recs["measured_at"] <= now)]
    if "entered_at" in recs:
        recs = recs[recs["entered_at"].isna() | (recs["entered_at"] <= now)]
    ses = calib.sessions(calib.ir_valid(recs, crules), pkg["target"].get("pick", "avg"))
    if ses.empty:
        return {"ok": False, "n": 0, "msg": "该炉最近 3 个月没有可用的校核数据"}
    times = [pd.Timestamp(t) for t in ses["t"]]
    rows, _ = infer(pkg, model, spec, furnace, times, anchors)
    y = ses["y"].to_numpy(float)
    p = np.array([r["value"] if r["value"] is not None else np.nan for r in rows], float)
    k = np.isfinite(p)
    mae = float(np.mean(np.abs(p[k] - y[k]))) if k.any() else None
    bias = float(np.mean(p[k] - y[k])) if k.any() else None
    lim = max((home_mae or 5) * 1.5, (home_mae or 5) + 3)
    ok = mae is not None and mae <= lim
    return {"ok": ok, "n": int(len(y)), "n_out": int(k.sum()), "mae": round(mae, 2) if mae is not None else None,
            "bias": round(bias, 2) if bias is not None else None, "home_mae": home_mae, "limit": round(lim, 1),
            "period": [f"{t0:%Y-%m-%d}", f"{now:%Y-%m-%d}"],
            "msg": ("偏差在可接受范围内，可以先用这个模型上线（影子模式），后续积累本炉数据再训练专用模型。" if ok else
                    "偏差明显偏大，不建议直接使用，建议用这台炉的数据单独训练。")}


def create_instance(s, sc: db.Scenario, cand: db.Candidate, furnace: str, mode: str, cross: dict | None) -> db.Instance:
    if cand.life != "已发布":
        raise ValueError("只能部署已发布的模型版本")
    pkg, model = load_package(cand)
    tag_map, rows = map_tags(pkg, furnace)
    miss = [r["name"] for r in rows if not r["ok"]]
    if miss:
        raise ValueError(f"以下参数在 {furnace} 上找不到对应位号：{'、'.join(miss)}")
    home = furnace == pkg["furnace"]
    if not home:
        if not cross or not cross.get("ok"):
            raise ValueError("跨炉检验未通过或尚未检验，不能创建实例")
        mode = "shadow"
    exist = list(s.scalars(select(db.Instance).where(db.Instance.scenario_id == sc.id, db.Instance.furnace == furnace,
                                                     db.Instance.mode != "stopped")))
    if mode == "online" and any(i.mode == "online" for i in exist):
        raise ValueError("这台炉已有在线实例：新版本请先以影子模式运行，对比后再切换为在线")
    if any(i.model_code == cand.code for i in exist):
        raise ValueError(f"{cand.code} 已部署到这台炉")
    now = clock.now_min()
    spec = spec_from_package(pkg, furnace, tag_map, None)
    anchors = find_anchors(furnace, spec, now, pkg["regime"].get("cycle0_start") if home else None)
    k = int(furnace.split("-")[1]) - 100
    inst = db.Instance(scenario_id=sc.id, cand_id=cand.id, model_code=cand.code, furnace=furnace, mode=mode,
                       tag_map=tag_map, cross=cross or {}, state={"last_t": f"{now:%Y-%m-%d %H:%M}", "anchors": anchors},
                       output_tag=sc.config["target"].get("output_tag", "").replace("101", f"{k}01") if not home else
                       sc.config["target"].get("output_tag", ""), created_sim=f"{now:%Y-%m-%d %H:%M}")
    s.add(inst)
    s.flush()
    return inst
