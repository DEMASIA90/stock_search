const $ = (q) => document.querySelector(q);
const $$ = (q) => [...document.querySelectorAll(q)];

const CATEGORY = {
  KR: { short: '국장', dir: 'kr' },
  US: { short: '미장', dir: 'us' },
  US_ETF: { short: '미장 ETF', dir: 'us-etf' },
};

const MODE = {
  cheap: {
    label: '싼게 좋아',
    emoji: '🧺',
    titleWord: '바닥',
    kicker: '싸게 눌린 대형주를 뒤적뒤적 찾는 모드',
    maxRaw: 5.0,
    scoreColumns: [
      ['s1_percent_b', '① 볼린저 하단 접근'],
      ['s2_upper_swing', '② 과거 상단 이력'],
      ['s3_daily_ha', '③ 일봉 HA 반전'],
      ['s4_weekly_ha', '④ 주봉 HA 양봉'],
      ['s5_monthly_ha', '⑤ 월봉 HA 양봉'],
      ['s6_ma60_slope', '⑥ 60일선 상승'],
    ],
  },
  rising: {
    label: '오르는게 좋아',
    emoji: '🚀',
    titleWord: '상승',
    kicker: '60일선 돌파 뒤 힘이 붙는 종목을 쫓아가는 모드',
    maxRaw: 4.5,
    scoreColumns: [
      ['r1_ma60_breakout', '① 60일선 돌파 시점'],
      ['r2_ha_bull', '② 현재 HA 양봉'],
      ['r3_volume_profile', '③ 아래 매물대 우위'],
      ['r4_post_breakout_gain', '④ 돌파 후 최고 상승'],
    ],
  },
};

const DATA_BASE = location.hostname.endsWith('github.io')
  ? 'https://morninginv.web.app'
  : '.';

const NEWS_PROXY_URL = String(window.BADAK_NEWS_PROXY_URL || '').trim();
const NEWS_CACHE_MS = 5 * 60 * 1000;

function dataUrl(path, force=false) {
  const clean = String(path).replace(/^\.\//, '').replace(/^\//, '');
  const base = DATA_BASE === '.' ? '.' : DATA_BASE.replace(/\/$/, '');
  const url = `${base}/${clean}`;
  return force ? `${url}?ts=${Date.now()}` : url;
}

const DEFAULT_TOP_N = 100;
const BACKTEST_MIN_DISPLAY_SCORE = 50.0;

const state = {
  category: 'KR',
  mode: 'cheap',
  data: { KR: null, US: null, US_ETF: null },
  filtered: [],
  rendered: 0,
  batch: 80,
  query: '',
  selected: null,
  selectedSummary: null,
  detailTarget: null,
  detailEl: null,
  chartDays: 120,
  backtestOpen: false,
  detailCache: new Map(),
  newsCache: new Map(),
};

function modeConfig() { return MODE[state.mode]; }
function modeView(stock, mode=state.mode) {
  if (!stock) return {eligible:false, rank:null, score:0, scores:{}, metrics:{}, backtest:{}};
  if (mode === 'rising') {
    return stock.rising || {eligible:false, rank:null, score:0, scores:{}, metrics:{}, backtest:{}};
  }
  return {
    eligible: true,
    rank: stock.rank,
    score: stock.score,
    display_score: stock.display_score,
    scores: stock.scores || {},
    metrics: stock.metrics || {},
    backtest: stock.backtest || {},
  };
}


function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c]));
}
function money(v, currency) {
  if (v == null) return '—';
  return currency === 'KRW'
    ? '₩' + Math.round(v).toLocaleString('ko-KR')
    : '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function marketSize(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  if (n >= 1e12) return `${(n/1e12).toFixed(n>=100e12?0:1)}조`;
  return `${(n/1e8).toFixed(0)}억`;
}

function pct(v, digits=1) {
  if (v == null) return '—';
  return `${(Number(v) * 100).toFixed(digits)}%`;
}
function dayPct(v, digits=2) {
  if (v == null) return '—';
  const n = Number(v); return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}
function scoreClass(v) {
  const n = Number(v || 0); return n > 0 ? 'score-pos' : n < 0 ? 'score-neg' : 'score-zero';
}
function displayScore(raw, maxRaw=modeConfig().maxRaw) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return '—';
  return (Math.min(100, Math.max(0, n / maxRaw * 100))).toFixed(1);
}
function displayScoreNumber(raw, maxRaw=modeConfig().maxRaw) {
  const n = Number(raw);
  return Number.isFinite(n) ? Math.min(100, Math.max(0, n / maxRaw * 100)) : 0;
}
function heatClass(raw, maxRaw=modeConfig().maxRaw) {
  const n = displayScoreNumber(raw, maxRaw);
  if (n >= 80) return 'heat-80';
  if (n >= 70) return 'heat-70';
  if (n >= 60) return 'heat-60';
  return '';
}
function sizeText(stock) {
  if (stock?.category === 'US_ETF') return '';
  return ` · 시총 ${marketSize(stock?.market_size_krw ?? stock?.metrics?.market_size_krw)}`;
}
function changeClass(v) { return Number(v) > 0 ? 'up' : Number(v) < 0 ? 'down' : ''; }
function currentData() { return state.data[state.category]; }

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

