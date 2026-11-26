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


# ---------------------------------------------------------------------------
# 合并：手动标注 > 已确认的自动发现 > 规则
# ---------------------------------------------------------------------------
SRC_RULE, SRC_AUTO, SRC_MAN = "规则", "自动发现", "手动"


def to_index(idx: pd.DatetimeIndex, ts: str) -> int:
    return int(idx.searchsorted(pd.Timestamp(ts)))


def combine(rule_labels: np.ndarray, idx: pd.DatetimeIndex, auto: list[dict], manual: list[dict]):
    lab = rule_labels.copy()
    src = np.full(len(lab), SRC_RULE, dtype=object)
    for c in auto:
        if c.get("st") == "ok":
            a, b = to_index(idx, c["a"]), to_index(idx, c["b"])
            lab[a:b] = c["w"]
            src[a:b] = SRC_AUTO
    for m in manual:
        a, b = to_index(idx, m["a"]), to_index(idx, m["b"])
        lab[a:b] = m["w"]
        src[a:b] = SRC_MAN
    return lab, src


def runs_of(labels: np.ndarray) -> list[tuple[int, int, str]]:
    """连续相同标签的区间 [a, b)。"""
    n = len(labels)
    if n == 0:
        return []
    chg = np.flatnonzero(labels[1:] != labels[:-1]) + 1
    starts = np.concatenate([[0], chg])
    ends = np.concatenate([chg, [n]])
    return [(int(a), int(b), labels[a]) for a, b in zip(starts, ends)]


# ---------------------------------------------------------------------------
# 自动发现：关键参数的滚动变化量，用稳健标准差归一化，超过阈值的点合并成时段
# ---------------------------------------------------------------------------
SENS = {"high": 2.2, "mid": 3.0, "low": 4.0}
SENS_NAME = {"high": "高", "mid": "中", "low": "低"}


def auto_discover(values: dict[str, np.ndarray], units: dict[str, str], idx: pd.DatetimeIndex,
                  period: np.ndarray, sens: str, rule_labels: np.ndarray, names: dict[str, str]) -> list[dict]:
    """按小时均值计算 3 小时变化量 → 除以小时均值的稳健标准差 → 超过阈值（灵敏度系数 × 1.5：高 3.3 / 中 4.5 / 低 6）
    → 合并相邻时段（间隔 ≤ 3 h）。

    返回时段列表（时刻字符串）。只在所选时段（period）内寻找。描述文字由数据生成。
    """
    thr = SENS.get(sens, 3.0) * 1.5
    hours = pd.Series(np.arange(len(idx)), index=idx).resample("1h").first()
    hidx = hours.index
    hp = pd.Series(period, index=idx).resample("1h").mean().fillna(0).to_numpy() > 0.5
    flag = np.zeros(len(hidx), bool)
    best = [None] * len(hidx)
    hv = {}
    for k, v in values.items():
        h = pd.Series(v, index=idx).resample("1h").mean()
        hv[k] = h.to_numpy()
        d = (h - h.shift(3)).to_numpy()
        hv_ok = h.to_numpy()[hp]
        hv_ok = hv_ok[~np.isnan(hv_ok)]
        if not len(hv_ok):
            continue
        # 尺度取所选时段内小时均值水平的稳健标准差：只找相对正常波动明显的变化
        sd = float(np.median(np.abs(hv_ok - np.median(hv_ok))) * 1.4826) or 1e-9
        z = np.abs(d) / sd
        hit = (z > thr) & hp
        for i in np.flatnonzero(hit):
            if best[i] is None or z[i] > best[i][1]:
                best[i] = (k, z[i])
        flag |= hit
    segs, cur = [], None
    for i in np.flatnonzero(flag):
        if cur and i - cur[1] <= 3:
            cur[1] = i + 1
        else:
            cur = [i, i + 1]
            segs.append(cur)
    out = []
    for a, b in segs:
        a, b = max(0, a - 3), min(len(hidx), b + 2)
        ma, mb = int(hours.iloc[a]), int(hours.iloc[b - 1]) + 60
        mb = min(mb, len(idx))
        desc = []
        cnt = {}
        for i in range(a, b):
            if best[i]:
                cnt[best[i][0]] = cnt.get(best[i][0], 0) + 1
        for k in sorted(cnt, key=lambda x: -cnt[x])[:2]:
            seg = hv[k][a:b]
            seg = seg[~np.isnan(seg)]
            if not len(seg):
                continue
            ch = float(seg.max() - seg.min())
            first, last = seg[0], seg[-1]
            mid = seg[np.argmax(np.abs(seg - first))]
            if abs(last - first) < 0.35 * ch:
                how = "先变化后恢复"
            else:
                how = "上升" if last > first else "下降"
            dec = 0 if ch >= 100 else 1 if ch >= 1 else 2
            desc.append(f"{names.get(k, k)}{how}约 {ch:.{dec}f} {units.get(k, '')}".rstrip())
        lab = rule_labels[ma:mb]
        vals, counts = np.unique(lab, return_counts=True)
        rw = str(vals[np.argmax(counts)]) if len(vals) else NORM
        out.append({"a": f"{idx[ma]:%Y-%m-%d %H:%M}", "b": f"{idx[mb - 1] + pd.Timedelta(minutes=1):%Y-%m-%d %H:%M}",
                    "desc": "；".join(desc) or "多个参数同时波动", "rw": rw, "w": rw, "st": "new"})
    return out


