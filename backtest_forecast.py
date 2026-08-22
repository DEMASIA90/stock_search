from __future__ import annotations

"""Walk-forward validation for DTC Forecast PJT 1.

The backtester uses the exact Forecast PJT 1 mathematics but precomputes adjusted
prices, relative volume, date lookup and log returns once per symbol. This avoids
rebuilding pandas rolling windows for ~700 stocks x ~60 dates x the parameter grid.
Every evaluation state is still point-in-time: only arrays at indices <= t enter
anchor selection, sigma, WLS and confidence.
"""

import argparse
import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

from trend_forecast import (
    MODEL_WINDOW,
    REQUIRED_FORECAST_HISTORY,
    WEIGHT_RATIO_CAP,
    PRED_RETURN_CLIP,
    MIN_VALID_PER_BUCKET,
    _bucket_bounds,
)

SAMPLE_STEP = 20
HALF_LIVES = (42, 63, 84, 105, 126, np.inf)
BUCKET_COUNTS = (4, 6, 8)
HORIZONS = (10, 20, 40)
MIN_BACKTEST_YEARS = 3.0
RECOMMENDED_BACKTEST_YEARS = 5.0


def _normalized(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.DatetimeIndex(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    return out[~out.index.duplicated(keep="last")].sort_index()


@dataclass
class FastForecastSeries:
    index: pd.DatetimeIndex
    price: np.ndarray
    volume: np.ndarray
    rv: np.ndarray
    logret: np.ndarray
    date_pos: dict[pd.Timestamp, int]
    anchor_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray] | None] = field(default_factory=dict)
    fit_cache: dict[tuple[int, int, str], tuple[float, float] | None] = field(default_factory=dict)

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> "FastForecastSeries":
        d = _normalized(df)
        close = pd.to_numeric(d.get("Close"), errors="coerce")
        if "Adj Close" in d.columns:
            adj = pd.to_numeric(d["Adj Close"], errors="coerce")
            p = adj.where(adj.notna() & (adj > 0), close)
        else:
            p = close
        v = pd.to_numeric(d.get("Volume"), errors="coerce")
        sma = v.rolling(20, min_periods=20).mean()
        rv = v / sma.replace(0.0, np.nan)
        logp = np.log(p.where(p > 0))
        lr = logp.diff()
        idx = pd.DatetimeIndex(d.index)
        return cls(
            index=idx,
            price=p.to_numpy(dtype=float),
            volume=v.to_numpy(dtype=float),
            rv=rv.to_numpy(dtype=float),
            logret=lr.to_numpy(dtype=float),
            date_pos={pd.Timestamp(x): i for i, x in enumerate(idx)},
        )

    def anchors(self, pos: int, n_buckets: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        key = (pos, n_buckets)
        if key in self.anchor_cache:
            return self.anchor_cache[key]
        if pos + 1 < REQUIRED_FORECAST_HISTORY:
            self.anchor_cache[key] = None
            return None
        start = pos - (MODEL_WINDOW - 1)
        taus, prices, relvols = [], [], []
        for a, b in _bucket_bounds(n_buckets):
            lo, hi = start + a, start + b
            pp = self.price[lo:hi]
            vv = self.volume[lo:hi]
            rr = self.rv[lo:hi]
            valid = np.isfinite(pp) & (pp > 0) & np.isfinite(vv) & (vv > 0) & np.isfinite(rr) & (rr >= 0)
            # Mirror trend_forecast: exclude the evaluation day itself.
            valid &= (np.arange(lo, hi) != pos)
            if valid.sum() < MIN_VALID_PER_BUCKET:
                self.anchor_cache[key] = None
                return None
            local = np.flatnonzero(valid)
            # np.argmax returns the first maximum: deterministic tie rule matching pandas idxmax.
            chosen_local = local[int(np.argmax(rr[local]))]
            absolute = lo + chosen_local
            taus.append(float(absolute - pos))
            prices.append(float(self.price[absolute]))
            relvols.append(float(self.rv[absolute]))
        ans = (np.asarray(taus), np.asarray(prices), np.asarray(relvols))
        self.anchor_cache[key] = ans
        return ans

    def fit_state(self, pos: int, n_buckets: int, half_life: float) -> tuple[float, float] | None:
        hkey = "inf" if np.isinf(half_life) else f"{float(half_life):g}"
        key = (pos, n_buckets, hkey)
        if key in self.fit_cache:
            return self.fit_cache[key]
        anc = self.anchors(pos, n_buckets)
        if anc is None:
            self.fit_cache[key] = None
            return None
        tau, price, relvol = anc
        if np.isinf(half_life):
            decay = np.ones_like(tau)
        else:
            decay = np.exp(-np.log(2.0) * np.abs(tau) / float(half_life))
        raw = relvol * decay
        if not np.all(np.isfinite(raw)) or raw.max() <= 0:
            self.fit_cache[key] = None
            return None
        raw = np.maximum(raw, raw.max() / WEIGHT_RATIO_CAP)
        w = raw / raw.sum()
        X = np.column_stack([tau, np.ones_like(tau)])
        W = np.diag(w)
        xtwx = X.T @ W @ X
        try:
            inv = np.linalg.inv(xtwx)
        except np.linalg.LinAlgError:
            self.fit_cache[key] = None
            return None
        y = np.log(price)
        c = inv @ (X.T @ W @ y)
        m, b = float(c[0]), float(c[1])
        r = y - X @ c
        dof = len(tau) - 2
        if dof <= 0:
            self.fit_cache[key] = None
            return None
        s2 = float(r.T @ W @ r) / dof
        se = math.sqrt(max(0.0, s2 * float(inv[0, 0])))
        if se <= 1e-15:
            t_m = 0.0 if abs(m) <= 1e-15 else float(np.sign(m) * np.inf)
        else:
            t_m = m / se
        conf_t = min(abs(t_m) / 2.0, 1.0) if np.isfinite(t_m) else 1.0
        p0 = self.price[pos]
        if not np.isfinite(p0) or p0 <= 0:
            self.fit_cache[key] = None
            return None
        lr = self.logret[max(0, pos - 59):pos + 1]
        lr = lr[np.isfinite(lr)]
        sigma = float(np.std(lr, ddof=1)) if len(lr) >= 2 else np.nan
        delta_t = abs(float(tau[-1]))
        gap = b - math.log(float(p0))
        if np.isfinite(sigma) and sigma > 1e-15:
            z = gap / (sigma * math.sqrt(max(delta_t, 1.0)))
            conf_z = float(np.clip(1.0 - abs(z) / 2.0, 0.0, 1.0))
        else:
            conf_z = 1.0 if abs(gap) <= 1e-12 else 0.0
        state = (m, float(conf_t * conf_z))
        self.fit_cache[key] = state
        return state

    def score(self, pos: int, n_buckets: int, half_life: float, horizon: int) -> float:
        state = self.fit_state(pos, n_buckets, half_life)
        if state is None:
            return np.nan
        m, confidence = state
        pred = math.exp(float(np.clip(m * horizon, -50.0, 50.0))) - 1.0
        pred = float(np.clip(pred, -PRED_RETURN_CLIP, PRED_RETURN_CLIP))
        return pred * confidence


def _engines(frames: dict[str, pd.DataFrame]) -> dict[str, FastForecastSeries]:
    return {k: FastForecastSeries.from_frame(v) for k, v in frames.items() if v is not None and not v.empty}


def _reference_dates(engines: dict[str, FastForecastSeries], markets: dict[str, str],
                     horizon: int) -> dict[str, list[pd.Timestamp]]:
    """Sampling dates per market.

    KR and US have different holiday calendars. Driving every market off one
    reference ticker silently drops the other market's entire cross-section on
    dates it does not trade, which also breaks that date's market adjustment.
    """
    grouped: dict[str, list[FastForecastSeries]] = {}
    for ticker, e in engines.items():
        grouped.setdefault(markets.get(ticker, "UNKNOWN"), []).append(e)
    out: dict[str, list[pd.Timestamp]] = {}
    for market, series in grouped.items():
        ref = max(series, key=lambda e: len(e.index))
        if len(ref.index) < REQUIRED_FORECAST_HISTORY + horizon:
            continue
        out[market] = [pd.Timestamp(ref.index[i])
                       for i in range(REQUIRED_FORECAST_HISTORY - 1, len(ref.index) - horizon, SAMPLE_STEP)]
    return out


def _baseline_12_1(e: FastForecastSeries, pos: int) -> float:
    if pos < 252:
        return np.nan
    a, b = e.price[pos - 252], e.price[pos - 21]
    return b / a - 1.0 if np.isfinite(a) and a > 0 and np.isfinite(b) and b > 0 else np.nan


def _baseline_3m(e: FastForecastSeries, pos: int) -> float:
    if pos < 63:
        return np.nan
    a, b = e.price[pos - 63], e.price[pos]
    return b / a - 1.0 if np.isfinite(a) and a > 0 and np.isfinite(b) and b > 0 else np.nan


def _event_rows(engines: dict[str, FastForecastSeries], markets: dict[str, str], *,
                half_life: float, n_buckets: int, horizon: int, include_baselines: bool) -> pd.DataFrame:
    rows = []
    by_market: dict[str, list[str]] = {}
    for ticker in engines:
        by_market.setdefault(markets.get(ticker, "UNKNOWN"), []).append(ticker)
    for market, dates in _reference_dates(engines, markets, horizon).items():
      for date in dates:
        local = []
        for ticker in by_market.get(market, []):
            e = engines[ticker]
            pos = e.date_pos.get(date)
            if pos is None or pos < REQUIRED_FORECAST_HISTORY - 1 or pos + horizon >= len(e.price):
                continue
            p0, p1 = e.price[pos], e.price[pos + horizon]
            if not (np.isfinite(p0) and p0 > 0 and np.isfinite(p1) and p1 > 0):
                continue
            score = e.score(pos, n_buckets, half_life, horizon)
            if not np.isfinite(score):
                continue
            row = {"date": date, "ticker": ticker, "market": market, "score": score, "fwd": p1 / p0 - 1.0}
            if include_baselines:
                row["mom_12_1"] = _baseline_12_1(e, pos)
                row["ret_3m"] = _baseline_3m(e, pos)
                row["no_decay"] = e.score(pos, n_buckets, np.inf, horizon)
            local.append(row)
        if len(local) >= 4:
            chunk = pd.DataFrame(local)
            chunk["fwd_adj"] = chunk["fwd"] - chunk["fwd"].mean()
            rows.extend(chunk.to_dict("records"))
    out = pd.DataFrame(rows)
    if not out.empty:
        out["date"] = pd.to_datetime(out["date"])
    return out


def _safe_t(values: Iterable[float]) -> float:
    x = np.asarray([v for v in values if np.isfinite(v)], dtype=float)
    if len(x) < 2:
        return np.nan
    sd = x.std(ddof=1)
    if sd <= 1e-15:
        return np.inf if abs(x.mean()) > 1e-15 else 0.0
    return float(x.mean() / (sd / math.sqrt(len(x))))


def _metric_block(events: pd.DataFrame, score_col: str) -> dict:
    d = events[["date", "ticker", "market", "fwd_adj", score_col]].dropna().rename(columns={score_col: "model_score"}).copy()
    if d.empty:
        return {"n_events": 0, "n_dates": 0}
    # confidence == 0 (or an exactly flat baseline) is an explicit "no opinion".
    # np.sign(0) never matches +/-1, so leaving these rows in would score every
    # abstention as a miss and bias the edge downward for this model only.
    n_all = len(d)
    d = d[d["model_score"] != 0].copy()
    coverage = float(len(d) / n_all) if n_all else np.nan
    if d.empty:
        return {"n_events": 0, "n_dates": 0, "coverage": coverage}
    d["hit"] = np.sign(d["model_score"]) == np.sign(d["fwd_adj"])
    date_stats, ics, spreads, decparts = [], [], [], []
    for date, g in d.groupby("date", sort=True):
        if len(g) < 4:
            continue
        hit = float(g["hit"].mean())
        base = float((g["fwd_adj"] > 0).mean())
        ic = float(g["model_score"].corr(g["fwd_adj"], method="spearman")) if g["model_score"].nunique() > 1 else np.nan
        ranks = g["model_score"].rank(method="first", pct=True)
        decile = np.clip(np.ceil(ranks * 10), 1, 10).astype(int)
        gg = g.assign(decile=decile)
        top, bottom = gg.loc[gg.decile == 10, "fwd_adj"], gg.loc[gg.decile == 1, "fwd_adj"]
        date_stats.append({"date": date, "hit_rate": hit, "base_rate": base, "edge": hit - base})
        if np.isfinite(ic): ics.append(ic)
        if len(top) and len(bottom): spreads.append(float(top.mean() - bottom.mean()))
        decparts.append(gg[["hit", "fwd_adj", "decile"]])
    ds = pd.DataFrame(date_stats)
    iv = np.asarray(ics, dtype=float); sv = np.asarray(spreads, dtype=float)
    deciles = []
    if decparts:
        dec = pd.concat(decparts).groupby("decile").agg(hit_rate=("hit", "mean"), avg_fwd_adj=("fwd_adj", "mean"), n=("hit", "size")).reset_index()
        deciles = dec.to_dict("records")
    return {
        "n_events": int(len(d)), "n_dates": int(ds.date.nunique()) if not ds.empty else 0,
        "coverage": coverage,
        "hit_rate": float(d.hit.mean()), "base_rate": float(ds.base_rate.mean()) if not ds.empty else np.nan,
        "edge": float(ds.edge.mean()) if not ds.empty else np.nan, "edge_t": _safe_t(ds.edge) if not ds.empty else np.nan,
        "mean_ic": float(iv.mean()) if len(iv) else np.nan, "std_ic": float(iv.std(ddof=1)) if len(iv)>1 else np.nan,
        "icir": float(iv.mean()/iv.std(ddof=1)) if len(iv)>1 and iv.std(ddof=1)>0 else np.nan, "ic_t": _safe_t(iv),
        "decile_spread": float(sv.mean()) if len(sv) else np.nan, "spread_t": _safe_t(sv), "deciles": deciles,
    }


def _deterministic_shuffle(events: pd.DataFrame) -> pd.Series:
    out = pd.Series(index=events.index, dtype=float)
    for date, ids in events.groupby("date").groups.items():
        seed = int(hashlib.sha256(str(pd.Timestamp(date).date()).encode()).hexdigest()[:16], 16)
        rng = np.random.default_rng(seed)
        values = events.loc[list(ids), "score"].to_numpy(copy=True); rng.shuffle(values)
        out.loc[list(ids)] = values
    return out


def evaluate(frames: dict[str, pd.DataFrame], markets: dict[str, str], *, half_life: float=84, n_buckets: int=6, horizon: int=20) -> dict:
    engines = _engines(frames)
    events = _event_rows(engines, markets, half_life=half_life, n_buckets=n_buckets, horizon=horizon, include_baselines=True)
    if events.empty:
        return {"available": False, "reason": "no_events"}
    events["random"] = _deterministic_shuffle(events)
    cols = {"forecast_pjt_1":"score", "momentum_12_1":"mom_12_1", "return_3m":"ret_3m", "no_decay":"no_decay", "random":"random"}
    metrics = {name:_metric_block(events,col) for name,col in cols.items()}
    years = (events.date.max()-events.date.min()).days/365.25
    main, mom = metrics["forecast_pjt_1"], metrics["momentum_12_1"]
    beats = all(np.isfinite(x) for x in [main.get("edge",np.nan),mom.get("edge",np.nan),main.get("mean_ic",np.nan),mom.get("mean_ic",np.nan)]) and main["edge"]>mom["edge"] and main["mean_ic"]>mom["mean_ic"]
    return {"available":True, "sample_years":years, "sample_warning":"표본 부족 — 단일 국면만 포함" if years<MIN_BACKTEST_YEARS else None,
            "recommended_history_met":years>=RECOMMENDED_BACKTEST_YEARS, "parameters":{"half_life":half_life,"n_buckets":n_buckets,"horizon":horizon,"sampling_step":SAMPLE_STEP},
            "models":metrics, "verdict":"채택 가치 있음 — 본 모델이 동일 표본에서 12-1 모멘텀의 엣지와 IC를 모두 상회했다." if beats else "채택 가치 부족/보류 — 본 모델이 동일 표본에서 12-1 모멘텀의 엣지와 IC를 모두 이기지 못했다."}


def parameter_sweep(frames: dict[str, pd.DataFrame], markets: dict[str, str]) -> dict:
    engines = _engines(frames)
    rows=[]
    for n in BUCKET_COUNTS:
        for H in HALF_LIVES:
            for h in HORIZONS:
                ev=_event_rows(engines,markets,half_life=H,n_buckets=n,horizon=h,include_baselines=False)
                if ev.empty: continue
                dates=sorted(ev.date.unique()); split=max(1,len(dates)//2); train=set(dates[:split]); test=set(dates[split:])
                a=_metric_block(ev[ev.date.isin(train)],"score"); b=_metric_block(ev[ev.date.isin(test)],"score")
                rows.append({"H":"inf" if np.isinf(H) else int(H),"N":n,"h":h,"train_edge":a.get("edge"),"train_ic":a.get("mean_ic"),"test_edge":b.get("edge"),"test_ic":b.get("mean_ic")})
    valid=[r for r in rows if np.isfinite(r.get("train_ic",np.nan))]; best=max(valid,key=lambda r:r["train_ic"]) if valid else None
    stable=None if best is None else bool(np.isfinite(best.get("test_ic",np.nan)) and best["test_ic"]>0 and np.isfinite(best.get("test_edge",np.nan)) and best["test_edge"]>0)
    return {"rows":rows,"selection_rule":"first-half highest mean cross-sectional IC","best_train":best,"holds_in_second_half":stable}


def _pct(v,d=2): return "—" if not np.isfinite(v) else f"{100*v:.{d}f}%"
def _num(v,d=3): return "—" if not np.isfinite(v) else f"{v:.{d}f}"


def render_markdown(result: dict, sweep: dict|None=None) -> str:
    if not result.get("available"):
        return "# Forecast PJT 1 백테스트 리포트\n\n**판정 보류 — 유효 백테스트 표본이 없습니다.**\n"
    L=["# Forecast PJT 1 백테스트 리포트","",f"- 표본 기간: **{result['sample_years']:.2f}년**"]
    if result.get("sample_warning"): L.append(f"- ⚠️ **{result['sample_warning']}**")
    L += ["","## 헤드라인 및 베이스라인 비교","","| 모델 | 커버리지 | 적중률 | 기준선 | 엣지 | Edge t | 평균 IC | ICIR | IC t | 상·하위10% 스프레드 |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    labels={"forecast_pjt_1":"본 모델","momentum_12_1":"12-1 모멘텀","return_3m":"3개월 수익률","no_decay":"감쇠 없음","random":"랜덤"}
    for k in labels:
        m=result["models"].get(k,{})
        L.append(f"| {labels[k]} | {_pct(m.get('coverage',np.nan))} | {_pct(m.get('hit_rate',np.nan))} | {_pct(m.get('base_rate',np.nan))} | {_pct(m.get('edge',np.nan))} | {_num(m.get('edge_t',np.nan))} | {_num(m.get('mean_ic',np.nan))} | {_num(m.get('icir',np.nan))} | {_num(m.get('ic_t',np.nan))} | {_pct(m.get('decile_spread',np.nan))} |")
    L += ["","## 본 모델 10분위 단조성","","| 분위 | 적중률 | 평균 시장조정 수익률 | 표본 |","|---:|---:|---:|---:|"]
    for r in result["models"]["forecast_pjt_1"].get("deciles",[]): L.append(f"| {int(r['decile'])} | {_pct(r['hit_rate'])} | {_pct(r['avg_fwd_adj'])} | {int(r['n'])} |")
    if sweep:
        L += ["","## 파라미터 스윕","","전반부 최고 평균 IC 조합을 선택하고 후반부 유지 여부를 확인한다.","","| H | N | h | 전반 Edge | 전반 IC | 후반 Edge | 후반 IC |","|---:|---:|---:|---:|---:|---:|---:|"]
        for r in sweep.get("rows",[]): L.append(f"| {r['H']} | {r['N']} | {r['h']} | {_pct(r.get('train_edge',np.nan))} | {_num(r.get('train_ic',np.nan))} | {_pct(r.get('test_edge',np.nan))} | {_num(r.get('test_ic',np.nan))} |")
        if sweep.get("best_train"): 
            b=sweep["best_train"]; L += ["",f"- 전반부 최적: **H={b['H']}, N={b['N']}, h={b['h']}**",f"- 후반부 유지 여부: **{'유지' if sweep.get('holds_in_second_half') else '미유지 — 과적합 가능성'}**"]
    L += ["","## 최종 판정","",f"**{result['verdict']}**","","> R²는 판정 기준으로 사용하지 않았다. forward return은 각 시점의 KR/US 시장별 횡단면 평균을 차감했다.",
        "",
        "### 표본의 알려진 한계",
        "",
        "- **생존편향**: 유니버스를 현재 `summary.json` 구성종목에서 가져오므로 기간 중 상장폐지·탈락 종목이 빠져 있다. 모멘텀 계열 신호는 이 편향으로 과대평가되는 경향이 있으므로 절대 수치보다 모델 간 상대비교로 해석해야 한다.",
        "- **사후 조정가**: `Adj Close` 는 이후 발생한 배당·분할을 소급 반영한 시계열이므로 엄밀히는 미세한 룩어헤드를 포함한다. 본 모델과 모든 베이스라인·실현수익률에 동일하게 적용되어 상대비교는 공정하다.",
        "- **커버리지**: confidence=0(의견 없음)인 관측은 적중률 집계에서 제외했다. 커버리지 열이 실제 판정에 사용된 표본 비율이다.",]
    return "\n".join(L)+"\n"


def load_csv_directory(path: Path):
    frames={}; markets={}
    for csv in sorted(path.glob("*.csv")):
        market,ticker=(csv.stem.split("__",1) if "__" in csv.stem else ("UNKNOWN",csv.stem))
        frames[ticker]=pd.read_csv(csv,index_col=0,parse_dates=True); markets[ticker]=market
    return frames,markets



def _frame_for_download(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        for level in range(raw.columns.nlevels):
            if ticker in set(map(str, raw.columns.get_level_values(level))):
                try:
                    return raw.xs(ticker, axis=1, level=level).copy()
                except Exception:
                    pass
        return pd.DataFrame()
    return raw.copy()


def download_from_summaries(data_dir: Path, years: int = 7, batch_size: int = 48):
    """Download long history only for the current scanner-eligible universe.

    This keeps the expensive research backtest separate from normal QUICK/FULL
    site refreshes. The current summaries provide the exact live scanner universe;
    category labels are collapsed to KR/US only for market adjustment.
    """
    if yf is None:
        raise RuntimeError("yfinance is required for --download-from-summary")
    specs = [("kr","KR"),("kr-etf","KR"),("us","US"),("us-etf","US")]
    tickers=[]; markets={}
    for folder, market in specs:
        path=data_dir/folder/"summary.json"
        if not path.is_file():
            continue
        import json
        payload=json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("items") or []:
            ticker=str(item.get("ticker") or "").strip()
            if ticker and ticker not in markets:
                tickers.append(ticker); markets[ticker]=market
    if not tickers:
        raise RuntimeError("no summary tickers found")
    end=pd.Timestamp.utcnow().normalize()+pd.Timedelta(days=1)
    start=end-pd.Timedelta(days=int(years*366+40))
    frames={}
    for i in range(0,len(tickers),batch_size):
        batch=tickers[i:i+batch_size]
        raw=yf.download(batch,start=start.date().isoformat(),end=end.date().isoformat(),interval="1d",group_by="ticker",auto_adjust=False,actions=False,progress=False,threads=min(8,len(batch)),timeout=45,multi_level_index=True)
        for ticker in batch:
            f=_frame_for_download(raw,ticker)
            if not f.empty:
                frames[ticker]=f
        print(f"download {min(i+batch_size,len(tickers))}/{len(tickers)} | frames={len(frames)}")
    return frames,markets


def main():
    ap=argparse.ArgumentParser()
    source=ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv-dir",type=Path)
    source.add_argument("--download-from-summary",type=Path,metavar="DOCS_DATA_DIR")
    ap.add_argument("--years",type=int,default=7)
    ap.add_argument("--report",type=Path,default=Path("forecast_backtest_report.md"))
    ap.add_argument("--sweep",action="store_true")
    args=ap.parse_args()
    if args.csv_dir:
        frames,markets=load_csv_directory(args.csv_dir)
    else:
        frames,markets=download_from_summaries(args.download_from_summary,args.years)
    result=evaluate(frames,markets)
    sweep=parameter_sweep(frames,markets) if args.sweep else None
    args.report.write_text(render_markdown(result,sweep),encoding="utf-8")
    print(args.report)

if __name__=="__main__": main()
