from __future__ import annotations

"""DTC Forecast PJT 1: relative-volume anchor trend forecast.

The implementation follows the project specification literally:
- adjusted daily close is used for the model level series when available;
- the latest 252 trading sessions are split into N equal buckets (default 6);
- one anchor per bucket is selected by relative volume = Volume / inclusive SMA20(Volume);
- a recency-decayed WLS line is fit to log price vs tau, where today is tau=0;
- only the fitted slope is used for the forward projection; the projection starts at
  today's actual adjusted close;
- forecast score is signed predicted return multiplied by confidence.

No market data is fetched in this module. All functions are pure with respect to the
input DataFrame.
"""

from math import exp, log, sqrt
from typing import Any

import numpy as np
import pandas as pd

REQUIRED_FORECAST_HISTORY = 272
MODEL_WINDOW = 252
RV_SMA_WINDOW = 20
SIGMA_WINDOW = 60
DEFAULT_HALF_LIFE = 84
DEFAULT_BUCKETS = 6
DEFAULT_HORIZON = 20
PRED_RETURN_CLIP = 0.20
WEIGHT_RATIO_CAP = 4.0
MIN_VALID_PER_BUCKET = 5


def _false(reason: str, *, half_life: float | int = DEFAULT_HALF_LIFE,
           n_buckets: int = DEFAULT_BUCKETS, horizon: int = DEFAULT_HORIZON) -> dict[str, Any]:
    return {
        "forecastable": False,
        "reason": reason,
        "half_life": None if np.isinf(float(half_life)) else float(half_life),
        "n_buckets": int(n_buckets),
        "horizon": int(horizon),
        "slope_pct_per_day": None,
        "t_stat": None,
        "r_pred_20d": None if horizon != 20 else None,
        "r_pred": None,
        "conf_t": None,
        "conf_z": None,
        "confidence": 0.0,
        "score": 0.0,
        "anchors": [],
        "projection": [],
    }


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out.index = pd.DatetimeIndex(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep="last")].sort_index()

    if "Close" not in out.columns or "Volume" not in out.columns:
        return pd.DataFrame()
    close = pd.to_numeric(out["Close"], errors="coerce")
    if "Adj Close" in out.columns:
        adj = pd.to_numeric(out["Adj Close"], errors="coerce")
        # Use adjusted close wherever valid. If the active intraday row has no
        # Adj Close yet, fall back only for that row to raw Close.
        model_close = adj.where(adj.notna() & (adj > 0), close)
    else:
        model_close = close
    volume = pd.to_numeric(out["Volume"], errors="coerce")

    out = pd.DataFrame({"Price": model_close, "Volume": volume}, index=out.index)
    return out


def _bucket_bounds(n_buckets: int) -> list[tuple[int, int]]:
    if n_buckets <= 0 or n_buckets > MODEL_WINDOW:
        raise ValueError("n_buckets must be between 1 and 252")
    # Default N=6 is exactly 42 sessions per bucket as specified. Sweep values
    # such as N=8 cannot divide 252 exactly, so the deterministic remainder is
    # assigned one session at a time to the earliest buckets (32/31 sessions).
    base, remainder = divmod(MODEL_WINDOW, n_buckets)
    bounds = []
    start = 0
    for i in range(n_buckets):
        width = base + (1 if i < remainder else 0)
        bounds.append((start, start + width))
        start += width
    return bounds