# ---------------------------------------------------------------------------
# 运行周期：两次重置事件（烧焦 / 停炉）之间的连续运行段
# ---------------------------------------------------------------------------
CYCLE_EVENTS = {"both": ("burn", "stop"), "burn": ("burn",), "none": ()}
EVENT_NAME = {"burn": "烧焦", "stop": "停炉检修"}


def cycles(labels: np.ndarray, idx: pd.DatetimeIndex, mode: str, cycle0_start: str | None,
           end_ts: pd.Timestamp, min_event_hours: float = 6) -> list[dict]:
    """在 [数据起点, end_ts] 内按边界事件切分周期。事件 = 连续的烧焦（或停炉）标签段，
    短于 min_event_hours 的忽略；相邻事件间隔不足 1 天的合并（例如停炉后紧接烧焦）。"""
    evs = CYCLE_EVENTS.get(mode, ())
    if not evs:
        return []
    m_end = int(idx.searchsorted(end_ts))
    lab = labels[:m_end]
    events = []
    for a, b, w in runs_of(lab):
        if w in evs and (b - a) >= min_event_hours * 60:
            if events and a - events[-1][1] < 1440:
                events[-1][1] = b
                events[-1][2] = events[-1][2] if events[-1][2] == EVENT_NAME[w] else events[-1][2] + "、" + EVENT_NAME[w]
            else:
                events.append([a, b, EVENT_NAME[w]])
    start = pd.Timestamp(cycle0_start) if cycle0_start else idx[0]
    pre = start < idx[0]
    out = []
    cur = start
    for a, b, why in events:
        out.append({"start": cur, "end": idx[a] - pd.Timedelta(minutes=1), "why": why, "done": True, "pre": pre,
                    "event_start": idx[a], "event_end": idx[min(b, len(idx) - 1)]})
        cur = idx[b] if b < len(idx) else idx[-1]
        pre = False
    out.append({"start": cur, "end": end_ts, "why": "—", "done": False, "pre": pre,
                "event_start": None, "event_end": None})
    res = []
    for i, c in enumerate(out):
        res.append({"i": i + 1, "start": f"{c['start']:%Y-%m-%d %H:%M}", "end": f"{c['end']:%Y-%m-%d %H:%M}",
                    "days": round((c["end"] - c["start"]).total_seconds() / 86400, 1), "why": c["why"],
                    "done": c["done"], "pre": c["pre"],
                    "event": [f"{c['event_start']:%Y-%m-%d %H:%M}", f"{c['event_end']:%Y-%m-%d %H:%M}"]
                    if c["event_start"] is not None else None})
    return res
