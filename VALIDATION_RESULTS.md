# Validation results — TradingView repair

- Python syntax compile: `market_data.py`, `scanner.py` — PASS
- SuperTrend strategy tests: 8/8 — PASS
- Market-data adapter tests: 12/12 — PASS
- Combined unit tests: 20/20 — PASS
- TradingView regression test confirms `create_series = [chart_session, sds_1, s1, sds_sym_1, 1D, bars]` — PASS
- `du` historical candle parsing — PASS
- `symbol_error` isolation — PASS
- all-socket-failure propagation with diagnostic text — PASS
- No Yahoo OHLC fallback marker — PASS

A live TradingView websocket call could not be executed in the artifact runtime because outbound DNS is unavailable there. GitHub Actions will now run a 3-symbol TradingView preflight before a US scan and will print the actual handshake/protocol failure if the external endpoint is unavailable.
