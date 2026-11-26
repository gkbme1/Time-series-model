"""适用范围：判断模型对当前输入"有没有把握"。检验时生成并审核，随模型一起发布；上线时逐点判断，
不适用时不输出并记录原因。

四类检查（任一不满足即不适用）：
1. 单变量范围：每个输入（当前值列和组合量）在训练数据 P1–P99 之内（可人工调整，须填依据）
2. 支持的工况：实时判定的工况在支持列表中
3. 多变量组合：标准化后的马氏距离不超过阈值（训练样本距离的某个分位数）
4. 人工补充规则：重新投料后若干小时内 / 某参数短时间变化过大
另外输入列有缺失（质量不合格）时直接不输出。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

PCTS = [95, 97.5, 99, 99.5, 99.9]
REASON = {"quality": "输入数据质量异常", "range": "参数超出了模型见过的范围", "regime": "处在模型没学过的工况",
          "mahal": "参数单看正常，但组合没见过", "rule": "人工补充规则"}


def _hist(v: np.ndarray, lo: float, hi: float, n=30) -> list[int]:
    c, _ = np.histogram(v, bins=n, range=(lo, hi))
    return c.astype(int).tolist()


def generate(Xtr: pd.DataFrame, var_meta: list[dict], regimes: list[dict], pct: float = 99,
             rules: list[dict] | None = None) -> dict:
    """var_meta：[{col, name, unit}]；regimes：[{id, name, n_train, mae, include}]。"""
    vars_ = []
    for m in var_meta:
        v = Xtr[m["col"]].to_numpy(dtype="float64")
        v = v[np.isfinite(v)]
        if not len(v):
            continue
        p1, p99 = float(np.percentile(v, 1)), float(np.percentile(v, 99))
        span = (p99 - p1) or abs(p99) * 0.1 or 1.0
        a, b = p1 - span * 0.6, p99 + span * 0.6
        if v.min() >= 0:
            a = max(0.0, a)  # 非负变量（流量、天数等）的分布图不画负值
        dec = 0 if span >= 50 else 1 if span >= 2 else 2 if span >= 0.05 else 4
        vars_.append({**m, "p1": round(p1, dec), "p99": round(p99, dec), "lo": round(p1, dec), "hi": round(p99, dec),
                      "why": "", "dec": dec, "hmin": round(a, dec + 1), "hmax": round(b, dec + 1),
                      "hist": _hist(v, a, b)})
    cols = [v["col"] for v in vars_]
    A = Xtr[cols].to_numpy(dtype="float64")
    A = A[np.isfinite(A).all(axis=1)]
    mu, sd = A.mean(axis=0), A.std(axis=0)
    sd[sd == 0] = 1.0
    Z = (A - mu) / sd
    lw = LedoitWolf().fit(Z)
    prec = np.linalg.inv(lw.covariance_)
    d = np.sqrt(np.einsum("ij,jk,ik->i", Z, prec, Z))
    mahal = {"method": "马氏距离", "pct": pct, "cols": cols, "mean": mu.tolist(), "scale": sd.tolist(),
             "prec": prec.tolist(), "train_d": np.sort(d).round(3).tolist(),
             "thr": round(float(np.percentile(d, pct)), 3), "thr_by_pct": {str(p): round(float(np.percentile(d, p)), 3) for p in PCTS}}
    return {"vars": vars_, "regimes": regimes, "mahal": mahal, "rules": rules or [], "reviewed": False}


def set_pct(appl: dict, pct: float) -> None:
    m = appl["mahal"]
    m["pct"] = pct
    m["thr"] = m["thr_by_pct"].get(str(pct)) or round(float(np.percentile(m["train_d"], pct)), 3)


def mahal(appl: dict, X: pd.DataFrame) -> np.ndarray:
    m = appl["mahal"]
    A = X[m["cols"]].to_numpy(dtype="float64")
    Z = (A - np.array(m["mean"])) / np.array(m["scale"])
    P = np.array(m["prec"])
    with np.errstate(invalid="ignore"):
        return np.sqrt(np.einsum("ij,jk,ik->i", Z, P, Z))


def rule_text(r: dict, names: dict[str, str]) -> str:
    if r["type"] == "quality":
        return "关键输入数据质量异常（有输入列无效）时，判为不适用"
    if r["type"] == "after_event":
        return f"烧焦或停炉结束、重新投料后 {r.get('hours', 2)} 小时内，判为不适用"
    if r["type"] == "rate":
        return f"{names.get(r.get('tag'), r.get('tag'))} {r.get('minutes', 5)} 分钟内变化超过 {r.get('pct', 15)}% 时，判为不适用"
    return r.get("text", "")


def evaluate(appl: dict, X: pd.DataFrame, regime: np.ndarray, model_cols: list[str], aux: dict[str, np.ndarray]):
    """返回 (适用, 原因类别, 原因说明)。aux：'since_h'（距上次周期起点的小时数）、'rate:<i>'（第 i 条规则的变化比例）。"""
    n = len(X)
    ok = np.ones(n, bool)
    cat = np.full(n, "", dtype=object)
    det = np.full(n, "", dtype=object)

    def mark(m, c, txt):
        nonlocal ok
        new = m & ok
        cat[new] = c
        if isinstance(txt, np.ndarray):
            det[new] = txt[new]
        else:
            det[new] = txt
        ok &= ~m

    q = ~np.isfinite(X[model_cols].to_numpy(dtype="float64")).all(axis=1)
    mark(q, "quality", "有输入列无效")
    for v in appl["vars"]:
        x = X[v["col"]].to_numpy(dtype="float64")
        with np.errstate(invalid="ignore"):
            hi_m, lo_m = x > v["hi"], x < v["lo"]
        txt = np.where(hi_m, [f"{v['name']}偏高（生效范围上限 {v['hi']}）"] * n, [f"{v['name']}偏低（生效范围下限 {v['lo']}）"] * n)
        mark(hi_m | lo_m, "range", txt)
    sup = {r["id"] for r in appl["regimes"] if r.get("include")}
    names = {r["id"]: r["name"] for r in appl["regimes"]}
    reg = np.asarray(regime)
    mark(~np.isin(reg, list(sup)), "regime", np.array([f"当前工况：{names.get(x, x)}" for x in reg], dtype=object))
    d = mahal(appl, X)
    with np.errstate(invalid="ignore"):
        mark(d > appl["mahal"]["thr"], "mahal", np.array([f"马氏距离 {x:.2f} > 阈值 {appl['mahal']['thr']}" for x in d], dtype=object))
    for i, r in enumerate(appl["rules"]):
        if not r.get("on") or r["type"] == "quality":
            continue
        if r["type"] == "after_event" and "since_h" in aux:
            with np.errstate(invalid="ignore"):
                mark(aux["since_h"] < float(r.get("hours", 2)), "rule", f"重新投料后不足 {r.get('hours', 2)} 小时")
        if r["type"] == "rate" and f"rate:{i}" in aux:
            with np.errstate(invalid="ignore"):
                mark(aux[f"rate:{i}"] > float(r.get("pct", 15)), "rule", rule_text(r, {}).split("时")[0])
    return ok, cat, det


def validate_edit(appl: dict) -> str | None:
    """审核前检查：上下限有效、调整过的范围必须填写依据。"""
    for v in appl["vars"]:
        if v["lo"] >= v["hi"]:
            return f"适用范围中\"{v['name']}\"的上下限无效，请修改后再审批"
    for v in appl["vars"]:
        if (v["lo"] != v["p1"] or v["hi"] != v["p99"]) and not str(v.get("why", "")).strip():
            return f"适用范围中\"{v['name']}\"已调整但未填写调整依据，请补充后再审批"
    return None
