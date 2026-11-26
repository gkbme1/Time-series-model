"""阶段 3：训练（按时间顺序划分、多算法、分工况对比）、检验（响应方向、适用范围、审批发布）。"""
import time

import numpy as np
import pandas as pd
import pytest

from app.core import applicability as AP
from app.core import models as M


def test_baseline_uses_only_previous_value():
    t_all = np.array(["2026-01-01T08:00", "2026-01-01T16:00", "2026-01-02T00:00"], dtype="datetime64[m]")
    y_all = np.array([1000.0, 1010.0, 1020.0])
    p = M.baseline(np.array(["2026-01-01T16:00", "2026-01-01T07:00", "2026-01-01T23:59"], dtype="datetime64[m]"), t_all, y_all)
    assert p[0] == 1000 and np.isnan(p[1]) and p[2] == 1010  # 同一时刻的校核值不能用


def test_time_cv_is_ordered():
    for tr, te in M.time_cv_splits(200):
        assert tr.max() < te.min()  # 调参用的交叉验证只用过去预测未来


def test_tune_and_interval_small():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(240, 3))
    y = X @ np.array([3.0, -2.0, 0.5]) + rng.normal(0, 0.5, 240)
    res = M.tune("xgb", X, y, 3)
    assert res["trials"] == 3 and res["cv_mae"] > 0
    f = M.fit_one("xgb", res["params"], X[:200], y[:200], [1, -1, 0])
    p, lo, hi = f.predict(X[200:])
    assert (lo <= p).all() and (hi >= p).all()
    cov = np.mean((y[200:] >= lo) & (y[200:] <= hi))
    assert 0.6 <= cov <= 1.0


def test_applicability_evaluate():
    rng = np.random.default_rng(1)
    X = pd.DataFrame({"a": rng.normal(10, 1, 500), "b": rng.normal(5, 1, 500)})
    X["b"] = X["b"] + 0.9 * (X["a"] - 10)
    appl = AP.generate(X, [{"col": "a", "name": "A", "unit": ""}, {"col": "b", "name": "B", "unit": ""}],
                       [{"id": "norm", "name": "正常稳定", "include": True, "n_train": 500}], 99, [])
    T = pd.DataFrame({"a": [10.0, 30.0, 11.8, 10.0, np.nan], "b": [5.0, 5.0, 3.0, 5.0, 5.0]})
    ok, cat, _ = AP.evaluate(appl, T, np.array(["norm", "norm", "norm", "burn", "norm"]), ["a", "b"], {})
    assert list(ok) == [True, False, False, False, False]
    assert list(cat) == ["", "range", "mahal", "regime", "quality"]
    appl["vars"][0]["hi"] = 40
    assert "依据" in AP.validate_edit(appl)
    appl["vars"][0]["why"] = "工艺确认"
    assert AP.validate_edit(appl) is None


def _wait(get, done, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = get()
        if done(v):
            return v
        time.sleep(1)
    raise TimeoutError


def test_stage3_flow(client):
    sid = client.get("/api/projects").json()[0]["scenarios"][0]["id"]
    if not client.get(f"/api/scenarios/{sid}/datasets").json():
        client.post(f"/api/scenarios/{sid}/datasets")
    pg = client.put(f"/api/scenarios/{sid}/train/config", json={"mode": "uni", "compare": True, "pm": "def",
                                                                "algs": ["base", "lin", "xgb"]}).json()
    assert [c["role"] for c in pg["cycles"]][-2:] == ["test", "skip"]  # 默认最后一个完整周期检验、未结束的不用
    assert pg["funnel"][-1]["v"] > 500 and pg["n_exp"] == 5
    assert client.post(f"/api/scenarios/{sid}/train/run").status_code == 200
    b = _wait(lambda: client.get(f"/api/scenarios/{sid}/train/batches").json()["batches"][0],
              lambda b: all(e["status"] in ("已完成", "失败") for e in b["exps"]))
    ex = {(e["mode"], e["algo"]): e for e in b["exps"]}
    assert all(e["status"] == "已完成" for e in b["exps"]), [e["error"] for e in b["exps"]]
    xu, base = ex[("uni", "xgb")]["metrics"], ex[("uni", "base")]["metrics"]
    assert xu["mae"] < base["mae"] * 0.8  # XGBoost 明显优于基线
    hu, hp = xu["by_regime"]["hi"]["mae"], ex[("per", "xgb")]["metrics"]["by_regime"]["hi"]["mae"]
    assert abs(hu - hp) > 1.0  # 分工况对比在高负荷上有差异
    assert ex[("uni", "xgb")]["importance"] and b["conclusion"]
    # 送检 → 自动检验
    c = client.post(f"/api/experiments/{ex[('uni', 'xgb')]['id']}/send").json()
    v = _wait(lambda: client.get(f"/api/scenarios/{sid}/validate").json()["current"],
              lambda v: v["status"] in ("已完成", "失败"))
    assert v["status"] == "已完成", v["error"]
    assert all(r["ok"] for r in v["resp"])  # 响应方向测试通过
    assert v["life"] == "检验通过" and v["checks"][2]["state"] == "todo"
    appl = v["appl"]
    assert appl["vars"] and appl["mahal"]["thr"] > 0
    # 人工调整范围必须填依据
    steam = next(x for x in appl["vars"] if x["name"] == "稀释蒸汽流量")
    steam["hi"] = steam["hi"] + 1.5
    pv = client.post(f"/api/candidates/{c['id']}/appl/preview", json={"appl": appl}).json()
    assert pv["invalid"] and pv["share"] is not None
    r = client.post(f"/api/candidates/{c['id']}/approve", json={"appl": appl})
    assert r.status_code == 400 and "依据" in r.json()["detail"]
    steam["why"] = "工艺确认高负荷下稀释蒸汽可到 17.8 t/h，属正常操作区"
    pv2 = client.post(f"/api/candidates/{c['id']}/appl/preview", json={"appl": appl}).json()
    assert pv2["invalid"] is None and pv2["share"] > pv["share"] - 1e-9
    r = client.post(f"/api/candidates/{c['id']}/approve", json={"appl": appl})
    assert r.status_code == 200 and r.json()["life"] == "已发布"
    # 发布后不能单独修改适用范围
    assert client.put(f"/api/candidates/{c['id']}/appl", json={"appl": appl}).status_code == 400
    import json
    from pathlib import Path
    from app import config
    pkg = json.loads((Path(config.VAR_DIR) / "models" / f"S{sid}" / "published" / c["code"] / "package.json").read_text("utf-8"))
    for k in ("rules", "features", "regime", "applicability", "report_code", "rule_code", "f_code", "wc_code", "dataset"):
        assert pkg[k]
    assert pkg["applicability"]["reviewed"]
