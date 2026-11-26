""""检验效果"：候选送来时自动完成回放、分组统计、误差分布、区间覆盖率、响应方向、适用范围草稿；
人工审核适用范围后审批发布，发布时把模型、特征定义、处理规则、工况方案、适用范围打包成一个版本。"""
from __future__ import annotations

import copy
import json
import shutil
import traceback

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from .. import config
from ..core import applicability as AP
from ..core import features as F
from ..core import models as M
from ..core import pipeline, quality, templates
from ..store import db
from . import context as cx
from . import jobs, versions
from . import spec as sp
from . import train as T

LIFE = ["开发中", "待检验", "检验通过", "已发布", "已归档"]


def cand_dir(scid: int, code: str):
    p = config.VAR_DIR / "models" / f"S{scid}" / "candidates" / code
    p.mkdir(parents=True, exist_ok=True)
    return p


def response_cfg(sc) -> list[dict]:
    if "response" not in sc.config:
        sc.config["response"] = copy.deepcopy(templates.all_templates().get(sc.template_id, {}).get("response", []))
    return sc.config["response"]


# ----------------------------------------------------------------------------- 送检
def send(s, e: db.Experiment) -> db.Candidate:
    if e.algo == "base":
        raise ValueError("基线不能送去检验")
    if e.status != "已完成":
        raise ValueError("实验还没有完成")
    old = s.scalars(select(db.Candidate).where(db.Candidate.exp_id == e.id)).first()
    if old:
        return old
    pre = M.PREFIX[e.algo]
    n = s.scalar(select(func.count()).select_from(db.Candidate).where(
        db.Candidate.scenario_id == e.scenario_id, db.Candidate.code.like(f"{pre}-V%"))) or 0
    nr = s.scalar(select(func.count()).select_from(db.Candidate).where(db.Candidate.scenario_id == e.scenario_id)) or 0
    c = db.Candidate(scenario_id=e.scenario_id, exp_id=e.id, code=f"{pre}-V{n + 1}", life="待检验", status="排队中",
                     report_code=f"VR-{nr + 1:03d}", history=[{"t": f"{db.now():%Y-%m-%d %H:%M}", "life": "待检验",
                                                               "by": config.DEFAULT_USER, "note": f"由 {e.code} 送检"}])
    s.add(c)
    e.label = "待检验"
    sc = s.get(db.Scenario, e.scenario_id)
    b = s.get(db.TrainBatch, e.batch_id)
    ds = T.latest_ds(s, sc, b.ds_code)
    ds.used_by = list(dict.fromkeys((ds.used_by or []) + [c.code]))
    versions.advance(sc, "检验效果")
    s.flush()
    s.commit()
    jobs.submit(f"val{c.id}", run_validation, c.id)
    return c


def _upd(cid, **kw):
    with db.session() as s:
        c = s.get(db.Candidate, cid)
        for k, v in kw.items():
            setattr(c, k, v)


def replay_mask(df, role) -> np.ndarray:
    tr_cycles = set(df["cycle"][role == "train"])
    usable = (df["included"] & df["complete"]).to_numpy()
    newer = usable & (role == "skip") & (df["cycle"].to_numpy() > (max(tr_cycles) if tr_cycles else 0)) & (df["cycle"].to_numpy() > 0)
    return (role == "test") | newer


def online_cand(s, sc_id: int, exclude_id: int | None = None):
    q = select(db.Candidate).where(db.Candidate.scenario_id == sc_id, db.Candidate.life == "已发布")
    c = s.scalars(q).first()
    return c if c and c.id != exclude_id else None


def predict_with(s, cand: db.Candidate, times: pd.DatetimeIndex, grid: pd.DatetimeIndex):
    """用某个候选（按它自己的版本快照）在指定时刻上预测。"""
    e = s.get(db.Experiment, cand.exp_id)
    b = s.get(db.TrainBatch, e.batch_id)
    sc, ds, df, cols, spec, role, cyc = T.load_context(s, b)
    model = T.load_model(e.path)
    X, rt = sp.features_full(sc, spec)
    pos = X.index.get_indexer(times)
    p = model.predict(X.iloc[pos].reset_index(drop=True), rt[pos])[0]
    gp = X.index.get_indexer(grid)
    g = model.predict(X.iloc[gp].reset_index(drop=True), rt[gp])[0]
    return p, g


