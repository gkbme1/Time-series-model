"""数据处理规则 → 质量标记。

原则：
1. 原始数据永不修改：输入原始序列，输出"处理后的值 + 质量标记"，另存。
2. 只用当前及以前的数据：每个时刻的标记只取决于该时刻及之前的原始值。
   因此对整段历史一次算完，与上线时逐点计算结果相同（见 tests/test_no_future.py）。
3. 训练和上线共用本模块。

质量标记（uint8 位掩码，可叠加）：
    MISSING  原始值缺失
    RANGE    超出物理范围
    FROZEN   冻结（过去 N 分钟变化小于阈值）
    SPIKE    尖峰（偏离过去若干分钟中位数超过阈值）
    FILLED   已用上一个有效值补齐
    UNUSABLE 最终不能使用（不参与训练 / 上线时视为输入无效）
    DECOKE   处在烧焦期间（仅标记，是否使用由规则和分工况决定）
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

MISSING, RANGE, FROZEN, SPIKE, FILLED, UNUSABLE, DECOKE = 1, 2, 4, 8, 16, 32, 64
FLAG_NAMES = {MISSING: "缺失", RANGE: "超范围", FROZEN: "冻结", SPIKE: "尖峰", FILLED: "已补齐",
              UNUSABLE: "不可用", DECOKE: "烧焦期间"}

# 每类规则"触发后"的处理方式，序号与界面下拉框一致（文案沿用原型）
ACTIONS = {
    "range": ["标为无效，按缺失处理", "仅标记，照常使用"],
    "freeze": ["按缺失处理：短的补齐，长的该时段不参与训练", "整段不参与训练", "仅标记，照常使用"],
    "spike": ["剔除该点，按缺失处理", "仅标记，照常使用"],
    "fill": ["短缺失用历史值补齐，更长的该时段不参与训练", "一律不参与训练"],
    "decoke": ["保留，交给\"分工况\"决定是否使用", "不参与训练"],
    "smooth": ["用平滑后的值替换原值"],
}
RULE_TITLES = {"range": "物理范围", "freeze": "冻结检测", "spike": "尖峰处理", "fill": "缺失补齐",
               "decoke": "烧焦期间", "smooth": "平滑"}
RULE_ORDER = ["range", "freeze", "spike", "smooth", "fill", "decoke"]


def nice(x: float) -> float:
    """向上取一个好读的数：1、1.2、1.5、2、2.5、3、4、5、6、8 × 10^k。"""
    if not np.isfinite(x) or x <= 0:
        return 0.0
    e = np.floor(np.log10(x))
    m = x / 10 ** e
    for c in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m <= c:
            return float(round(c * 10 ** e, 6))
    return float(x)


def robust_scales(x: pd.Series) -> dict:
    """由历史数据估计默认阈值用的尺度（只用于给出默认值，用户可改）：
    - hampel: 当前值偏离过去 5 分钟中位数的 99.9% 分位数（尖峰阈值取其 1.5 倍）
    - diff:   1 分钟差分的稳健标准差（冻结阈值取其 5%）
    """
    v = x.dropna()
    if len(v) < 1000:
        return {"hampel": 1.0, "diff": 0.1}
    med = x.shift(1).rolling(5, min_periods=2).median()
    dev = (x - med).abs().dropna()
    d = v.diff().dropna()
    mad = float(1.4826 * np.nanmedian(np.abs(d - np.nanmedian(d))))
    return {"hampel": float(np.nanquantile(dev, 0.999)) or 1.0, "diff": mad or 0.01}


def default_rules(tag_row: dict, scales: dict, override: dict | None = None, base: dict | None = None) -> dict:
    """一个参数的默认规则。物理范围默认取参数表中的仪表量程（标"来自本体"）。"""
    base = base or {}
    lo, hi = float(tag_row["range_lo"]), float(tag_row["range_hi"])
    r = {
        "range": {"on": True, "lo": lo, "hi": hi, "src": "本体", "action": 0},
        "freeze": {"on": True, "minutes": 30, "tol": max(0.001, nice(0.05 * scales["diff"])), "action": 0},
        "spike": {"on": True, "window": 5, "jump": nice(1.5 * scales["hampel"]), "action": 0},
        "fill": {"on": True, "max_minutes": 2, "action": 0},
        "decoke": {"on": True, "action": 0},
    }
    for k, v in base.items():
        if k in r and isinstance(v, dict):
            r[k].update({kk: vv for kk, vv in v.items() if kk in ("on", "action", "minutes", "max_minutes", "window")})
    for k, v in (override or {}).items():
        r.setdefault(k, {"on": False, "action": 0})
        r[k].update(v)
    return r


def describe(kind: str, r: dict, unit: str) -> str:
    """规则的一句话说明（界面显示）。"""
    u = f" {unit}" if unit else ""
    if kind == "range":
        return f"{fmt(r['lo'])} ~ {fmt(r['hi'])}{u}，超出标记无效"
    if kind == "freeze":
        return f"{r['minutes']} min 变化 < {fmt(r['tol'])}{u} 标记冻结"
    if kind == "spike":
        return f"偏离过去 {r['window']} min 中位数 > {fmt(r['jump'])}{u} → 剔除（只看历史）"
    if kind == "fill":
        return f"≤ {r['max_minutes']} min 用上一个有效值补齐（仅用历史值）"
    if kind == "decoke":
        return "原料流量 < 5 t/h 且烧焦空气阀开度 > 10% 的时段"
    if kind == "smooth":
        return f"{r.get('minutes', 5)} min 滑动中位数（只往回算）"
    return ""


def fmt(v) -> str:
    v = float(v)
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def rule_hash(r: dict) -> str:
    return hashlib.md5(json.dumps(r, sort_keys=True).encode()).hexdigest()[:12]


@dataclass
class QualityResult:
    value: np.ndarray  # 处理后的值；不可用处为 NaN
    flags: np.ndarray  # uint8 位掩码

    @property
    def usable(self) -> np.ndarray:
        return (self.flags & UNUSABLE) == 0


def apply_rules(raw: np.ndarray, rules: dict, decoke_mask: np.ndarray | None = None) -> QualityResult:
    """对一个参数的原始序列（1 分钟等间隔）应用处理规则。只用当前及以前的数据。"""
    x = np.asarray(raw, dtype="float64")
    n = len(x)
    flags = np.zeros(n, np.uint8)
    missing = np.isnan(x)
    flags[missing] |= MISSING
    invalid = missing.copy()  # 需要按缺失处理的点
    hard_bad = np.zeros(n, bool)  # 直接不参与训练的点（不补齐）

    # 物理范围
    r = rules.get("range", {})
    if r.get("on"):
        oor = ~missing & ((x < r["lo"]) | (x > r["hi"]))
        flags[oor] |= RANGE
        if r.get("action", 0) == 0:
            invalid |= oor

    s = pd.Series(np.where(invalid, np.nan, x))

    # 冻结：过去 minutes 分钟全部有值，且最大值 - 最小值 ≤ 阈值
    r = rules.get("freeze", {})
    if r.get("on"):
        w = int(r.get("minutes", 30))
        raw_s = pd.Series(x)
        rng = raw_s.rolling(w, min_periods=w).max() - raw_s.rolling(w, min_periods=w).min()
        frozen = (rng <= float(r.get("tol", 0.001))).to_numpy() & ~missing
        flags[frozen] |= FROZEN
        act = r.get("action", 0)
        if act == 0:
            invalid |= frozen
        elif act == 1:
            invalid |= frozen
            hard_bad |= frozen

    # 尖峰：与过去 window 分钟（不含当前点）有效值的中位数比较
    r = rules.get("spike", {})
    if r.get("on") and r.get("jump", 0) > 0:
        w = int(r.get("window", 5))
        med = s.shift(1).rolling(w, min_periods=max(2, w // 2)).median().to_numpy()
        spike = ~invalid & ~np.isnan(med) & (np.abs(x - med) > float(r["jump"]))
        flags[spike] |= SPIKE
        if r.get("action", 0) == 0:
            invalid |= spike

    val = np.where(invalid, np.nan, x)

    # 平滑（可选）：只往回算的滑动中位数
    r = rules.get("smooth", {})
    if r.get("on"):
        w = int(r.get("minutes", 5))
        val = np.array(pd.Series(val).rolling(w, min_periods=1).median(), dtype="float64")
        val[invalid] = np.nan

    # 缺失补齐：只用上一个有效值；连续无效时长超过上限的部分不可用
    r = rules.get("fill", {})
    idx = np.arange(n)
    last_valid = np.maximum.accumulate(np.where(~invalid, idx, -1))
    gap_len = idx - last_valid  # 有效点为 0
    if r.get("on") and r.get("action", 0) == 0:
        m = int(r.get("max_minutes", 2))
        can_fill = invalid & ~hard_bad & (last_valid >= 0) & (gap_len <= m)
        val = val.copy()
        val[can_fill] = val[last_valid[can_fill]]
        flags[can_fill] |= FILLED
        unusable = invalid & ~can_fill
    else:
        unusable = invalid.copy()

    # 烧焦期间
    r = rules.get("decoke", {})
    if decoke_mask is not None and r.get("on"):
        flags[decoke_mask] |= DECOKE
        if r.get("action", 0) == 1:
            unusable |= decoke_mask

    flags[unusable] |= UNUSABLE
    val = np.where(unusable, np.nan, val)
    return QualityResult(val, flags)


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """布尔序列中连续 True 的区间 [a, b)。"""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(np.int8))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist()))


def diagnose(res: QualityResult, period: np.ndarray) -> dict:
    """所选时段（period 为布尔掩码）内的质量诊断。"""
    f = res.flags[period]
    n = max(1, len(f))
    compl = 1 - np.count_nonzero(f & MISSING) / n
    usable = 1 - np.count_nonzero(f & UNUSABLE) / n
    fr = len(runs((f & FROZEN) > 0))
    sp = int(np.count_nonzero(f & SPIKE))
    oor = len(runs((f & RANGE) > 0))
    grade = "A" if usable >= 0.98 else "B" if usable >= 0.93 else "C"
    return {"completeness": round(compl * 100, 1), "usable": round(usable * 100, 1), "freeze": fr, "spike": sp,
            "range": oor, "grade": grade}


def merged_rules(draft: dict, tag: str) -> dict:
    return copy.deepcopy(draft.get(tag, {}))
