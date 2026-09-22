import { decryptEnvelope } from "./crypto.mjs";

const $ = (selector) => document.querySelector(selector);
let encryptedEnvelope = null;
let portfolio = null;
let watchlist = null;

const won = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const qty = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 6 });
const pct = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" });

function number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value, currency = "KRW") {
  return currency === "USD" ? usd.format(number(value)) : won.format(number(value));
}

function percent(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return `${parsed > 0 ? "+" : ""}${parsed.toFixed(digits)}%`;
}

function marketCap(value) {
  const amount = number(value);
  if (amount >= 1e12) return `${(amount / 1e12).toFixed(amount >= 1e14 ? 0 : 1)}조`;
  if (amount >= 1e8) return `${(amount / 1e8).toFixed(0)}억`;
  return amount ? won.format(amount) : "—";
}

function setText(selector, value) {
  const target = $(selector);
  if (target) target.textContent = value;
}

function compactDate(value) {
  return String(value || "").replace(/[^0-9]/g, "").slice(0, 8);
}

function dateText(value, includeTime = false) {
  if (!value) return "—";
  const compact = String(value).replace(/[^0-9]/g, "");
  if (/^\d{8}/.test(compact)) {
    const base = `${compact.slice(0, 4)}.${compact.slice(4, 6)}.${compact.slice(6, 8)}`;
    if (includeTime && compact.length >= 12) return `${base} ${compact.slice(8, 10)}:${compact.slice(10, 12)}`;
    return base;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric", month: "2-digit", day: "2-digit",
    ...(includeTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function pnlClass(value) {
  return number(value) > 0 ? "positive" : number(value) < 0 ? "negative" : "";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function activateTab(name) {
  const isWatchlist = name === "watchlist";
  $("#watchlistTab").classList.toggle("is-active", isWatchlist);
  $("#portfolioTab").classList.toggle("is-active", !isWatchlist);
  $("#watchlistTab").setAttribute("aria-selected", String(isWatchlist));
  $("#portfolioTab").setAttribute("aria-selected", String(!isWatchlist));
  $("#watchlistView").classList.toggle("is-hidden", !isWatchlist);
  $("#portfolioView").classList.toggle("is-hidden", isWatchlist);
  history.replaceState(null, "", isWatchlist ? "#watchlist" : "#portfolio");
}

function trendBadge(direction, label = "") {
  const known = direction === "UP" || direction === "DOWN";
  const text = direction === "UP" ? "상승" : direction === "DOWN" ? "하락" : "미확인";
  return `<span class="trend ${known ? direction.toLowerCase() : "unknown"}">${escapeHtml(label)}${text}</span>`;
}

function slopeBadge(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '<span class="trend unknown">미확인</span>';
  return `<span class="trend ${number(value) > 0 ? "up" : number(value) < 0 ? "down" : "flat"}">${number(value) > 0 ? "↗" : number(value) < 0 ? "↘" : "→"} ${percent(value)}</span>`;
}

function bandBadge(label) {
  const classes = { "최하단": "bottom-most", "하단": "bottom", "중단": "middle", "상단": "top", "최상단": "top-most" };
  return `<span class="band ${classes[label] || "unknown"}">${escapeHtml(label || "미확인")}</span>`;
}


function technicalPrice(value, currency = "KRW") {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return currency === "USD" ? usd.format(parsed) : won.format(parsed);
}

function closeTechnicalChart() {
  const modal = $("#technicalChartModal");
  if (!modal) return;
  modal.classList.add("is-hidden");
  if ($("#pinModal")?.classList.contains("is-hidden")) document.body.classList.remove("modal-open");
}

function technicalDomain(rows) {
  const values = [];
  rows.forEach((row) => {
    [row.low, row.high, row.bb_lower, row.bb_middle, row.bb_upper, row.st_14_3].forEach((value) => {
      const parsed = Number(value);
      if (Number.isFinite(parsed) && parsed > 0) values.push(parsed);
    });
  });
  if (!values.length) return [0, 1];
  let low = Math.min(...values); let high = Math.max(...values);
  const spread = Math.max((high - low) * 0.08, Math.abs(high) * 0.015, 0.01);
  return [Math.max(0, low - spread), high + spread];
}

function technicalLine(rows, key, x, y) {
  const segments = [];
  let current = [];
  rows.forEach((row, index) => {
    const value = Number(row[key]);
    if (Number.isFinite(value) && value > 0) current.push(`${x(index)},${y(value)}`);
    else if (current.length) { segments.push(current.join(" ")); current = []; }
  });
  if (current.length) segments.push(current.join(" "));
  return segments;
}

function openTechnicalChart(item) {
  const rows = (Array.isArray(item?.chart_bars) ? item.chart_bars : [])
    .filter((row) => Number(row?.close) > 0)
    .slice(-120);
  const modal = $("#technicalChartModal");
  const host = $("#technicalChartHost");
  if (!modal || !host) return;
  setText("#technicalChartTitle", item?.name || item?.code || "종목 차트");
  setText("#technicalChartMeta", `${item?.code || "—"} · ${item?.market || "—"} · ${rows.length ? `${dayLabel(rows[0].date)} ~ ${dayLabel(rows.at(-1).date)}` : "차트 데이터 없음"}`);
  document.body.classList.add("modal-open");
  modal.classList.remove("is-hidden");
  if (!rows.length) {
    host.innerHTML = '<div class="chart-empty">이 종목의 가격 차트 데이터가 아직 없습니다.<br>다음 관심종목/포트폴리오 업데이트 후 다시 확인해 주세요.</div>';
    return;
  }

  const currency = item?.currency || (String(item?.market).toUpperCase() === "US" ? "USD" : "KRW");
  const width = 1020; const height = 520;
  const pad = { top: 22, right: 24, bottom: 48, left: 88 };
  const [low, high] = technicalDomain(rows);
  const x = (index) => pad.left + ((index + 0.5) / rows.length) * (width - pad.left - pad.right);
  const y = (value) => height - pad.bottom - ((value - low) / Math.max(1e-9, high - low)) * (height - pad.top - pad.bottom);
  const yTicks = createTicks(low, high, 5);
  const candleSpace = (width - pad.left - pad.right) / rows.length;
  const candleWidth = Math.max(2.2, Math.min(8, candleSpace * 0.62));
  const xTickIndexes = [...new Set([0, Math.floor((rows.length - 1) * .25), Math.floor((rows.length - 1) * .5), Math.floor((rows.length - 1) * .75), rows.length - 1])];
  const bbUpper = technicalLine(rows, "bb_upper", x, y);
  const bbMiddle = technicalLine(rows, "bb_middle", x, y);
  const bbLower = technicalLine(rows, "bb_lower", x, y);
  const stSegments = rows.slice(1).map((row, index) => {
    const previous = rows[index];
    const a = Number(previous.st_14_3); const b = Number(row.st_14_3);
    if (!Number.isFinite(a) || !Number.isFinite(b) || a <= 0 || b <= 0) return "";
    return `<line class="technical-st ${row.st_trend === "DOWN" ? "down" : "up"}" x1="${x(index)}" y1="${y(a)}" x2="${x(index + 1)}" y2="${y(b)}"/>`;
  }).join("");

  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(item?.name || item?.code || "종목")} 캔들 차트">
    ${yTicks.map((tick) => `<g><line class="technical-grid" x1="${pad.left}" y1="${y(tick)}" x2="${width - pad.right}" y2="${y(tick)}"/><text class="technical-axis-label" x="${pad.left - 10}" y="${y(tick) + 4}" text-anchor="end">${escapeHtml(technicalPrice(tick, currency))}</text></g>`).join("")}
    ${xTickIndexes.map((index) => `<text class="technical-axis-label" x="${x(index)}" y="${height - 16}" text-anchor="middle">${escapeHtml(dayLabel(rows[index].date, true))}</text>`).join("")}
    ${bbUpper.map((points) => `<polyline class="technical-bb outer" points="${points}"/>`).join("")}
    ${bbMiddle.map((points) => `<polyline class="technical-bb middle" points="${points}"/>`).join("")}
    ${bbLower.map((points) => `<polyline class="technical-bb outer" points="${points}"/>`).join("")}
    ${stSegments}
    ${rows.map((row, index) => {
      const open = Number(row.open); const close = Number(row.close); const highValue = Number(row.high); const lowValue = Number(row.low);
      const up = close >= open;
      const top = y(Math.max(open, close)); const bottom = y(Math.min(open, close));
      const bodyHeight = Math.max(1.6, bottom - top);
      return `<g class="technical-candle ${up ? "up" : "down"}" data-index="${index}"><line x1="${x(index)}" y1="${y(highValue)}" x2="${x(index)}" y2="${y(lowValue)}"/><rect x="${x(index) - candleWidth / 2}" y="${top}" width="${candleWidth}" height="${bodyHeight}" rx="1"/></g>`;
    }).join("")}
  </svg><div class="technical-hover" id="technicalHover"></div>`;

  const hover = host.querySelector("#technicalHover");
  host.querySelectorAll(".technical-candle").forEach((node) => {
    const row = rows[Number(node.dataset.index)];
    const show = (event) => {
      if (!hover) return;
      hover.innerHTML = `<strong>${escapeHtml(dayLabel(row.date))}</strong><span>O ${escapeHtml(technicalPrice(row.open, currency))} · H ${escapeHtml(technicalPrice(row.high, currency))}</span><span>L ${escapeHtml(technicalPrice(row.low, currency))} · C ${escapeHtml(technicalPrice(row.close, currency))}</span><span>ST14·3 ${escapeHtml(technicalPrice(row.st_14_3, currency))} · ${row.st_trend === "DOWN" ? "하락" : "상승"}</span>`;
      const rect = host.getBoundingClientRect();
      hover.style.left = `${Math.max(8, Math.min(event.clientX - rect.left + 12, host.clientWidth - 220))}px`;
      hover.style.top = `${Math.max(8, Math.min(event.clientY - rect.top - 76, host.clientHeight - 96))}px`;
      hover.classList.add("is-visible");
    };
    node.addEventListener("mouseenter", show);
    node.addEventListener("mousemove", show);
  });
  host.addEventListener("mouseleave", () => hover?.classList.remove("is-visible"));
}

function attachRowChartHandlers(selector, rows) {
  document.querySelectorAll(selector).forEach((node) => {
    const item = rows[Number(node.dataset.rowIndex)];
    if (!item) return;
    node.addEventListener("click", () => openTechnicalChart(item));
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openTechnicalChart(item); }
    });
  });
}

function renderWatchlistRows() {
  const allRows = Array.isArray(watchlist?.items) ? watchlist.items : [];
  const market = $("#watchMarketFilter").value;
  const band = $("#watchBandFilter").value;
  const rows = allRows.filter((item) => {
    const marketMatch = market === "ALL" || item.market === market || (market === "ETF" && item.kind === "leveraged_etf");
    return marketMatch && (band === "ALL" || item.band === band);
  });
  $("#watchlistEmpty").classList.toggle("is-hidden", rows.length > 0);
  $("#watchlistBody").innerHTML = rows.map((item, index) => {
    const currency = item.currency || (item.market === "US" ? "USD" : "KRW");
    return `<tr class="instrument-row clickable-row ${item.band === "최하단" ? "priority-row" : ""}" data-row-index="${index}" tabindex="0" title="클릭하여 캔들·Bollinger·Supertrend 차트 보기">
      <td data-label="#"><span class="rank">${index + 1}</span></td>
      <td data-label="종목" class="mobile-span-all"><div class="instrument"><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)} · ${item.kind === "leveraged_etf" ? "3X ETF" : escapeHtml(item.market)}${item.stale ? " · 이전 데이터" : ""}</span></div></td>
      <td data-label="밴드">${bandBadge(item.band_display || item.band)}</td>
      <td data-label="점수" class="numeric"><span class="score">${number(item.score).toFixed(0)}</span><small>/50</small></td>
      <td data-label="ST 10·3">${trendBadge(item.st_10_3)}</td><td data-label="ST 20·4">${trendBadge(item.st_20_4)}</td>
      <td data-label="60일선">${slopeBadge(item.ma60_slope_pct)}</td><td data-label="200일선">${slopeBadge(item.ma200_slope_pct)}</td>
      <td data-label="현재가" class="numeric"><strong>${money(item.price, currency)}</strong><small class="${pnlClass(item.change_pct)}">${percent(item.change_pct)}</small></td>
      <td data-label="60일 평균 대비" class="numeric ${pnlClass(item.vs_sma60_pct)}"><strong>${percent(item.vs_sma60_pct)}</strong></td>
      <td data-label="섹터"><span class="sector-chip">${escapeHtml(item.sector || "기타")}</span></td>
      <td data-label="시가총액" class="numeric">${item.kind === "leveraged_etf" ? "3X ETF" : marketCap(item.market_cap_krw)}</td>
    </tr>`;
  }).join("");
  attachRowChartHandlers("#watchlistBody .instrument-row", rows);
}

function renderWatchlist(data) {
  watchlist = data;
  const rows = Array.isArray(data.items) ? data.items : [];
  setText("#watchUpdatedAt", dateText(data.updated_at, true));
  setText("#watchSource", data.source || data.message || "NHPLUG 시세");
  setText("#watchEligible", `${rows.length}개`);
  setText("#watchScanned", `후보 ${data.scanned_count || 0}개 분석`);
  setText("#watchBottomCount", `${rows.filter((item) => item.band === "최하단").length}개`);
  setText("#watchTopScore", rows.length ? `${Math.max(...rows.map((item) => number(item.score)))}점` : "—");
  const warnings = Array.isArray(data.warnings) ? [...data.warnings] : [];
  const updatedAt = new Date(data.updated_at);
  if (!Number.isNaN(updatedAt.getTime()) && Date.now() - updatedAt.getTime() > 2.5 * 60 * 60 * 1000) {
    warnings.unshift(`관심종목 자동 업데이트가 2시간 이상 지연되었습니다. 마지막 계산: ${dateText(data.updated_at, true)}`);
  }
  $("#watchWarningPanel").classList.toggle("is-hidden", warnings.length === 0);
  $("#watchWarnings").innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  renderWatchlistRows();
}

async function loadWatchlist() {
  try {
    const response = await fetch(`./data/watchlist.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`관심종목 데이터를 불러오지 못했습니다 (${response.status}).`);
    renderWatchlist(await response.json());
  } catch (error) {
    renderWatchlist({ items: [], warnings: [error.message], message: error.message });
  }
}

