const $ = (q) => document.querySelector(q);
const $$ = (q) => [...document.querySelectorAll(q)];

const SCORE_LABELS = {
  bollinger: '볼린저',
  rsi: 'RSI',
  volume: '거래량',
  reversal: '반전',
  macd: 'MACD',
};

const state = { market: 'KR', sort: 'rank', data: { KR: null, US: null }, selected: null, chartDays: 180 };

function money(v, currency) {
  if (v == null) return '—';
  return currency === 'KRW'
    ? '₩' + Math.round(v).toLocaleString('ko-KR')
    : '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pct(v, digits = 1) {
  if (v == null) return '—';
  const n = Number(v);
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function changeClass(v) {
  return Number(v) > 0 ? 'up' : Number(v) < 0 ? 'down' : '';
}

function currentData() {
  return state.data[state.market];
}

function sortedItems(items) {
  const out = [...items];
  if (state.sort === 'gap') out.sort((a, b) => Math.abs(a.gap_pct ?? 999) - Math.abs(b.gap_pct ?? 999));
  else if (state.sort === 'rsi') out.sort((a, b) => (a.rsi14 ?? 999) - (b.rsi14 ?? 999));
  else if (state.sort === 'volume') out.sort((a, b) => (b.volume_ratio ?? 0) - (a.volume_ratio ?? 0));
  else out.sort((a, b) => a.rank - b.rank);
  return out;
}

function render() {
  const data = currentData();
  $('#marketLabel').textContent = state.market === 'KR' ? 'KOREA' : 'UNITED STATES';
  if (!data) {
    $('#status').style.display = 'block';
    $('#status').textContent = '데이터가 아직 생성되지 않았습니다.';
    $('#stockList').innerHTML = '';
    $('#marketDate').textContent = '—';
    $('#coverage').textContent = '—';
    return;
  }

  const dt = data.generated_at_utc ? new Date(data.generated_at_utc) : null;
  $('#updated').textContent = dt && !Number.isNaN(dt.getTime())
    ? dt.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '—';
  $('#marketDate').textContent = data.market_date ? `${data.market_date} 기준` : '—';
  $('#coverage').textContent = `${Number(data.universe_count).toLocaleString()}종목 · ${Number(data.coverage_pct).toFixed(0)}% 수집`;

  const items = sortedItems(data.top20 || []);
  $('#status').style.display = items.length ? 'none' : 'block';
  $('#stockList').innerHTML = items.map((s) => `
    <button class="stock-card" data-ticker="${s.ticker}">
      <div class="rank">${String(s.rank).padStart(2, '0')}</div>
      <div class="stock-main">
        <div class="stock-name"><strong>${escapeHtml(s.name)}</strong><span class="grade ${s.grade}">${s.grade}</span></div>
        <div class="ticker">${escapeHtml(s.symbol)} · ${escapeHtml(s.exchange)}</div>
        <div class="chips">
          <span>Band <b>${pct(s.gap_pct, 1)}</b></span>
          <span>RSI <b>${s.rsi14?.toFixed(1) ?? '—'}</b></span>
          <span>Vol <b>${s.volume_ratio?.toFixed(2) ?? '—'}x</b></span>
        </div>
      </div>
      <div class="stock-right">
        <strong>${money(s.close, s.currency)}</strong>
        <div class="day-change ${changeClass(s.day_change_pct)}">${pct(s.day_change_pct, 2)}</div>
        <div class="score">${s.score.toFixed(1)}</div>
      </div>
    </button>
  `).join('');

  $$('.stock-card').forEach((button) => {
    button.onclick = () => openStock((data.top20 || []).find((x) => x.ticker === button.dataset.ticker));
  });
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function openStock(stock) {
  if (!stock) return;
  state.selected = stock;
  state.chartDays = 180;
  $$('#periodTabs button').forEach((b) => b.classList.toggle('active', Number(b.dataset.days) === 180));
  $('#modal').classList.add('open');
  $('#modal').setAttribute('aria-hidden', 'false');
  $('#modalTicker').textContent = `${stock.symbol} · ${stock.exchange}`;
  $('#modalName').textContent = stock.name;
  $('#modalScore').textContent = stock.score.toFixed(1);
  $('#modalPrice').textContent = money(stock.close, stock.currency);
  $('#modalChange').textContent = pct(stock.day_change_pct, 2);
  $('#modalChange').className = changeClass(stock.day_change_pct);

  $('#scoreBars').innerHTML = Object.entries(stock.scores).map(([key, value]) => `
    <div class="bar-row">
      <span>${SCORE_LABELS[key]}</span>
      <div class="bar"><i style="width:${Math.max(0, Math.min(100, value / 20 * 100))}%"></i></div>
      <b>${value.toFixed(1)}</b>
    </div>
  `).join('');

  $('#quoteGrid').innerHTML = `
    <div><span>Lower</span><b>${money(stock.lower, stock.currency)}</b></div>
    <div><span>Band 거리</span><b>${pct(stock.gap_pct, 2)}</b></div>
    <div><span>RSI 14</span><b>${stock.rsi14?.toFixed(1) ?? '—'}</b></div>
    <div><span>거래량</span><b>${stock.volume_ratio?.toFixed(2) ?? '—'}x</b></div>
    <div><span>3일</span><b>${pct(stock.ret3_pct, 2)}</b></div>
    <div><span>5일</span><b>${pct(stock.ret5_pct, 2)}</b></div>
    <div><span>SMA20</span><b>${money(stock.sma20, stock.currency)}</b></div>
    <div><span>SMA60 이격</span><b>${pct(stock.trend60_pct, 1)}</b></div>
  `;
  drawSelectedChart();
}

function drawSelectedChart() {
  if (!state.selected) return;
  const source = state.selected.chart || [];
  const data = source.slice(-state.chartDays);
  drawChart($('#chart'), data, state.selected.currency);
}

function drawChart(el, data, currency) {
  if (!data.length) { el.innerHTML = ''; return; }
  const W = Math.max(320, el.clientWidth || 700), H = Math.max(260, el.clientHeight || 330);
  const pad = { l: 8, r: 58, t: 16, b: 24 };
  const values = data.flatMap((d) => [d.close, d.lower, d.upper].filter(Number.isFinite));
  const min = Math.min(...values), max = Math.max(...values), span = Math.max(max - min, Math.abs(max) * .02, 1);
  const lo = min - span * .08, hi = max + span * .08;
  const X = (i) => pad.l + i / Math.max(1, data.length - 1) * (W - pad.l - pad.r);
  const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const path = (key) => data.map((d, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(d[key]).toFixed(1)}`).join(' ');
  const polygon = data.map((d, i) => `${X(i)},${Y(d.upper)}`).join(' ') + ' ' + [...data].reverse().map((d, ri) => {
    const i = data.length - 1 - ri; return `${X(i)},${Y(d.lower)}`;
  }).join(' ');
  let grid = '';
  for (let i = 0; i < 5; i++) {
    const y = pad.t + i * (H - pad.t - pad.b) / 4;
    const value = hi - i * (hi - lo) / 4;
    const label = currency === 'KRW' ? Math.round(value).toLocaleString('ko-KR') : '$' + value.toFixed(value >= 100 ? 0 : 1);
    grid += `<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" stroke="#202631"/><text x="${W-pad.r+6}" y="${y+4}" fill="#718096" font-size="9">${label}</text>`;
  }
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="bandFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7aa8ff" stop-opacity=".14"/><stop offset="1" stop-color="#7aa8ff" stop-opacity=".02"/></linearGradient></defs>${grid}<polygon points="${polygon}" fill="url(#bandFill)"/><path d="${path('upper')}" fill="none" stroke="#5579b8" stroke-width="1.1"/><path d="${path('lower')}" fill="none" stroke="#5579b8" stroke-width="1.1"/><path d="${path('sma20')}" fill="none" stroke="#f0c36b" stroke-width="1.2"/><path d="${path('close')}" fill="none" stroke="#79efb6" stroke-width="2"/></svg>`;
}

async function loadData(force = false) {
  $('#status').style.display = 'block';
  $('#status').textContent = '데이터를 불러오는 중입니다.';
  const ts = force ? `?ts=${Date.now()}` : '';
  const loads = await Promise.allSettled([
    fetch(`./data/kr.json${ts}`).then((r) => { if (!r.ok) throw new Error(); return r.json(); }),
    fetch(`./data/us.json${ts}`).then((r) => { if (!r.ok) throw new Error(); return r.json(); }),
  ]);
  state.data.KR = loads[0].status === 'fulfilled' ? loads[0].value : null;
  state.data.US = loads[1].status === 'fulfilled' ? loads[1].value : null;
  render();
}

$('#marketTabs').onclick = (event) => {
  const button = event.target.closest('.market-tab'); if (!button) return;
  $$('.market-tab').forEach((b) => b.classList.remove('active'));
  button.classList.add('active'); state.market = button.dataset.market; state.sort = 'rank';
  $$('.sort-btn').forEach((b) => b.classList.toggle('active', b.dataset.sort === 'rank'));
  render();
};

$('.toolbar').onclick = (event) => {
  const button = event.target.closest('.sort-btn'); if (!button) return;
  $$('.sort-btn').forEach((b) => b.classList.remove('active')); button.classList.add('active');
  state.sort = button.dataset.sort; render();
};

$('#periodTabs').onclick = (event) => {
  const button = event.target.closest('button'); if (!button) return;
  $$('#periodTabs button').forEach((b) => b.classList.remove('active')); button.classList.add('active');
  state.chartDays = Number(button.dataset.days); drawSelectedChart();
};

$('#closeModal').onclick = () => $('#modal').classList.remove('open');
$('#modal').onclick = (event) => { if (event.target === $('#modal')) $('#closeModal').click(); };
$('#reloadBtn').onclick = () => loadData(true);
window.addEventListener('resize', () => { if ($('#modal').classList.contains('open')) drawSelectedChart(); });

loadData(false);
