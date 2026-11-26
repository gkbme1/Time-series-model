"""工况规则引擎（逐分钟判定）。

条件：大于、小于、介于、N 小时内上升超过、N 小时内下降超过。
每组规则可选"全部满足 / 任一满足"，并有最短持续时间；从上到下按优先级判定，
都不满足的为"正常稳定"（norm）。

两种模式，同一份代码：
- offline（用于选训练数据、分组统计）：持续时间不足的整段视为不满足；
- causal （用于上线、以及作为模型输入）：只有已持续满最短时间之后才判定为该工况，
  不需要未来数据。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

OPS = {">": "大于", "<": "小于", "between": "介于", "rise": "上升超过", "fall": "下降超过"}
NORM = "norm"


def eval_condition(v: np.ndarray, c: dict) -> np.ndarray:
    op = c["op"]
    with np.errstate(invalid="ignore"):
        if op == ">":
            return v > float(c["v"])
        if op == "<":
            return v < float(c["v"])
        if op == "between":
            return (v >= float(c["v"])) & (v <= float(c.get("v2", c["v"])))
        w = max(1, int(round(float(c.get("hours", 1)) * 60)))
        s = pd.Series(v)
        if op == "rise":
            return (v - s.rolling(w + 1, min_periods=1).min().to_numpy()) > float(c["v"])
        if op == "fall":
            return (s.rolling(w + 1, min_periods=1).max().to_numpy() - v) > float(c["v"])
    raise ValueError(f"未知条件 {op}")


def evaluate(rules: list[dict], values: dict[str, np.ndarray], n: int, causal: bool = False) -> np.ndarray:
    """返回每分钟的工况 id（object 数组）。values 按参数名提供处理后的序列。"""
    out = np.full(n, NORM, dtype=object)
    decided = np.zeros(n, bool)
    for r in rules:
        conds = r.get("conds") or []
        if not conds:
            continue
        ms = []
        for c in conds:
            v = values.get(c["param"])
            ms.append(np.zeros(n, bool) if v is None else eval_condition(v, c))
        ok = np.logical_and.reduce(ms) if r.get("all", True) else np.logical_or.reduce(ms)
        dur = int(round(float(r.get("min_hours", 0)) * 60))
        if dur > 1:
            ok = _apply_duration(ok, dur, causal)
        take = ok & ~decided
        out[take] = r["regime"]
        decided |= ok
    return out


def _apply_duration(ok: np.ndarray, dur: int, causal: bool) -> np.ndarray:
    idx = np.arange(len(ok))
    if causal:
        # 截至当前已连续满足的分钟数 ≥ dur 才算
        last_false = np.maximum.accumulate(np.where(~ok, idx, -1))
        return ok & (idx - last_false >= dur)
    m = np.concatenate([[False], ok, [False]]).astype(np.int8)
    d = np.diff(m)
    a, b = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    res = np.zeros(len(ok), bool)
    for s, e in zip(a, b):
        if e - s >= dur:
            res[s:e] = True
    return res


def needed_params(rules: list[dict]) -> list[str]:
    out = []
    for r in rules:
        for c in r.get("conds") or []:
            if c["param"] not in out:
                out.append(c["param"])
    return out
