""""分工况""加工输入"页接口。"""
from __future__ import annotations

import copy
import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .. import config
from ..core import features as F
from ..core import lag, regime
from ..services import context as cx
from ..services import features_page, regime_page, versions
from ..services import spec as sp
from ..store import db

router = APIRouter(prefix="/api")


def _load(s, sid) -> db.Scenario:
    sc = s.get(db.Scenario, sid)
    if not sc:
        raise HTTPException(404, "场景不存在")
    return sc


# ======================================================================= 分工况
def regime_view(s, sc) -> dict:
    rg = sp.regime_cfg(sc)
    db.touch_json(sc)
    code, dirty, nxt = versions.dirty(s, sc, "WC", versions.regime_content(rg))
    return {**rg, "params": regime_page.params_available(sc), "ops": regime.OPS,
            "summary": regime_page.summary(sc), "cycles": sp.cycles(sc), "code": code, "dirty": dirty, "next": nxt,
            "data_start": sc.config["data"]["start"], "data_end": sc.config["data"]["end"]}


@router.get("/scenarios/{sid}/regime")
def get_regime(sid: int):
    with db.session() as s:
        return regime_view(s, _load(s, sid))


class RegimeIn(BaseModel):
    rules: list[dict] | None = None
    classes: list[dict] | None = None
    cycle_mode: str | None = None
    cycle0_start: str | None = None
    auto_keys: list[str] | None = None
    show: list[str] | None = None
    sens: str | None = None


