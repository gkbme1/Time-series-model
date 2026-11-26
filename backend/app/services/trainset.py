"""训练表：数据版本（DS）的校核样本 + 共用计算链算出的输入列 + 工况 / 周期 / 训练检验角色。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from ..core import pipeline, quality
from ..store import db
from . import context as cx
from . import spec as sp

_tcache = cx.LRU(6)


def ds_dir(ds: db.Dataset) -> Path:
    return Path(ds.config["path"])


def rules_of(s, sc, ds) -> dict:
    rv = s.scalars(select(db.RuleVersion).where(db.RuleVersion.scenario_id == sc.id,
                                                db.RuleVersion.code == ds.rule_code)).first()
    return rv.rules if rv else sc.config["rules"]


def spec_for(s, sc, ds, feats=None, rg=None) -> pipeline.Spec:
    return sp.build_spec(sc, rules_override=rules_of(s, sc, ds), tags=ds.config["tags"], feats=feats, rg=rg)


def cycle_of(ts: pd.Series, cycles: list[dict]) -> np.ndarray:
    out = np.zeros(len(ts), int)
    t = pd.to_datetime(ts).to_numpy()
    for c in cycles:
        a, b = np.datetime64(pd.Timestamp(c["start"])), np.datetime64(pd.Timestamp(c["end"]) + pd.Timedelta(minutes=1))
        out[(t >= a) & (t < b)] = c["i"]
    return out


def table(s, sc, ds, feats: dict | None = None, rg: dict | None = None) -> tuple[pd.DataFrame, list[str], pipeline.Spec]:
    """feats / rg：训练批次保存的 F-vN、WC-vN 快照；不传则用当前配置。"""
    rg = rg or sp.regime_cfg(sc)
    rules = rules_of(s, sc, ds)
    spec = spec_for(s, sc, ds, feats, rg)
    key = (ds.id, sp.h(spec.__dict__), sp.h(sp.versions_content(rg)))
    hit = _tcache.get(key)
    if hit is not None:
        return hit
    X, rt = sp.features_full(sc, spec)
    cols = [c["col"] for c in pipeline.columns(spec) if not c.get("excluded")]
    cal = pd.read_parquet(ds_dir(ds) / "calib.parquet")
    cal["t"] = pd.to_datetime(cal["t"]).dt.floor("min")
    pos = X.index.get_indexer(pd.DatetimeIndex(cal["t"]))
    cal = cal[pos >= 0].reset_index(drop=True)
    pos = pos[pos >= 0]
    lab, _ = sp.final_labels(sc, rg, rules)
    inc = sp.included_ids(sc, rg)
    df = pd.DataFrame({"t": cal["t"], "y": cal["y"].astype(float), "kind": cal.get("kind", "红外"),
                       "regime": lab[pos], "regime_rt": rt[pos]})
    df["cycle"] = cycle_of(df["t"], sp.cycles(sc, rg, rules))
    feats = X.iloc[pos][cols].reset_index(drop=True)
    df = pd.concat([df, feats], axis=1)
    df["included"] = df["regime"].isin(inc)
    df["complete"] = np.isfinite(feats.to_numpy(dtype="float64")).all(axis=1)
    return _tcache.put(key, (df, cols, spec))


def funnel(s, sc, ds, df: pd.DataFrame, rg: dict | None = None) -> list[dict]:
    f = pd.read_parquet(ds_dir(ds) / "flags.parquet")
    tags = [c for c in f.columns if c not in ("timestamp", "excluded")]
    raw = len(f)
    not_ex = ~f["excluded"].to_numpy()
    ok_q = not_ex.copy()
    for t in tags:
        ok_q &= (f[t].to_numpy() & quality.UNUSABLE) == 0
    idx = cx.index(sc.furnace)
    pos = idx.get_indexer(pd.DatetimeIndex(f["timestamp"]))
    lab, _ = sp.final_labels(sc, rg, rules_of(s, sc, ds))
    inc = np.isin(lab[pos], list(sp.included_ids(sc, rg)))
    n = int((df["included"] & df["complete"]).sum())
    return [{"label": "原始时段", "v": raw / 60, "unit": "h", "src": ""},
            {"label": "排除停炉、检修等", "v": int(not_ex.sum()) / 60, "unit": "h", "src": "选数据"},
            {"label": "数据质量剔除后", "v": int(ok_q.sum()) / 60, "unit": "h", "src": f"选数据 · 处理规则 {ds.rule_code}"},
            {"label": "只保留纳入的工况", "v": int((ok_q & inc).sum()) / 60, "unit": "h", "src": "分工况"},
            {"label": "有校核值的样本", "v": n, "unit": "条", "src": "选数据 · 校核数据"}]


def default_roles(cycles: list[dict]) -> dict[str, str]:
    full = [c["i"] for c in cycles if c["done"]]
    last = full[-1] if full else None
    return {str(c["i"]): ("skip" if not c["done"] else "test" if c["i"] == last else "train") for c in cycles}


def assign(df: pd.DataFrame, split: dict, cycles: list[dict]) -> np.ndarray:
    """每个样本的角色：train / test / skip（只看可用样本：纳入的工况、输入完整）。"""
    role = np.full(len(df), "skip", dtype=object)
    usable = (df["included"] & df["complete"]).to_numpy()
    if split.get("type") == "time":
        idx = np.flatnonzero(usable)
        idx = idx[np.argsort(df["t"].to_numpy()[idx])]
        k = int(round(len(idx) * (1 - float(split.get("frac", 0.2)))))
        role[idx[:k]] = "train"
        role[idx[k:]] = "test"
        return role
    roles = {**default_roles(cycles), **(split.get("roles") or {})}
    cyc = df["cycle"].astype(str).to_numpy()
    for i, r in roles.items():
        role[usable & (cyc == i)] = r
    return role
