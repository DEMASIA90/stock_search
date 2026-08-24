# TradingView exact-source alignment — v14.4.2 repair

US / US_ETF OHLC remains TradingView-only with `session=regular` and `adjustment=splits`; no Yahoo OHLC fallback was added.

## Repaired protocol path

- `create_series`: distinct identifiers are now used: `sds_1` (series id), `s1` (series key), `sds_sym_1` (resolved alias).
- Historical bar messages: both `du` and `timescale_update` are parsed.
- `symbol_error`, `series_error`, `critical_error`, and `protocol_error` are surfaced instead of being silently discarded.
- WebSocket handshake/receive failures now propagate with concrete exception details.
- TradingView framing uses UTF-8 byte length and accepts text or byte receives.
- Heartbeats are echoed in TradingView framing.
- Anonymous mode remains `unauthorized_user_token`; optional `TRADINGVIEW_AUTH_TOKEN` environment support is available without being required.
- Connection pacing and source preflight reduce burst pressure on shared GitHub Actions IPs.
- Three consecutive complete US source-batch failures trigger a circuit breaker rather than retrying the whole universe blindly.

## Regression coverage

`test_market_data.py` now validates the corrected `create_series` request, `du` parsing, symbol-error isolation, total transport failure propagation, and UTF-8 frame sizing.
