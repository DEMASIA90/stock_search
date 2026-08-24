const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];

const CATEGORY = {
  KR: { short: '국장', dir: 'kr' },
  KR_ETF: { short: '국장ETF', dir: 'kr-etf' },
  US: { short: '미장', dir: 'us' },
  US_ETF: { short: '미장ETF', dir: 'us-etf' },
};
const CONFIGURED_DATA_ORIGIN = String(window.DTC_DATA_ORIGIN || '').trim().replace(/\/$/, '');
const IS_ANDROID_APP = location.hostname === 'localhost' || location.protocol === 'capacitor:';
const DATA_BASE = (location.hostname.endsWith('github.io') || IS_ANDROID_APP) ? (CONFIGURED_DATA_ORIGIN || '.') : '.';
const CAP_FILTER_PRESETS = {
  equity: [
    [10_000_000_000_000,'10조 이상'],[50_000_000_000_000,'50조 이상'],[100_000_000_000_000,'100조 이상'],
    [500_000_000_000_000,'500조 이상'],[1_000_000_000_000_000,'1000조 이상'],
  ],
  etf: [[0,'전체'],[100_000_000_000,'0.1조 이상'],[500_000_000_000,'0.5조 이상'],[1_000_000_000_000,'1조 이상'],[5_000_000_000_000,'5조 이상']],
};
const QUIZ_WINDOW_DAYS = 90;
const QUIZ_HIDDEN_DAYS = 30;
const QUIZ_MIN_MARKET_SIZE = 100_000_000_000_000;
const QUIZ_SHARDS = ['kr','kr-etf','us','us-etf'];
const OPINION_ORDER = {BUY:0,SHORT_BUY:1,LONG_BUY:1,HOLD:2,SELL_CONSIDER:3,SELL:4};
const OPINION_TEXT = {BUY:'매수',SHORT_BUY:'단기 매수',LONG_BUY:'장기 매수',HOLD:'HOLD',SELL_CONSIDER:'매도 고려',SELL:'매도'};

const state = {
  sheet:'KR', category:'KR', data:{KR:null,KR_ETF:null,US:null,US_ETF:null},
  capMin:{equity:100_000_000_000_000,etf:0}, selectedTicker:null, detailCache:new Map(),
  quiz:{pool:null,detailCache:new Map(),question:null,answered:false,loading:false},
};