function openPinModal() {
  const message = $("#pinMessage");
  const button = $("#unlockButton");
  $("#pin").value = "";
  message.textContent = "";
  button.disabled = false;
  if (!encryptedEnvelope) {
    message.textContent = "암호화 데이터를 불러오는 중입니다…";
    button.disabled = true;
  } else if (encryptedEnvelope.empty) {
    message.textContent = encryptedEnvelope.message || "먼저 자산 업데이트 배치를 실행하세요.";
    button.disabled = true;
  }
  $("#pinModal").classList.remove("is-hidden");
  document.body.classList.add("modal-open");
  requestAnimationFrame(() => $("#pin").focus());
}

function closePinModal({ returnToWatchlist = false } = {}) {
  $("#pinModal").classList.add("is-hidden");
  document.body.classList.remove("modal-open");
  $("#pin").value = "";
  if (returnToWatchlist && !portfolio) activateTab("watchlist");
}

function renderHoldings(holdings) {
  const rows = Array.isArray(holdings) ? holdings : [];
  setText("#positionsCount", `${rows.length}개`);
  $("#holdingsEmpty").classList.toggle("is-hidden", rows.length > 0);
  $("#holdingsBody").innerHTML = rows.map((item, index) => `<tr class="instrument-row clickable-row" data-row-index="${index}" tabindex="0" title="클릭하여 캔들·Bollinger·Supertrend 차트 보기">
    <td data-label="종목" class="mobile-span-all"><div class="instrument"><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)} · ${escapeHtml(item.market)}</span></div></td>
    <td data-label="섹터"><span class="sector-chip">${escapeHtml(item.sector || "기타")}</span></td>
    <td data-label="평가금액" class="numeric strong">${money(item.eval_amount_krw)}</td>
    <td data-label="평가손익" class="numeric ${pnlClass(item.pnl_amount_krw)}"><strong>${money(item.pnl_amount_krw)}</strong></td>
    <td data-label="수익률" class="numeric ${pnlClass(item.pnl_pct)}"><strong>${percent(item.pnl_pct)}</strong></td>
    <td data-label="Bollinger">${bandBadge(item.band_display || item.band)}</td><td data-label="ST 14·3">${trendBadge(item.st_14_3)}</td>
    <td data-label="60일선">${slopeBadge(item.ma60_slope_pct)}</td><td data-label="200일선">${slopeBadge(item.ma200_slope_pct)}</td>
  </tr>`).join("");
  attachRowChartHandlers("#holdingsBody .instrument-row", rows);
}



