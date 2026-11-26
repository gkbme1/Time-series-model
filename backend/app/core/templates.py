"""场景模板（data/templates/*.json）。"""
from __future__ import annotations

import json
from functools import lru_cache

from .. import config


@lru_cache(maxsize=1)
def all_templates() -> dict[str, dict]:
    out = {}
    order = ["cracking_tmt", "coke", "gas", "col", "fc", "blank"]
    files = sorted((config.DATA_DIR / "templates").glob("*.json"),
                   key=lambda p: order.index(p.stem) if p.stem in order else 99)
    for p in files:
        t = json.loads(p.read_text("utf-8"))
        out[t["id"]] = t
    return out


def get(tid: str) -> dict:
    t = all_templates().get(tid)
    if t is None:
        raise KeyError(tid)
    return t
