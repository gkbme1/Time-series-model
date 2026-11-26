"""阶段 2：加工输入不用未来数据、公式安全、工况判定与埋入事件吻合、时滞峰值。"""
import numpy as np
import pandas as pd
import pytest

from app.core import features as F
from app.core import pipeline, regime, templates
from app.core.datasource import FileDataSource
from app.services import context as cx

TPL = templates.get("cracking_tmt")


def make_spec(cycle_anchor="2025-12-13 00:00"):
    rows = cx.tag_rows("BA-101")
    tags = ["FI-1021", "FI-1001", "FI-1012", "TI-1052", "TI-1041", "PDI-1063"]
    names = {r["tag"]: r["name"] for r in rows}
    params = {t: F.default_param_spec(names[t], TPL["features_default"].get(names[t])) for t in tags}
    params["FI-1012"] = {"cur": True, "lag": {"on": True, "min": 10}, "mean": {"on": True, "min": 15},
                         "std": {"on": True, "min": 15}, "rate": {"on": True, "min": 30}}
    combos = [
        {"name": "稀释比", "kind": "formula", "expr": "{稀释蒸汽流量} ÷ {原料流量}", "zero": "invalid", "on": True},
        {"name": "单耗沿用", "kind": "formula", "expr": "{燃料气流量} / {原料流量}", "zero": "hold", "on": True},
        {"name": "本周期运行天数", "kind": "op", "op": "since", "event": "cycle", "on": True},
        {"name": "累计进料", "kind": "op", "op": "cum", "event": "cycle", "param": "FI-1001", "on": True},
        {"name": "距原料切换", "kind": "op", "op": "ago", "event": "sw", "on": True},
        {"name": "压降平滑", "kind": "op", "op": "ema", "param": "PDI-1063", "tau": 0.5, "on": True},
        {"name": "脚本", "kind": "script", "on": True},
    ]
    rules = {r["tag"]: cx.rules_for({}, "BA-101", r["tag"]) for r in rows if not r["is_target"]}
    return pipeline.Spec(tags=tags, names=names, units={r["tag"]: r["unit"] for r in rows}, rules=rules,
                         params=params, combos=combos, regime_rules=TPL["regime_rules"], cycle_mode="both",
                         cycle_anchor=cycle_anchor)


@pytest.fixture(scope="module")
def raw():
    spec = make_spec()
    # 取 2026-05-01 至 08-31：包含烧焦、原料切换
    return FileDataSource().read("BA-101", pipeline.required_tags(spec)).loc["2026-05-01":"2026-08-31"]


def test_features_do_not_use_future(raw):
    spec = make_spec("2026-03-22 00:00")
    full, lab = pipeline.build(raw, spec)
    assert "脚本" not in full.columns and full.shape[1] >= 20
    rng = np.random.default_rng(0)
    for ts in ["2026-05-28 07:00", "2026-06-25 12:30", "2026-07-30 10:17"]:
        cut = raw.index.get_loc(pd.Timestamp(ts))
        mod = raw.copy()
        mod.iloc[cut + 1:] = mod.iloc[cut + 1:].to_numpy() * rng.uniform(0.5, 1.5, mod.iloc[cut + 1:].shape)
        mod.iloc[cut + 5:cut + 200, 0] = np.nan
        b, lab2 = pipeline.build(mod, spec)
        pd.testing.assert_frame_equal(full.iloc[: cut + 1], b.iloc[: cut + 1])
        assert (lab[: cut + 1] == lab2[: cut + 1]).all()
        # 上线时的情形：只有到 t 为止的数据，最后一行与全段计算一致
        part, _ = pipeline.build(raw.iloc[: cut + 1], spec)
        pd.testing.assert_series_equal(full.iloc[cut], part.iloc[-1])


def test_window_features_backward_only():
    x = np.arange(100, dtype=float)
    assert F.param_feature(x, "lag", 5)[10] == 5
    assert F.param_feature(x, "mean", 10)[20] == pytest.approx(np.mean(x[11:21]))
    assert F.param_feature(x, "rate", 30)[50] == pytest.approx(60.0)
    assert np.isnan(F.param_feature(x, "mean", 10)[5])


@pytest.mark.parametrize("bad", ["__import__('os')", "{a}.real", "open({a})", "{a} ** 2", "lambda: 1",
                                 "[{a}]", "{a} if {a} else 1", "max({a}, 1)", "eval('1')"])
def test_formula_rejects_unsafe(bad):
    with pytest.raises(F.ExprError):
        F.eval_formula(bad, {"a": np.ones(3)})


def test_formula_zero_handling():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    b = np.array([2.0, 0.0, 2.0, 0.0])
    v, nbad = F.eval_formula("{a} ÷ {b} + abs(-1) + sqrt(4) - log(1)", {"a": a, "b": b})
    assert nbad == 2 and np.isnan(v[1]) and v[0] == pytest.approx(3.5)
    v, _ = F.eval_formula("{a} / {b}", {"a": a, "b": b}, zero="hold")
    assert v[1] == pytest.approx(0.5) and v[3] == pytest.approx(1.5)  # 沿用上一个有效值