function eventDate(value) {
  return compactDate(value);
}

function addCalendarDays(day, offset) {
  if (!/^\d{8}$/.test(String(day || ""))) return String(day || "");
  const date = new Date(Number(day.slice(0, 4)), Number(day.slice(4, 6)) - 1, Number(day.slice(6, 8)));
  date.setDate(date.getDate() + offset);
  return `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, "0")}${String(date.getDate()).padStart(2, "0")}`;
}

function seoulDayFromTimestamp(value) {
  const text = String(value || "");
  if (/^\d{4}-\d{2}-\d{2}T/.test(text) && /(Z|[+-]\d{2}:?\d{2})$/.test(text)) {
    const date = new Date(text);
    if (!Number.isNaN(date.getTime())) {
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
      }).formatToParts(date);
      const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
      return `${values.year}${values.month}${values.day}`;
    }
  }
  return eventDate(value);
}

function assetSnapshotDay(row) {
  const explicit = compactDate(row?.snapshot_date);
  if (/^\d{8}$/.test(explicit)) return explicit;
  return seoulDayFromTimestamp(row?.at);
}

function realizedChartDay(item) {
  const explicit = compactDate(item?.account_date);
  if (/^\d{8}$/.test(explicit)) return explicit;
  const tradeDay = eventDate(item?.date);
  if (!/^\d{8}$/.test(tradeDay)) return tradeDay;
  // Legacy periodPnlDetail rows carry the U.S. exchange date.  The account
  // snapshot is a Korea-calendar snapshot, so regular U.S. trades belong to
  // the following Korea calendar day.  transaction_fallback already comes
  // from dailyTransaction and keeps its observed account date.
  if (String(item?.market || "").toUpperCase() === "US" && item?.source === "period_pnl_detail") {
    return addCalendarDays(tradeDay, 1);
  }
  return tradeDay;
}

let selectedYieldWeeks = 4;
let selectedChartDay = null;

function parseCompactDay(value) {
  const day = eventDate(value);
  if (!/^\d{8}$/.test(day)) return null;
  return new Date(Number(day.slice(0, 4)), Number(day.slice(4, 6)) - 1, Number(day.slice(6, 8)));
}