def run_validation(cid: int):
    try:
        _upd(cid, status="检验中", error="")
        with db.session() as s:
            c = s.get(db.Candidate, cid)
            e = s.get(db.Experiment, c.exp_id)
            b = s.get(db.TrainBatch, e.batch_id)
            sc, ds, df, cols, spec, role, cyc = T.load_context(s, b)
            model = T.load_model(e.path)
            resp_items = copy.deepcopy(response_cfg(sc))
            db.touch_json(sc)
            rg = b.config["rg"]
            names = {k["id"]: k["name"] for k in rg["classes"]}
            inc = [k["id"] for k in rg["classes"] if k.get("include")]
            on = online_cand(s, sc.id, cid)
            mask = replay_mask(df, role)
            R = df[mask].reset_index(drop=True)
            p, lo, hi = model.predict(R[cols], R["regime_rt"].to_numpy())
            bp = M.baseline(R["t"].to_numpy(), df["t"].to_numpy(), df["y"].to_numpy())
            X, rt = sp.features_full(sc, spec)
            g0 = pd.Timestamp(R["t"].min()).floor("D")
            g1 = pd.Timestamp(ds.config["end"]) + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)
            grid = pd.date_range(g0, g1, freq="1h")
            gpos = X.index.get_indexer(grid)
            G = X.iloc[gpos].reset_index(drop=True)
            gp, glo, ghi = model.predict(G, rt[gpos])
            po = gpo = None
            if on:
                po, gpo = predict_with(s, on, pd.DatetimeIndex(R["t"]), grid)
            on_code = on.code if on else None
            # 响应方向
            resp = response_test(sc, spec, model, R, cols, p, resp_items)
            # 适用范围草稿
            Xtr = df[role == "train"]
            appl = draft(sc, spec, Xtr, df, role, R, p, rg, names, inc)
            scid, code = sc.id, c.code
        y = R["y"].to_numpy(float)
        ro = R["regime"].to_numpy()
        ok = np.isfinite(p)
        err = p - y
        rep = {"mae": _mae(p, y), "max": _r1(np.nanmax(np.abs(err))) if ok.any() else None, "n": int(ok.sum()),
               "n_all": int(len(y)), "base_mae": _mae(bp, y), "rmse": _r2(np.sqrt(np.nanmean(err ** 2))),
               "period": [f"{R['t'].min():%Y-%m-%d}", f"{R['t'].max():%Y-%m-%d}"],
               "cycles": sorted(int(x) for x in R["cycle"].unique()),
               "test_cycles": sorted(int(x) for x in R["cycle"][role[mask] == "test"].unique())}
        if po is not None:
            rep["online"] = {"code": on_code, "mae": _mae(po, y), "max": _r1(np.nanmax(np.abs(po - y)))}
        seg_r = []
        for r in list(dict.fromkeys(inc + [x for x in np.unique(ro) if x not in inc])):
            k = ro == r
            if not k.any() and r not in inc:
                continue
            seg_r.append({"id": r, "name": names.get(r, r), "n": int(k.sum()), "mae": _mae(p[k], y[k]),
                          "online": _mae(po[k], y[k]) if po is not None else None, "include": r in inc})
        seg_c = []
        for ci in rep["cycles"]:
            k = R["cycle"].to_numpy() == ci
            seg_c.append({"cycle": ci, "test": ci in rep["test_cycles"], "n": int(k.sum()), "mae": _mae(p[k], y[k]),
                          "online": _mae(po[k], y[k]) if po is not None else None})
        e_ok = err[ok]
        cnt, _ = np.histogram(np.clip(e_ok, -19.99, 19.99), bins=20, range=(-20, 20))
        cov = float(np.mean((y[ok] >= lo[ok]) & (y[ok] <= hi[ok]))) * 100 if ok.any() else None
        d = cand_dir(scid, code)
        R2 = R.copy()
        R2["p"], R2["lo"], R2["hi"], R2["base"] = p, lo, hi, bp
        if po is not None:
            R2["p_online"] = po
        R2.to_parquet(d / "replay_samples.parquet", index=False)
        G2 = G.copy()
        G2["t"], G2["p"], G2["lo"], G2["hi"], G2["regime_rt"] = grid, gp, glo, ghi, rt[gpos]
        if gpo is not None:
            G2["p_online"] = gpo
        G2.to_parquet(d / "replay_grid.parquet", index=False)
        step = max(1, len(grid) // 900)
        report = {"rep": rep, "seg_regime": seg_r, "seg_cycle": seg_c, "hist": cnt.astype(int).tolist(),
                  "coverage": round(cov, 1) if cov is not None else None, "resp": resp,
                  "chart": {"t": [f"{x:%Y-%m-%d %H:%M}" for x in grid[::step]], "p": _l(gp[::step]), "lo": _l(glo[::step]),
                            "hi": _l(ghi[::step]), "online": _l(gpo[::step]) if gpo is not None else None,
                            "points": [[f"{t:%Y-%m-%d %H:%M}", round(float(v), 1)] for t, v in zip(R["t"], y)]},
                  "algo": M.ALGS.get(model.algo), "mode": M.MODES.get(model.mode),
                  "created": f"{db.now():%Y-%m-%d %H:%M}"}
        with db.session() as s:
            c = s.get(db.Candidate, cid)
            c.report, c.appl, c.status = report, appl, "已完成"
            chk = checks(s, c)
            if c.life == "待检验" and not any(k["state"] == "bad" for k in chk[:2]):
                c.life = "检验通过"
                c.history = c.history + [{"t": f"{db.now():%Y-%m-%d %H:%M}", "life": "检验通过", "by": "平台（自动检验）",
                                          "note": f"检验报告 {c.report_code}"}]
    except Exception as ex:
        _upd(cid, status="失败", error=f"{ex}\n{traceback.format_exc()[-1200:]}")


def _mae(p, y):
    k = np.isfinite(p) & np.isfinite(y)
    return round(float(np.mean(np.abs(p[k] - y[k]))), 2) if k.any() else None


def _r1(x):
    return round(float(x), 1)


def _r2(x):
    return round(float(x), 2) if np.isfinite(x) else None


def _l(a):
    return [None if not np.isfinite(x) else round(float(x), 2) for x in a]


# ----------------------------------------------------------------------------- 响应方向
def response_test(sc, spec: pipeline.Spec, model, R: pd.DataFrame, cols, p_base, items: list[dict]) -> list[dict]:
    """在检验数据上把某个输入按规定幅度改变，其他输入不变，重新走同一条计算链，看估计值平均变化多少。"""
    n2t = {spec.names[t]: t for t in spec.tags}
    combos = {c["name"] for c in spec.combos if c.get("on")}
    out = []
    raw = None
    for it in items:
        name = it["change"]
        label = f"{name} {'+' if it.get('pct', it.get('delta', 0)) >= 0 else ''}{it['pct']}%" if "pct" in it else \
            f"{name} +{it.get('delta')} {'天' if '天' in name else ''}".rstrip()
        if it.get("note"):
            label += f"（{it['note']}）"
        val = None
        msg = ""
        try:
            if name in n2t:
                tag = n2t[name]
                if raw is None:
                    raw = cx.src().read(sc.furnace, pipeline.required_tags(spec))
                mod = raw.copy()
                mod[tag] = mod[tag] * (1 + float(it.get("pct", 5)) / 100)

                def qf(t, arr, rules, tag=tag):
                    if t == tag:
                        return pipeline.default_quality(t, arr, rules)
                    return cx.quality_of(sc.furnace, t, rules).value
                Xm, _ = pipeline.build(mod, spec, qf)
                pos = Xm.index.get_indexer(pd.DatetimeIndex(R["t"]))
                pm = model.predict(Xm.iloc[pos].reset_index(drop=True), R["regime_rt"].to_numpy())[0]
            elif name in combos and name in cols:
                Xm = R[cols].copy()
                Xm[name] = Xm[name] + float(it.get("delta", 0))
                pm = model.predict(Xm, R["regime_rt"].to_numpy())[0]
            else:
                msg = "该输入不在模型中，未测试"
                pm = None
            if pm is not None:
                k = np.isfinite(pm) & np.isfinite(p_base)
                val = round(float(np.mean(pm[k] - p_base[k])), 2) if k.any() else None
        except Exception as ex:
            msg = f"计算失败：{ex}"
        out.append({"change": name, "label": label, "value": val, "msg": msg})
    return out


def judge_resp(report: dict, items: list[dict]) -> list[dict]:
    exp = {it["change"]: it for it in items}
    out = []
    for r in report.get("resp", []):
        it = exp.get(r["change"], {})
        e = it.get("expect", "up")
        v = r["value"]
        ok = None if v is None else (v > 0 if e == "up" else v < 0)
        out.append({**r, "expect": e, "expect_label": it.get("label", "上升" if e == "up" else "下降"), "ok": ok})
    return out


# ----------------------------------------------------------------------------- 适用范围
def default_rules(spec: pipeline.Spec) -> list[dict]:
    steam = next((t for t in spec.tags if spec.names[t] == "稀释蒸汽流量"), None)
    out = [{"type": "quality", "on": True}, {"type": "after_event", "hours": 2, "on": True}]
    if steam:
        out.append({"type": "rate", "tag": steam, "minutes": 5, "pct": 15, "on": False})
    return out


def draft(sc, spec, Xtr, df, role, R, p, rg, names, inc) -> dict:
    meta = []
    for c in pipeline.columns(spec):
        if c.get("excluded"):
            continue
        if c["kind"] == "cur" or c["kind"] in ("formula", "op"):
            unit = spec.units.get(c["tag"], "") if c.get("tag") else next((x.get("unit", "") for x in spec.combos if x["name"] == c["col"]), "")
            meta.append({"col": c["col"], "name": c["col"], "unit": unit})
    regs = []
    y = R["y"].to_numpy(float)
    ro = R["regime"].to_numpy()
    for k in rg["classes"]:
        ntr = int(((role == "train") & (df["regime"] == k["id"])).sum())
        sel = ro == k["id"]
        mae = _mae(p[sel], y[sel]) if sel.any() else None
        if k.get("include") and ntr:
            sug = "纳入"
        elif not ntr:
            sug = "训练未使用"
        else:
            sug = "样本少或误差大，建议排除"
        regs.append({"id": k["id"], "name": k["name"], "n_train": ntr, "mae": mae, "include": bool(k.get("include") and ntr),
                     "suggest": sug})
    return AP.generate(Xtr, meta, regs, 99, default_rules(spec))


def aux_for(sc, spec: pipeline.Spec, appl: dict, times: pd.DatetimeIndex, rt_full: np.ndarray) -> dict:
    """人工规则需要的辅助量：距上次周期起点的小时数、某参数短时变化比例（都只用历史数据）。"""
    idx = cx.index(sc.furnace)
    pos = idx.get_indexer(times)
    out = {}
    in_ev = F.event_marks(rt_full, "cycle", spec.cycle_mode)
    off = None
    if spec.cycle_anchor:
        off = (idx[0] - pd.Timestamp(spec.cycle_anchor)).total_seconds() / 60
    out["since_h"] = F.eval_op("ago", in_ev, None, start_offset_min=off if off and off > 0 else None)[pos]
    for i, r in enumerate(appl["rules"]):
        if r["type"] == "rate" and r.get("tag"):
            x = cx.quality_of(sc.furnace, r["tag"], spec.rules.get(r["tag"]) or cx.rules_for({}, sc.furnace, r["tag"])).value
            m = int(r.get("minutes", 5))
            prev = np.concatenate([np.full(m, np.nan), x[:-m]])
            with np.errstate(all="ignore"):
                out[f"rate:{i}"] = (np.abs(x - prev) / np.abs(prev) * 100)[pos]
    return out


def appl_preview(s, c: db.Candidate, appl: dict) -> dict:
    e = s.get(db.Experiment, c.exp_id)
    b = s.get(db.TrainBatch, e.batch_id)
    sc = s.get(db.Scenario, c.scenario_id)
    ds = T.latest_ds(s, sc, b.ds_code)
    from . import trainset
    spec = trainset.spec_for(s, sc, ds, b.config["feats"], b.config["rg"])
    model = T.load_model(e.path)
    _, rt_full = sp.features_full(sc, spec)
    d = cand_dir(sc.id, c.code)
    R = pd.read_parquet(d / "replay_samples.parquet")
    G = pd.read_parquet(d / "replay_grid.parquet")
    okS, catS, _ = AP.evaluate(appl, R, R["regime_rt"].to_numpy(), model.columns,
                               aux_for(sc, spec, appl, pd.DatetimeIndex(R["t"]), rt_full))
    okG, catG, _ = AP.evaluate(appl, G, G["regime_rt"].to_numpy(), model.columns,
                               aux_for(sc, spec, appl, pd.DatetimeIndex(G["t"]), rt_full))
    y, p = R["y"].to_numpy(float), R["p"].to_numpy(float)
    share = float(okG.mean() * 100) if len(okG) else None
    dG = AP.mahal(appl, G)
    fin = np.isfinite(dG)
    reasons = {k: int((catG == k).sum()) for k in AP.REASON}
    var_out = []
    for v in appl["vars"]:
        x = G[v["col"]].to_numpy(float)
        with np.errstate(invalid="ignore"):
            var_out.append({"col": v["col"], "out_pct": round(float(np.mean((x < v["lo"]) | (x > v["hi"]))) * 100, 2)})
    return {"share": round(share, 1) if share is not None else None,
            "mae_in": _mae(np.where(okS, p, np.nan), y), "mae_out": _mae(np.where(~okS, p, np.nan), y),
            "n_in": int((okS & np.isfinite(p)).sum()), "n_out": int((~okS & np.isfinite(p)).sum()),
            "reasons_hours": reasons, "mahal_out_pct": round(float(np.mean(dG[fin] > appl["mahal"]["thr"]) * 100), 1) if fin.any() else None,
            "replay_d_hist": np.histogram(np.clip(dG[fin], 0, 8), bins=40, range=(0, 8))[0].astype(int).tolist() if fin.any() else [],
            "train_d_hist": np.histogram(np.clip(appl["mahal"]["train_d"], 0, 8), bins=40, range=(0, 8))[0].astype(int).tolist(),
            "vars": var_out, "adjusted": sum(1 for v in appl["vars"] if v["lo"] != v["p1"] or v["hi"] != v["p99"]),
            "invalid": AP.validate_edit(appl)}


# ----------------------------------------------------------------------------- 结论、审批
def checks(s, c: db.Candidate) -> list[dict]:
    sc = s.get(db.Scenario, c.scenario_id)
    rp = c.report or {}
    if c.status != "已完成":
        st = "todo"
        return [{"name": n, "state": st, "text": "检验中…" if c.status in ("排队中", "检验中") else c.status}
                for n in ("回放结果", "响应方向", "适用范围", "结论与审批")]
    rep = rp["rep"]
    hi = next((x for x in rp["seg_regime"] if x["id"] == "hi"), None)
    nm = next((x for x in rp["seg_regime"] if x["id"] == "norm"), None)
    notes, state = [], "ok"
    ref = rep.get("online")
    if rep["mae"] is None or (rep["base_mae"] and rep["mae"] >= rep["base_mae"]):
        state = "bad"
        notes.append("不比基线好")
    if ref and rep["mae"] is not None and rep["mae"] > ref["mae"] * 1.02:
        state = "warn" if state == "ok" else state
        notes.append(f"不如在线 {ref['code']}")
    if hi and nm and hi["mae"] and nm["mae"] and hi["mae"] > nm["mae"] * 1.3:
        state = "warn" if state == "ok" else state
        notes.append(f"高负荷是正常的 {hi['mae'] / nm['mae']:.1f} 倍")
    cov = rp.get("coverage")
    if cov is not None and abs(cov - 80) > 5:
        state = "warn" if state == "ok" else state
        notes.append(f"区间覆盖率 {cov:.0f}%")
    t1 = f"{rep['mae']} ℃（{'在线 ' + str(ref['mae']) + ' ℃' if ref else '基线 ' + str(rep['base_mae']) + ' ℃'}）" + \
        (" · " + "、".join(notes) if notes else "")
    jr = judge_resp(rp, response_cfg(sc))
    bad = [r for r in jr if r["ok"] is False]
    t2 = (f"{len(jr)} 项全部符合" if not bad else f"{len(bad)} 项方向相反") if jr else "无期望方向"
    st2 = "bad" if bad else "ok"
    inv = AP.validate_edit(c.appl) if c.appl else None
    if c.life in ("已发布", "已归档"):
        s3, t3 = "ok", "已随模型发布"
    elif inv:
        s3, t3 = "bad", "有调整未填依据或上下限无效"
    elif c.appl.get("reviewed"):
        s3, t3 = "ok", "已审核"
    else:
        s3, t3 = "todo", "待人工审核"
    if c.life == "已发布":
        s4, t4 = "ok", "已发布"
    elif c.life == "已归档":
        s4, t4 = "ok", "已归档"
    else:
        base = ref["mae"] if ref else rep["base_mae"]
        s4 = "todo"
        t4 = (f"比{'在线模型' if ref else '基线'}好 {base - rep['mae']:.1f} ℃，待审批" if rep["mae"] is not None and base and rep["mae"] < base
              else f"不如{'在线模型' if ref else '基线'}")
    return [{"name": "回放结果", "state": state, "text": t1}, {"name": "响应方向", "state": st2, "text": t2},
            {"name": "适用范围", "state": s3, "text": t3}, {"name": "结论与审批", "state": s4, "text": t4}]


def conclusion(s, c, chk) -> str:
    if c.status != "已完成":
        return ""
    bad = [k for k in chk if k["state"] == "bad"]
    warn = [k for k in chk if k["state"] == "warn"]
    rep = c.report["rep"]
    ref = rep.get("online")
    base = ref["mae"] if ref else rep["base_mae"]
    who = "在线模型" if ref else "基线（沿用上一个校核值）"
    if bad:
        return f"{'、'.join(k['name'] for k in bad)}未通过，不建议发布，需回到训练调整。"
    if rep["mae"] is not None and rep["mae"] < base:
        return (f"比{who}好 {base - rep['mae']:.1f} ℃，" + ("需关注：" + "；".join(f"{k['name']}（{k['text']}）" for k in warn) + "。"
                if warn else "各项检查均通过。") + ("建议发布，先以影子模式与在线模型并行运行。" if ref else "建议发布，上线后先以影子模式运行。"))
    return f"没有比{who}更好，不建议{'替换' if ref else '发布'}。"


def approve(s, c: db.Candidate):
    if c.status != "已完成":
        raise ValueError("检验还没有完成")
    if c.life in ("已发布", "已归档"):
        raise ValueError("该版本已发布或已归档")
    inv = AP.validate_edit(c.appl)
    if inv:
        raise ValueError(inv)
    chk = checks(s, c)
    bad = [k for k in chk[:2] if k["state"] == "bad"]
    if bad:
        raise ValueError("；".join(f"{k['name']}：{k['text']}" for k in bad) + "，不能发布")
    sc = s.get(db.Scenario, c.scenario_id)
    e = s.get(db.Experiment, c.exp_id)
    b = s.get(db.TrainBatch, e.batch_id)
    ds = T.latest_ds(s, sc, b.ds_code)
    from . import trainset
    rules = trainset.rules_of(s, sc, ds)
    appl = copy.deepcopy(c.appl)
    appl["reviewed"] = True
    c.appl = appl
    old = online_cand(s, sc.id, c.id)
    if old:
        old.life = "已归档"
        old.history = old.history + [{"t": f"{db.now():%Y-%m-%d %H:%M}", "life": "已归档", "by": "平台", "note": f"被 {c.code} 替换"}]
        oe = s.get(db.Experiment, old.exp_id)
        oe.label = f"已发布 {old.code}，已归档"
    d = config.VAR_DIR / "models" / f"S{sc.id}" / "published" / c.code
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(f"{e.path}/model.pkl", d / "model.pkl")
    pkg = {"code": c.code, "scenario": sc.name, "furnace": sc.furnace, "target": sc.config["target"],
           "experiment": e.code, "batch": b.code, "algo": e.algo, "mode": e.mode, "params": e.params,
           "dataset": b.ds_code, "rule_code": b.rule_code, "rules": rules, "wc_code": b.wc_code, "regime": b.config["rg"],
           "f_code": b.f_code, "features": b.config["feats"], "tags": ds.config["tags"],
           "applicability": appl, "report_code": c.report_code, "report": {k: v for k, v in c.report.items() if k != "chart"},
           "approvers": config.APPROVERS, "published_at": f"{db.now():%Y-%m-%d %H:%M}"}
    (d / "package.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=2, default=str), "utf-8")
    c.package = str(d)
    c.life = "已发布"
    c.published_at = db.now()
    c.history = c.history + [{"t": f"{db.now():%Y-%m-%d %H:%M}", "life": "已发布", "by": "、".join(config.APPROVERS),
                              "note": "审批通过并发布；适用范围随模型一起审批"}]
    e.label = f"已发布 {c.code}"
    sc.config["online_model"] = c.code
    versions.advance(sc, "上线投用")
    sc.status = "已发布"
    db.touch_json(sc)


def reject(s, c: db.Candidate, note: str = ""):
    if c.life in ("已发布", "已归档"):
        raise ValueError("该版本已发布或已归档")
    c.life = "开发中"
    c.history = c.history + [{"t": f"{db.now():%Y-%m-%d %H:%M}", "life": "开发中", "by": config.APPROVERS[0],
                              "note": "退回修改" + (f"：{note}" if note else "")}]
    e = s.get(db.Experiment, c.exp_id)
    e.label = "已退回"


def cand_dict(s, c: db.Candidate, full=True) -> dict:
    e = s.get(db.Experiment, c.exp_id)
    b = s.get(db.TrainBatch, e.batch_id)
    out = {"id": c.id, "code": c.code, "exp": e.code, "algo": M.ALGS[e.algo], "mode": M.MODES[e.mode], "life": c.life,
           "status": c.status, "error": c.error, "ds": b.ds_code, "rule": b.rule_code, "wc": b.wc_code, "f": b.f_code,
           "batch": b.code, "report_code": c.report_code, "train_mae": (e.metrics or {}).get("mae"),
           "published_at": f"{c.published_at:%Y-%m-%d %H:%M}" if c.published_at else None, "history": c.history}
    if full:
        sc = s.get(db.Scenario, c.scenario_id)
        chk = checks(s, c)
        out.update({"report": c.report, "appl": c.appl, "checks": chk, "conclusion": conclusion(s, c, chk),
                    "resp": judge_resp(c.report or {}, response_cfg(sc)) if c.report else [],
                    "response_cfg": response_cfg(sc), "lives": LIFE,
                    "tag_names": {r["tag"]: r["name"] for r in cx.tag_rows(sc.furnace)},
                    "rule_texts": [AP.rule_text(r, {rr["tag"]: rr["name"] for rr in cx.tag_rows(sc.furnace)}) for r in (c.appl or {}).get("rules", [])],
                    "regime_names": {k["id"]: k["name"] for k in b.config["rg"]["classes"]}})
    return out
