"""校核数据：来源、读取、校核规则、按"校核值取法"生成训练样本。

- 所有配对都用测量时刻 measured_at（取整到分钟），不插值。
- 红外测温"1#–8# 管按时刻平均"：同一来源相邻记录间隔 ≤ 30 min 的归为一次测量，
  至少测到 4 根出口段管才形成一个样本，时刻取这次测量的中位时刻。
- 1# 管热电偶是连续测点，数据在时序文件里；取整点值，失效时段剔除。
  取法为"按时刻平均"时只作参考，不作为训练样本（1# 管与平均值之间有固定偏差）。
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from .. import config
from . import quality

COLS = ["measured_at", "position", "value", "source", "instrument", "operator", "status", "entered_at"]
HEADER_ALIASES = {
    "measured_at": ["measured_at", "测量时刻", "测量时间", "时间"],
    "position": ["position", "测量位置", "位置"],
    "value": ["value", "校核值", "测量值", "数值", "温度"],
    "instrument": ["instrument", "仪器"],
    "operator": ["operator", "测量人"],
    "source": ["source", "来源"],
    "status": ["status", "状态"],
    "entered_at": ["entered_at", "录入时刻"],
}
MIN_TUBES = 4
SESSION_GAP_MIN = 30


def manifest() -> list[dict]:
    p = config.DATA_DIR / "calib" / "_sources.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else []


def file_records(filename: str) -> pd.DataFrame:
    df = pd.read_csv(config.DATA_DIR / "calib" / filename, encoding="utf-8-sig")
    return normalize(df)


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    ren = {}
    for k, names in HEADER_ALIASES.items():
        for c in df.columns:
            if c in names and k not in ren.values():
                ren[c] = k
    df = df.rename(columns=ren)
    if "measured_at" not in df or "value" not in df:
        raise ValueError("缺少必需的列：测量时刻（measured_at）和校核值（value）")
    for c in COLS:
        if c not in df:
            df[c] = None
    df["measured_at"] = pd.to_datetime(df["measured_at"], errors="coerce").dt.floor("min")
    df["entered_at"] = pd.to_datetime(df["entered_at"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["status"] = df["status"].fillna("有效")
    df["position"] = df["position"].fillna("出口段 1# 管").astype(str)
    df = df.dropna(subset=["measured_at"])
    return df[COLS].sort_values("measured_at").reset_index(drop=True)


def tube_no(position: str) -> int | None:
    m = re.match(r"出口段\s*(\d+)#", str(position))
    return int(m.group(1)) if m else None


def calib_rules_default(target: dict) -> dict:
    return {
        "range": {"on": True, "lo": float(target["lo"]), "hi": float(target["hi"]), "src": "估算目标", "action": 0},
        "tc_fail": {"on": True, "jump": 30, "action": 0},
        "align": {"on": True, "action": 0},
        "decoke": {"on": True, "action": 0},
    }


def tc_rules(crules: dict) -> dict:
    """连续热电偶的失效判定：超范围、冻结、1 分钟跳变过大（只看历史）。"""
    return {
        "range": crules.get("range", {"on": False}),
        "freeze": {"on": True, "minutes": 30, "tol": 0.01, "action": 1},
        "spike": {"on": crules.get("tc_fail", {}).get("on", True), "window": 3,
                  "jump": crules.get("tc_fail", {}).get("jump", 30), "action": 0},
        "fill": {"on": False},
    }


def tc_samples(raw: np.ndarray, index: pd.DatetimeIndex, crules: dict) -> pd.DataFrame:
    """连续热电偶 → 每小时整点样本。"""
    res = quality.apply_rules(raw, tc_rules(crules))
    on_hour = index.minute == 0
    ok = on_hour & res.usable
    return pd.DataFrame({"t": index[ok], "y": res.value[ok], "n": 1})


def ir_valid(rec: pd.DataFrame, crules: dict) -> pd.DataFrame:
    """校核记录按规则过滤：只用"有效"状态、物理范围。"""
    r = rec[rec["status"] == "有效"].copy()
    rr = crules.get("range", {})
    if rr.get("on"):
        r = r[(r["value"] >= rr["lo"]) & (r["value"] <= rr["hi"])]
    return r.dropna(subset=["value"])


def sessions(rec: pd.DataFrame, pick: str) -> pd.DataFrame:
    """红外记录 → 样本。pick = avg（1#–8# 按时刻平均）或 t1（只用 1# 管）。"""
    if rec.empty:
        return pd.DataFrame(columns=["t", "y", "n"])
    rec = rec.assign(tube=rec["position"].map(tube_no)).dropna(subset=["tube"])
    if pick == "t1":
        r = rec[rec["tube"] == 1]
        return pd.DataFrame({"t": r["measured_at"].values, "y": r["value"].values, "n": 1})
    rec = rec.sort_values("measured_at")
    gap = rec["measured_at"].diff().dt.total_seconds().fillna(1e9) / 60 > SESSION_GAP_MIN
    sid = gap.cumsum()
    g = rec.groupby(sid)
    out = pd.DataFrame({
        "t": g["measured_at"].apply(lambda s: s.iloc[len(s) // 2]).values,
        "y": g["value"].mean().values,
        "n": g["tube"].nunique().values,
    })
    return out[out["n"] >= MIN_TUBES].reset_index(drop=True)
