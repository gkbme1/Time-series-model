"""阶段 4：回放时钟不给未来数据；上线推理与训练用同一份代码、结果一致；不适用时不输出；校核审核与配对。"""
import numpy as np
import pandas as pd
import pytest

from app.core import pipeline
from app.core.datasource import FileDataSource
from app.services import runtime as RT
from app.services.replay import FutureDataError, clock, replay_source

from test_stage2 import make_spec


def test_replay_source_refuses_future():
    clock.set(sim="2026-11-05 08:00", running=False)
    src = replay_source()
    df = src.read("BA-101", ["FI-1021"], "2026-11-05 07:00", "2026-11-05 08:00")
    assert df.index[-1] == pd.Timestamp("2026-11-05 08:00")
    with pytest.raises(FutureDataError):
        src.read("BA-101", ["FI-1021"], "2026-11-05 07:00", "2026-11-05 08:01")


@pytest.mark.parametrize("ts", ["2026-11-03 10:17", "2026-11-12 13:20", "2026-11-22 18:45"])
def test_online_window_equals_offline(ts):
    """上线时只用"往前 7 天"的数据 + 周期锚点计算，结果必须与训练时的整段计算一致。"""
    spec = make_spec("2025-12-13 00:00")
    spec.combos = [c for c in spec.combos if c.get("op") != "ema"]  # 指数平滑的窗口差异另有说明（取 5 倍时间常数的历史）
    t = pd.Timestamp(ts)
    raw = FileDataSource().read("BA-101", pipeline.required_tags(spec), None, t)
    full, lab_full = pipeline.build(raw, spec)
    clock.set(sim=ts, running=False)
    # 上线时：实例创建时求出一次周期锚点（只读创建时刻之前的数据），之后用 runtime.window_build 取窗口计算
    created = t - pd.Timedelta(days=3)
    anchors = RT.find_anchors("BA-101", spec, created, "2025-12-13 00:00")
    _, rt0, raw0, _ = RT.window_build(spec, "BA-101", [created], anchors)  # 实例运行中不断更新锚点
    anchors = {**anchors, **RT.last_ends(rt0, raw0.index, spec, RT.op_events(spec))}
    part, lab_part, _, _ = RT.window_build(spec, "BA-101", [t], anchors)
    a, b = full.loc[t], part.loc[t]
    for c in full.columns:
        assert (np.isnan(a[c]) and np.isnan(b[c])) or a[c] == pytest.approx(b[c], rel=1e-9, abs=1e-9), c
    assert lab_full[-1] == lab_part[-1]


def test_online_flow(client):
    sid = 1
    v = client.get(f"/api/scenarios/{sid}/validate").json()
    pub = [c for c in v["cands"] if c["life"] == "已发布"]
    if not pub:
        pytest.skip("需要先有已发布的模型（test_stage3 会发布一个）")
    cid = pub[0]["id"]
    client.put("/api/clock", json={"sim": "2026-11-12 11:00", "speed": 600, "running": False})
    r = client.post(f"/api/scenarios/{sid}/deploy/create", json={"cand_id": cid, "furnace": "BA-101", "mode": "online"})
    assert r.status_code == 200, r.json()
    iid = r.json()["id"]
    # 同一台炉不能再建第二个在线实例
    assert client.post(f"/api/scenarios/{sid}/deploy/create", json={"cand_id": cid, "furnace": "BA-101", "mode": "online"}).status_code == 400
    client.put("/api/clock", json={"sim": "2026-11-12 15:00"})
    RT.step(iid, clock.now_min())
    lv = client.get(f"/api/instances/{iid}/live").json()
    assert lv["last"]["t"] == "2026-11-12 15:00" and lv["n_2h"] == 121  # 13:00–15:00，两端都含
    from app.store import db
    with db.session() as s:
        rows = list(s.scalars(db.select(db.Output).where(db.Output.instance_id == iid)))
    assert len(rows) == 240
    na = [o for o in rows if not o.applicable]
    assert na, "11-12 13:00–13:40 高负荷下稀释比偏低，应判为不适用"
    assert all(o.value is None and o.p10 is None and o.reason for o in na)  # 不适用时不输出，并记录原因
    assert all(o.value is not None and o.p10 <= o.value <= o.p90 for o in rows if o.applicable)
    assert any("13:" in o.t for o in na)
    # 手工录入：待审核 → 审核通过后才计入；按测量时刻配对
    bad = client.post(f"/api/scenarios/{sid}/check/manual", json={"measured_at": "2026-11-12 14:30", "position": "出口段 3# 管",
                                                                   "value": 500, "instrument": "IR-03", "operator": "张工"})
    assert bad.status_code == 400
    ok = client.post(f"/api/scenarios/{sid}/check/manual", json={"measured_at": "2026-11-12 14:30", "position": "出口段 3# 管",
                                                                  "value": 1050, "instrument": "IR-03", "operator": "张工"})
    assert ok.status_code == 200
    ck = client.get(f"/api/scenarios/{sid}/check", params={"src": "手工录入 / 导入"}).json()
    row = ck["rows"][0]
    assert row["status"] == "待审核" and not row["counted"] and ck["kpi"]["pending"] == 1
    client.post(f"/api/calib-records/{ck['pending'][0]['id']}/review", json={"action": "approve"})
    row = client.get(f"/api/scenarios/{sid}/check", params={"src": "手工录入 / 导入"}).json()["rows"][0]
    assert row["status"] == "有效" and row["p"] is not None and row["t"] == "2026-11-12 14:30"
    m = client.get(f"/api/scenarios/{sid}/metrics").json()
    assert m["n_pairs"] > 0 and m["mae"] is not None and 0 < m["share"] < 100
    vd = client.get(f"/api/scenarios/{sid}/metrics/valid").json()
    assert vd["events"] and all(e["minutes"] > 0 for e in vd["events"])
    assert client.get(f"/api/scenarios/{sid}/iterate").status_code == 200
