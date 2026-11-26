"""项目、场景、模板接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..core import templates
from ..services import scenarios as svc
from ..store import db

router = APIRouter(prefix="/api")


class ProjectIn(BaseModel):
    name: str
    device: str = "乙烯装置 · 裂解区"
    ontology: str = "乙烯装置本体 v2.1"


class ScenarioIn(BaseModel):
    name: str
    template_id: str
    furnace: str
    type: str = "软测量"


def project_dict(p: db.Project, scs) -> dict:
    return {"id": p.id, "name": p.name, "device": p.device, "ontology": p.ontology,
            "created_at": f"{p.created_at:%Y-%m-%d}", "opened_at": f"{p.opened_at:%Y-%m-%d %H:%M}" if p.opened_at else None,
            "scenarios": [svc.scenario_dict(x, with_config=False) for x in scs]}


@router.get("/projects")
def list_projects():
    with db.session() as s:
        ps = list(s.scalars(select(db.Project).order_by(db.Project.opened_at.desc().nullslast(), db.Project.id)))
        return [project_dict(p, list(s.scalars(select(db.Scenario).where(db.Scenario.project_id == p.id))))
                for p in ps]


@router.post("/projects")
def create_project(body: ProjectIn):
    if not body.name.strip():
        raise HTTPException(400, "请输入项目名称")
    with db.session() as s:
        p = db.Project(name=body.name.strip(), device=body.device, ontology=body.ontology, opened_at=db.now())
        s.add(p)
        s.flush()
        return project_dict(p, [])


@router.get("/projects/{pid}")
def get_project(pid: int):
    with db.session() as s:
        p = s.get(db.Project, pid)
        if not p:
            raise HTTPException(404, "项目不存在")
        return project_dict(p, list(s.scalars(select(db.Scenario).where(db.Scenario.project_id == pid))))


@router.post("/projects/{pid}/open")
def open_project(pid: int):
    with db.session() as s:
        p = s.get(db.Project, pid)
        if not p:
            raise HTTPException(404, "项目不存在")
        p.opened_at = db.now()
    return get_project(pid)


@router.post("/projects/{pid}/scenarios")
def create_scenario(pid: int, body: ScenarioIn):
    if body.template_id not in templates.all_templates():
        raise HTTPException(400, "模板不存在")
    if body.furnace not in [f["unit"] for f in svc.furnaces()]:
        raise HTTPException(400, "设备不存在")
    sc = svc.create_scenario(pid, body.name.strip() or templates.get(body.template_id)["name"], body.template_id,
                             body.furnace, body.type)
    return svc.scenario_dict(sc)


@router.get("/templates")
def list_templates():
    return list(templates.all_templates().values())


@router.get("/furnaces")
def list_furnaces():
    return svc.furnaces()


@router.get("/scenarios/{sid}")
def get_scenario(sid: int):
    with db.session() as s:
        sc = s.get(db.Scenario, sid)
        if not sc:
            raise HTTPException(404, "场景不存在")
        return svc.scenario_dict(sc)
