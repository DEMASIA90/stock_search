const $ = (q) => document.querySelector(q);
const $$ = (q) => [...document.querySelectorAll(q)];

const CATEGORY = {
  KR: { short: '국장', market: 'KR', dir: 'kr' },
  KR_ETF: { short: '국장 ETF', market: 'KR ETF', dir: 'kr-etf' },
  US: { short: '미장', market: 'US', dir: 'us' },
  US_ETF: { short: '미장 ETF', market: 'US ETF', dir: 'us-etf' },
};

const MODE = {
  cheap: { label: '싼게 좋아', emoji: '🧺', maxRaw: 5.0 },
  rising: { label: '오르는게 좋아', emoji: '🚀', maxRaw: 4.5 },
};

const DATA_BASE = location.hostname.endsWith('github.io')
  ? 'https://morninginv.web.app'
  : '.';

const NEWS_PROXY_URL = String(window.BADAK_NEWS_PROXY_URL || '').trim();
const NEWS_CACHE_MS = 5 * 60 * 1000;
const DEFAULT_TOP_N = 100;

const state = {
  category: 'KR',
  data: { KR: null, KR_ETF: null, US: null, US_ETF: null },
  query: '',
  lists: { cheap: [], rising: [] },
  detailCache: new Map(),
  newsCache: new Map(),
  openPanel: null,
  openKey: '',
  selected: null,
  selectedMode: 'cheap',
  chartDays: 120,
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c]));
}

function dataUrl(path, force=false) {
  const clean = String(path).replace(/^\.\//, '').replace(/^\//, '');
  const base = DATA_BASE === '.' ? '.' : DATA_BASE.replace(/\/$/, '');
  const url = `${base}/${clean}`;
  return force ? `${url}?ts=${Date.now()}` : url;
}

function currentData() { return state.data[state.category]; }

function modeView(stock, mode) {
  if (!stock) return { eligible:false, rank:null, score:0, backtest:{} };
  if (mode === 'rising') {
    return stock.rising || { eligible:false, rank:null, score:0, backtest:{} };
  }
  return {
    eligible: true,
    rank: stock.rank,
    score: stock.score,
    display_score: stock.display_score,
    backtest: stock.backtest || {},
  };
}

function displayScore(raw, mode) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return '—';
  const maxRaw = MODE[mode]?.maxRaw || 1;
  return Math.min(100, Math.max(0, n / maxRaw * 100)).toFixed(1);
}

function scoreNumber(raw, mode) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 0;
  return Math.min(100, Math.max(0, n / (MODE[mode]?.maxRaw || 1) * 100));
}

function heatClass(raw, mode) {
  const n = scoreNumber(raw, mode);
  if (n >= 80) return 'heat-80';
  if (n >= 70) return 'heat-70';
  if (n >= 60) return 'heat-60';
  return '';
}

function money(v, currency) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  return currency === 'KRW'
    ? `₩${Math.round(Number(v)).toLocaleString('ko-KR')}`
    : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function marketSize(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  if (n >= 1e12) return `${(n / 1e12).toFixed(n >= 100e12 ? 0 : 1)}조`;
  if (n >= 1e8) return `${(n / 1e8).toFixed(0)}억`;
  return Math.round(n).toLocaleString('ko-KR');
}

function ratioPct(v, digits=1) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v) * 100;
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function winPct(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  return `${(Number(v) * 100).toFixed(0)}%`;
}

function changeClass(v) {
  return Number(v) > 0 ? 'up' : Number(v) < 0 ? 'down' : '';
}

