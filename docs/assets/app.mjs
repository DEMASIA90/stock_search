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
    return `<tr class="${item.band === "최하단" ? "priority-row" : ""}">
      <td><span class="rank">${index + 1}</span></td>
      <td><div class="instrument"><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)} · ${item.kind === "leveraged_etf" ? "3X ETF" : escapeHtml(item.market)}${item.stale ? " · 이전 데이터" : ""}</span></div></td>
      <td>${bandBadge(item.band)}</td>
      <td class="numeric"><span class="score">${number(item.score).toFixed(0)}</span><small>/30</small></td>
      <td>${trendBadge(item.st_10_3)}</td><td>${trendBadge(item.st_20_4)}</td>
      <td>${slopeBadge(item.ma60_slope_pct)}</td><td>${slopeBadge(item.ma200_slope_pct)}</td>
      <td class="numeric"><strong>${money(item.price, currency)}</strong><small class="${pnlClass(item.change_pct)}">${percent(item.change_pct)}</small></td>
      <td class="numeric ${pnlClass(item.vs_sma60_pct)}"><strong>${percent(item.vs_sma60_pct)}</strong></td>
      <td><span class="sector-chip">${escapeHtml(item.sector || "기타")}</span></td>
      <td class="numeric">${item.kind === "leveraged_etf" ? "3X ETF" : marketCap(item.market_cap_krw)}</td>
    </tr>`;
  }).join("");
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
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
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
  $("#holdingsBody").innerHTML = rows.map((item) => `<tr>
    <td><div class="instrument"><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)} · ${escapeHtml(item.market)}</span></div></td>
    <td><span class="sector-chip">${escapeHtml(item.sector || "기타")}</span></td>
    <td class="numeric strong">${money(item.eval_amount_krw)}</td>
    <td class="numeric ${pnlClass(item.pnl_amount_krw)}"><strong>${money(item.pnl_amount_krw)}</strong></td>
    <td class="numeric ${pnlClass(item.pnl_pct)}"><strong>${percent(item.pnl_pct)}</strong></td>
    <td>${bandBadge(item.band)}</td><td>${trendBadge(item.st_14_3)}</td>
    <td>${slopeBadge(item.ma60_slope_pct)}</td><td>${slopeBadge(item.ma200_slope_pct)}</td>
  </tr>`).join("");
}



function eventDate(value) {
  return compactDate(value);
}

