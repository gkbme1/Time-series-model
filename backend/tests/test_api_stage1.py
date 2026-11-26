def test_stage1_flow(client):
    ps = client.get("/api/projects").json()
    assert ps and ps[0]["scenarios"]
    sid = ps[0]["scenarios"][0]["id"]
    # 估算目标校验
    assert client.put(f"/api/scenarios/{sid}/target", json={"name": "", "lo": 1, "hi": 2}).status_code == 400
    assert client.put(f"/api/scenarios/{sid}/target", json={"name": "x", "lo": 3, "hi": 2}).status_code == 400
    tags = client.get(f"/api/scenarios/{sid}/tags").json()
    target = [t for t in tags if t["is_target"]][0]["tag"]
    assert client.put(f"/api/scenarios/{sid}/data", json={"tags": [target]}).status_code == 400
    s = client.get(f"/api/scenarios/{sid}/data/summary").json()
    assert len(s["quality"]) == 8 and 0 < s["impact"]["lost_pct"] < 50
    assert s["calib"]["total"] > 300
    # 关掉氧含量的冻结检测（仅标记）→ 影响比例下降
    r = client.put(f"/api/scenarios/{sid}/rules/AI-1071", json={"rules": {"freeze": {"action": 2}}})
    assert r.status_code == 200 and r.json()["dirty"]
    s2 = client.get(f"/api/scenarios/{sid}/data/summary").json()
    assert s2["impact"]["lost_pct"] < s["impact"]["lost_pct"]
    p = client.get(f"/api/scenarios/{sid}/preview", params={"tags": "TI-1052,FI-1021", "buckets": 300}).json()
    assert len(p["series"]) == 2 and len(p["t"]) <= 320 and p["calib"]
    # 导入校核数据 → 待审核 → 审核通过后计入
    csv = "测量时刻,测量位置,校核值,仪器,测量人\n" + "\n".join(
        f"2026-10-0{d} 08:3{k},出口段 {k}# 管,1050,红外高温计 IR-03,张工" for d in range(1, 4) for k in range(1, 9))
    r = client.post(f"/api/scenarios/{sid}/calib/import", files={"file": ("new.csv", csv.encode("utf-8"), "text/csv")})
    assert r.status_code == 200 and r.json()["rows"] == 24
    iid = r.json()["id"]
    src = [x for x in client.get(f"/api/scenarios/{sid}/data/summary").json()["calib"]["sources"]
           if x["id"] == f"imp:{iid}"][0]
    assert src["status"] == "待审核" and src["available"] == 0
    client.post(f"/api/calib-imports/{iid}/review", json={"action": "approve"})
    src = [x for x in client.get(f"/api/scenarios/{sid}/data/summary").json()["calib"]["sources"]
           if x["id"] == f"imp:{iid}"][0]
    assert src["available"] == 3
    # 生成数据版本：规则有改动 → 自动保存 R-v2
    ds = client.post(f"/api/scenarios/{sid}/datasets").json()
    assert ds["code"] == "DS-v1" and ds["rule_code"] == "R-v2" and ds["n_tags"] == 8
    ds2 = client.post(f"/api/scenarios/{sid}/datasets").json()
    assert ds2["code"] == "DS-v2" and ds2["rule_code"] == "R-v2"
