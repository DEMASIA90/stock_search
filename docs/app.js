const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];

const CATEGORY = {
  KR: { short: '국장', dir: 'kr' },
  KR_ETF: { short: '국장 ETF', dir: 'kr-etf' },
  US: { short: '미장', dir: 'us' },
  US_ETF: { short: '미장 ETF', dir: 'us-etf' },
};

const IS_ANDROID_APP = location.hostname === 'localhost' || location.protocol === 'capacitor:';
const CONFIGURED_DATA_ORIGIN = String(window.DTC_DATA_ORIGIN || '').trim().replace(/\/$/, '');
const DATA_BASE = (location.hostname.endsWith('github.io') || IS_ANDROID_APP)
  ? (CONFIGURED_DATA_ORIGIN || '.')
  : '.';
const NEWS_PROXY_URL = String(window.BADAK_NEWS_PROXY_URL || '').trim();
const NEWS_CACHE_MS = 5 * 60 * 1000;
const DEFAULT_TOP_N = 20;
const DEFAULT_EQUITY_SIZE_MIN = 100_000_000_000_000;
const DEFAULT_ETF_SIZE_MIN = 0;
const CAP_FILTER_PRESETS = {
  equity: [
    [10_000_000_000_000, '10조 이상'],
    [50_000_000_000_000, '50조 이상'],
    [100_000_000_000_000, '100조 이상'],
    [500_000_000_000_000, '500조 이상'],
    [1_000_000_000_000_000, '1000조 이상'],
  ],
  etf: [
    [0, '전체'],
    [100_000_000_000, '0.1조 이상'],
    [500_000_000_000, '0.5조 이상'],
    [1_000_000_000_000, '1조 이상'],
    [5_000_000_000_000, '5조 이상'],
  ],
};
const CHART_TRADING_DAYS = 63;
const QUIZ_WINDOW_DAYS = 90;
const QUIZ_HIDDEN_DAYS = 30;
const QUIZ_MIN_MARKET_SIZE = 100_000_000_000_000;
const QUIZ_SHARDS = ['kr', 'kr-etf', 'us', 'us-etf'];

const state = {
  mode: 'forecast',
  category: 'KR',
  data: { KR:null, KR_ETF:null, US:null, US_ETF:null },
  query: '',
  marketSizeMin: { equity: DEFAULT_EQUITY_SIZE_MIN, etf: DEFAULT_ETF_SIZE_MIN },
  filtered: [],
  detailCache: new Map(),
  newsCache: new Map(),
  cardObserver: null,
  quiz: {
    pool: null,
    detailCache: new Map(),
    question: null,
    answered: false,
    loading: false,
    number: 0,
  },
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c]));
}

