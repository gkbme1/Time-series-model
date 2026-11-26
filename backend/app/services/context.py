"""场景计算上下文：参数表、默认规则、质量结果缓存、工况标签、校核样本。

页面接口和数据版本生成都通过这里取数，保证同一套规则得到同一份结果。
"""
from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict
from functools import lru_cache

import numpy as np
import pandas as pd

from ..core import calib, quality, regime, templates
from ..core.datasource import default_source

_lock = threading.Lock()


class LRU:
    def __init__(self, size=128):
        self.size, self.d = size, OrderedDict()

    def get(self, k):
        with _lock:
            if k in self.d:
                self.d.move_to_end(k)
                return self.d[k]
        return None

    def put(self, k, v):
        with _lock:
            self.d[k] = v
            self.d.move_to_end(k)
            while len(self.d) > self.size:
                self.d.popitem(last=False)
        return v


_qcache = LRU(160)
_rcache = LRU(16)


def src():
    return default_source()


def tag_rows(furnace: str) -> list[dict]:
    t = src().tag_table()
    return t[t["furnace"] == furnace].to_dict("records")


def tag_row(furnace: str, tag: str) -> dict:
    for r in tag_rows(furnace):
        if r["tag"] == tag:
            return r
    raise KeyError(tag)


def tag_by_name(furnace: str, name: str) -> str | None:
    for r in tag_rows(furnace):
        if r["name"] == name:
            return r["tag"]
    return None


def target_tag(furnace: str) -> str | None:
    for r in tag_rows(furnace):
        if int(r["is_target"]) == 1:
            return r["tag"]
    return None


def index(furnace: str) -> pd.DatetimeIndex:
    a, b = src().span(furnace)
    return src().read(furnace, [], None, None).index


@lru_cache(maxsize=128)
def scales(furnace: str, tag: str) -> dict:
    return quality.robust_scales(src().read(furnace, [tag])[tag])


def default_rules(template_id: str, furnace: str, target: dict) -> dict:
    tpl = templates.get(template_id) if template_id in templates.all_templates() else {}
    ov = tpl.get("rules_override", {})
    base = tpl.get("rules_default", {})
    out = {}
    for r in tag_rows(furnace):
        if int(r["is_target"]):
            continue
        out[r["tag"]] = quality.default_rules(r, scales(furnace, r["tag"]), ov.get(r["name"]), base)
    out["__calib__"] = calib.calib_rules_default(target)
    return out


def rules_for(draft: dict, furnace: str, tag: str, template_id: str = "cracking_tmt", target=None) -> dict:
    if tag in draft:
        return draft[tag]
    tpl = templates.get(template_id) if template_id in templates.all_templates() else {}
    r = tag_row(furnace, tag)
    return quality.default_rules(r, scales(furnace, tag), tpl.get("rules_override", {}).get(r["name"]),
                                 tpl.get("rules_default", {}))


def core_rules(r: dict) -> dict:
    """参与质量计算的规则部分（烧焦期间单独叠加，避免与工况判定循环依赖）。"""
    return {k: v for k, v in r.items() if k != "decoke"}


def quality_of(furnace: str, tag: str, rules: dict) -> quality.QualityResult:
    cr = core_rules(rules)
    key = (furnace, tag, quality.rule_hash(cr))
    hit = _qcache.get(key)
    if hit is not None:
        return hit
    raw = src().read(furnace, [tag])[tag].to_numpy()
    return _qcache.put(key, quality.apply_rules(raw, cr))


def with_decoke(res: quality.QualityResult, rules: dict, decoke_mask: np.ndarray) -> quality.QualityResult:
    r = rules.get("decoke", {})
    if not r.get("on"):
        return res
    flags = res.flags.copy()
    flags[decoke_mask] |= quality.DECOKE
    val = res.value
    if r.get("action", 0) == 1:
        flags[decoke_mask] |= quality.UNUSABLE
        val = np.where(decoke_mask, np.nan, val)
    return quality.QualityResult(val, flags)


def regime_rules(scn_cfg: dict, template_id: str) -> list[dict]:
    rg = scn_cfg.get("regime", {})
    if rg.get("rules") is not None:
        return rg["rules"]
    tpl = templates.get(template_id) if template_id in templates.all_templates() else {}
    return tpl.get("regime_rules", [])


