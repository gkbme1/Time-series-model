"""训练和上线共用的一条计算链：原始数据 → 处理规则 → 工况判定（causal）→ 加工输入。

- 训练：对整段历史调用 build()，再在校核样本时刻取行。
- 上线：对"当前时刻往前一段历史"调用同一个 build()，取最后一行。
两者只有输入数据的长短不同，计算代码完全一样（见 tests/test_no_future.py）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from . import features as F
from . import quality, regime


@dataclass
class Spec:
    tags: list[str]  # 选作输入的位号
    names: dict[str, str]  # 位号 → 参数名（本设备全部参数）
    units: dict[str, str]
    rules: dict[str, dict]  # 位号 → 处理规则
    params: dict[str, dict]  # 位号 → 加工方式
    lags: dict[str, dict] = field(default_factory=dict)
    combos: list[dict] = field(default_factory=list)
    regime_rules: list[dict] = field(default_factory=list)
    cycle_mode: str = "both"
    cycle_anchor: str | None = None  # 数据起点之前最近一次周期起点（烧焦 / 停炉结束）
    event_anchors: dict = field(default_factory=dict)  # 其他事件（如原料切换）在数据起点之前最近一次结束的时刻

    @property
    def name2tag(self) -> dict[str, str]:
        return {v: k for k, v in self.names.items()}


def required_tags(spec: Spec) -> list[str]:
    n2t = spec.name2tag
    out = list(spec.tags)
    for p in regime.needed_params(spec.regime_rules):
        t = n2t.get(p)
        if t and t not in out:
            out.append(t)
    for c in spec.combos:
        if c.get("on") and c.get("param") and c["param"] not in out:
            out.append(c["param"])
    return out


def columns(spec: Spec) -> list[dict]:
    """训练表中将出现的输入列（不计算数据）。"""
    out = []
    for t in spec.tags:
        p = spec.params.get(t) or F.default_param_spec(spec.names[t], None)
        name, unit = spec.names[t], spec.units.get(t, "")
        if p.get("cur", True):
            out.append({"col": F.col_name(name, "cur"), "source": t, "desc": F.col_desc("cur", None, unit),
                        "kind": "cur", "tag": t})
        for k, _ in F.KINDS:
            kk = p.get(k) or {}
            if kk.get("on"):
                src = spec.lags.get(t, {}).get("src") if k == "lag" else None
                out.append({"col": F.col_name(name, k, kk["min"]), "source": t,
                            "desc": F.col_desc(k, kk["min"], unit, src), "kind": k, "tag": t})
    for c in spec.combos:
        if not c.get("on"):
            continue
        src = {"formula": "组合量", "op": "累计量" if c.get("op") != "ema" else "组合量", "script": "脚本"}[c["kind"]]
        out.append({"col": c["name"], "source": src, "desc": F.combo_desc(c, spec.names), "kind": c["kind"],
                    "excluded": c["kind"] == "script"})
    return out


def default_quality(tag: str, raw: np.ndarray, rules: dict) -> np.ndarray:
    core = {k: v for k, v in rules.items() if k != "decoke"}
    return quality.apply_rules(raw, core).value


def build(raw: pd.DataFrame, spec: Spec,
          quality_fn: Callable[[str, np.ndarray, dict], np.ndarray] | None = None) -> tuple[pd.DataFrame, np.ndarray]:
    """返回 (输入列宽表, causal 工况标签)。raw 为 1 分钟等间隔原始宽表（缺失为 NaN）。"""
    qf = quality_fn or default_quality
    n = len(raw)
    vals: dict[str, np.ndarray] = {}
    for t in required_tags(spec):
        arr = raw[t].to_numpy(dtype="float64") if t in raw else np.full(n, np.nan)
        vals[t] = qf(t, arr, spec.rules.get(t, {}))
    by_name = {spec.names[t]: v for t, v in vals.items()}
    labels = regime.evaluate(spec.regime_rules, by_name, n, causal=True)

    cols: dict[str, np.ndarray] = {}
    for c in columns(spec):
        if c.get("excluded"):
            continue
        if c["kind"] in ("cur", "lag", "mean", "std", "rate"):
            p = spec.params.get(c["tag"]) or {}
            m = None if c["kind"] == "cur" else (p.get(c["kind"]) or {}).get("min")
            cols[c["col"]] = F.param_feature(vals[c["tag"]], c["kind"], m)
    for cb in spec.combos:
        if not cb.get("on") or cb["kind"] == "script":
            continue
        if cb["kind"] == "formula":
            sel = {spec.names[t]: vals[t] for t in spec.tags}
            cols[cb["name"]], _ = F.eval_formula(cb["expr"], sel, cb.get("zero", "invalid"))
        else:
            cols[cb["name"]] = eval_combo_op(cb, labels, vals, raw.index, spec)
    return pd.DataFrame(cols, index=raw.index), labels


def eval_combo_op(cb: dict, labels: np.ndarray, vals: dict[str, np.ndarray], index: pd.DatetimeIndex,
                  spec: Spec) -> np.ndarray:
    ev = cb.get("event", "cycle")
    in_ev = F.event_marks(labels, ev, spec.cycle_mode)
    offset = None
    anchor = spec.cycle_anchor if ev == "cycle" else (spec.event_anchors or {}).get(ev)
    if anchor:
        offset = (index[0] - pd.Timestamp(anchor)).total_seconds() / 60
        if offset < 0:
            offset = None
    x = vals.get(cb.get("param")) if cb.get("param") else None
    return F.eval_op(cb["op"], in_ev, x, float(cb.get("tau", 3)), offset, nan_in_event=(ev == "cycle"))