function dataUrl(path, force=false) {
  const clean = String(path).replace(/^\.\//, '').replace(/^\//, '');
  const base = DATA_BASE === '.' ? '.' : DATA_BASE.replace(/\/$/, '');
  const url = `${base}/${clean}`;
  return force ? `${url}${url.includes('?') ? '&' : '?'}ts=${Date.now()}` : url;
}

function currentData() { return state.data[state.category]; }

function switchAnalysisMode(mode) {
  const next = ['forecast', 'quiz'].includes(mode) ? mode : 'forecast';
  state.mode = next;
  const forecastView = $('#forecastModeView');
  const quizView = $('#quizModeView');
  if (forecastView) forecastView.hidden = next !== 'forecast';
  if (quizView) quizView.hidden = next !== 'quiz';
  const select = $('#analysisMode');
  if (select && select.value !== next) select.value = next;
  document.body.dataset.analysisMode = next;
  if (next === 'forecast') {
    if (currentData()) { renderMeta(); renderList(); }
    requestAnimationFrame(() => activateLazyCards());
  } else if (state.cardObserver) {
    state.cardObserver.disconnect();
  }
}

function isEtfCategory(category=state.category) {
  return category === 'KR_ETF' || category === 'US_ETF';
}

function sizeFilterMode(category=state.category) {
  return isEtfCategory(category) ? 'etf' : 'equity';
}

function currentSizeMin() {
  const mode = sizeFilterMode();
  const value = Number(state.marketSizeMin[mode]);
  if (Number.isFinite(value) && value >= 0) return value;
  return mode === 'etf' ? DEFAULT_ETF_SIZE_MIN : DEFAULT_EQUITY_SIZE_MIN;
}

function renderSizeFilters() {
  const mode = sizeFilterMode();
  const presets = CAP_FILTER_PRESETS[mode];
  const activeValue = currentSizeMin();
  const buttons = $$('.cap-filter');
  buttons.forEach((button, i) => {
    const [value, label] = presets[i];
    button.dataset.cap = String(value);
    button.textContent = label;
    button.classList.toggle('active', Number(value) === activeValue);
  });
  const nav = $('#capFilterTabs');
  if (nav) nav.setAttribute('aria-label', mode === 'etf' ? 'ETF 규모 필터' : '시가총액 필터');
}

// 'forecast' = Forecast PJT 1 score 순, 'setup' = 기존 셋업 점수(0~10) 순.
// 예측 모델이 백테스트로 검증되기 전까지는 'setup' 이 안전한 기본값이다.
const RANK_BY = 'forecast';

function setupValue(stock) {
  const n = Number(stock?.base_score ?? stock?.score);
  return Number.isFinite(n) ? n : -1e18;
}

function forecastValue(stock) {
  const f = stock?.forecast || {};
  if (!f.forecastable) return null;
  const n = Number(f.score ?? stock?.forecast_score);
  return Number.isFinite(n) ? n : null;
}

function scoreValue(stock) {
  if (RANK_BY === 'setup') return setupValue(stock);
  const n = forecastValue(stock);
  // Non-forecastable names sort below any real forecast but keep their setup
  // ordering among themselves instead of collapsing into one -1e18 bucket.
  return n === null ? -1e9 + setupValue(stock) : n;
}

function scoreText(stock) {
  const n = forecastValue(stock);
  if (n === null) {
    const s = setupValue(stock);
    return s > -1e17 ? `셋업 ${s.toFixed(1)}` : '—';
  }
  return `${n > 0 ? '+' : ''}${(n * 100).toFixed(2)}%`;
}

function heatClass(stock) {
  const n = scoreValue(stock);
  if (!Number.isFinite(n) || n < -1e10) return '';
  if (n >= 0.05) return 'heat-80';
  if (n >= 0.025) return 'heat-70';
  if (n >= 0.01) return 'heat-60';
  return '';
}

function money(v, currency) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  return currency === 'KRW'
    ? `₩${Math.round(Number(v)).toLocaleString('ko-KR')}`
    : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits:2, maximumFractionDigits:2 })}`;
}

function marketSize(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  if (n >= 1e12) return `${(n / 1e12).toFixed(n >= 100e12 ? 0 : 1)}조`;
  if (n >= 1e8) return `${(n / 1e8).toFixed(0)}억`;
  return Math.round(n).toLocaleString('ko-KR');
}

function changeText(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '<span class="flat">—</span>';
  if (n > 0) return `<span class="up">▲${Math.abs(n).toFixed(1)}%</span>`;
  if (n < 0) return `<span class="down">▼${Math.abs(n).toFixed(1)}%</span>`;
  return '<span class="flat">0.0%</span>';
}

function ratioPct(v, digits=1) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v) * 100;
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

async function ensureData(category, force=false) {
  if (state.data[category] && !force) return state.data[category];
  const response = await fetch(dataUrl(`data/${CATEGORY[category].dir}/summary.json`, force), {
    cache: force ? 'no-store' : 'default',
  });
  if (!response.ok) throw new Error(`${category} summary ${response.status}`);
  const data = await response.json();
  state.data[category] = data;
  return data;
}

async function ensureDetail(stock, force=false) {
  const key = `${stock.category}:${stock.ticker}`;
  if (state.detailCache.has(key) && !force) return state.detailCache.get(key);
  if (!stock.detail_path) throw new Error('detail_path missing');
  const url = stock.detail_path.startsWith('http')
    ? (force ? `${stock.detail_path}?ts=${Date.now()}` : stock.detail_path)
    : dataUrl(stock.detail_path, force);
  const response = await fetch(url, { cache: force ? 'no-store' : 'default' });
  if (!response.ok) throw new Error(`detail ${response.status}`);
  const detail = await response.json();
  state.detailCache.set(key, detail);
  return detail;
}

function filterItems() {
  const items = currentData()?.items || [];
  const q = state.query.trim().toLowerCase();
  const capMin = currentSizeMin();
  const capMatched = capMin <= 0 ? items : items.filter((s) => {
    const cap = Number(s.market_size_krw);
    return Number.isFinite(cap) && cap >= capMin;
  });
  const candidates = q
    ? capMatched.filter((s) => `${s.name} ${s.symbol} ${s.ticker} ${s.sector || ''}`.toLowerCase().includes(q))
    : capMatched;
  const sorted = [...candidates].sort((a, b) => {
    const diff = scoreValue(b) - scoreValue(a);
    if (Math.abs(diff) > 1e-12) return diff;
    const ar = Number(a.rank), br = Number(b.rank);
    if (Number.isFinite(ar) && Number.isFinite(br)) return ar - br;
    return String(a.ticker || '').localeCompare(String(b.ticker || ''));
  });
  state.filtered = q ? sorted : sorted.slice(0, DEFAULT_TOP_N);
  return { totalCapMatched: capMatched.length };
}

function marketSizeLabel(stock) {
  return stock?.market_size_basis === 'total_assets' ? '순자산' : '시총';
}

function forecastSignalLines(stock) {
  const f = stock?.forecast || {};
  if (!f.forecastable) {
    return `<div class="trade-signal-box"><div class="trade-signal-row"><span class="signal-title">Forecast</span><span>히스토리 부족 또는 앵커 추출 불가</span></div></div>`;
  }
  const pred = Number(f.r_pred_20d ?? f.r_pred);
  const conf = Number(f.confidence);
  const t = Number(f.t_stat);
  const slope = Number(f.slope_pct_per_day);
  const cls = pred > 0 ? 'signal-hot' : pred < 0 ? 'signal-cold' : '';
  return `<div class="trade-signal-box">
    <div class="trade-signal-row">
      <span class="signal-title breakout">20D 예측</span>
      <span><b class="${cls}">${Number.isFinite(pred) ? `${pred > 0 ? '+' : ''}${(pred*100).toFixed(2)}%` : '—'}</b></span><i>·</i>
      <span>Confidence <b>${Number.isFinite(conf) ? (conf*100).toFixed(1)+'%' : '—'}</b></span><i>·</i>
      <span>Score <b>${scoreText(stock)}</b></span>
    </div>
    <div class="trade-signal-row">
      <span class="signal-title pullback">WLS</span>
      <span>기울기 <b class="${slope>0?'signal-hot':slope<0?'signal-cold':''}">${Number.isFinite(slope)?`${slope>0?'+':''}${slope.toFixed(3)}%/day`:'—'}</b></span><i>·</i>
      <span>t <b>${Number.isFinite(t)?t.toFixed(2):'—'}</b></span><i>·</i>
      <span>앵커 6개 · H=84</span>
    </div>
  </div>`;
}

function backtestLine(stock) {
  const f = stock?.forecast || {};
  if (!f.forecastable) return 'Forecast PJT 1: <b>예측 불가</b> <small>· 최소 272거래일 필요</small>';
  const ct=Number(f.conf_t), cz=Number(f.conf_z), gap=Number(f.z_today_gap);
  return `Forecast PJT 1: <b>방향·상대순위 예측</b> <small>· conf_t ${Number.isFinite(ct)?ct.toFixed(2):'—'} · conf_z ${Number.isFinite(cz)?cz.toFixed(2):'—'} · today gap z ${Number.isFinite(gap)?gap.toFixed(2):'—'}</small>`;
}

function stockCard(stock) {
  const ticker = stock.symbol || stock.ticker || '—';
  const sector = stock.sector || '—';
  const key = `${stock.category}:${stock.ticker}`;
  return `<article class="stock-card ${heatClass(stock)}" data-stock-key="${escapeHtml(key)}" data-ticker="${escapeHtml(stock.ticker)}">
    <section class="stock-info-pane">
      <div class="stock-headline-row">
        <h2>${escapeHtml(stock.name)} <span>(${escapeHtml(ticker)})</span></h2>
        <button class="score-pill ${heatClass(stock)}" type="button" data-score-detail="${escapeHtml(stock.ticker)}" aria-label="Forecast 상세 보기">${scoreText(stock)}</button>
      </div>
      <div class="stock-meta-line">
        <span class="sector-name">${escapeHtml(sector)}</span><i>·</i>
        <span class="market-stat">${marketSizeLabel(stock)} ${marketSize(stock.market_size_krw)}</span><i>·</i>
        <span class="market-stat">현재가 ${money(stock.close, stock.currency)} ${changeText(stock.day_change_pct)}</span>
      </div>
      ${forecastSignalLines(stock)}
      <div class="news-one-line" data-news-line><span class="line-label">NEWS</span><span class="line-placeholder">최신 뉴스 불러오는 중…</span></div>
      <div class="backtest-one-line">${backtestLine(stock)}</div>
    </section>
    <section class="stock-chart-pane"><div class="inline-chart" data-chart-box><div class="chart-loading">63D + 20D FORECAST</div></div></section>
  </article>`;
}

function renderList() {
  const { totalCapMatched } = filterItems();
  const list = $('#stockList');
  list.innerHTML = state.filtered.length
    ? state.filtered.map(stockCard).join('')
    : '<div class="empty-state">선택한 시총 기준의 검색 결과가 없습니다.</div>';
  $('#resultCount').textContent = state.query
    ? `${state.filtered.length.toLocaleString()}개`
    : `TOP ${Math.min(DEFAULT_TOP_N, totalCapMatched).toLocaleString()} / ${totalCapMatched.toLocaleString()}개`;
  activateLazyCards();
}

function renderMeta() {
  const data = currentData();
  if (!data) {
    $('#marketDate').textContent = '—';
    $('#coverage').textContent = '—';
    $('#scanStatus').textContent = '—';
    return;
  }
  $('#marketDate').textContent = data.market_date || '—';
  $('#coverage').textContent = `가격수신 ${Number(data.coverage_pct || 0).toFixed(1)}%`;
  $('#scanStatus').textContent = `${data.scan_mode === 'QUICK' ? '장중 QUICK' : '종가 확정 FULL'} · Forecast PJT 1 score 순`;
}

function stockByTicker(ticker) {
  return (currentData()?.items || []).find((x) => x.ticker === ticker) || null;
}

function chartRows(detail) {
  const c = detail?.chart || {};
  const d = c.d || [];
  return d.map((date, i) => {
    const close = Number(c.c?.[i]);
    const open = Number(c.o?.[i]);
    const high = Number(c.h?.[i]);
    const low = Number(c.lo?.[i]);
    return {
      date, close,
      open: Number.isFinite(open) ? open : close,
      high: Number.isFinite(high) ? high : close,
      low: Number.isFinite(low) ? low : close,
      mid: Number(c.m?.[i]),
      upper: Number(c.u?.[i]),
      lower: Number(c.l?.[i]),
    };
  }).filter((x) => Number.isFinite(x.close)).slice(-CHART_TRADING_DAYS);
}

function drawForecastChart(el, detail) {
  // Never return silently: any early exit here used to leave the card stuck on
  // its "63D + 20D FORECAST" placeholder with no indication of why.
  if (!el) { console.warn('drawForecastChart: no element'); return; }
  const data = chartRows(detail);
  if (data.length < 5) {
    const n = (detail?.chart?.d || []).length;
    el.innerHTML = `<div class="chart-empty">차트 데이터 없음<br><small>chart.d = ${n}봉</small></div>`;
    return;
  }

  // The forecast is an OVERLAY. Candles must render whether or not the payload
  // carries a usable forecast block - stale data files, newly listed names and
  // sub-272-session histories all legitimately have no forecast.
  const f = detail?.forecast || {};
  const m = Number(f.slope_log_per_day);
  const b = Number(f.intercept_log);
  const p0 = Number(f.current_price ?? data[data.length - 1].close);
  const conf = Number(f.confidence);
  const hasForecast = Boolean(f.forecastable) && [m, b, p0].every(Number.isFinite);

  const last = data.length - 1;
  const fitted = hasForecast ? data.map((_, i) => Math.exp(b + m * (i - last))) : [];
  const future = hasForecast && Array.isArray(f.projection) ? f.projection : [];
  const projection = hasForecast
    ? [{ offset: 0, price: p0 }].concat(future
        .map((x) => ({ offset: Number(x.offset), price: Number(x.price) }))
        .filter((x) => Number.isFinite(x.offset) && Number.isFinite(x.price)))
    : [];
  const futureSlots = 20;

  const bandVals = data.flatMap((r) => [r.mid, r.upper, r.lower]).filter(Number.isFinite);
  const all = data.flatMap((r) => [r.low, r.high]).filter(Number.isFinite)
    .concat(bandVals, fitted, projection.map((x) => x.price)).filter(Number.isFinite);
  if (!all.length) { el.innerHTML = '<div class="chart-empty">차트 데이터 없음</div>'; return; }
  let lo = Math.min(...all), hi = Math.max(...all);
  if (!(hi > lo)) { lo *= 0.99; hi *= 1.01; }
  const pr = (hi - lo) * 0.08 || Math.abs(hi) * 0.01 || 1; lo -= pr; hi += pr;

  const W = 700, H = 250, pad = { l: 12, r: 55, t: 16, b: 25 };
  const totalSlots = data.length + futureSlots;
  const plotW = W - pad.l - pad.r, step = plotW / totalSlots;
  const X = (i) => pad.l + (i + 0.5) * step;
  const Y = (v) => pad.t + (hi - v) * (H - pad.t - pad.b) / (hi - lo);

  let grid = '';
  for (let i = 0; i < 4; i++) {
    const y = pad.t + i * (H - pad.t - pad.b) / 3;
    const v = hi - i * (hi - lo) / 3;
    const label = detail.currency === 'KRW' ? Math.round(v).toLocaleString('ko-KR') : `$${v.toFixed(Math.abs(v) >= 100 ? 0 : 1)}`;
    grid += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y}" y2="${y}" class="chart-grid"/><text x="${W - pad.r + 6}" y="${y + 4}" class="price-axis">${label}</text>`;
  }

  const bandPath = (key) => {
    const pts = data.map((r, i) => (Number.isFinite(r[key]) ? `${X(i).toFixed(1)},${Y(r[key]).toFixed(1)}` : null)).filter(Boolean);
    return pts.length > 1 ? `<path d="M${pts.join(' L')}" class="chart-band-line chart-band-${key}"/>` : '';
  };
  const bands = bandVals.length ? bandPath('upper') + bandPath('mid') + bandPath('lower') : '';

  const bodyW = Math.max(1.5, Math.min(4.8, step * 0.6));
  let candles = '';
  data.forEach((r, i) => {
    const x = X(i), yo = Y(r.open), yc = Y(r.close), yh = Y(r.high), yl = Y(r.low);
    const klass = r.close >= r.open ? 'chart-candle-up' : 'chart-candle-down';
    const top = Math.min(yo, yc), bh = Math.max(1.1, Math.abs(yc - yo));
    candles += `<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="chart-candle-wick ${klass}"/><rect x="${x - bodyW / 2}" y="${top}" width="${bodyW}" height="${bh}" class="${klass}" rx=".4"/>`;
  });

  const todayX = X(last);
  let overlay = '';
  let legendExtra = '';
  if (hasForecast) {
    const fitPath = fitted.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
    const predPath = projection.map((r, i) => `${i ? 'L' : 'M'}${X(last + r.offset).toFixed(1)},${Y(r.price).toFixed(1)}`).join(' ');
    const anchorByDate = new Map(data.map((r, i) => [r.date, i]));
    let anchors = '';
    (f.anchors || []).forEach((a) => {
      const i = anchorByDate.get(a.date);
      if (i == null) return;
      anchors += `<circle cx="${X(i)}" cy="${Y(Number(a.close))}" r="4" class="forecast-anchor"><title>${escapeHtml(a.date)} · RV ${Number(a.rel_volume).toFixed(2)} · w ${Number(a.weight).toFixed(3)}</title></circle>`;
    });
    const gap = `<line x1="${todayX}" x2="${todayX}" y1="${Y(Math.exp(b))}" y2="${Y(p0)}" class="forecast-gap-line"/>`;
    const opacity = Number.isFinite(conf) ? Math.max(0.15, Math.min(1, conf)) : 0.15;
    overlay = `<path d="${fitPath}" class="forecast-fit-line"/>${anchors}${gap}<line x1="${todayX}" x2="${todayX}" y1="${pad.t}" y2="${H - pad.b}" class="forecast-today-line"/><path d="${predPath}" class="forecast-projection-line" style="opacity:${opacity.toFixed(3)}"/>`;
    const anchorTitle = (f.anchors || []).map((a, i) => `A${i + 1} ${a.date} · RV ${Number(a.rel_volume).toFixed(2)} · w ${Number(a.weight).toFixed(3)}`).join(' | ');
    legendExtra = `<span>WLS REGRESSION</span><span title="${escapeHtml(anchorTitle)}">ANCHOR 6</span><span>20D FORECAST</span>`;
  } else {
    legendExtra = `<span class="legend-muted" title="${escapeHtml(f.reason || 'forecast 데이터 없음')}">FORECAST 없음</span>`;
  }

  const rightLabel = hasForecast
    ? `<text x="${X(last + 20)}" y="${H - 6}" text-anchor="end" class="date-axis">+20D</text>`
    : `<text x="${X(last)}" y="${H - 6}" text-anchor="end" class="date-axis">${escapeHtml(data[last]?.date?.slice(5) || '')}</text>`;
  const dateLabels = `<text x="${X(0)}" y="${H - 6}" text-anchor="start" class="date-axis">${escapeHtml(data[0]?.date?.slice(5) || '')}</text>${hasForecast ? `<text x="${todayX}" y="${H - 6}" text-anchor="middle" class="date-axis">TODAY</text>` : ''}${rightLabel}`;

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="주가 차트">${grid}${bands}${candles}${overlay}${dateLabels}</svg><div class="chart-legend"><span>CANDLE</span><span>BB</span>${legendExtra}</div>`;
}


function newsSearchQuery(stock) {
  const name = String(stock?.name || '').trim();
  const symbol = String(stock?.symbol || stock?.ticker || '').trim();
  if (stock?.category === 'KR_ETF') return `${name} ${symbol} ETF`;
  if (stock?.category === 'KR') return `${name} ${symbol} 주식`;
  if (stock?.category === 'US_ETF') return `${name} ${symbol} ETF`;
  return `${name} ${symbol} stock`;
}

function newsSearchUrl(stock) {
  const q = encodeURIComponent(newsSearchQuery(stock));
  return ['KR','KR_ETF'].includes(stock?.category)
    ? `https://news.google.com/search?q=${q}&hl=ko&gl=KR&ceid=KR:ko`
    : `https://news.google.com/search?q=${q}&hl=en-US&gl=US&ceid=US:en`;
}

