from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import keyring

from src.config import (
    ENCRYPTED_DATA_FILE,
    KEYRING_SERVICE,
    LEGACY_KEYRING_SERVICE,
    ConnectionProfile,
)
from src.nh_client import NhReadOnlyClient
from src.portfolio import (
    decrypt_envelope,
    encrypt_payload,
    holdings_for_web,
    mask_account,
    merge_persistent_events,
    monthly_realized_performance,
    number,
    portfolio_totals,
    realized_summary,
    update_snapshot_history,
    update_yield_history,
)
from src.watchlist import MarketAnalyzer


ENV_KEY = "web_environment"
ACCOUNT_KEY = "web_account"
PASSWORD_KEY = "web_password"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _keyring_get(service: str, name: str) -> str:
    try:
        return keyring.get_password(service, name) or ""
    except Exception:
        return ""


def _keyring_set(service: str, name: str, value: str) -> None:
    try:
        keyring.set_password(service, name, value)
    except Exception:
        if not os.getenv("CI"):
            raise


def _saved(name: str, *, allow_legacy: bool = False) -> str:
    value = _keyring_get(KEYRING_SERVICE, name)
    if not value and allow_legacy:
        value = _keyring_get(LEGACY_KEYRING_SERVICE, name)
        if value:
            _keyring_set(KEYRING_SERVICE, name, value)
    return value


def _credentials(non_interactive: bool = False) -> tuple[str, str]:
    app_key = os.getenv("NHPLUG_APP_KEY", "") or _saved("app_key", allow_legacy=True)
    app_secret = os.getenv("NHPLUG_APP_SECRET", "") or _saved("app_secret", allow_legacy=True)
    if app_key and app_secret:
        return app_key.strip(), app_secret.strip()

    if non_interactive:
        raise ValueError("자동 업데이트용 NHPLUG_APP_KEY와 NHPLUG_APP_SECRET Secret이 필요합니다.")

    print("\n처음 한 번만 NHPLUG API 정보를 입력합니다.")
    app_key = input("AppKey: ").strip()
    app_secret = getpass.getpass("AppSecret (화면에 표시되지 않음): ").strip()
    if not app_key or not app_secret:
        raise ValueError("AppKey와 AppSecret을 모두 입력해야 합니다.")
    _keyring_set(KEYRING_SERVICE, "app_key", app_key)
    _keyring_set(KEYRING_SERVICE, "app_secret", app_secret)
    return app_key, app_secret


def _choose_environment(force: bool, non_interactive: bool = False) -> str:
    saved = "" if force else _saved(ENV_KEY)
    requested = (os.getenv("ASSET_WEB_ENV") or saved).strip().lower()
    if requested in {"live", "mock"}:
        return requested
    if non_interactive:
        raise ValueError("자동 업데이트용 ASSET_WEB_ENV 변수(live 또는 mock)가 필요합니다.")
    answer = input("조회 환경 [1: 실계좌, 2: 모의투자 / 기본 1]: ").strip() or "1"
    mapping = {"1": "live", "2": "mock", "live": "live", "mock": "mock"}
    if answer.lower() not in mapping:
        raise ValueError("1(실계좌) 또는 2(모의투자)를 선택해 주세요.")
    return mapping[answer.lower()]


def _choose_account(client: NhReadOnlyClient, force: bool, non_interactive: bool = False) -> str:
    accounts = client.accounts()
    if not accounts:
        raise RuntimeError("선택한 환경에서 조회 가능한 계좌를 찾지 못했습니다.")

    saved = "" if force else _saved(ACCOUNT_KEY)
    requested = (os.getenv("ASSET_WEB_ACCOUNT") or saved).strip()
    if requested and any(item.account_no == requested for item in accounts):
        return requested
    if len(accounts) == 1 and not force:
        return accounts[0].account_no

    if non_interactive:
        raise ValueError("자동 업데이트용 ASSET_WEB_ACCOUNT Secret이 필요합니다.")

    print("\n웹에서 모니터링할 계좌를 선택하세요.")
    for index, account in enumerate(accounts, 1):
        print(f"  {index}. {mask_account(account.account_no)} ({account.environment_label})")
    raw = input(f"번호 [1-{len(accounts)}]: ").strip()
    try:
        return accounts[int(raw) - 1].account_no
    except (ValueError, IndexError) as exc:
        raise ValueError("목록에 있는 계좌 번호를 선택해 주세요.") from exc


