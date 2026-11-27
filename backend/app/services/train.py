""""训练模型"页：训练数据（漏斗、预览）、建模方式、划分、算法与参数、后台训练、结果对比、批次历史。"""
from __future__ import annotations

import datetime as dt
import json
import pickle
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from .. import config
from ..core import models as M
from ..core import pipeline, templates
from ..store import db
from . import jobs, trainset, versions
from . import spec as sp

DEFAULT_CFG = {"ds": None, "mode": "uni", "compare": True, "split": {"type": "cycle", "roles": {}},
               "algs": ["base", "lin", "rf", "xgb"], "pm": "def", "budget": 40, "manual": {}, "interval": True,
               "monotone": True}


def train_cfg(sc) -> dict:
    t = sc.config.setdefault("train", {})
    for k, v in DEFAULT_CFG.items():
        t.setdefault(k, json.loads(json.dumps(v)))
    return t


def latest_ds(s, sc, code=None):
    q = select(db.Dataset).where(db.Dataset.scenario_id == sc.id)
    if code:
        q = q.where(db.Dataset.code == code)
    return s.scalars(q.order_by(db.Dataset.id.desc())).first()


def monotone_cols(sc, spec: pipeline.Spec, enabled: bool) -> dict[str, int]:
    """模板中的单调约束（按参数名）落到具体输入列：当前值、滞后、均值列继承约束。"""
    if not enabled:
        return {}
    mono = templates.all_templates().get(sc.template_id, {}).get("monotone", {})
    out = {}
    for c in pipeline.columns(spec):
        if c.get("tag") and c["kind"] in ("cur", "lag", "mean"):
            sgn = mono.get(spec.names[c["tag"]])
            if sgn:
                out[c["col"]] = int(sgn)
    return out


def snapshot(sc) -> dict:
    return {"feats": versions.features_content(sp.features_cfg(sc)), "rg": versions.regime_content(sp.regime_cfg(sc))}


def signature(sc, ds_code) -> str:
    t = train_cfg(sc)
    return sp.h([ds_code, {k: t[k] for k in DEFAULT_CFG if k != "ds"}, snapshot(sc)])


# ----------------------------------------------------------------------------- 页面
def page(s, sc) -> dict:
    t = train_cfg(sc)
    dss = list(s.scalars(select(db.Dataset).where(db.Dataset.scenario_id == sc.id).order_by(db.Dataset.id.desc())))
    out = {"cfg": t, "datasets": [{"code": d.code, "start": d.config["start"], "end": d.config["end"],
                                   "rule_code": d.rule_code} for d in dss],
           "algs": M.ALGS, "modes": M.MODES, "param_meta": {k: [(a, b, c) for a, b, c, _ in v] for k, v in M.PARAM_META.items()},
           "defaults": M.DEFAULTS}
    ds = latest_ds(s, sc, t["ds"]) if dss else None
    if ds is None:
        return {**out, "empty": True, "batches": []}
    t["ds"] = ds.code
    df, cols, spec = trainset.table(s, sc, ds)
    cyc = sp.cycles(sc)
    usable = df[df["included"] & df["complete"]]
    out["funnel"] = trainset.funnel(s, sc, ds, df)
    prev = usable.sort_values("t").head(5)
    out["preview"] = {"cols": cols[:5], "n_cols": len(cols), "target": sc.config["target"]["name"],
                      "unit": sc.config["target"].get("unit", ""),
                      "rows": [{"t": f"{r.t:%Y-%m-%d %H:%M}", "x": [None if pd.isna(r[c]) else round(float(r[c]), 3) for c in cols[:5]],
                                "y": round(float(r.y), 1)} for _, r in prev.iterrows()]}
    rg = sp.regime_cfg(sc)
    names = {c["id"]: c["name"] for c in rg["classes"]}
    inc = [c["id"] for c in rg["classes"] if c.get("include")]
    per = [{"id": r, "name": names.get(r, r), "n": int((usable["regime"] == r).sum())} for r in inc]
    out["per_regime"] = per
    n = max(1, len(usable))
    min_share = min([p["n"] / n for p in per] or [1])
    out["recommend_mode"] = "main" if len(inc) < 2 else ("uni" if min_share < 0.25 else "per")
    out["min_share"] = {"name": min(per, key=lambda p: p["n"])["name"] if per else "", "pct": round(min_share * 100)}
    full = [c for c in cyc if c["done"]]
    out["can_cycle"] = len(full) >= 2
    if not out["can_cycle"] and t["split"]["type"] == "cycle":
        t["split"]["type"] = "time"
    role = trainset.assign(df, t["split"], cyc)
    roles = {**trainset.default_roles(cyc), **(t["split"].get("roles") or {})}
    out["cycles"] = [{**c, "role": roles.get(str(c["i"]), "skip"),
                      "n": int(((df["cycle"] == c["i"]) & df["included"] & df["complete"]).sum())} for c in cyc]
    counts = {r: int((role == r).sum()) for r in ("train", "test", "skip")}
    out["split_counts"] = counts
    out["test_by_regime"] = [{"name": names.get(r, r), "n": int(((role == "test") & (df["regime"] == r)).sum())} for r in inc]
    out["target_continuous"] = False
    out["n_samples"] = int(len(usable))
    spec_cols = pipeline.columns(spec)
    out["monotone"] = [{"col": k, "sign": v} for k, v in monotone_cols(sc, spec, True).items()]
    out["mono_names"] = list(templates.all_templates().get(sc.template_id, {}).get("monotone", {}).keys())
    n_exp = len(t["algs"]) * (2 if t["compare"] and t["mode"] != "main" else 1) - \
        (1 if t["compare"] and t["mode"] != "main" and "base" in t["algs"] else 0)
    out["n_exp"] = n_exp
    out["sig"] = signature(sc, ds.code)
    out["batches"] = batches(s, sc)
    return out


