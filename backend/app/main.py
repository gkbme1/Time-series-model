"""FastAPI 入口。构建后的前端由这里一并托管，单端口运行。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import data, projects
from .services import scenarios
from .store import db

@asynccontextmanager
async def lifespan(_app):
    db.init()
    scenarios.seed()
    yield


app = FastAPI(title="时序建模与软测量平台 Demo", lifespan=lifespan)
app.include_router(projects.router)
app.include_router(data.router)


@app.get("/api/health")
def health():
    return {"ok": True}


if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = config.FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html")
