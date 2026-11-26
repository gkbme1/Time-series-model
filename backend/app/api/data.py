""""选数据"页接口：估算目标、参数选择、预览、质量诊断、处理规则、校核数据、数据版本。"""
from __future__ import annotations

import copy
import io

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from ..core import calib, quality
from ..services import context as cx
from ..services import data_page, datasets
from ..services.scenarios import STAGES
from ..store import db

router = APIRouter(prefix="/api")


def _load(s, sid) -> db.Scenario:
    sc = s.get(db.Scenario, sid)
    if not sc:
        raise HTTPException(404, "场景不存在")
    return sc


def _imports(s, sid):
    return list(s.scalars(select(db.CalibImport).where(db.CalibImport.scenario_id == sid)))


# ---------------------------------------------------------------- 估算目标
class TargetIn(BaseModel):
    name: str
    type: str = "soft"
    horizons: list[int] = []
    unit: str = ""
    lo: float
    hi: float
    pick: str = "avg"
    output_tag: str = ""


@router.put("/scenarios/{sid}/target")
def put_target(sid: int, body: TargetIn):
    if not body.name.strip():
        raise HTTPException(400, "请填写目标名称")
    if body.lo >= body.hi:
        raise HTTPException(400, "合理范围的下限要小于上限")
    t = body.model_dump()
    t["name"] = t["name"].strip()
    if t["type"] == "fc" and not t["horizons"]:
        t["horizons"] = [15]
    if t["type"] != "fc":
        t["horizons"] = []
    with db.session() as s:
        sc = _load(s, sid)
        sc.config["target"] = t
        cr = sc.config["rules"].setdefault("__calib__", calib.calib_rules_default(t))
        if cr.get("range", {}).get("src") == "估算目标":
            cr["range"]["lo"], cr["range"]["hi"] = t["lo"], t["hi"]
        db.touch_json(sc)
        return t


# ---------------------------------------------------------------- 参数与时段
@router.get("/scenarios/{sid}/tags")
def scenario_tags(sid: int):
    with db.session() as s:
        sc = _load(s, sid)
    tpl_rec = set()
    from ..core import templates
    t = templates.all_templates().get(sc.template_id, {})
    tpl_rec = set(t.get("recommended", []))
    rows = cx.tag_rows(sc.furnace)
    for r in rows:
        r["recommended"] = int(r["name"] in tpl_rec) if tpl_rec else int(r["recommended"])
    return rows


class Exclude(BaseModel):
    start: str
    end: str
    reason: str = ""


class DataIn(BaseModel):
    tags: list[str] | None = None
    start: str | None = None
    end: str | None = None
    excludes: list[Exclude] | None = None
    calib_use: dict[str, bool] | None = None
    preview: list[str] | None = None


@router.put("/scenarios/{sid}/data")
def put_data(sid: int, body: DataIn):
    with db.session() as s:
        sc = _load(s, sid)
        d = sc.config["data"]
        rows = {r["tag"]: r for r in cx.tag_rows(sc.furnace)}
        a, b = cx.src().span(sc.furnace)
        if body.tags is not None:
            for t in body.tags:
                if t not in rows:
                    raise HTTPException(400, f"位号 {t} 不在本设备")
                if int(rows[t]["is_target"]):
                    raise HTTPException(400, f"{rows[t]['name']} 是估算目标，不能选作输入")
            d["tags"] = list(dict.fromkeys(body.tags))
            for t in d["tags"]:
                if t not in sc.config["rules"]:
                    sc.config["rules"][t] = cx.rules_for({}, sc.furnace, t, sc.template_id)
        st = body.start or d["start"]
        en = body.end or d["end"]
        try:
            ps, pe = pd.Timestamp(st), pd.Timestamp(en)
        except Exception:
            raise HTTPException(400, "日期格式不对")
        if ps > pe:
            raise HTTPException(400, "开始日期不能晚于结束日期")
        d["start"] = max(ps, a.normalize()).strftime("%Y-%m-%d")
        d["end"] = min(pe, b.normalize()).strftime("%Y-%m-%d")
        if body.excludes is not None:
            ex = []
            for e in body.excludes:
                if pd.Timestamp(e.start) > pd.Timestamp(e.end):
                    raise HTTPException(400, "排除时段的开始日期不能晚于结束日期")
                ex.append({"start": e.start, "end": e.end, "reason": e.reason or "未注明"})
            d["excludes"] = sorted(ex, key=lambda x: x["start"])
        if body.calib_use is not None:
            d.setdefault("calib_use", {}).update(body.calib_use)
        if body.preview is not None:
            d["preview"] = [t for t in body.preview if t in d["tags"]][:5]
        db.touch_json(sc)
        return d


@router.get("/scenarios/{sid}/data/summary")
def data_summary(sid: int):
    with db.session() as s:
        sc = _load(s, sid)
        imps = _imports(s, sid)
    return data_page.summary(sc, imps)