def exp_dict(e: db.Experiment) -> dict:
    return {"id": e.id, "code": e.code, "mode": e.mode, "algo": e.algo, "status": e.status, "progress": e.progress,
            "stage": e.stage_text, "metrics": e.metrics, "importance": e.importance, "label": e.label,
            "error": e.error, "params": e.params}


def batches(s, sc) -> list[dict]:
    out = []
    for b in s.scalars(select(db.TrainBatch).where(db.TrainBatch.scenario_id == sc.id).order_by(db.TrainBatch.id.desc())):
        exps = list(s.scalars(select(db.Experiment).where(db.Experiment.batch_id == b.id).order_by(db.Experiment.id)))
        out.append({"id": b.id, "code": b.code, "ds": b.ds_code, "rule": b.rule_code, "wc": b.wc_code, "f": b.f_code,
                    "note": b.note, "sig": b.sig, "time": f"{b.created_at:%Y-%m-%d %H:%M}", "cfg": b.config.get("train"),
                    "exps": [exp_dict(e) for e in exps], "conclusion": conclusion(exps)})
    return out


def conclusion(exps) -> list[str]:
    done = [e for e in exps if e.status == "已完成" and e.metrics]
    if not done:
        return []
    out = []
    cand = [e for e in done if e.algo != "base" and e.metrics.get("mae") == e.metrics.get("mae")]
    if not cand:
        return out
    best = min(cand, key=lambda e: e.metrics["mae"])
    out.append(f"{best.code}（{M.MODES[best.mode]} · {M.ALGS[best.algo]}）总体偏差 {best.metrics['mae']:.1f} ℃"
               + (f"，比基线好 {best.metrics['improve']:.0f}%" if best.metrics.get("improve") is not None else "") + "。")
    xg = [e for e in done if e.algo == "xgb"]
    if xg:
        x = min(xg, key=lambda e: e.metrics["mae"])
        if x.id != best.id:
            out.append(f"XGBoost 最好为 {x.metrics['mae']:.1f} ℃（比基线好 {x.metrics['improve']:.0f}%），"
                       f"不如{M.ALGS[best.algo]}，说明这套数据里输入与壁温的关系接近线性。")
    for algo in ("xgb", "lin", "rf"):
        u = next((e for e in done if e.algo == algo and e.mode == "uni"), None)
        p = next((e for e in done if e.algo == algo and e.mode == "per"), None)
        if u and p:
            hu = (u.metrics["by_regime"].get("hi") or {}).get("mae")
            hp = (p.metrics["by_regime"].get("hi") or {}).get("mae")
            if hu is not None and hp is not None:
                d = hu - hp
                tail = ("分开后明显改善，值得分工况训练。" if d > 0.8 else
                        "分开后反而更差：高负荷训练样本少，单独建模学不准，建议用统一模型。" if d < -0.8 else
                        "差别不大，建议用统一模型，更简单。")
                out.append(f"统一模型与分工况对比（{M.ALGS[algo]}）：高负荷偏差 {hu:.1f} → {hp:.1f} ℃，{tail}")
            break
    if any(e.algo == "lin" for e in done) and best.algo == "lin":
        out.append("线性回归最好时，建议同时看 XGBoost 的响应方向和适用范围，再决定发布哪一个。")
    return out


