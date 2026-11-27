""""选数据"页的真实计算：已选参数完整率、数据质量诊断、影响汇总、训练用校核数据、预览降采样。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core import calib, quality
from . import context as cx

EXCLUDE_REGIMES = ("burn", "stop")


def _ctx(scn):
    cfg = scn.config
    d = cfg["data"]
    idx = cx.index(scn.furnace)
    period = cx.period_mask(idx, d["start"], d["end"], d.get("excludes", []))
    return cfg, d, idx, period


def labels_offline(scn) -> np.ndarray:
    cfg = scn.config
    return cx.regime_labels(scn.furnace, cx.regime_rules(cfg, scn.template_id), cfg["rules"], scn.template_id)


def tag_quality(scn, tag: str, labels=None) -> quality.QualityResult:
    rules = cx.rules_for(scn.config["rules"], scn.furnace, tag, scn.template_id)
    res = cx.quality_of(scn.furnace, tag, rules)
    if rules.get("decoke", {}).get("on"):
        labels = labels if labels is not None else labels_offline(scn)
        res = cx.with_decoke(res, rules, labels == "burn")
    return res


def calib_exclude_fn(scn, idx, period, labels):
    crules = scn.config["rules"].get("__calib__", {})
    bad = ~period
    if crules.get("decoke", {}).get("on", True):
        bad = bad | np.isin(labels, EXCLUDE_REGIMES)

    def fn(ts: pd.DatetimeIndex):
        pos = idx.get_indexer(ts)
        out = np.ones(len(ts), bool)
        ok = pos >= 0
        out[ok] = bad[pos[ok]]
        return out
    return fn


def calib_table(scn, imports, labels, idx, period):
    cfg = scn.config
    d = cfg["data"]
    pick = cfg["target"].get("pick", "avg")
    crules = cfg["rules"].get("__calib__", calib.calib_rules_default(cfg["target"]))
    exfn = calib_exclude_fn(scn, idx, period, labels)
    use = d.get("calib_use", {})
    rows, samples = [], []
    for s in cx.calib_sources(scn.furnace, imports, scn.id):
        on = bool(use.get(s["id"], s["default_use"]))
        training = not (s["kind"] == "热电偶" and pick == "avg")
        df = cx.source_samples(scn.furnace, s, crules, pick, exfn)
        if s["kind"] == "热电偶":
            raw = cx.src().read(scn.furnace, [s["tag"]])[s["tag"]].dropna()
            cov = (raw.index[0], raw.index[-1]) if len(raw) else (None, None)
            pos = s["position"]
        else:
            rec = cx.source_records(s)
            cov = (rec["measured_at"].min(), rec["measured_at"].max()) if len(rec) else (None, None)
            tubes = sorted({t for t in rec["position"].map(calib.tube_no).dropna().astype(int)})
            pos = (f"出口段 {tubes[0]}#–{tubes[-1]}# 管" if len(tubes) > 1 else f"出口段 {tubes[0]}# 管") if tubes else \
                "、".join(sorted(rec["position"].unique())[:3])
        note = s["note"]
        if s["kind"] == "热电偶" and pick == "avg":
            note += "；取法为按时刻平均时只作时滞分析和趋势参考，不作为训练样本"
        rows.append({"id": s["id"], "use": on, "name": s["name"], "kind": s["kind"], "status": s["status"],
                     "from": _d(cov[0]), "to": _d(cov[1]), "available": int(len(df)), "position": pos,
                     "note": note, "training": training, "import_id": s.get("import_id")})
        if on and training and len(df):
            samples.append(df.assign(source=s["id"], kind=s["kind"]))
    sm = pd.concat(samples, ignore_index=True).sort_values("t") if samples else \
        pd.DataFrame(columns=["t", "y", "n", "source", "kind"])
    return rows, sm


def _d(t):
    return None if t is None or pd.isna(t) else pd.Timestamp(t).strftime("%Y-%m-%d")


def summary(scn, imports) -> dict:
    cfg, d, idx, period = _ctx(scn)
    labels = labels_offline(scn)
    tags = d.get("tags", [])
    rows = {r["tag"]: r for r in cx.tag_rows(scn.furnace)}
    sel, qrows, unusable = [], [], {}
    for t in tags:
        r = rows[t]
        res = tag_quality(scn, t, labels)
        dg = quality.diagnose(res, period)
        sel.append({"tag": t, "name": r["name"], "device": r["device"], "part": r["part"], "desc": r["desc"],
                    "unit": r["unit"], "period": r["period"], "completeness": dg["completeness"]})
        qrows.append({"tag": t, "name": r["name"], **dg})
        unusable[t] = (res.flags[period] & quality.UNUSABLE) > 0

    # 影响汇总：至少一个参数无效的时间点比例，以及去掉某个参数能多保留多少
    impact = {"lost_pct": 0.0, "top": [], "suggest": None}
    if tags and period.any():
        M = np.vstack([unusable[t] for t in tags])
        lost = M.any(axis=0)
        impact["lost_pct"] = round(lost.mean() * 100, 1)
        indiv = sorted(((rows[t]["name"], M[i].mean() * 100) for i, t in enumerate(tags)), key=lambda x: -x[1])
        impact["top"] = [{"name": n, "pct": round(p, 1)} for n, p in indiv if p >= 0.5][:3]
        best = None
        for i, t in enumerate(tags):
            if len(tags) < 2:
                break
            rest = np.delete(M, i, axis=0).any(axis=0).mean() * 100
            gain = impact["lost_pct"] - rest
            if best is None or gain > best[1]:
                best = (rows[t]["name"], gain)
        if best and best[1] >= 2.0:
            impact["suggest"] = {"name": best[0], "gain_pct": round(best[1], 1)}

    crow, samples = calib_table(scn, imports, labels, idx, period)
    tc_n = sum(r["available"] for r in crow if r["use"] and r["kind"] == "热电偶")
    ir_n = sum(r["available"] for r in crow if r["use"] and r["kind"] != "热电偶")
    total = int(len(samples))
    names = {c["id"]: c["name"] for c in cx.regime_classes(cfg, scn.template_id)}
    cov = []
    if total:
        pos = idx.get_indexer(pd.DatetimeIndex(samples["t"]))
        lab = pd.Series(labels[pos]).value_counts()
        for rid in ["norm", "hi"] + [k for k in lab.index if k not in ("norm", "hi")]:
            if rid in lab.index or rid in ("norm", "hi"):
                cov.append({"id": rid, "name": names.get(rid, "正常稳定" if rid == "norm" else rid),
                            "n": int(lab.get(rid, 0))})
    eff_days = round(period.sum() / 1440, 1)
    return {
        "selected": sel, "quality": qrows, "impact": impact,
        "calib": {"sources": crow, "total": total, "tc": int(tc_n), "ir": int(ir_n), "coverage": cov,
                  "pick": cfg["target"].get("pick", "avg"), "warn": total < 300},
        "gen": {"n_tags": len(tags), "start": d["start"], "end": d["end"], "eff_days": eff_days,
                "calib_points": total},
    }


def preview(scn, tags: list[str], start: str | None, end: str | None, buckets: int, show_calib: bool,
            imports) -> dict:
    cfg, d, idx, period = _ctx(scn)
    a = pd.Timestamp(start or d["start"])
    b = pd.Timestamp(end) if end else pd.Timestamp(d["end"]) + pd.Timedelta(days=1)
    i0, i1 = int(idx.searchsorted(a)), int(idx.searchsorted(b))
    i1 = max(i1, i0 + 1)
    n = i1 - i0
    step = max(1, int(np.ceil(n / max(50, buckets))))
    nb = int(np.ceil(n / step))
    t_axis = idx[i0:i1:step]
    labels = labels_offline(scn)
    rows = {r["tag"]: r for r in cx.tag_rows(scn.furnace)}
    out = {"t": [x.strftime("%Y-%m-%d %H:%M") for x in t_axis], "step_minutes": step, "series": [],
           "excludes": d.get("excludes", []), "start": a.strftime("%Y-%m-%d %H:%M"),
           "end": b.strftime("%Y-%m-%d %H:%M")}

    def agg(v):
        pad = nb * step - len(v)
        vv = np.concatenate([v, np.full(pad, np.nan)]).reshape(nb, step)
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                return np.nanmean(vv, 1), np.nanmin(vv, 1), np.nanmax(vv, 1)

    for t in tags:
        r = rows.get(t)
        if r is None:
            continue
        res = tag_quality(scn, t, labels)
        raw = cx.src().read(scn.furnace, [t])[t].to_numpy()[i0:i1]
        val = res.value[i0:i1]
        f = res.flags[i0:i1]
        mean, mn, mx = agg(val)
        spans = []
        bad = ((f & quality.FROZEN) > 0) | (((f & quality.MISSING) > 0) & ((f & quality.UNUSABLE) > 0))
        for s, e in quality.runs(bad):
            if e - s < 10:
                continue
            if spans and s - spans[-1][1] <= max(30, step):
                spans[-1][1] = e
            else:
                spans.append([s, e])
        pts = np.flatnonzero((f & (quality.RANGE | quality.SPIKE)) > 0)
        if len(pts) > 400:
            pts = pts[:: int(np.ceil(len(pts) / 400))]
        out["series"].append({
            "tag": t, "name": r["name"], "unit": r["unit"],
            "mean": _clean(mean), "min": _clean(mn), "max": _clean(mx),
            "bad_spans": [[_ts(idx, i0 + s), _ts(idx, i0 + e - 1)] for s, e in spans],
            "marks": [[_ts(idx, i0 + p), None if np.isnan(raw[p]) else round(float(raw[p]), 3),
                       "超范围" if f[p] & quality.RANGE else "尖峰"] for p in pts],
        })
    if show_calib:
        imports_ = imports
        _, samples = calib_table(scn, imports_, labels, idx, period)
        s = samples[(samples["t"] >= a) & (samples["t"] < b)]
        if len(s) > 1500:
            s = s.iloc[:: int(np.ceil(len(s) / 1500))]
        out["calib"] = [[pd.Timestamp(t).strftime("%Y-%m-%d %H:%M"), round(float(y), 1), k]
                        for t, y, k in zip(s["t"], s["y"], s["kind"])]
        out["target"] = {"name": cfg["target"]["name"], "unit": cfg["target"].get("unit", "")}
    return out


def _ts(idx, i):
    return idx[min(i, len(idx) - 1)].strftime("%Y-%m-%d %H:%M")


def _clean(a):
    return [None if not np.isfinite(x) else round(float(x), 4) for x in a]