let selectedYieldRange = "6M";
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
  const assetByDay = new Map();
  const cumulativeByDay = new Map();
  const realizedByDay = new Map();
  const flowByDay = new Map();

  (portfolio?.yield_history || []).forEach((row) => {
    const day = eventDate(row.at);
    if (!day) return;
    if (row.cumulative_realized_krw !== null && row.cumulative_realized_krw !== undefined && row.cumulative_realized_krw !== "") {
      cumulativeByDay.set(day, number(row.cumulative_realized_krw));
    }
    const rawAsset = row.total_asset_krw;
    const hasAsset = row.asset_recorded !== false && row.kind !== "realized_daily" && rawAsset !== null && rawAsset !== undefined && rawAsset !== "" && Number.isFinite(Number(rawAsset));
    if (!hasAsset) return;
    const current = assetByDay.get(day);
    if (!current || String(row.at) >= String(current.at)) {
      assetByDay.set(day, { day, at: row.at, total_asset_krw: Number(rawAsset) });
    }
  });

  (portfolio?.realized_events || []).forEach((item) => {
    const day = eventDate(item.date);
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
    .map((item) => ({ day: eventDate(item.date), pnl: Number(item.realized_pnl_krw) }))
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
    ...assetByDay.keys(),
    ...realizedByDay.keys(),
    ...flowByDay.keys(),
    ...cumulativeByDay.keys(),
  ]);
  const updatedDay = eventDate(portfolio?.updated_at);
  if (/^\d{8}$/.test(updatedDay)) allDays.add(updatedDay);

  return {
    assetPoints: [...assetByDay.values()].sort((a, b) => a.day.localeCompare(b.day)),
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
  let start;
  if (selectedYieldRange === "ALL") {
    start = validDays.length ? parseCompactDay(validDays[0]) : new Date(end);
  } else {
    start = new Date(end);
    if (selectedYieldRange === "1M") start.setMonth(start.getMonth() - 1);
    else if (selectedYieldRange === "3M") start.setMonth(start.getMonth() - 3);
    else if (selectedYieldRange === "6M") start.setMonth(start.getMonth() - 6);
    else if (selectedYieldRange === "1Y") start.setFullYear(start.getFullYear() - 1);
  }
  if (start.getTime() === end.getTime()) start.setDate(start.getDate() - 1);
  return { start, end, startDay: toDayKey(start), endDay: toDayKey(end) };
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
  const asset = data.assetPoints.find((point) => point.day === day);
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

  const realized = (portfolio.realized_events || []).filter((item) => eventDate(item.date) === day);
  const flows = (portfolio.cash_flows || []).filter((item) => eventDate(item.date) === day);
  const parts = [
    ...realized.map((item) => `<article class="event-item"><span class="event-icon realized">R</span><div><strong>${escapeHtml(item.name || item.code)}</strong><small>${qty.format(number(item.qty))}주 · ${moneyMaybe(item.sell_price, item.currency === "USD" ? "USD" : "KRW")}${item.pnl_available === false ? " · 손익 미확인" : ""}</small></div><b class="${pnlClass(item.realized_pnl_krw)}">${moneyMaybe(item.realized_pnl_krw)}</b></article>`),
    ...flows.map((item) => `<article class="event-item"><span class="event-icon flow">${item.side === "DEPOSIT" ? "+" : "−"}</span><div><strong>${item.side === "DEPOSIT" ? "입금" : "출금"}</strong><small>${escapeHtml(item.label || "계좌 현금흐름")}</small></div><b class="${item.side === "DEPOSIT" ? "positive" : "negative"}">${item.side === "DEPOSIT" ? "+" : "−"}${money(item.amount_krw)}</b></article>`),
  ];
  $("#selectedEvents").innerHTML = parts.length ? parts.join("") : '<p class="muted">해당 날짜의 실현 종목 또는 입출금이 없습니다.</p>';
}