function escapeHtml(value){return String(value??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function dataUrl(path,force=false){const clean=String(path).replace(/^\.\//,'').replace(/^\//,'');const base=DATA_BASE==='.'?'.':DATA_BASE.replace(/\/$/,'');const url=`${base}/${clean}`;return force?`${url}${url.includes('?')?'&':'?'}ts=${Date.now()}`:url;}
function numberOrNaN(v){if(v===null||v===undefined||v==='')return Number.NaN;const n=Number(v);return Number.isFinite(n)?n:Number.NaN;}
function isEtfCategory(c=state.category){return c==='KR_ETF'||c==='US_ETF';}
function sizeMode(){return isEtfCategory()?'etf':'equity';}
function currentCapMin(){return Number(state.capMin[sizeMode()]||0);}
function currentData(){return state.data[state.category];}
function stockByTicker(ticker){return (currentData()?.items||[]).find(x=>x.ticker===ticker)||null;}

async function ensureData(category,force=false){if(state.data[category]&&!force)return state.data[category];const r=await fetch(dataUrl(`data/${CATEGORY[category].dir}/summary.json`,force),{cache:force?'no-store':'default'});if(!r.ok)throw new Error(`${category} summary ${r.status}`);const d=await r.json();state.data[category]=d;return d;}
async function ensureDetail(stock,force=false){const key=`${stock.category}:${stock.ticker}`;if(state.detailCache.has(key)&&!force)return state.detailCache.get(key);const r=await fetch(dataUrl(stock.detail_path,force),{cache:force?'no-store':'default'});if(!r.ok)throw new Error(`detail ${r.status}`);const d=await r.json();state.detailCache.set(key,d);return d;}

function money(v,currency){const n=numberOrNaN(v);if(!Number.isFinite(n))return '—';return currency==='KRW'?`${Math.round(n).toLocaleString('ko-KR')}원`:`$${n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}`;}
function changeAmount(v,currency){const n=numberOrNaN(v);if(!Number.isFinite(n))return '—';const sign=n>0?'+':'';return currency==='KRW'?`${sign}${Math.round(n).toLocaleString('ko-KR')}`:`${sign}$${n.toFixed(2)}`;}
function pct(v,d=2){const n=numberOrNaN(v);if(!Number.isFinite(n))return '—';return `${n>0?'+':''}${n.toFixed(d)}%`;}
function marketSize(v){const n=numberOrNaN(v);if(!Number.isFinite(n))return '—';if(n>=1e12)return `${(n/1e12).toFixed(n>=100e12?0:1)}조`;if(n>=1e8)return `${(n/1e8).toFixed(0)}억`;return Math.round(n).toLocaleString('ko-KR');}
function changeClass(v){const n=numberOrNaN(v);return n>0?'change-up':n<0?'change-down':'';}
function stClass(v){return String(v)==='상승'?'st-up':String(v)==='하락'?'st-down':'';}
function opinionClass(text){const s=String(text||'');if(s.includes('매수'))return 'opinion-buy';if(s.includes('매도'))return 'opinion-sell';return 'opinion-hold';}
function backtestText(stock){const bt=stock?.supertrend?.backtest||{};const med=numberOrNaN(bt.median_max_return_pct);const n=Number(bt.completed_events||0);return Number.isFinite(med)?`최고수익 중위 ${pct(med,1)} · ${n}회`:'—';}

function renderCapSelect(){const select=$('#capSelect');if(!select)return;const presets=CAP_FILTER_PRESETS[sizeMode()];const active=currentCapMin();select.innerHTML=presets.map(([v,label])=>`<option value="${v}" ${Number(v)===active?'selected':''}>${escapeHtml(label)}</option>`).join('');}
function filteredItems(){const min=currentCapMin();const items=(currentData()?.items||[]).filter(s=>min<=0||(Number.isFinite(Number(s.market_size_krw))&&Number(s.market_size_krw)>=min));return [...items].sort((a,b)=>{const ra=Number(a.rank_level??OPINION_ORDER[a.opinion_code]??2),rb=Number(b.rank_level??OPINION_ORDER[b.opinion_code]??2);if(ra!==rb)return ra-rb;return (Number(b.market_size_krw)||-1)-(Number(a.market_size_krw)||-1);});}

function renderSheet(){const body=$('#sheetBody');if(!body)return;const items=filteredItems();const data=currentData();const selected=state.selectedTicker;body.innerHTML=items.map((s,i)=>{const row=i+3;const opinion=s.opinion_label||s.opinion||OPINION_TEXT[s.opinion_code]||'HOLD';const selectedClass=s.ticker===selected?' selected':'';return `<tr class="stock-row${selectedClass}" data-ticker="${escapeHtml(s.ticker)}"><th class="row-number">${row}</th>
<td class="stock-name-cell">${escapeHtml(s.name)} <small>${escapeHtml(s.symbol||s.ticker)}</small></td>
<td>${escapeHtml(s.sector||'—')}</td>
<td class="num">${money(s.close,s.currency)}</td>
<td class="num ${changeClass(s.day_change_amount)}">${changeAmount(s.day_change_amount,s.currency)}</td>
<td class="num ${changeClass(s.day_change_pct)}">${pct(s.day_change_pct,2)}</td>
<td class="num">${marketSize(s.market_size_krw)}</td>
<td class="center ${stClass(s.st_d_direction)}">${escapeHtml(s.st_d_direction||'—')}</td>
<td class="center ${stClass(s.st_w_direction)}">${escapeHtml(s.st_w_direction||'—')}</td>
<td class="num">${Number.isFinite(numberOrNaN(s.adx))?Number(s.adx).toFixed(1):'—'}</td>
<td class="center ${opinionClass(opinion)}">${escapeHtml(opinion)}</td>
<td class="backtest-cell">${escapeHtml(backtestText(s))}</td>
<td class="chart-cell"><div class="${s.ticker===selected?'row-chart':'row-chart-placeholder'}" data-row-chart>${s.ticker===selected?'<div class="chart-loading">차트를 불러오는 중입니다.</div>':'행을 클릭하면 차트 표시'}</div></td></tr>`;}).join('');
const status=`${CATEGORY[state.category].short} · ${data?.market_date||'—'} · ${items.length.toLocaleString()}개 · 가격수신 ${Number(data?.coverage_pct||0).toFixed(1)}%`;
$('#sheetStatusCell').textContent=status;$('#bottomStatus').textContent=status;
if(selected){const stock=stockByTicker(selected);const tr=body.querySelector(`tr[data-ticker="${CSS.escape(selected)}"]`);if(stock&&tr)void hydrateSelectedRow(stock,tr);}
}

async function selectRow(ticker){state.selectedTicker=ticker;const stock=stockByTicker(ticker);if(!stock)return;$('#nameBox').textContent='L'+(filteredItems().findIndex(x=>x.ticker===ticker)+3);$('#formulaInput').value=`${stock.name} | ${stock.opinion_label||stock.opinion||''}`;renderSheet();}
async function hydrateSelectedRow(stock,row){const box=$('[data-row-chart]',row);if(!box)return;try{const detail=await ensureDetail(stock);if(!row.isConnected||state.selectedTicker!==stock.ticker)return;drawSheetChart(box,detail,stock);}catch(err){console.error(err);box.innerHTML='<div class="chart-empty">차트를 불러오지 못했습니다.</div>';}}

function drawSheetChart(el,detail,stock){const st=detail?.supertrend||{};const rows=st.chart||[];if(rows.length<15){el.innerHTML='<div class="chart-empty">차트 데이터 부족</div>';return;}const vals=rows.flatMap(r=>[numberOrNaN(r.low),numberOrNaN(r.high),numberOrNaN(r.supertrend),numberOrNaN(r.weekly_supertrend)]).filter(Number.isFinite);let lo=Math.min(...vals),hi=Math.max(...vals);if(!(hi>lo)){lo*=.99;hi*=1.01;}const ext=(hi-lo)*.06||1;lo-=ext;hi+=ext;const W=720,H=286,pad={l:9,r:55,t:13,b:22},plotW=W-pad.l-pad.r,plotH=H-pad.t-pad.b,step=plotW/rows.length,X=i=>pad.l+(i+.5)*step,Y=v=>pad.t+(hi-v)*plotH/(hi-lo);let grid='';for(let g=0;g<4;g++){const y=pad.t+g*plotH/3,v=hi-g*(hi-lo)/3,label=stock.currency==='KRW'?Math.round(v).toLocaleString('ko-KR'):`$${v.toFixed(Math.abs(v)>=100?0:1)}`;grid+=`<line x1="${pad.l}" x2="${W-pad.r}" y1="${y}" y2="${y}" class="chart-grid"/><text x="${W-pad.r+5}" y="${y+3}" class="price-axis">${label}</text>`;}const bw=Math.max(1.2,Math.min(4.8,step*.58));let candles='';rows.forEach((r,i)=>{const o=Number(r.open),h=Number(r.high),l=Number(r.low),c=Number(r.close);if(![o,h,l,c].every(Number.isFinite))return;const x=X(i),yo=Y(o),yc=Y(c),yh=Y(h),yl=Y(l),up=c>=o,cls=up?'chart-candle-up':'chart-candle-down',top=Math.min(yo,yc),bh=Math.max(1.1,Math.abs(yc-yo));candles+=`<line x1="${x}" x2="${x}" y1="${yh}" y2="${yl}" class="chart-candle-wick ${cls}"/><rect x="${x-bw/2}" y="${top}" width="${bw}" height="${bh}" class="${cls}"><title>${escapeHtml(r.date)} O ${o} H ${h} L ${l} C ${c}</title></rect>`;});
function paths(valueKey,dirKey,upClass,downClass){let up='',down='',u=false,d=false;rows.forEach((r,i)=>{const v=numberOrNaN(r[valueKey]),dir=numberOrNaN(r[dirKey]);if(!Number.isFinite(v)){u=d=false;return;}if(dir>0){up+=`${u?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;u=true;d=false;}else if(dir<0){down+=`${d?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)} `;d=true;u=false;}else{u=d=false;}});return `<path d="${up}" class="${upClass}"/><path d="${down}" class="${downClass}"/>`;}
const dates=`<text x="${X(0)}" y="${H-4}" text-anchor="start" class="date-axis">${escapeHtml(rows[0].date.slice(5))}</text><text x="${X(rows.length-1)}" y="${H-4}" text-anchor="end" class="date-axis">${escapeHtml(rows.at(-1).date.slice(5))}</text>`;const isUS=String(stock.category||'').startsWith('US');el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}${candles}${paths('supertrend','direction','st-d-up','st-d-down')}${paths('weekly_supertrend','weekly_direction','st-w-up','st-w-down')}${dates}</svg><div class="chart-legend"><span>ST_D 실선</span><span>ST_W 점선</span><span>양봉 빨강 · 음봉 파랑</span></div><div class="chart-open-hint">${isUS?'TradingView':'토스증권'} 차트 열기 ↗</div><button class="chart-open-hit" type="button" data-open-external="${escapeHtml(stock.ticker)}" aria-label="외부 차트 열기"></button>`;}

function tossProductCode(stock){const existing=String(stock?.toss_product_code||'').trim().toUpperCase();if(existing)return existing;const raw=String(stock?.symbol||stock?.ticker||'').trim().toUpperCase().replace(/\.(KS|KQ)$/i,'');if(/^[0-9A-Z]{6}$/.test(raw))return `A${raw}`;if(/^A[0-9A-Z]{6}$/.test(raw))return raw;return '';}
function openTossChart(stock){const code=tossProductCode(stock);window.open(code?`https://www.tossinvest.com/stocks/${encodeURIComponent(code)}/order`:'https://www.tossinvest.com/','_blank','noopener,noreferrer');}
function tradingViewSymbol(stock){const raw=String(stock?.symbol||stock?.ticker||'').trim().replace(/-/g,'.').replace(/\.(KS|KQ)$/i,'');const ex=String(stock?.exchange||'').toUpperCase();let p='NASDAQ';if(ex==='NYSE')p='NYSE';else if(ex.includes('AMERICAN')||ex.includes('ARCA')||ex==='AMEX')p='AMEX';else if(ex.includes('CBOE')||ex.includes('BZX')||ex==='BATS')p='BATS';return `${p}:${raw}`;}
function openTradingView(stock){window.open(`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tradingViewSymbol(stock))}`,'_blank','noopener,noreferrer');}
function openExternal(stock){if(String(stock?.category||'').startsWith('US'))openTradingView(stock);else openTossChart(stock);}

async function switchSheet(sheet,force=false){state.sheet=sheet;$$('.sheet-tab').forEach(b=>b.classList.toggle('active',b.dataset.sheet===sheet));if(sheet==='QUIZ'){$('#marketSheetView').hidden=true;$('#quizSheetView').hidden=false;$('#nameBox').textContent='A1';$('#formulaInput').value='Quiz · 1문제 / 보기 5개';return;}$('#quizSheetView').hidden=true;$('#marketSheetView').hidden=false;state.category=sheet;state.selectedTicker=null;renderCapSelect();$('#sheetBody').innerHTML='<tr><th class="row-number">3</th><td colspan="12">데이터를 불러오는 중입니다.</td></tr>';try{await ensureData(sheet,force);renderCapSelect();renderSheet();}catch(err){console.error(err);$('#sheetBody').innerHTML='<tr><th class="row-number">3</th><td colspan="12">데이터를 불러오지 못했습니다.</td></tr>';$('#sheetStatusCell').textContent='데이터 로드 실패';}}

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
  const multipliers = [0.72, 0.84, 1.16, 1.32];
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
  // network search is required to manufacture the four distractors (5 choices total).
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

    const correctIndex = quizRandomInt(0, 4);
    const options = [];
    let di = 0;
    for (let i=0;i<5;i++) options.push(i === correctIndex ? built.correct : built.distractors[di++]);

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
  $('#quizNumber').textContent='QUIZ';
  $('#quizIdentity').textContent=state.quiz.answered
    ? `${q.stock.name} (${q.stock.symbol || q.stock.ticker}) · ${q.rows[0].date} ~ ${q.rows[q.rows.length-1].date}`
    : '종목 · 기간 비공개';
  $('#quizSegmentLabel').textContent='1문제 · 보기 5개 · 다음 30거래일 가림';
  const instruction=$('#quizInstructionSub');
  if(instruction){
    instruction.textContent=state.quiz.answered
      ? '제출 후 실제 다음 30거래일 캔들과 정답을 공개합니다.'
      : '앞 60거래일을 보고 5개 보기 중 다음 30거래일을 고르세요. 보기를 누르면 빈 구간에 미리 적용됩니다.';
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
    feedback.innerHTML=`<b>${ok?'정답입니다.':'오답입니다.'}</b> 정답은 ${q.correctIndex+1}번입니다. 실제 종목은 ${escapeHtml(q.stock.name)} (${escapeHtml(q.stock.symbol || q.stock.ticker)})이며, 가려진 30거래일 캔들을 공개했습니다.`;
  }else{
    feedback.hidden=true;
    feedback.textContent='';
  }
  const newBtn=$('#newQuizBtn');
  if(newBtn && !state.quiz.loading){
    newBtn.disabled = !state.quiz.answered && Boolean(state.quiz.question);
    newBtn.textContent = state.quiz.answered ? '새 문제' : '문제 시작';
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
    renderQuizQuestion();
  }catch(err){
    console.error('quiz',err);
    $('#quizStatus').hidden=false;
    $('#quizStatus').textContent='퀴즈 데이터가 아직 없습니다. 최신 코드 배포 후 ALL · FULL 스캔을 한 번 실행해 주세요.';
    $('#quizGame').hidden=true;
  }finally{
    state.quiz.loading=false;
    if(btn){
      btn.disabled = !state.quiz.answered && Boolean(state.quiz.question);
      btn.textContent = state.quiz.answered ? '새 문제' : (state.quiz.question ? '현재 문제' : '문제 시작');
    }
  }
}



$('#capSelect')?.addEventListener('change',e=>{state.capMin[sizeMode()]=Number(e.target.value)||0;state.selectedTicker=null;renderSheet();});
$('.sheet-nav')?.addEventListener('click',e=>{const b=e.target.closest('[data-sheet]');if(b)void switchSheet(b.dataset.sheet);});
$('#sheetBody')?.addEventListener('click',e=>{const external=e.target.closest('[data-open-external]');if(external){e.stopPropagation();const stock=stockByTicker(external.dataset.openExternal);if(stock)openExternal(stock);return;}const row=e.target.closest('tr[data-ticker]');if(row)void selectRow(row.dataset.ticker);});
$('#newQuizBtn')?.addEventListener('click',()=>void newQuizQuestion(false));
$('#quizChoices')?.addEventListener('click',e=>{const b=e.target.closest('[data-quiz-choice]');if(!b||state.quiz.answered||!state.quiz.question)return;const i=Number(b.dataset.quizChoice);if(!Number.isInteger(i)||i<0||i>4)return;state.quiz.question.selectedIndex=i;renderQuizQuestion();});
$('#quizSubmitBtn')?.addEventListener('click',()=>{if(!state.quiz.question||state.quiz.answered||!Number.isInteger(state.quiz.question.selectedIndex))return;state.quiz.answered=true;renderQuizQuestion();});

window.addEventListener('resize',()=>{const stock=stockByTicker(state.selectedTicker);const row=state.selectedTicker?$('#sheetBody')?.querySelector(`tr[data-ticker="${CSS.escape(state.selectedTicker)}"]`):null;const detail=stock?state.detailCache.get(`${stock.category}:${stock.ticker}`):null;if(stock&&row&&detail)drawSheetChart($('[data-row-chart]',row),detail,stock);});

void switchSheet('KR');