function jsonp(url, params={}, timeoutMs=12000) {
  return new Promise((resolve, reject) => {
    const callback = `__dtcNews_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const script = document.createElement('script');
    const query = new URLSearchParams({ ...params, callback });
    const sep = url.includes('?') ? '&' : '?';
    let done = false;
    const cleanup = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      script.remove();
      try { delete window[callback]; } catch (_) { window[callback] = undefined; }
    };
    window[callback] = (payload) => { cleanup(); resolve(payload); };
    script.onerror = () => { cleanup(); reject(new Error('news proxy load failed')); };
    const timer = setTimeout(() => { cleanup(); reject(new Error('news proxy timeout')); }, timeoutMs);
    script.src = `${url}${sep}${query.toString()}`;
    script.async = true;
    document.head.appendChild(script);
  });
}

async function loadHeadline(stock, card) {
  const line = $('[data-news-line]', card);
  if (!line?.isConnected) return;
  const direct = newsSearchUrl(stock);
  if (!NEWS_PROXY_URL || NEWS_PROXY_URL.includes('PASTE_YOUR')) {
    line.innerHTML = `<span class="line-label">NEWS</span><a href="${direct}" target="_blank" rel="noopener noreferrer">최신 뉴스 보기 ↗</a>`;
    return;
  }

  const key = `${stock.category}:${stock.ticker}`;
  const cached = state.newsCache.get(key);
  let articles = null;
  if (cached && Date.now() - cached.at < NEWS_CACHE_MS) {
    articles = cached.articles;
  } else {
    try {
      const payload = await jsonp(NEWS_PROXY_URL, {
        q: newsSearchQuery(stock),
        region: ['KR','KR_ETF'].includes(stock.category) ? 'KR' : 'US',
        limit: '1',
      });
      if (!payload?.ok) throw new Error(payload?.error || 'news proxy error');
      articles = Array.isArray(payload.articles) ? payload.articles.slice(0, 1) : [];
      state.newsCache.set(key, { at:Date.now(), articles });
    } catch (err) {
      console.error('headline', err);
      articles = [];
    }
  }

  if (!line?.isConnected) return;
  const article = articles?.[0];
  line.innerHTML = article?.title && article?.link
    ? `<span class="line-label">NEWS</span><a href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(article.title)}">${escapeHtml(article.title)}</a>`
    : `<span class="line-label">NEWS</span><a href="${direct}" target="_blank" rel="noopener noreferrer">최신 뉴스 보기 ↗</a>`;
}

function drawChart(el, detail) {
  if (!el) return;
  try {
    drawForecastChart(el, detail);
  } catch (err) {
    console.error('drawChart', err);
    el.innerHTML = `<div class="chart-empty">차트 렌더 오류<br><small>${escapeHtml(String(err && err.message || err))}</small></div>`;
  }
}

async function hydrateCard(card) {
  if (!card?.isConnected || card.dataset.hydrated === '1') return;
  card.dataset.hydrated = '1';
  const ticker = card.dataset.ticker;
  const stock = stockByTicker(ticker);
  if (!stock) return;

  // News and chart are intentionally independent. A news/proxy failure must
  // never prevent the detail JSON from loading or the forecast chart rendering.
  Promise.resolve()
    .then(() => loadHeadline(stock, card))
    .catch((err) => {
      console.error('headline hydrate', err);
      const line = $('[data-news-line]', card);
      if (line?.isConnected) {
        const direct = newsSearchUrl(stock);
        line.innerHTML = `<span class="line-label">NEWS</span><a href="${direct}" target="_blank" rel="noopener noreferrer">최신 뉴스 보기 ↗</a>`;
      }
    });

  const chartBox = () => $('[data-chart-box]', card)
    || $(`.stock-card[data-ticker="${CSS.escape(String(stock.ticker))}"] [data-chart-box]`);

  try {
    const detail = await ensureDetail(stock);
    const box = chartBox();
    if (!box) { console.warn('chart box missing for', stock.ticker); return; }
    drawChart(box, detail);
  } catch (err) {
    console.error('chart detail', stock.ticker, stock.detail_path, err);
    const box = chartBox();
    if (box) box.innerHTML = `<div class="chart-empty">차트 로드 실패<br><small>${escapeHtml(String(err && err.message || err))}</small></div>`;
  }
}

function activateLazyCards() {
  if (state.cardObserver) state.cardObserver.disconnect();
  state.cardObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      state.cardObserver.unobserve(entry.target);
      void hydrateCard(entry.target);
    });
  }, { rootMargin:'500px 0px' });
  $$('.stock-card').forEach((card) => state.cardObserver.observe(card));
}

