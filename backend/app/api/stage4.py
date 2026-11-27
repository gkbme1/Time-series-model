""""上线投用""在线校核""跟踪改进"及场景概览指标接口。"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

from ..core import calib
from ..services import iterate as IT
from ..services import online as ON
from ..services import runtime as RT
from ..services import scenarios as SV
from ..services.replay import clock
from ..store import db

router = APIRouter(prefix="/api")


def _sc(s, sid) -> db.Scenario:
    sc = s.get(db.Scenario, sid)
    if not sc:
        raise HTTPException(404, "场景不存在")
    return sc


# ======================================================================= 回放时钟
@router.get("/clock")
def get_clock():
    return clock.state()


class ClockIn(BaseModel):
    sim: str | None = None
    speed: int | None = None
    running: bool | None = None


@router.put("/clock")
def put_clock(body: ClockIn):
    if body.sim is not None:
        t = pd.Timestamp(body.sim)
        with db.session() as s:  # 时钟回拨时，运行实例从新时刻重新开始（已写入的输出保留）
            for inst in s.scalars(select(db.Instance)):
                st = dict(inst.state or {})
                if st.get("last_t") and pd.Timestamp(st["last_t"]) > t:
                    st["last_t"] = f"{t:%Y-%m-%d %H:%M}"
                    inst.state = st
    clock.set(body.sim, body.speed, body.running)
    return clock.state()


# ======================================================================= 上线投用
def inst_dict(s, i: db.Instance) -> dict:
    # 最新值取不晚于回放时钟的最后一条输出（时钟回拨后也一致）
    o = s.scalars(select(db.Output).where(db.Output.instance_id == i.id, db.Output.t <= f"{clock.now_min():%Y-%m-%d %H:%M}")
                  .order_by(db.Output.t.desc()).limit(1)).first()
    last = {"t": o.t, "value": o.value, "applicable": o.applicable, "reason_cat": o.reason_cat, "reason": o.reason,
            "quality": o.quality} if o else {}
    return {"id": i.id, "furnace": i.furnace, "furnace_name": next((f["name"] for f in SV.furnaces() if f["unit"] == i.furnace), i.furnace),
            "model": i.model_code, "mode": i.mode, "period": i.period_s, "created": i.created_sim, "output_tag": i.output_tag,
            "last": last, "cross": i.cross, "last_t": (i.state or {}).get("last_t")}


@router.get("/scenarios/{sid}/deploy")
def deploy_page(sid: int):
    with db.session() as s:
        sc = _sc(s, sid)
        insts = list(s.scalars(select(db.Instance).where(db.Instance.scenario_id == sid).order_by(db.Instance.id)))
        pubs = list(s.scalars(select(db.Candidate).where(db.Candidate.scenario_id == sid, db.Candidate.life == "已发布")))
        return {"clock": clock.state(), "instances": [inst_dict(s, i) for i in insts],
                "published": [{"id": c.id, "code": c.code, "mae": (c.report or {}).get("rep", {}).get("mae")} for c in pubs],
                "furnaces": SV.furnaces(), "home": sc.furnace}


@router.get("/instances/{iid}/live")
def live(iid: int):
    with db.session() as s:
        inst = s.get(db.Instance, iid)
        if not inst:
            raise HTTPException(404, "实例不存在")
        sc = s.get(db.Scenario, inst.scenario_id)
        c = s.get(db.Candidate, inst.cand_id)
        pkg, _ = RT.load_package(c)
        now = clock.now_min()
        out = ON.outputs(s, iid, now - pd.Timedelta(hours=2), now)
        last = out.iloc[-1].to_dict() if not out.empty else None
        pts = []
        if inst.furnace == sc.furnace:
            rec = ON.records(s, sc, now - pd.Timedelta(hours=2), now)
            pts = [[f"{r['t']:%Y-%m-%d %H:%M}", r["y"], r["source"]] for _, r in rec.iterrows() if r["eval"]]
        names = {r["id"]: r["name"] for r in pkg["regime"]["classes"]}
        inputs = []
        if last:
            for v in pkg["applicability"]["vars"]:
                x = (last.get("inputs") or {}).get(v["col"])
                inputs.append({"name": v["name"], "unit": v["unit"], "value": x, "p1": v["p1"], "p99": v["p99"], "lo": v["lo"], "hi": v["hi"],
                               "hmin": v["hmin"], "hmax": v["hmax"],
                               "state": "无效" if x is None else ("超出" if x < v["lo"] or x > v["hi"] else "范围内")})
        trend = [[f"{t:%Y-%m-%d %H:%M}", _n(r.value), _n(r.p10), _n(r.p90)] for t, r in out.set_index("t").iterrows()] if not out.empty else []
        ref = []
        if inst.furnace == sc.furnace:
            tt = cx_target(sc)
            if tt:
                raw = RT.replay_source().read(inst.furnace, [tt], now - pd.Timedelta(hours=2), now)[tt]
                off = ON.tc_offset(s, sc)
                ref = [[f"{t:%Y-%m-%d %H:%M}", None if pd.isna(v) or v > 1300 else round(float(v) - off, 1)] for t, v in raw.items()]
        return {"clock": clock.state(), "instance": inst_dict(s, inst), "target": sc.config["target"],
                "last": None if last is None else {"value": _n(last["value"]), "p10": _n(last["p10"]), "p90": _n(last["p90"]),
                                                   "applicable": bool(last["applicable"]), "reason_cat": last["reason_cat"],
                                                   "reason": last["reason"], "quality": last["quality"], "regime": last["regime"],
                                                   "latency_ms": int(last["latency_ms"]),
                                                   "t": f"{last['t']:%Y-%m-%d %H:%M}", "regime_name": names.get(last["regime"], last["regime"])},
                "trend": trend, "ref": ref, "points": pts, "inputs": inputs, "n_2h": int(len(out)),
                "n_bad_2h": int((~out["applicable"]).sum()) if not out.empty else 0}


def cx_target(sc):
    from ..services import context as cx
    return cx.target_tag(sc.furnace)


def _n(x):
    return None if x is None or pd.isna(x) else round(float(x), 2)


class PrepIn(BaseModel):
    cand_id: int
    furnace: str


@router.post("/scenarios/{sid}/deploy/prepare")
def deploy_prepare(sid: int, body: PrepIn):
    """新增运行实例第 1 步：按参数名自动映射位号。"""
    with db.session() as s:
        c = s.get(db.Candidate, body.cand_id)
        if not c or c.scenario_id != sid or c.life != "已发布":
            raise HTTPException(400, "只能部署已发布的模型版本")
        pkg, _ = RT.load_package(c)
        m, rows = RT.map_tags(pkg, body.furnace)
        return {"rows": rows, "home": body.furnace == pkg["furnace"], "n_ok": sum(r["ok"] for r in rows), "n": len(rows)}


@router.post("/scenarios/{sid}/deploy/cross")
def deploy_cross(sid: int, body: PrepIn):
    """第 2 步：用该炉最近 3 个月的数据做一次跨炉检验。"""
    with db.session() as s:
        c = s.get(db.Candidate, body.cand_id)
        pkg, model = RT.load_package(c)
        m, rows = RT.map_tags(pkg, body.furnace)
        if not all(r["ok"] for r in rows):
            raise HTTPException(400, "先完成测点映射")
        return RT.cross_check(pkg, model, body.furnace, m, (c.report or {}).get("rep", {}).get("mae"))


class CreateIn(BaseModel):
    cand_id: int
    furnace: str
    mode: str = "shadow"
    cross: dict | None = None


@router.post("/scenarios/{sid}/deploy/create")
def deploy_create(sid: int, body: CreateIn):
    with db.session() as s:
        sc = _sc(s, sid)
        c = s.get(db.Candidate, body.cand_id)
        try:
            inst = RT.create_instance(s, sc, c, body.furnace, body.mode, body.cross)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if inst.mode == "online" and inst.furnace == sc.furnace:
            sc.status = "在线运行"
        return inst_dict(s, inst)


class ModeIn(BaseModel):
    mode: str  # online / shadow / stopped


@router.put("/instances/{iid}/mode")
def set_mode(iid: int, body: ModeIn):
    with db.session() as s:
        inst = s.get(db.Instance, iid)
        if body.mode not in ("online", "shadow", "stopped"):
            raise HTTPException(400, "模式不对")
        if body.mode == "online":
            for o in s.scalars(select(db.Instance).where(db.Instance.scenario_id == inst.scenario_id,
                                                         db.Instance.furnace == inst.furnace, db.Instance.mode == "online")):
                if o.id != inst.id:
                    o.mode = "stopped"  # 切换为在线时，原在线实例停用
            if inst.cross and not inst.cross.get("ok", True):
                raise HTTPException(400, "跨炉检验未通过，不能切换为在线")
        inst.mode = body.mode
        if body.mode != "stopped":
            st = dict(inst.state or {})
            st["last_t"] = max(st.get("last_t") or "", f"{clock.now_min():%Y-%m-%d %H:%M}") if body.mode else st.get("last_t")
            inst.state = st
        sc = s.get(db.Scenario, inst.scenario_id)
        if body.mode == "online" and inst.furnace == sc.furnace:
            sc.status = "在线运行"
            sc.config["online_model"] = inst.model_code
            db.touch_json(sc)
        return inst_dict(s, inst)


# ======================================================================= 在线校核
@router.get("/scenarios/{sid}/check")
def check_page(sid: int, src: str = "", st: str = "", page: int = 0):
    with db.session() as s:
        sc = _sc(s, sid)
        inst = ON.online_instance(s, sid, sc.furnace)
        t0, t1 = ON.window()
        pr = ON.pairs(s, sc, inst, t0, t1) if inst else ON.records(s, sc, t0, t1)
        if not len(pr):
            pr = pd.DataFrame(columns=["t", "y", "position", "source", "status", "note", "entered", "eval", "kind", "p", "dev"])
        pend = list(s.scalars(select(db.CalibRecord).where(db.CalibRecord.scenario_id == sid, db.CalibRecord.status == "待审核")))
        valid = pr[pr["eval"] == True] if len(pr) else pr  # noqa: E712
        lat = []
        for _, r in pr.iterrows():
            if r["kind"] != "热电偶" and pd.notna(r.get("entered")):
                lat.append((pd.Timestamp(r["entered"]) - pd.Timestamp(r["t"])).total_seconds() / 60)
        rows = pr.copy()
        rows["src_group"] = rows["source"].map(lambda x: "热电偶" if "热电偶" in str(x) else ("巡检红外测温" if x == "巡检红外测温" else "手工录入 / 导入"))
        if src:
            rows = rows[rows["src_group"] == src]
        if st:
            rows = rows[rows["status"] == st]
        rows = rows.sort_values("t", ascending=False)
        n = len(rows)
        pg = rows.iloc[page * 10: page * 10 + 10]
        tt = cx_target(sc)
        return {
            "clock": clock.state(), "instance": inst_dict(s, inst) if inst else None,
            "kpi": {"n7": int(len(valid)), "tc": int((valid["kind"] == "热电偶").sum()) if len(valid) else 0,
                    "ir": int((valid["kind"] == "红外").sum()) if len(valid) else 0,
                    "man": int((valid["kind"] == "手工").sum()) if len(valid) else 0, "pending": len(pend),
                    "latency": round(float(np.mean(lat)), 0) if lat else None},
            "offset": ON.tc_offset(s, sc), "target": sc.config["target"],
            "sources": [[f"{tt} 热电偶", "现场测点", "本地时序文件（随回放时钟流入）", "1 min，取整点值", "测量时刻即时间戳",
                         "出口段 1# 管（扣除偏差后代表平均）", "A"],
                        ["巡检红外测温", "巡检系统", "接口同步（按录入时刻到达）", "每班 1 次", "使用巡检记录的测量时刻", "出口段 1#–8# 管，按时刻平均", "B"],
                        ["手工录入 / 导入", "手工", "页面录入、Excel / CSV 导入", "不定期", "录入时填写测量时刻", "录入时选择位置", "B，需审核"]],
            "rows": [{"t": f"{r['t']:%Y-%m-%d %H:%M}", "position": r["position"], "y": _n(r["y"]), "p": _n(r.get("p")),
                      "dev": _n(r.get("dev")), "source": r["source"], "status": r["status"], "note": r["note"],
                      "id": int(r["id"]) if "id" in r and pd.notna(r.get("id")) else None,
                      "counted": bool(r["eval"] and r.get("applicable") and r.get("dev") is not None),
                      "applicable": None if r.get("applicable") is None else bool(r.get("applicable")),
                      "backfill": bool(r.get("backfill", False))} for _, r in pg.iterrows()],
            "total": n, "page": page, "pages": max(1, (n + 9) // 10),
            "pending": [{"id": r.id, "t": r.measured_at, "position": r.position, "value": r.value, "source": r.source,
                         "who": r.operator, "instrument": r.instrument} for r in pend],
        }


class ManualIn(BaseModel):
    measured_at: str
    position: str
    value: float
    instrument: str
    operator: str


@router.post("/scenarios/{sid}/check/manual")
def check_manual(sid: int, body: ManualIn):
    with db.session() as s:
        sc = _sc(s, sid)
        t = pd.Timestamp(body.measured_at).floor("min")
        g = sc.config["target"]
        if not body.operator.strip():
            raise HTTPException(400, "请填写测量人")
        if t > clock.now_min():
            raise HTTPException(400, f"测量时刻不能晚于当前时刻（回放时钟 {clock.now_min():%Y-%m-%d %H:%M}）")
        if not (g["lo"] <= body.value <= g["hi"]):
            raise HTTPException(400, f"校核值超出合理范围 {g['lo']:g}–{g['hi']:g} {g.get('unit', '')}，请确认")
        s.add(db.CalibRecord(scenario_id=sid, source="手工录入", measured_at=f"{t:%Y-%m-%d %H:%M}", position=body.position,
                             value=body.value, instrument=body.instrument, operator=body.operator.strip(), status="待审核",
                             entered_at=f"{clock.now_min():%Y-%m-%d %H:%M}"))
        return {"ok": True}


@router.post("/scenarios/{sid}/check/import")
async def check_import(sid: int, file: UploadFile = File(...)):
    content = await file.read()
    name = file.filename or "upload.csv"
    try:
        if name.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        else:
            try:
                df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(content), encoding="gbk")
        df = calib.normalize(df).dropna(subset=["value"])
    except Exception as e:
        raise HTTPException(400, f"文件解析失败：{e}")
    now = clock.now_min()
    with db.session() as s:
        sc = _sc(s, sid)
        g = sc.config["target"]
        n = skipped = 0
        for _, r in df.iterrows():
            if r["measured_at"] > now or not (g["lo"] <= r["value"] <= g["hi"]):
                skipped += 1
                continue
            s.add(db.CalibRecord(scenario_id=sid, source=f"导入：{name}", measured_at=f"{r['measured_at']:%Y-%m-%d %H:%M}",
                                 position=str(r["position"]), value=float(r["value"]), instrument=str(r["instrument"] or ""),
                                 operator=str(r["operator"] or ""), status="待审核", entered_at=f"{now:%Y-%m-%d %H:%M}"))
            n += 1
        return {"n": n, "skipped": skipped, "name": name}


class ReviewIn(BaseModel):
    action: str  # approve / void


@router.post("/calib-records/{rid}/review")
def calib_review(rid: int, body: ReviewIn):
    with db.session() as s:
        r = s.get(db.CalibRecord, rid)
        if not r:
            raise HTTPException(404, "记录不存在")
        r.status = "有效" if body.action == "approve" else "已作废"
        return {"id": r.id, "status": r.status}


# ======================================================================= 场景概览指标
@router.get("/scenarios/{sid}/metrics")
def metrics(sid: int, thr: float = 12):
    with db.session() as s:
        return ON.overview(s, _sc(s, sid), thr)


@router.get("/scenarios/{sid}/metrics/{kind}")
def metrics_detail(sid: int, kind: str, thr: float = 12):
    with db.session() as s:
        sc = _sc(s, sid)
        if kind == "mae":
            return ON.mae_detail(s, sc)
        if kind == "valid":
            return ON.valid_detail(s, sc)
        if kind == "big":
            return ON.big_detail(s, sc, thr)
        if kind == "model":
            return ON.model_detail(s, sc) or {}
        raise HTTPException(404, "")


class MarkIn(BaseModel):
    t: str
    action: str  # 加入训练集 / 忽略 / 待处理


@router.post("/scenarios/{sid}/metrics/big/mark")
def big_mark(sid: int, body: MarkIn):
    with db.session() as s:
        sc = _sc(s, sid)
        inst = ON.online_instance(s, sid, sc.furnace)
        old = s.scalars(select(db.Sample).where(db.Sample.scenario_id == sid, db.Sample.kind == "较大偏差", db.Sample.t == body.t)).first()
        if body.action == "待处理":
            if old:
                s.delete(old)
            return {"ok": True}
        if old is None:
            old = db.Sample(scenario_id=sid, instance_id=inst.id, key=f"big:{inst.id}:{body.t}", kind="较大偏差", t=body.t, t_end=body.t,
                            reason="较大偏差（概览中标记）", detail=f"{body.t} 校核点偏差较大，在场景概览中标记。",
                            created_sim=f"{clock.now_min():%Y-%m-%d %H:%M}")
            s.add(old)
            s.flush()
        IT.review(s, sc, old, body.action == "加入训练集", "模型不足" if body.action == "加入训练集" else "", "概览中标记")
        return {"ok": True}


# ======================================================================= 跟踪改进
@router.get("/scenarios/{sid}/iterate")
def iterate_page(sid: int, st: str = ""):
    with db.session() as s:
        sc = _sc(s, sid)
        IT.sync(s, sc)
        s.flush()
        q = select(db.Sample).where(db.Sample.scenario_id == sid)
        all_ = list(s.scalars(q.order_by(db.Sample.t.desc())))
        lst = [IT.sample_dict(x) for x in all_ if not st or x.status == st]
        by = {}
        for x in all_:
            by[x.kind] = by.get(x.kind, 0) + 1
        return {"samples": lst, "by_kind": by, "pending": sum(1 for x in all_ if x.status == "待复核"),
                "accepted": sum(1 for x in all_ if x.status == "已入训练集"), "psi": IT.psi(s, sc), "causes": IT.CAUSES,
                "clock": clock.state()}


@router.get("/samples/{xid}")
def sample_detail(xid: int):
    with db.session() as s:
        x = s.get(db.Sample, xid)
        sc = s.get(db.Scenario, x.scenario_id)
        return {**IT.sample_dict(x), "chart": IT.sample_chart(s, sc, x)}


class SampleReviewIn(BaseModel):
    use: bool
    cause: str = ""
    memo: str = ""


@router.post("/samples/{xid}/review")
def sample_review(xid: int, body: SampleReviewIn):
    with db.session() as s:
        x = s.get(db.Sample, xid)
        sc = s.get(db.Scenario, x.scenario_id)
        IT.review(s, sc, x, body.use, body.cause, body.memo)
        return IT.sample_dict(x)


@router.get("/projects/{pid}/stats")
def project_stats(pid: int):
    with db.session() as s:
        scs = list(s.scalars(select(db.Scenario).where(db.Scenario.project_id == pid)))
        for sc in scs:
            IT.sync(s, sc)
        s.flush()
        sids = [x.id for x in scs]
        insts = list(s.scalars(select(db.Instance).where(db.Instance.scenario_id.in_(sids), db.Instance.mode != "stopped")))
        pend = s.scalar(select(func.count()).select_from(db.Sample).where(db.Sample.scenario_id.in_(sids), db.Sample.status == "待复核")) or 0
        per = {}
        for x in s.scalars(select(db.Sample).where(db.Sample.scenario_id.in_(sids), db.Sample.status == "待复核")):
            per[x.scenario_id] = per.get(x.scenario_id, 0) + 1
        return {"instances": len(insts), "furnaces": len({i.furnace for i in insts}), "pending": pend, "pending_by_scenario": per,
                "clock": clock.state()}
