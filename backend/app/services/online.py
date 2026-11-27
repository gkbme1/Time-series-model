""""在线校核"与"场景概览"三个指标：校核值来源、审核、按测量时刻与预测值配对、补算、指标与明细。

校核值来源（都受回放时钟约束，不会出现"未来"的校核值）：
- 1# 管热电偶：连续测点随时钟流入，取整点值；失效时段标"质量异常"。目标为出口段平均时，
  先扣除训练期估计出的"1# 管 − 出口段平均"偏差，再用于评价。
- 巡检红外测温：巡检系统同步，按记录的录入时刻到达；同一次巡检按时刻取平均（≥ 4 根管）。
- 手工录入 / 导入：进入"待审核"，审核通过后才参与偏差计算。
配对一律按测量时刻（取整到分钟）；该时刻还没有估计值（例如实例创建之前）时，用同一条计算链补算。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select

from ..core import calib
from ..core import models as M
from ..store import db
from . import context as cx
from . import runtime as RT
from .replay import clock, replay_source

_offset_cache: dict = {}


def online_instance(s, sid: int, furnace: str | None = None):
    q = select(db.Instance).where(db.Instance.scenario_id == sid, db.Instance.mode == "online")
    if furnace:
        q = q.where(db.Instance.furnace == furnace)
    return s.scalars(q.order_by(db.Instance.id)).first()


def tc_offset(s, sc) -> float:
    """训练期（数据版本时段）内 1# 管热电偶整点值与同一次巡检平均值的平均差。"""
    key = (sc.id, sc.config["data"]["start"], sc.config["data"]["end"])
    if key in _offset_cache:
        return _offset_cache[key]
    tt = cx.target_tag(sc.furnace)
    crules = sc.config["rules"].get("__calib__") or calib.calib_rules_default(sc.config["target"])
    raw = cx.src().read(sc.furnace, [tt], sc.config["data"]["start"], sc.config["data"]["end"] + " 23:59")[tt]
    tc = calib.tc_samples(raw.to_numpy(), raw.index, crules).set_index("t")["y"]
    diffs = []
    for s_ in cx.calib_sources(sc.furnace, []):
        if s_["kind"] == "热电偶" or not s_["default_use"]:
            continue
        ses = calib.sessions(calib.ir_valid(cx.source_records(s_), crules), "avg")
        for t, y in zip(ses["t"], ses["y"]):
            h = pd.Timestamp(t).round("h")
            if h in tc.index:
                diffs.append(tc[h] - y)
    v = float(np.median(diffs)) if diffs else 0.0
    _offset_cache[key] = round(v, 2)
    return _offset_cache[key]


def records(s, sc, t0: pd.Timestamp, t1: pd.Timestamp) -> pd.DataFrame:
    """[t0, t1] 内、且已经到达（不晚于回放时钟）的校核记录。列：t, y, position, source, status, note, entered, eval, kind"""
    now = clock.now_min()
    t1 = min(t1, now)
    out = []
    pick = sc.config["target"].get("pick", "avg")
    crules = sc.config["rules"].get("__calib__") or calib.calib_rules_default(sc.config["target"])
    # 1# 管热电偶
    tt = cx.target_tag(sc.furnace)
    if tt and t1 > t0:
        raw = replay_source().read(sc.furnace, [tt], t0 - pd.Timedelta(minutes=60), t1)[tt]
        res = calib.quality.apply_rules(raw.to_numpy(), calib.tc_rules(crules))
        idx = raw.index
        on_h = (idx.minute == 0) & (idx >= t0)
        off = tc_offset(s, sc) if pick == "avg" else 0.0
        for i in np.flatnonzero(on_h):
            ok = res.usable[i]
            v = raw.iloc[i]
            out.append({"t": idx[i], "y": round(float(res.value[i] - off), 1) if ok else (None if pd.isna(v) else round(float(v), 1)),
                        "raw": None if pd.isna(v) else round(float(v), 1), "position": "出口段 1# 管",
                        "source": f"{tt} 热电偶", "status": "有效" if ok else "质量异常",
                        "note": (f"已扣除 1# 管偏差 {off:+.1f} ℃" if ok and off else "") if ok else
                        ("读数缺失" if pd.isna(v) else "热电偶失效（超范围 / 冻结 / 跳变）"),
                        "entered": idx[i], "eval": bool(ok), "kind": "热电偶"})
    # 巡检红外测温（按录入时刻到达）
    for m in calib.manifest():
        if m["furnace"] != sc.furnace or m.get("role") != "online" or not m.get("file"):
            continue
        rec = calib.file_records(m["file"])
        rec = rec[(rec["measured_at"] >= t0) & (rec["measured_at"] <= t1) & (rec["entered_at"] <= now)]
        if rec.empty:
            continue
        valid = calib.ir_valid(rec, crules)
        ses = calib.sessions(valid, pick)
        for _, r in ses.iterrows():
            out.append({"t": pd.Timestamp(r["t"]), "y": round(float(r["y"]), 1), "raw": None,
                        "position": f"出口段 {int(r['n'])} 根管平均" if pick == "avg" else "出口段 1# 管",
                        "source": "巡检红外测温", "status": "有效", "note": "巡检系统同步",
                        "entered": rec[rec["measured_at"] >= pd.Timestamp(r["t"]) - pd.Timedelta(minutes=30)]["entered_at"].min(),
                        "eval": True, "kind": "红外"})
    # 手工录入 / 导入
    for r in s.scalars(select(db.CalibRecord).where(db.CalibRecord.scenario_id == sc.id)):
        t = pd.Timestamp(r.measured_at)
        if t0 <= t <= t1:
            out.append({"t": t, "y": r.value, "raw": None, "position": r.position, "source": r.source,
                        "status": r.status, "note": f"{r.instrument} · {r.operator}", "entered": pd.Timestamp(r.entered_at or r.measured_at),
                        "eval": r.status == "有效", "kind": "手工", "id": r.id})
    df = pd.DataFrame(out)
    if df.empty:
        return pd.DataFrame(columns=["t", "y", "raw", "position", "source", "status", "note", "entered", "eval", "kind", "id"])
    return df.sort_values("t").reset_index(drop=True)