# ----------------------------------------------------------------------------- 训练
def start(s, sc) -> db.TrainBatch:
    t = train_cfg(sc)
    ds = latest_ds(s, sc, t["ds"])
    if ds is None:
        raise ValueError("请先在\"选数据\"中生成数据版本")
    if not t["algs"]:
        raise ValueError("请至少选择一个算法")
    df, cols, spec = trainset.table(s, sc, ds)
    role = trainset.assign(df, t["split"], sp.cycles(sc))
    if (role == "train").sum() < 50:
        raise ValueError("训练样本少于 50 条，请扩大时间范围或调整划分")
    if (role == "test").sum() < 10:
        raise ValueError("检验样本少于 10 条，至少需要一个检验周期")
    snap = snapshot(sc)
    wc, _ = versions.ensure(s, sc, "WC", snap["rg"], "训练时自动保存")
    fc, _ = versions.ensure(s, sc, "F", snap["feats"], "训练时自动保存")
    sc.config["regime"]["code"], sc.config["features"]["code"] = wc, fc
    n = s.scalar(select(func.count()).select_from(db.TrainBatch).where(db.TrainBatch.scenario_id == sc.id)) or 0
    modes = [t["mode"]] + ([{"uni": "per", "per": "uni"}[t["mode"]]] if t["compare"] and t["mode"] != "main" else [])
    note = f"{M.MODES[t['mode']]}{'（含对比）' if len(modes) > 1 else ''} · " + \
        {"def": "默认参数", "auto": f"自动调参 {t['budget']} 轮", "man": "手动参数"}[t["pm"]] + \
        f" · {'按运行周期' if t['split']['type'] == 'cycle' else '按时间后 20%'}划分"
    b = db.TrainBatch(scenario_id=sc.id, code=f"批次 {n + 1}", ds_code=ds.code, rule_code=ds.rule_code, wc_code=wc,
                      f_code=fc, config={"train": json.loads(json.dumps(t)), **snap}, sig=signature(sc, ds.code), note=note)
    s.add(b)
    s.flush()
    ne = s.scalar(select(func.count()).select_from(db.Experiment).where(db.Experiment.scenario_id == sc.id)) or 0
    ids = []
    for mi, m in enumerate(modes):
        for a in t["algs"]:
            if a == "lstm" or (mi > 0 and a == "base"):
                continue
            ne += 1
            e = db.Experiment(batch_id=b.id, scenario_id=sc.id, code=f"EXP-{ne:02d}", mode=m, algo=a)
            s.add(e)
            s.flush()
            ids.append(e.id)
    versions.advance(sc, "训练模型")
    db.touch_json(sc)
    s.commit()
    for i in ids:
        jobs.submit(f"exp{i}", run_experiment, i)
    return b


def _upd(eid, **kw):
    with db.session() as s:
        e = s.get(db.Experiment, eid)
        for k, v in kw.items():
            setattr(e, k, v)


def exp_dir(sc_id: int, code: str) -> Path:
    p = config.VAR_DIR / "models" / f"S{sc_id}" / code
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_context(s, b: db.TrainBatch):
    sc = s.get(db.Scenario, b.scenario_id)
    ds = latest_ds(s, sc, b.ds_code)
    df, cols, spec = trainset.table(s, sc, ds, b.config["feats"], b.config["rg"])
    rules = trainset.rules_of(s, sc, ds)
    cyc = sp.cycles(sc, b.config["rg"], rules)
    role = trainset.assign(df, b.config["train"]["split"], cyc)
    return sc, ds, df, cols, spec, role, cyc