@router.get("/scenarios/{sid}/preview")
def data_preview(sid: int, tags: str = "", start: str | None = None, end: str | None = None,
                 buckets: int = Query(600, ge=50, le=3000), calib_on: bool = True):
    with db.session() as s:
        sc = _load(s, sid)
        imps = _imports(s, sid)
    tl = [t for t in tags.split(",") if t][:5]
    return data_page.preview(sc, tl, start, end, buckets, calib_on, imps)


# ---------------------------------------------------------------- 处理规则
CALIB_ACTIONS = {"range": ["标为无效，不作为校核值"], "tc_fail": ["标为可疑，不作为校核值"],
                 "align": ["按测量时刻与预测配对"], "decoke": ["剔除，不作为校核值"]}
CALIB_TITLES = {"range": "物理范围", "tc_fail": "热电偶失效", "align": "时间对齐", "decoke": "烧焦期间"}


def _describe_calib(k, r, unit):
    if k == "range":
        return f"{quality.fmt(r['lo'])} ~ {quality.fmt(r['hi'])} {unit}，超出标记无效"
    if k == "tc_fail":
        return f"开路满量程或 1 分钟跳变 > {quality.fmt(r.get('jump', 30))} {unit} 标为失效"
    if k == "align":
        return "按测量时刻对齐，不插值"
    if k == "decoke":
        return "烧焦、停炉期间的测量不作为校核值"
    return ""


def rules_view(sc) -> dict:
    items = []
    rows = {r["tag"]: r for r in cx.tag_rows(sc.furnace)}
    for t in sc.config["data"].get("tags", []):
        r = cx.rules_for(sc.config["rules"], sc.furnace, t, sc.template_id)
        lst = []
        for k in quality.RULE_ORDER:
            if k not in r:
                continue
            v = r[k]
            lst.append({"kind": k, "title": quality.RULE_TITLES[k], "params": v,
                        "desc": quality.describe(k, v, rows[t]["unit"]), "actions": quality.ACTIONS[k],
                        "src": v.get("src") if k == "range" else None})
        items.append({"key": t, "name": rows[t]["name"], "unit": rows[t]["unit"], "rules": lst})
    cr = sc.config["rules"].get("__calib__") or calib.calib_rules_default(sc.config["target"])
    unit = sc.config["target"].get("unit", "")
    items.append({"key": "__calib__", "name": f"{sc.config['target']['name']}（校核）", "unit": unit,
                  "rules": [{"kind": k, "title": CALIB_TITLES[k], "params": v, "desc": _describe_calib(k, v, unit),
                             "actions": CALIB_ACTIONS[k], "src": v.get("src") if k == "range" else None}
                            for k, v in cr.items()]})
    return items


@router.get("/scenarios/{sid}/rules")
def get_rules(sid: int):
    with db.session() as s:
        sc = _load(s, sid)
        vers = list(s.scalars(select(db.RuleVersion).where(db.RuleVersion.scenario_id == sid)
                              .order_by(db.RuleVersion.id.desc())))
        last = vers[0] if vers else None
        import json
        dirty = last is None or json.dumps(last.rules, sort_keys=True) != json.dumps(sc.config["rules"], sort_keys=True)
        nxt = f"R-v{len(vers) + 1}"
        return {"items": rules_view(sc), "code": last.code if last else None, "dirty": dirty, "next": nxt,
                "versions": [{"code": v.code, "note": v.note, "created_at": f"{v.created_at:%Y-%m-%d %H:%M}"}
                             for v in vers]}


class RulesIn(BaseModel):
    rules: dict


@router.put("/scenarios/{sid}/rules/{key}")
def put_rules(sid: int, key: str, body: RulesIn):
    with db.session() as s:
        sc = _load(s, sid)
        cur = copy.deepcopy(cx.rules_for(sc.config["rules"], sc.furnace, key, sc.template_id)) \
            if key != "__calib__" else copy.deepcopy(sc.config["rules"].get("__calib__", {}))
        for k, v in body.rules.items():
            if k not in cur:
                continue
            for kk, vv in v.items():
                if kk in ("lo", "hi", "tol", "jump") and vv is not None:
                    vv = float(vv)
                if kk in ("minutes", "max_minutes", "window", "action"):
                    vv = int(vv)
                    if kk != "action" and vv < 1:
                        raise HTTPException(400, "时间参数必须大于 0")
                cur[k][kk] = vv
            if k == "range" and "lo" in v or k == "range" and "hi" in v:
                if cur["range"]["lo"] >= cur["range"]["hi"]:
                    raise HTTPException(400, "物理范围的下限要小于上限")
                cur["range"]["src"] = "手工"
        sc.config["rules"][key] = cur
        db.touch_json(sc)
    return get_rules(sid)


@router.post("/scenarios/{sid}/rules/save")
def save_rules(sid: int):
    with db.session() as s:
        sc = _load(s, sid)
        code = datasets.ensure_rule_version(s, sc, "手动保存")
    return {"code": code, **get_rules(sid)}