def outputs(s, inst_id: int, t0: pd.Timestamp, t1: pd.Timestamp) -> pd.DataFrame:
    q = select(db.Output).where(db.Output.instance_id == inst_id, db.Output.t >= f"{t0:%Y-%m-%d %H:%M}",
                                db.Output.t <= f"{t1:%Y-%m-%d %H:%M}").order_by(db.Output.t)
    rows = [{"t": o.t, "value": o.value, "p10": o.p10, "p90": o.p90, "applicable": o.applicable, "reason_cat": o.reason_cat,
             "reason": o.reason, "quality": o.quality, "regime": o.regime, "latency_ms": o.latency_ms, "backfill": o.backfill,
             "inputs": o.inputs} for o in s.scalars(q)]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["t"] = pd.to_datetime(df["t"])
    return df


def pairs(s, sc, inst: db.Instance | None, t0, t1, backfill=True) -> pd.DataFrame:
    """校核记录 + 同一测量时刻的估计值。缺少估计值时补算（只对已到达的校核值）。"""
    rec = records(s, sc, t0, t1)
    if rec.empty or inst is None:
        for c in ("p", "applicable", "reason", "dev"):
            rec[c] = None
        return rec
    ts = pd.DatetimeIndex(rec["t"]).floor("min")
    out = outputs(s, inst.id, ts.min(), ts.max())
    have = set(out["t"]) if not out.empty else set()
    miss = sorted({t for t in ts if t not in have and t >= pd.Timestamp("2026-01-08")})
    if backfill and miss:
        RT.backfill(s, inst, miss)
        s.flush()
        out = outputs(s, inst.id, ts.min(), ts.max())
    o = out.set_index("t") if not out.empty else pd.DataFrame(columns=["value", "applicable", "reason", "backfill"])
    rec["p"] = [o["value"].get(t) if t in o.index else None for t in ts]
    rec["applicable"] = [bool(o["applicable"].get(t)) if t in o.index else None for t in ts]
    rec["reason"] = [o["reason"].get(t) if t in o.index else "" for t in ts]
    rec["backfill"] = [bool(o["backfill"].get(t)) if t in o.index else False for t in ts]
    rec["dev"] = [None if (y is None or p is None or pd.isna(p) or pd.isna(y)) else round(float(p) - float(y), 2)
                  for y, p in zip(rec["y"], rec["p"])]
    return rec


# ----------------------------------------------------------------------------- 概览三个指标
def window(days=7):
    now = clock.now_min()
    return now - pd.Timedelta(days=days), now


MERGE_GAP_MIN = 15