function toDayKey(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}${String(date.getMonth() + 1).padStart(2, "0")}${String(date.getDate()).padStart(2, "0")}`;
}

function dayLabel(value, dense = false) {
  const day = eventDate(value);
  if (!/^\d{8}$/.test(day)) return dateText(value);
  return dense
    ? `${day.slice(2, 4)}.${day.slice(4, 6)}.${day.slice(6, 8)}`
    : `${day.slice(0, 4)}.${day.slice(4, 6)}.${day.slice(6, 8)}`;
}

function axisMoney(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  const amount = Math.round(parsed);
  const sign = amount < 0 ? "−" : "";
  return `${sign}₩${Math.abs(amount).toLocaleString("ko-KR")}`;
}

function moneyMaybe(value, currency = "KRW") {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? money(parsed, currency) : "—";
}

function buildChartData() {
  const assetPoints = [];
  const latestAssetByDay = new Map();
  const cumulativeByDay = new Map();
  const realizedByDay = new Map();
  const flowByDay = new Map();

  (portfolio?.yield_history || []).forEach((row) => {
    const day = assetSnapshotDay(row);
    if (!day) return;
    if (row.cumulative_realized_krw !== null && row.cumulative_realized_krw !== undefined && row.cumulative_realized_krw !== "") {
      cumulativeByDay.set(day, number(row.cumulative_realized_krw));
    }
    const rawAsset = row.total_asset_krw;
    const hasAsset = row.asset_recorded !== false && row.kind !== "realized_daily" && rawAsset !== null && rawAsset !== undefined && rawAsset !== "" && Number.isFinite(Number(rawAsset));
    if (!hasAsset) return;
    const parsed = new Date(row.at);
    const fallback = parseCompactDay(day);
    const atMs = !Number.isNaN(parsed.getTime()) ? parsed.getTime() : (fallback ? fallback.getTime() : 0);
    const point = {
      day, at: row.at, at_ms: atMs, total_asset_krw: Number(rawAsset),
      cash_krw: row.cash_krw === null || row.cash_krw === undefined || row.cash_krw === "" ? null : Number(row.cash_krw),
      holdings_evaluation_krw: row.holdings_evaluation_krw === null || row.holdings_evaluation_krw === undefined || row.holdings_evaluation_krw === "" ? null : Number(row.holdings_evaluation_krw),
      total_asset_source: String(row.total_asset_source || ""),
    };
    const current = latestAssetByDay.get(day);
    if (!current || point.at_ms >= current.at_ms) latestAssetByDay.set(day, point);
  });
  // One chart point per calendar day. Older builds stored one point per hourly
  // GitHub run; collapse them here immediately even before the backend compacts
  // the encrypted history on the next update.
  assetPoints.push(...[...latestAssetByDay.values()].sort((a, b) => a.day.localeCompare(b.day)));

  (portfolio?.realized_events || []).forEach((item) => {
    const day = realizedChartDay(item);
    if (!day || item.pnl_available === false || item.realized_pnl_krw === null || item.realized_pnl_krw === undefined || item.realized_pnl_krw === "") return;
    const pnl = Number(item.realized_pnl_krw);
    if (Number.isFinite(pnl)) realizedByDay.set(day, (realizedByDay.get(day) || 0) + pnl);
  });

  (portfolio?.cash_flows || []).forEach((item) => {
    const day = eventDate(item.date);
    if (!day) return;
    const signed = item.side === "DEPOSIT" ? number(item.amount_krw) : -number(item.amount_krw);
    flowByDay.set(day, (flowByDay.get(day) || 0) + signed);
  });

  const realizedEvents = (portfolio?.realized_events || [])
    .filter((item) => item.pnl_available !== false && item.realized_pnl_krw !== null && item.realized_pnl_krw !== undefined && item.realized_pnl_krw !== "")
    .map((item) => ({ day: realizedChartDay(item), pnl: Number(item.realized_pnl_krw) }))
    .filter((item) => /^\d{8}$/.test(item.day) && Number.isFinite(item.pnl))
    .sort((a, b) => a.day.localeCompare(b.day));

  function cumulativeForDay(day) {
    if (cumulativeByDay.has(day)) return cumulativeByDay.get(day);
    let sum = 0;
    for (const item of realizedEvents) {
      if (item.day > day) break;
      sum += item.pnl;
    }
    return sum;
  }

  const allDays = new Set([
    ...latestAssetByDay.keys(),
    ...realizedByDay.keys(),
    ...flowByDay.keys(),
    ...cumulativeByDay.keys(),
  ]);
  const updatedDay = seoulDayFromTimestamp(portfolio?.updated_at);
  if (/^\d{8}$/.test(updatedDay)) allDays.add(updatedDay);

  return {
    assetPoints,
    latestAssetByDay,
    realizedPoints: [...realizedByDay.entries()].map(([day, value]) => ({ day, daily_realized_krw: value })).sort((a, b) => a.day.localeCompare(b.day)),
    flowByDay,
    realizedByDay,
    cumulativeForDay,
    allDays: [...allDays].sort(),
  };
}

function getRangeBounds(data) {
  const validDays = data.allDays.filter((day) => /^\d{8}$/.test(day));
  const fallbackEnd = new Date();
  const end = validDays.length ? parseCompactDay(validDays.at(-1)) : fallbackEnd;
  end.setHours(23, 59, 59, 999);
  const weeks = Math.max(1, Math.min(156, Number(selectedYieldWeeks) || 4));
  const start = new Date(end);
  start.setDate(start.getDate() - (weeks * 7 - 1));
  start.setHours(0, 0, 0, 0);
  return { start, end, startDay: toDayKey(start), endDay: toDayKey(end), weeks };
}

function inRange(day, bounds) {
  return day >= bounds.startDay && day <= bounds.endDay;
}

function createTicks(low, high, count = 4) {
  if (!Number.isFinite(low) || !Number.isFinite(high)) return [0];
  if (low === high) return [low];
  return Array.from({ length: count }, (_, index) => low + (high - low) * (index / (count - 1)));
}

function extent(values, { includeZero = false } = {}) {
  const safe = values.filter((value) => Number.isFinite(value));
  if (!safe.length) return includeZero ? [-1, 1] : [0, 1];
  let low = Math.min(...safe);
  let high = Math.max(...safe);
  if (includeZero) { low = Math.min(low, 0); high = Math.max(high, 0); }
  if (low === high) {
    const spread = Math.max(1, Math.abs(high) * 0.02);
    low -= spread;
    high += spread;
  } else {
    const spread = Math.max(1, (high - low) * 0.08);
    low -= spread;
    high += spread;
  }
  return [low, high];
}

function dateTicks(bounds, count = 5) {
  const startMs = bounds.start.getTime();
  const endMs = bounds.end.getTime();
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(startMs + (endMs - startMs) * (index / (count - 1)));
    return { date, label: dayLabel(toDayKey(date), true) };
  });
}

function xForDay(day, bounds, left, right, width) {
  const date = parseCompactDay(day);
  if (!date) return left;
  const span = Math.max(1, bounds.end.getTime() - bounds.start.getTime());
  const ratio = Math.max(0, Math.min(1, (date.getTime() - bounds.start.getTime()) / span));
  return left + ratio * (width - left - right);
}

function renderChartTooltip(host, payload) {
  if (!host) return;
  let tooltip = host.querySelector(".chart-tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    host.appendChild(tooltip);
  }
  tooltip.innerHTML = `<strong>${escapeHtml(payload.title)}</strong>${payload.lines.map((line) => `<span>${escapeHtml(line)}</span>`).join("")}`;
  tooltip.style.left = `${Math.max(8, Math.min(payload.x, host.clientWidth - 200))}px`;
  tooltip.style.top = `${Math.max(8, Math.min(payload.y, host.clientHeight - 95))}px`;
  tooltip.classList.add("is-visible");
}

function clearChartTooltip(host) {
  host?.querySelector(".chart-tooltip")?.classList.remove("is-visible");
}

function renderSelectedDay(day, data) {
  if (!day) return;
  selectedChartDay = day;
  const asset = data.latestAssetByDay.get(day);
  const dayRealized = data.realizedByDay.get(day) || 0;
  const dayFlow = data.flowByDay.get(day) || 0;
  const cumulative = data.cumulativeForDay(day);

  setText("#eventDetailTitle", dayLabel(day));
  setText("#selectedAsset", asset ? moneyMaybe(asset.total_asset_krw) : "—");
  const dayRealizedNode = $("#selectedDayRealized");
  dayRealizedNode.textContent = moneyMaybe(dayRealized);
  dayRealizedNode.className = pnlClass(dayRealized);
  const cumulativeNode = $("#selectedCumulativeRealized");
  cumulativeNode.textContent = moneyMaybe(cumulative);
  cumulativeNode.className = pnlClass(cumulative);
  const flowNode = $("#selectedDayNetFlow");
  flowNode.textContent = moneyMaybe(dayFlow);
  flowNode.className = pnlClass(dayFlow);

  const realized = (portfolio.realized_events || []).filter((item) => realizedChartDay(item) === day);
  const flows = (portfolio.cash_flows || []).filter((item) => eventDate(item.date) === day);
  const parts = [
    ...realized.map((item) => {
      const tradeDay = eventDate(item.date);
      const accountDay = realizedChartDay(item);
      const dateNote = tradeDay && accountDay && tradeDay !== accountDay ? ` · 거래일 ${dayLabel(tradeDay)}` : "";
      return `<article class="event-item"><span class="event-icon realized">R</span><div><strong>${escapeHtml(item.name || item.code)}</strong><small>${qty.format(number(item.qty))}주 · ${moneyMaybe(item.sell_price, item.currency === "USD" ? "USD" : "KRW")}${dateNote}${item.pnl_available === false ? " · 손익 미확인" : ""}</small></div><b class="${pnlClass(item.realized_pnl_krw)}">${moneyMaybe(item.realized_pnl_krw)}</b></article>`;
    }),
    ...flows.map((item) => `<article class="event-item"><span class="event-icon flow">${item.side === "DEPOSIT" ? "+" : "−"}</span><div><strong>${item.side === "DEPOSIT" ? "입금" : "출금"}</strong><small>${escapeHtml(item.label || "계좌 현금흐름")}</small></div><b class="${item.side === "DEPOSIT" ? "positive" : "negative"}">${item.side === "DEPOSIT" ? "+" : "−"}${money(item.amount_krw)}</b></article>`),
  ];
  $("#selectedEvents").innerHTML = parts.length ? parts.join("") : '<p class="muted">해당 날짜의 실현 종목 또는 입출금이 없습니다.</p>';
}

function renderAssetChart(data, bounds) {
  const host = $("#assetChart");
  const points = data.assetPoints.filter((point) => point.at_ms >= bounds.start.getTime() && point.at_ms <= bounds.end.getTime());
  if (!points.length) {
    host.innerHTML = '<div class="chart-empty">선택 기간에 실제로 저장된 총자산 스냅샷이 없습니다.<br>과거 자산은 임의 역산하지 않습니다.</div>';
    return;
  }
  const width = 860; const height = 280;
  const pad = { top: 18, right: 18, bottom: 44, left: 118 };
  const values = points.map((point) => Number(point.total_asset_krw));
  const [low, high] = extent(values);
  const yTicks = createTicks(low, high, 4);
  const y = (value) => height - pad.bottom - ((value - low) / Math.max(1, high - low)) * (height - pad.top - pad.bottom);
  const spanMs = Math.max(1, bounds.end.getTime() - bounds.start.getTime());
  const xPoint = (point) => pad.left + Math.max(0, Math.min(1, (point.at_ms - bounds.start.getTime()) / spanMs)) * (width - pad.left - pad.right);
  const xDay = (day) => xForDay(day, bounds, pad.left, pad.right, width);
  const line = points.map((point) => `${xPoint(point)},${y(Number(point.total_asset_krw))}`).join(" ");
  const ticks = dateTicks(bounds);
  const fill = points.length >= 2 ? `<polygon points="${xPoint(points[0])},${height - pad.bottom} ${line} ${xPoint(points.at(-1))},${height - pad.bottom}" fill="url(#assetFill)"/>` : "";
  const polyline = points.length >= 2 ? `<polyline points="${line}" fill="none" stroke="#51e4ba" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>` : "";
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="총자산 일별 선 그래프">
    <defs><linearGradient id="assetFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#51e4ba" stop-opacity=".28"/><stop offset="1" stop-color="#51e4ba" stop-opacity="0"/></linearGradient></defs>
    ${yTicks.map((tick) => `<g><line x1="${pad.left}" y1="${y(tick)}" x2="${width - pad.right}" y2="${y(tick)}" class="chart-grid-line"/><text x="${pad.left - 10}" y="${y(tick) + 4}" class="chart-y-label" text-anchor="end">${axisMoney(tick)}</text></g>`).join("")}
    ${ticks.map((tick) => `<g><line x1="${xDay(toDayKey(tick.date))}" y1="${height - pad.bottom}" x2="${xDay(toDayKey(tick.date))}" y2="${height - pad.bottom + 6}" class="chart-axis-line"/><text x="${xDay(toDayKey(tick.date))}" y="${height - 14}" class="chart-x-label" text-anchor="middle">${tick.label}</text></g>`).join("")}
    ${fill}${polyline}
    ${points.map((point) => {
      const realizedAnchor = data.realizedByDay.has(point.day) && data.latestAssetByDay.get(point.day)?.at_ms === point.at_ms;
      const selectedAnchor = point.day === selectedChartDay && data.latestAssetByDay.get(point.day)?.at_ms === point.at_ms;
      return `<circle class="chart-point asset ${realizedAnchor ? "has-realized" : ""} ${selectedAnchor ? "selected" : ""}" data-at-ms="${point.at_ms}" cx="${xPoint(point)}" cy="${y(Number(point.total_asset_krw))}" r="${selectedAnchor ? 6 : (realizedAnchor ? 5 : 3.2)}"/>`;
    }).join("")}
  </svg>`;

  host.querySelectorAll(".chart-point.asset").forEach((node) => {
    const point = points.find((item) => String(item.at_ms) === node.dataset.atMs);
    if (!point) return;
    const tooltip = () => renderChartTooltip(host, {
      x: Number(node.getAttribute("cx")) / width * host.clientWidth + 10,
      y: Number(node.getAttribute("cy")) / height * host.clientHeight - 16,
      title: dateText(point.at, true),
      lines: [
        `총자산 ${moneyMaybe(point.total_asset_krw)}`,
        ...(Number.isFinite(point.holdings_evaluation_krw) ? [`보유평가 ${moneyMaybe(point.holdings_evaluation_krw)}`] : []),
        ...(Number.isFinite(point.cash_krw) ? [`예수금 ${moneyMaybe(point.cash_krw)} · 총자산에 포함`] : []),
        `해당일 실현수익 ${moneyMaybe(data.realizedByDay.get(point.day) || 0)}`,
        `누적 실현손익 ${moneyMaybe(data.cumulativeForDay(point.day))}`,
      ],
    });
    node.addEventListener("mouseenter", tooltip);
    node.addEventListener("click", () => {
      selectedChartDay = point.day;
      renderSelectedDay(point.day, data);
      host.querySelectorAll(".chart-point.asset").forEach((dot) => dot.classList.remove("selected"));
      node.classList.add("selected");
      tooltip();
    });
  });
  host.addEventListener("mouseleave", () => clearChartTooltip(host));
}

