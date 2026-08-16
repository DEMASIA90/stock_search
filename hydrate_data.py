from __future__ import annotations

import argparse
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"

CATEGORY_DIR = {
    "KR": "kr",
    "US": "us",
    "US_ETF": "us-etf",
}

RESTORE_BY_MARKET = {
    "ALL": [],
    # Partial refreshes restore all three Hosting snapshots first. The selected
    # category is then overwritten by scanner.py, while its restored universe
    # snapshot remains available as an outage fallback.
    "KR": ["KR", "US", "US_ETF"],
    "US": ["KR", "US", "US_ETF"],
    "US_ETF": ["KR", "US", "US_ETF"],
    "US_GROUP": ["KR", "US", "US_ETF"],
}

ROOT_UNIVERSE_CACHE = {
    "KR": "universe_kr.json",
    "US": "universe_us.json",
    "US_ETF": "universe_us_etf.json",
}


def safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in zf.infolist():
        target = (destination / member.filename).resolve()
        if root != target and root not in target.parents:
            raise RuntimeError(f"Unsafe ZIP path: {member.filename}")
    zf.extractall(destination)


def download(url: str, output: Path) -> None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MorningInvest-GitHubActions/1.0",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        if getattr(response, "status", 200) != 200:
            raise RuntimeError(f"HTTP {response.status}")
        output.write_bytes(response.read())


def restore_category(category: str, bases: list[str]) -> None:
    folder = CATEGORY_DIR[category]
    dest = DATA_DIR / folder
    tmp = Path("/tmp") / f"morning-invest-{folder}.zip"

    errors = []
    for base in bases:
        url = f"{base.rstrip('/')}/data/{folder}/bundle.zip?ts={int(time.time())}"
        try:
            print(f"[hydrate] {category}: {url}")
            download(url, tmp)
            if not zipfile.is_zipfile(tmp):
                raise RuntimeError("downloaded file is not a ZIP")
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(tmp, "r") as zf:
                safe_extract(zf, dest)
            if not (dest / "summary.json").is_file():
                raise RuntimeError("summary.json missing after extraction")
            if not (dest / "stocks").is_dir():
                raise RuntimeError("stocks directory missing after extraction")
            shutil.copy2(tmp, dest / "bundle.zip")

            universe_snapshot = dest / "universe.json"
            if universe_snapshot.is_file():
                shutil.copy2(universe_snapshot, DATA_DIR / ROOT_UNIVERSE_CACHE[category])

            print(f"[hydrate] {category}: restored")
            return
        except Exception as exc:
            errors.append(f"{base}: {type(exc).__name__}: {exc}")
            shutil.rmtree(dest, ignore_errors=True)

    raise RuntimeError(
        f"Unable to restore {category}. Run ALL once after installing v7. "
        + " | ".join(errors)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=list(RESTORE_BY_MARKET))
    parser.add_argument("--base-url", action="append", dest="base_urls", default=[])
    args = parser.parse_args()

    bases = args.base_urls or ["https://morninginv.web.app"]

    shutil.rmtree(DATA_DIR, ignore_errors=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    restore = RESTORE_BY_MARKET[args.market]
    if not restore:
        print("[hydrate] ALL refresh: no previous market data needed")
        return

    print(f"[hydrate] refresh={args.market}; restoring live snapshots={restore}")
    for category in restore:
        restore_category(category, bases)


if __name__ == "__main__":
    main()