async function openScoreDetail(stock) {
  const modal=$('#scoreModal'), body=$('#scoreModalBody');
  $('#scoreModalTitle').textContent=`${stock.name} (${stock.symbol || stock.ticker}) · ${scoreText(stock)}`;
  modal.hidden=false; document.body.classList.add('modal-open');
  body.innerHTML='<div class="modal-loading">Forecast 상세를 불러오는 중…</div>';
  let detail=stock; try { detail=await ensureDetail(stock); } catch (_) {}
  if (modal.hidden) return;
  const f=detail.forecast || stock.forecast || {};
  if (!f.forecastable) { body.innerHTML='<div class="empty-state">Forecast 불가: 최소 272거래일 및 각 버킷 5개 이상의 유효 거래일이 필요합니다.</div>'; return; }
  const rows=[
    ['20D 예측수익률', `${Number(f.r_pred_20d)*100 >= 0 ? '+' : ''}${(Number(f.r_pred_20d)*100).toFixed(2)}%`],
    ['Confidence', `${(Number(f.confidence)*100).toFixed(1)}%`],
    ['기울기', `${Number(f.slope_pct_per_day).toFixed(4)}% / day`],
    ['t-stat', Number(f.t_stat).toFixed(3)],
    ['conf_t / conf_z', `${Number(f.conf_t).toFixed(3)} / ${Number(f.conf_z).toFixed(3)}`],
    ['오늘 괴리 z', Number.isFinite(Number(f.z_today_gap)) ? Number(f.z_today_gap).toFixed(3) : '—'],
    ['최종 Score', scoreText(stock)],
  ];
  const anchors=(f.anchors||[]).map((a,i)=>`<div class="score-row"><div><b>Anchor ${i+1}</b><small>${escapeHtml(a.date)} · τ ${a.tau} · RV ${Number(a.rel_volume).toFixed(2)}</small></div><strong>${money(Number(a.close), stock.currency)}<small>w ${Number(a.weight).toFixed(3)}</small></strong></div>`).join('');
  body.innerHTML=`<div class="score-total"><span>FORECAST PJT 1</span><b>${scoreText(stock)}</b><small> score</small></div><div class="score-rows">${rows.map(([a,b])=>`<div class="score-row"><div><b>${a}</b></div><strong>${b}</strong></div>`).join('')}${anchors}</div><div class="backtest-one-line" style="margin-top:14px">Ranking = clipped 20D predicted return × confidence · H=84 · 6 relative-volume anchors</div>`;
}

function closeModal() {
  $('#scoreModal').hidden = true;
  document.body.classList.remove('modal-open');
}

