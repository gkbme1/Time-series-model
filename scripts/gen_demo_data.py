#!/usr/bin/env python
"""示例数据生成脚本。

生成一套有物理规律的乙烯裂解炉模拟数据，保证 Demo 能端到端跑通：

- data/tags.csv                     参数表（代替本体）
- data/timeseries/BA-101.parquet    1 号炉宽表，1 分钟一个点，2026-01-01 至 2026-11-30
- data/timeseries/BA-104.parquet    4 号炉（跨炉检验用），同名参数、不同位号
- data/calib/*.csv + _sources.json  校核数据（红外测温，每班一次，带人工误差）
- data/lag_refs/*.json              外部滞后参考（APC 模型参数、阶跃测试）
- data/templates/*.json             场景模板
- data/_truth/*.parquet             生成器内部真值（只给测试和验收用，平台界面不读）

固定随机种子，重复运行结果一致。用法：

    python scripts/gen_demo_data.py            # 输出到 ./data
    python scripts/gen_demo_data.py --out DIR
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import lfilter

START = pd.Timestamp("2026-01-01 00:00")
END = pd.Timestamp("2026-11-30 23:59")
IDX = pd.date_range(START, END, freq="min")
N = len(IDX)


def mi(ts: str) -> int:
    """时间字符串 → 分钟序号（可以为负，表示早于数据起点）。"""
    return int((pd.Timestamp(ts) - START).total_seconds() // 60)


# ---------------------------------------------------------------------------
# 参数清单（以原型"选择参数"弹窗的设备树为准，补充烧焦空气阀开度）
# 位号中的 {n} 为炉号：1 号炉 → FI-1021，4 号炉 → FI-4021
# ---------------------------------------------------------------------------
TAGS = [
    # tag, name, device, part, desc, unit, period, lo, hi, rec, target
    ("FI-{n}021", "燃料气流量", "F", "燃料气系统", "燃料气总管流量", "Nm³/h", "5 s", 0, 18000, 1, 0),
    ("PI-{n}022", "燃料气压力", "F", "燃料气系统", "燃料气总管压力", "kPa", "5 s", 0, 400, 0, 0),
    ("TI-{n}023", "燃料气温度", "F", "燃料气系统", "燃料气总管温度", "℃", "5 s", -20, 100, 0, 0),
    ("FI-{n}001", "原料流量", "F", "进料与稀释蒸汽", "石脑油进料流量", "t/h", "5 s", 0, 50, 1, 0),
    ("FI-{n}012", "稀释蒸汽流量", "F", "进料与稀释蒸汽", "稀释蒸汽总管流量", "t/h", "5 s", 0, 22, 1, 0),
    ("AI-{n}005", "原料密度", "F", "进料与稀释蒸汽", "进料密度分析仪", "kg/m³", "1 min", 600, 800, 0, 0),
    ("HV-{n}015", "烧焦空气阀开度", "F", "进料与稀释蒸汽", "烧焦空气调节阀开度", "%", "5 s", 0, 100, 0, 0),
    ("TI-{n}035", "横跨段温度", "F", "辐射段", "辐射段入口温度 XOT", "℃", "5 s", 500, 700, 1, 0),
    ("TI-{n}052", "炉膛温度", "F", "辐射段", "辐射段炉膛温度（西）", "℃", "5 s", 700, 1250, 1, 0),
    ("TI-{n}053", "炉膛温度（东侧）", "F", "辐射段", "辐射段炉膛温度（东）", "℃", "5 s", 700, 1250, 0, 0),
    ("TMT-{n}01", "炉管壁温", "F", "辐射段", "出口段 1# 管表面热电偶", "℃", "1 min", 850, 1130, 0, 1),
    ("TI-{n}041", "出口温度", "F", "炉出口", "裂解炉出口温度 COT", "℃", "5 s", 750, 900, 1, 0),
    ("PDI-{n}063", "炉管压降", "F", "炉出口", "炉管进出口压差", "kPa", "5 s", 0, 400, 1, 0),
    ("PI-{n}064", "出口压力", "F", "炉出口", "裂解炉出口压力", "kPa", "5 s", 0, 200, 0, 0),
    ("AI-{n}071", "烟气氧含量", "F", "烟道", "烟道氧分析仪", "%", "10 s", 0, 10, 1, 0),
    ("TI-{n}080", "排烟温度", "F", "烟道", "烟道出口温度", "℃", "5 s", 0, 300, 0, 0),
    ("PI-{n}082", "炉膛负压", "F", "烟道", "辐射段顶部负压", "Pa", "5 s", -200, 50, 0, 0),
    ("TI-{n}091", "急冷锅炉出口温度", "E", "", "TLE 出口裂解气温度", "℃", "5 s", 200, 600, 0, 0),
    ("PI-{n}092", "汽包压力", "E", "", "高压汽包压力", "MPa", "5 s", 0, 15, 0, 0),
    ("FI-{n}093", "锅炉给水流量", "E", "", "TLE 给水流量", "t/h", "5 s", 0, 60, 0, 0),
    ("HC-{n}101", "底部烧嘴开度", "B", "", "底部烧嘴调节阀开度", "%", "5 s", 0, 100, 0, 0),
    ("HC-{n}102", "侧壁烧嘴开度", "B", "", "侧壁烧嘴调节阀开度", "%", "5 s", 0, 100, 0, 0),
]
DEVICES = {
    "F": "{k}号裂解炉 BA-10{k}",
    "E": "急冷锅炉 E-10{k}",
    "B": "燃烧器组 BN-10{k}",
}

TUBE_OFFSET = {1: 3.0, 2: -2.5, 3: -5.0, 4: 1.5, 5: 6.5, 6: -1.0, 7: 4.0, 8: -6.0}  # 各管与平均的固定偏差
INSTRUMENTS = {"红外高温计 IR-03": 2.0, "红外高温计 IR-05": -1.5}
OPERATORS = {"张工": 1.0, "刘工": -1.5, "陈工": 0.5, "王师傅": -0.5}


@dataclass
class FurnaceCfg:
    unit: str  # BA-101
    k: int  # 炉号
    seed: int
    cycle0_start: str  # 第一个周期的起点（可早于数据起点）
    stops: list  # [(start, end, reason)]
    decokes: list  # [(start, end)]
    high_loads: list  # [(start, end)]
    feed_switches: list  # [(start, density_after)] 过渡 5 h
    fuel_dist: list  # [(start, end)] 燃料气总管压力扰动
    fuel_push: list = field(default_factory=list)  # [(start, end, +比例)] 炉膛温度偏高事件
    low_ratio: list = field(default_factory=list)  # [(start, end, ratio)] 稀释比异常偏低
    base_tmt: float = 1040.0
    g_fuel: float = 0.015  # ℃ / (Nm³/h)
    g_feed: float = 1.6  # ℃ / (t/h)
    g_steam: float = 0.5
    coke_rate: float = 0.20  # ℃/天（周期初）
    coke_acc: float = 0.0025  # ℃/天²
    tc_offset: float = 3.0  # 1# 管热电偶与出口段平均的偏差


DCS_GAP = ("2026-06-14 00:00", "2026-06-16 23:59")

BA101 = FurnaceCfg(
    unit="BA-101", k=1, seed=101, cycle0_start="2025-12-13 00:00",
    stops=[("2026-03-08 00:00", "2026-03-21 23:59", "停炉检修")],
    decokes=[("2026-05-28 06:00", "2026-05-30 18:00"), ("2026-08-20 06:00", "2026-08-22 02:00"),
             ("2026-10-12 08:00", "2026-10-14 00:00")],
    high_loads=[("2026-07-18 00:00", "2026-07-30 00:00"), ("2026-09-03 00:00", "2026-09-12 00:00"),
                ("2026-11-10 08:00", "2026-11-14 20:00")],
    feed_switches=[("2026-06-25 09:00", 710.0), ("2026-07-12 14:00", 702.0),
                   ("2026-09-18 10:00", 712.0), ("2026-10-05 15:00", 702.0),
                   ("2026-11-22 16:10", 713.0), ("2026-11-27 09:00", 702.0)],
    fuel_dist=[("2026-09-25 03:00", "2026-09-25 06:00")],
    fuel_push=[("2026-11-21 02:10", "2026-11-21 04:20", 0.075)],
    low_ratio=[("2026-11-12 13:00", "2026-11-12 13:40", 0.31)],
)

BA104 = FurnaceCfg(
    unit="BA-104", k=4, seed=104, cycle0_start="2025-12-01 00:00",
    stops=[],
    decokes=[("2026-02-14 06:00", "2026-02-16 12:00"), ("2026-04-28 08:00", "2026-04-30 20:00"),
             ("2026-07-15 06:00", "2026-07-17 08:00"), ("2026-09-28 06:00", "2026-09-30 10:00")],
    high_loads=[("2026-08-05 00:00", "2026-08-14 00:00"), ("2026-11-03 08:00", "2026-11-08 20:00")],
    feed_switches=[("2026-06-10 09:00", 711.0), ("2026-06-25 14:00", 702.0)],
    fuel_dist=[("2026-10-18 10:00", "2026-10-18 12:30")],
    base_tmt=1047.0, g_fuel=0.0158, g_feed=1.75, g_steam=0.55, coke_rate=0.22, coke_acc=0.0030,
    tc_offset=2.0,
)

# 数据质量问题配置：长缺失(段数, 最短h, 最长h)、短缺失段数、冻结(段数, 最短h, 最长h)、尖峰(个数, 幅度)、超量程(次数, 最短min, 最长min, 读数)
QUALITY = {
    "FI-{n}021": dict(gaps=(1, 2, 6), short=40, freeze=(1, 1, 2), spikes=(12, 2500)),
    "PI-{n}022": dict(gaps=(2, 3, 10), short=40, freeze=(1, 2, 4), spikes=(6, 25)),
    "TI-{n}023": dict(gaps=(3, 4, 12), short=40, freeze=(2, 2, 6), spikes=(4, 8)),
    "FI-{n}001": dict(gaps=(1, 2, 5), short=30, freeze=(1, 1, 2), spikes=(8, 6), oor=(3, 3, 10, -4.0)),
    "FI-{n}012": dict(gaps=(2, 3, 12), short=40, freeze=(2, 1, 3), spikes=(6, 3)),
    "AI-{n}005": dict(gaps=(10, 12, 40), short=60, freeze=(5, 4, 16), spikes=(6, 12)),
    "HV-{n}015": dict(gaps=(1, 2, 6), short=20),
    "TI-{n}035": dict(gaps=(1, 2, 4), short=30, freeze=(1, 1, 2), spikes=(6, 25)),
    "TI-{n}052": dict(gaps=(4, 6, 20), short=80, freeze=(4, 2, 6), spikes=(40, 45)),
    "TI-{n}053": dict(gaps=(5, 6, 24), short=80, freeze=(3, 2, 8), spikes=(30, 45)),
    "TI-{n}041": dict(gaps=(1, 1, 3), short=25, freeze=(1, 1, 2), spikes=(10, 20), oor=(3, 5, 30, 999.0)),
    "PDI-{n}063": dict(gaps=(8, 6, 30), short=60, freeze=(3, 3, 8), spikes=(20, 32)),
    "PI-{n}064": dict(gaps=(2, 3, 10), short=40, freeze=(1, 1, 3), spikes=(5, 8)),
    "AI-{n}071": dict(gaps=(12, 12, 48), short=80, freeze=(12, 3, 12), spikes=(15, 1.5), oor=(5, 30, 60, 0.0)),
    "TI-{n}080": dict(gaps=(2, 2, 8), short=30, freeze=(1, 2, 4), spikes=(5, 15)),
    "PI-{n}082": dict(gaps=(3, 3, 10), short=40, freeze=(2, 2, 6), spikes=(8, 20)),
    "TI-{n}091": dict(gaps=(2, 2, 8), short=30, freeze=(1, 2, 4), spikes=(6, 20)),
    "PI-{n}092": dict(gaps=(1, 2, 4), short=20, freeze=(1, 1, 3), spikes=(4, 0.8)),
    "FI-{n}093": dict(gaps=(2, 3, 10), short=40, freeze=(1, 2, 4), spikes=(6, 4)),
    "HC-{n}101": dict(gaps=(2, 2, 8), short=30, freeze=(1, 2, 4), spikes=(4, 8)),
    "HC-{n}102": dict(gaps=(2, 2, 8), short=30, freeze=(1, 2, 4), spikes=(4, 8)),
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def fopdt(u: np.ndarray, dead: int, tau: float) -> np.ndarray:
    """一阶惯性 + 纯滞后（离散，1 分钟步长），初值取 u[0] 的稳态。"""
    if dead > 0:
        u = np.concatenate([np.full(dead, u[0]), u[:-dead]])
    a = np.exp(-1.0 / tau)
    return lfilter([1 - a], [1, -a], u - u[0]) + u[0]


def lowpass(u: np.ndarray, tau: float) -> np.ndarray:
    return fopdt(u, 0, tau)


def ar1(rng, n: int, sigma: float, tc: float) -> np.ndarray:
    """平稳 AR(1) 噪声，标准差 sigma，相关时间 tc 分钟。"""
    a = np.exp(-1.0 / tc)
    x = lfilter([1.0], [1, -a], rng.normal(scale=sigma * np.sqrt(1 - a * a), size=n))
    return x


def piecewise(rng, n: int, mean_hours: float, draw) -> np.ndarray:
    """分段常数过程：段长服从指数分布，每段取值由 draw() 给出。"""
    out = np.empty(n)
    i = 0
    while i < n:
        L = max(30, int(rng.exponential(mean_hours * 60)))
        out[i:i + L] = draw()
        i += L
    return out


def mask_of(intervals, n=N) -> np.ndarray:
    m = np.zeros(n, bool)
    for iv in intervals:
        a, b = mi(iv[0]), mi(iv[1]) + 1
        m[max(0, a):max(0, min(n, b))] = True
    return m


# ---------------------------------------------------------------------------
# 单台炉的过程模拟
# ---------------------------------------------------------------------------
def simulate(cfg: FurnaceCfg):
    rng = np.random.default_rng(cfg.seed)
    t = np.arange(N)

    # ---- 运行状态：0 运行 / 1 升温开炉 / 2 烧焦 / 3 停炉 ----
    mode = np.zeros(N, np.int8)
    stop_m = mask_of(cfg.stops)
    dec_m = mask_of(cfg.decokes)
    mode[dec_m] = 2
    mode[stop_m] = 3
    ramp_frac = np.ones(N)
    cycle_starts = [mi(cfg.cycle0_start)]
    for iv in sorted([(s, e) for s, e, *_ in cfg.stops] + list(cfg.decokes)):
        end = mi(iv[1]) + 1
        cycle_starts.append(end)
        ramp = np.arange(end, min(N, end + 12 * 60))
        mode[ramp] = 1
        ramp_frac[ramp] = (ramp - end) / (12 * 60)
    running = mode <= 1

    # 本周期运行天数（烧焦或停炉期间为 0）
    cyc_id = np.searchsorted(np.array(cycle_starts), t, side="right")  # 1 起
    cyc_start_arr = np.array(cycle_starts)[cyc_id - 1]
    days = np.where(running, (t - cyc_start_arr) / 1440.0, 0.0)

    # ---- 负荷与原料 ----
    hi_m = mask_of(cfg.high_loads)
    feed_sp = piecewise(rng, N, 30, lambda: np.clip(rng.normal(38, 0.7), 36.5, 39.5))
    feed_sp = np.where(hi_m, 42.0 + rng.normal(0, 0.15), feed_sp)
    feed_sp = lowpass(feed_sp, 90)  # 负荷调整约 1.5 h 完成
    feed_sp = np.where(mode == 1, feed_sp * ramp_frac, feed_sp)
    feed_sp = np.where(mode == 2, 0.3, feed_sp)
    feed_sp = np.where(mode == 3, 0.0, feed_sp)
    feed = lowpass(feed_sp, 15) + rng.normal(0, 0.12, N) + ar1(rng, N, 0.12, 20)
    feed = np.where(mode == 3, rng.normal(0, 0.02, N).clip(0), feed)
    feed = np.where(mode == 2, np.abs(rng.normal(0.3, 0.12, N)), feed)

    density = np.full(N, 702.0)
    cur = 702.0
    for s, after in sorted(cfg.feed_switches):
        a = mi(s)
        ramp = np.arange(a, min(N, a + 300))
        density[ramp] = cur + (after - cur) * (ramp - a) / 300
        density[a + 300:] = after
        cur = after
    heavy = np.clip((density - 702) / 10, 0, 1.2)
    density_meas = density + ar1(rng, N, 0.6, 60) + rng.normal(0, 0.5, N)

    # 稀释比与稀释蒸汽
    ratio = piecewise(rng, N, 36, lambda: np.clip(rng.normal(0.39, 0.012), 0.36, 0.42))
    for s, e, r in cfg.low_ratio:
        ratio[mask_of([(s, e)])] = r
    ratio = lowpass(ratio, 10)
    steam = ratio * feed_sp + rng.normal(0, 0.06, N) + ar1(rng, N, 0.06, 30)
    steam = np.where(mode == 2, 9.5 + ar1(rng, N, 0.3, 60), steam)
    steam = np.where(mode == 3, np.abs(rng.normal(0, 0.02, N)), steam)
    steam = np.where(mode == 1, np.maximum(steam, 9.5 * (1 - ramp_frac) + ratio * feed_sp), steam)

    # ---- 结焦：按运行天数累积，重原料和高负荷加快，烧焦/停炉后清零 ----
    rate = (cfg.coke_rate + 2 * cfg.coke_acc * days) * (1 + 0.5 * heavy) * np.clip(feed / 38, 0, 1.3)
    rate = np.where(running, rate, 0.0) / 1440.0
    coke = np.zeros(N)
    pre = 0.0
    if cycle_starts[0] < 0:  # 第一个周期早于数据起点：补算之前的结焦
        d0 = -cycle_starts[0] / 1440
        pre = cfg.coke_rate * d0 + cfg.coke_acc * d0 * d0
    for i, cs in enumerate(cycle_starts):
        a = max(0, cs)
        b = cycle_starts[i + 1] if i + 1 < len(cycle_starts) else N
        if b <= 0:
            continue
        seg = np.cumsum(rate[a:b])
        coke[a:b] = seg + (pre if i == 0 else 0.0)
    coke = np.where(running, coke, 0.0)

    # ---- 燃料气：前馈 + 操作扰动（独立于原料，保证响应方向可学） ----
    cot_sp = np.where(hi_m, 852.0, 845.0) - 2.0 * heavy
    cot_sp = lowpass(cot_sp, 60)
    fuel_ff = 2900 + 240 * feed_sp + 90 * (cot_sp - 845) + 4.0 * coke + 25 * (ratio - 0.39) * 100
    moves = piecewise(rng, N, 3, lambda: np.clip(rng.normal(0, 0.028), -0.06, 0.06))
    moves = lowpass(moves, 3)
    fast = ar1(rng, N, 0.010, 15)
    fuel = fuel_ff * (1 + moves + fast)
    for s, e, p in cfg.fuel_push:
        mm = mask_of([(s, e)])
        fuel = np.where(mm, fuel * (1 + p), fuel)
    fuel_p = 185 - 0.0015 * (fuel - 12100)
    for s, e in cfg.fuel_dist:  # 燃料气总管压力扰动：压力骤降，流量先掉后由控制器拉回
        a, b = mi(s), mi(e) + 1
        k = np.arange(a, b)
        prog = (k - a) / (b - a)
        drop = np.exp(-prog * 3.0) * (1 - np.exp(-(k - a) / 3))
        fuel[a:b] *= 1 - 0.24 * drop
        fuel_p[a:b] -= 35 * drop
    fuel = np.where(mode == 1, 7000 + (fuel - 7000) * ramp_frac, fuel)
    fuel = np.where(mode == 2, 6800 + ar1(rng, N, 120, 30), fuel)
    fuel = np.where(mode == 3, np.abs(rng.normal(0, 3, N)), fuel)
    fuel = fuel + rng.normal(0, 35, N)
    fuel_p = np.where(mode == 3, 5 + rng.normal(0, 0.5, N), fuel_p) + ar1(rng, N, 1.2, 30) + rng.normal(0, 0.6, N)

    # ---- 动态响应 ----
    uf, uq, us = fuel - 12100, feed - 38, steam - 14.8
    runw = lowpass(running.astype(float), 60)  # 运行与烧焦/停炉之间的平滑过渡

    def blend(run_val, dec_val, stop_val, tau=120):
        other = np.where(mode == 3, stop_val, dec_val)
        other = lowpass(np.where(running, dec_val, other), tau)
        return runw * run_val + (1 - runw) * other

    # 炉管壁温（出口段平均）真值：燃料气 5 min 纯滞后 + 6 min 时间常数
    tmt_run = (cfg.base_tmt + cfg.g_fuel * fopdt(uf, 5, 6) - cfg.g_feed * fopdt(uq, 7, 9)
               - cfg.g_steam * fopdt(us, 6, 8) + coke + 6.0 * heavy + ar1(rng, N, 1.5, 40))
    tmt = blend(tmt_run, 905.0, 140.0)

    cot = blend(845 + 0.010 * fopdt(uf, 1, 3) - 2.0 * fopdt(uq, 2, 4) - 0.6 * fopdt(us, 2, 4)
                - 0.04 * coke + ar1(rng, N, 0.6, 20) + rng.normal(0, 0.35, N), 790.0, 60.0)
    furn_w = blend(1165 + 0.028 * fopdt(uf, 1, 4) - 0.8 * fopdt(uq, 2, 5) + 0.35 * coke
                   + ar1(rng, N, 1.5, 30), 1080.0, 150.0) + rng.normal(0, 2.2, N)
    furn_e = furn_w - 7 + ar1(rng, N, 2.0, 60) + rng.normal(0, 2.0, N)
    xot = blend(612 + 0.004 * fopdt(uf, 2, 8) - 1.2 * fopdt(uq, 3, 8) - 3 * heavy
                + ar1(rng, N, 0.8, 40), 560.0, 80.0) + rng.normal(0, 0.4, N)
    pdi = 0.0574 * (feed + steam) ** 2 * (1 + coke / 90) + rng.normal(0, 1.2, N) + ar1(rng, N, 1.0, 60)
    pdi = np.where(mode == 2, 25 + ar1(rng, N, 2, 60), pdi)
    pdi = np.where(mode == 3, np.abs(rng.normal(0, 0.4, N)), pdi)
    p_out = 60 + 0.0045 * (feed + steam) ** 2 + ar1(rng, N, 0.5, 60) + rng.normal(0, 0.3, N)
    p_out = np.where(mode == 3, 2 + rng.normal(0, 0.3, N), p_out)
    fuel_t = (30 + 14 * np.sin(2 * np.pi * (t / 1440 - 105) / 365) + 4 * np.sin(2 * np.pi * (t / 1440 - 0.375))
              + ar1(rng, N, 0.8, 120) + rng.normal(0, 0.3, N))
    o2 = 2.4 - 0.00012 * fopdt(uf - lowpass(uf, 240), 0, 2) + ar1(rng, N, 0.25, 30) + rng.normal(0, 0.06, N)
    o2 = np.where(mode == 2, 8.0 + ar1(rng, N, 0.4, 60), o2)
    o2 = np.where(mode == 3, 20.9 + rng.normal(0, 0.05, N), o2)
    flue = blend(128 + 0.004 * uf + 0.3 * coke + ar1(rng, N, 1.5, 120), 110.0, 40.0) + rng.normal(0, 0.6, N)
    draft = np.where(mode == 3, -5.0, -45.0) + ar1(rng, N, 3.0, 20) + rng.normal(0, 1.5, N)
    tle = blend(392 + 1.0 * uq + 0.9 * coke + ar1(rng, N, 1.5, 60), 300.0, 60.0) + rng.normal(0, 0.8, N)
    drum = blend(11.2 + 0.05 * uq + ar1(rng, N, 0.08, 60), 10.5, 0.3) + rng.normal(0, 0.02, N)
    bfw = np.where(running, 1.08 * feed, np.where(mode == 2, 8.0, 0.0)) + rng.normal(0, 0.4, N)
    bfw = np.maximum(bfw, 0)
    burner_b = np.where(mode == 3, 0.0, 62 * fuel / 12100) + ar1(rng, N, 0.8, 30)
    burner_s = np.where(mode == 3, 0.0, 48 * fuel / 12100) + ar1(rng, N, 0.8, 30)
    air = np.where(mode == 2, 35 + ar1(rng, N, 2.0, 60), 0.0) + np.abs(rng.normal(0, 0.1, N))

    # ---- 1# 管热电偶：周期后段漂移 → 断偶，烧焦/停炉后更换 ----
    tc = tmt + cfg.tc_offset + rng.normal(0, 0.6, N)
    tc_bad = np.zeros(N, bool)
    for i, cs in enumerate(cycle_starts):
        b = cycle_starts[i + 1] if i + 1 < len(cycle_starts) else N
        fail_day = rng.uniform(52, 70)
        f0 = cs + int(fail_day * 1440)
        if f0 >= b:
            continue
        drift0 = f0 - int(rng.uniform(3, 6) * 1440)
        k = np.arange(max(0, drift0), min(N, f0))
        if len(k):
            tc[k] -= 8 * (k - drift0) / max(1, f0 - drift0)
        k = np.arange(max(0, f0), min(N, b))
        tc_bad[k] = True
        # 断偶期间偶尔回读 1370 ℃（开路满量程）
        burst = rng.choice(k, size=min(len(k), 25), replace=False) if len(k) else []
        tc[burst] = 1370.0
    nan_tc = tc_bad & (tc != 1370.0)
    tc[nan_tc] = np.nan

    cols = {
        "FI-{n}021": fuel, "PI-{n}022": fuel_p, "TI-{n}023": fuel_t, "FI-{n}001": feed, "FI-{n}012": steam,
        "AI-{n}005": density_meas, "HV-{n}015": air, "TI-{n}035": xot, "TI-{n}052": furn_w, "TI-{n}053": furn_e,
        "TMT-{n}01": tc, "TI-{n}041": cot, "PDI-{n}063": pdi, "PI-{n}064": p_out, "AI-{n}071": o2,
        "TI-{n}080": flue, "PI-{n}082": draft, "TI-{n}091": tle, "PI-{n}092": drum, "FI-{n}093": bfw,
        "HC-{n}101": burner_b, "HC-{n}102": burner_s,
    }
    for arr in (fuel, feed, steam, bfw, burner_b, burner_s, air):
        np.maximum(arr, 0, out=arr)
    log = []
    df = {}
    for tpl, arr in cols.items():
        arr = np.asarray(arr, float).copy()
        if tpl in QUALITY:
            arr = inject_quality(rng, arr, QUALITY[tpl], tpl.format(n=cfg.k), log, mode)
        arr[mask_of([DCS_GAP])] = np.nan
        df[tpl.format(n=cfg.k)] = arr
    decimals = 3
    wide = pd.DataFrame(df, index=IDX).round(decimals)
    wide.index.name = "timestamp"

    # 真实工况（供阶段 2 验收对照）
    regime = np.full(N, "正常稳定", dtype=object)
    regime[hi_m & running] = "高负荷"
    sw_m = np.zeros(N, bool)
    for s, _ in cfg.feed_switches:
        sw_m[mi(s):mi(s) + 300] = True
    regime[sw_m] = "原料切换"
    regime[mode == 1] = "升温 / 开炉"
    regime[mode == 2] = "烧焦"
    regime[mode == 3] = "停炉"
    truth = pd.DataFrame({
        "tmt_avg": tmt.round(3), "mode": mode, "regime": regime, "cycle": cyc_id,
        "cycle_days": days.round(4), "coke": coke.round(3), "heavy_feed": heavy.round(3), "tc_failed": tc_bad,
    }, index=IDX)
    truth.index.name = "timestamp"
    return wide, truth, log, cycle_starts


def inject_quality(rng, arr, q, tag, log, mode):
    n = len(arr)
    def rand_start(L):
        return int(rng.integers(0, n - L - 1))
    if "gaps" in q:
        c, h0, h1 = q["gaps"]
        for _ in range(c):
            L = int(rng.uniform(h0, h1) * 60)
            a = rand_start(L)
            arr[a:a + L] = np.nan
            log.append((tag, "长缺失", IDX[a], L))
    for _ in range(q.get("short", 0)):
        L = int(rng.integers(1, 15))
        a = rand_start(L)
        arr[a:a + L] = np.nan
    if "freeze" in q:
        c, h0, h1 = q["freeze"]
        for _ in range(c):
            L = int(rng.uniform(h0, h1) * 60)
            a = rand_start(L)
            while mode[a] != 0:  # 冻结注入在正常运行期间，更容易看出来
                a = rand_start(L)
            v = arr[a] if not np.isnan(arr[a]) else arr[a - 1]
            arr[a:a + L] = v
            log.append((tag, "冻结", IDX[a], L))
    if "spikes" in q:
        c, mag = q["spikes"]
        for _ in range(c):
            a = rand_start(1)
            while mode[a] != 0:
                a = rand_start(1)
            if not np.isnan(arr[a]):
                arr[a] += mag * rng.choice([-1, 1]) * rng.uniform(0.9, 1.3)
                log.append((tag, "尖峰", IDX[a], 1))
    if "oor" in q:
        c, m0, m1, val = q["oor"]
        for _ in range(c):
            L = int(rng.integers(m0, m1 + 1))
            a = rand_start(L)
            while mode[a] != 0:
                a = rand_start(L)
            arr[a:a + L] = val
            log.append((tag, "超量程", IDX[a], L))
    return arr


# ---------------------------------------------------------------------------
# 校核数据：红外测温（每班一次，1#–8# 管，带人工误差）
# ---------------------------------------------------------------------------
def gen_ir(cfg: FurnaceCfg, truth: pd.DataFrame, start: str, end: str, seed: int, entered: str | None):
    rng = np.random.default_rng(seed)
    tmt = truth["tmt_avg"].values
    mode = truth["mode"].values
    rows = []
    for day in pd.date_range(start, end, freq="D"):
        for hh, mm in [(0, 30), (8, 30), (16, 30)]:
            if rng.random() < 0.07:
                continue  # 漏班
            t0 = day + pd.Timedelta(hours=hh, minutes=mm + int(np.clip(rng.normal(0, 10), -25, 25)))
            if t0 > pd.Timestamp(end) + pd.Timedelta(hours=23, minutes=59):
                continue
            i0 = mi(str(t0))
            if i0 < 0 or i0 >= N or mode[i0] == 3:
                continue  # 停炉期间不巡检
            op = rng.choice(list(OPERATORS))
            inst = rng.choice(list(INSTRUMENTS))
            for tube in range(1, 9):
                if rng.random() < 0.05:
                    continue
                ts = t0 + pd.Timedelta(minutes=int((tube - 1) * 1.5))
                i = mi(str(ts))
                if i >= N:
                    continue
                v = tmt[i] + TUBE_OFFSET[tube] + INSTRUMENTS[inst] + OPERATORS[op] + rng.normal(0, 3.5)
                status = "有效"
                r = rng.random()
                if r < 0.002:
                    v = v / 10  # 录入时小数点错位
                elif r < 0.004:
                    v = v + rng.choice([-1, 1]) * rng.uniform(120, 200)
                    status = "已作废"
                ent = entered if entered else str((ts + pd.Timedelta(minutes=int(rng.uniform(20, 90)))).floor("min"))
                rows.append((ts.strftime("%Y-%m-%d %H:%M"), f"出口段 {tube}# 管", round(float(v), 1), "巡检红外测温",
                             inst, op, status, pd.Timestamp(ent).strftime("%Y-%m-%d %H:%M")))
    return pd.DataFrame(rows, columns=["measured_at", "position", "value", "source", "instrument", "operator",
                                       "status", "entered_at"])


def gen_maint(cfg: FurnaceCfg, seed: int):
    """停炉检修期间的测温记录（不代表运行状态，默认不用于训练）。"""
    rng = np.random.default_rng(seed)
    rows = []
    s, e, _ = cfg.stops[0]
    for day in pd.date_range(pd.Timestamp(s).normalize() + pd.Timedelta(days=1), pd.Timestamp(e).normalize(), freq="D"):
        for hh in (9, 15):
            for tube in rng.choice(range(1, 9), size=2, replace=False):
                ts = day + pd.Timedelta(hours=int(hh), minutes=int(rng.integers(0, 50)))
                rows.append((ts.strftime("%Y-%m-%d %H:%M"), f"辐射段 {tube}# 管", round(float(rng.uniform(35, 160)), 1),
                             "检修测温", "红外高温计 IR-05", "检修组", "有效", "2026-03-23 10:00"))
    return pd.DataFrame(rows, columns=["measured_at", "position", "value", "source", "instrument", "operator",
                                       "status", "entered_at"])


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def tags_rows(cfg: FurnaceCfg):
    rows = []
    for tpl, name, dev, part, desc, unit, period, lo, hi, rec, tgt in TAGS:
        rows.append(dict(
            tag=tpl.format(n=cfg.k), name=name, device=DEVICES[dev].format(k=cfg.k), part=part,
            desc=desc, unit=unit, period=period, range_lo=lo, range_hi=hi, recommended=rec, is_target=tgt,
            furnace=cfg.unit,
        ))
    return rows


def template_json():
    return {
        "id": "cracking_tmt",
        "name": "裂解炉 · 炉管壁温",
        "version": "1.3",
        "desc": "用燃料气、COT、稀释蒸汽等估算 TMT，校核数据来自热电偶或红外测温",
        "tags": ["软测量", "12 个变量角色", "5 类工况"],
        "device_type": "裂解炉",
        "calib_label": "TMT 热电偶 / 红外测温（稀疏）",
        "split": "按运行周期",
        "target": {"name": "炉管壁温 · 出口段平均", "type": "soft", "unit": "℃", "lo": 850, "hi": 1130,
                   "pick": "avg", "output_tag": "TMT{k}01_SS", "target_param": "炉管壁温"},
        "roles": {
            "燃料气流量": "操作量", "原料流量": "负荷", "稀释蒸汽流量": "操作量", "横跨段温度": "过程温度",
            "炉膛温度": "过程温度", "出口温度": "被控量 COT", "炉管压降": "结焦指示", "烟气氧含量": "燃烧状态",
            "原料密度": "原料性质", "烧焦空气阀开度": "工况判定", "急冷锅炉出口温度": "结焦指示", "炉管壁温": "目标",
        },
        "recommended": ["燃料气流量", "原料流量", "稀释蒸汽流量", "横跨段温度", "炉膛温度", "出口温度",
                        "炉管压降", "烟气氧含量"],
        # 处理规则默认值；物理范围留空表示取本体中的仪表量程
        "rules_default": {
            "range": {"on": True, "action": 0},
            "freeze": {"on": True, "minutes": 30, "action": 0},
            "spike": {"on": True, "window": 5, "action": 0},
            "fill": {"on": True, "max_minutes": 2, "long_minutes": 10, "action": 0},
            "decoke": {"on": True, "action": 0},
        },
        "rules_override": {
            "炉膛温度": {"freeze": {"tol": 0.1}, "spike": {"jump": 35}},
            "炉膛温度（东侧）": {"freeze": {"tol": 0.1}, "spike": {"jump": 35}},
            "炉管压降": {"freeze": {"minutes": 60, "tol": 0.05}, "spike": {"jump": 25},
                     "fill": {"max_minutes": 10}, "smooth": {"on": False, "minutes": 5}},
            "烧焦空气阀开度": {"freeze": {"on": False}, "spike": {"on": False}},
            "原料密度": {"freeze": {"minutes": 120}},
        },
        "calib_rules": {"range": True, "tc_fail": True, "align": True, "decoke": True},
        "regime_classes": [
            {"id": "norm", "name": "正常稳定", "color": "#0d7680", "include": True},
            {"id": "hi", "name": "高负荷", "color": "#e67700", "include": True},
            {"id": "burn", "name": "烧焦", "color": "#c92a2a", "include": False},
            {"id": "up", "name": "升温 / 开炉", "color": "#868e96", "include": False},
            {"id": "sw", "name": "原料切换", "color": "#7048e8", "include": False},
            {"id": "stop", "name": "停炉", "color": "#495057", "include": False},
        ],
        "regime_rules": [
            {"regime": "stop", "all": True, "min_hours": 2,
             "conds": [{"param": "原料流量", "op": "<", "v": 1}, {"param": "燃料气流量", "op": "<", "v": 500}]},
            {"regime": "burn", "all": True, "min_hours": 2,
             "conds": [{"param": "原料流量", "op": "<", "v": 5}, {"param": "烧焦空气阀开度", "op": ">", "v": 10}]},
            {"regime": "up", "all": True, "min_hours": 1,
             "conds": [{"param": "原料流量", "op": "between", "v": 3, "v2": 35}]},
            {"regime": "sw", "all": False, "min_hours": 1,
             "conds": [{"param": "原料密度", "op": "rise", "v": 5, "hours": 6},
                       {"param": "原料密度", "op": "fall", "v": 5, "hours": 6}]},
            {"regime": "hi", "all": True, "min_hours": 2, "conds": [{"param": "原料流量", "op": ">", "v": 40}]},
        ],
        "features_default": {
            "燃料气流量": {"cur": True, "lag": 8}, "原料流量": {"cur": True, "lag": 12},
            "炉膛温度": {"cur": True, "mean": 10}, "出口温度": {"cur": True, "mean": 10, "std": 10},
            "炉管压降": {"cur": True, "rate": 30}, "烟气氧含量": {"cur": True, "lag": 5, "mean": 10},
        },
        "combos": [
            {"name": "稀释比", "kind": "formula", "expr": "{稀释蒸汽流量} / {原料流量}", "unit": "", "zero": "invalid", "on": True},
            {"name": "本周期运行天数", "kind": "op", "op": "since", "event": "cycle", "unit": "天", "on": True},
            {"name": "燃料气单耗", "kind": "formula", "expr": "{燃料气流量} / {原料流量}", "unit": "Nm³/t", "zero": "invalid", "on": False},
        ],
        "response": [
            {"change": "燃料气流量", "pct": 5, "expect": "up", "label": "上升"},
            {"change": "原料流量", "pct": 5, "expect": "down", "label": "下降", "note": "燃料气不变"},
            {"change": "稀释蒸汽流量", "pct": 10, "expect": "down", "label": "小幅下降"},
            {"change": "本周期运行天数", "delta": 10, "expect": "up", "label": "上升（结焦加重）"},
        ],
        "monotone": {"燃料气流量": 1},
        "algorithms": ["base", "lin", "rf", "xgb"],
        "detail": [
            ["变量角色", "燃料气流量、XOT、COT、稀释蒸汽、原料流量、炉膛温度、炉管压降、烟气氧含量、原料密度、TMT 校核点等 12 个"],
            ["清洗规则", "温度物理范围、热电偶失效判定、冻结检测、尖峰处理、烧焦期标记"],
            ["工况规则", "正常 / 高负荷 / 烧焦 / 升温开炉 / 原料切换 / 停炉"],
            ["特征模板", "COT 当前值、燃料气滞后 5–10 min、周期内累计运行时间、压降变化率等"],
            ["数据集划分", "按运行周期留出"],
            ["工艺约束", "壁温 850–1130 ℃，随燃料气增加单调上升"],
            ["推荐算法", "基线 + XGBoost + RF，可选 LSTM"],
            ["验证报告", "分工况误差、跨周期、跨炉、区间覆盖率"],
        ],
    }


OTHER_TEMPLATES = [
    {"id": "coke", "name": "裂解炉 · 结焦程度", "desc": "基于 TMT 上升速率、压降、运行时间估计结焦程度指数",
     "tags": ["软测量", "趋势评价", "含机理约束"], "device_type": "裂解炉", "calib_label": "烧焦记录 + 机理计算（参考校核值）",
     "split": "按运行周期", "incomplete": True,
     "detail": [["变量角色", "TMT 各点、炉管压降、COT、原料流量、运行时间、烧焦记录"], ["校核数据", "烧焦记录 + 机理计算（参考校核值）"],
                ["评价方式", "趋势合理性、与烧焦时机一致性"], ["机理约束", "结焦程度在周期内单调累积"]]},
    {"id": "gas", "name": "气化炉 · 炉温", "desc": "热电偶易失效，用氧煤比、甲烷含量、渣口压差估算炉温",
     "tags": ["软测量", "10 个变量角色", "3 类工况"], "device_type": "气化炉", "calib_label": "炉温热电偶（有效期内）",
     "split": "按时间段", "incomplete": True,
     "detail": [["变量角色", "氧煤比、蒸汽量、合成气 CH₄/CO₂、渣口压差、炉温热电偶"], ["清洗规则", "高温热电偶失效判定与有效期管理"],
                ["工况规则", "投料 / 稳态 / 负荷调整"], ["特征模板", "氧煤比、CH₄ 含量滞后、渣口压差变化率"]]},
    {"id": "col", "name": "精馏塔 · 产品质量", "desc": "用灵敏板温度、回流比、塔压估算产品组分，对照化验值",
     "tags": ["软测量", "化验延迟对齐", "3 类工况"], "device_type": "精馏塔", "calib_label": "化验分析值（4 小时一次）",
     "split": "按时间段", "incomplete": True,
     "detail": [["变量角色", "灵敏板温度、回流比、塔压、进料量、化验值"], ["校核数据", "化验值，按取样时刻对齐（有延迟）"],
                ["特征模板", "压力补偿温度、塔板温差"]]},
    {"id": "fc", "name": "通用 · 时序预测", "desc": "任意变量未来多时域预测，支持 5/15/30/60 分钟",
     "tags": ["时序预测", "多预测时域"], "device_type": "任意设备", "calib_label": "目标变量自身的测量值",
     "split": "按时间段", "incomplete": True,
     "detail": [["预测时域", "5 / 15 / 30 / 60 分钟分别评价"], ["变量角色", "用户自定义"]]},
    {"id": "blank", "name": "空白项目", "desc": "不使用模板，所有配置手动完成", "tags": ["通用"], "device_type": "任意设备",
     "calib_label": "自定义", "split": "自定义", "incomplete": True, "detail": [["说明", "无预置内容"]]},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data"))
    args = ap.parse_args()
    out = Path(args.out)
    for d in ["timeseries", "calib", "lag_refs", "templates", "_truth"]:
        (out / d).mkdir(parents=True, exist_ok=True)

    tag_rows = []
    sources = []
    summary = {}
    for cfg in (BA101, BA104):
        print(f"生成 {cfg.unit} ...")
        wide, truth, log, cycle_starts = simulate(cfg)
        wide.reset_index().to_parquet(out / "timeseries" / f"{cfg.unit}.parquet", index=False)
        truth.reset_index().to_parquet(out / "_truth" / f"{cfg.unit}_truth.parquet", index=False)
        pd.DataFrame(log, columns=["tag", "kind", "start", "minutes"]).to_csv(
            out / "_truth" / f"{cfg.unit}_quality_log.csv", index=False, encoding="utf-8-sig")
        tag_rows += tags_rows(cfg)
        summary[cfg.unit] = {
            "cycle_starts": [str(START + pd.Timedelta(minutes=c)) for c in cycle_starts],
            "stops": cfg.stops, "decokes": cfg.decokes, "high_loads": cfg.high_loads,
            "feed_switches": cfg.feed_switches, "fuel_disturbance": cfg.fuel_dist,
            "fuel_push": cfg.fuel_push, "low_ratio": cfg.low_ratio, "dcs_gap": DCS_GAP,
            "dynamics": {"fuel->tmt": {"dead_time": 5, "tau": 6}, "feed->tmt": {"dead_time": 7, "tau": 9},
                         "steam->tmt": {"dead_time": 6, "tau": 8}},
            "tube_offset": TUBE_OFFSET, "tc_offset": cfg.tc_offset,
        }

        if cfg.unit == "BA-101":
            files = [
                ("BA-101_红外测温_2026上半年.csv", "2026-01-01", "2026-06-30", 11, "2026-07-02 10:15",
                 "巡检每班一次 · 2026-07-02 导入 · 已审核", True, "train"),
                ("BA-101_红外测温_2026三季度.csv", "2026-07-01", "2026-09-30", 12, "2026-10-08 09:40",
                 "巡检每班一次 · 2026-10-08 导入 · 已审核", True, "train"),
                ("BA-101_红外测温_2026-10.csv", "2026-10-01", "2026-10-31", 13, None,
                 "巡检系统接口同步，每 30 min", True, "train"),
                ("BA-101_红外测温_2026-11.csv", "2026-11-01", "2026-11-30", 14, None,
                 "巡检系统接口同步，上线回放时按录入时刻到达", False, "online"),
            ]
            for fn, a, b, seed, ent, note, use, role in files:
                df = gen_ir(cfg, truth, a, b, seed, ent)
                df.to_csv(out / "calib" / fn, index=False, encoding="utf-8-sig")
                sources.append(dict(file=fn, furnace=cfg.unit, kind="红外", source="巡检红外测温", note=note,
                                    default_use=use, role=role, rows=len(df)))
            mt = gen_maint(cfg, 15)
            mt.to_csv(out / "calib" / "BA-101_检修测温_2026-03.csv", index=False, encoding="utf-8-sig")
            sources.append(dict(file="BA-101_检修测温_2026-03.csv", furnace=cfg.unit, kind="红外", source="检修测温",
                                note="停炉期间测量，不代表运行状态，不建议用于训练", default_use=False, role="train",
                                rows=len(mt)))
        else:
            df = gen_ir(cfg, truth, "2026-08-01", "2026-11-30", 41, None)
            df.to_csv(out / "calib" / "BA-104_红外测温_2026.csv", index=False, encoding="utf-8-sig")
            sources.append(dict(file="BA-104_红外测温_2026.csv", furnace=cfg.unit, kind="红外", source="巡检红外测温",
                                note="4 号炉巡检记录，跨炉检验用", default_use=True, role="cross", rows=len(df)))

    # 连续热电偶作为一个"测点型"校核来源，数据在时序文件中
    for unit, k in (("BA-101", 1), ("BA-104", 4)):
        sources.insert(0 if unit == "BA-101" else len(sources), dict(
            file=None, tag=f"TMT-{k}01", furnace=unit, kind="热电偶", source=f"TMT-{k}01 热电偶",
            note="每小时取整点值，热电偶失效时段自动剔除", default_use=True, role="train", rows=None,
            position="出口段 1# 管"))

    pd.DataFrame(tag_rows).to_csv(out / "tags.csv", index=False, encoding="utf-8-sig")
    (out / "calib" / "_sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2), "utf-8")

    (out / "lag_refs" / "BA-101_APC模型参数_2024-05.json").write_text(json.dumps({
        "_meta": {"type": "APC 模型参数", "name": "BA-101 APC 模型（2024-05，厂商提供）", "furnace": "BA-101"},
        "FI-1021": {"dead_time": 5, "tau": 6}, "FI-1001": {"dead_time": 7, "tau": 9},
        "FI-1012": {"dead_time": 6, "tau": 8}, "TI-1035": {"dead_time": 1, "tau": 4},
        "HC-1101": {"dead_time": 4, "tau": 5},
    }, ensure_ascii=False, indent=2), "utf-8")
    (out / "lag_refs" / "BA-101_燃料气阶跃测试_2025-09-12.json").write_text(json.dumps({
        "_meta": {"type": "阶跃测试记录", "name": "燃料气阶跃测试_2025-09-12", "furnace": "BA-101"},
        "FI-1021": {"dead_time": 6, "tau": 5},
    }, ensure_ascii=False, indent=2), "utf-8")

    (out / "templates" / "cracking_tmt.json").write_text(json.dumps(template_json(), ensure_ascii=False, indent=2), "utf-8")
    for t in OTHER_TEMPLATES:
        (out / "templates" / f"{t['id']}.json").write_text(json.dumps(t, ensure_ascii=False, indent=2), "utf-8")
    (out / "_truth" / "events.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), "utf-8")
    print("完成：", out)


if __name__ == "__main__":
    main()
