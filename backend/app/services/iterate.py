""""跟踪改进"：待分析样本自动回收、人工复核、输入分布漂移（PSI）。

触发条件（在运行实例的输出表和校核配对上真实计算）：
- 误差持续增大：连续 2 个以上校核配对点偏差 > 15 ℃
- 输入超出适用范围：参数超范围或组合异常，持续 ≥ 30 min
- 识别出新工况：处在模型没学过的工况（烧焦、停炉除外），持续 ≥ 30 min
- 输入数据质量异常：持续 ≥ 30 min
- 新旧模型输出差异：影子实例与在线实例差值 > 12 ℃，持续 ≥ 10 min
复核为"进入训练集"的样本，其时间窗内的有效校核值在下一次生成数据版本时作为一个校核来源。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select

from ..store import db
from . import online as ON
from .replay import clock

KINDS = {"err": "误差持续增大", "range": "输入超出适用范围", "regime": "识别出新工况", "quality": "输入数据质量异常",
         "diff": "新旧模型输出差异", "big": "较大偏差"}
CAUSES = ["工况变化", "仪表问题", "模型不足", "校核值不可信"]


def _add(s, sc, inst, kind, t, t_end, reason, detail):
    key = f"{kind}:{inst.id}:{t}"
    if s.scalars(select(db.Sample).where(db.Sample.scenario_id == sc.id, db.Sample.key == key)).first():
        return
    s.add(db.Sample(scenario_id=sc.id, instance_id=inst.id, key=key, kind=KINDS[kind], t=t, t_end=t_end, reason=reason,
                    detail=detail, created_sim=f"{clock.now_min():%Y-%m-%d %H:%M}"))


def sync(s, sc):
    """扫描各运行实例，把新触发的情况加入样本库（幂等）。"""
    insts = list(s.scalars(select(db.Instance).where(db.Instance.scenario_id == sc.id)))
    now = clock.now_min()
    names = {r["id"]: r["name"] for r in sc.config.get("regime", {}).get("classes", [])}
    for inst in insts:
        t0 = pd.Timestamp(inst.created_sim)
        out = ON.outputs(s, inst.id, t0, now)
        if out.empty:
            continue
        out = out[~out["backfill"]]
        for e in ON.episodes(out):
            if e["minutes"] < 30:
                continue
            if e["cat"] in ("range", "mahal"):
                _add(s, sc, inst, "range", e["start"], e["end"], f"输入超出适用范围（{e['minutes']} min）",
                     f"{inst.furnace} · {inst.model_code}：{e['detail']}，持续 {e['minutes']} min，期间不输出。")
            elif e["cat"] == "regime" and not any(k in e["detail"] for k in ("烧焦", "停炉")):
                _add(s, sc, inst, "regime", e["start"], e["end"], f"识别出新工况（{e['detail'].replace('当前工况：', '')}）",
                     f"{inst.furnace}：{e['detail']}，持续 {e['minutes']} min，模型没学过这个工况，期间不输出。")
            elif e["cat"] == "quality":
                q = out[(out["t"] >= pd.Timestamp(e["start"])) & (out["t"] < pd.Timestamp(e["end"]))]["quality"]
                _add(s, sc, inst, "quality", e["start"], e["end"], f"输入数据质量异常（{e['minutes']} min）",
                     f"{inst.furnace}：{q.value_counts().index[0] if len(q) else ''}，模型已自动暂停输出。")
        if inst.mode == "online" and inst.furnace == sc.furnace:
            pr = ON.pairs(s, sc, inst, t0, now, backfill=False)
            pr = pr[(pr["eval"] == True) & pr["dev"].notna()] if not pr.empty else pr  # noqa: E712
            big = (pr["dev"].abs() > 15).to_numpy() if len(pr) else np.array([])
            i = 0
            while i < len(big):
                if big[i]:
                    j = i
                    while j + 1 < len(big) and big[j + 1]:
                        j += 1
                    if j > i:
                        a, b = pr.iloc[i], pr.iloc[j]
                        _add(s, sc, inst, "err", f"{a['t']:%Y-%m-%d %H:%M}", f"{b['t']:%Y-%m-%d %H:%M}",
                             f"误差持续增大（> 15 ℃，{j - i + 1} 个校核点）",
                             f"{a['t']:%m-%d %H:%M} 至 {b['t']:%m-%d %H:%M} 连续 {j - i + 1} 个校核点偏差超过 15 ℃，"
                             f"最大 {pr['dev'].iloc[i:j + 1].abs().max():.1f} ℃。")
                    i = j + 1
                else:
                    i += 1
    # 影子 vs 在线
    by_f = {}
    for inst in insts:
        by_f.setdefault(inst.furnace, []).append(inst)
    for f, lst in by_f.items():
        on = next((i for i in lst if i.mode == "online"), None)
        for sh in [i for i in lst if i.mode == "shadow"]:
            if on is None:
                continue
            t0 = pd.Timestamp(max(sh.created_sim, on.created_sim))
            a, b = ON.outputs(s, on.id, t0, now), ON.outputs(s, sh.id, t0, now)
            if a.empty or b.empty:
                continue
            m = a.set_index("t")["value"].astype(float).to_frame("on").join(b.set_index("t")["value"].astype(float).to_frame("sh"), how="inner")
            d = (m["sh"] - m["on"]).abs() > 12
            runs = (d != d.shift()).cumsum()
            for _, g in m[d].groupby(runs[d]):
                if len(g) >= 10:
                    _add(s, sc, sh, "diff", f"{g.index[0]:%Y-%m-%d %H:%M}", f"{g.index[-1]:%Y-%m-%d %H:%M}",
                         f"新旧模型输出差异（{sh.model_code} vs {on.model_code} > 12 ℃）",
                         f"{f}：影子 {sh.model_code} 与在线 {on.model_code} 差值超过 12 ℃，持续 {len(g)} min，最大 "
                         f"{(g['sh'] - g['on']).abs().max():.1f} ℃，需对照校核值判断。")


def sample_dict(x: db.Sample) -> dict:
    return {"id": x.id, "kind": x.kind, "t": x.t, "t_end": x.t_end, "reason": x.reason, "detail": x.detail, "status": x.status,
            "cause": x.cause, "memo": x.memo, "instance_id": x.instance_id, "reviewer": x.reviewer, "n_pairs": len(x.pairs or [])}


def sample_chart(s, sc, x: db.Sample) -> dict:
    t0 = pd.Timestamp(x.t) - pd.Timedelta(hours=2)
    t1 = min(pd.Timestamp(x.t_end) + pd.Timedelta(hours=2), clock.now_min())
    inst = s.get(db.Instance, x.instance_id)
    out = ON.outputs(s, inst.id, t0, t1)
    pr = ON.pairs(s, sc, ON.online_instance(s, sc.id, inst.furnace) or inst, t0, t1, backfill=False) if inst.furnace == sc.furnace else None
    return {"t": [f"{t:%Y-%m-%d %H:%M}" for t in out["t"]] if not out.empty else [],
            "v": [None if pd.isna(v) else v for v in out["value"]] if not out.empty else [],
            "points": [[f"{r['t']:%Y-%m-%d %H:%M}", r["y"], r["source"]] for _, r in pr.iterrows() if r["eval"]] if pr is not None and not pr.empty else [],
            "mark": [x.t, x.t_end]}


def review(s, sc, x: db.Sample, use: bool, cause: str, memo: str):
    x.status = "已入训练集" if use else "已排除"
    x.cause, x.memo, x.reviewer = cause, memo, "李工"
    if use:
        inst = s.get(db.Instance, x.instance_id)
        if inst.furnace == sc.furnace:
            t0, t1 = pd.Timestamp(x.t) - pd.Timedelta(hours=2), pd.Timestamp(x.t_end) + pd.Timedelta(hours=2)
            rec = ON.records(s, sc, t0, t1)
            rec = rec[(rec["eval"] == True) & (rec["kind"] != "热电偶")] if not rec.empty else rec  # noqa: E712
            x.pairs = [[f"{r['t']:%Y-%m-%d %H:%M}", float(r["y"]), r["source"]] for _, r in rec.iterrows()]


def psi(s, sc) -> list[dict]:
    """近 30 天在线实例的输入（适用范围变量）与训练数据的分布差异。以训练数据分布图的分箱为基准（合并为 10 箱）。"""
    inst = ON.online_instance(s, sc.id, sc.furnace)
    if inst is None:
        return []
    c = s.get(db.Candidate, inst.cand_id)
    from . import runtime as RT
    pkg, _ = RT.load_package(c)
    now = clock.now_min()
    out = ON.outputs(s, inst.id, now - pd.Timedelta(days=30), now)
    if out.empty:
        return []
    res = []
    for v in pkg["applicability"]["vars"]:
        x = np.array([r.get(v["col"]) for r in out["inputs"]], dtype=float)
        x = x[np.isfinite(x)]
        if len(x) < 60:
            continue
        h = np.array(v["hist"], float)
        edges = np.linspace(v["hmin"], v["hmax"], len(h) + 1)
        e = h.reshape(10, -1).sum(axis=1)
        e10 = edges[::len(h) // 10]
        a = np.histogram(np.clip(x, v["hmin"], v["hmax"] - 1e-9), bins=e10)[0].astype(float)
        e, a = e / e.sum(), a / a.sum()
        e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
        res.append({"name": v["name"], "psi": round(float(np.sum((a - e) * np.log(a / e))), 3), "n": int(len(x))})
    return sorted(res, key=lambda r: -r["psi"])
