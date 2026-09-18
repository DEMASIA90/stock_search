from __future__ import annotations

import shutil
import subprocess
import sys

from update_portfolio import ACCOUNT_KEY, ENV_KEY, PASSWORD_KEY, _saved


def _run(arguments: list[str], *, secret: str | None = None) -> None:
    subprocess.run(
        arguments,
        input=secret,
        text=True,
        check=True,
        stdout=None,
        stderr=None,
    )


def main() -> int:
    if not shutil.which("gh"):
        print("[ERROR] GitHub CLI is not installed: https://cli.github.com/", file=sys.stderr)
        return 1
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
    try:
        _run(["gh", "auth", "status"])
        for name, value in values.items():
            _run(["gh", "secret", "set", name], secret=value)
        _run(["gh", "variable", "set", "ASSET_WEB_ENV", "--body", environment])
        print("Hourly update secrets and the account environment were saved to GitHub.")
        print("The next scheduled run starts at minute 17 of each hour.")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] GitHub setup failed with exit code {exc.returncode}.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