function newsSearchQuery(stock) {
  const name = String(stock?.name || '').trim();
  const symbol = String(stock?.symbol || stock?.ticker || '').trim();
  if (stock?.category === 'KR') return `${name} ${symbol} 주식`;
  if (stock?.category === 'US_ETF') return `${name} ${symbol} ETF`;
  return `${name} ${symbol} stock`;
}

function newsSearchUrl(stock) {
  const q = encodeURIComponent(newsSearchQuery(stock));
  return stock?.category === 'KR'
    ? `https://news.google.com/search?q=${q}&hl=ko&gl=KR&ceid=KR:ko`
    : `https://news.google.com/search?q=${q}&hl=en-US&gl=US&ceid=US:en`;
}

function jsonp(url, params={}, timeoutMs=12000) {
  return new Promise((resolve, reject) => {
    const callback = `__badakNews_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const script = document.createElement('script');
    const query = new URLSearchParams({...params, callback});
    const sep = url.includes('?') ? '&' : '?';
    let done = false;

    const cleanup = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      script.remove();
      try { delete window[callback]; } catch (_) { window[callback] = undefined; }
    };

    window[callback] = (payload) => {
      cleanup();
      resolve(payload);
    };

    script.onerror = () => {
      cleanup();
      reject(new Error('news proxy load failed'));
    };

    const timer = setTimeout(() => {
      cleanup();
      reject(new Error('news proxy timeout'));
    }, timeoutMs);

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
  if (sec < 3600) return `${Math.floor(sec/60)}분 전`;
  if (sec < 86400) return `${Math.floor(sec/3600)}시간 전`;
  if (sec < 604800) return `${Math.floor(sec/86400)}일 전`;
  return dt.toLocaleDateString('ko-KR', {month:'2-digit', day:'2-digit'});
}

function renderNewsList(root, stock, articles) {
  if (!root?.isConnected) return;
  const list = root.querySelector('.news-list');
  if (!list) return;

  if (!Array.isArray(articles) || !articles.length) {
    list.innerHTML = `<div class="news-empty">최근 기사를 찾지 못했습니다.</div>`;
    return;
  }

  list.innerHTML = articles.slice(0,5).map((a, i) => {
    const source = escapeHtml(a.source || 'News');
    const time = escapeHtml(newsTimeText(a.published_at));
    const meta = [source, time].filter(Boolean).join(' · ');
    return `<a class="news-item" href="${escapeHtml(a.link)}" target="_blank" rel="noopener noreferrer">
      <span class="news-index">${String(i+1).padStart(2,'0')}</span>
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
    list.innerHTML = `<div class="news-setup">기사 기능 연결이 필요합니다. <a href="${newsSearchUrl(stock)}" target="_blank" rel="noopener noreferrer">Google News에서 보기 ↗</a></div>`;
    return;
  }

  const key = `${stock.category}:${stock.ticker}`;
  const cached = state.newsCache.get(key);
  if (cached && Date.now() - cached.at < NEWS_CACHE_MS) {
    renderNewsList(root, stock, cached.articles);
    return;
  }

  list.innerHTML = `<div class="news-loading"><span class="news-spinner"></span> 최신 기사 불러오는 중…</div>`;

  try {
    const payload = await jsonp(NEWS_PROXY_URL, {
      q: newsSearchQuery(stock),
      region: stock.category === 'KR' ? 'KR' : 'US',
      limit: '5',
    });
    if (!payload?.ok) throw new Error(payload?.error || 'news proxy error');
    const articles = Array.isArray(payload.articles) ? payload.articles.slice(0,5) : [];
    state.newsCache.set(key, {at: Date.now(), articles});
    renderNewsList(root, stock, articles);
  } catch (err) {
    console.error('news', err);
    if (!root?.isConnected) return;
    list.innerHTML = `<div class="news-error">기사를 불러오지 못했습니다. <a href="${newsSearchUrl(stock)}" target="_blank" rel="noopener noreferrer">직접 검색 ↗</a></div>`;
  }
}

function modeSort(a, b) {
  const av = modeView(a), bv = modeView(b);
  const ae = av.eligible !== false, be = bv.eligible !== false;
  if (ae !== be) return ae ? -1 : 1;
  const ar = Number(av.rank), br = Number(bv.rank);
  if (Number.isFinite(ar) && Number.isFinite(br)) return ar - br;
  if (Number.isFinite(ar)) return -1;
  if (Number.isFinite(br)) return 1;
  return Number(bv.score || 0) - Number(av.score || 0);
}

function applyFilter() {
  const data = currentData();
  const items = data?.items || [];
  const q = state.query.trim().toLowerCase();

  if (q) {
    // Search always sees the complete common universe. A rising-mode symbol that
    // does not satisfy the MA60 breakout rule still appears, marked unqualified.
    state.filtered = items
      .filter((s) => `${s.name} ${s.symbol} ${s.ticker} ${s.exchange}`.toLowerCase().includes(q))
      .sort(modeSort);
  } else {
    const eligible = state.mode === 'rising'
      ? items.filter((s) => modeView(s).eligible)
      : items;
    state.filtered = [...eligible].sort(modeSort).slice(0, DEFAULT_TOP_N);
  }

  state.rendered = 0;
  $('#stockTableBody').innerHTML = '';
  $('#mobileList').innerHTML = '';

  const modeEligibleCount = state.mode === 'rising'
    ? Number(data?.rising_eligible_count || 0)
    : items.length;
  $('#resultCount').textContent = q
    ? `${state.filtered.length.toLocaleString()}개 검색`
    : `상위 ${state.filtered.length.toLocaleString()}개 · 모드대상 ${modeEligibleCount.toLocaleString()}개`;

  renderTableHeader();
  renderNextBatch();
}

function renderMeta() {
  const data = currentData();
  const cfg = modeConfig();
  $('#categoryTitle').textContent = `${CATEGORY[state.category].short} ${cfg.titleWord} TOP100`;
  $('#modeKicker').textContent = cfg.kicker;
  if (!data) {
    $('#updated').textContent = '—'; $('#marketDate').textContent = '—'; $('#coverage').textContent = '—'; return;
  }
  const dt = data.generated_at_utc ? new Date(data.generated_at_utc) : null;
  $('#updated').textContent = dt && !Number.isNaN(dt.getTime())
    ? dt.toLocaleString('ko-KR', { timeZone:'Asia/Seoul', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' })
    : '—';
  $('#marketDate').textContent = data.market_date ? `${data.market_date} 기준` : '—';
  const modeCount = state.mode === 'rising' ? Number(data.rising_eligible_count || 0) : Number(data.passed_count || 0);
  $('#coverage').textContent = `${modeCount.toLocaleString()} 모드대상 · ${Number(data.universe_count || 0).toLocaleString()} 전체`;
}


function btLabel(stock) {
  return modeView(stock)?.backtest?.quality_label || 'NORMAL';
}
function btRowClass(stock) {
  const q = btLabel(stock);
  return q === 'STRONG' ? 'bt-strong' : q === 'GOOD' ? 'bt-good' : '';
}
function btBadge(stock) {
  const bt = modeView(stock)?.backtest;
  if (!bt || !bt.available) return '';
  if (bt.quality_label === 'STRONG') return `<span class="bt-badge strong">BT ${Number(bt.quality_score || 0).toFixed(0)}</span>`;
  if (bt.quality_label === 'GOOD') return `<span class="bt-badge good">BT ${Number(bt.quality_score || 0).toFixed(0)}</span>`;
  return '';
}
function modeMissBadge(stock) {
  return state.mode === 'rising' && !modeView(stock).eligible
    ? '<span class="mode-miss-badge">60일선 돌파 조건 미충족</span>'
    : '';
}

function renderTableHeader() {
  const cols = modeConfig().scoreColumns;
  $('#tableHeadRow').innerHTML = `
    <th class="rank-col">#</th>
    <th class="stock-col">종목</th>
    <th class="total-col">총점</th>
    ${cols.map(([,label]) => `<th>${escapeHtml(label)}</th>`).join('')}`;
}

function rowHtml(s) {
  const mv = modeView(s), sc = mv.scores || {};
  const rank = Number.isFinite(Number(mv.rank)) ? Number(mv.rank).toLocaleString() : '—';
  return `<tr class="${btRowClass(s)} ${heatClass(mv.score)} ${mv.eligible === false ? 'mode-ineligible' : ''}" data-ticker="${escapeHtml(s.ticker)}">
    <td class="rank">${rank}</td>
    <td class="stock"><div class="stock-name"><strong>${escapeHtml(s.name)}</strong>${btBadge(s)}${modeMissBadge(s)}</div><div class="stock-sub">${escapeHtml(s.symbol)} · ${escapeHtml(s.exchange)} · ${money(s.close, s.currency)}${sizeText(s)}</div></td>
    <td class="total ${heatClass(mv.score)}"><span class="score-pill">${displayScore(mv.score)}</span></td>
    ${modeConfig().scoreColumns.map(([k]) => `<td class="${scoreClass(sc[k])}">${Number(sc[k] || 0).toFixed(3)}</td>`).join('')}
  </tr>`;
}

function mobileHtml(s) {
  const mv = modeView(s);
  const rank = Number.isFinite(Number(mv.rank)) ? Number(mv.rank) : '—';
  // Mobile list cards intentionally show only identity + total score.
  // Component scores appear only after the user expands the card.
  return `<button class="mobile-card ${btRowClass(s)} ${heatClass(mv.score)} ${mv.eligible === false ? 'mode-ineligible' : ''}" data-ticker="${escapeHtml(s.ticker)}">
    <div class="mobile-top">
      <span class="mobile-rank">${rank}</span>
      <div class="mobile-title"><strong>${escapeHtml(s.name)} ${btBadge(s)}</strong><span>${escapeHtml(s.symbol)} · ${escapeHtml(s.exchange)} · ${money(s.close, s.currency)}${sizeText(s)}</span>${modeMissBadge(s)}</div>
      <div class="mobile-score ${heatClass(mv.score)}"><strong>${displayScore(mv.score)}</strong><span>/ 100</span></div>
    </div>
  </button>`;
}

function renderNextBatch() {
  if (state.rendered >= state.filtered.length) return;
  const end = Math.min(state.rendered + state.batch, state.filtered.length);
  const slice = state.filtered.slice(state.rendered, end);
  $('#stockTableBody').insertAdjacentHTML('beforeend', slice.map(rowHtml).join(''));
  $('#mobileList').insertAdjacentHTML('beforeend', slice.map(mobileHtml).join(''));
  state.rendered = end;
}


function bindResultClicks() {
  document.addEventListener('click', (e) => {
    const target = e.target.closest('tr[data-ticker], .mobile-card[data-ticker]');
    if (!target) return;
    const stock = state.filtered.find((x) => x.ticker === target.dataset.ticker);
    if (stock) void openInlineStock(stock, target);
  });
}

function render() {
  renderMeta();
  $$('.mode-btn').forEach((b) => b.classList.toggle('active', b.dataset.mode === state.mode));
  renderTableHeader();
  const data = currentData();
  if (!data) {
    $('#status').style.display = 'block'; $('#status').textContent = '데이터가 아직 없습니다.';
    $('#desktopTable').style.display = 'none'; $('#mobileList').style.display = 'none'; return;
  }
  $('#status').style.display = 'none';
  $('#desktopTable').style.display = '';
  $('#mobileList').style.display = '';
  applyFilter();
}

async function switchCategory(category, force=false) {
  state.category = category; state.query = ''; $('#searchInput').value = '';
  if (force) {
    for (const key of [...state.detailCache.keys()]) {
      if (key.startsWith(`${category}:`)) state.detailCache.delete(key);
    }
  }
  $$('.market-tab').forEach((b) => b.classList.toggle('active', b.dataset.category === category));
  $('#status').style.display = 'block'; $('#status').textContent = '데이터를 불러오는 중입니다.';
  $('#desktopTable').style.display = 'none'; $('#mobileList').style.display = 'none';
  try { await ensureData(category, force); render(); }
  catch (err) { $('#status').textContent = '데이터를 불러오지 못했습니다.'; renderMeta(); }
}

function scoreTile(key, label, scores) {
  const v = Number(scores[key] || 0);
  return `<div class="score-tile"><span>${label}</span><b class="${scoreClass(v)}">${v.toFixed(3)}</b></div>`;
}


function collapseInlineDetail() {
  if (state.detailEl) state.detailEl.remove();
  if (state.detailTarget) state.detailTarget.classList.remove('expanded');
  state.detailEl = null;
  state.detailTarget = null;
  state.selected = null;
  state.selectedSummary = null;
  state.backtestOpen = false;
}

function inlineDetailShell(summary, mobile=false) {
  const tag = mobile ? 'section' : 'div';
  const mv = modeView(summary);
  const cfg = modeConfig();
  return `<${tag} class="inline-detail-card">
    <header class="inline-detail-head">
      <div>
        <span class="eyebrow detail-ticker">${escapeHtml(summary.symbol)} · ${escapeHtml(summary.exchange)} · ${cfg.emoji} ${cfg.label}</span>
        <h2 class="detail-name">${escapeHtml(summary.name)}</h2>
      </div>
      <button class="inline-close icon-btn" aria-label="상세 닫기">×</button>
    </header>
    <section class="detail-hero">
      <div class="detail-score ${heatClass(mv.score)}"><strong>${displayScore(mv.score)}</strong><span>/ 100</span></div>
      <div class="detail-price"><strong>${money(summary.close, summary.currency)}</strong><span class="${changeClass(summary.day_change_pct)}">${dayPct(summary.day_change_pct,2)}</span></div>
    </section>

    <section class="chart-card detail-chart-first">
      <div class="chart-head">
        <b>Daily</b>
        <div class="chart-controls">
          <div class="period-tabs inline-period-tabs">
            <button data-days="20">1M</button>
            <button data-days="60">3M</button>
            <button class="active" data-days="120">6M</button>
          </div>
          <button class="backtest-btn inline-backtest-btn" disabled>백테스트</button>
        </div>
      </div>
      <div class="chart detail-chart"><div class="detail-loading chart-loading-inline">차트 로딩 중…</div></div>
      <div class="legend"><span class="price-line">Price</span><span class="mid-line">BB Mid</span><span class="band-line">Band</span><span>MA60</span></div>
    </section>

    <section class="news-panel">
      <div class="news-head">
        <div><span class="eyebrow">LATEST NEWS</span><h3>최신 기사 헤드라인</h3></div>
        <a class="news-all-link" href="#" target="_blank" rel="noopener noreferrer">전체 기사 ↗</a>
      </div>
      <div class="news-list"><div class="news-loading"><span class="news-spinner"></span> 최신 기사 불러오는 중…</div></div>
    </section>

    <section class="backtest-panel inline-backtest-panel" hidden>
      <div class="backtest-head">
        <div><span class="eyebrow">BACKTEST · ${escapeHtml(cfg.label)}</span><h3>총점 50점 이상 과거 신호 성과</h3></div>
        <span class="backtest-rule">1Y · 총점 ≥ 50.0 · Next Open</span>
      </div>
      <div class="backtest-summary"></div>
      <section class="forecast-panel" hidden>
        <div class="forecast-head"><div><span class="eyebrow">1 MONTH OUTLOOK</span><h3>백테스트 기반 예상</h3></div><span class="forecast-quality">—</span></div>
        <div class="forecast-summary"></div>
        <div class="forecast-chart"></div>
        <div class="forecast-legend"><span class="forecast-mean">Expected</span><span class="forecast-range">25–75% range</span></div>
      </section>
      <div class="backtest-table-wrap">
        <table class="backtest-table"><thead><tr><th>신호일</th><th>점수</th><th>5D</th><th>10D</th><th>20D</th><th>MFE20</th><th>MAE20</th></tr></thead><tbody class="backtest-body"></tbody></table>
      </div>
    </section>

    <section class="detail-section-title">상세 점수 · ${escapeHtml(cfg.label)}</section>
    <section class="score-grid detail-score-grid"><div class="detail-loading">상세 데이터 로딩 중…</div></section>
    <section class="metric-grid detail-metric-grid"></section>
  </${tag}>`;
}


async function openInlineStock(summary, target) {
  if (state.detailTarget === target && state.detailEl) {
    collapseInlineDetail();
    return;
  }
  collapseInlineDetail();

  state.selectedSummary = summary;
  state.detailTarget = target;
  target.classList.add('expanded');
  const mobile = target.classList.contains('mobile-card');

  if (mobile) {
    target.insertAdjacentHTML('afterend', `<div class="inline-detail-wrap mobile-detail-wrap">${inlineDetailShell(summary,true)}</div>`);
    state.detailEl = target.nextElementSibling;
  } else {
    const colspan = 3 + modeConfig().scoreColumns.length;
    target.insertAdjacentHTML('afterend', `<tr class="inline-detail-row"><td colspan="${colspan}"><div class="inline-detail-wrap">${inlineDetailShell(summary,false)}</div></td></tr>`);
    state.detailEl = target.nextElementSibling;
  }

  const detail = state.detailEl.querySelector('.inline-detail-card');
  detail.scrollIntoView({behavior:'smooth', block:'nearest'});
  void loadLatestNews(summary, detail);

  try {
    const stock = await ensureDetail(summary);
    if (state.detailTarget !== target) return;
    renderInlineStockDetail(stock);
  } catch (err) {
    console.error(err);
    const grid = detail.querySelector('.detail-score-grid');
    grid.innerHTML = '<div class="detail-error">상세 데이터를 불러오지 못했습니다.</div>';
    detail.querySelector('.detail-chart').innerHTML = '';
  }
}

function renderInlineStockDetail(stock) {
  if (!state.detailEl) return;
  state.selected = stock;
  state.chartDays = 120;
  state.backtestOpen = false;
  const root = state.detailEl;
  const btn = root.querySelector('.inline-backtest-btn');
  btn.disabled = false;
  btn.classList.remove('active');
  btn.textContent = '백테스트';
  root.querySelector('.inline-backtest-panel').hidden = true;
  root.querySelector('.forecast-panel').hidden = true;
  root.querySelectorAll('.inline-period-tabs button').forEach((b)=>b.classList.toggle('active',Number(b.dataset.days)===120));

  const mv = modeView(stock);
  const s = mv.scores || {}, m = mv.metrics || {};
  root.querySelector('.detail-score-grid').innerHTML = modeConfig().scoreColumns.map(([k,l]) => scoreTile(k,l,s)).join('');

  const age = (v, suffix) => v == null ? '—' : `${v}${suffix}`;
  const commonSize = stock.category === 'US_ETF'
    ? 'ETF 면제'
    : marketSize((stock.metrics || {}).market_size_krw ?? m.market_size_krw);

  if (state.mode === 'cheap') {
    const maSlope = m.ma60_slope_pct == null ? '—' : `${Number(m.ma60_slope_pct) > 0 ? '+' : ''}${(Number(m.ma60_slope_pct) * 100).toFixed(3)}%/일`;
    root.querySelector('.detail-metric-grid').innerHTML = `
      <div><span>${stock.category === 'US_ETF' ? '시총 필터' : '시가총액'}</span><b>${commonSize}</b></div>
      <div><span>%B</span><b>${m.percent_b == null ? '—' : Number(m.percent_b).toFixed(3)}</b></div>
      <div><span>Band Width</span><b>${pct(m.bandwidth,1)}</b></div>
      <div><span>Upper Swing</span><b>${age(m.upper_swing_age,'D')}</b></div>
      <div><span>일봉 HA 반전</span><b>${age(m.daily_ha_age,'D')} · 직전 음봉 ${Number(m.daily_ha_prior_bear || 0)}일</b></div>
      <div><span>현재 주봉 HA</span><b>${m.weekly_ha_bull ? '양봉 · +0.5' : '음봉 · +0.0'}</b></div>
      <div><span>현재 월봉 HA</span><b>${m.monthly_ha_bull ? '양봉 · +0.5' : '음봉 · +0.0'}</b></div>
      <div><span>MA60</span><b>${m.ma60 == null ? '—' : money(m.ma60, stock.currency)}</b></div>
      <div><span>MA60 기울기</span><b>${maSlope}</b></div>`;
  } else {
    const breakout = m.breakout_age == null ? '조건 미충족' : `${m.breakout_age}거래일 전`;
    const profile = m.volume_profile_below_share == null ? '데이터 없음' : `${(Number(m.volume_profile_below_share)*100).toFixed(1)}% 아래`;
    const gain = m.post_breakout_max_gain == null ? '—' : `${(Number(m.post_breakout_max_gain)*100).toFixed(1)}%`;
    root.querySelector('.detail-metric-grid').innerHTML = `
      <div><span>${stock.category === 'US_ETF' ? '시총 필터' : '시가총액'}</span><b>${commonSize}</b></div>
      <div><span>60일선 돌파 조건</span><b>${mv.eligible ? '충족' : '미충족'}</b></div>
      <div><span>최근 60일선 돌파</span><b>${breakout}</b></div>
      <div><span>돌파일 종가</span><b>${m.breakout_close == null ? '—' : money(m.breakout_close, stock.currency)}</b></div>
      <div><span>현재 MA60</span><b>${m.ma60 == null ? '—' : money(m.ma60, stock.currency)}</b></div>
      <div><span>현재 HA</span><b>D ${m.daily_ha_bull?'양':'음'} · W ${m.weekly_ha_bull?'양':'음'} · M ${m.monthly_ha_bull?'양':'음'}</b></div>
      <div><span>60D 매물대</span><b>${profile}</b></div>
      <div><span>아래 / 위 매물대</span><b>${m.volume_profile_below == null ? '—' : `${Number(m.volume_profile_below).toLocaleString()} / ${Number(m.volume_profile_above || 0).toLocaleString()}`}</b></div>
      <div><span>돌파 후 최고 상승률</span><b>${gain}</b></div>`;
  }

  drawSelectedChart();
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

function backtestMetric(label, value, sub='') {
  return `<div class="bt-metric"><span>${label}</span><b>${value}</b>${sub ? `<small>${sub}</small>` : ''}</div>`;
}


function forecastMoney(v, currency) {
  return money(v, currency);
}

function renderForecast(stock) {
  if (!state.detailEl) return;
  const bt = modeView(stock)?.backtest || {};
  const fc = bt?.forecast || {};
  const panel = state.detailEl.querySelector('.forecast-panel');

  if (!fc.available || !['GOOD','STRONG'].includes(bt.quality_label)) {
    panel.hidden = true;
    panel.querySelector('.forecast-chart').innerHTML = '';
    return;
  }

  panel.hidden = false;
  const quality = panel.querySelector('.forecast-quality');
  quality.textContent = `${bt.quality_label} · BT ${Number(bt.quality_score || 0).toFixed(0)}`;
  quality.className = `forecast-quality ${bt.quality_label === 'STRONG' ? 'strong' : 'good'}`;
  panel.querySelector('.forecast-summary').innerHTML = [
    backtestMetric('현재가', forecastMoney(fc.current_price, stock.currency)),
    backtestMetric('20D 예상', forecastMoney(fc.expected_price_20d, stock.currency), ratioPct(fc.expected_return_20d)),
    backtestMetric('예상 범위', `${forecastMoney(fc.range_low_20d, stock.currency)} ~ ${forecastMoney(fc.range_high_20d, stock.currency)}`, `유사신호 ${fc.sample_count || 0}개`),
  ].join('');
  drawForecastChart(panel.querySelector('.forecast-chart'), fc, stock.currency);
}

function drawForecastChart(el, fc, currency) {
  const mean = (fc.mean_price || []).map(Number);
  const low = (fc.low_price || []).map(Number);
  const high = (fc.high_price || []).map(Number);
  const current = Number(fc.current_price);

  if (!Number.isFinite(current) || !mean.length || mean.length !== low.length || mean.length !== high.length) {
    el.innerHTML = '';
    return;
  }

  const W = Math.max(320, el.clientWidth || 720);
  const H = 240;
  const pad = {l:10, r:64, t:18, b:28};
  const all = [current, ...mean, ...low, ...high].filter(Number.isFinite);
  const mn = Math.min(...all), mx = Math.max(...all);
  const span = Math.max(mx-mn, Math.abs(mx)*0.03, 1);
  const lo = mn-span*.12, hi = mx+span*.12;

  const pts = [current, ...mean];
  const lows = [current, ...low];
  const highs = [current, ...high];
  const X = i => pad.l + i/Math.max(1, pts.length-1)*(W-pad.l-pad.r);
  const Y = v => pad.t + (1-(v-lo)/(hi-lo))*(H-pad.t-pad.b);
  const path = arr => arr.map((v,i)=>`${i?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
  const polygon = highs.map((v,i)=>`${X(i)},${Y(v)}`).join(' ') + ' ' +
    [...lows].reverse().map((v,ri)=>{const i=lows.length-1-ri; return `${X(i)},${Y(v)}`}).join(' ');

  let grid = '';
  for (let i=0;i<4;i++) {
    const yy = pad.t + i*(H-pad.t-pad.b)/3;
    const val = hi - i*(hi-lo)/3;
    const label = currency === 'KRW' ? Math.round(val).toLocaleString('ko-KR') : '$'+val.toFixed(val>=100?0:1);
    grid += `<line x1="${pad.l}" x2="${W-pad.r}" y1="${yy}" y2="${yy}" stroke="#202631"/><text x="${W-pad.r+6}" y="${yy+4}" fill="#718096" font-size="9">${label}</text>`;
  }
  const xlabels = [0,5,10,15,20].map(i => {
    const xx = X(i);
    return `<text x="${xx}" y="${H-7}" text-anchor="${i===0?'start':i===20?'end':'middle'}" fill="#718096" font-size="9">${i===0?'Now':`D+${i}`}</text>`;
  }).join('');

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs><linearGradient id="forecastFill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#78f2b6" stop-opacity=".18"/>
      <stop offset="1" stop-color="#78f2b6" stop-opacity=".03"/>
    </linearGradient></defs>
    ${grid}
    <polygon points="${polygon}" fill="url(#forecastFill)"/>
    <path d="${path(highs)}" fill="none" stroke="#5f8c7a" stroke-width="1" stroke-dasharray="3 3"/>
    <path d="${path(lows)}" fill="none" stroke="#5f8c7a" stroke-width="1" stroke-dasharray="3 3"/>
    <path d="${path(pts)}" fill="none" stroke="#78f2b6" stroke-width="2.2"/>
    <circle cx="${X(0)}" cy="${Y(current)}" r="3" fill="#e8eef5"/>
    <circle cx="${X(20)}" cy="${Y(mean[mean.length-1])}" r="3.5" fill="#78f2b6"/>
    ${xlabels}
  </svg>`;
}

function renderBacktest(stock) {
  if (!state.detailEl) return;
  const bt = modeView(stock)?.backtest || {};
  const panel = state.detailEl.querySelector('.inline-backtest-panel');
  const rule = panel.querySelector('.backtest-rule');
  const summary = panel.querySelector('.backtest-summary');
  const body = panel.querySelector('.backtest-body');

  if (!bt.available) {
    panel.querySelector('.forecast-panel').hidden = true;
    rule.textContent = '1Y · 총점 ≥ 50.0 · 데이터 부족';
    summary.innerHTML = backtestMetric('Signals', '0');
    body.innerHTML = '';
    return;
  }

  const maxRaw = Number(bt.raw_max_score || modeConfig().maxRaw);
  rule.textContent = `1Y · 총점 ≥ ${displayScore(bt.min_signal_score ?? maxRaw*0.5, maxRaw)} · Next Open · ${bt.cooldown_days || 10}D cooldown`;
  summary.innerHTML = [
    backtestMetric('Signals', Number(bt.signals || 0).toLocaleString()),
    backtestMetric('5D Avg', ratioPct(bt.avg_5d), `Win ${winPct(bt.win_5d)}`),
    backtestMetric('10D Avg', ratioPct(bt.avg_10d), `Win ${winPct(bt.win_10d)}`),
    backtestMetric('20D Avg', ratioPct(bt.avg_20d), `Win ${winPct(bt.win_20d)}`),
    backtestMetric('20D Median', ratioPct(bt.median_20d)),
    backtestMetric('MFE / MAE', `${ratioPct(bt.avg_mfe_20d)} / ${ratioPct(bt.avg_mae_20d)}`),
  ].join('');

  const trades = bt.trades || [];
  renderForecast(stock);
  body.innerHTML = trades.length
    ? trades.map(t => `<tr><td>${escapeHtml(t.signal_date || '—')}</td><td>${t.score == null ? '—' : displayScore(t.score, Number(bt.raw_max_score || modeConfig().maxRaw))}</td><td class="${changeClass((t.ret_5d || 0)*100)}">${ratioPct(t.ret_5d)}</td><td class="${changeClass((t.ret_10d || 0)*100)}">${ratioPct(t.ret_10d)}</td><td class="${changeClass((t.ret_20d || 0)*100)}">${ratioPct(t.ret_20d)}</td><td class="up">${ratioPct(t.mfe_20d)}</td><td class="down">${ratioPct(t.mae_20d)}</td></tr>`).join('')
    : `<tr><td colspan="7" class="bt-empty">최근 1년 내 총점 50점 이상 독립 신호가 없습니다.</td></tr>`;
}


function chartRows(stock) {
  const c = stock.chart || {}, d = c.d || [];
  return d.map((date,i) => ({ date, close:c.c?.[i], mid:c.m?.[i], upper:c.u?.[i], lower:c.l?.[i], ma60:c.a60?.[i] }));
}
function drawSelectedChart() {
  if (!state.selected) return;
  if (!state.detailEl) return;
  drawChart(state.detailEl.querySelector('.detail-chart'), chartRows(state.selected).slice(-state.chartDays), state.selected.currency);
}
function drawChart(el, data, currency) {
  data = data.filter((d) => [d.close,d.mid,d.upper,d.lower].every(Number.isFinite));
  if (!data.length) { el.innerHTML=''; return; }
  const W = Math.max(320, el.clientWidth || 760), H = Math.max(260, el.clientHeight || 330);
  const pad={l:8,r:58,t:15,b:22};
  const vals=data.flatMap((d)=>[d.close,d.upper,d.lower]); const min=Math.min(...vals), max=Math.max(...vals), span=Math.max(max-min,Math.abs(max)*.02,1); const lo=min-span*.08, hi=max+span*.08;
  const X=(i)=>pad.l+i/Math.max(1,data.length-1)*(W-pad.l-pad.r), Y=(v)=>pad.t+(1-(v-lo)/(hi-lo))*(H-pad.t-pad.b);
  const path=(key)=>data.filter((d)=>Number.isFinite(d[key])).map((d,i,arr)=>{ const realIndex=data.indexOf(d); return `${i?'L':'M'}${X(realIndex).toFixed(1)},${Y(d[key]).toFixed(1)}`; }).join(' ');
  const poly=data.map((d,i)=>`${X(i)},${Y(d.upper)}`).join(' ')+' '+[...data].reverse().map((d,ri)=>{const i=data.length-1-ri;return `${X(i)},${Y(d.lower)}`}).join(' ');
  let grid=''; for(let i=0;i<5;i++){const y=pad.t+i*(H-pad.t-pad.b)/4,v=hi-i*(hi-lo)/4,label=currency==='KRW'?Math.round(v).toLocaleString('ko-KR'):'$'+v.toFixed(v>=100?0:1);grid+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" stroke="#202631"/><text x="${W-pad.r+6}" y="${y+4}" fill="#718096" font-size="9">${label}</text>`}
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="bandFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7faaff" stop-opacity=".14"/><stop offset="1" stop-color="#7faaff" stop-opacity=".02"/></linearGradient></defs>${grid}<polygon points="${poly}" fill="url(#bandFill)"/><path d="${path('upper')}" fill="none" stroke="#5579b8" stroke-width="1.1"/><path d="${path('lower')}" fill="none" stroke="#5579b8" stroke-width="1.1"/><path d="${path('mid')}" fill="none" stroke="#efc46a" stroke-width="1.2"/><path d="${path('ma60')}" fill="none" stroke="#b899ff" stroke-width="1.4"/><path d="${path('close')}" fill="none" stroke="#78f2b6" stroke-width="2"/></svg>`;
}

$('#modeSwitch').addEventListener('click', (e) => {
  const b = e.target.closest('.mode-btn');
  if (!b || !MODE[b.dataset.mode] || b.dataset.mode === state.mode) return;
  collapseInlineDetail();
  state.mode = b.dataset.mode;
  $$('.mode-btn').forEach((x) => x.classList.toggle('active', x.dataset.mode === state.mode));
  render();
});

$('#marketTabs').addEventListener('click', (e) => { const b=e.target.closest('.market-tab'); if(b){ collapseInlineDetail(); switchCategory(b.dataset.category); } });
let searchTimer;
$('#searchInput').addEventListener('input', (e) => { clearTimeout(searchTimer); searchTimer=setTimeout(()=>{state.query=e.target.value; collapseInlineDetail(); applyFilter();},100); });
$('#reloadBtn').onclick=()=>{ collapseInlineDetail(); switchCategory(state.category,true); };

document.addEventListener('click',(e)=>{
  const close=e.target.closest('.inline-close');
  if(close){ collapseInlineDetail(); return; }

  const p=e.target.closest('.inline-period-tabs button');
  if(p && state.detailEl){
    state.detailEl.querySelectorAll('.inline-period-tabs button').forEach(x=>x.classList.remove('active'));
    p.classList.add('active');
    state.chartDays=Number(p.dataset.days);
    drawSelectedChart();
    return;
  }

  const bt=e.target.closest('.inline-backtest-btn');
  if(bt && state.selected && state.detailEl){
    state.backtestOpen=!state.backtestOpen;
    const panel=state.detailEl.querySelector('.inline-backtest-panel');
    panel.hidden=!state.backtestOpen;
    bt.classList.toggle('active',state.backtestOpen);
    bt.textContent=state.backtestOpen?'백테스트 닫기':'백테스트';
    if(state.backtestOpen) renderBacktest(state.selected);
  }
});

window.addEventListener('resize',()=>{
  if(state.detailEl && state.selected){
    drawSelectedChart();
    const bt = modeView(state.selected)?.backtest;
    if(state.backtestOpen && bt?.forecast?.available){
      drawForecastChart(state.detailEl.querySelector('.forecast-chart'), bt.forecast, state.selected.currency);
    }
  }
});

const observer = new IntersectionObserver((entries)=>{ if(entries[0]?.isIntersecting) renderNextBatch(); }, { rootMargin:'600px 0px' });
observer.observe($('#sentinel'));
bindResultClicks();
switchCategory('KR');
