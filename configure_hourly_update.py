from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.github_cli import ensure_gh
from update_portfolio import ACCOUNT_KEY, ENV_KEY, PASSWORD_KEY, _saved

WORKFLOW = "update-and-deploy.yml"


def _run(arguments: list[str], *, secret: str | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        input=secret,
        text=True,
        check=True,
        capture_output=capture,
    )


def _ensure_github_login(gh: str) -> None:
    try:
        _run([gh, "auth", "status", "--hostname", "github.com"])
        return
    except subprocess.CalledProcessError:
        pass

    print("[INFO] GitHub 로그인이 필요합니다. 브라우저 인증을 시작합니다...")
    print("       브라우저가 열리면 현재 stock_search 저장소에 접근 가능한 GitHub 계정으로 로그인하세요.")
    _run([
        gh,
        "auth",
        "login",
        "--hostname",
        "github.com",
        "--git-protocol",
        "https",
        "--web",
    ])
    _run([gh, "auth", "status", "--hostname", "github.com"])


def main() -> int:
    values = {
        "NHPLUG_APP_KEY": _saved("app_key", allow_legacy=True),
        "NHPLUG_APP_SECRET": _saved("app_secret", allow_legacy=True),
        "ASSET_WEB_ACCOUNT": _saved(ACCOUNT_KEY),
        "PORTFOLIO_PIN": _saved(PASSWORD_KEY),
    }
    environment = _saved(ENV_KEY)
    missing = [name for name, value in values.items() if not value]
    if missing or environment not in {"live", "mock"}:
        print("[ERROR] Run first_deploy.bat once before enabling hourly updates.", file=sys.stderr)
        return 1
    if len(values["PORTFOLIO_PIN"]) != 4 or not values["PORTFOLIO_PIN"].isdigit():
        print("[ERROR] The saved portfolio PIN is not four digits.", file=sys.stderr)
        return 1
    if not (Path(".git") / "config").exists():
        print(
            "[ERROR] configure_hourly_update.bat must be run inside the existing stock_search Git repository.",
            file=sys.stderr,
        )
        return 1

    try:
        gh = str(ensure_gh())
        _ensure_github_login(gh)

        repository = _run(
            [gh, "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            capture=True,
        ).stdout.strip()
        if repository:
            print(f"[OK] GitHub repository: {repository}")

        print("[INFO] GitHub Actions Secrets/Variable을 갱신합니다...")
        for name, value in values.items():
            _run([gh, "secret", "set", name], secret=value)
        _run([gh, "variable", "set", "ASSET_WEB_ENV", "--body", environment])
        print("[OK] Hourly update secrets and account environment were saved to GitHub.")

        try:
            _run([gh, "workflow", "enable", WORKFLOW])
            _run([gh, "workflow", "run", WORKFLOW, "--ref", "main"])
            print("[OK] A verification run was started immediately in GitHub Actions.")
            print("     Run check_hourly_update.bat to inspect its status and failed-step log.")
        except subprocess.CalledProcessError:
            print("[WARN] Secrets were saved, but the workflow could not be started automatically.")
            print("       First push .github/workflows/update-and-deploy.yml to GitHub, then run this file again.")
        print("After verification, scheduled runs start at minute 17 of each hour.")
        return 0
    except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
        code = exc.returncode if isinstance(exc, subprocess.CalledProcessError) else "setup"
        print(f"[ERROR] GitHub setup failed ({code}): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
