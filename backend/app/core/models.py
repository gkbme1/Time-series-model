"""模型：基线、线性回归（Ridge）、随机森林、XGBoost；预测区间；自动调参；输入重要性。

- 训练 / 调参 / 检验都按时间顺序划分，不随机抽样（调参用 TimeSeriesSplit）。
- 预测区间：XGBoost 用分位数回归（P10 / P90），再用按时间顺序交叉验证做共形校准（CQR）；
  线性回归、随机森林用训练集按时间顺序交叉验证的残差分位数。
- 建模方式：统一模型（工况作为输入，独热编码）/ 分工况多模型 / 只训练主工况。
- 模型对象可 pickle，发布时打包。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ALGS = {"base": "基线", "lin": "线性回归", "rf": "随机森林", "xgb": "XGBoost", "lstm": "LSTM"}
PREFIX = {"base": "BASE", "lin": "LIN", "rf": "RF", "xgb": "XGB", "lstm": "LSTM"}
MODES = {"uni": "统一模型", "per": "分工况多模型", "main": "只训练主工况"}
MIN_PER_REGIME = 15  # 分工况多模型：某工况训练样本少于此数时不单独建模（该工况不输出）

DEFAULTS = {
    "lin": {"alpha": 1.0},
    "rf": {"n_estimators": 300, "max_depth": 12, "min_samples_leaf": 3},
    "xgb": {"n_estimators": 600, "max_depth": 4, "learning_rate": 0.05, "subsample": 0.8,
            "colsample_bytree": 0.8, "min_child_weight": 3},
}
# 手动设置时的界面说明（与原型一致）
PARAM_META = {
    "xgb": [("max_depth", "树的深度", "越深越能拟合复杂关系，但越容易学到偶然规律", int),
            ("n_estimators", "树的数量", "越多越细，训练越慢", int),
            ("learning_rate", "学习步长", "越小越稳，需要更多的树", float),
            ("subsample", "每棵树用的样本比例", "小于 1 可以减少过拟合", float)],
    "rf": [("n_estimators", "树的数量", "一般 200–500 就够", int), ("max_depth", "树的深度", "太深容易过拟合", int)],
    "lin": [("alpha", "正则强度", "越大越保守，输入多时有用", float)],
}


def make_estimator(algo: str, params: dict, monotone: list[int] | None = None, objective: str | None = None,
                   alpha: float | None = None):
    p = {**DEFAULTS.get(algo, {}), **(params or {})}
    if algo == "lin":
        return make_pipeline(StandardScaler(), Ridge(alpha=float(p["alpha"])))
    if algo == "rf":
        return RandomForestRegressor(n_estimators=int(p["n_estimators"]), max_depth=int(p["max_depth"]),
                                     min_samples_leaf=int(p.get("min_samples_leaf", 3)), n_jobs=2, random_state=0)
    if algo == "xgb":
        import xgboost as xgb
        kw = dict(n_estimators=int(p["n_estimators"]), max_depth=int(p["max_depth"]),
                  learning_rate=float(p["learning_rate"]), subsample=float(p["subsample"]),
                  colsample_bytree=float(p.get("colsample_bytree", 0.8)), min_child_weight=float(p.get("min_child_weight", 3)),
                  tree_method="hist", n_jobs=2, random_state=0)
        if monotone and any(monotone):
            kw["monotone_constraints"] = "(" + ",".join(str(int(m)) for m in monotone) + ")"
        if objective:
            kw["objective"] = objective
            kw["quantile_alpha"] = alpha
        return xgb.XGBRegressor(**kw)
    raise ValueError(f"不支持的算法 {algo}")


def time_cv_splits(n: int, k: int = 4):
    k = max(2, min(k, n // 20))
    return list(TimeSeriesSplit(n_splits=k).split(np.arange(n)))


def oof_residuals(algo, params, X, y, monotone=None) -> np.ndarray:
    """按时间顺序交叉验证得到的样本外残差（y − 预测），用于给线性 / 随机森林估计区间。"""
    res = []
    for tr, te in time_cv_splits(len(y)):
        m = make_estimator(algo, params, monotone)
        m.fit(X[tr], y[tr])
        res.append(y[te] - m.predict(X[te]))
    return np.concatenate(res) if res else np.zeros(1)


def tune(algo: str, X: np.ndarray, y: np.ndarray, trials: int, monotone=None,
         progress: Callable[[float], None] | None = None) -> dict:
    """Optuna 自动调参：目标为按时间顺序交叉验证的平均偏差（MAE），不随机抽样。"""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    splits = time_cv_splits(len(y))

    def space(t):
        if algo == "xgb":
            return {"n_estimators": t.suggest_int("n_estimators", 150, 900, step=50),
                    "max_depth": t.suggest_int("max_depth", 2, 7),
                    "learning_rate": t.suggest_float("learning_rate", 0.01, 0.2, log=True),
                    "subsample": t.suggest_float("subsample", 0.5, 1.0),
                    "colsample_bytree": t.suggest_float("colsample_bytree", 0.4, 1.0),
                    "min_child_weight": t.suggest_float("min_child_weight", 1, 12)}
        if algo == "rf":
            return {"n_estimators": t.suggest_int("n_estimators", 100, 500, step=50),
                    "max_depth": t.suggest_int("max_depth", 3, 20),
                    "min_samples_leaf": t.suggest_int("min_samples_leaf", 1, 12)}
        return {"alpha": t.suggest_float("alpha", 1e-3, 100, log=True)}

    def obj(t):
        p = space(t)
        errs = []
        for tr, te in splits:
            m = make_estimator(algo, p, monotone)
            m.fit(X[tr], y[tr])
            errs.append(np.mean(np.abs(m.predict(X[te]) - y[te])))
        return float(np.mean(errs))

    st = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=0))
    for i in range(trials):
        st.optimize(obj, n_trials=1)
        if progress:
            progress((i + 1) / trials)
    best = {**DEFAULTS.get(algo, {}), **st.best_params}
    return {"params": best, "cv_mae": round(st.best_value, 3), "trials": trials}


@dataclass
class Fitted:
    """一个估计器 + 区间。"""
    algo: str
    point: object
    lo: object = None
    hi: object = None
    q: tuple[float, float] = (0.0, 0.0)  # 残差分位数（无分位数模型时）

    def predict(self, X):
        p = self.point.predict(X)
        if self.lo is not None:
            adj = -self.q[0]  # 共形校准量：分位数回归区间两端各放宽这么多
            lo, hi = self.lo.predict(X) - adj, self.hi.predict(X) + adj
            lo, hi = np.minimum(lo, p), np.maximum(hi, p)
        else:
            lo, hi = p + self.q[0], p + self.q[1]
        return p, lo, hi


def cqr_adjust(params, X, y, monotone=None, target=0.8) -> float:
    """共形分位数回归（CQR）：小样本上分位数回归的区间往往偏窄。用按时间顺序交叉验证的
    样本外结果，计算区间需要向两端放宽多少，才能让 P10–P90 覆盖约 80% 的样本外校核值。"""
    scores = []
    for tr, te in time_cv_splits(len(y)):
        lo = make_estimator("xgb", params, monotone, "reg:quantileerror", 0.1).fit(X[tr], y[tr]).predict(X[te])
        hi = make_estimator("xgb", params, monotone, "reg:quantileerror", 0.9).fit(X[tr], y[tr]).predict(X[te])
        scores.append(np.maximum(lo - y[te], y[te] - hi))
    sc = np.concatenate(scores)
    n = len(sc)
    return float(max(0.0, np.quantile(sc, min(1.0, target * (1 + 1 / n)))))


def fit_one(algo, params, X, y, monotone=None, interval=True) -> Fitted:
    m = make_estimator(algo, params, monotone)
    m.fit(X, y)
    f = Fitted(algo, m)
    if not interval:
        return f
    if algo == "xgb":
        f.lo = make_estimator(algo, params, monotone, "reg:quantileerror", 0.1).fit(X, y)
        f.hi = make_estimator(algo, params, monotone, "reg:quantileerror", 0.9).fit(X, y)
        f.q = (-cqr_adjust(params, X, y, monotone), 0.0)
    else:
        r = oof_residuals(algo, params, X, y, monotone)
        f.q = (float(np.quantile(r, 0.1)), float(np.quantile(r, 0.9)))
    return f


@dataclass
class SoftSensorModel:
    """可发布的模型对象：输入列 → 估计值与 P10 / P90。"""
    algo: str
    mode: str
    columns: list[str]
    regimes: list[str]  # 纳入的工况（统一模型中作为独热输入）
    models: dict[str, Fitted] = field(default_factory=dict)  # "all" 或 工况 id
    params: dict = field(default_factory=dict)
    monotone: dict = field(default_factory=dict)  # 列 → +1 / −1

    def design(self, X: pd.DataFrame, regime: np.ndarray) -> np.ndarray:
        A = X[self.columns].to_numpy(dtype="float64")
        if self.mode == "uni":
            oh = np.stack([(np.asarray(regime) == r).astype(float) for r in self.regimes], axis=1)
            A = np.hstack([A, oh])
        return A

    def predict(self, X: pd.DataFrame, regime: np.ndarray):
        """regime：每行的工况（上线时为实时判定结果）。不能输出的行返回 NaN。"""
        n = len(X)
        p, lo, hi = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
        A = self.design(X, regime)
        ok = np.isfinite(A).all(axis=1)
        regime = np.asarray(regime)
        if self.mode == "per":
            for r, m in self.models.items():
                sel = ok & (regime == r)
                if sel.any():
                    p[sel], lo[sel], hi[sel] = m.predict(A[sel])
        else:
            sel = ok & np.isin(regime, self.regimes if self.mode == "uni" else ["norm"])
            if sel.any():
                p[sel], lo[sel], hi[sel] = self.models["all"].predict(A[sel])
        return p, lo, hi


def train(algo: str, mode: str, X: pd.DataFrame, y: np.ndarray, regime: np.ndarray, columns: list[str],
          regimes: list[str], params_by_key: dict[str, dict], monotone: dict[str, int], interval: bool) -> SoftSensorModel:
    m = SoftSensorModel(algo, mode, columns, regimes, params=params_by_key, monotone=monotone)
    mono = [monotone.get(c, 0) for c in columns]
    if mode == "uni":
        mono = mono + [0] * len(regimes)
    A = m.design(X, regime)
    if mode == "per":
        for r in regimes:
            sel = regime == r
            if sel.sum() >= MIN_PER_REGIME:
                m.models[r] = fit_one(algo, params_by_key.get(r, {}), A[sel], y[sel], mono, interval)
    else:
        sel = np.ones(len(y), bool) if mode == "uni" else (regime == "norm")
        m.models["all"] = fit_one(algo, params_by_key.get("all", {}), A[sel], y[sel], mono, interval)
    return m


def train_keys(mode: str, regime: np.ndarray, regimes: list[str]) -> dict[str, np.ndarray]:
    """每个子模型用哪些训练行（用于自动调参）。"""
    if mode == "per":
        return {r: regime == r for r in regimes if (regime == r).sum() >= MIN_PER_REGIME}
    if mode == "main":
        return {"all": regime == "norm"}
    return {"all": np.ones(len(regime), bool)}


def baseline(t_eval: np.ndarray, t_all: np.ndarray, y_all: np.ndarray) -> np.ndarray:
    """基线：沿用上一个校核值（严格早于预测时刻）。"""
    order = np.argsort(t_all)
    ts, ys = np.asarray(t_all)[order], np.asarray(y_all)[order]
    pos = np.searchsorted(ts, np.asarray(t_eval), side="left") - 1
    out = np.full(len(t_eval), np.nan)
    ok = pos >= 0
    out[ok] = ys[pos[ok]]
    return out


def permutation_importance(model: SoftSensorModel, X: pd.DataFrame, y: np.ndarray, regime: np.ndarray,
                           repeats: int = 3, seed: int = 0) -> list[dict]:
    """在检验集上逐列打乱，看偏差（MAE）增加多少。"工况"作为一个整体打乱。"""
    rng = np.random.default_rng(seed)
    p, _, _ = model.predict(X, regime)
    ok = np.isfinite(p)
    if ok.sum() < 5:
        return []
    X, y, regime = X[ok].reset_index(drop=True), y[ok], regime[ok]
    base = float(np.mean(np.abs(model.predict(X, regime)[0] - y)))
    out = []
    for c in model.columns + (["__regime__"] if model.mode == "uni" else []):
        inc = []
        for _ in range(repeats):
            if c == "__regime__":
                rr = rng.permutation(regime)
                pp = model.predict(X, rr)[0]
            else:
                Xp = X.copy()
                Xp[c] = rng.permutation(Xp[c].to_numpy())
                pp = model.predict(Xp, regime)[0]
            f = np.isfinite(pp)
            inc.append(float(np.mean(np.abs(pp[f] - y[f]))) - base)
        out.append({"col": "工况" if c == "__regime__" else c, "inc": round(float(np.mean(inc)), 3)})
    out.sort(key=lambda d: -d["inc"])
    return out


def metrics(y, p, lo, hi, regime, base_p) -> dict:
    ok = np.isfinite(p)
    e = p[ok] - y[ok]
    by = {}
    for r in np.unique(regime):
        k = ok & (regime == r)
        by[str(r)] = {"mae": round(float(np.mean(np.abs(p[k] - y[k]))), 2) if k.any() else None,
                      "n": int(k.sum()), "n_all": int((regime == r).sum())}
    bo = ok & np.isfinite(base_p)
    mae = float(np.mean(np.abs(e))) if len(e) else math.nan
    base_mae = float(np.mean(np.abs(base_p[bo] - y[bo]))) if bo.any() else math.nan
    cov = float(np.mean((y[ok] >= lo[ok]) & (y[ok] <= hi[ok]))) if ok.any() else math.nan
    return {"mae": round(mae, 2), "max": round(float(np.max(np.abs(e))), 1) if len(e) else None,
            "rmse": round(float(np.sqrt(np.mean(e ** 2))), 2) if len(e) else None,
            "n": int(ok.sum()), "n_all": int(len(y)), "by_regime": by,
            "base_mae": round(base_mae, 2), "improve": round((1 - mae / base_mae) * 100, 1) if base_mae else None,
            "coverage": round(cov * 100, 1)}