def test_ops():
    ev = np.zeros(20, bool)
    ev[5:8] = True
    s = F.eval_op("ago", ev)
    assert np.isnan(s[2]) and np.isnan(s[6]) and s[8] == pytest.approx(0) and s[10] == pytest.approx(2 / 60)
    s = F.eval_op("since", ev, start_offset_min=1440)
    assert s[0] == pytest.approx(1.0)
    c = F.eval_op("cum", ev, np.full(20, 60.0), start_offset_min=0)
    assert c[4] == pytest.approx(5) and c[9] == pytest.approx(2)


def test_rules_match_planted_events():
    """规则判定结果与生成数据中埋入的事件基本吻合。"""
    tr = pd.read_parquet(cx.src().dir / "_truth" / "BA-101_truth.parquet")
    names = regime.needed_params(TPL["regime_rules"])
    vals = {n: cx.quality_of("BA-101", cx.tag_by_name("BA-101", n), cx.rules_for({}, "BA-101", cx.tag_by_name("BA-101", n))).value
            for n in names}
    lab = regime.evaluate(TPL["regime_rules"], vals, len(tr))
    mp = {"正常稳定": "norm", "高负荷": "hi", "烧焦": "burn", "升温 / 开炉": "up", "原料切换": "sw", "停炉": "stop"}
    truth = tr["regime"].map(mp).to_numpy()
    m = (tr["timestamp"] < "2026-11-01").to_numpy()
    assert (lab[m] == truth[m]).mean() > 0.98
    for k in ("burn", "hi", "stop"):
        tk = m & (truth == k)
        assert (lab[tk] == k).mean() > 0.95, k


def test_stage2_api(client):
    sid = client.get("/api/projects").json()[0]["scenarios"][0]["id"]
    rg = client.get(f"/api/scenarios/{sid}/regime").json()
    cy = rg["cycles"]
    assert len(cy) == 5 and cy[0]["pre"] and sum(c["done"] for c in cy) == 4 and not cy[-1]["done"]
    # 时滞分析：燃料气 → 1# 管热电偶，峰值约 8 分钟
    lg = client.get(f"/api/scenarios/{sid}/lag", params={"tag": "FI-1021", "y": "TMT-101"}).json()
    assert 6 <= lg["peak"] <= 10 and lg["r_peak"] > 0.5
    # 自动发现：找到 4 次原料切换，确认后生效；手动标注优先
    r = client.post(f"/api/scenarios/{sid}/regime/auto/run", json={"keys": ["原料密度"], "sens": "mid"}).json()
    sw = [i for i, a in enumerate(r["auto"]) if "原料密度" in a["desc"]]
    assert len(sw) >= 4
    r = client.post(f"/api/scenarios/{sid}/regime/auto/{sw[0]}", json={"action": "ok", "w": "sw"}).json()
    assert any(x["id"] == "sw" and x["sources"].get("自动发现") for x in r["summary"])
    r = client.post(f"/api/scenarios/{sid}/regime/manual", json={"a": "2026-09-25 03:00", "b": "2026-09-25 06:00",
                                                                  "name": "燃料气扰动"}).json()
    new = [c for c in r["classes"] if c["name"] == "燃料气扰动"][0]
    assert [x for x in r["summary"] if x["id"] == new["id"]][0]["minutes"] == 180
    # 组合量：公式安全检查、试算
    bad = client.put(f"/api/scenarios/{sid}/features/combos", json={"combos": [
        {"name": "x", "kind": "formula", "expr": "__import__('os')", "on": True}]})
    assert bad.status_code == 400
    p = client.post(f"/api/scenarios/{sid}/features/preview", json={"combo": {
        "kind": "op", "op": "since", "event": "cycle"}}).json()
    vals = [v for v in p["v"] if v is not None]
    assert max(vals) > 60 and min(vals) < 1
    s = client.post(f"/api/scenarios/{sid}/features/preview", json={"combo": {"kind": "script"}}).json()
    assert s["script"]
    # 采用滞后 → 加工方式随之更新；保存版本
    f = client.post(f"/api/scenarios/{sid}/lag/adopt", json={"tag": "FI-1021", "v": 8, "src": "对目标计算",
                                                             "detail": "峰值 8 分钟"}).json()
    assert any(c["col"] == "燃料气流量 · 8 分钟前" and "对目标计算" in c["desc"] for c in f["columns"])
    assert client.post(f"/api/scenarios/{sid}/regime/save").json()["code"] == "WC-v1"
    assert client.post(f"/api/scenarios/{sid}/features/save").json()["code"] == "F-v1"