function renderRealizedChart(data, bounds) {
  const host = $("#realizedChart");
  const points = data.realizedPoints.filter((point) => inRange(point.day, bounds));
  const width = 860; const height = 280;
  const pad = { top: 18, right: 18, bottom: 44, left: 118 };
  const ticks = dateTicks(bounds);
  if (!points.length) {
    host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="실현수익 막대 그래프">${ticks.map((tick) => `<text x="${xForDay(toDayKey(tick.date), bounds, pad.left, pad.right, width)}" y="${height - 14}" class="chart-x-label" text-anchor="middle">${tick.label}</text>`).join("")}</svg><div class="chart-empty overlay-empty">선택 기간에 확인된 실현수익이 없습니다.</div>`;
    return;
  }
  const values = points.map((point) => Number(point.daily_realized_krw));
  const [low, high] = extent(values, { includeZero: true });
  const yTicks = createTicks(low, high, 5);
  const y = (value) => height - pad.bottom - ((value - low) / Math.max(1, high - low)) * (height - pad.top - pad.bottom);
  const x = (day) => xForDay(day, bounds, pad.left, pad.right, width);
  const zeroY = y(0);
  const daySpan = Math.max(1, (bounds.end.getTime() - bounds.start.getTime()) / 86400000);
  const barWidth = Math.max(5, Math.min(18, (width - pad.left - pad.right) / daySpan * 0.76));
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="일별 실현수익 막대 그래프">
    ${yTicks.map((tick) => `<g><line x1="${pad.left}" y1="${y(tick)}" x2="${width - pad.right}" y2="${y(tick)}" class="chart-grid-line"/><text x="${pad.left - 10}" y="${y(tick) + 4}" class="chart-y-label" text-anchor="end">${axisMoney(tick)}</text></g>`).join("")}
    <line x1="${pad.left}" y1="${zeroY}" x2="${width - pad.right}" y2="${zeroY}" class="chart-zero-line"/>
    ${ticks.map((tick) => `<g><line x1="${x(toDayKey(tick.date))}" y1="${height - pad.bottom}" x2="${x(toDayKey(tick.date))}" y2="${height - pad.bottom + 6}" class="chart-axis-line"/><text x="${x(toDayKey(tick.date))}" y="${height - 14}" class="chart-x-label" text-anchor="middle">${tick.label}</text></g>`).join("")}
    ${points.map((point) => {
      const value = Number(point.daily_realized_krw);
      const top = value >= 0 ? y(value) : zeroY;
      const barHeight = Math.max(2, Math.abs(y(value) - zeroY));
      return `<rect class="chart-bar ${value >= 0 ? "gain" : "loss"} ${point.day === selectedChartDay ? "selected" : ""}" data-day="${point.day}" x="${x(point.day) - barWidth / 2}" y="${top}" width="${barWidth}" height="${barHeight}" rx="3" ry="3"></rect>`;
    }).join("")}
  </svg>`;

  host.querySelectorAll(".chart-bar").forEach((node) => {
    const point = points.find((item) => item.day === node.dataset.day);
    const tooltip = () => renderChartTooltip(host, {
      x: (Number(node.getAttribute("x")) + Number(node.getAttribute("width")) / 2) / width * host.clientWidth + 10,
      y: Number(node.getAttribute("y")) / height * host.clientHeight - 16,
      title: dayLabel(point.day),
      lines: [`일자 실현수익 ${moneyMaybe(point.daily_realized_krw)}`, `누적 실현손익 ${moneyMaybe(data.cumulativeForDay(point.day))}`, `당일 순입출금 ${moneyMaybe(data.flowByDay.get(point.day) || 0)}`],
    });
    node.addEventListener("mouseenter", tooltip);
    node.addEventListener("click", () => {
      selectedChartDay = point.day;
      renderSelectedDay(point.day, data);
      host.querySelectorAll(".chart-bar").forEach((bar) => bar.classList.toggle("selected", bar === node));
      tooltip();
    });
  });
  host.addEventListener("mouseleave", () => clearChartTooltip(host));
}

function renderYieldCharts() {
  const data = buildChartData();
  const bounds = getRangeBounds(data);
  const weeksText = bounds.weeks === 156 ? "3년" : `${bounds.weeks}주`;
  setText("#yieldWeekValue", weeksText);
  setText("#yieldRangeLabel", `최근 ${weeksText} · ${dayLabel(bounds.startDay)} ~ ${dayLabel(bounds.endDay)}`);
  const slider = $("#yieldWeekSlider");
  if (slider) slider.value = String(bounds.weeks);

  if (data.assetPoints.length) {
    const first = data.assetPoints[0].day;
    const last = data.assetPoints.at(-1).day;
    setText("#assetCoverageLabel", `실제 총자산 기록 ${dayLabel(first)} ~ ${dayLabel(last)} · ${data.assetPoints.length}일`);
  } else {
    setText("#assetCoverageLabel", "실제 저장된 총자산 기록 없음");
  }

  renderAssetChart(data, bounds);
  renderRealizedChart(data, bounds);

  const candidateDays = data.allDays.filter((day) => inRange(day, bounds));
  if (!selectedChartDay || !inRange(selectedChartDay, bounds) || !data.allDays.includes(selectedChartDay)) {
    selectedChartDay = candidateDays.at(-1) || data.allDays.at(-1) || null;
  }
  if (selectedChartDay) renderSelectedDay(selectedChartDay, data);
}
const sectorColors = ["#51e4ba", "#7fa8ff", "#9d8cff", "#f6bd60", "#ef7185", "#66c7d8", "#b5d86b", "#d88dd2"];

function renderSectorAnalytics(holdings) {
  const grouped = new Map();
  (holdings || []).forEach((item) => {
    const sector = item.sector || "기타";
    const current = grouped.get(sector) || { evaluation: 0, pnl: 0, principal: 0 };
    current.evaluation += number(item.eval_amount_krw);
    current.pnl += number(item.pnl_amount_krw);
    current.principal += number(item.eval_amount_krw) - number(item.pnl_amount_krw);
    grouped.set(sector, current);
  });
  const rows = [...grouped.entries()].sort((a, b) => Math.abs(b[1].pnl) - Math.abs(a[1].pnl));
  setText("#sectorCount", rows.length);
  const totalWeight = rows.reduce((sum, [, value]) => sum + (Math.abs(value.pnl) || value.evaluation), 0) || 1;
  let cursor = 0;
  const stops = rows.map(([, value], index) => {
    const start = cursor; cursor += ((Math.abs(value.pnl) || value.evaluation) / totalWeight) * 360;
    return `${sectorColors[index % sectorColors.length]} ${start}deg ${cursor}deg`;
  });
  $("#sectorDonut").style.background = rows.length ? `conic-gradient(${stops.join(",")})` : "#182439";
  $("#sectorLegend").innerHTML = rows.map(([sector, value], index) => {
    const returnPct = value.principal ? value.pnl / value.principal * 100 : 0;
    return `<div><i style="background:${sectorColors[index % sectorColors.length]}"></i><span>${escapeHtml(sector)}</span><b class="${pnlClass(value.pnl)}">${percent(returnPct)}</b></div>`;
  }).join("") || '<p class="muted">보유종목 데이터가 없습니다.</p>';
}

function renderMonthly(rows) {
  const data = (Array.isArray(rows) ? rows : []).slice(-12);
  if (!data.length) {
    $("#monthlyChart").innerHTML = '<div class="chart-empty">실현손익 기록이 누적되면 월별 수익률이 표시됩니다.</div>';
    return;
  }
  const values = data.map((item) => Number.isFinite(Number(item.return_pct)) ? number(item.return_pct) : 0);
  const max = Math.max(1, ...values.map(Math.abs));
  $("#monthlyChart").innerHTML = `<div class="monthly-zero"></div>${data.map((item, index) => {
    const value = values[index]; const height = Math.max(4, Math.abs(value) / max * 45);
    return `<div class="month-column"><span class="month-value ${pnlClass(value)}">${Number.isFinite(Number(item.return_pct)) ? percent(value, 1) : "—"}</span><i class="${value >= 0 ? "gain" : "loss"}" style="height:${height}%;${value >= 0 ? "bottom:50%" : "top:50%"}"></i><small>${escapeHtml(String(item.month || "").slice(2).replace("-", "."))}</small></div>`;
  }).join("")}`;
}

function renderRealizedEvents(events) {
  const rows = (Array.isArray(events) ? events : []).slice().sort((a, b) => {
    const dateOrder = String(b.date || "").localeCompare(String(a.date || ""));
    if (dateOrder) return dateOrder;
    return String(b.id || "").localeCompare(String(a.id || ""));
  });
  const visibleRows = rows.slice(0, 20).map((item) => {
    const key = `${String(item.market || "").toUpperCase()}:${String(item.code || "").toUpperCase()}`;
    const chart = portfolio?.technical_charts?.[key];
    return chart ? { ...item, chart_bars: chart.chart_bars, currency: item.currency || chart.currency } : item;
  });
  setText("#realizedEventsCount", `${visibleRows.length}건`);
  $("#realizedEmpty").classList.toggle("is-hidden", visibleRows.length > 0);
  $("#realizedBody").innerHTML = visibleRows.map((item, index) => {
    const pnlText = moneyMaybe(item.realized_pnl_krw);
    const rateText = item.return_pct === null || item.return_pct === undefined || item.return_pct === "" ? "—" : percent(item.return_pct);
    return `<div class="realized-compact-row instrument-row clickable-row" data-row-index="${index}" tabindex="0" title="클릭하여 캔들·Bollinger·Supertrend 차트 보기">
      <span class="realized-compact-date">${dateText(item.date)}</span>
      <strong class="realized-compact-name">${escapeHtml(item.name || item.code)}</strong>
      <span class="realized-compact-pnl ${pnlClass(item.realized_pnl_krw)}">${pnlText} <small>(${rateText})</small></span>
    </div>`;
  }).join("");
  attachRowChartHandlers("#realizedBody .instrument-row", visibleRows);
}

function render(data) {
  portfolio = data;
  const totals = data.totals || {};
  const realized = data.realized_summary || {};
  const netFlow = (data.cash_flows || []).reduce((sum, item) => sum + (item.side === "DEPOSIT" ? number(item.amount_krw) : -number(item.amount_krw)), 0);
  setText("#environment", data.account?.environment === "mock" ? "모의투자" : "실계좌");
  setText("#account", data.account?.masked || "—");
  setText("#recordingStart", dateText(data.recording_started_at));
  setText("#updatedAt", dateText(data.updated_at, true));
  setText("#totalAsset", money(totals.total_asset_krw || totals.evaluation_krw));
  setText("#realizedPnl", money(realized.cumulative_realized_krw));
  $("#realizedPnl").className = pnlClass(realized.cumulative_realized_krw);
  setText("#realizedCount", `최근 1년 · ${realized.trade_count || 0}건 실현`);
  setText("#unrealizedPnl", money(totals.unrealized_pnl_krw ?? totals.pnl_krw));
  $("#unrealizedPnl").className = pnlClass(totals.unrealized_pnl_krw ?? totals.pnl_krw);
  setText("#unrealizedRate", percent(totals.unrealized_pnl_pct ?? totals.pnl_pct));
  setText("#netCashFlow", money(netFlow));
  $("#netCashFlow").className = pnlClass(netFlow);
  setText("#winRate", percent(realized.win_rate_pct, 1));
  setText("#avgTakeProfit", percent(realized.average_take_profit_pct));
  setText("#avgStopLoss", percent(realized.average_stop_loss_pct));
  setText("#exitCount", `${realized.trade_count || 0}건`);
  renderYieldCharts();
  renderHoldings(data.holdings || []);
  renderSectorAnalytics(data.holdings || []);
  renderMonthly(data.monthly_performance || []);
  renderRealizedEvents(data.realized_events || []);
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  $("#warningPanel").classList.toggle("is-hidden", warnings.length === 0);
  $("#warnings").innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  setText("#source", data.source || "조회 전용");
  const diagnostics = data.diagnostics || {};
  const recentByMarket = diagnostics.recent_realized_by_market || {};
  const realizedApi = diagnostics.realized_api || {};
  const transactionApi = diagnostics.transaction_api || {};
  const pendingUs = number(realizedApi.us_pnl_unavailable_events);
  const usTrades = number(transactionApi.us_daily_trades);
  const usSellTrades = number(transactionApi.us_daily_sell_trades || realizedApi.us_sell_trades);
  const queryDays = number(diagnostics.history_query_days || data.history_policy?.query_window_days || 1);
  setText("#diagnostics", `진단: 조회 ${queryDays}일 · 실현 KR ${number(recentByMarket.KR)} / US ${number(recentByMarket.US)} · US 거래 ${usTrades} / SELL ${usSellTrades}${pendingUs ? ` / 손익 미확인 ${pendingUs}` : ""} · 최근 1년 지표 ${number(diagnostics.realized_metric_count)}건 · 자산 스냅샷 ${number(diagnostics.asset_snapshot_points)}p`);
}

async function loadEnvelope() {
  const response = await fetch(`./data/portfolio.enc.json?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`데이터를 불러오지 못했습니다 (${response.status}).`);
  encryptedEnvelope = await response.json();
  if (!$("#pinModal").classList.contains("is-hidden")) openPinModal();
}

$("#watchlistTab").addEventListener("click", () => { closePinModal(); activateTab("watchlist"); });
$("#portfolioTab").addEventListener("click", () => { activateTab("portfolio"); if (!portfolio) openPinModal(); });
$("#watchMarketFilter").addEventListener("change", renderWatchlistRows);
$("#watchBandFilter").addEventListener("change", renderWatchlistRows);
$("#yieldWeekSlider")?.addEventListener("input", (event) => {
  selectedYieldWeeks = Math.max(1, Math.min(156, Number(event.target.value) || 4));
  setText("#yieldWeekValue", selectedYieldWeeks === 156 ? "3년" : `${selectedYieldWeeks}주`);
  if (!portfolio) return;
  selectedChartDay = null;
  renderYieldCharts();
});
$("#closeTechnicalChartButton")?.addEventListener("click", closeTechnicalChart);
$("#technicalChartModal")?.addEventListener("click", (event) => { if (event.target === $("#technicalChartModal")) closeTechnicalChart(); });
$("#openPinButton").addEventListener("click", openPinModal);
$("#closePinButton").addEventListener("click", () => closePinModal({ returnToWatchlist: true }));
$("#cancelPinButton").addEventListener("click", () => closePinModal({ returnToWatchlist: true }));
$("#pin").addEventListener("input", (event) => { event.target.value = event.target.value.replace(/\D/g, "").slice(0, 4); $("#pinMessage").textContent = ""; });

$("#pinForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const pin = $("#pin").value; const button = $("#unlockButton"); const message = $("#pinMessage");
  if (!/^\d{4}$/.test(pin)) { message.textContent = "숫자 4자리를 입력해 주세요."; $("#pin").focus(); return; }
  button.disabled = true; message.textContent = "Yield Monitor를 여는 중…";
  try {
    render(await decryptEnvelope(encryptedEnvelope, pin));
    $("#portfolioLocked").classList.add("is-hidden"); $("#dashboardContent").classList.remove("is-hidden"); closePinModal();
  } catch (error) {
    portfolio = null; message.textContent = error.code === "EMPTY" ? error.message : "PIN이 맞지 않거나 데이터가 손상되었습니다."; $("#pin").value = ""; $("#pin").focus();
  } finally { button.disabled = Boolean(encryptedEnvelope?.empty); }
});

$("#lockButton").addEventListener("click", () => {
  portfolio = null; $("#dashboardContent").classList.add("is-hidden"); $("#portfolioLocked").classList.remove("is-hidden"); activateTab("watchlist");
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!$("#technicalChartModal")?.classList.contains("is-hidden")) { closeTechnicalChart(); return; }
  if (!$("#pinModal").classList.contains("is-hidden")) closePinModal({ returnToWatchlist: true });
});

activateTab(location.hash === "#portfolio" ? "portfolio" : "watchlist");
if (location.hash === "#portfolio") openPinModal();
loadWatchlist();
loadEnvelope().catch((error) => {
  encryptedEnvelope = { empty: true, message: `${error.message} 웹 서버 상태를 확인하세요.` };
  if (!$("#pinModal").classList.contains("is-hidden")) openPinModal();
});
