"""SQLite 元数据：项目、场景、规则版本、数据版本、导入的校核来源等。

配置类内容（目标、选数据设置、规则草稿……）以 JSON 存在场景上；
有编号、需要追溯的对象（R-vN、DS-vN……）单独成表，彼此通过编号关联。
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from .. import config


class Base(DeclarativeBase):
    pass


def now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    device: Mapped[str] = mapped_column(String(200))
    ontology: Mapped[str] = mapped_column(String(200), default="乙烯装置本体 v2.1")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    opened_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20))  # 软测量 / 预测
    template_id: Mapped[str] = mapped_column(String(50))
    furnace: Mapped[str] = mapped_column(String(50))
    stage: Mapped[str] = mapped_column(String(20), default="选数据")
    status: Mapped[str] = mapped_column(String(20), default="数据准备")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    code: Mapped[str] = mapped_column(String(20))  # R-v1
    rules: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    code: Mapped[str] = mapped_column(String(20))  # DS-v1
    rule_code: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSON)
    stats: Mapped[dict] = mapped_column(JSON)
    used_by: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class ConfigVersion(Base):
    """工况方案 WC-vN、输入量配置 F-vN 等配置快照。"""
    __tablename__ = "config_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    kind: Mapped[str] = mapped_column(String(10))  # WC / F
    code: Mapped[str] = mapped_column(String(20))
    content: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class TrainBatch(Base):
    """一次"开始训练"= 一个批次，包含若干实验。"""
    __tablename__ = "train_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    code: Mapped[str] = mapped_column(String(20))  # 批次 N
    ds_code: Mapped[str] = mapped_column(String(20))
    rule_code: Mapped[str] = mapped_column(String(20))
    wc_code: Mapped[str] = mapped_column(String(20))
    f_code: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSON)
    sig: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("train_batches.id"))
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    code: Mapped[str] = mapped_column(String(20))  # EXP-01
    mode: Mapped[str] = mapped_column(String(10))
    algo: Mapped[str] = mapped_column(String(10))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="排队中")  # 排队中 / 训练中 / 已完成 / 失败
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage_text: Mapped[str] = mapped_column(String(100), default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    importance: Mapped[list] = mapped_column(JSON, default=list)
    label: Mapped[str] = mapped_column(String(40), default="")  # 待检验 / 已发布 XGB-V1 …
    error: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Candidate(Base):
    """送去检验的候选模型版本（XGB-V1 …）及其检验报告、适用范围、审批状态。"""
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    exp_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"))
    code: Mapped[str] = mapped_column(String(20))
    life: Mapped[str] = mapped_column(String(20), default="待检验")  # 开发中 / 待检验 / 检验通过 / 已发布 / 已归档
    status: Mapped[str] = mapped_column(String(20), default="排队中")  # 检验计算状态
    report_code: Mapped[str] = mapped_column(String(20), default="")
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    appl: Mapped[dict] = mapped_column(JSON, default=dict)  # 适用范围（草稿 / 已审核）
    history: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    package: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class CalibImport(Base):
    """用户导入的校核数据文件（内容另存为 Parquet）。"""
    __tablename__ = "calib_imports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    furnace: Mapped[str] = mapped_column(String(50))
    filename: Mapped[str] = mapped_column(String(300))
    rows: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="待审核")  # 待审核 / 已审核 / 已作废
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


_engine = None
_Session = None


def init(db_path=None):
    global _engine, _Session
    config.ensure_dirs()
    path = db_path or (config.VAR_DIR / "app.db")
    _engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(_engine, expire_on_commit=False)


@contextmanager
def session():
    if _Session is None:
        init()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def touch_json(obj, attr="config"):
    flag_modified(obj, attr)


__all__ = ["Project", "Scenario", "RuleVersion", "Dataset", "CalibImport", "ConfigVersion", "TrainBatch", "Experiment", "Candidate", "session", "init", "select", "now",
           "touch_json"]
