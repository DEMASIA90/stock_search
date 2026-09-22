from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.config import WATCHLIST_DATA_FILE, ConnectionProfile
from src.nh_client import NhReadOnlyClient
from src.watchlist import MarketAnalyzer
from update_portfolio import _credentials


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


def main() -> int:
    parser = argparse.ArgumentParser(description="관심종목 기술지표 데이터 업데이트")
    parser.add_argument("--non-interactive", action="store_true", help="GitHub Actions 자동 업데이트 모드")
    args = parser.parse_args()
    try:
        app_key, app_secret = _credentials(args.non_interactive)
        client = NhReadOnlyClient()
        client.configure(ConnectionProfile(app_key, app_secret, "live"))
        previous = _read_json(WATCHLIST_DATA_FILE, {})
        print("관심종목의 시가총액과 520거래일 기술지표(일·주·월 Bollinger)를 조회합니다...")
        payload = MarketAnalyzer(client).build_watchlist(previous)
        _atomic_json(WATCHLIST_DATA_FILE, payload)
        print(
            f"완료: 후보 {payload['scanned_count']}개 중 조건 충족 "
            f"{payload['eligible_count']}개"
        )
        if payload["warnings"]:
            print(f"참고: 일부 종목 조회 경고 {len(payload['warnings'])}건")
        return 0
    except KeyboardInterrupt:
        print("\n사용자가 업데이트를 취소했습니다.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\n[관심종목 업데이트 실패] {exc}", file=sys.stderr)
        print("기존 관심종목 데이터는 변경하지 않았습니다.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
