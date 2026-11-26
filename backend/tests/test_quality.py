import numpy as np
import pandas as pd

from app.core import calib, quality


def test_fill_only_short_gaps_with_history():
    x = np.array([1, 2, np.nan, np.nan, 5, np.nan, np.nan, np.nan, np.nan, 10], float)
    r = quality.apply_rules(x, {"fill": {"on": True, "max_minutes": 2, "action": 0}})
    # 短缺失用上一个有效值（2）补齐，不用后面的 5 插值
    assert r.value[2] == 2 and r.value[3] == 2
    assert r.flags[2] & quality.FILLED
    # 长缺失：前 2 分钟补齐，之后不可用
    assert r.value[5] == 5 and r.value[6] == 5
    assert np.isnan(r.value[7]) and r.flags[7] & quality.UNUSABLE


def test_range_spike_freeze_flags():
    x = np.full(200, 100.0) + np.sin(np.arange(200))
    x[50] = 500  # 尖峰
    x[60] = -10  # 超范围
    x[100:150] = 123.0  # 冻结
    r = quality.apply_rules(x, {"range": {"on": True, "lo": 0, "hi": 1000, "action": 0},
                                "freeze": {"on": True, "minutes": 30, "tol": 0.01, "action": 0},
                                "spike": {"on": True, "window": 5, "jump": 20, "action": 0},
                                "fill": {"on": True, "max_minutes": 2, "action": 0}})
    assert r.flags[50] & quality.SPIKE
    assert r.flags[60] & quality.RANGE
    assert r.flags[129] & quality.FROZEN and not r.flags[128] & quality.FROZEN  # 窗口内 30 个点全部冻结才判冻结
    assert r.flags[140] & quality.UNUSABLE
    assert not (r.flags[10] & quality.UNUSABLE)


def test_only_mark_keeps_value():
    x = np.array([1.0, 2.0, 2000.0, 3.0])
    r = quality.apply_rules(x, {"range": {"on": True, "lo": 0, "hi": 100, "action": 1}})
    assert r.flags[2] & quality.RANGE and not r.flags[2] & quality.UNUSABLE and r.value[2] == 2000


def test_ir_sessions_average_and_min_tubes():
    rows = []
    for k in range(1, 9):
        rows.append({"measured_at": pd.Timestamp("2026-01-01 08:30") + pd.Timedelta(minutes=k), "position":
                     f"出口段 {k}# 管", "value": 1000 + k, "status": "有效"})
    for k in range(1, 3):  # 只测了 2 根管，不形成样本
        rows.append({"measured_at": pd.Timestamp("2026-01-01 16:30") + pd.Timedelta(minutes=k), "position":
                     f"出口段 {k}# 管", "value": 1000, "status": "有效"})
    df = calib.normalize(pd.DataFrame(rows))
    s = calib.sessions(df, "avg")
    assert len(s) == 1 and abs(s["y"].iloc[0] - 1004.5) < 1e-9 and s["n"].iloc[0] == 8
    assert len(calib.sessions(df, "t1")) == 2