def episodes(out: pd.DataFrame) -> list[dict]:
    """不适用的时段。逐分钟的判定不变（不适用的每一分钟都不输出）；统计和展示时，把间隔不超过
    15 分钟的不适用分钟合并成一次，原因取其中最多的一类，"持续"为其中实际不输出的分钟数。"""
    if out.empty:
        return []
    bad = ~out["applicable"].to_numpy()
    idx = np.flatnonzero(bad)
    if not len(idx):
        return []
    t = out["t"].to_numpy()
    groups, cur = [], [idx[0]]
    for k in idx[1:]:
        if (t[k] - t[cur[-1]]) <= np.timedelta64(MERGE_GAP_MIN, "m"):
            cur.append(k)
        else:
            groups.append(cur)
            cur = [k]
    groups.append(cur)
    ev = []
    for g in groups:
        sub = out.iloc[g]
        cat = sub["reason_cat"].value_counts().index[0]
        det = sub[sub["reason_cat"] == cat]["reason"].value_counts()
        ev.append({"start": f"{pd.Timestamp(t[g[0]]):%Y-%m-%d %H:%M}",
                   "end": f"{pd.Timestamp(t[g[-1]]) + pd.Timedelta(minutes=1):%Y-%m-%d %H:%M}",
                   "minutes": int(len(g)), "span": int((t[g[-1]] - t[g[0]]) / np.timedelta64(1, "m")) + 1,
                   "cat": str(cat), "detail": str(det.index[0]) if len(det) else ""})
    return ev


def overview(s, sc, big_thr: float = 12) -> dict:
    inst = online_instance(s, sc.id, sc.furnace)
    t0, t1 = window()
    res = {"clock": f"{clock.now():%Y-%m-%d %H:%M}", "window": [f"{t0:%Y-%m-%d %H:%M}", f"{t1:%Y-%m-%d %H:%M}"], "instance": None}
    if inst is None:
        return res
    c = s.get(db.Candidate, inst.cand_id)
    pr = pairs(s, sc, inst, max(t0, pd.Timestamp(inst.created_sim)), t1)
    ok = pr[(pr["eval"] == True) & (pr["applicable"] == True) & pr["dev"].notna()] if not pr.empty else pr  # noqa: E712
    out = outputs(s, inst.id, t0, t1)
    ev = episodes(out)
    share = float(out["applicable"].mean() * 100) if not out.empty else None
    big = ok[ok["dev"].abs() > big_thr] if not ok.empty else ok
    res.update({
        "instance": {"id": inst.id, "furnace": inst.furnace, "model": inst.model_code, "created": inst.created_sim},
        "val_mae": (c.report or {}).get("rep", {}).get("mae"),
        "mae": round(float(ok["dev"].abs().mean()), 2) if len(ok) else None, "n_pairs": int(len(ok)),
        "share": round(share, 1) if share is not None else None, "minutes": int(len(out)),
        "n_events": len(ev), "bad_minutes": int(sum(e["minutes"] for e in ev)),
        "big": int(len(big)), "big_thr": big_thr,
    })
    return res


def mae_detail(s, sc) -> dict:
    inst = online_instance(s, sc.id, sc.furnace)
    t0, t1 = window()
    if inst is None:
        return {"rows": []}
    t0 = max(t0, pd.Timestamp(inst.created_sim))
    out = outputs(s, inst.id, t0, t1)
    pr = pairs(s, sc, inst, t0, t1)
    c = s.get(db.Candidate, inst.cand_id)
    curve = []
    if not out.empty:
        o = out.set_index("t").resample("10min").agg({"value": "mean", "p10": "mean", "p90": "mean"})
        curve = [[f"{t:%Y-%m-%d %H:%M}", _n(r.value), _n(r.p10), _n(r.p90)] for t, r in o.iterrows()]
    rows = []
    for _, r in pr.iterrows():
        st = "正常" if r["eval"] and r["applicable"] else ("不适用，未输出" if r["eval"] and r["applicable"] is False else
                                                         f"{r['status']}，不计入")
        rows.append({"t": f"{r['t']:%Y-%m-%d %H:%M}", "p": _n(r["p"]), "y": _n(r["y"]), "dev": _n(r["dev"]), "source": r["source"],
                     "position": r["position"], "quality": st, "counted": bool(r["eval"] and r["applicable"] and r["dev"] is not None),
                     "backfill": bool(r.get("backfill"))})
    return {"curve": curve, "rows": rows, "val_mae": (c.report or {}).get("rep", {}).get("mae"), "window": [f"{t0:%Y-%m-%d}", f"{t1:%Y-%m-%d}"]}