async function ensureData(category, force=false) {
  if (state.data[category] && !force) return state.data[category];
  const response = await fetch(
    dataUrl(`data/${CATEGORY[category].dir}/summary.json`, force),
    { cache: force ? 'no-store' : 'default' }
  );
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

function modeSort(mode) {
  return (a, b) => {
    const av = modeView(a, mode), bv = modeView(b, mode);
    const ar = Number(av.rank), br = Number(bv.rank);
    if (Number.isFinite(ar) && Number.isFinite(br)) return ar - br;
    if (Number.isFinite(ar)) return -1;
    if (Number.isFinite(br)) return 1;
    return Number(bv.score || 0) - Number(av.score || 0);
  };
}

function filterLists() {
  const items = currentData()?.items || [];
  const q = state.query.trim().toLowerCase();
  const matches = q
    ? items.filter((s) => `${s.name} ${s.symbol} ${s.ticker} ${s.exchange}`.toLowerCase().includes(q))
    : items;

  state.lists.cheap = [...matches]
    .sort(modeSort('cheap'))
    .slice(0, q ? matches.length : DEFAULT_TOP_N);

  const rising = matches.filter((s) => modeView(s, 'rising').eligible !== false);
  state.lists.rising = [...rising]
    .sort(modeSort('rising'))
    .slice(0, q ? rising.length : DEFAULT_TOP_N);
}

function stockCard(stock, mode) {
  const mv = modeView(stock, mode);
  const score = displayScore(mv.score, mode);
  const market = CATEGORY[stock.category]?.market || stock.category || '—';
  const ticker = stock.symbol || stock.ticker || '—';
  const size = marketSize(stock.market_size_krw ?? stock.metrics?.market_size_krw);
  const key = `${mode}:${stock.ticker}`;

  return `<article class="compact-stock-card ${heatClass(mv.score, mode)}" data-card-key="${escapeHtml(key)}">
    <div class="compact-card-head">
      <strong class="compact-stock-name">${escapeHtml(stock.name)}</strong>
      <span class="compact-score ${heatClass(mv.score, mode)}">${score}</span>
    </div>

    <div class="compact-stock-fields">
      <div><span>TICKER</span><b>${escapeHtml(ticker)}</b></div>
      <div><span>MARKET</span><b>${escapeHtml(market)}</b></div>
      <div><span>시총</span><b>${size}</b></div>
      <div><span>현재가</span><b>${money(stock.close, stock.currency)}</b></div>
    </div>

    <div class="compact-actions">
      <button type="button" data-stock-action="chart" data-mode="${mode}" data-ticker="${escapeHtml(stock.ticker)}">차트</button>
      <button type="button" data-stock-action="news" data-mode="${mode}" data-ticker="${escapeHtml(stock.ticker)}">뉴스</button>
      <button type="button" data-stock-action="backtest" data-mode="${mode}" data-ticker="${escapeHtml(stock.ticker)}">백테스팅</button>
    </div>
    <div class="compact-action-slot"></div>
  </article>`;
}

function renderLists() {
  filterLists();

  const cheap = state.lists.cheap;
  const rising = state.lists.rising;
  $('#cheapList').innerHTML = cheap.length
    ? cheap.map((s) => stockCard(s, 'cheap')).join('')
    : '<div class="column-empty">검색 결과 없음</div>';
  $('#risingList').innerHTML = rising.length
    ? rising.map((s) => stockCard(s, 'rising')).join('')
    : '<div class="column-empty">검색 결과 없음</div>';

  $('#cheapCount').textContent = `${cheap.length.toLocaleString()}개`;
  $('#risingCount').textContent = `${rising.length.toLocaleString()}개`;
  $('#resultCount').textContent = state.query.trim()
    ? `싼게 ${cheap.length.toLocaleString()} · 오르는게 ${rising.length.toLocaleString()}`
    : '각 TOP100';
}

function renderMeta() {
  const data = currentData();
  $('#categoryTitle').textContent = `${CATEGORY[state.category].short} TOP100`;
  if (!data) {
    $('#updated').textContent = '—';
    $('#marketDate').textContent = '—';
    $('#coverage').textContent = '—';
    return;
  }

  const dt = data.generated_at_utc ? new Date(data.generated_at_utc) : null;
  $('#updated').textContent = dt && !Number.isNaN(dt.getTime())
    ? dt.toLocaleString('ko-KR', { timeZone:'Asia/Seoul', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' })
    : '—';

  const scanStatus = data.scan_mode === 'QUICK' ? '장중 QUICK' : '종가 확정';
  $('#marketDate').textContent = data.market_date ? `${data.market_date} · ${scanStatus}` : scanStatus;
  $('#marketDate').classList.toggle('intraday', data.scan_mode === 'QUICK');
  $('#coverage').textContent = `전체 ${Number(data.universe_count || 0).toLocaleString()}종목`;
}

function closeActionPanel() {
  if (state.openPanel?.isConnected) state.openPanel.innerHTML = '';
  $$('.compact-actions button.active').forEach((b) => b.classList.remove('active'));
  state.openPanel = null;
  state.openKey = '';
  state.selected = null;
}

function actionPanelShell(action, stock, mode) {
  if (action === 'chart') {
    return `<section class="mini-panel mini-chart-panel">
      <div class="mini-panel-head"><b>${escapeHtml(stock.name)} · 차트</b><button class="mini-close" aria-label="닫기">×</button></div>
      <div class="mini-period-tabs">
        <button data-days="20">1M</button>
        <button data-days="60">3M</button>
        <button class="active" data-days="120">6M</button>
      </div>
      <div class="mini-chart"><div class="mini-loading">차트 로딩 중…</div></div>
    </section>`;
  }

  if (action === 'news') {
    return `<section class="mini-panel mini-news-panel">
      <div class="mini-panel-head"><b>${escapeHtml(stock.name)} · 뉴스</b><button class="mini-close" aria-label="닫기">×</button></div>
      <div class="news-head mini-news-head"><span></span><a class="news-all-link" href="#" target="_blank" rel="noopener noreferrer">전체 기사 ↗</a></div>
      <div class="news-list"><div class="news-loading"><span class="news-spinner"></span> 최신 기사 불러오는 중…</div></div>
    </section>`;
  }

  return `<section class="mini-panel mini-backtest-panel">
    <div class="mini-panel-head"><b>${escapeHtml(stock.name)} · ${escapeHtml(MODE[mode].label)} 백테스팅</b><button class="mini-close" aria-label="닫기">×</button></div>
    <div class="mini-loading backtest-loading">백테스트 불러오는 중…</div>
    <div class="backtest-summary"></div>
    <div class="backtest-table-wrap" hidden>
      <table class="backtest-table">
        <thead><tr><th>신호일</th><th>점수</th><th>5D</th><th>10D</th><th>20D</th><th>MFE20</th><th>MAE20</th></tr></thead>
        <tbody class="backtest-body"></tbody>
      </table>
    </div>
  </section>`;
}

async function openAction(button) {
  const action = button.dataset.stockAction;
  const mode = button.dataset.mode;
  const ticker = button.dataset.ticker;
  const stock = (currentData()?.items || []).find((s) => s.ticker === ticker);
  if (!stock || !MODE[mode]) return;

  const key = `${mode}:${ticker}:${action}`;
  if (state.openKey === key && state.openPanel?.isConnected) {
    closeActionPanel();
    return;
  }

  closeActionPanel();
  const card = button.closest('.compact-stock-card');
  const slot = card?.querySelector('.compact-action-slot');
  if (!slot) return;

  button.classList.add('active');
  state.openKey = key;
  state.openPanel = slot;
  state.selectedMode = mode;
  state.chartDays = 120;
  slot.innerHTML = actionPanelShell(action, stock, mode);
  slot.scrollIntoView({ behavior:'smooth', block:'nearest' });

  if (action === 'news') {
    await loadLatestNews(stock, slot);
    return;
  }

  try {
    const detail = await ensureDetail(stock);
    if (state.openKey !== key || !slot.isConnected) return;
    state.selected = detail;

    if (action === 'chart') {
      drawMiniChart(slot, detail);
    } else if (action === 'backtest') {
      renderMiniBacktest(slot, detail, mode);
    }
  } catch (err) {
    console.error(err);
    if (state.openKey === key && slot.isConnected) {
      slot.querySelector('.mini-loading')?.replaceChildren(document.createTextNode('데이터를 불러오지 못했습니다.'));
    }
  }
}

function chartRows(stock) {
  const c = stock.chart || {}, d = c.d || [];
  return d.map((date, i) => ({
    date,
    close: c.c?.[i],
    mid: c.m?.[i],
    upper: c.u?.[i],
    lower: c.l?.[i],
    ma60: c.a60?.[i],
  }));
}

function drawMiniChart(root, stock) {
  const el = root.querySelector('.mini-chart');
  if (!el) return;
  drawChart(el, chartRows(stock).slice(-state.chartDays), stock.currency);
}

function drawChart(el, data, currency) {
  data = data.filter((d) => [d.close, d.mid, d.upper, d.lower].every(Number.isFinite));
  if (!data.length) {
    el.innerHTML = '<div class="mini-loading">차트 데이터 없음</div>';
    return;
  }

  const W = Math.max(320, el.clientWidth || 520);
  const H = Math.max(220, el.clientHeight || 260);
  const pad = { l:8, r:56, t:14, b:18 };
  const vals = data.flatMap((d) => [d.close, d.upper, d.lower]).filter(Number.isFinite);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = Math.max(max - min, Math.abs(max) * .02, 1);
  const lo = min - span * .08, hi = max + span * .08;
  const X = (i) => pad.l + i / Math.max(1, data.length - 1) * (W - pad.l - pad.r);
  const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const path = (key) => data.map((d, i) => Number.isFinite(d[key]) ? `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(d[key]).toFixed(1)}` : '').filter(Boolean).join(' ');
  const poly = data.map((d, i) => `${X(i)},${Y(d.upper)}`).join(' ') + ' ' +
    [...data].reverse().map((d, ri) => { const i = data.length - 1 - ri; return `${X(i)},${Y(d.lower)}`; }).join(' ');

  let grid = '';
  for (let i = 0; i < 4; i++) {
    const y = pad.t + i * (H - pad.t - pad.b) / 3;
    const v = hi - i * (hi - lo) / 3;
    const label = currency === 'KRW'
      ? Math.round(v).toLocaleString('ko-KR')
      : `$${v.toFixed(v >= 100 ? 0 : 1)}`;
    grid += `<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" stroke="#202631"/><text x="${W-pad.r+5}" y="${y+4}" fill="#718096" font-size="8">${label}</text>`;
  }

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs><linearGradient id="miniBandFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7faaff" stop-opacity=".14"/><stop offset="1" stop-color="#7faaff" stop-opacity=".02"/></linearGradient></defs>
    ${grid}
    <polygon points="${poly}" fill="url(#miniBandFill)"/>
    <path d="${path('upper')}" fill="none" stroke="#5579b8" stroke-width="1"/>
    <path d="${path('lower')}" fill="none" stroke="#5579b8" stroke-width="1"/>
    <path d="${path('mid')}" fill="none" stroke="#efc46a" stroke-width="1.1"/>
    <path d="${path('ma60')}" fill="none" stroke="#b899ff" stroke-width="1.3"/>
    <path d="${path('close')}" fill="none" stroke="#78f2b6" stroke-width="2"/>
  </svg>`;
}

function backtestMetric(label, value, sub='') {
  return `<div class="bt-metric"><span>${label}</span><b>${value}</b>${sub ? `<small>${sub}</small>` : ''}</div>`;
}

function renderMiniBacktest(root, stock, mode) {
  const mv = modeView(stock, mode);
  const bt = mv.backtest || {};
  const loading = root.querySelector('.backtest-loading');
  const summary = root.querySelector('.backtest-summary');
  const tableWrap = root.querySelector('.backtest-table-wrap');
  const body = root.querySelector('.backtest-body');
  if (loading) loading.remove();

  if (!bt.available) {
    summary.innerHTML = backtestMetric('Signals', '0', '데이터 부족');
    tableWrap.hidden = true;
    return;
  }

  summary.innerHTML = [
    backtestMetric('Signals', Number(bt.signals || 0).toLocaleString()),
    backtestMetric('5D Avg', ratioPct(bt.avg_5d), `Win ${winPct(bt.win_5d)}`),
    backtestMetric('10D Avg', ratioPct(bt.avg_10d), `Win ${winPct(bt.win_10d)}`),
    backtestMetric('20D Avg', ratioPct(bt.avg_20d), `Win ${winPct(bt.win_20d)}`),
    backtestMetric('20D Median', ratioPct(bt.median_20d)),
    backtestMetric('MFE / MAE', `${ratioPct(bt.avg_mfe_20d)} / ${ratioPct(bt.avg_mae_20d)}`),
  ].join('');

  const trades = bt.trades || [];
  body.innerHTML = trades.length
    ? trades.map((t) => `<tr>
        <td>${escapeHtml(t.signal_date || '—')}</td>
        <td>${t.score == null ? '—' : displayScore(t.score, mode)}</td>
        <td class="${changeClass((t.ret_5d || 0) * 100)}">${ratioPct(t.ret_5d)}</td>
        <td class="${changeClass((t.ret_10d || 0) * 100)}">${ratioPct(t.ret_10d)}</td>
        <td class="${changeClass((t.ret_20d || 0) * 100)}">${ratioPct(t.ret_20d)}</td>
        <td class="up">${ratioPct(t.mfe_20d)}</td>
        <td class="down">${ratioPct(t.mae_20d)}</td>
      </tr>`).join('')
    : '<tr><td colspan="7" class="bt-empty">최근 독립 신호 없음</td></tr>';
  tableWrap.hidden = false;
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
  return ['KR', 'KR_ETF'].includes(stock?.category)
    ? `https://news.google.com/search?q=${q}&hl=ko&gl=KR&ceid=KR:ko`
    : `https://news.google.com/search?q=${q}&hl=en-US&gl=US&ceid=US:en`;
}

function jsonp(url, params={}, timeoutMs=12000) {
  return new Promise((resolve, reject) => {
    const callback = `__badakNews_${Date.now()}_${Math.random().toString(36).slice(2)}`;
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

function newsTimeText(value) {
  if (!value) return '';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return '';
  const sec = Math.max(0, (Date.now() - dt.getTime()) / 1000);
  if (sec < 60) return '방금 전';
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}시간 전`;
  if (sec < 604800) return `${Math.floor(sec / 86400)}일 전`;
  return dt.toLocaleDateString('ko-KR', { month:'2-digit', day:'2-digit' });
}

function renderNewsList(root, articles) {
  if (!root?.isConnected) return;
  const list = root.querySelector('.news-list');
  if (!list) return;
  if (!Array.isArray(articles) || !articles.length) {
    list.innerHTML = '<div class="news-empty">최근 기사를 찾지 못했습니다.</div>';
    return;
  }

  list.innerHTML = articles.slice(0, 5).map((a, i) => {
    const meta = [escapeHtml(a.source || 'News'), escapeHtml(newsTimeText(a.published_at))].filter(Boolean).join(' · ');
    return `<a class="news-item" href="${escapeHtml(a.link)}" target="_blank" rel="noopener noreferrer">
      <span class="news-index">${String(i + 1).padStart(2, '0')}</span>
      <span class="news-copy"><strong>${escapeHtml(a.title)}</strong><small>${meta}</small></span>
      <span class="news-open">↗</span>
    </a>`;
  }).join('');
}

async function loadLatestNews(stock, root) {
  if (!root?.isConnected) return;
  const allLink = root.querySelector('.news-all-link');
  if (allLink) allLink.href = newsSearchUrl(stock);
  const list = root.querySelector('.news-list');
  if (!list) return;

  if (!NEWS_PROXY_URL || NEWS_PROXY_URL.includes('PASTE_YOUR')) {
    list.innerHTML = `<div class="news-setup">기사 연결 필요 · <a href="${newsSearchUrl(stock)}" target="_blank" rel="noopener noreferrer">Google News ↗</a></div>`;
    return;
  }

  const key = `${stock.category}:${stock.ticker}`;
  const cached = state.newsCache.get(key);
  if (cached && Date.now() - cached.at < NEWS_CACHE_MS) {
    renderNewsList(root, cached.articles);
    return;
  }

  try {
    const payload = await jsonp(NEWS_PROXY_URL, {
      q: newsSearchQuery(stock),
      region: ['KR', 'KR_ETF'].includes(stock.category) ? 'KR' : 'US',
      limit: '5',
    });
    if (!payload?.ok) throw new Error(payload?.error || 'news proxy error');
    const articles = Array.isArray(payload.articles) ? payload.articles.slice(0, 5) : [];
    state.newsCache.set(key, { at: Date.now(), articles });
    renderNewsList(root, articles);
  } catch (err) {
    console.error('news', err);
    if (!root?.isConnected) return;
    list.innerHTML = `<div class="news-error">기사를 불러오지 못했습니다. <a href="${newsSearchUrl(stock)}" target="_blank" rel="noopener noreferrer">직접 검색 ↗</a></div>`;
  }
}

async function switchCategory(category, force=false) {
  if (!CATEGORY[category]) return;
  closeActionPanel();
  state.category = category;
  state.query = '';
  $('#searchInput').value = '';

  if (force) {
    for (const key of [...state.detailCache.keys()]) {
      if (key.startsWith(`${category}:`)) state.detailCache.delete(key);
    }
  }

  $$('.market-tab').forEach((b) => b.classList.toggle('active', b.dataset.category === category));
  $('#status').style.display = 'block';
  $('#status').textContent = '데이터를 불러오는 중입니다.';
  $('#dualBoard').style.display = 'none';

  try {
    await ensureData(category, force);
    renderMeta();
    renderLists();
    $('#status').style.display = 'none';
    $('#dualBoard').style.display = '';
  } catch (err) {
    console.error(err);
    renderMeta();
    $('#status').style.display = 'block';
    $('#status').textContent = '데이터를 불러오지 못했습니다.';
  }
}

$('#marketTabs').addEventListener('click', (e) => {
  const b = e.target.closest('.market-tab');
  if (b) void switchCategory(b.dataset.category);
});

let searchTimer;
$('#searchInput').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    closeActionPanel();
    state.query = e.target.value;
    renderLists();
  }, 100);
});

$('#reloadBtn').addEventListener('click', () => void switchCategory(state.category, true));

document.addEventListener('click', (e) => {
  const action = e.target.closest('[data-stock-action]');
  if (action) {
    void openAction(action);
    return;
  }

  if (e.target.closest('.mini-close')) {
    closeActionPanel();
    return;
  }

  const period = e.target.closest('.mini-period-tabs button');
  if (period && state.openPanel?.isConnected && state.selected) {
    state.openPanel.querySelectorAll('.mini-period-tabs button').forEach((b) => b.classList.remove('active'));
    period.classList.add('active');
    state.chartDays = Number(period.dataset.days) || 120;
    drawMiniChart(state.openPanel, state.selected);
  }
});

window.addEventListener('resize', () => {
  if (state.openPanel?.isConnected && state.selected && state.openPanel.querySelector('.mini-chart')) {
    drawMiniChart(state.openPanel, state.selected);
  }
});

void switchCategory('KR');