def _select_anchors(prepared: pd.DataFrame, n_buckets: int) -> tuple[list[dict[str, Any]], pd.Series] | tuple[None, None]:
    if len(prepared) < REQUIRED_FORECAST_HISTORY:
        return None, None

    # Inclusive 20-session SMA. min_periods is intentionally strict: the first
    # model-window observation must have 20 real observations available.
    sma20 = prepared["Volume"].rolling(RV_SMA_WINDOW, min_periods=RV_SMA_WINDOW).mean()
    rv = prepared["Volume"] / sma20.replace(0.0, np.nan)

    model = prepared.iloc[-MODEL_WINDOW:].copy()
    model_rv = rv.iloc[-MODEL_WINDOW:].copy()
    taus = np.arange(-(MODEL_WINDOW - 1), 1, dtype=float)
    anchors: list[dict[str, Any]] = []

    for start, stop in _bucket_bounds(n_buckets):
        part = pd.DataFrame({
            "price": pd.to_numeric(model["Price"].iloc[start:stop], errors="coerce").to_numpy(dtype=float),
            "volume": pd.to_numeric(model["Volume"].iloc[start:stop], errors="coerce").to_numpy(dtype=float),
            "rv": pd.to_numeric(model_rv.iloc[start:stop], errors="coerce").to_numpy(dtype=float),
            "tau": taus[start:stop],
            "date": model.index[start:stop],
        })
        valid = (
            np.isfinite(part["price"].to_numpy())
            & (part["price"].to_numpy() > 0)
            & np.isfinite(part["volume"].to_numpy())
            & (part["volume"].to_numpy() > 0)
            & np.isfinite(part["rv"].to_numpy())
            & (part["rv"].to_numpy() >= 0)
            # Today (tau=0) must never become an anchor: conf_z is meant to be an
            # out-of-sample residual against today's close.
            & (part["tau"].to_numpy() != 0.0)
        )
        part = part.loc[valid]
        if len(part) < MIN_VALID_PER_BUCKET:
            return None, None

        # idxmax is deterministic and picks the earliest row on an exact tie.
        row = part.loc[part["rv"].idxmax()]
        anchors.append({
            "tau": float(row["tau"]),
            "date": pd.Timestamp(row["date"]).date().isoformat(),
            "close": float(row["price"]),
            "rel_volume": float(row["rv"]),
        })

    return anchors, rv


def _weights(anchors: list[dict[str, Any]], half_life: float | int) -> np.ndarray:
    rv = np.array([a["rel_volume"] for a in anchors], dtype=float)
    tau = np.array([a["tau"] for a in anchors], dtype=float)
    if np.isinf(float(half_life)):
        decay = np.ones_like(tau)
    else:
        if float(half_life) <= 0:
            raise ValueError("half_life must be > 0 or infinity")
        decay = np.exp(-np.log(2.0) * np.abs(tau) / float(half_life))
    raw = rv * decay
    if not np.all(np.isfinite(raw)) or np.max(raw) <= 0:
        raise ValueError("invalid anchor weights")
    floor = np.max(raw) / WEIGHT_RATIO_CAP
    capped = np.maximum(raw, floor)
    return capped / capped.sum()


def _fit_wls(anchors: list[dict[str, Any]], weights: np.ndarray) -> tuple[float, float, float, float]:
    tau = np.array([a["tau"] for a in anchors], dtype=float)
    y = np.log(np.array([a["close"] for a in anchors], dtype=float))
    X = np.column_stack([tau, np.ones_like(tau)])
    W = np.diag(weights)
    xtwx = X.T @ W @ X
    try:
        inv = np.linalg.inv(xtwx)
    except np.linalg.LinAlgError as exc:
        raise ValueError("singular weighted design") from exc
    c = inv @ (X.T @ W @ y)
    m, b = float(c[0]), float(c[1])
    residual = y - X @ c
    dof = len(anchors) - 2
    if dof <= 0:
        raise ValueError("not enough degrees of freedom")
    s2 = float((residual.T @ W @ residual) / dof)
    variance_m = max(0.0, float(s2 * inv[0, 0]))
    se_m = sqrt(variance_m)
    if se_m <= 1e-15:
        if abs(m) <= 1e-15:
            t_m = 0.0
        else:
            t_m = float(np.sign(m) * np.inf)
    else:
        t_m = m / se_m
    return m, b, se_m, t_m


