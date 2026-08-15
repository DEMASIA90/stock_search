from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from universe import ALL, Stock

OUT = Path(__file__).resolve().parent / 'docs' / 'data' / 'market.json'
PERIOD = '1y'
CHART_POINTS = 140


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def lerp_score(x, x0, y0, x1, y1):
    if x1 == x0:
        return y1
    t = clamp((x - x0) / (x1 - x0), 0.0, 1.0)
    return y0 + (y1 - y0) * t


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c = pd.to_numeric(out['Close'], errors='coerce')
    v = pd.to_numeric(out.get('Volume'), errors='coerce')

    out['SMA20'] = c.rolling(20).mean()
    out['SMA60'] = c.rolling(60).mean()
    std20 = c.rolling(20).std(ddof=0)
    out['Upper'] = out['SMA20'] + 2.0 * std20
    out['Lower'] = out['SMA20'] - 2.0 * std20

    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out['RSI14'] = 100 - (100 / (1 + rs))

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    out['MACD'] = ema12 - ema26
    out['MACDSignal'] = out['MACD'].ewm(span=9, adjust=False).mean()
    out['MACDHist'] = out['MACD'] - out['MACDSignal']

    out['VolAvg20'] = v.rolling(20).mean()
    out['Ret1'] = c.pct_change(1) * 100
    out['Ret3'] = c.pct_change(3) * 100
    return out


def score_bollinger(gap_pct: float) -> float:
    # Lower 이하: 20점. Lower 위 8%에서는 20 -> 0 선형 감소.
    if gap_pct <= 0:
        return 20.0
    return clamp(20.0 * (1.0 - gap_pct / 8.0), 0.0, 20.0)


def score_rsi(rsi: float) -> float:
    if not np.isfinite(rsi):
        return 0.0
    if rsi <= 30:
        return 20.0
    if rsi <= 40:
        return lerp_score(rsi, 30, 20, 40, 13)
    if rsi <= 50:
        return lerp_score(rsi, 40, 13, 50, 5)
    if rsi <= 60:
        return lerp_score(rsi, 50, 5, 60, 0)
    return 0.0


def score_volume(ratio: float) -> float:
    if not np.isfinite(ratio):
        return 0.0
    if ratio <= 0.6:
        return 0.0
    if ratio <= 1.0:
        return lerp_score(ratio, 0.6, 0, 1.0, 8)
    if ratio <= 1.5:
        return lerp_score(ratio, 1.0, 8, 1.5, 15)
    if ratio <= 2.0:
        return lerp_score(ratio, 1.5, 15, 2.0, 20)
    return 20.0


def score_reversal(ret1: float, ret3: float) -> float:
    # 과매도 후보에서 '낙폭 둔화/양전환'을 보는 점수. 각각 10점.
    s1 = lerp_score(ret1, -3.0, 0.0, 3.0, 10.0) if np.isfinite(ret1) else 0.0
    s3 = lerp_score(ret3, -6.0, 0.0, 6.0, 10.0) if np.isfinite(ret3) else 0.0
    return clamp(s1 + s3, 0.0, 20.0)


def score_macd(macd: float, signal: float, hist: float, prev_hist: float, prev_macd: float, prev_signal: float) -> float:
    if not np.isfinite([macd, signal, hist, prev_hist, prev_macd, prev_signal]).all():
        return 0.0
    score = 0.0
    if hist > prev_hist:
        score += 8.0
    if macd > signal:
        score += 6.0
    if prev_macd <= prev_signal and macd > signal:
        score += 6.0
    elif hist > 0:
        score += 3.0
    return clamp(score, 0.0, 20.0)


def label_score(total: float) -> str:
    if total >= 85:
        return 'A'
    if total >= 70:
        return 'B'
    if total >= 55:
        return 'C'
    return 'D'


def clean_num(v, digits=4):
    try:
        f = float(v)
        return round(f, digits) if np.isfinite(f) else None
    except Exception:
        return None


