"""时滞分析：参数变化后，目标要过多久才跟着变。

对目标（有连续测量时）或代理变量做互相关，滞后 0–30 分钟，只用纳入的工况、双方都有效的时刻。
先各自减去 60 分钟滑动均值（去掉结焦、负荷等慢变化），再计算 corr(x[t−L], y[t])。
这是离线分析工具，不进入模型输入，所以用居中滑动均值去趋势没有问题。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config

MAX_LAG = 30
DETREND = 61


def xcorr(x: np.ndarray, y: np.ndarray, mask: np.ndarray, max_lag: int = MAX_LAG) -> dict:
    xs, ys = pd.Series(x), pd.Series(y)
    xd = (xs - xs.rolling(DETREND, center=True, min_periods=DETREND // 2).mean()).to_numpy()
    yd = (ys - ys.rolling(DETREND, center=True, min_periods=DETREND // 2).mean()).to_numpy()
    rs, ns = [], []
    for L in range(max_lag + 1):
        a = xd[: len(xd) - L] if L else xd
        b = yd[L:]
        m = mask[L:] & mask[: len(mask) - L] & np.isfinite(a) & np.isfinite(b)
        if m.sum() < 100:
            rs.append(None)
            ns.append(int(m.sum()))
            continue
        rs.append(round(float(np.corrcoef(a[m], b[m])[0, 1]), 4))
        ns.append(int(m.sum()))
    valid = [(i, r) for i, r in enumerate(rs) if r is not None]
    if not valid:
        return {"r": rs, "peak": None, "r_peak": None, "n": 0}
    i, r = max(valid, key=lambda t: abs(t[1]))
    return {"r": rs, "peak": i, "r_peak": r, "n": ns[i], "sign": "同向" if r > 0 else "反向"}


def eq_lag(dead: float, tau: float) -> int:
    """等效滞后 = 纯滞后 + 时间常数的一半（经验换算）。"""
    return int(round(dead + tau / 2))


def ref_dirs() -> list[Path]:
    return [config.DATA_DIR / "lag_refs", config.VAR_DIR / "imports" / "lag_refs"]


def load_refs(furnace: str) -> list[dict]:
    out = []
    for d in ref_dirs():
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                j = json.loads(p.read_text("utf-8-sig"))
            except Exception:
                continue
            meta = j.get("_meta", {})
            if meta.get("furnace") and meta["furnace"] != furnace:
                continue
            m = {k: v for k, v in j.items() if not k.startswith("_") and isinstance(v, dict)}
            out.append({"id": p.stem, "type": meta.get("type", "外部参考"), "name": meta.get("name", p.stem),
                        "map": m, "imported": d != ref_dirs()[0]})
    return out


def parse_ref_file(content: bytes) -> dict:
    """导入文件格式：{位号: {"dead_time": 分钟, "tau": 分钟}}，可带 "_meta"。"""
    j = json.loads(content.decode("utf-8-sig"))
    if not isinstance(j, dict):
        raise ValueError("格式应为 {位号: {\"dead_time\": 分钟, \"tau\": 分钟}}")
    ok = {}
    for k, v in j.items():
        if k.startswith("_"):
            continue
        if not isinstance(v, dict) or "dead_time" not in v or "tau" not in v:
            raise ValueError(f"{k} 缺少 dead_time 或 tau")
        ok[k] = {"dead_time": float(v["dead_time"]), "tau": float(v["tau"])}
    if not ok:
        raise ValueError("文件里没有位号记录")
    return ok