def valid_detail(s, sc) -> dict:
    inst = online_instance(s, sc.id, sc.furnace)
    t0, t1 = window()
    if inst is None:
        return {"events": []}
    out = outputs(s, inst.id, t0, t1)
    ev = episodes(out)
    cats = {}
    for e in ev:
        k = cats.setdefault(e["cat"], {"cat": e["cat"], "n": 0, "minutes": 0, "examples": []})
        k["n"] += 1
        k["minutes"] += e["minutes"]
    # 每类原因的"例如"：按不输出的分钟数排序，取最多的 3 个具体原因（去掉阈值数字后合并）
    if not out.empty:
        bad = out[~out["applicable"]]
        key = bad["reason"].str.replace(r"（.*?）", "", regex=True)
        key = key.str.replace(r"^马氏距离.*$", "参数组合超出训练时见过的范围", regex=True).str.strip()
        for cat, g in bad.assign(k=key).groupby("reason_cat"):
            k = cats.setdefault(cat, {"cat": cat, "n": 0, "minutes": 0, "examples": []})
            k["minutes"] = int(len(g))  # 按分钟统计（事件合并只影响"次数"）
            top = g["k"].value_counts().head(3)
            k["examples"] = [f"{a} {b} min" for a, b in top.items()]
    return {"events": ev[::-1], "cats": list(cats.values()), "minutes": int(len(out)),
            "share": round(float(out["applicable"].mean() * 100), 1) if not out.empty else None,
            "window": [f"{t0:%Y-%m-%d %H:%M}", f"{t1:%Y-%m-%d %H:%M}"]}


def big_detail(s, sc, thr: float) -> dict:
    inst = online_instance(s, sc.id, sc.furnace)
    t0, t1 = window()
    if inst is None:
        return {"rows": []}
    t0 = max(t0, pd.Timestamp(inst.created_sim))
    pr = pairs(s, sc, inst, t0, t1)
    ok = pr[(pr["eval"] == True) & (pr["applicable"] == True) & pr["dev"].notna()]  # noqa: E712
    marks = {x.t: x.status for x in s.scalars(select(db.Sample).where(db.Sample.scenario_id == sc.id, db.Sample.kind == "较大偏差"))}
    pts = [{"t": f"{r['t']:%Y-%m-%d %H:%M}", "p": _n(r["p"]), "y": _n(r["y"]), "dev": _n(r["dev"]), "source": r["source"],
            "big": abs(r["dev"]) > thr, "mark": marks.get(f"{r['t']:%Y-%m-%d %H:%M}", "待处理")} for _, r in ok.iterrows()]
    out = outputs(s, inst.id, t0, t1)
    return {"points": pts, "thr": thr, "n": len(pts), "big": sum(p["big"] for p in pts),
            "max": max([abs(p["dev"]) for p in pts], default=0), "episodes": episodes(out),
            "window": [f"{t0:%Y-%m-%d %H:%M}", f"{t1:%Y-%m-%d %H:%M}"]}


def _n(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) or pd.isna(x) else round(float(x), 2)


def model_detail(s, sc) -> dict | None:
    cs = list(s.scalars(select(db.Candidate).where(db.Candidate.scenario_id == sc.id).order_by(db.Candidate.id.desc())))
    pub = next((c for c in cs if c.life == "已发布"), None)
    if pub is None:
        return None
    pkg, _ = RT.load_package(pub)
    insts = list(s.scalars(select(db.Instance).where(db.Instance.scenario_id == sc.id).order_by(db.Instance.id)))
    hist = []
    for c in cs:
        if c.life not in ("已发布", "已归档"):
            continue
        ins = [i for i in insts if i.cand_id == c.id and i.mode == "online" and i.furnace == sc.furnace]
        mae = None
        if ins:
            t0, t1 = window(3650)
            pr = pairs(s, sc, ins[0], max(t0, pd.Timestamp(ins[0].created_sim)), t1, backfill=False)
            ok = pr[(pr["eval"] == True) & (pr["applicable"] == True) & pr["dev"].notna()] if not pr.empty else pr  # noqa: E712
            mae = round(float(ok["dev"].abs().mean()), 2) if len(ok) else None
        hist.append({"code": c.code, "life": c.life, "published": f"{c.published_at:%Y-%m-%d %H:%M}" if c.published_at else "",
                     "online_mae": mae, "note": next((h["note"] for h in c.history[::-1] if h["life"] == "已归档"), "—")})
    a = pkg["applicability"]
    return {"code": pub.code, "algo": M.ALGS.get(pkg["algo"]), "mode": M.MODES.get(pkg["mode"]), "val_mae": pkg["report"]["rep"]["mae"],
            "published": pkg["published_at"], "experiment": pkg["experiment"], "batch": pkg["batch"], "dataset": pkg["dataset"],
            "rule_code": pkg["rule_code"], "wc_code": pkg["wc_code"], "f_code": pkg["f_code"], "report_code": pkg["report_code"],
            "approvers": pkg["approvers"], "n_inputs": len([1 for _ in pkg["tags"]]),
            "appl": [f"{v['name']} {v['lo']}–{v['hi']}" for v in a["vars"][:4]],
            "regimes": [r["name"] for r in a["regimes"] if r.get("include")],
            "instances": [{"id": i.id, "furnace": i.furnace, "model": i.model_code, "mode": i.mode, "period": i.period_s,
                           "created": i.created_sim} for i in insts],
            "history": hist}
