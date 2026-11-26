"""加工输入：把处理后的参数序列加工成模型直接用的输入列。

所有窗口、滞后只往回算：t 时刻的值只用 t 及以前的数据。训练和上线调用同一份代码
（core/pipeline.py 负责串起"处理规则 → 工况判定 → 本模块"）。

参数加工方式：
    cur   当前值
    lag   N 分钟前的值
    mean  过去 N 分钟均值（有效点 ≥ 80% 才计算）
    std   过去 N 分钟标准差（同上）
    rate  N 分钟变化率 = (x_t − x_{t−N}) / N × 60，单位"每小时"

组合量：
    formula  四则运算 + abs/log/sqrt，用 ast 白名单解析，不用 eval
    op       内置算子：自事件起的累计时间、自事件起的累计量、距上次事件多久、指数平滑
    script   只保存文本，Demo 不执行
"""
from __future__ import annotations

import ast
import re

import numpy as np
import pandas as pd

KINDS = [("lag", "过去某时刻的值"), ("mean", "一段时间平均"), ("std", "一段时间波动"), ("rate", "变化快慢")]
DEFAULT_MIN = {"lag": 5, "mean": 10, "std": 10, "rate": 30}
MIN_VALID = 0.8

OPS = {"since": "自事件起的累计时间", "cum": "自事件起的累计量", "ago": "距上次事件多久", "ema": "指数平滑"}
OP_UNIT = {"since": "天", "ago": "小时"}
EVENTS = {"cycle": "烧焦或停炉结束", "sw": "原料切换"}


# ---------------------------------------------------------------------------
# 参数加工
# ---------------------------------------------------------------------------
def col_name(name: str, kind: str, minutes: int | None = None) -> str:
    if kind == "cur":
        return name
    return {"lag": f"{name} · {minutes} 分钟前", "mean": f"{name} · {minutes} 分钟均值",
            "std": f"{name} · {minutes} 分钟波动", "rate": f"{name} · {minutes} 分钟变化率"}[kind]


def col_desc(kind: str, minutes: int | None, unit: str, lag_src: str | None = None) -> str:
    if kind == "cur":
        return "当前值"
    if kind == "lag":
        return f"取 {minutes} 分钟前的值" + (f"（{lag_src}）" if lag_src else "")
    if kind == "mean":
        return f"过去 {minutes} 分钟的平均"
    if kind == "std":
        return f"过去 {minutes} 分钟的标准差"
    return f"(当前值 − {minutes} 分钟前的值) ÷ {minutes} × 60，{unit}/h"


def param_feature(x: np.ndarray, kind: str, minutes: int | None) -> np.ndarray:
    s = pd.Series(x)
    if kind == "cur":
        return s.to_numpy(dtype="float64", copy=True)
    n = int(minutes)
    if kind == "lag":
        return s.shift(n).to_numpy(dtype="float64")
    mp = max(1, int(np.ceil(n * MIN_VALID)))
    if kind == "mean":
        return s.rolling(n, min_periods=mp).mean().to_numpy(dtype="float64")
    if kind == "std":
        return s.rolling(n, min_periods=max(2, mp)).std().to_numpy(dtype="float64")
    if kind == "rate":
        return ((s - s.shift(n)) / n * 60).to_numpy(dtype="float64")
    raise ValueError(kind)


def default_param_spec(name: str, tpl_default: dict | None) -> dict:
    d = tpl_default or {}
    spec = {"cur": bool(d.get("cur", True))}
    for k, _ in KINDS:
        spec[k] = {"on": k in d, "min": int(d.get(k, DEFAULT_MIN[k]))}
    return spec


# ---------------------------------------------------------------------------
# 公式：安全表达式解析（ast 白名单）
# ---------------------------------------------------------------------------
FUNCS = {"abs": np.abs, "log": np.log, "sqrt": np.sqrt}
ZERO_EPS = 1e-6  # |除数| 小于此值视为零
_SYM = str.maketrans({"÷": "/", "×": "*", "−": "-", "＋": "+", "（": "(", "）": ")", "–": "-"})


class ExprError(ValueError):
    pass