def run_experiment(eid: int):
    t0 = dt.datetime.now()
    try:
        _upd(eid, status="训练中", progress=5, stage_text="准备训练表")
        with db.session() as s:
            e = s.get(db.Experiment, eid)
            b = s.get(db.TrainBatch, e.batch_id)
            sc, ds, df, cols, spec, role, cyc = load_context(s, b)
            tcfg = b.config["train"]
            algo, mode = e.algo, e.mode
            mono = monotone_cols(sc, spec, tcfg.get("monotone", True))
            inc = [c["id"] for c in b.config["rg"]["classes"] if c.get("include")]
            code, scid = e.code, sc.id
        tr, te = role == "train", role == "test"
        y = df["y"].to_numpy(float)
        X = df[cols].reset_index(drop=True)
        ro, rt = df["regime"].to_numpy(), df["regime_rt"].to_numpy()
        bp = M.baseline(df["t"].to_numpy()[te], df["t"].to_numpy(), y)
        if algo == "base":
            p = bp
            lo = hi = np.full(len(p), np.nan)
            mt = M.metrics(y[te], p, lo, hi, ro[te], bp)
            mt["coverage"] = None
            _save_preds(scid, code, df[te], p, lo, hi)
            _upd(eid, status="已完成", progress=100, stage_text="", metrics=mt, params={"说明": "沿用上一个校核值"},
                 finished_at=db.now())
            return
        Xtr, ytr = X[tr].reset_index(drop=True), y[tr]
        sel_reg = ro[tr] if mode == "per" else rt[tr]
        params = {}
        keys = M.train_keys(mode, sel_reg, inc)
        if tcfg["pm"] == "auto":
            for ki, (k, msk) in enumerate(keys.items()):
                _upd(eid, progress=10, stage_text=f"自动调参（{k}）")
                A = M.SoftSensorModel(algo, mode, cols, inc).design(Xtr[msk].reset_index(drop=True), sel_reg[msk])
                res = M.tune(algo, A, ytr[msk], int(tcfg["budget"]), [mono.get(c, 0) for c in cols] +
                             ([0] * len(inc) if mode == "uni" else []),
                             lambda f, ki=ki: _upd(eid, progress=int(10 + 60 * (ki + f) / len(keys))))
                params[k] = res["params"]
        elif tcfg["pm"] == "man":
            for k in keys:
                params[k] = {kk: v for kk, v in (tcfg["manual"].get(algo) or {}).items()}
        _upd(eid, progress=72, stage_text="训练模型与预测区间")
        model = M.train(algo, mode, Xtr, ytr, sel_reg, cols, inc, params, mono, tcfg.get("interval", True))
        _upd(eid, progress=88, stage_text="检验集评估、输入重要性")
        Xte = X[te].reset_index(drop=True)
        p, lo, hi = model.predict(Xte, rt[te])
        mt = M.metrics(y[te], p, lo, hi, ro[te], bp)
        if not tcfg.get("interval", True):
            mt["coverage"] = None
        imp = M.permutation_importance(model, Xte, y[te], rt[te])
        d = exp_dir(scid, code)
        with open(d / "model.pkl", "wb") as f:
            pickle.dump(model, f)
        _save_preds(scid, code, df[te], p, lo, hi)
        shown = {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in params.items()} \
            or {"all": M.DEFAULTS.get(algo, {})}
        mt["monotone"] = len(mono) > 0 and algo == "xgb"
        mt["seconds"] = round((dt.datetime.now() - t0).total_seconds(), 1)
        _upd(eid, status="已完成", progress=100, stage_text="", metrics=mt, importance=imp[:30], params=shown,
             path=str(d), finished_at=db.now())
    except Exception as ex:
        _upd(eid, status="失败", stage_text="", error=f"{ex}\n{traceback.format_exc()[-800:]}", finished_at=db.now())


def _save_preds(scid, code, sub: pd.DataFrame, p, lo, hi):
    d = exp_dir(scid, code)
    pd.DataFrame({"t": sub["t"].to_numpy(), "y": sub["y"].to_numpy(), "p": p, "lo": lo, "hi": hi,
                  "regime": sub["regime"].to_numpy(), "cycle": sub["cycle"].to_numpy()}).to_parquet(d / "test_pred.parquet", index=False)


def load_model(path: str) -> M.SoftSensorModel:
    with open(Path(path) / "model.pkl", "rb") as f:
        return pickle.load(f)
