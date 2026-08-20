const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];

const CATEGORY = {
  KR: { short: '국장', dir: 'kr' },
  KR_ETF: { short: '국장 ETF', dir: 'kr-etf' },
  US: { short: '미장', dir: 'us' },
  US_ETF: { short: '미장 ETF', dir: 'us-etf' },
};

const DATA_BASE = location.hostname.endsWith('github.io')
  ? 'https://morninginv.web.app'
  : '.';
const NEWS_PROXY_URL = String(window.BADAK_NEWS_PROXY_URL || '').trim();
const NEWS_CACHE_MS = 5 * 60 * 1000;
const DEFAULT_TOP_N = 100;

const state = {
  category: 'KR',
  data: { KR:null, KR_ETF:null, US:null, US_ETF:null },
  query: '',
  filtered: [],
  detailCache: new Map(),
  newsCache: new Map(),
  cardObserver: null,
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

function scoreValue(stock) {
  const n = Number(stock?.display_score ?? stock?.score);
  return Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;
}

function scoreText(stock) {
  const n = Number(stock?.display_score ?? stock?.score);
  return Number.isFinite(n) ? n.toFixed(1) : '—';
}

function heatClass(stock) {
  const n = scoreValue(stock);
  if (n >= 80) return 'heat-80';
  if (n >= 70) return 'heat-70';
  if (n >= 60) return 'heat-60';
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
  const matched = q
    ? items.filter((s) => `${s.name} ${s.symbol} ${s.ticker} ${s.sector || ''}`.toLowerCase().includes(q))
    : items.slice(0, DEFAULT_TOP_N);
  state.filtered = [...matched].sort((a, b) => {
    const ar = Number(a.rank), br = Number(b.rank);
    if (Number.isFinite(ar) && Number.isFinite(br)) return ar - br;
    return scoreValue(b) - scoreValue(a);
  });
}

function backtestLine(stock) {
  const bt = stock?.backtest || {};
  if (!bt.available || bt.avg_60d == null) {
    return '백테스팅 결과: <b>60일 후 평균 기대수익 —</b> <small>(60점 이상 매수 기준)</small>';
  }
  const klass = Number(bt.avg_60d) > 0 ? 'up' : Number(bt.avg_60d) < 0 ? 'down' : 'flat';
  return `백테스팅 결과: <b>60일 후 평균 기대수익 <span class="${klass}">${ratioPct(bt.avg_60d)}</span></b> <small>(60점 이상 매수 기준)</small>`;
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
        <span>${escapeHtml(sector)}</span>
        <i>·</i>
        <span>시총 ${marketSize(stock.market_size_krw)}</span>
        <i>·</i>
        <span>현재가 ${money(stock.close, stock.currency)} ${changeText(stock.day_change_pct)}</span>
      </div>

      <div class="news-one-line" data-news-line>
        <span class="line-label">NEWS</span>
        <span class="line-placeholder">최신 뉴스 불러오는 중…</span>
      </div>

      <div class="backtest-one-line">${backtestLine(stock)}</div>
    </section>

    <section class="stock-chart-pane">
      <div class="inline-chart" data-chart-box>
        <div class="chart-loading">1Y CHART</div>
      </div>
    </section>
  </article>`;
}

function renderList() {
  filterItems();
  const list = $('#stockList');
  list.innerHTML = state.filtered.length
    ? state.filtered.map(stockCard).join('')
    : '<div class="empty-state">검색 결과가 없습니다.</div>';
  $('#resultCount').textContent = state.query
    ? `${state.filtered.length.toLocaleString()}개`
    : `TOP ${state.filtered.length.toLocaleString()}`;
  activateLazyCards();
}

function renderMeta() {
  const data = currentData();
  $('#categoryTitle').textContent = `${CATEGORY[state.category].short} TOP100`;
  if (!data) {
    $('#marketDate').textContent = '—';
    $('#coverage').textContent = '—';
    $('#scanStatus').textContent = '—';
    return;
  }
  $('#marketDate').textContent = data.market_date || '—';
  $('#coverage').textContent = `가격수신 ${Number(data.coverage_pct || 0).toFixed(1)}%`;
  $('#scanStatus').textContent = data.scan_mode === 'QUICK' ? '장중 QUICK' : '종가 확정 FULL';
}

function stockByTicker(ticker) {
  return (currentData()?.items || []).find((x) => x.ticker === ticker) || null;
}

function chartRows(detail) {
  const c = detail?.chart || {};
  const d = c.d || [];
  return d.map((date, i) => ({
    date,
    close: Number(c.c?.[i]),
    mid: Number(c.m?.[i]),
    upper: Number(c.u?.[i]),
    lower: Number(c.l?.[i]),
  })).filter((x) => Number.isFinite(x.close));
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
  const all = data.flatMap((r) => [r.close, r.upper, r.lower]).filter(Number.isFinite).concat(profileVals);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (!(hi > lo)) { hi = lo * 1.01; lo = lo * 0.99; }
  const padRange = (hi - lo) * 0.07;
  lo -= padRange; hi += padRange;

  const W = 700, H = 250;
  const pad = { l:12, r:55, t:16, b:25 };
  const X = (i) => pad.l + i * (W - pad.l - pad.r) / Math.max(1, data.length - 1);
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

  const profileClasses = ['profile40','profile60','profile120','profile200'];
  let profileSvg = '';
  profiles.forEach((p, idx) => {
    const center = Number(p.center);
    if (!Number.isFinite(center)) return;
    const y = Y(center);
    const labelY = y + (idx - 1.5) * 7;
    profileSvg += `<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" class="profile-line ${profileClasses[idx] || ''}"/>
      <text x="${W-pad.r-4}" y="${labelY}" text-anchor="end" class="profile-label ${profileClasses[idx] || ''}">${escapeHtml(p.days)}D</text>`;
  });

  const dates = [0, Math.floor((data.length - 1)/2), data.length - 1].map((i) => ({ i, label:data[i]?.date?.slice(5) || '' }));
  const dateSvg = dates.map((d) => `<text x="${X(d.i)}" y="${H-6}" text-anchor="${d.i===0?'start':d.i===data.length-1?'end':'middle'}" class="date-axis">${escapeHtml(d.label)}</text>`).join('');

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="1년 가격 차트">
    ${grid}
    <polygon points="${poly}" class="bb-fill"/>
    <path d="${path('upper')}" class="bb-edge"/>
    <path d="${path('lower')}" class="bb-edge"/>
    <path d="${path('mid')}" class="bb-mid"/>
    ${profileSvg}
    <path d="${path('close')}" class="price-line"/>
    ${dateSvg}
  </svg>
  <div class="chart-legend"><span>PRICE</span><span>BB</span><span>40D</span><span>60D</span><span>120D</span><span>200D</span></div>`;
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
  const rows = [
    ['볼린저 하단 근접', Number(s.bollinger || 0), detail.metrics?.percent_b == null ? '' : `%B ${Number(detail.metrics.percent_b).toFixed(3)}`],
    ['40일 주매물대', Number(s.profile_40 || 0), profileText(profiles['40'])],
    ['60일 주매물대', Number(s.profile_60 || 0), profileText(profiles['60'])],
    ['120일 주매물대', Number(s.profile_120 || 0), profileText(profiles['120'])],
    ['200일 주매물대', Number(s.profile_200 || 0), profileText(profiles['200'])],
  ];
  body.innerHTML = `<div class="score-total"><span>TOTAL SCORE</span><b>${scoreText(stock)}</b><small>/ 100</small></div>
    <div class="score-rows">${rows.map(([label, value, sub]) => `<div class="score-row">
      <div><b>${escapeHtml(label)}</b>${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</div>
      <strong class="${value >= 20 ? 'full' : ''}">${Number(value).toFixed(1)}<small>/20</small></strong>
    </div>`).join('')}</div>`;
}

function profileText(p) {
  if (!p?.available) return '매물대 계산 불가';
  const center = Number(p.center);
  return `${p.hit ? '현재가 포함' : '현재가 미포함'} · 중심 ${Number.isFinite(center) ? center.toLocaleString('ko-KR', { maximumFractionDigits:2 }) : '—'}`;
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

$('#marketTabs').addEventListener('click', (e) => {
  const b = e.target.closest('.market-tab');
  if (b) void switchCategory(b.dataset.category);
});

let searchTimer;
$('#searchInput').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = e.target.value;
    renderList();
  }, 100);
});

$('#reloadBtn').addEventListener('click', () => void switchCategory(state.category, true));

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

void switchCategory('KR');
