const $ = (q) => document.querySelector(q);
const $$ = (q) => [...document.querySelectorAll(q)];

const CATEGORY = {
  KR: { label: 'KOREA', dir: 'kr' },
  US: { label: 'UNITED STATES', dir: 'us' },
  US_ETF: { label: 'US ETF', dir: 'us-etf' },
};

const DATA_BASE = location.hostname.endsWith('github.io')
  ? 'https://morninginv.web.app'
  : '.';

function dataUrl(path, force=false) {
  const clean = String(path).replace(/^\.\//, '').replace(/^\//, '');
  const base = DATA_BASE === '.' ? '.' : DATA_BASE.replace(/\/$/, '');
  const url = `${base}/${clean}`;
  return force ? `${url}?ts=${Date.now()}` : url;
}

const SCORE_COLUMNS = [
  ['s1_percent_b', '① %B'],
  ['s2_upper_swing', '② Swing'],
  ['s3_psar', '③ SAR'],
  ['s4_daily_ha', '④ D-HA'],
  ['s5_weekly_ha', '⑤ W-HA'],
  ['s6_monthly_ha', '⑥ M-HA'],
  ['s8_turnover', '⑧ Value'],
];

const state = {
  category: 'KR',
  data: { KR: null, US: null, US_ETF: null },
  filtered: [],
  rendered: 0,
  batch: 80,
  query: '',
  selected: null,
  chartDays: 120,
  backtestOpen: false,
  detailCache: new Map(),
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c]));
}
function money(v, currency) {
  if (v == null) return '—';
  return currency === 'KRW'
    ? '₩' + Math.round(v).toLocaleString('ko-KR')
    : '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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

function applyFilter() {
  const data = currentData();
  const items = data?.items || [];
  const q = state.query.trim().toLowerCase();
  state.filtered = q
    ? items.filter((s) => `${s.name} ${s.symbol} ${s.ticker} ${s.exchange}`.toLowerCase().includes(q))
    : items;
  state.rendered = 0;
  $('#stockTableBody').innerHTML = '';
  $('#mobileList').innerHTML = '';
  $('#resultCount').textContent = `${state.filtered.length.toLocaleString()}개`;
  renderNextBatch();
}

function renderMeta() {
  const data = currentData();
  $('#categoryLabel').textContent = CATEGORY[state.category].label;
  if (!data) {
    $('#updated').textContent = '—'; $('#marketDate').textContent = '—'; $('#coverage').textContent = '—'; return;
  }
  const dt = data.generated_at_utc ? new Date(data.generated_at_utc) : null;
  $('#updated').textContent = dt && !Number.isNaN(dt.getTime())
    ? dt.toLocaleString('ko-KR', { timeZone:'Asia/Seoul', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' })
    : '—';
  $('#marketDate').textContent = data.market_date ? `${data.market_date} 기준` : '—';
  $('#coverage').textContent = `${Number(data.passed_count || 0).toLocaleString()} 통과 · ${Number(data.universe_count || 0).toLocaleString()} 전체`;
}


function btLabel(stock) {
  return stock?.backtest?.quality_label || 'NORMAL';
}
function btRowClass(stock) {
  const q = btLabel(stock);
  return q === 'STRONG' ? 'bt-strong' : q === 'GOOD' ? 'bt-good' : '';
}
function btBadge(stock) {
  const bt = stock?.backtest;
  if (!bt || !bt.available) return '';
  if (bt.quality_label === 'STRONG') return `<span class="bt-badge strong">BT ${Number(bt.quality_score || 0).toFixed(0)}</span>`;
  if (bt.quality_label === 'GOOD') return `<span class="bt-badge good">BT ${Number(bt.quality_score || 0).toFixed(0)}</span>`;
  return '';
}

function rowHtml(s) {
  const sc = s.scores || {};
  return `<tr class="${btRowClass(s)}" data-ticker="${escapeHtml(s.ticker)}">
    <td class="rank">${Number(s.rank).toLocaleString()}</td>
    <td class="stock"><div class="stock-name"><strong>${escapeHtml(s.name)}</strong>${btBadge(s)}</div><div class="stock-sub">${escapeHtml(s.symbol)} · ${escapeHtml(s.exchange)} · ${money(s.close, s.currency)}</div></td>
    <td class="total">${Number(s.score).toFixed(3)}</td>
    ${SCORE_COLUMNS.map(([k]) => `<td class="${scoreClass(sc[k])}">${Number(sc[k] || 0).toFixed(3)}</td>`).join('')}
  </tr>`;
}

function mobileHtml(s) {
  const sc = s.scores || {};
  return `<button class="mobile-card ${btRowClass(s)}" data-ticker="${escapeHtml(s.ticker)}">
    <div class="mobile-top">
      <span class="mobile-rank">${s.rank}</span>
      <div class="mobile-title"><strong>${escapeHtml(s.name)} ${btBadge(s)}</strong><span>${escapeHtml(s.symbol)} · ${escapeHtml(s.exchange)} · ${money(s.close, s.currency)}</span></div>
      <div class="mobile-score"><strong>${Number(s.score).toFixed(3)}</strong><span>/ 2.70</span></div>
    </div>
    <div class="mobile-grid">
      <span>① %B<b>${Number(sc.s1_percent_b || 0).toFixed(3)}</b></span>
      <span>③ SAR<b>${Number(sc.s3_psar || 0).toFixed(3)}</b></span>
      <span>⑤ W-HA<b>${Number(sc.s5_weekly_ha || 0).toFixed(3)}</b></span>
      <span>⑧ Value<b class="${scoreClass(sc.s8_turnover)}">${Number(sc.s8_turnover || 0).toFixed(3)}</b></span>
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
    if (stock) void openStock(stock);
  });
}

function render() {
  renderMeta();
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


async function openStock(summary) {
  state.selected = null;
  state.backtestOpen = false;
  $('#backtestPanel').hidden = true;
  $('#forecastPanel').hidden = true;
  $('#backtestBtn').classList.remove('active');
  $('#backtestBtn').textContent = '백테스트';
  $('#backtestBtn').disabled = true;

  $('#modalTicker').textContent = `${summary.symbol} · ${summary.exchange}`;
  $('#modalName').textContent = summary.name;
  $('#modalScore').textContent = Number(summary.score).toFixed(3);
  $('#modalPrice').textContent = money(summary.close, summary.currency);
  $('#modalChange').textContent = dayPct(summary.day_change_pct, 2);
  $('#modalChange').className = changeClass(summary.day_change_pct);
  $('#scoreGrid').innerHTML = '<div class="detail-loading">상세 데이터 로딩 중…</div>';
  $('#metricGrid').innerHTML = '';
  $('#chart').innerHTML = '<div class="detail-loading chart-loading-inline">차트 로딩 중…</div>';
  $('#modal').classList.add('open');
  $('#modal').setAttribute('aria-hidden','false');

  try {
    const detail = await ensureDetail(summary);
    renderStockDetail(detail);
  } catch (err) {
    console.error(err);
    $('#scoreGrid').innerHTML = '<div class="detail-error">상세 데이터를 불러오지 못했습니다.</div>';
    $('#chart').innerHTML = '';
  }
}

function renderStockDetail(stock) {
  state.selected = stock; state.chartDays = 120; state.backtestOpen = false;
  $('#backtestPanel').hidden = true;
  $('#forecastPanel').hidden = true;
  $('#backtestBtn').classList.remove('active');
  $('#backtestBtn').textContent = '백테스트';
  $('#backtestBtn').disabled = false;
  state.chartDays = 120;
  $$('#periodTabs button').forEach((b) => b.classList.toggle('active', Number(b.dataset.days) === 120));
  $('#modalTicker').textContent = `${stock.symbol} · ${stock.exchange}`;
  $('#modalName').textContent = stock.name;
  $('#modalScore').textContent = Number(stock.score).toFixed(3);
  $('#modalPrice').textContent = money(stock.close, stock.currency);
  $('#modalChange').textContent = dayPct(stock.day_change_pct, 2);
  $('#modalChange').className = changeClass(stock.day_change_pct);

  const s = stock.scores || {}, m = stock.metrics || {};
  $('#scoreGrid').innerHTML = SCORE_COLUMNS.map(([k,l]) => scoreTile(k,l,s)).join('') +
    `<div class="score-tile cap"><span>⑦ HA Cap</span><b>${Number(s.s7_ha_capped || 0).toFixed(3)}</b></div>`;

  const age = (v, suffix) => v == null ? '—' : `${v}${suffix}`;
  $('#metricGrid').innerHTML = `
    <div><span>%B</span><b>${m.percent_b == null ? '—' : Number(m.percent_b).toFixed(3)}</b></div>
    <div><span>Band Width</span><b>${pct(m.bandwidth,1)}</b></div>
    <div><span>R (5 / 115)</span><b>${m.turnover_r == null ? '—' : Number(m.turnover_r).toFixed(2)}x</b></div>
    <div><span>Bull Value</span><b>${pct(m.bullish_turnover_share,0)}</b></div>
    <div><span>Upper Swing</span><b>${age(m.upper_swing_age,'D')}</b></div>
    <div><span>PSAR</span><b>${age(m.psar_age,'D')}</b></div>
    <div><span>D / W / M HA</span><b>${age(m.daily_ha_age,'D')} · ${age(m.weekly_ha_age,'W')} · ${age(m.monthly_ha_age,'M')}</b></div>
    <div><span>20D Value</span><b>${formatCompact(m.turnover20, stock.currency)}</b></div>
  `;

  $('#modal').classList.add('open'); $('#modal').setAttribute('aria-hidden','false');
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
  const bt = stock?.backtest || {};
  const fc = bt?.forecast || {};
  const panel = $('#forecastPanel');

  if (!fc.available || !['GOOD','STRONG'].includes(bt.quality_label)) {
    panel.hidden = true;
    $('#forecastChart').innerHTML = '';
    return;
  }

  panel.hidden = false;
  $('#forecastQuality').textContent = `${bt.quality_label} · BT ${Number(bt.quality_score || 0).toFixed(0)}`;
  $('#forecastQuality').className = `forecast-quality ${bt.quality_label === 'STRONG' ? 'strong' : 'good'}`;
  $('#forecastSummary').innerHTML = [
    backtestMetric('현재가', forecastMoney(fc.current_price, stock.currency)),
    backtestMetric('20D 예상', forecastMoney(fc.expected_price_20d, stock.currency), ratioPct(fc.expected_return_20d)),
    backtestMetric('예상 범위', `${forecastMoney(fc.range_low_20d, stock.currency)} ~ ${forecastMoney(fc.range_high_20d, stock.currency)}`, `유사신호 ${fc.sample_count || 0}개`),
  ].join('');
  drawForecastChart($('#forecastChart'), fc, stock.currency);
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
  const bt = stock?.backtest || {};
  const panel = $('#backtestPanel');
  if (!bt.available) {
    $('#forecastPanel').hidden = true;
    $('#backtestRule').textContent = '데이터 부족';
    $('#backtestSummary').innerHTML = backtestMetric('Signals', '0');
    $('#backtestBody').innerHTML = '';
    return;
  }

  $('#backtestRule').textContent = `약 2Y · Next Open · ${bt.cooldown_days || 10}D cooldown`;
  $('#backtestSummary').innerHTML = [
    backtestMetric('Signals', Number(bt.signals || 0).toLocaleString()),
    backtestMetric('5D Avg', ratioPct(bt.avg_5d), `Win ${winPct(bt.win_5d)}`),
    backtestMetric('10D Avg', ratioPct(bt.avg_10d), `Win ${winPct(bt.win_10d)}`),
    backtestMetric('20D Avg', ratioPct(bt.avg_20d), `Win ${winPct(bt.win_20d)}`),
    backtestMetric('20D Median', ratioPct(bt.median_20d)),
    backtestMetric('MFE / MAE', `${ratioPct(bt.avg_mfe_20d)} / ${ratioPct(bt.avg_mae_20d)}`),
  ].join('');

  const trades = bt.trades || [];
  renderForecast(stock);

  $('#backtestBody').innerHTML = trades.length
    ? trades.map(t => `<tr>
        <td>${escapeHtml(t.signal_date || '—')}</td>
        <td>${t.score == null ? '—' : Number(t.score).toFixed(3)}</td>
        <td class="${changeClass((t.ret_5d || 0) * 100)}">${ratioPct(t.ret_5d)}</td>
        <td class="${changeClass((t.ret_10d || 0) * 100)}">${ratioPct(t.ret_10d)}</td>
        <td class="${changeClass((t.ret_20d || 0) * 100)}">${ratioPct(t.ret_20d)}</td>
        <td class="up">${ratioPct(t.mfe_20d)}</td>
        <td class="down">${ratioPct(t.mae_20d)}</td>
      </tr>`).join('')
    : `<tr><td colspan="7" class="bt-empty">해당 기간의 독립 신호가 없습니다.</td></tr>`;
}

function formatCompact(v, currency) {
  if (v == null) return '—'; const n = Number(v);
  if (currency === 'KRW') {
    if (n >= 1e12) return `${(n/1e12).toFixed(1)}조`;
    if (n >= 1e8) return `${(n/1e8).toFixed(1)}억`;
    return `₩${Math.round(n).toLocaleString('ko-KR')}`;
  }
  if (n >= 1e9) return `$${(n/1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n/1e6).toFixed(1)}M`;
  return `$${Math.round(n).toLocaleString('en-US')}`;
}

function chartRows(stock) {
  const c = stock.chart || {}, d = c.d || [];
  return d.map((date,i) => ({ date, close:c.c?.[i], mid:c.m?.[i], upper:c.u?.[i], lower:c.l?.[i] }));
}
function drawSelectedChart() {
  if (!state.selected) return;
  drawChart($('#chart'), chartRows(state.selected).slice(-state.chartDays), state.selected.currency);
}
function drawChart(el, data, currency) {
  data = data.filter((d) => [d.close,d.mid,d.upper,d.lower].every(Number.isFinite));
  if (!data.length) { el.innerHTML=''; return; }
  const W = Math.max(320, el.clientWidth || 760), H = Math.max(260, el.clientHeight || 330);
  const pad={l:8,r:58,t:15,b:22};
  const vals=data.flatMap((d)=>[d.close,d.upper,d.lower]); const min=Math.min(...vals), max=Math.max(...vals), span=Math.max(max-min,Math.abs(max)*.02,1); const lo=min-span*.08, hi=max+span*.08;
  const X=(i)=>pad.l+i/Math.max(1,data.length-1)*(W-pad.l-pad.r), Y=(v)=>pad.t+(1-(v-lo)/(hi-lo))*(H-pad.t-pad.b);
  const path=(key)=>data.map((d,i)=>`${i?'L':'M'}${X(i).toFixed(1)},${Y(d[key]).toFixed(1)}`).join(' ');
  const poly=data.map((d,i)=>`${X(i)},${Y(d.upper)}`).join(' ')+' '+[...data].reverse().map((d,ri)=>{const i=data.length-1-ri;return `${X(i)},${Y(d.lower)}`}).join(' ');
  let grid=''; for(let i=0;i<5;i++){const y=pad.t+i*(H-pad.t-pad.b)/4,v=hi-i*(hi-lo)/4,label=currency==='KRW'?Math.round(v).toLocaleString('ko-KR'):'$'+v.toFixed(v>=100?0:1);grid+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" stroke="#202631"/><text x="${W-pad.r+6}" y="${y+4}" fill="#718096" font-size="9">${label}</text>`}
  el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="bandFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7faaff" stop-opacity=".14"/><stop offset="1" stop-color="#7faaff" stop-opacity=".02"/></linearGradient></defs>${grid}<polygon points="${poly}" fill="url(#bandFill)"/><path d="${path('upper')}" fill="none" stroke="#5579b8" stroke-width="1.1"/><path d="${path('lower')}" fill="none" stroke="#5579b8" stroke-width="1.1"/><path d="${path('mid')}" fill="none" stroke="#efc46a" stroke-width="1.2"/><path d="${path('close')}" fill="none" stroke="#78f2b6" stroke-width="2"/></svg>`;
}

$('#marketTabs').addEventListener('click', (e) => { const b=e.target.closest('.market-tab'); if(b) switchCategory(b.dataset.category); });
let searchTimer;
$('#searchInput').addEventListener('input', (e) => { clearTimeout(searchTimer); searchTimer=setTimeout(()=>{state.query=e.target.value; applyFilter();},100); });
$('#reloadBtn').onclick=()=>switchCategory(state.category,true);
$('#periodTabs').addEventListener('click',(e)=>{const b=e.target.closest('button');if(!b)return;$$('#periodTabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.chartDays=Number(b.dataset.days);drawSelectedChart();});
$('#backtestBtn').onclick=()=>{
  if(!state.selected) return;
  state.backtestOpen = !state.backtestOpen;
  $('#backtestPanel').hidden = !state.backtestOpen;
  $('#backtestBtn').classList.toggle('active', state.backtestOpen);
  $('#backtestBtn').textContent = state.backtestOpen ? '백테스트 닫기' : '백테스트';
  if(state.backtestOpen) renderBacktest(state.selected);
};
$('#closeModal').onclick=()=>{$('#modal').classList.remove('open');$('#modal').setAttribute('aria-hidden','true');};
$('#modal').onclick=(e)=>{if(e.target===$('#modal'))$('#closeModal').click();};
window.addEventListener('resize',()=>{
  if($('#modal').classList.contains('open')){
    drawSelectedChart();
    if(state.backtestOpen && state.selected?.backtest?.forecast?.available) {
      drawForecastChart($('#forecastChart'), state.selected.backtest.forecast, state.selected.currency);
    }
  }
});

const observer = new IntersectionObserver((entries)=>{ if(entries[0]?.isIntersecting) renderNextBatch(); }, { rootMargin:'600px 0px' });
observer.observe($('#sentinel'));
bindResultClicks();
switchCategory('KR');