def _new_password() -> str:
    password = getpass.getpass("새 포트폴리오 PIN (숫자 4자리): ")
    if len(password) != 4 or not password.isascii() or not password.isdigit():
        raise ValueError("포트폴리오 PIN은 숫자 4자리여야 합니다.")
    confirmation = getpass.getpass("새 포트폴리오 PIN 다시 입력: ")
    if password != confirmation:
        raise ValueError("두 PIN이 일치하지 않습니다.")
    return password


def _password(change: bool, non_interactive: bool = False) -> tuple[str, str]:
    environment_pin = os.getenv("PORTFOLIO_PIN", "").strip()
    old_environment_pin = os.getenv("PORTFOLIO_OLD_PIN", "").strip()
    if environment_pin:
        if len(environment_pin) != 4 or not environment_pin.isascii() or not environment_pin.isdigit():
            raise ValueError("PORTFOLIO_PIN Secret은 숫자 4자리여야 합니다.")
        current = old_environment_pin or environment_pin
        return current, environment_pin

    current = _saved(PASSWORD_KEY)
    if not current and ENCRYPTED_DATA_FILE.exists():
        envelope = _read_json(ENCRYPTED_DATA_FILE, {})
        if isinstance(envelope, dict) and not envelope.get("empty"):
            current = getpass.getpass("현재 웹 비밀번호 또는 PIN: ")
    is_four_digit_pin = len(current) == 4 and current.isascii() and current.isdigit()
    if change or not is_four_digit_pin:
        if non_interactive:
            raise ValueError("자동 업데이트용 PORTFOLIO_PIN Secret이 필요합니다.")
        return current, _new_password()
    return current, current


def _previous_payload(password: str) -> dict[str, Any]:
    envelope = _read_json(ENCRYPTED_DATA_FILE, {})
    if not isinstance(envelope, dict) or envelope.get("empty") or not envelope:
        return {}
    if not password:
        raise ValueError("기존 데이터 복호화를 위한 현재 웹 비밀번호 또는 PIN이 필요합니다.")
    try:
        return decrypt_envelope(envelope, password)
    except Exception as exc:
        raise ValueError("현재 웹 비밀번호 또는 PIN이 맞지 않아 기존 데이터를 열 수 없습니다.") from exc