def _check_rules(sc, rules: list[dict], classes: list[dict]):
    names = {p["name"] for p in regime_page.params_available(sc)}
    ids = {c["id"] for c in classes}
    for r in rules:
        if r.get("regime") not in ids or r["regime"] == "norm":
            raise HTTPException(400, "规则对应的工况不存在")
        try:
            r["min_hours"] = float(r.get("min_hours", 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "最短持续时间要填数字")
        for c in r.get("conds", []):
            if c.get("param") not in names:
                raise HTTPException(400, f"参数\"{c.get('param')}\"不在本设备")
            if c.get("op") not in regime.OPS:
                raise HTTPException(400, "条件不支持")
            try:
                c["v"] = float(c["v"])
                if c["op"] == "between":
                    c["v2"] = float(c.get("v2", c["v"]))
                    if c["v2"] < c["v"]:
                        raise HTTPException(400, "\"介于\"的上限要不小于下限")
                if c["op"] in ("rise", "fall"):
                    c["hours"] = float(c.get("hours", 1))
                    if c["hours"] <= 0:
                        raise HTTPException(400, "小时数要大于 0")
            except (TypeError, ValueError, KeyError):
                raise HTTPException(400, "条件里的数值要填数字")


@router.put("/scenarios/{sid}/regime")
def put_regime(sid: int, body: RegimeIn):
    with db.session() as s:
        sc = _load(s, sid)
        rg = sp.regime_cfg(sc)
        if body.classes is not None:
            inc = {c["id"]: bool(c.get("include")) for c in body.classes}
            for c in rg["classes"]:
                if c["id"] in inc:
                    c["include"] = inc[c["id"]]
        if body.rules is not None:
            rules = copy.deepcopy(body.rules)
            _check_rules(sc, rules, rg["classes"])
            rg["rules"] = rules
        if body.cycle_mode is not None:
            if body.cycle_mode not in regime.CYCLE_EVENTS:
                raise HTTPException(400, "周期边界设置不对")
            rg["cycle_mode"] = body.cycle_mode
        if body.cycle0_start is not None:
            rg["cycle0_start"] = body.cycle0_start or None
        if body.auto_keys is not None:
            rg["auto_keys"] = body.auto_keys
        if body.show is not None:
            rg["show"] = body.show[:5]
        if body.sens is not None:
            rg["sens"] = body.sens
        db.touch_json(sc)
        return regime_view(s, sc)


class ClassIn(BaseModel):
    name: str


@router.post("/scenarios/{sid}/regime/classes")
def add_class(sid: int, body: ClassIn):
    if not body.name.strip():
        raise HTTPException(400, "请输入新工况名称")
    with db.session() as s:
        sc = _load(s, sid)
        cid = regime_page.new_class(sp.regime_cfg(sc), body.name.strip())
        db.touch_json(sc)
        return {"id": cid, **regime_view(s, sc)}


class ManualIn(BaseModel):
    a: str
    b: str
    w: str | None = None
    name: str | None = None


@router.post("/scenarios/{sid}/regime/manual")
def add_manual(sid: int, body: ManualIn):
    with db.session() as s:
        sc = _load(s, sid)
        rg = sp.regime_cfg(sc)
        if body.a >= body.b:
            raise HTTPException(400, "框选的时段无效")
        w = body.w
        if body.name:
            w = regime_page.new_class(rg, body.name.strip())
        if w not in {c["id"] for c in rg["classes"]}:
            raise HTTPException(400, "请选择工况")
        rg["manual"].append({"a": body.a, "b": body.b, "w": w, "who": config.DEFAULT_USER})
        rg["manual"].sort(key=lambda m: m["a"])
        for c in rg["auto"]:
            if c["st"] == "new" and c["a"] < body.b and body.a < c["b"]:
                c["st"] = "man"
        db.touch_json(sc)
        return regime_view(s, sc)


@router.delete("/scenarios/{sid}/regime/manual/{i}")
def del_manual(sid: int, i: int):
    with db.session() as s:
        sc = _load(s, sid)
        rg = sp.regime_cfg(sc)
        if 0 <= i < len(rg["manual"]):
            rg["manual"].pop(i)
        db.touch_json(sc)
        return regime_view(s, sc)


class AutoRunIn(BaseModel):
    keys: list[str]
    sens: str = "mid"


@router.post("/scenarios/{sid}/regime/auto/run")
def auto_run(sid: int, body: AutoRunIn):
    if not body.keys:
        raise HTTPException(400, "至少选一个参数")
    with db.session() as s:
        sc = _load(s, sid)
        rg = sp.regime_cfg(sc)
        rg["auto_keys"], rg["sens"] = body.keys, body.sens
        rg["auto"] = regime_page.run_auto(sc, body.keys, body.sens)
        db.touch_json(sc)
        return regime_view(s, sc)


class AutoActIn(BaseModel):
    action: str  # ok / no / undo
    w: str | None = None
    name: str | None = None


@router.post("/scenarios/{sid}/regime/auto/{i}")
def auto_act(sid: int, i: int, body: AutoActIn):
    with db.session() as s:
        sc = _load(s, sid)
        rg = sp.regime_cfg(sc)
        if not (0 <= i < len(rg["auto"])):
            raise HTTPException(404, "时段不存在")
        c = rg["auto"][i]
        if body.action == "ok":
            w = body.w or c["w"]
            if body.name:
                w = regime_page.new_class(rg, body.name.strip())
            if w not in {x["id"] for x in rg["classes"]}:
                raise HTTPException(400, "请选择工况")
            c["w"], c["st"] = w, "ok"
        elif body.action == "no":
            c["st"] = "no"
        else:
            c["st"], c["w"] = "new", c["rw"]
        db.touch_json(sc)
        return regime_view(s, sc)


@router.get("/scenarios/{sid}/regime/chart")
def regime_chart(sid: int, params: str = "", start: str | None = None, end: str | None = None, buckets: int = 700):
    with db.session() as s:
        sc = _load(s, sid)
        sp.regime_cfg(sc)
    names = [p for p in params.split(",") if p][:5]
    return regime_page.chart(sc, names, start, end, max(100, min(buckets, 2000)))


@router.post("/scenarios/{sid}/regime/save")
def save_regime(sid: int):
    with db.session() as s:
        sc = _load(s, sid)
        code, new = versions.ensure(s, sc, "WC", versions.regime_content(sp.regime_cfg(sc)), "手动保存")
        sc.config["regime"]["code"] = code
        versions.advance(sc, "加工输入")
        db.touch_json(sc)
        s.flush()
        return {"code": code, "new": new, **regime_view(s, sc)}


# ======================================================================= 加工输入
def features_view(s, sc) -> dict:
    v = features_page.view(sc)
    db.touch_json(sc)
    code, dirty, nxt = versions.dirty(s, sc, "F", versions.features_content(sp.features_cfg(sc)))
    return {**v, "code": code, "dirty": dirty, "next": nxt}


@router.get("/scenarios/{sid}/features")
def get_features(sid: int):
    with db.session() as s:
        return features_view(s, _load(s, sid))


@router.put("/scenarios/{sid}/features/params/{tag}")
def put_param(sid: int, tag: str, body: dict):
    with db.session() as s:
        sc = _load(s, sid)
        if tag not in sc.config["data"]["tags"]:
            raise HTTPException(400, "参数不在已选参数中")
        cur = copy.deepcopy(sp.param_spec(sc, tag))
        if "cur" in body:
            cur["cur"] = bool(body["cur"])
        for k, _ in F.KINDS:
            if k in body and isinstance(body[k], dict):
                if "on" in body[k]:
                    cur[k]["on"] = bool(body[k]["on"])
                if "min" in body[k]:
                    try:
                        m = int(body[k]["min"])
                    except (TypeError, ValueError):
                        raise HTTPException(400, "时间要填整数分钟")
                    if m < 1 or m > 7 * 1440:
                        raise HTTPException(400, "时间要在 1 分钟到 7 天之间")
                    cur[k]["min"] = m
        sp.features_cfg(sc)["params"][tag] = cur
        db.touch_json(sc)
        return features_view(s, sc)


def _check_combo(sc, c: dict) -> dict:
    c = copy.deepcopy(c)
    if not str(c.get("name", "")).strip():
        raise HTTPException(400, "请填写名称")
    c["name"] = c["name"].strip()
    if c.get("kind") not in ("formula", "op", "script"):
        raise HTTPException(400, "方式不对")
    names = {sp.build_spec(sc).names[t] for t in sc.config["data"]["tags"]}
    if c["kind"] == "formula":
        try:
            _, used = F.parse_formula(c.get("expr", ""))
        except F.ExprError as e:
            raise HTTPException(400, str(e))
        miss = [n for n in used if n not in names]
        if miss:
            raise HTTPException(400, f"\"{miss[0]}\"不在\"选数据\"已选参数中")
        c.setdefault("zero", "invalid")
    elif c["kind"] == "op":
        if c.get("op") not in F.OPS:
            raise HTTPException(400, "算子不对")
        if c["op"] in ("cum", "ema") and not c.get("param"):
            raise HTTPException(400, "请选择参数")
        c.setdefault("event", "cycle")
    else:
        c["st"] = "待审核"
    return c


@router.put("/scenarios/{sid}/features/combos")
def put_combos(sid: int, body: dict):
    with db.session() as s:
        sc = _load(s, sid)
        combos = [_check_combo(sc, c) for c in body.get("combos", [])]
        cols = [c["name"] for c in combos]
        if len(set(cols)) != len(cols):
            raise HTTPException(400, "组合量名称不能重复")
        sp.features_cfg(sc)["combos"] = combos
        db.touch_json(sc)
        return features_view(s, sc)


@router.post("/scenarios/{sid}/features/preview")
def preview_combo(sid: int, body: dict):
    with db.session() as s:
        sc = _load(s, sid)
        sp.features_cfg(sc)
    c = body.get("combo", {})
    try:
        if c.get("kind") != "script":
            c = _check_combo(sc, {**c, "name": c.get("name") or "试算"})
        return features_page.combo_preview(sc, c)
    except F.ExprError as e:
        raise HTTPException(400, str(e))


@router.get("/scenarios/{sid}/lag")
def lag_calc(sid: int, tag: str, y: str):
    with db.session() as s:
        sc = _load(s, sid)
        sp.regime_cfg(sc)
    return features_page.lag_analysis(sc, tag, y)


@router.get("/scenarios/{sid}/lag/refs")
def lag_refs(sid: int):
    with db.session() as s:
        sc = _load(s, sid)
    return lag.load_refs(sc.furnace)


@router.post("/scenarios/{sid}/lag/import")
async def lag_import(sid: int, file: UploadFile = File(...), type: str = Form("APC 模型参数")):
    with db.session() as s:
        sc = _load(s, sid)
    try:
        m = lag.parse_ref_file(await file.read())
    except Exception as e:
        raise HTTPException(400, f"文件解析失败：{e}" if str(e) else "文件解析失败")
    d = config.VAR_DIR / "imports" / "lag_refs"
    d.mkdir(parents=True, exist_ok=True)
    stem = (file.filename or "外部参考").rsplit(".", 1)[0]
    (d / f"{stem}.json").write_text(json.dumps({"_meta": {"type": type, "name": file.filename, "furnace": sc.furnace},
                                                **m}, ensure_ascii=False, indent=2), "utf-8")
    return {"name": file.filename, "n": len(m), "refs": lag.load_refs(sc.furnace)}


class AdoptIn(BaseModel):
    tag: str
    v: int
    src: str
    detail: str = ""


@router.post("/scenarios/{sid}/lag/adopt")
def lag_adopt(sid: int, body: AdoptIn):
    if body.v < 0 or body.v > 180:
        raise HTTPException(400, "滞后要在 0–180 分钟之间")
    with db.session() as s:
        sc = _load(s, sid)
        fc = sp.features_cfg(sc)
        fc["lags"][body.tag] = {"v": body.v, "src": body.src, "detail": body.detail, "at": f"{db.now():%Y-%m-%d %H:%M}"}
        ps = copy.deepcopy(sp.param_spec(sc, body.tag))
        if body.v > 0:
            ps["lag"] = {"on": True, "min": body.v}
        fc["params"][body.tag] = ps
        db.touch_json(sc)
        return features_view(s, sc)


@router.post("/scenarios/{sid}/features/example")
def apply_example(sid: int):
    with db.session() as s:
        sc = _load(s, sid)
        from ..core import templates
        ex = templates.all_templates().get(sc.template_id, {}).get("features_default", {})
        fc = sp.features_cfg(sc)
        n = 0
        for t in sc.config["data"]["tags"]:
            name = cx.tag_row(sc.furnace, t)["name"]
            if name in ex:
                fc["params"][t] = F.default_param_spec(name, ex[name])
                n += 1
        for c in fc["combos"]:
            if c["name"] in ("稀释比", "本周期运行天数"):
                c["on"] = True
        db.touch_json(sc)
        return {"n": n, **features_view(s, sc)}


@router.post("/scenarios/{sid}/features/save")
def save_features(sid: int):
    with db.session() as s:
        sc = _load(s, sid)
        code, new = versions.ensure(s, sc, "F", versions.features_content(sp.features_cfg(sc)), "手动保存")
        sc.config["features"]["code"] = code
        versions.advance(sc, "训练模型")
        db.touch_json(sc)
        s.flush()
        return {"code": code, "new": new, **features_view(s, sc)}