def extract_one(stock: Stock, df: pd.DataFrame):
    df = df.dropna(subset=['Close']).copy()
    if len(df) < 70:
        return None
    ind = add_indicators(df)
    valid = ind.dropna(subset=['SMA20', 'SMA60', 'Upper', 'Lower', 'RSI14', 'MACDHist'])
    if len(valid) < 2:
        return None

    last = valid.iloc[-1]
    prev = valid.iloc[-2]
    close = float(last['Close'])
    lower = float(last['Lower'])
    upper = float(last['Upper'])
    sma20 = float(last['SMA20'])
    sma60 = float(last['SMA60'])
    rsi = float(last['RSI14'])
    gap_pct = (close - lower) / lower * 100 if lower else np.nan
    band_pos = (close - lower) / (upper - lower) * 100 if upper != lower else np.nan
    vol_avg = float(last['VolAvg20']) if pd.notna(last['VolAvg20']) else np.nan
    volume = float(last['Volume']) if pd.notna(last.get('Volume')) else np.nan
    vol_ratio = volume / vol_avg if vol_avg and np.isfinite(vol_avg) else np.nan
    ret1 = float(last['Ret1']) if pd.notna(last['Ret1']) else np.nan
    ret3 = float(last['Ret3']) if pd.notna(last['Ret3']) else np.nan

    s_bb = score_bollinger(gap_pct)
    s_rsi = score_rsi(rsi)
    s_vol = score_volume(vol_ratio)
    s_rev = score_reversal(ret1, ret3)
    s_macd = score_macd(
        float(last['MACD']), float(last['MACDSignal']), float(last['MACDHist']),
        float(prev['MACDHist']), float(prev['MACD']), float(prev['MACDSignal'])
    )
    total = round(s_bb + s_rsi + s_vol + s_rev + s_macd, 1)

    if gap_pct <= 0:
        proximity = 'LOWER_BREAK'
    elif gap_pct <= 2:
        proximity = 'VERY_NEAR'
    elif gap_pct <= 5:
        proximity = 'NEAR'
    else:
        proximity = 'FAR'

    hist = valid.tail(CHART_POINTS)
    chart = []
    for idx, row in hist.iterrows():
        chart.append({
            'date': pd.Timestamp(idx).date().isoformat(),
            'close': clean_num(row['Close']),
            'sma20': clean_num(row['SMA20']),
            'upper': clean_num(row['Upper']),
            'lower': clean_num(row['Lower']),
            'rsi': clean_num(row['RSI14'], 2),
        })

    return {
        'ticker': stock.ticker,
        'name': stock.name,
        'market': stock.market,
        'currency': stock.currency,
        'date': pd.Timestamp(last.name).date().isoformat(),
        'close': clean_num(close),
        'lower': clean_num(lower),
        'upper': clean_num(upper),
        'sma20': clean_num(sma20),
        'sma60': clean_num(sma60),
        'gap_pct': clean_num(gap_pct, 3),
        'band_position_pct': clean_num(band_pos, 2),
        'rsi14': clean_num(rsi, 2),
        'volume_ratio': clean_num(vol_ratio, 2),
        'ret1_pct': clean_num(ret1, 2),
        'ret3_pct': clean_num(ret3, 2),
        'macd_hist': clean_num(last['MACDHist'], 4),
        'macd_hist_prev': clean_num(prev['MACDHist'], 4),
        'proximity': proximity,
        'candidate': bool(np.isfinite(gap_pct) and gap_pct <= 8.0),
        'score': total,
        'grade': label_score(total),
        'scores': {
            'bollinger': round(s_bb, 1),
            'rsi': round(s_rsi, 1),
            'volume': round(s_vol, 1),
            'reversal': round(s_rev, 1),
            'macd': round(s_macd, 1),
        },
        'chart': chart,
    }


def download_batch(stocks: list[Stock]):
    tickers = [s.ticker for s in stocks]
    return yf.download(
        tickers=tickers,
        period=PERIOD,
        interval='1d',
        group_by='ticker',
        auto_adjust=False,
        progress=False,
        threads=True,
        timeout=30,
    )


def frame_for(raw: pd.DataFrame, ticker: str):
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        level1 = set(raw.columns.get_level_values(1))
        if ticker in level0:
            return raw[ticker].copy()
        if ticker in level1:
            return raw.xs(ticker, axis=1, level=1).copy()
        return pd.DataFrame()
    return raw.copy()


def main():
    items = []
    errors = []
    for market in ('US', 'KR'):
        stocks = [s for s in ALL if s.market == market]
        raw = download_batch(stocks)
        for stock in stocks:
            try:
                frame = frame_for(raw, stock.ticker)
                if frame.empty:
                    raise RuntimeError('empty price frame')
                result = extract_one(stock, frame)
                if result:
                    items.append(result)
                else:
                    errors.append({'ticker': stock.ticker, 'reason': 'insufficient data'})
            except Exception as e:
                errors.append({'ticker': stock.ticker, 'reason': str(e)[:160]})

    items.sort(key=lambda x: (-x['score'], abs(x['gap_pct']) if x['gap_pct'] is not None else 999))
    payload = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'method': {
            'name': 'Band Scout 5-Signal Composite',
            'score_max': 100,
            'components': [
                {'key': 'bollinger', 'label': '볼린저 하단 근접', 'max': 20},
                {'key': 'rsi', 'label': 'RSI 과매도', 'max': 20},
                {'key': 'volume', 'label': '거래량 증가', 'max': 20},
                {'key': 'reversal', 'label': '단기 반전', 'max': 20},
                {'key': 'macd', 'label': 'MACD 개선', 'max': 20},
            ],
        },
        'universe_count': len(ALL),
        'item_count': len(items),
        'items': items,
        'errors': errors,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'wrote {OUT} with {len(items)} items / {len(errors)} errors')


if __name__ == '__main__':
    main()