async function switchCategory(category, force=false) {
  if (!CATEGORY[category]) return;
  state.category = category;
  state.query = '';
  $('#searchInput').value = '';
  if (force) {
    state.data[category] = null;
    for (const key of [...state.detailCache.keys()]) if (key.startsWith(`${category}:`)) state.detailCache.delete(key);
  }

  $$('.market-tab').forEach((b) => b.classList.toggle('active', b.dataset.category === category));
  renderSizeFilters();
  $('#status').hidden = false;
  $('#stockList').hidden = true;
  $('#status').textContent = '데이터를 불러오는 중입니다.';
  try {
    await ensureData(category, force);
    renderMeta();
    renderList();
    $('#status').hidden = true;
    $('#stockList').hidden = false;
  } catch (err) {
    console.error(err);
    renderMeta();
    $('#status').hidden = false;
    $('#status').textContent = '데이터를 불러오지 못했습니다.';
  }
}


// -----------------------------------------------------------------------------
// Quiz mode
// -----------------------------------------------------------------------------
function quizRandomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function quizPick(arr) {
  return arr?.length ? arr[quizRandomInt(0, arr.length - 1)] : null;
}

function quizPickEntry(pool) {
  if (!pool?.length) return null;
  const groups = new Map();
  pool.forEach((item) => {
    const key = String(item?.category || 'OTHER');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  const category = quizPick([...groups.keys()]);
  return quizPick(groups.get(category));
}

async function ensureQuizPool(force=false) {
  if (state.quiz.pool && !force) return state.quiz.pool;
  const settled = await Promise.allSettled(QUIZ_SHARDS.map(async (shard) => {
    const response = await fetch(dataUrl(`data/quiz/${shard}/manifest.json`, force), { cache: force ? 'no-store' : 'default' });
    if (!response.ok) throw new Error(`${shard} quiz ${response.status}`);
    return response.json();
  }));
  const pool = [];
  settled.forEach((result) => {
    if (result.status !== 'fulfilled') return;
    const rows = Array.isArray(result.value?.items) ? result.value.items : [];
    rows.forEach((row) => {
      const n = Number(row?.points || 0);
      const cap = Number(row?.market_size_krw);
      if (n >= QUIZ_WINDOW_DAYS + 20 && Number.isFinite(cap) && cap >= QUIZ_MIN_MARKET_SIZE && row?.detail_path) pool.push(row);
    });
  });
  if (force) state.quiz.detailCache.clear();
  state.quiz.pool = pool;
  return pool;
}

async function ensureQuizStock(entry, force=false) {
  const key = `${entry?.category}:${entry?.ticker}`;
  if (!force && state.quiz.detailCache.has(key)) return state.quiz.detailCache.get(key);
  if (!entry?.detail_path) throw new Error('quiz detail path missing');
  const response = await fetch(dataUrl(entry.detail_path, force), { cache: force ? 'no-store' : 'default' });
  if (!response.ok) throw new Error(`quiz detail ${response.status}`);
  const detail = await response.json();
  state.quiz.detailCache.set(key, detail);
  return detail;
}

function quizRows(stock, start, count=QUIZ_WINDOW_DAYS) {
  const rows = [];
  for (let i = start; i < start + count; i++) {
    const o = Number(stock.o?.[i]), h = Number(stock.h?.[i]), l = Number(stock.l?.[i]);
    const c = Number(stock.c?.[i]), v = Number(stock.v?.[i]);
    if (![o,h,l,c].every(Number.isFinite)) return [];
    rows.push({
      index:i,
      date:String(stock.d?.[i] || ''),
      open:o,
      high:h,
      low:l,
      close:c,
      volume:Number.isFinite(v) ? Math.max(0,v) : 0,
    });
  }
  return rows;
}

function quizBollinger(stock, start, count=QUIZ_WINDOW_DAYS) {
  const closes = (stock.c || []).map(Number);
  return Array.from({length:count}, (_, offset) => {
    const idx = start + offset;
    if (idx < 19) return { mid:NaN, upper:NaN, lower:NaN };
    const w = closes.slice(idx - 19, idx + 1).filter(Number.isFinite);
    if (w.length !== 20) return { mid:NaN, upper:NaN, lower:NaN };
    const mid = w.reduce((a,b) => a+b, 0) / w.length;
    const variance = w.reduce((a,b) => a + (b-mid)*(b-mid), 0) / w.length;
    const sd = Math.sqrt(Math.max(0, variance));
    return { mid, upper:mid + 2*sd, lower:mid - 2*sd };
  });
}

function quizVolumeProfile(rows) {
  if (!rows?.length) return null;
  const pmin = Math.min(...rows.map(r => r.low));
  const pmax = Math.max(...rows.map(r => r.high));
  if (!(pmax > pmin)) return null;
  const edges = Array.from({length:11}, (_, i) => pmin + (pmax-pmin)*i/10);
  const values = Array(10).fill(0);
  rows.forEach((r) => {
    const span = r.high - r.low;
    if (span > 1e-12) {
      for (let b=0;b<10;b++) {
        const overlap = Math.max(0, Math.min(r.high, edges[b+1]) - Math.max(r.low, edges[b]));
        values[b] += r.volume * overlap / span;
      }
    } else {
      const b = Math.max(0, Math.min(9, Math.floor((r.close-pmin)/(pmax-pmin)*10)));
      values[b] += r.volume;
    }
  });
  return { pmin, pmax, edges, values, max:Math.max(...values, 1) };
}

function quizResidualSignature(values) {
  if (!values?.length || values.some(v => !Number.isFinite(v) || v <= 0)) return [];
  const logs = values.map(Math.log);
  const a = logs[0], b = logs[logs.length-1];
  const residual = logs.map((v,i) => v - (a + (b-a)*i/Math.max(1,logs.length-1)));
  const mean = residual.reduce((x,y)=>x+y,0)/residual.length;
  const sd = Math.sqrt(residual.reduce((x,y)=>x+(y-mean)*(y-mean),0)/residual.length) || 1;
  return residual.map(v => (v-mean)/sd);
}

function quizShapeDistance(a, b) {
  const sa = quizResidualSignature(a), sb = quizResidualSignature(b);
  if (!sa.length || sa.length !== sb.length) return Infinity;
  return Math.sqrt(sa.reduce((sum,v,i) => sum + (v-sb[i])*(v-sb[i]), 0) / sa.length);
}

function quizDailyVol(values) {
  if (!values?.length || values.length < 2) return 0;
  const r=[];
  for(let i=1;i<values.length;i++){
    const a=Number(values[i-1]), b=Number(values[i]);
    if(a>0&&b>0) r.push(Math.log(b/a));
  }
  if(!r.length) return 0;
  const mean=r.reduce((x,y)=>x+y,0)/r.length;
  return Math.sqrt(r.reduce((x,y)=>x+(y-mean)*(y-mean),0)/r.length);
}


function quizNormalizeToStart(values, targetStart) {
  if (!values?.length || !Number.isFinite(targetStart) || targetStart <= 0) return [];
  const first = Number(values[0]);
  if (!Number.isFinite(first) || first <= 0) return [];
  const scale = targetStart / first;
  return values.map((v) => Number(v) * scale);
}

function quizEndGapRatio(a, b) {
  const ea = Number(a?.[a.length - 1]);
  const eb = Number(b?.[b.length - 1]);
  if (!(ea > 0) || !(eb > 0)) return 0;
  return Math.abs(ea - eb) / Math.max(1e-9, Math.min(ea, eb));
}

function quizSeriesSimilarity(a, b) {
  return quizShapeDistance(a, b) + Math.min(1.5, quizEndGapRatio(a, b) * 1.6);
}

function quizPrepareOption(raw, displayStart) {
  const rawCloses = Array.isArray(raw?.closes) ? raw.closes.map(Number) : [];
  const rawCandles = Array.isArray(raw?.candles) ? raw.candles : [];
  const displayCloses = quizNormalizeToStart(rawCloses, displayStart);
  if (!displayCloses.length) return null;
  const displayCandles = quizTransformCandles(rawCandles, displayCloses);
  return {
    rawCloses,
    rawCandles,
    closes: displayCloses,
    candles: displayCandles,
    end: displayCloses[displayCloses.length - 1],
  };
}


function quizFastDistractorSeries(correctValues, targetEndMultiplier, variant) {
  if (!correctValues?.length || correctValues.some(v => !Number.isFinite(v) || v <= 0)) return null;
  const n = correctValues.length;
  const start = Number(correctValues[0]);
  const correctEnd = Number(correctValues[n - 1]);
  if (!(start > 0) || !(correctEnd > 0)) return null;
  const targetEnd = correctEnd * targetEndMultiplier;
  if (!(targetEnd > 0)) return null;

  // Anchor every option at exactly the same first close.  The endpoint is chosen
  // separately, while the interior shape is a bounded market-like residual.
  const logStart = Math.log(start);
  const logEnd = Math.log(targetEnd);
  const dailyVol = Math.max(0.008, Math.min(0.045, quizDailyVol(correctValues) || 0.018));
  const amp = Math.max(0.045, Math.min(0.14, dailyVol * Math.sqrt(n) * 0.95));
  const sig = quizResidualSignature(correctValues);
  const out = [];
  for (let i=0;i<n;i++) {
    const x = i / Math.max(1, n - 1);
    const trend = logStart + (logEnd - logStart) * x;
    let shape = 0;
    if (variant === 0) {
      // selloff -> recovery
      shape = -amp * Math.sin(Math.PI * x) + 0.30 * amp * Math.sin(3 * Math.PI * x);
    } else if (variant === 1) {
      // early rally -> fade
      shape = 0.98 * amp * Math.sin(Math.PI * x) - 0.36 * amp * Math.sin(2 * Math.PI * x);
    } else if (variant === 2) {
      // two-leg whipsaw
      shape = 0.80 * amp * Math.sin(2 * Math.PI * x) + 0.34 * amp * Math.sin(5 * Math.PI * x);
    } else if (variant === 3) {
      // late breakout: weak first half, acceleration into the finish
      shape = -0.55 * amp * Math.sin(Math.PI * x) + 0.72 * amp * Math.sin(Math.PI * Math.pow(x, 1.8));
    } else if (variant === 4) {
      // late breakdown: strong first half, sharp fade late
      shape = 0.62 * amp * Math.sin(Math.PI * x) - 0.78 * amp * Math.sin(Math.PI * Math.pow(1 - x, 1.7));
    } else {
      // range / W-shape
      shape = -0.62 * amp * Math.sin(3 * Math.PI * x) + 0.28 * amp * Math.sin(6 * Math.PI * x);
    }
    const src = sig.length === n ? sig[(variant === 0 ? n - 1 - i : (i * (variant + 2)) % n)] : 0;
    const taper = Math.sin(Math.PI * x); // exactly zero at both edges
    const micro = Number.isFinite(src) ? src * dailyVol * 0.20 * taper : 0;
    out.push(Math.exp(trend + shape + micro));
  }
  out[0] = start;
  out[n - 1] = targetEnd;
  return out;
}

function quizBuildFastOptions(correctSeries, correctRawCandles, displayStartValue) {
  const displayStart = Number(displayStartValue);
  if (!(displayStart > 0)) return null;
  const correct = quizPrepareOption({ closes:correctSeries, candles:correctRawCandles }, displayStart);
  if (!correct) return null;

  // Endpoints are intentionally separated by >=10%. For each endpoint, choose
  // the shape template that is farthest from the real path and already-selected
  // distractors. This is CPU-only and normally completes in a few milliseconds.
  const multipliers = [0.76, 0.88, 1.14];
  const distractors = [];
  for (const mult of multipliers) {
    let best = null;
    let bestScore = -Infinity;
    for (let variant=0; variant<6; variant++) {
      const closes = quizFastDistractorSeries(correctSeries, mult, variant);
      if (!closes) continue;
      const candles = quizTransformCandles(correctRawCandles, closes);
      const prepared = quizPrepareOption({ closes, candles }, displayStart);
      if (!prepared) continue;
      const comparisons = [correct, ...distractors];
      const minShapeDistance = Math.min(...comparisons.map(x => quizShapeDistance(prepared.closes, x.closes)));
      if (minShapeDistance > bestScore) {
        bestScore = minShapeDistance;
        best = prepared;
      }
    }
    if (!best) return null;
    distractors.push(best);
  }

  const all = [correct, ...distractors];
  const endsOK = all.every((opt, i) => all.every((other, j) => i === j || quizEndGapRatio(opt.closes, other.closes) >= 0.10));
  if (!endsOK) return null;
  return { correct, distractors };
}

function quizTransformCandles(sourceRows, transformedCloses) {
  if (!sourceRows?.length || sourceRows.length !== transformedCloses?.length) return [];
  return sourceRows.map((r, i) => {
    const targetClose = Number(transformedCloses[i]);
    const srcClose = Number(r.close);
    const scale = srcClose > 0 ? targetClose / srcClose : 1;
    const open = Number(r.open) * scale;
    const high = Number(r.high) * scale;
    const low = Number(r.low) * scale;
    return {
      open,
      high:Math.max(high, open, targetClose),
      low:Math.min(low, open, targetClose),
      close:targetClose,
    };
  });
}

async function buildQuizQuestion(pool) {
  // Fast path: one stock JSON fetch per question.  No repeated donor-stock
  // network search is required to manufacture the three distractors.
  for (let attempt=0; attempt<24; attempt++) {
    const entry = quizPickEntry(pool);
    if (!entry) continue;
    let stock;
    try { stock = await ensureQuizStock(entry); } catch (_) { continue; }
    const n = stock?.c?.length || 0;
    if (n < QUIZ_WINDOW_DAYS + 20) continue;
    const start = quizRandomInt(19, n - QUIZ_WINDOW_DAYS);
    const rows = quizRows(stock, start);
    if (rows.length !== QUIZ_WINDOW_DAYS) continue;

    // Quiz is always "predict the next 30 trading days": show D1-D60 and hide D61-D90.
    const hiddenPart = 2;
    const hiddenStart = 60;
    const hiddenEnd = 90;
    const correctSeries = rows.slice(hiddenStart, hiddenEnd).map(r => r.close);
    if (correctSeries.some(v => !Number.isFinite(v) || v <= 0)) continue;
    const correctRawCandles = rows.slice(hiddenStart, hiddenEnd).map(r => ({open:r.open, high:r.high, low:r.low, close:r.close}));
    const visibleAnchor = Number(rows[hiddenStart - 1]?.close);
    const built = quizBuildFastOptions(correctSeries, correctRawCandles, visibleAnchor);
    if (!built) continue;

    const correctIndex = quizRandomInt(0, 3);
    const options = [];
    let di = 0;
    for (let i=0;i<4;i++) options.push(i === correctIndex ? built.correct : built.distractors[di++]);

    return {
      entry,
      stock,
      rows,
      bb:quizBollinger(stock, start),
      // Volume profile is intentionally calculated ONLY from the visible D1-D60.
      profile:quizVolumeProfile(rows.slice(0, hiddenStart)),
      start,
      hiddenPart,
      hiddenStart,
      hiddenEnd,
      correctIndex,
      options,
      selectedIndex:null,
    };
  }
  return null;
}

function quizPath(values, X, Y) {
  return values.map((v,i) => Number.isFinite(v) ? `${i?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}` : '').filter(Boolean).join(' ');
}

function renderQuizOption(option, index, correctIndex, selectedIndex, answered) {
  const values = option?.closes || [];
  const candles = option?.candles || [];
  const W=260, H=126, pad={l:32,r:32,t:12,b:12};
  const extrema = candles.flatMap(c => [Number(c.low), Number(c.high)]).filter(Number.isFinite);
  const fitted = extrema.length ? extrema : values.filter(Number.isFinite);
  let lo=Math.min(...fitted), hi=Math.max(...fitted);
  if (!(hi>lo)) { lo*=.99; hi*=1.01; }
  const extra=(hi-lo)*.08 || Math.abs(hi)*.01 || 1;
  lo-=extra; hi+=extra;
  const plotW=W-pad.l-pad.r;
  const step=plotW/Math.max(1,values.length);
  const X=i=>pad.l+(i+.5)*step;
  const Y=v=>pad.t+(hi-v)*(H-pad.t-pad.b)/(hi-lo);
  const selected = selectedIndex === index;
  const klass = answered ? (index===correctIndex ? ' correct' : selected ? ' wrong' : '') : selected ? ' selected' : '';
  const bodyW=Math.max(2.2,Math.min(5.5,step*.58));
  let candleSvg='';
  candles.forEach((r,i)=>{
    if (![r.open,r.high,r.low,r.close].every(Number.isFinite)) return;
    const x=X(i),yo=Y(r.open),yc=Y(r.close),yh=Y(r.high),yl=Y(r.low);
    const up=r.close>=r.open, ck=up?'quiz-candle-up':'quiz-candle-down';
    const top=Math.min(yo,yc),bh=Math.max(1.2,Math.abs(yc-yo));
    candleSvg+=`<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="quiz-option-wick ${ck}"/><rect x="${x-bodyW/2}" y="${top}" width="${bodyW}" height="${bh}" class="quiz-option-body ${ck}" rx=".35"/>`;
  });
  return `<button class="quiz-choice${klass}" type="button" data-quiz-choice="${index}" ${answered?'disabled':''}>
    <span class="quiz-choice-key">${index+1}</span>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="보기 ${index+1}">
      <line x1="${pad.l}" x2="${W-pad.r}" y1="${pad.t}" y2="${pad.t}" class="quiz-option-grid"/>
      <line x1="${pad.l}" x2="${W-pad.r}" y1="${H/2}" y2="${H/2}" class="quiz-option-grid"/>
      <line x1="${pad.l}" x2="${W-pad.r}" y1="${H-pad.b}" y2="${H-pad.b}" class="quiz-option-grid"/>
      ${candleSvg}
      <path d="${quizPath(values,X,Y)}" class="quiz-option-line"/>
    </svg>
  </button>`;
}

function renderQuizMainChart(question, answered=false) {
  const el = $('#quizMainChart');
  if (!el || !question) return;
  const {rows,bb,profile,hiddenStart,hiddenEnd,selectedIndex,options} = question;
  const preview = (!answered && Number.isInteger(selectedIndex) && selectedIndex >= 0 && selectedIndex < options.length) ? options[selectedIndex] : null;
  const W=1000,H=430,pad={l:18,r:72,t:22,b:32};
  const vals = rows.flatMap((r,i)=>[r.low,r.high,bb[i]?.lower,bb[i]?.upper]).filter(Number.isFinite);
  let lo=Math.min(...vals), hi=Math.max(...vals);
  if (preview?.candles?.length) {
    preview.candles.forEach((r) => { vals.push(r.low, r.high); });
    lo = Math.min(lo, ...preview.candles.map((r) => r.low));
    hi = Math.max(hi, ...preview.candles.map((r) => r.high));
  }
  if (!(hi>lo)) { lo*=.99; hi*=1.01; }
  const extra=(hi-lo)*.055; lo-=extra; hi+=extra;
  const plotW=W-pad.l-pad.r, plotH=H-pad.t-pad.b;
  const X=i=>pad.l+(i+.5)*plotW/rows.length;
  const Y=v=>pad.t+(hi-v)*plotH/(hi-lo);
  const candleStep=plotW/rows.length;
  const bodyW=Math.max(2.1,candleStep*.56);
  const hiddenX1=pad.l+hiddenStart*candleStep;
  const hiddenX2=pad.l+hiddenEnd*candleStep;
  const isHidden=i=>!answered && i>=hiddenStart && i<hiddenEnd;

  let grid='';
  for(let g=0;g<5;g++){
    const y=pad.t+g*plotH/4;
    const v=hi-g*(hi-lo)/4;
    const label=question.stock.currency==='KRW'?Math.round(v).toLocaleString('ko-KR'):`$${v.toFixed(v>=100?0:1)}`;
    grid+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" class="quiz-chart-grid"/><text x="${W-pad.r+7}" y="${y+4}" class="quiz-price-axis">${label}</text>`;
  }

  let profileSvg='';
  if(profile){
    const maxWidth=plotW*.23;
    const visibleCurrent=rows[Math.max(0, hiddenStart - 1)]?.close;
    const currentBin=Math.max(0,Math.min(9,Math.floor((visibleCurrent-profile.pmin)/(profile.pmax-profile.pmin)*10)));
    for(let b=0;b<10;b++){
      const y1=Y(profile.edges[b+1]), y2=Y(profile.edges[b]);
      const barH=Math.max(1,Math.abs(y2-y1)-1);
      const bw=maxWidth*(profile.values[b]/profile.max);
      // Left-side volume profile: it stays entirely inside the visible 2/3 zone.
      profileSvg+=`<rect x="${pad.l}" y="${Math.min(y1,y2)+.5}" width="${bw}" height="${barH}" class="quiz-profile-bar${b===currentBin?' current':''}"/>`;
    }
  }

  function bbPath(key){
    let out='',drawing=false;
    for(let i=0;i<rows.length;i++){
      const v=Number(bb[i]?.[key]);
      if(!Number.isFinite(v)||isHidden(i)){drawing=false;continue;}
      out+=`${drawing?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;
      drawing=true;
    }
    return out.trim();
  }

  let candles='';
  rows.forEach((r,i)=>{
    if(isHidden(i)) return;
    const up=r.close>=r.open;
    const klass=up?'quiz-candle-up':'quiz-candle-down';
    const x=X(i), yo=Y(r.open), yc=Y(r.close), yh=Y(r.high), yl=Y(r.low);
    const top=Math.min(yo,yc), bh=Math.max(1.4,Math.abs(yc-yo));
    candles+=`<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="quiz-candle-wick ${klass}"/><rect x="${x-bodyW/2}" y="${top}" width="${bodyW}" height="${bh}" class="${klass}" rx=".5"/>`;
  });

  let previewSvg='';
  if (preview?.candles?.length) {
    const previewCloses = preview.closes || [];
    previewSvg += `<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-preview-zone"/>`;
    preview.candles.forEach((r, localIndex) => {
      if (![r.open,r.high,r.low,r.close].every(Number.isFinite)) return;
      const i = hiddenStart + localIndex;
      const x = X(i), yo = Y(r.open), yc = Y(r.close), yh = Y(r.high), yl = Y(r.low);
      const up = r.close >= r.open;
      const klass = up ? 'quiz-candle-up' : 'quiz-candle-down';
      const top = Math.min(yo, yc), bh = Math.max(1.4, Math.abs(yc - yo));
      previewSvg += `<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="quiz-preview-wick ${klass}"/><rect x="${x-bodyW/2}" y="${top}" width="${bodyW}" height="${bh}" class="quiz-preview-body ${klass}" rx=".5"/>`;
    });
    const previewPath = previewCloses.length ? quizPath(previewCloses, (idx) => X(hiddenStart + idx), Y) : '';
    if (previewPath) previewSvg += `<path d="${previewPath}" class="quiz-preview-line"/>`;
  }

  const dateIndices=[0,44,89];
  const dates=dateIndices.map((i)=>{
    const label=answered?(rows[i]?.date?.slice(2)||''):`D${i+1}`;
    const anchor=i===0?'start':i===89?'end':'middle';
    return `<text x="${i===0?pad.l:i===89?W-pad.r:X(i)}" y="${H-9}" text-anchor="${anchor}" class="quiz-date-axis">${escapeHtml(label)}</text>`;
  }).join('');

  const hiddenOverlay = answered
    ? `<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-reveal-zone"/>`
    : (!preview ? `<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-hidden-block"/><text x="${(hiddenX1+hiddenX2)/2}" y="${pad.t+plotH/2}" text-anchor="middle" class="quiz-hidden-label">HIDDEN</text>` : '');

  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${grid}
    <path d="${bbPath('upper')}" class="quiz-bb-line"/><path d="${bbPath('lower')}" class="quiz-bb-line"/><path d="${bbPath('mid')}" class="quiz-bb-mid"/>
    ${candles}${hiddenOverlay}${previewSvg}${profileSvg}${dates}
  </svg><div class="quiz-chart-legend"><span>CANDLE</span><span>BB 20,2</span><span>VISIBLE 60D · 10-ZONE PROFILE</span></div>`;
}

function renderQuizQuestion() {
  const q=state.quiz.question;
  if(!q) return;
  $('#quizStatus').hidden=true;
  $('#quizGame').hidden=false;
  $('#quizNumber').textContent=`QUIZ #${String(state.quiz.number).padStart(3,'0')}`;
  $('#quizIdentity').textContent=state.quiz.answered
    ? `${q.stock.name} (${q.stock.symbol || q.stock.ticker}) · ${q.rows[0].date} ~ ${q.rows[q.rows.length-1].date}`
    : '종목 · 기간 비공개';
  $('#quizSegmentLabel').textContent='마지막 1/3 · 다음 30거래일 가림';
  const instruction=$('#quizInstructionSub');
  if(instruction){
    instruction.textContent=state.quiz.answered
      ? '제출 후 실제 다음 30거래일 캔들과 정답 여부를 공개합니다.'
      : '앞 60거래일을 보고 다음 30거래일을 예측하세요. 보기를 누르면 후보가 빈 구간에 먼저 채워집니다.';
  }
  renderQuizMainChart(q,state.quiz.answered);
  $('#quizChoices').innerHTML=q.options.map((option,i)=>renderQuizOption(option,i,q.correctIndex,q.selectedIndex,state.quiz.answered)).join('');
  const submitBtn = $('#quizSubmitBtn');
  if (submitBtn) submitBtn.disabled = state.quiz.answered || !Number.isInteger(q.selectedIndex);
  const selectedHint = $('#quizSelectedHint');
  if (selectedHint) {
    selectedHint.textContent = state.quiz.answered
      ? `제출 완료 · 선택한 보기 ${Number.isInteger(q.selectedIndex) ? q.selectedIndex + 1 : '—'}번`
      : Number.isInteger(q.selectedIndex)
        ? `${q.selectedIndex + 1}번 보기를 차트에 적용했습니다. 마음에 들면 정답 제출을 누르세요.`
        : '보기를 선택하면 가려진 구간에 해당 차트가 먼저 채워집니다.';
  }
  const feedback=$('#quizFeedback');
  if(state.quiz.answered){
    feedback.hidden=false;
    const ok=q.selectedIndex===q.correctIndex;
    feedback.innerHTML=`<b>${ok?'정답입니다.':'오답입니다.'}</b> 정답은 ${q.correctIndex+1}번입니다. 실제 종목은 ${escapeHtml(q.stock.name)} (${escapeHtml(q.stock.symbol || q.stock.ticker)})이며, 가려진 30거래일 캔들을 차트에 공개했습니다.`;
  }else{
    feedback.hidden=true;
    feedback.textContent='';
  }
}

async function newQuizQuestion(forcePool=false) {
  if(state.quiz.loading) return;
  state.quiz.loading=true;
  const btn=$('#newQuizBtn');
  if(btn){btn.disabled=true;btn.textContent='문제 생성 중';}
  $('#quizStatus').hidden=false;
  $('#quizStatus').textContent='100조 이상 종목의 90거래일 차트를 불러오는 중입니다.';
  $('#quizGame').hidden=true;
  try{
    const pool=await ensureQuizPool(forcePool);
    if(!pool.length) throw new Error('quiz pool empty');
    const q=await buildQuizQuestion(pool);
    if(!q) throw new Error('could not build plausible distractors');
    q.selectedIndex = null;
    state.quiz.question=q;
    state.quiz.answered=false;
    state.quiz.number+=1;
    renderQuizQuestion();
  }catch(err){
    console.error('quiz',err);
    $('#quizStatus').hidden=false;
    $('#quizStatus').textContent='퀴즈 데이터가 아직 없습니다. 최신 코드 배포 후 ALL · FULL 스캔을 한 번 실행해 주세요.';
    $('#quizGame').hidden=true;
  }finally{
    state.quiz.loading=false;
    if(btn){btn.disabled=false;btn.textContent='문제 내기';}
  }
}

$('#analysisMode').addEventListener('change', (e) => {
  switchAnalysisMode(e.target.value);
});

$('#marketTabs').addEventListener('click', (e) => {
  const b = e.target.closest('.market-tab');
  if (b) void switchCategory(b.dataset.category);
});

$('#capFilterTabs').addEventListener('click', (e) => {
  const b = e.target.closest('.cap-filter');
  if (!b) return;
  const cap = Number(b.dataset.cap);
  if (!Number.isFinite(cap) || cap < 0) return;
  state.marketSizeMin[sizeFilterMode()] = cap;
  $$('.cap-filter').forEach((button) => button.classList.toggle('active', button === b));
  renderList();
});

let searchTimer;
$('#searchInput').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = e.target.value;
    renderList();
  }, 100);
});