def forecast(df: pd.DataFrame, half_life: int | float = DEFAULT_HALF_LIFE,
             n_buckets: int = DEFAULT_BUCKETS, horizon: int = DEFAULT_HORIZON) -> dict[str, Any]:
    """Forecast signed forward trend from relative-volume anchors.

    Parameters are deliberately exposed exactly as specified. ``half_life`` may
    be ``np.inf`` for the no-decay baseline.
    """
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    prepared = _prepare(df)
    if len(prepared) < REQUIRED_FORECAST_HISTORY:
        return _false("history_lt_272d", half_life=half_life, n_buckets=n_buckets, horizon=horizon)

    price = pd.to_numeric(prepared["Price"], errors="coerce")
    p0 = float(price.iloc[-1]) if np.isfinite(price.iloc[-1]) else np.nan
    if not np.isfinite(p0) or p0 <= 0:
        return _false("invalid_current_price", half_life=half_life, n_buckets=n_buckets, horizon=horizon)

    try:
        anchors, _ = _select_anchors(prepared, n_buckets)
        if anchors is None:
            return _false("bucket_has_lt_5_valid_days", half_life=half_life, n_buckets=n_buckets, horizon=horizon)
        weights = _weights(anchors, half_life)
        m, b, se_m, t_m = _fit_wls(anchors, weights)
    except (ValueError, FloatingPointError):
        return _false("wls_failed", half_life=half_life, n_buckets=n_buckets, horizon=horizon)

    # Forecast shape comes from m; level always starts at the observed P0.
    raw_pred = exp(float(np.clip(m * horizon, -50.0, 50.0))) - 1.0
    r_pred = float(np.clip(raw_pred, -PRED_RETURN_CLIP, PRED_RETURN_CLIP))

    conf_t = min(abs(t_m) / 2.0, 1.0) if np.isfinite(t_m) else 1.0
    recent = np.log(price / price.shift(1)).iloc[-SIGMA_WINDOW:].replace([np.inf, -np.inf], np.nan).dropna()
    sigma = float(recent.std(ddof=1)) if len(recent) >= 2 else np.nan
    delta_t = abs(float(anchors[-1]["tau"]))
    divergence = b - log(p0)
    if np.isfinite(sigma) and sigma > 1e-15:
        z = divergence / (sigma * sqrt(max(delta_t, 1.0)))
        conf_z = float(np.clip(1.0 - abs(z) / 2.0, 0.0, 1.0))
    else:
        z = 0.0 if abs(divergence) <= 1e-12 else float(np.sign(divergence) * np.inf)
        conf_z = 1.0 if abs(divergence) <= 1e-12 else 0.0

    confidence = float(conf_t * conf_z)
    score = float(r_pred * confidence)

    anchor_payload = []
    for anchor, weight in zip(anchors, weights):
        anchor_payload.append({
            "date": anchor["date"],
            "tau": int(anchor["tau"]),
            "close": round(anchor["close"], 6),
            "rel_volume": round(anchor["rel_volume"], 6),
            "weight": round(float(weight), 8),
        })

    projection = [
        {"offset": j, "price": round(float(p0 * exp(m * j)), 6)}
        for j in range(1, horizon + 1)
    ]


    result = {
        "forecastable": True,
        "reason": None,
        "slope_pct_per_day": round(m * 100.0, 6),
        "slope_log_per_day": round(m, 10),
        "intercept_log": round(b, 10),
        "fitted_price_tau0": round(float(exp(b)), 6),
        "current_price": round(p0, 6),
        "se_slope": None if not np.isfinite(se_m) else round(se_m, 10),
        "t_stat": None if not np.isfinite(t_m) else round(float(t_m), 6),
        "r_pred": round(r_pred, 8),
        "conf_t": round(float(conf_t), 6),
        "conf_z": round(float(conf_z), 6),
        "z_today_gap": None if not np.isfinite(z) else round(float(z), 6),
        "confidence": round(confidence, 6),
        "score": round(score, 8),
        "anchors": anchor_payload,
        "projection": projection,
        "half_life": None if np.isinf(float(half_life)) else float(half_life),
        "n_buckets": int(n_buckets),
        "horizon": int(horizon),
        "last_anchor_gap": int(delta_t),
        "today_is_anchor": bool(anchor_payload[-1]["tau"] == 0),
    }
    # Preserve the requested field name for the production 20D horizon.
    result["r_pred_20d"] = round(r_pred, 8) if horizon == 20 else None
    return result


def forecast_at(df: pd.DataFrame, asof, half_life: int | float = DEFAULT_HALF_LIFE,
                n_buckets: int = DEFAULT_BUCKETS, horizon: int = DEFAULT_HORIZON) -> dict[str, Any]:
    """Forecast using only rows at or before ``asof``; future rows are ignored."""
    if df is None or df.empty:
        return _false("empty_frame", half_life=half_life, n_buckets=n_buckets, horizon=horizon)
    index = pd.DatetimeIndex(df.index)
    stamp = pd.Timestamp(asof)
    if index.tz is not None and stamp.tzinfo is None:
        stamp = stamp.tz_localize(index.tz)
    sliced = df.loc[index <= stamp]
    return forecast(sliced, half_life=half_life, n_buckets=n_buckets, horizon=horizon)


__all__ = [
    "forecast",
    "forecast_at",
    "REQUIRED_FORECAST_HISTORY",
    "MODEL_WINDOW",
    "DEFAULT_HALF_LIFE",
    "DEFAULT_BUCKETS",
    "DEFAULT_HORIZON",
]
