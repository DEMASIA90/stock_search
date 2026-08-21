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
  mode: 'profile',
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
  const next = ['profile', 'candle', 'quiz'].includes(mode) ? mode : 'profile';
  state.mode = next;
  const views = {
    profile: $('#profileModeView'),
    candle: $('#candleModeView'),
    quiz: $('#quizModeView'),
  };
  Object.entries(views).forEach(([key, view]) => {
    if (view) view.hidden = key !== next;
  });
  const select = $('#analysisMode');
  if (select && select.value !== next) select.value = next;
  document.body.dataset.analysisMode = next;
  if (next === 'profile') {
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

function scoreValue(stock) {
  const n = Number(stock?.display_score ?? stock?.score);
  return Number.isFinite(n) ? Math.max(0, Math.min(10, n)) : 0;
}

function scoreText(stock) {
  const n = Number(stock?.display_score ?? stock?.score);
  return Number.isFinite(n) ? n.toFixed(2) : '—';
}

function heatClass(stock) {
  const n = scoreValue(stock);
  if (n >= 8) return 'heat-80';
  if (n >= 7) return 'heat-70';
  if (n >= 6) return 'heat-60';
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
  const matched = q
    ? capMatched.filter((s) => `${s.name} ${s.symbol} ${s.ticker} ${s.sector || ''}`.toLowerCase().includes(q))
    : capMatched.slice(0, DEFAULT_TOP_N);
  state.filtered = [...matched].sort((a, b) => {
    const ar = Number(a.rank), br = Number(b.rank);
    if (Number.isFinite(ar) && Number.isFinite(br)) return ar - br;
    return scoreValue(b) - scoreValue(a);
  });
  return { totalCapMatched: capMatched.length };
}

function marketSizeLabel(stock) {
  return stock?.market_size_basis === 'total_assets' ? '순자산' : '시총';
}

function numericOrNaN(v) {
  if (v == null || v === '') return Number.NaN;
  const n = Number(v);
  return Number.isFinite(n) ? n : Number.NaN;
}

function signedPct(v, digits=1) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function signalClass(kind, value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '';
  if (kind === 'percentB') return n <= 0.25 ? 'signal-hot' : n >= 0.8 ? 'signal-cold' : '';
  if (kind === 'rsi') return n >= 30 && n <= 45 ? 'signal-hot' : n >= 70 ? 'signal-cold' : '';
  if (kind === 'highGap') return n >= -1 ? 'signal-hot' : n >= -3 ? 'signal-near' : '';
  if (kind === 'volume') return n >= 1.5 ? 'signal-hot' : n >= 1.0 ? 'signal-near' : '';
  return '';
}

function tradeSignalGrade(kind, a, b) {
  const x = Number(a), y = Number(b);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return { text:'판단불가', cls:'unknown' };
  let points = 0;
  if (kind === 'pullback') {
    points += x <= 0.25 ? 2 : x <= 0.50 ? 1 : 0;
    points += (y >= 30 && y <= 45) ? 2 : (y >= 25 && y <= 55) ? 1 : 0;
  } else {
    points += x >= -1 ? 2 : x >= -3 ? 1 : 0;
    points += y >= 1.5 ? 2 : y >= 1.0 ? 1 : 0;
  }
  if (points >= 3) return { text:'좋음', cls:'good' };
  if (points >= 1) return { text:'보통', cls:'normal' };
  return { text:'나쁨', cls:'bad' };
}

function tradeSignalLines(stock) {
  const signal = stock?.trade_signals || {};
  const pb = signal.pullback || {};
  const bo = signal.breakout || {};
  const percentB = numericOrNaN(pb.percent_b);
  const rsi = numericOrNaN(pb.rsi14);
  const highGap = numericOrNaN(bo.high20_gap_pct);
  const volumeRatio = numericOrNaN(bo.volume_ratio_20d);
  const bbText = Number.isFinite(percentB) ? `${(percentB * 100).toFixed(0)}%` : '—';
  const rsiText = Number.isFinite(rsi) ? rsi.toFixed(1) : '—';
  const volumeText = Number.isFinite(volumeRatio) ? `${volumeRatio.toFixed(2)}x` : '—';
  const pbGrade = tradeSignalGrade('pullback', percentB, rsi);
  const boGrade = tradeSignalGrade('breakout', highGap, volumeRatio);
  return `<div class="trade-signal-box">
    <div class="trade-signal-row">
      <span class="signal-title pullback">눌림목</span><em class="signal-grade ${pbGrade.cls}">${pbGrade.text}</em>
      <span>BB %B <b class="${signalClass('percentB', percentB)}">${bbText}</b></span>
      <i>·</i>
      <span>RSI14 <b class="${signalClass('rsi', rsi)}">${rsiText}</b></span>
    </div>
    <div class="trade-signal-row">
      <span class="signal-title breakout">돌파</span><em class="signal-grade ${boGrade.cls}">${boGrade.text}</em>
      <span>20일고점 <b class="${signalClass('highGap', highGap)}">${signedPct(highGap)}</b></span>
      <i>·</i>
      <span>거래량 <b class="${signalClass('volume', volumeRatio)}">${volumeText}</b></span>
    </div>
  </div>`;
}

function backtestLine(stock) {
  const bt = stock?.backtest || {};
  if (!bt.available || bt.avg_60d == null) {
    const n = Number(bt.signals || 0);
    return `통합 백테스트: <b>동일 점수대 60일 평균 —</b> <small>· 비중첩 표본 ${Number.isFinite(n) ? n : 0}건 · 순위 미반영</small>`;
  }
  const klass = Number(bt.avg_60d) > 0 ? 'up' : Number(bt.avg_60d) < 0 ? 'down' : 'flat';
  const used = Number(bt.signals_used || 0);
  const stocks = Number(bt.stock_count || 0);
  const band = Number(bt.score_band_half_width);
  const bandText = Number.isFinite(band) ? `±${band.toFixed(1)}점` : '유사 점수대';
  return `통합 백테스트: <b>${bandText} · 60일 평균 <span class="${klass}">${ratioPct(bt.avg_60d)}</span></b> <small>· 비중첩 ${used}건 / ${stocks}종목 · 순위 미반영</small>`;
}

function stockCard(stock) {
  const ticker = stock.symbol || stock.ticker || '—';
  const sector = stock.sector || '—';
  const key = `${stock.category}:${stock.ticker}`;
  return `<article class="stock-card ${heatClass(stock)}" data-stock-key="${escapeHtml(key)}" data-ticker="${escapeHtml(stock.ticker)}">
    <section class="stock-info-pane">
      <div class="stock-headline-row">
        <h2>${escapeHtml(stock.name)} <span>(${escapeHtml(ticker)})</span></h2>
        <button class="score-pill ${heatClass(stock)}" type="button" data-score-detail="${escapeHtml(stock.ticker)}" aria-label="점수 상세 보기">${scoreText(stock)}</button>
      </div>

      <div class="stock-meta-line">
        <span class="sector-name">${escapeHtml(sector)}</span>
        <i>·</i>
        <span class="market-stat">${marketSizeLabel(stock)} ${marketSize(stock.market_size_krw)}</span>
        <i>·</i>
        <span class="market-stat">현재가 ${money(stock.close, stock.currency)} ${changeText(stock.day_change_pct)}</span>
      </div>

      ${tradeSignalLines(stock)}

      <div class="news-one-line" data-news-line>
        <span class="line-label">NEWS</span>
        <span class="line-placeholder">최신 뉴스 불러오는 중…</span>
      </div>

      <div class="backtest-one-line">${backtestLine(stock)}</div>
    </section>

    <section class="stock-chart-pane">
      <div class="inline-chart" data-chart-box>
        <div class="chart-loading">3M CHART</div>
      </div>
    </section>
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
  $('#scanStatus').textContent = `${data.scan_mode === 'QUICK' ? '장중 QUICK' : '종가 확정 FULL'} · 현재점수순`;
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

function drawChart(el, detail) {
  if (!el?.isConnected) return;
  const data = chartRows(detail);
  if (data.length < 20) {
    el.innerHTML = '<div class="chart-empty">차트 데이터 부족</div>';
    return;
  }

  const profiles = Array.isArray(detail?.chart?.profiles) ? detail.chart.profiles : [];
  const profileVals = profiles.map((p) => Number(p.center)).filter(Number.isFinite);
  const all = data.flatMap((r) => [r.low, r.high, r.upper, r.lower]).filter(Number.isFinite).concat(profileVals);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (!(hi > lo)) { hi = lo * 1.01; lo = lo * 0.99; }
  const padRange = (hi - lo) * 0.07;
  lo -= padRange; hi += padRange;

  const W = 700, H = 250;
  const pad = { l:12, r:55, t:16, b:25 };
  const plotW = W - pad.l - pad.r;
  const step = plotW / Math.max(1, data.length);
  const X = (i) => pad.l + (i + .5) * step;
  const Y = (v) => pad.t + (hi - v) * (H - pad.t - pad.b) / (hi - lo);
  const path = (key) => data.map((d, i) => Number.isFinite(d[key]) ? `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(d[key]).toFixed(1)}` : '').filter(Boolean).join(' ');
  const poly = data.map((d, i) => `${X(i)},${Y(d.upper)}`).join(' ') + ' ' +
    [...data].reverse().map((d, ri) => { const i = data.length - 1 - ri; return `${X(i)},${Y(d.lower)}`; }).join(' ');

  let grid = '';
  for (let i = 0; i < 4; i++) {
    const y = pad.t + i * (H - pad.t - pad.b) / 3;
    const v = hi - i * (hi - lo) / 3;
    const label = detail.currency === 'KRW'
      ? Math.round(v).toLocaleString('ko-KR')
      : `$${v.toFixed(v >= 100 ? 0 : 1)}`;
    grid += `<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" class="chart-grid"/><text x="${W-pad.r+6}" y="${y+4}" class="price-axis">${label}</text>`;
  }

  const profileClasses = ['profile20','profile40','profile60','profile80','profile100','profile150','profile200','profile300','profile400'];
  let profileSvg = '';
  profiles.forEach((p, idx) => {
    const center = Number(p.center);
    if (!Number.isFinite(center)) return;
    const y = Y(center);
    const labelY = y + (idx - (profiles.length - 1) / 2) * 7;
    profileSvg += `<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" class="profile-line ${profileClasses[idx] || ''}"/>
      <text x="${W-pad.r-4}" y="${labelY}" text-anchor="end" class="profile-label ${profileClasses[idx] || ''}">${escapeHtml(p.days)}D</text>`;
  });

  const bodyW = Math.max(2.2, Math.min(7.2, step * .56));
  let candles = '';
  data.forEach((r, i) => {
    const x = X(i), yo = Y(r.open), yc = Y(r.close), yh = Y(r.high), yl = Y(r.low);
    const up = r.close >= r.open;
    const klass = up ? 'chart-candle-up' : 'chart-candle-down';
    const top = Math.min(yo, yc), bh = Math.max(1.4, Math.abs(yc - yo));
    candles += `<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="chart-candle-wick ${klass}"/><rect x="${x-bodyW/2}" y="${top}" width="${bodyW}" height="${bh}" class="${klass}" rx=".45"/>`;
  });

  const dates = [0, Math.floor((data.length - 1)/2), data.length - 1].map((i) => ({ i, label:data[i]?.date?.slice(5) || '' }));
  const dateSvg = dates.map((d) => `<text x="${X(d.i)}" y="${H-6}" text-anchor="${d.i===0?'start':d.i===data.length-1?'end':'middle'}" class="date-axis">${escapeHtml(d.label)}</text>`).join('');

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="3개월 캔들 차트">
    ${grid}
    <polygon points="${poly}" class="bb-fill"/>
    <path d="${path('upper')}" class="bb-edge"/>
    <path d="${path('lower')}" class="bb-edge"/>
    <path d="${path('mid')}" class="bb-mid"/>
    ${profileSvg}
    ${candles}
    ${dateSvg}
  </svg>
  <div class="chart-legend"><span>CANDLE</span><span>BB</span><span>10-ZONE PROFILE</span></div>`;
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
  const a = articles?.[0];
  line.innerHTML = a?.title && a?.link
    ? `<span class="line-label">NEWS</span><a href="${escapeHtml(a.link)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(a.title)}">${escapeHtml(a.title)}</a>`
    : `<span class="line-label">NEWS</span><a href="${direct}" target="_blank" rel="noopener noreferrer">최신 뉴스 보기 ↗</a>`;
}

async function hydrateCard(card) {
  if (!card?.isConnected || card.dataset.hydrated === '1') return;
  card.dataset.hydrated = '1';
  const ticker = card.dataset.ticker;
  const stock = stockByTicker(ticker);
  if (!stock) return;

  void loadHeadline(stock, card);
  try {
    const detail = await ensureDetail(stock);
    if (!card?.isConnected) return;
    drawChart($('[data-chart-box]', card), detail);
  } catch (err) {
    console.error('chart detail', err);
    const box = $('[data-chart-box]', card);
    if (box) box.innerHTML = '<div class="chart-empty">차트를 불러오지 못했습니다.</div>';
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
  const modal = $('#scoreModal');
  const body = $('#scoreModalBody');
  $('#scoreModalTitle').textContent = `${stock.name} (${stock.symbol || stock.ticker}) · ${scoreText(stock)}점`;
  modal.hidden = false;
  document.body.classList.add('modal-open');
  body.innerHTML = '<div class="modal-loading">점수 상세를 불러오는 중…</div>';

  let detail = stock;
  try { detail = await ensureDetail(stock); } catch (_) {}
  if (modal.hidden) return;
  const s = detail.scores || stock.scores || {};
  const profiles = detail.metrics?.profiles || {};
  const bt = detail.backtest || stock.backtest || {};
  const groupLabels = {
    short: ['단기 매물대', [20,40,60]],
    medium: ['중기 매물대', [80,100,150]],
    long: ['장기 매물대', [200,300,400]],
  };
  const rows = [
    ['볼린저 하단 근접', Number(s.bollinger || 0), 1, detail.metrics?.percent_b == null ? '' : `%B ${Number(detail.metrics.percent_b).toFixed(3)}`],
    ...Object.entries(groupLabels).map(([key, [label, days]]) => [
      label,
      Number(s[`profile_${key}`] || 0),
      3,
      days.map((d) => `${d}D ${profileText(profiles[String(d)], true)}`).join(' · '),
    ]),
  ];
  const btText = bt.avg_60d == null
    ? `통합 비중첩 표본 ${Number(bt.signals || 0)}건 · 계산 불가 · 순위에는 사용하지 않음`
    : `통합 60일 평균 ${ratioPct(bt.avg_60d)} · 비중첩 ${Number(bt.signals_used || 0)}건 · ${Number(bt.stock_count || 0)}종목 · 순위에는 사용하지 않음`;

  body.innerHTML = `<div class="score-total"><span>CURRENT SETUP SCORE</span><b>${scoreText(stock)}</b><small>/ 10</small></div>
    <div class="score-rows">${rows.map(([label, value, max, sub]) => {
      return `<div class="score-row">
        <div><b>${escapeHtml(label)}</b>${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</div>
        <strong class="${value >= max - 1e-9 ? 'full' : ''}">${Number(value).toFixed(3)}<small>/${max}</small></strong>
      </div>`;
    }).join('')}</div>
    <div class="backtest-one-line" style="margin-top:14px">${escapeHtml(btText)}</div>`;
}

function profileText(p, compact=false) {
  if (!p?.available) return '매물대 계산 불가';
  const share = Number(p.share);
  const relative = Number(p.relative_to_peak);
  const idx = Number(p.index);
  const zone = Number.isFinite(idx) ? `${idx + 1}/10구간` : '—';
  if (compact) {
    return `${Number.isFinite(relative) ? (relative * 100).toFixed(0) : '—'}%peak`;
  }
  return `${zone} · 거래량 비중 ${Number.isFinite(share) ? (share * 100).toFixed(1) : '—'}% · 최대 매물대 대비 ${Number.isFinite(relative) ? (relative * 100).toFixed(0) : '—'}%`;
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

function quizBridgePattern(source, target, hiddenPart, volatilityScale=1) {
  if (!source?.length || !target?.length || source.length !== target.length) return null;
  if (source.some(v => !Number.isFinite(v) || v <= 0)) return null;
  const logs = source.map(Math.log);
  const srcVol=quizDailyVol(source);
  const targetVol=quizDailyVol(target);
  const scale=srcVol>1e-9 ? Math.max(.25,Math.min(1.8,targetVol/srcVol*volatilityScale)) : 1;

  if(hiddenPart===0){
    // First third: only the right edge is observable. Preserve a plausible real
    // total move so the hidden starting level is not leaked by every option.
    const anchor=Math.log(target[target.length-1]);
    const srcAnchor=logs[logs.length-1];
    return logs.map(v=>Math.exp(anchor+(v-srcAnchor)*scale));
  }
  if(hiddenPart===2){
    // Last third: only the left edge is observable. Let the possible final price
    // vary with the real donor pattern rather than leaking the true endpoint.
    const anchor=Math.log(target[0]);
    const srcAnchor=logs[0];
    return logs.map(v=>Math.exp(anchor+(v-srcAnchor)*scale));
  }

  // Middle third: both adjacent visible sections constrain the endpoints. Remove
  // the donor trend and bridge its residual shape between the real endpoints.
  const srcA=logs[0], srcB=logs[logs.length-1];
  const targetA=Math.log(target[0]), targetB=Math.log(target[target.length-1]);
  const residual=logs.map((v,i)=>v-(srcA+(srcB-srcA)*i/(logs.length-1)));
  return residual.map((r,i)=>Math.exp(targetA+(targetB-targetA)*i/(logs.length-1)+r*scale));
}


async function quizRealSegment(pool, length=QUIZ_HIDDEN_DAYS) {
  for (let attempt=0; attempt<30; attempt++) {
    const entry = quizPickEntry(pool);
    if (!entry) continue;
    let stock;
    try { stock = await ensureQuizStock(entry); } catch (_) { continue; }
    const n = stock?.c?.length || 0;
    if (n < length + 1) continue;
    const start = quizRandomInt(0, n-length);
    const rows = quizRows(stock, start, length);
    const closes = rows.map(r => r.close);
    if (rows.length === length && closes.every(v => Number.isFinite(v) && v > 0)) return { rows, closes };
  }
  return null;
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
  for (let attempt=0; attempt<80; attempt++) {
    const entry = quizPickEntry(pool);
    if (!entry) continue;
    let stock;
    try { stock = await ensureQuizStock(entry); } catch (_) { continue; }
    const n = stock?.c?.length || 0;
    if (n < QUIZ_WINDOW_DAYS + 20) continue;
    const start = quizRandomInt(19, n - QUIZ_WINDOW_DAYS);
    const rows = quizRows(stock, start);
    if (rows.length !== QUIZ_WINDOW_DAYS) continue;
    const hiddenPart = quizRandomInt(0, 2);
    const hiddenStart = hiddenPart * QUIZ_HIDDEN_DAYS;
    const hiddenEnd = hiddenStart + QUIZ_HIDDEN_DAYS;
    const correctSeries = rows.slice(hiddenStart, hiddenEnd).map(r => r.close);
    if (correctSeries.some(v => !Number.isFinite(v) || v <= 0)) continue;

    const distractors = [];
    for (let dAttempt=0; dAttempt<120 && distractors.length<3; dAttempt++) {
      const donor = await quizRealSegment(pool);
      if (!donor) continue;
      const bridged = quizBridgePattern(donor.closes, correctSeries, hiddenPart, 0.75 + Math.random()*0.5);
      if (!bridged) continue;
      const distToCorrect = quizShapeDistance(bridged, correctSeries);
      if (!(distToCorrect > 0.55)) continue;
      if (distractors.some(x => quizShapeDistance(x.closes, bridged) < 0.38)) continue;
      distractors.push({ closes:bridged, candles:quizTransformCandles(donor.rows, bridged) });
    }
    if (distractors.length < 3) continue;

    const correctIndex = quizRandomInt(0, 3);
    const correctCandles = rows.slice(hiddenStart, hiddenEnd).map(r => ({open:r.open, high:r.high, low:r.low, close:r.close}));
    const correctOption = { closes:correctSeries, candles:correctCandles };
    const options = [];
    let di = 0;
    for (let i=0;i<4;i++) options.push(i === correctIndex ? correctOption : distractors[di++]);
    return {
      entry,
      stock,
      rows,
      bb:quizBollinger(stock, start),
      profile:quizVolumeProfile(rows),
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
  const selected = answered && selectedIndex === index;
  const klass = answered ? (index===correctIndex ? ' correct' : selected ? ' wrong' : '') : '';
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
  const {rows,bb,profile,hiddenStart,hiddenEnd} = question;
  const W=1000,H=430,pad={l:18,r:72,t:22,b:32};
  const vals = rows.flatMap((r,i)=>[r.low,r.high,bb[i]?.lower,bb[i]?.upper]).filter(Number.isFinite);
  let lo=Math.min(...vals), hi=Math.max(...vals);
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
    const current=rows[rows.length-1].close;
    const currentBin=Math.max(0,Math.min(9,Math.floor((current-profile.pmin)/(profile.pmax-profile.pmin)*10)));
    for(let b=0;b<10;b++){
      const y1=Y(profile.edges[b+1]), y2=Y(profile.edges[b]);
      const barH=Math.max(1,Math.abs(y2-y1)-1);
      const bw=maxWidth*(profile.values[b]/profile.max);
      profileSvg+=`<rect x="${W-pad.r-bw}" y="${Math.min(y1,y2)+.5}" width="${bw}" height="${barH}" class="quiz-profile-bar${b===currentBin?' current':''}"/>`;
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

  const dateIndices=[0,44,89];
  const dates=dateIndices.map((i)=>{
    const label=answered?(rows[i]?.date?.slice(2)||''):`D${i+1}`;
    const anchor=i===0?'start':i===89?'end':'middle';
    return `<text x="${i===0?pad.l:i===89?W-pad.r:X(i)}" y="${H-9}" text-anchor="${anchor}" class="quiz-date-axis">${escapeHtml(label)}</text>`;
  }).join('');

  const hiddenOverlay = answered
    ? `<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-reveal-zone"/>`
    : `<rect x="${hiddenX1}" y="${pad.t}" width="${hiddenX2-hiddenX1}" height="${plotH}" class="quiz-hidden-block"/><text x="${(hiddenX1+hiddenX2)/2}" y="${pad.t+plotH/2}" text-anchor="middle" class="quiz-hidden-label">HIDDEN</text>`;

  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${grid}
    <path d="${bbPath('upper')}" class="quiz-bb-line"/><path d="${bbPath('lower')}" class="quiz-bb-line"/><path d="${bbPath('mid')}" class="quiz-bb-mid"/>
    ${candles}${hiddenOverlay}${profileSvg}${dates}
  </svg><div class="quiz-chart-legend"><span>CANDLE</span><span>BB 20,2</span><span>90D 10-ZONE VOLUME PROFILE</span></div>`;
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
  const labels=['앞 1/3','중간 1/3','뒤 1/3'];
  $('#quizSegmentLabel').textContent=`${labels[q.hiddenPart]} · 30거래일 가림`;
  const instruction=$('#quizInstructionSub');
  if(instruction){
    instruction.textContent=q.hiddenPart===1
      ? '중간 구간은 양쪽 경계가격을 맞춘 실제 시장 패턴 기반 보기입니다.'
      : '보이는 한쪽 경계와 자연스럽게 연결한 실제 시장 패턴 기반 보기입니다.';
  }
  renderQuizMainChart(q,state.quiz.answered);
  $('#quizChoices').innerHTML=q.options.map((option,i)=>renderQuizOption(option,i,q.correctIndex,q.selectedIndex,state.quiz.answered)).join('');
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
  $('#quizStatus').textContent='100조 이상 종목의 과거 차트를 불러오는 중입니다.';
  $('#quizGame').hidden=true;
  try{
    const pool=await ensureQuizPool(forcePool);
    if(!pool.length) throw new Error('quiz pool empty');
    const q=await buildQuizQuestion(pool);
    if(!q) throw new Error('could not build plausible distractors');
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

switchAnalysisMode('profile');
void switchCategory('KR');
