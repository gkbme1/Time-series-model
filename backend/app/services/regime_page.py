""""分工况"页：工况图（降采样 + 工况色带）、汇总、自动发现、运行周期。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core import regime
from . import context as cx
from . import spec as sp

PAL = ["#1c7ed6", "#0ca678", "#d6336c", "#5c940d", "#f08c00", "#ae3ec9"]


def period(sc) -> np.ndarray:
    d = sc.config["data"]
    return cx.period_mask(cx.index(sc.furnace), d["start"], d["end"], d.get("excludes", []))


def params_available(sc) -> list[dict]:
    return [{"name": r["name"], "tag": r["tag"], "unit": r["unit"]} for r in cx.tag_rows(sc.furnace)
            if not int(r["is_target"])]


def values_by_name(sc, names: list[str]) -> dict[str, np.ndarray]:
    out = {}
    for n in names:
        t = cx.tag_by_name(sc.furnace, n)
        if t:
            out[n] = cx.quality_of(sc.furnace, t, cx.rules_for(sc.config["rules"], sc.furnace, t, sc.template_id)).value
    return out


def summary(sc) -> list[dict]:
    rg = sp.regime_cfg(sc)
    lab, src = sp.final_labels(sc)
    p = period(sc)
    n = max(1, int(p.sum()))
    out = []
    lp, sp_ = lab[p], src[p]
    for c in rg["classes"]:
        m = lp == c["id"]
        mins = int(m.sum())
        srcs = {}
        if mins:
            vals, cnt = np.unique(sp_[m], return_counts=True)
            srcs = {str(v): int(k) for v, k in zip(vals, cnt)}
        out.append({**c, "minutes": mins, "pct": round(mins / n * 100, 2), "sources": srcs})
    return out


def _bucket_labels(lab: np.ndarray, i0: int, i1: int, step: int) -> list[str]:
    seg = lab[i0:i1]
    out = []
    for k in range(0, len(seg), step):
        vals, cnt = np.unique(seg[k:k + step], return_counts=True)
        out.append(str(vals[np.argmax(cnt)]))
    return out


def _runs(ids: list[str], t: list[str], end: str) -> list[list]:
    res = []
    for i, w in enumerate(ids):
        if res and res[-1][2] == w:
            continue
        if res:
            res[-1][1] = t[i]
        res.append([t[i], None, w])
    if res:
        res[-1][1] = end
    return res


def chart(sc, names: list[str], start: str | None, end: str | None, buckets: int) -> dict:
    d = sc.config["data"]
    idx = cx.index(sc.furnace)
    lab, _ = sp.final_labels(sc)
    r0 = int(idx.searchsorted(pd.Timestamp(d["start"])))
    r1 = int(idx.searchsorted(pd.Timestamp(d["end"]) + pd.Timedelta(days=1)))
    a = pd.Timestamp(start) if start else idx[r0]
    b = pd.Timestamp(end) if end else idx[min(r1, len(idx) - 1)]
    i0, i1 = max(r0, int(idx.searchsorted(a))), min(r1, int(idx.searchsorted(b)))
    i1 = max(i1, i0 + 60)
    n = i1 - i0
    step = max(1, int(np.ceil(n / buckets)))
    t = [x.strftime("%Y-%m-%d %H:%M") for x in idx[i0:i1:step]]
    endt = idx[min(i1, len(idx) - 1)].strftime("%Y-%m-%d %H:%M")
    vals = values_by_name(sc, names)
    units = {r["name"]: r["unit"] for r in cx.tag_rows(sc.furnace)}
    series = []
    for nm in names:
        v = vals.get(nm)
        if v is None:
            continue
        seg = v[i0:i1]
        pad = int(np.ceil(n / step)) * step - n
        vv = np.concatenate([seg, np.full(pad, np.nan)]).reshape(-1, step)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mean, mn, mx = np.nanmean(vv, 1), np.nanmin(vv, 1), np.nanmax(vv, 1)
        cl = lambda arr: [None if not np.isfinite(x) else round(float(x), 4) for x in arr]
        series.append({"name": nm, "unit": units.get(nm, ""), "mean": cl(mean), "min": cl(mn), "max": cl(mx)})
    ostep = max(1, int(np.ceil((r1 - r0) / 500)))
    ot = [x.strftime("%Y-%m-%d %H:%M") for x in idx[r0:r1:ostep]]
    return {
        "t": t, "step_minutes": step, "start": t[0] if t else "", "end": endt, "series": series,
        "runs": _runs(_bucket_labels(lab, i0, i1, step), t, endt),
        "overview": {"start": ot[0], "end": idx[min(r1, len(idx) - 1)].strftime("%Y-%m-%d %H:%M"),
                     "runs": _runs(_bucket_labels(lab, r0, r1, ostep), ot, idx[min(r1, len(idx) - 1)].strftime("%Y-%m-%d %H:%M"))},
        "excludes": d.get("excludes", []),
    }


def run_auto(sc, keys: list[str], sens: str) -> list[dict]:
    rg = sp.regime_cfg(sc)
    idx = cx.index(sc.furnace)
    vals = values_by_name(sc, keys)
    units = {r["name"]: r["unit"] for r in cx.tag_rows(sc.furnace)}
    found = regime.auto_discover(vals, units, idx, period(sc), sens, sp.rule_labels(sc), {k: k for k in keys})
    keep = [c for c in rg["auto"] if c.get("st") in ("ok", "no")]
    out = list(keep)
    for c in found:
        if any(c["a"] < k["b"] and k["a"] < c["b"] for k in keep):
            continue
        if any(c["a"] < m["b"] and m["a"] < c["b"] for m in rg["manual"]):
            c["st"] = "man"
        out.append(c)
    out.sort(key=lambda x: x["a"])
    return out


def new_class(rg: dict, name: str) -> str:
    for c in rg["classes"]:
        if c["name"] == name:
            return c["id"]
    k = 1
    while f"w{k}" in {c["id"] for c in rg["classes"]}:
        k += 1
    cid = f"w{k}"
    rg["classes"].append({"id": cid, "name": name, "color": PAL[(k - 1) % len(PAL)], "include": False})
    return cid


def info_line(sc) -> str:
    rg = sp.regime_cfg(sc)
    inc = [c["name"] for c in rg["classes"] if c.get("include")]
    return f"{len(rg['classes'])} 种工况 · 纳入 {'、'.join(inc) or '—'}"