def parse_formula(expr: str) -> tuple[ast.Expression, list[str]]:
    """把 "{稀释蒸汽流量} / {原料流量}" 解析为语法树，返回 (树, 用到的参数名)。"""
    if not expr or not expr.strip():
        raise ExprError("请先写表达式")
    names: list[str] = []

    def repl(m):
        n = m.group(1).strip()
        if n not in names:
            names.append(n)
        return f"_v{names.index(n)}"

    src = re.sub(r"\{([^{}]+)\}", repl, expr.translate(_SYM))
    if "{" in src or "}" in src:
        raise ExprError("参数要用 { } 括起来，且不能嵌套")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise ExprError("表达式写法有误")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load)):
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            continue
        if isinstance(node, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            continue
        if isinstance(node, ast.Name) and (re.fullmatch(r"_v\d+", node.id) or node.id in FUNCS):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS \
                and len(node.args) == 1 and not node.keywords:
            continue
        raise ExprError("表达式里有不支持的写法（只支持 + − × ÷、括号、数字、abs / log / sqrt 和 {参数}）")
    return tree, names


def eval_formula(expr: str, values: dict[str, np.ndarray], zero: str = "invalid") -> tuple[np.ndarray, int]:
    """计算公式。返回 (结果, 无效点数)。除数为零（|除数| < 1e-6）、log/sqrt 定义域外的点为无效；
    zero = "hold" 时无效点沿用上一个有效值（只用历史值）。"""
    tree, names = parse_formula(expr)
    vs = {}
    for i, n in enumerate(names):
        if n not in values:
            raise ExprError(f"\"{n}\"不在\"选数据\"已选参数中")
        vs[f"_v{i}"] = np.asarray(values[n], dtype="float64")
    n = len(next(iter(vs.values()))) if vs else 1
    bad = np.zeros(n, bool)

    def ev(node):
        nonlocal bad
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return np.full(n, float(node.value))
        if isinstance(node, ast.Name):
            return vs[node.id]
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            # 只看当前点本身（不能用整段数据的统计量，否则会用到未来数据）
            tiny = np.abs(b) < ZERO_EPS
            bad = bad | (tiny & np.isfinite(b))
            with np.errstate(all="ignore"):
                return np.where(tiny, np.nan, a / np.where(tiny, 1.0, b))
        if isinstance(node, ast.Call):
            v = ev(node.args[0])
            f = node.func.id
            with np.errstate(all="ignore"):
                if f == "log":
                    bad = bad | (np.isfinite(v) & (v <= 0))
                    return np.where(v > 0, np.log(np.where(v > 0, v, 1.0)), np.nan)
                if f == "sqrt":
                    bad = bad | (np.isfinite(v) & (v < 0))
                    return np.where(v >= 0, np.sqrt(np.where(v >= 0, v, 0.0)), np.nan)
                return np.abs(v)
        raise ExprError("表达式写法有误")

    out = np.asarray(ev(tree), dtype="float64").copy()
    out[~np.isfinite(out)] = np.nan
    nbad = int(bad.sum())
    if zero == "hold":
        out = pd.Series(out).ffill().to_numpy()
    return out, nbad


# ---------------------------------------------------------------------------
# 内置算子（事件来自工况判定：只用到当前及以前已经发生的事件）
# ---------------------------------------------------------------------------
def event_marks(causal_labels: np.ndarray, event: str, cycle_mode: str) -> np.ndarray:
    """事件进行中的分钟为 True。cycle：烧焦（及停炉，取决于周期边界设置）；sw：原料切换。"""
    if event == "sw":
        return causal_labels == "sw"
    evs = {"both": ("burn", "stop"), "burn": ("burn",), "none": ()}.get(cycle_mode, ("burn", "stop"))
    return np.isin(causal_labels, evs) if evs else np.zeros(len(causal_labels), bool)


def eval_op(op: str, in_event: np.ndarray, x: np.ndarray | None = None, tau_days: float = 3.0,
            start_offset_min: float | None = None, nan_in_event: bool = True) -> np.ndarray:
    """op: since（天）/ ago（小时）/ cum（参数 × 小时）/ ema。
    start_offset_min：数据起点之前的上一次事件结束距数据起点的分钟数（第一个周期起点早于数据起点）。"""
    n = len(in_event)
    idx = np.arange(n, dtype="float64")
    if op == "ema":
        a = 1 - np.exp(-1.0 / max(1.0, tau_days * 1440))
        out = np.full(n, np.nan)
        s = np.nan
        xv = np.asarray(x, dtype="float64")
        for i in range(n):  # 逐点递推，只用历史；缺失时保持
            v = xv[i]
            if np.isfinite(v):
                s = v if not np.isfinite(s) else s + a * (v - s)
            out[i] = s
        return out
    # 上一次事件结束的位置（事件中的最后一分钟 + 1）
    end_mark = np.zeros(n, bool)
    end_mark[1:] = in_event[:-1] & ~in_event[1:]
    first = -float(start_offset_min) if start_offset_min is not None else np.nan
    last_end = np.where(end_mark, idx, np.nan)
    last_end[0] = last_end[0] if end_mark[0] else first
    last_end = pd.Series(last_end).ffill().to_numpy()
    if op in ("since", "ago"):
        el = idx - last_end
        out = el / 1440 if op == "since" else el / 60
    elif op == "cum":
        # 每分钟的量（参数为每小时的量，例如 t/h）/ 60，自上一次事件结束起累加；缺失按 0
        xv = np.nan_to_num(np.asarray(x, dtype="float64"), nan=0.0) / 60.0
        xv = np.where(in_event, 0.0, xv)
        cs = np.cumsum(xv)
        seg_start = np.maximum.accumulate(np.where(end_mark, np.arange(n), 0))
        prev = np.where(seg_start > 0, cs[np.maximum(seg_start - 1, 0)], 0.0)
        out = cs - prev
        if start_offset_min is None:
            out = np.where(np.isnan(last_end), np.nan, out)
    else:
        raise ValueError(op)
    if nan_in_event:
        out = np.where(in_event, np.nan, out)
    return out


def combo_desc(c: dict, tag_names: dict[str, str]) -> str:
    if c["kind"] == "formula":
        return c.get("expr", "").replace("{", "").replace("}", "")
    if c["kind"] == "op":
        s = OPS[c["op"]]
        if c["op"] != "ema":
            s += "：" + EVENTS.get(c.get("event", "cycle"), "")
        if c.get("param"):
            s += " · " + tag_names.get(c["param"], c["param"])
        if c["op"] == "ema":
            s += f" · 时间常数 {c.get('tau', 3)} 天"
        return s
    return f"Python 脚本 · 窗口 {c.get('window', 60)} 分钟（Demo 不执行）"