# ---------------------------------------------------------------- 校核数据导入
@router.post("/scenarios/{sid}/calib/import")
async def import_calib(sid: int, file: UploadFile = File(...)):
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
        df = calib.normalize(df)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "文件解析失败，请按导入模板填写")
    n_all = len(df)
    df = df.dropna(subset=["value"])
    if df.empty:
        raise HTTPException(400, "没有解析到有效的校核记录")
    df["source"] = df["source"].fillna("导入")
    with db.session() as s:
        sc = _load(s, sid)
        t = sc.config["target"]
        oor = int(((df["value"] < t["lo"]) | (df["value"] > t["hi"])).sum())
        im = db.CalibImport(scenario_id=sid, furnace=sc.furnace, filename=name, rows=len(df), status="待审核",
                            note=f"{oor} 条超出合理范围" if oor else "")
        s.add(im)
        s.flush()
        df.to_parquet(cx.import_path(im.id), index=False)
        sc.config["data"].setdefault("calib_use", {})[f"imp:{im.id}"] = True
        db.touch_json(sc)
        return {"id": im.id, "filename": name, "rows": len(df), "skipped": n_all - len(df), "out_of_range": oor,
                "from": f"{df['measured_at'].min():%Y-%m-%d}", "to": f"{df['measured_at'].max():%Y-%m-%d}"}


class ReviewIn(BaseModel):
    action: str  # approve / void


@router.post("/calib-imports/{iid}/review")
def review_import(iid: int, body: ReviewIn):
    with db.session() as s:
        im = s.get(db.CalibImport, iid)
        if not im:
            raise HTTPException(404, "记录不存在")
        im.status = "已审核" if body.action == "approve" else "已作废"
        return {"id": im.id, "status": im.status}


@router.get("/calib-template.csv")
def calib_template():
    txt = "测量时刻,测量位置,校核值,仪器,测量人\n2026-10-08 08:30,出口段 1# 管,1046,红外高温计 IR-03,张工\n"
    return Response(content=("﻿" + txt).encode("utf-8"), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''%E6%A0%A1%E6%A0%B8%E6%95%B0%E6%8D%AE%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.csv"})


# ---------------------------------------------------------------- 数据版本
@router.get("/scenarios/{sid}/datasets")
def list_datasets(sid: int):
    with db.session() as s:
        return [datasets.ds_dict(d) for d in s.scalars(select(db.Dataset).where(db.Dataset.scenario_id == sid)
                                                         .order_by(db.Dataset.id.desc()))]


@router.post("/scenarios/{sid}/datasets")
def create_dataset(sid: int):
    try:
        return datasets.create(sid)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- 场景概览
@router.get("/scenarios/{sid}/overview")
def overview(sid: int):
    with db.session() as s:
        sc = _load(s, sid)
        dss = list(s.scalars(select(db.Dataset).where(db.Dataset.scenario_id == sid).order_by(db.Dataset.id.desc())))
        rvs = list(s.scalars(select(db.RuleVersion).where(db.RuleVersion.scenario_id == sid)))
    d = sc.config["data"]
    from ..services import regime_page, spec as sp
    from ..core import pipeline
    ci = STAGES.index(sc.stage) if sc.stage in STAGES else 0
    info = [f"{len(d.get('tags', []))} 个参数 · {d['start'][:7]} 至 {d['end'][:7]}" +
            (f" · 最新 {dss[0].code}" if dss else " · 尚未生成数据版本")] + ["未开始"] * (len(STAGES) - 1)
    if ci >= 1:
        info[1] = regime_page.info_line(sc) + (f" · {sc.config['regime'].get('code')}" if sc.config.get("regime", {}).get("code") else "")
    if ci >= 2:
        ncol = len([c for c in pipeline.columns(sp.build_spec(sc)) if not c.get("excluded")])
        info[2] = f"{ncol} 个输入" + (f" · {sc.config['features'].get('code')}" if sc.config.get("features", {}).get("code") else "")
    return {
        "stages": [{"name": n, "state": "done" if i < ci else "cur" if i == ci else "todo", "info": info[i]}
                   for i, n in enumerate(STAGES)],
        "definition": [["场景模板", data_template_name(sc)], ["建模类型", sc.type],
                       ["设备", sc.furnace], ["校核数据来源", "TMT 热电偶 / 红外测温"],
                       ["输出", "当前估计值 + P10–P90 区间" if sc.type == "软测量" else "未来值"],
                       ["适用工况", "、".join(c["name"] for c in sc.config.get("regime", {}).get("classes", []) if c.get("include")) or "待分工况后确定"]],
        "assets": [["数据版本", f"{len(dss)} 个" + (f"（最新 {dss[0].code}）" if dss else "")],
                   ["数据处理规则", sc.config.get("rules_code") or "—"], ["规则版本数", f"{len(rvs)} 个"],
                   ["工况方案", sc.config.get("regime", {}).get("code") or "—"],
                   ["输入量配置", sc.config.get("features", {}).get("code") or "—"], ["训练次数", "0 次"], ["模型版本", "—"],
                   ["检验报告", "—"], ["投用位置", "—"]],
    }


def data_template_name(sc):
    from ..core import templates
    return templates.all_templates().get(sc.template_id, {}).get("name", sc.template_id)