def build_payload(
    client: NhReadOnlyClient,
    account_no: str,
    previous: dict[str, Any],
    app_key: str,
    app_secret: str,
) -> dict[str, Any]:
    now = datetime.now().astimezone()
    start = now - timedelta(days=30)

    print("[1/5] 현재 국내·미국 보유종목과 총자산을 조회합니다...")
    holdings, holding_warnings = client.all_holdings(account_no)
    try:
        asset_status = client.account_asset_status(account_no)
    except Exception as exc:
        asset_status = {}
        holding_warnings.append(f"통합 자산현황 조회 실패: {exc}")

    print("[2/5] 최근 1개월 매수·매도 및 실현손익을 조회합니다...")
    trades, trade_warnings = client.transaction_history(
        account_no, start.replace(tzinfo=None), now.replace(tzinfo=None)
    )
    recent_realized, realized_warnings = client.realized_pnl_history(
        account_no, start.replace(tzinfo=None), now.replace(tzinfo=None)
    )
    recent_flows, flow_warnings = client.cash_flow_history(
        account_no, start.replace(tzinfo=None), now.replace(tzinfo=None)
    )

    print("[3/5] 현재 보유종목의 기술지표를 계산합니다...")
    web_holdings = holdings_for_web(holdings)
    market_client = NhReadOnlyClient()
    market_client.configure(ConnectionProfile(app_key, app_secret, "live"))
    analyzer = MarketAnalyzer(market_client)
    web_holdings, indicator_warnings = analyzer.enrich_holdings(web_holdings)

    old_ledger = previous.get("transaction_ledger", previous.get("transactions", []))
    transaction_ledger = merge_persistent_events(
        old_ledger if isinstance(old_ledger, list) else [], trades
    )
    old_realized = previous.get("realized_events", [])
    realized_events = merge_persistent_events(
        old_realized if isinstance(old_realized, list) else [], recent_realized
    )
    old_flows = previous.get("cash_flows", [])
    cash_flows = merge_persistent_events(
        old_flows if isinstance(old_flows, list) else [], recent_flows
    )

    holding_sectors = {
        (str(item.get("market")), str(item.get("code"))): str(item.get("sector") or "기타")
        for item in web_holdings
    }
    for event in realized_events:
        event.setdefault(
            "sector",
            holding_sectors.get((str(event.get("market")), str(event.get("code"))), "기타"),
        )

    totals = portfolio_totals(web_holdings, trades)
    totals.update({key: value for key, value in asset_status.items() if number(value) != 0})
    totals.setdefault("total_asset_krw", totals["evaluation_krw"])
    totals.setdefault("unrealized_pnl_krw", totals["pnl_krw"])
    realized = realized_summary(realized_events)
    old_yield_history = previous.get("yield_history", [])
    if not old_yield_history and isinstance(previous.get("history"), list):
        old_yield_history = [
            {
                "at": item.get("at"),
                "total_asset_krw": item.get("evaluation_krw"),
                "evaluation_krw": item.get("evaluation_krw"),
                "unrealized_pnl_krw": item.get("pnl_krw"),
                "cumulative_realized_krw": 0,
                "net_cash_flow_krw": 0,
            }
            for item in previous.get("history", [])
            if isinstance(item, dict)
        ]
    yield_history = update_yield_history(
        old_yield_history if isinstance(old_yield_history, list) else [],
        totals=totals,
        realized=realized,
        cash_flows=cash_flows,
        at=now,
    )
    old_history = previous.get("history", []) if isinstance(previous, dict) else []
    history = update_snapshot_history(
        old_history if isinstance(old_history, list) else [], totals, now
    )
    monthly = monthly_realized_performance(realized_events, yield_history)
    print("[4/5] 누적 이력과 월별 수익 통계를 갱신합니다...")
    return {
        "schema_version": 3,
        "updated_at": now.isoformat(timespec="seconds"),
        "period": {"start": start.strftime("%Y-%m-%d"), "end": now.strftime("%Y-%m-%d")},
        "recording_started_at": previous.get("recording_started_at") or now.isoformat(timespec="seconds"),
        "account": {
            "masked": mask_account(account_no),
            "environment": client.environment,
        },
        "totals": totals,
        "holdings": web_holdings,
        "transactions": trades,
        "transaction_ledger": transaction_ledger,
        "realized_events": realized_events,
        "cash_flows": cash_flows,
        "realized_summary": realized,
        "monthly_performance": monthly,
        "yield_history": yield_history,
        "history": history,
        "warnings": (
            holding_warnings + trade_warnings + realized_warnings
            + flow_warnings + indicator_warnings
        )[:50],
        "source": "NH투자증권 Namuh PLUG (조회 전용)",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub Pages용 암호화 자산 데이터 업데이트")
    parser.add_argument("--configure", action="store_true", help="조회 환경과 계좌를 다시 선택")
    parser.add_argument("--change-password", action="store_true", help="포트폴리오 4자리 PIN 변경")
    parser.add_argument("--non-interactive", action="store_true", help="GitHub Actions 자동 업데이트 모드")
    args = parser.parse_args()

    try:
        app_key, app_secret = _credentials(args.non_interactive)
        environment = _choose_environment(args.configure, args.non_interactive)
        client = NhReadOnlyClient()
        client.configure(ConnectionProfile(app_key, app_secret, environment))
        account_no = _choose_account(client, args.configure, args.non_interactive)
        old_password, new_password = _password(args.change_password, args.non_interactive)
        previous = _previous_payload(old_password)

        print(
            f"선택 계좌: {mask_account(account_no)} / "
            f"{'실계좌' if environment == 'live' else '모의투자'}"
        )
        payload = build_payload(client, account_no, previous, app_key, app_secret)
        print("[5/5] 브라우저에서만 열 수 있도록 데이터를 암호화합니다...")
        envelope = encrypt_payload(payload, new_password)
        _atomic_json(ENCRYPTED_DATA_FILE, envelope)

        _keyring_set(KEYRING_SERVICE, ENV_KEY, environment)
        _keyring_set(KEYRING_SERVICE, ACCOUNT_KEY, account_no)
        _keyring_set(KEYRING_SERVICE, PASSWORD_KEY, new_password)
        print(
            f"완료: 보유종목 {len(payload['holdings'])}개, "
            f"최근 체결 {len(payload['transactions'])}건"
        )
        if payload["warnings"]:
            print(f"참고: 조회 경고 {len(payload['warnings'])}건이 웹에 표시됩니다.")
        return 0
    except KeyboardInterrupt:
        print("\n사용자가 업데이트를 취소했습니다.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\n[업데이트 실패] {exc}", file=sys.stderr)
        print("기존 암호화 웹 데이터는 변경하지 않았습니다.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
