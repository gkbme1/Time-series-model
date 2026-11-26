"""原则 1：不用未来数据。把某一时刻之后的数据改掉，该时刻及以前的处理结果不能变。"""
import numpy as np
import pandas as pd
import pytest

from app.core import quality, regime
from app.core.datasource import FileDataSource

RULES = {
    "range": {"on": True, "lo": 0, "hi": 18000, "action": 0},
    "freeze": {"on": True, "minutes": 30, "tol": 4.0, "action": 0},
    "spike": {"on": True, "window": 5, "jump": 800, "action": 0},
    "smooth": {"on": True, "minutes": 5},
    "fill": {"on": True, "max_minutes": 2, "action": 0},
}


@pytest.fixture(scope="module")
def fuel():
    return FileDataSource().read("BA-101", ["FI-1021"])["FI-1021"].to_numpy()


@pytest.mark.parametrize("cut", [1000, 123457, 300001])
@pytest.mark.parametrize("mode", ["noise", "nan", "spike", "frozen"])
def test_quality_does_not_use_future(fuel, cut, mode):
    rng = np.random.default_rng(cut)
    a = quality.apply_rules(fuel, RULES)
    x = fuel.copy()
    if mode == "noise":
        x[cut + 1:] += rng.normal(0, 3000, len(x) - cut - 1)
    elif mode == "nan":
        x[cut + 1:] = np.nan
    elif mode == "spike":
        x[cut + 1:cut + 3] = 99999
    else:
        x[cut + 1:] = x[cut]
    b = quality.apply_rules(x, RULES)
    np.testing.assert_array_equal(a.flags[:cut + 1], b.flags[:cut + 1])
    np.testing.assert_array_equal(a.value[:cut + 1], b.value[:cut + 1])


def test_truncated_equals_full(fuel):
    """截断到 t 再算（上线时的情形）与全段算的结果一致。"""
    full = quality.apply_rules(fuel, RULES)
    for cut in (5000, 250000):
        part = quality.apply_rules(fuel[:cut + 1], RULES)
        np.testing.assert_array_equal(full.flags[:cut + 1], part.flags)
        np.testing.assert_array_equal(full.value[:cut + 1], part.value)


def test_causal_regime_does_not_use_future():
    src = FileDataSource()
    df = src.read("BA-101", ["FI-1001", "HV-1015", "TI-1041"])
    rules = [{"regime": "burn", "all": True, "min_hours": 2,
              "conds": [{"param": "原料流量", "op": "<", "v": 5}, {"param": "烧焦空气阀开度", "op": ">", "v": 10}]},
             {"regime": "up", "all": True, "min_hours": 2,
              "conds": [{"param": "出口温度", "op": "rise", "v": 10, "hours": 4}, {"param": "原料流量", "op": "<", "v": 37}]},
             {"regime": "hi", "all": True, "min_hours": 2, "conds": [{"param": "原料流量", "op": ">", "v": 40}]}]
    vals = {"原料流量": df["FI-1001"].to_numpy(), "烧焦空气阀开度": df["HV-1015"].to_numpy(),
            "出口温度": df["TI-1041"].to_numpy()}
    n = len(df)
    a = regime.evaluate(rules, vals, n, causal=True)
    cut = int(df.index.get_indexer([pd.Timestamp("2026-08-20 07:00")])[0])  # 烧焦刚开始
    vals2 = {k: v.copy() for k, v in vals.items()}
    for v in vals2.values():
        v[cut + 1:] = 0
    b = regime.evaluate(rules, vals2, n, causal=True)
    assert (a[:cut + 1] == b[:cut + 1]).all()