function renderAssetChart(data, bounds) {
  const host = $("#assetChart");
  const points = data.assetPoints.filter((point) => inRange(point.day, bounds));
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
  const x = (day) => xForDay(day, bounds, pad.left, pad.right, width);
  const line = points.map((point) => `${x(point.day)},${y(Number(point.total_asset_krw))}`).join(" ");
  const ticks = dateTicks(bounds);
  const fill = points.length >= 2 ? `<polygon points="${x(points[0].day)},${height - pad.bottom} ${line} ${x(points.at(-1).day)},${height - pad.bottom}" fill="url(#assetFill)"/>` : "";
  const polyline = points.length >= 2 ? `<polyline points="${line}" fill="none" stroke="#51e4ba" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>` : "";
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="총자산 선 그래프">
    <defs><linearGradient id="assetFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#51e4ba" stop-opacity=".28"/><stop offset="1" stop-color="#51e4ba" stop-opacity="0"/></linearGradient></defs>
    ${yTicks.map((tick) => `<g><line x1="${pad.left}" y1="${y(tick)}" x2="${width - pad.right}" y2="${y(tick)}" class="chart-grid-line"/><text x="${pad.left - 10}" y="${y(tick) + 4}" class="chart-y-label" text-anchor="end">${axisMoney(tick)}</text></g>`).join("")}
    ${ticks.map((tick) => `<g><line x1="${x(toDayKey(tick.date))}" y1="${height - pad.bottom}" x2="${x(toDayKey(tick.date))}" y2="${height - pad.bottom + 6}" class="chart-axis-line"/><text x="${x(toDayKey(tick.date))}" y="${height - 14}" class="chart-x-label" text-anchor="middle">${tick.label}</text></g>`).join("")}
    ${fill}${polyline}
    ${points.map((point) => `<circle class="chart-point asset ${point.day === selectedChartDay ? "selected" : ""}" data-day="${point.day}" cx="${x(point.day)}" cy="${y(Number(point.total_asset_krw))}" r="${point.day === selectedChartDay ? 6 : 4}"/>`).join("")}
  </svg>`;

  host.querySelectorAll(".chart-point.asset").forEach((node) => {
    const point = points.find((item) => item.day === node.dataset.day);
    const tooltip = () => renderChartTooltip(host, {
      x: Number(node.getAttribute("cx")) / width * host.clientWidth + 10,
      y: Number(node.getAttribute("cy")) / height * host.clientHeight - 16,
      title: dayLabel(point.day),
      lines: [`총자산 ${moneyMaybe(point.total_asset_krw)}`, `일자 실현수익 ${moneyMaybe(data.realizedByDay.get(point.day) || 0)}`, `누적 실현손익 ${moneyMaybe(data.cumulativeForDay(point.day))}`],
    });
    node.addEventListener("mouseenter", tooltip);
    node.addEventListener("click", () => {
      selectedChartDay = point.day;
      renderSelectedDay(point.day, data);
      host.querySelectorAll(".chart-point.asset").forEach((dot) => dot.classList.toggle("selected", dot === node));
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
  const labels = { "1M": "최근 1개월", "3M": "최근 3개월", "6M": "최근 6개월", "1Y": "최근 1년", "ALL": "전체 기록" };
  setText("#yieldRangeLabel", `${labels[selectedYieldRange]} · ${dayLabel(bounds.startDay)} ~ ${dayLabel(bounds.endDay)}`);
  $("#yieldRangeSelector")?.querySelectorAll(".range-chip").forEach((node) => {
    const active = node.dataset.range === selectedYieldRange;
    node.classList.toggle("is-active", active);
    node.setAttribute("aria-pressed", String(active));
  });

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
  const rows = (Array.isArray(events) ? events : []).slice().reverse();
  setText("#realizedEventsCount", `${rows.length}건`);
  $("#realizedEmpty").classList.toggle("is-hidden", rows.length > 0);
  $("#realizedBody").innerHTML = rows.slice(0, 100).map((item) => `<tr>
    <td>${dateText(item.date)}</td><td><div class="instrument"><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)} · ${escapeHtml(item.market)}</span></div></td>
    <td><span class="sector-chip">${escapeHtml(item.sector || "기타")}</span></td><td class="numeric">${qty.format(number(item.qty))}</td><td class="numeric">${money(item.sell_price, item.currency === "USD" ? "USD" : "KRW")}</td>
    <td class="numeric ${pnlClass(item.realized_pnl_krw)}"><strong>${money(item.realized_pnl_krw)}</strong></td><td class="numeric ${pnlClass(item.return_pct)}"><strong>${percent(item.return_pct)}</strong></td>
  </tr>`).join("");
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
  setText("#realizedCount", `${realized.trade_count || 0}건 실현`);
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
  setText("#diagnostics", `진단: 최근30일 실현 KR ${number(recentByMarket.KR)} / US ${number(recentByMarket.US)} · US 거래 ${usTrades} / SELL ${usSellTrades}${pendingUs ? ` / 손익 미확인 ${pendingUs}` : ""} · 그래프 ${number(diagnostics.yield_history_points)}p`);
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
$("#yieldRangeSelector")?.querySelectorAll(".range-chip[data-range]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!portfolio) return;
    selectedYieldRange = button.dataset.range || "6M";
    selectedChartDay = null;
    renderYieldCharts();
  });
});
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
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("#pinModal").classList.contains("is-hidden")) closePinModal({ returnToWatchlist: true }); });

activateTab(location.hash === "#portfolio" ? "portfolio" : "watchlist");
if (location.hash === "#portfolio") openPinModal();
loadWatchlist();
loadEnvelope().catch((error) => {
  encryptedEnvelope = { empty: true, message: `${error.message} 웹 서버 상태를 확인하세요.` };
  if (!$("#pinModal").classList.contains("is-hidden")) openPinModal();
});
