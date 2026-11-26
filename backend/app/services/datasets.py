"""生成数据版本 DS-vN：保存参数清单、时间范围、排除时段、规则版本、校核来源和质量标记。

原始数据不动；处理后的值与质量标记另存在 var/datasets/<场景>/<DS-vN>/。
"""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
from sqlalchemy import select

from .. import config
from ..store import db
from . import context as cx
from . import data_page
from .scenarios import next_code


def ensure_rule_version(s, sc: db.Scenario, note="") -> str:
    """规则草稿与最新版本不同时，保存为新的 R-vN。"""
    last = s.scalars(select(db.RuleVersion).where(db.RuleVersion.scenario_id == sc.id)
                     .order_by(db.RuleVersion.id.desc())).first()
    if last is not None and json.dumps(last.rules, sort_keys=True) == json.dumps(sc.config["rules"], sort_keys=True):
        return last.code
    code = next_code(s, db.RuleVersion, sc.id, "R-v")
    s.add(db.RuleVersion(scenario_id=sc.id, code=code, rules=copy.deepcopy(sc.config["rules"]), note=note))
    sc.config["rules_code"] = code
    db.touch_json(sc)
    return code


def create(sc_id: int) -> dict:
    with db.session() as s:
        sc = s.get(db.Scenario, sc_id)
        imports = list(s.scalars(select(db.CalibImport).where(db.CalibImport.scenario_id == sc_id)))
        if not sc.config["data"].get("tags"):
            raise ValueError("请先选择参数")
        rule_code = ensure_rule_version(s, sc, "生成数据版本时自动保存")
        code = next_code(s, db.Dataset, sc.id, "DS-v")
        cfg = sc.config
        d = cfg["data"]
        idx = cx.index(sc.furnace)
        period = cx.period_mask(idx, d["start"], d["end"], d.get("excludes", []))
        rng_mask = cx.period_mask(idx, d["start"], d["end"], [])
        labels = data_page.labels_offline(sc)
        out_dir = config.VAR_DIR / "datasets" / f"S{sc.id}" / code
        out_dir.mkdir(parents=True, exist_ok=True)
        vals, flags = {}, {}
        for t in d["tags"]:
            res = data_page.tag_quality(sc, t, labels)
            vals[t] = res.value[rng_mask].astype("float32")
            flags[t] = res.flags[rng_mask]
        ts = idx[rng_mask]
        pd.DataFrame({"timestamp": ts, **vals}).to_parquet(out_dir / "values.parquet", index=False)
        fdf = pd.DataFrame({"timestamp": ts, **flags})
        fdf["excluded"] = ~period[rng_mask]
        fdf.to_parquet(out_dir / "flags.parquet", index=False)
        rows, samples = data_page.calib_table(sc, imports, labels, idx, period)
        samples.to_parquet(out_dir / "calib.parquet", index=False)
        summ = data_page.summary(sc, imports)
        stats = {"n_tags": len(d["tags"]), "calib_points": int(len(samples)),
                 "lost_pct": summ["impact"]["lost_pct"], "eff_days": summ["gen"]["eff_days"],
                 "minutes": int(period.sum())}
        conf = {"tags": d["tags"], "tag_names": {r["tag"]: r["name"] for r in cx.tag_rows(sc.furnace)
                                                 if r["tag"] in d["tags"]},
                "start": d["start"], "end": d["end"], "excludes": d.get("excludes", []),
                "rule_code": rule_code, "calib_sources": [r["id"] for r in rows if r["use"] and r["training"]],
                "calib_names": [r["name"] for r in rows if r["use"] and r["training"]],
                "target": copy.deepcopy(cfg["target"]), "furnace": sc.furnace, "path": str(out_dir)}
        (out_dir / "manifest.json").write_text(json.dumps({"code": code, **conf, "stats": stats},
                                                          ensure_ascii=False, indent=2, default=str), "utf-8")
        ds = db.Dataset(scenario_id=sc.id, code=code, rule_code=rule_code, config=conf, stats=stats, used_by=[])
        s.add(ds)
        s.flush()
        return ds_dict(ds)


def ds_dict(ds: db.Dataset) -> dict:
    return {"code": ds.code, "start": ds.config["start"], "end": ds.config["end"], "n_tags": ds.stats["n_tags"],
            "calib_points": ds.stats["calib_points"], "rule_code": ds.rule_code, "lost_pct": ds.stats["lost_pct"],
            "created_at": f"{ds.created_at:%Y-%m-%d %H:%M}", "used_by": ds.used_by,
            "excludes": ds.config.get("excludes", []), "calib_names": ds.config.get("calib_names", [])}