def regime_classes(scn_cfg: dict, template_id: str) -> list[dict]:
    rg = scn_cfg.get("regime", {})
    if rg.get("classes"):
        return rg["classes"]
    tpl = templates.get(template_id) if template_id in templates.all_templates() else {}
    return copy.deepcopy(tpl.get("regime_classes", [{"id": "norm", "name": "正常稳定", "color": "#0d7680",
                                                      "include": True}]))


def regime_labels(furnace: str, rules: list[dict], draft: dict, template_id: str, causal=False) -> np.ndarray:
    params = regime.needed_params(rules)
    tags = {p: tag_by_name(furnace, p) for p in params}
    key = (furnace, json.dumps(rules, sort_keys=True, ensure_ascii=False), causal,
           tuple(quality.rule_hash(core_rules(rules_for(draft, furnace, t, template_id))) for t in tags.values() if t))
    hit = _rcache.get(key)
    if hit is not None:
        return hit
    vals = {}
    for p, t in tags.items():
        if t:
            vals[p] = quality_of(furnace, t, rules_for(draft, furnace, t, template_id)).value
    n = len(index(furnace))
    return _rcache.put(key, regime.evaluate(rules, vals, n, causal=causal))


def period_mask(idx: pd.DatetimeIndex, start: str, end: str, excludes: list[dict]) -> np.ndarray:
    m = (idx >= pd.Timestamp(start)) & (idx < pd.Timestamp(end) + pd.Timedelta(days=1))
    for e in excludes or []:
        m &= ~((idx >= pd.Timestamp(e["start"])) & (idx < pd.Timestamp(e["end"]) + pd.Timedelta(days=1)))
    return np.asarray(m)


# ---------------------------------------------------------------------------
# 校核来源
# ---------------------------------------------------------------------------
def calib_sources(furnace: str, imports: list) -> list[dict]:
    out = []
    for m in calib.manifest():
        if m["furnace"] != furnace or m.get("role") == "online":
            continue
        if m["kind"] == "热电偶":
            out.append({"id": f"tc:{m['tag']}", "kind": "热电偶", "name": f"历史测点 {m['tag']} 热电偶（本地时序文件）",
                        "position": m.get("position", "出口段 1# 管"), "note": m["note"],
                        "default_use": m["default_use"], "status": "已审核", "tag": m["tag"]})
        else:
            out.append({"id": f"file:{m['file']}", "kind": m["kind"], "name": f"导入：{m['file']}",
                        "position": None, "note": m["note"], "default_use": m["default_use"], "status": "已审核",
                        "file": m["file"]})
    for im in imports:
        out.append({"id": f"imp:{im.id}", "kind": "红外", "name": f"导入：{im.filename}", "position": None,
                    "note": f"{im.created_at:%Y-%m-%d} 导入 · {im.status}" + (f" · {im.note}" if im.note else ""),
                    "default_use": True, "status": im.status, "import_id": im.id})
    return out


def import_path(import_id: int):
    from .. import config
    return config.VAR_DIR / "imports" / f"IMP-{import_id}.parquet"


@lru_cache(maxsize=64)
def _file_records_cached(fn: str) -> pd.DataFrame:
    return calib.file_records(fn)


def source_records(s: dict) -> pd.DataFrame:
    if "file" in s:
        return _file_records_cached(s["file"])
    if "import_id" in s:
        return pd.read_parquet(import_path(s["import_id"]))
    return pd.DataFrame(columns=calib.COLS)


def source_samples(furnace: str, s: dict, crules: dict, pick: str, exclude_mask_fn) -> pd.DataFrame:
    """一个来源的可用样本（已按校核规则过滤；exclude_mask_fn(t) 返回该时刻是否应剔除）。"""
    if s["kind"] == "热电偶":
        raw = src().read(furnace, [s["tag"]])[s["tag"]]
        df = calib.tc_samples(raw.to_numpy(), raw.index, crules)
    else:
        if s.get("status") != "已审核":
            return pd.DataFrame(columns=["t", "y", "n"])
        rec = calib.ir_valid(source_records(s), crules)
        df = calib.sessions(rec, pick)
    if len(df):
        df = df[~exclude_mask_fn(pd.DatetimeIndex(df["t"]))]
    return df.reset_index(drop=True)
