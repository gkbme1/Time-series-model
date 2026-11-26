""""训练模型""检验效果"页接口。"""
from __future__ import annotations

import copy

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..core import applicability as AP
from ..core import models as M
from ..services import train as T
from ..services import validate as V
from ..store import db

router = APIRouter(prefix="/api")


def _sc(s, sid) -> db.Scenario:
    sc = s.get(db.Scenario, sid)
    if not sc:
        raise HTTPException(404, "场景不存在")
    return sc


# ======================================================================= 训练模型
@router.get("/scenarios/{sid}/train")
def train_page(sid: int):
    with db.session() as s:
        sc = _sc(s, sid)
        out = T.page(s, sc)
        db.touch_json(sc)
        return out


@router.put("/scenarios/{sid}/train/config")
def train_config(sid: int, body: dict):
    with db.session() as s:
        sc = _sc(s, sid)
        t = T.train_cfg(sc)
        for k in ("ds", "mode", "compare", "pm", "budget", "interval", "monotone"):
            if k in body:
                t[k] = body[k]
        if "algs" in body:
            algs = [a for a in body["algs"] if a in M.ALGS and a != "lstm"]
            t["algs"] = ["base"] + [a for a in algs if a != "base"]
        if "split" in body:
            sp_ = body["split"]
            t["split"]["type"] = sp_.get("type", t["split"]["type"])
            if "roles" in sp_:
                t["split"]["roles"].update({str(k): v for k, v in sp_["roles"].items() if v in ("train", "test", "skip")})
        if "manual" in body:
            for a, ps in body["manual"].items():
                meta = {k: typ for k, _, _, typ in M.PARAM_META.get(a, [])}
                cur = t["manual"].setdefault(a, {})
                for k, v in ps.items():
                    try:
                        cur[k] = meta[k](v)
                    except (KeyError, ValueError, TypeError):
                        raise HTTPException(400, f"参数 {k} 的值不对")
        if t["mode"] not in M.MODES:
            raise HTTPException(400, "建模方式不对")
        db.touch_json(sc)
        return T.page(s, sc)


@router.post("/scenarios/{sid}/train/run")
def train_run(sid: int):
    with db.session() as s:
        sc = _sc(s, sid)
        try:
            b = T.start(s, sc)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"batch": b.code}


@router.get("/scenarios/{sid}/train/batches")
def train_batches(sid: int):
    with db.session() as s:
        sc = _sc(s, sid)
        ds = T.latest_ds(s, sc, T.train_cfg(sc)["ds"])
        return {"batches": T.batches(s, sc), "sig": T.signature(sc, ds.code) if ds else None}


@router.post("/experiments/{eid}/send")
def send_exp(eid: int):
    with db.session() as s:
        e = s.get(db.Experiment, eid)
        if not e:
            raise HTTPException(404, "实验不存在")
        try:
            c = V.send(s, e)
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        return {"id": c.id, "code": c.code}


# ======================================================================= 检验效果
@router.get("/scenarios/{sid}/validate")
def validate_page(sid: int, cid: int | None = None):
    with db.session() as s:
        sc = _sc(s, sid)
        cs = list(s.scalars(select(db.Candidate).where(db.Candidate.scenario_id == sid).order_by(db.Candidate.id.desc())))
        if not cs:
            return {"cands": [], "current": None}
        cur = next((c for c in cs if c.id == cid), cs[0])
        on = V.online_cand(s, sid)
        out = {"cands": [V.cand_dict(s, c, False) for c in cs], "current": V.cand_dict(s, cur),
               "online": on.code if on else None}
        db.touch_json(sc)
        return out


class ApplIn(BaseModel):
    appl: dict


def _cand(s, cid) -> db.Candidate:
    c = s.get(db.Candidate, cid)
    if not c:
        raise HTTPException(404, "候选不存在")
    return c


def _clean_appl(c: db.Candidate, a: dict) -> dict:
    """只允许改动可编辑的字段：生效范围、依据、支持的工况、阈值分位数、人工规则。"""
    base = copy.deepcopy(c.appl)
    for v, nv in zip(base["vars"], a.get("vars", [])):
        if nv.get("col") != v["col"]:
            continue
        for k in ("lo", "hi"):
            try:
                v[k] = float(nv[k])
            except (TypeError, ValueError, KeyError):
                pass
        v["why"] = str(nv.get("why", ""))[:200]
    inc = {r["id"]: bool(r.get("include")) for r in a.get("regimes", [])}
    for r in base["regimes"]:
        if r["id"] in inc and r["n_train"]:
            r["include"] = inc[r["id"]]
    if "mahal" in a and a["mahal"].get("pct") is not None:
        AP.set_pct(base, float(a["mahal"]["pct"]))
    rules = []
    for r in a.get("rules", base["rules"]):
        if r.get("type") not in ("quality", "after_event", "rate"):
            continue
        rr = {"type": r["type"], "on": bool(r.get("on"))}
        if r["type"] == "after_event":
            rr["hours"] = float(r.get("hours", 2))
        if r["type"] == "rate":
            rr.update({"tag": r.get("tag"), "minutes": int(r.get("minutes", 5)), "pct": float(r.get("pct", 15))})
        rules.append(rr)
    base["rules"] = rules
    base["reviewed"] = False
    return base


@router.post("/candidates/{cid}/appl/preview")
def appl_preview(cid: int, body: ApplIn):
    with db.session() as s:
        c = _cand(s, cid)
        if c.status != "已完成":
            raise HTTPException(400, "检验还没有完成")
        return V.appl_preview(s, c, _clean_appl(c, body.appl))


@router.put("/candidates/{cid}/appl")
def appl_save(cid: int, body: ApplIn):
    with db.session() as s:
        c = _cand(s, cid)
        if c.life in ("已发布", "已归档"):
            raise HTTPException(400, "已发布的适用范围不能单独修改")
        c.appl = _clean_appl(c, body.appl)
        return V.cand_dict(s, c)


@router.post("/candidates/{cid}/appl/regen")
def appl_regen(cid: int):
    with db.session() as s:
        c = _cand(s, cid)
        if c.life in ("已发布", "已归档"):
            raise HTTPException(400, "已发布的适用范围不能单独修改")
        c.status = "排队中"
    from ..services import jobs
    jobs.submit(f"val{cid}", V.run_validation, cid)
    return {"ok": True}


@router.put("/scenarios/{sid}/response")
def put_response(sid: int, body: dict):
    with db.session() as s:
        sc = _sc(s, sid)
        items = V.response_cfg(sc)
        exp = {x["change"]: x for x in body.get("items", [])}
        for it in items:
            if it["change"] in exp and exp[it["change"]].get("expect") in ("up", "down"):
                it["expect"] = exp[it["change"]]["expect"]
                it["label"] = exp[it["change"]].get("label") or ("上升" if it["expect"] == "up" else "下降")
        db.touch_json(sc)
        return {"items": items}


class NoteIn(BaseModel):
    note: str = ""


@router.post("/candidates/{cid}/approve")
def approve(cid: int, body: ApplIn | None = None):
    with db.session() as s:
        c = _cand(s, cid)
        if body and body.appl and c.life not in ("已发布", "已归档"):
            c.appl = _clean_appl(c, body.appl)
        try:
            V.approve(s, c)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return V.cand_dict(s, c)


@router.post("/candidates/{cid}/reject")
def reject(cid: int, body: NoteIn):
    with db.session() as s:
        c = _cand(s, cid)
        try:
            V.reject(s, c, body.note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return V.cand_dict(s, c)