$('#reloadBtn').addEventListener('click', () => {
  if (state.mode === 'quiz') {
    void newQuizQuestion(true);
    return;
  }
  void switchCategory(state.category, true);
});

$('#newQuizBtn')?.addEventListener('click', () => void newQuizQuestion(false));

$('#quizChoices')?.addEventListener('click', (e) => {
  const button = e.target.closest('[data-quiz-choice]');
  if (!button || state.quiz.answered || !state.quiz.question) return;
  const index = Number(button.dataset.quizChoice);
  if (!Number.isInteger(index) || index < 0 || index > 3) return;
  state.quiz.question.selectedIndex = index;
  renderQuizQuestion();
});

$('#quizSubmitBtn')?.addEventListener('click', () => {
  if (!state.quiz.question || state.quiz.answered) return;
  if (!Number.isInteger(state.quiz.question.selectedIndex)) return;
  state.quiz.answered = true;
  renderQuizQuestion();
});

document.addEventListener('click', (e) => {
  const score = e.target.closest('[data-score-detail]');
  if (score) {
    const stock = stockByTicker(score.dataset.scoreDetail);
    if (stock) void openScoreDetail(stock);
    return;
  }
  if (e.target.closest('[data-close-modal]')) closeModal();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#scoreModal').hidden) closeModal();
});

window.addEventListener('resize', () => {
  $$('.stock-card[data-hydrated="1"]').forEach(async (card) => {
    const stock = stockByTicker(card.dataset.ticker);
    if (!stock) return;
    const key = `${stock.category}:${stock.ticker}`;
    const detail = state.detailCache.get(key);
    if (detail) drawChart($('[data-chart-box]', card), detail);
  });
});

switchAnalysisMode('forecast');
void switchCategory('KR');
