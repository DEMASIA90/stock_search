from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

MORNING_INVEST_COMPONENT_VERSION = "11.9"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"

CATEGORY_DIR = {
    "KR": "kr",
    "KR_ETF": "kr-etf",
    "US": "us",
    "US_ETF": "us-etf",
}

LEGACY_FILE = {
    "KR": "kr.json",
    "KR_ETF": "kr_etf.json",
    "US": "us.json",
    "US_ETF": "us_etf.json",
}

ROOT_UNIVERSE_CACHE = {
    "KR": "universe_kr.json",
    "KR_ETF": "universe_kr_etf.json",
    "US": "universe_us.json",
    "US_ETF": "universe_us_etf.json",
}

ALL_CATEGORIES = ["KR", "KR_ETF", "US", "US_ETF"]
QUIZ_DIRS = ["kr", "kr-etf", "us", "us-etf"]
RESTORE_BY_MARKET = {
    "ALL": ALL_CATEGORIES,
    "KR": ALL_CATEGORIES,
    "KR_ETF": ALL_CATEGORIES,
    "KR_GROUP": ALL_CATEGORIES,
    "US": ALL_CATEGORIES,
    "US_ETF": ALL_CATEGORIES,
    "US_GROUP": ALL_CATEGORIES,
}


def safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in zf.infolist():
        target = (destination / member.filename).resolve()
        if root != target and root not in target.parents:
            raise RuntimeError(f"Unsafe ZIP path: {member.filename}")
    zf.extractall(destination)


def download(url: str, output: Path, timeout: int = 90) -> None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DTC-GitHubActions/1.2",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if getattr(response, "status", 200) != 200:
            raise RuntimeError(f"HTTP {response.status}")
        output.write_bytes(response.read())


def detail_filename(item: dict) -> str:
    raw = str(item.get("symbol") or item.get("ticker") or "stock")
    symbol = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    symbol = (symbol or "stock")[:36]
    digest = hashlib.sha1(str(item.get("ticker", raw)).encode("utf-8")).hexdigest()[:12]
    return f"{symbol}-{digest}.json"


def summary_item(item: dict, detail_path: str) -> dict:
    bt = item.get("backtest") or {}
    return {
        "ticker": item.get("ticker"),
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "category": item.get("category"),
        "exchange": item.get("exchange"),
        "currency": item.get("currency"),
        "date": item.get("date"),
        "close": item.get("close"),
        "day_change_pct": item.get("day_change_pct"),
        "rank": item.get("rank"),
        "base_score": item.get("base_score"),
        "score": item.get("score"),
        "display_score": item.get("display_score", item.get("score")),
        "scores": item.get("scores") or {},
        "trade_signals": item.get("trade_signals") or {},
        "sector": item.get("sector") or "—",
        "market_size_krw": item.get("market_size_krw"),
        "market_size_basis": item.get("market_size_basis"),
        "backtest": {
            "available": bool(bt.get("available")),
            "reason": bt.get("reason"),
            "evaluation_days": bt.get("evaluation_days"),
            "signals": bt.get("signals"),
            "signals_used": bt.get("signals_used"),
            "current_score_threshold": bt.get("current_score_threshold"),
            "avg_60d": bt.get("avg_60d"),
            "raw_avg_60d": bt.get("raw_avg_60d"),
            "median_60d": bt.get("median_60d"),
            "win_60d": bt.get("win_60d"),
            "excluded_low": bt.get("excluded_low"),
            "excluded_high": bt.get("excluded_high"),
        },
        "detail_path": detail_path,
    }


def build_bundle(dest: Path) -> None:
    summary = dest / "summary.json"
    stocks = dest / "stocks"
    bundle = dest / "bundle.zip"
    if not summary.is_file() or not stocks.is_dir():
        raise RuntimeError("cannot build bundle: summary/stocks missing")

    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(summary, "summary.json")
        for optional_name in ("sizes.json", "backtest_model.json", "yahoo-unavailable.json", "universe.json"):
            optional = dest / optional_name
            if optional.is_file():
                zf.write(optional, optional_name)
        for detail in sorted(stocks.glob("*.json")):
            zf.write(detail, f"stocks/{detail.name}")


def restore_v7_bundle(category: str, base: str) -> bool:
    folder = CATEGORY_DIR[category]
    dest = DATA_DIR / folder
    tmp = Path("/tmp") / f"dtc-{folder}-bundle.zip"
    url = f"{base.rstrip('/')}/data/{folder}/bundle.zip?ts={int(time.time())}"

    print(f"[hydrate] {category}: try v7 bundle")
    download(url, tmp)
    if not zipfile.is_zipfile(tmp):
        raise RuntimeError("downloaded v7 bundle is not a ZIP")

    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tmp, "r") as zf:
        safe_extract(zf, dest)

    if not (dest / "summary.json").is_file():
        raise RuntimeError("summary.json missing after bundle extraction")
    if not (dest / "stocks").is_dir():
        raise RuntimeError("stocks directory missing after bundle extraction")

    shutil.copy2(tmp, dest / "bundle.zip")
    universe = dest / "universe.json"
    if universe.is_file():
        shutil.copy2(universe, DATA_DIR / ROOT_UNIVERSE_CACHE[category])

    print(f"[hydrate] {category}: restored v7 bundle")
    return True


def migrate_legacy_json(category: str, base: str) -> bool:
    """Convert previous v6 monolithic JSON into v7 summary/detail files."""
    legacy_name = LEGACY_FILE[category]
    tmp = Path("/tmp") / f"dtc-{legacy_name}"
    url = f"{base.rstrip('/')}/data/{legacy_name}?ts={int(time.time())}"

    print(f"[hydrate] {category}: try legacy {legacy_name}")
    download(url, tmp)

    payload = json.loads(tmp.read_text(encoding="utf-8"))
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("legacy JSON has no items list")

    dest = DATA_DIR / CATEGORY_DIR[category]
    shutil.rmtree(dest, ignore_errors=True)
    stocks = dest / "stocks"
    stocks.mkdir(parents=True, exist_ok=True)

    summary_items = []
    for pos, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        if not item.get("rank"):
            item["rank"] = pos

        filename = detail_filename(item)
        relative = f"data/{CATEGORY_DIR[category]}/stocks/{filename}"
        summary_items.append(summary_item(item, relative))
        (stocks / filename).write_text(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    summary_payload = {k: v for k, v in payload.items() if k != "items"}
    summary_payload.update(
        {
            "storage_model": "summary_plus_lazy_stock_detail_v7_legacy_migration",
            "detail_count": len(summary_items),
            "passed_count": summary_payload.get("passed_count", len(summary_items)),
            "items": summary_items,
        }
    )
    (dest / "summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    build_bundle(dest)
    print(f"[hydrate] {category}: migrated legacy data ({len(summary_items):,} items)")
    return True


def migrate_checkout_legacy(category: str, backup_dir: Path) -> bool:
    legacy_name = LEGACY_FILE[category]
    src = backup_dir / legacy_name
    if not src.is_file():
        return False

    print(f"[hydrate] {category}: try checked-out legacy {legacy_name}")
    payload = json.loads(src.read_text(encoding="utf-8"))
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("checked-out legacy JSON has no items list")

    dest = DATA_DIR / CATEGORY_DIR[category]
    shutil.rmtree(dest, ignore_errors=True)
    stocks = dest / "stocks"
    stocks.mkdir(parents=True, exist_ok=True)

    summary_items = []
    for pos, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        if not item.get("rank"):
            item["rank"] = pos
        filename = detail_filename(item)
        relative = f"data/{CATEGORY_DIR[category]}/stocks/{filename}"
        summary_items.append(summary_item(item, relative))
        (stocks / filename).write_text(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    summary_payload = {k: v for k, v in payload.items() if k != "items"}
    summary_payload.update({
        "storage_model": "summary_plus_lazy_stock_detail_v7_checkout_migration",
        "detail_count": len(summary_items),
        "passed_count": summary_payload.get("passed_count", len(summary_items)),
        "items": summary_items,
    })
    (dest / "summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    old_cache = backup_dir / ROOT_UNIVERSE_CACHE[category]
    if old_cache.is_file():
        shutil.copy2(old_cache, DATA_DIR / ROOT_UNIVERSE_CACHE[category])
        shutil.copy2(old_cache, dest / "universe.json")

    build_bundle(dest)
    print(f"[hydrate] {category}: migrated checked-out legacy ({len(summary_items):,} items)")
    return True


def restore_category(category: str, bases: list[str], checkout_backup: Path | None = None) -> bool:
    errors = []
    bases = [b for b in bases if b and b.strip()]
    for base in bases:
        try:
            return restore_v7_bundle(category, base)
        except Exception as exc:
            errors.append(f"v7@{base}: {type(exc).__name__}: {exc}")

        try:
            return migrate_legacy_json(category, base)
        except Exception as exc:
            errors.append(f"legacy@{base}: {type(exc).__name__}: {exc}")

    if checkout_backup is not None:
        try:
            if migrate_checkout_legacy(category, checkout_backup):
                return True
        except Exception as exc:
            errors.append(f"checkout-legacy: {type(exc).__name__}: {exc}")

    shutil.rmtree(DATA_DIR / CATEGORY_DIR[category], ignore_errors=True)
    print(f"[hydrate] {category}: no previous snapshot available")
    for err in errors:
        print(f"[hydrate]   {err}")
    return False


def restore_optional_live_file(relative_path: str, bases: list[str]) -> bool:
    target = DATA_DIR / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for base in bases:
        tmp = Path('/tmp') / f"dtc-optional-{hashlib.sha1(relative_path.encode()).hexdigest()[:10]}"
        try:
            url = f"{base.rstrip('/')}/data/{relative_path}?ts={int(time.time())}"
            download(url, tmp, timeout=45)
            if not tmp.is_file() or tmp.stat().st_size <= 0:
                raise RuntimeError('empty file')
            target.write_bytes(tmp.read_bytes())
            print(f"[hydrate] optional restored: {relative_path}")
            return True
        except Exception as exc:
            errors.append(f"{base}: {type(exc).__name__}: {exc}")
    print(f"[hydrate] optional missing: {relative_path}")
    return False


def restore_quiz_bundle(folder: str, bases: list[str]) -> bool:
    dest = DATA_DIR / "quiz" / folder
    errors = []
    for base in bases:
        tmp = Path('/tmp') / f"dtc-quiz-{folder}.zip"
        try:
            url = f"{base.rstrip('/')}/data/quiz/{folder}/bundle.zip?ts={int(time.time())}"
            download(url, tmp, timeout=90)
            if not zipfile.is_zipfile(tmp):
                raise RuntimeError('quiz bundle is not a ZIP')
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(tmp, 'r') as zf:
                safe_extract(zf, dest)
            if not (dest / 'manifest.json').is_file() or not (dest / 'stocks').is_dir():
                raise RuntimeError('quiz manifest/stocks missing after extraction')
            shutil.copy2(tmp, dest / 'bundle.zip')
            print(f"[hydrate] quiz restored: {folder}")
            return True
        except Exception as exc:
            errors.append(f"{base}: {type(exc).__name__}: {exc}")
    shutil.rmtree(dest, ignore_errors=True)
    print(f"[hydrate] quiz missing: {folder}")
    return False



def _valid_local_category(category: str) -> bool:
    dest = DATA_DIR / CATEGORY_DIR[category]
    return (
        (dest / "summary.json").is_file()
        and (dest / "bundle.zip").is_file()
        and (dest / "stocks").is_dir()
    )


def _valid_local_quiz(folder: str) -> bool:
    dest = DATA_DIR / "quiz" / folder
    return (
        (dest / "manifest.json").is_file()
        and (dest / "bundle.zip").is_file()
        and (dest / "stocks").is_dir()
    )


def _extract_action_cache(snapshot: Path) -> Path | None:
    """Extract our own Actions snapshot and return its docs/data directory."""
    if not snapshot.is_file() or snapshot.stat().st_size <= 0:
        return None
    seed_root = Path("/tmp/dtc-actions-cache-seed")
    shutil.rmtree(seed_root, ignore_errors=True)
    seed_root.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(snapshot, "r:gz") as tf:
            root = seed_root.resolve()
            for member in tf.getmembers():
                target = (seed_root / member.name).resolve()
                if root != target and root not in target.parents:
                    raise RuntimeError(f"Unsafe cache TAR path: {member.name}")
            tf.extractall(seed_root)
    except Exception as exc:
        print(f"[hydrate] Actions cache snapshot unusable: {type(exc).__name__}: {exc}")
        return None
    seed_data = seed_root / "docs" / "data"
    if not seed_data.is_dir():
        print("[hydrate] Actions cache snapshot has no docs/data tree")
        return None
    return seed_data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=list(RESTORE_BY_MARKET))
    parser.add_argument("--base-url", action="append", dest="base_urls", default=[])
    parser.add_argument("--cache-snapshot", default="", help="Optional Actions data-snapshot.tar.gz to seed docs/data before network hydration")
    args = parser.parse_args()

    bases = [b.strip() for b in args.base_urls if b and b.strip()]
    if not bases:
        configured = os.environ.get("DTC_FIREBASE_ORIGIN", "").strip().rstrip("/")
        bases = [b for b in [configured, "https://demasia90.github.io/stock_search"] if b]
    print(f"[hydrate] bootstrap bases={bases}")

    # Migration safety: old tracked v6 JSON/cache files may still exist in the
    # checkout. Preserve them before clearing docs/data so they remain a final
    # bootstrap source when Hosting has no KR snapshot yet.
    checkout_backup = Path("/tmp/dtc-checkout-legacy")
    shutil.rmtree(checkout_backup, ignore_errors=True)
    checkout_backup.mkdir(parents=True, exist_ok=True)
    for name in [*LEGACY_FILE.values(), *ROOT_UNIVERSE_CACHE.values()]:
        src = DATA_DIR / name
        if src.is_file():
            shutil.copy2(src, checkout_backup / name)
            print(f"[hydrate] preserved checkout legacy: {name}")

    cache_seed = _extract_action_cache(Path(args.cache_snapshot)) if args.cache_snapshot else None

    shutil.rmtree(DATA_DIR, ignore_errors=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if cache_seed is not None:
        shutil.copytree(cache_seed, DATA_DIR, dirs_exist_ok=True)
        print(f"[hydrate] seeded docs/data from Actions cache: {args.cache_snapshot}")

    restored = []
    missing = []
    for category in RESTORE_BY_MARKET[args.market]:
        if _valid_local_category(category):
            restored.append(f"{category}(actions-cache)")
            print(f"[hydrate] {category}: use Actions cache; skip live bundle download")
            # Restore scanner-root universe cache from the cached category snapshot.
            universe = DATA_DIR / CATEGORY_DIR[category] / "universe.json"
            if universe.is_file():
                shutil.copy2(universe, DATA_DIR / ROOT_UNIVERSE_CACHE[category])
            continue
        if restore_category(category, bases, checkout_backup=checkout_backup):
            restored.append(category)
        else:
            missing.append(category)

    for folder in QUIZ_DIRS:
        if _valid_local_quiz(folder):
            print(f"[hydrate] quiz/{folder}: use Actions cache")
        else:
            restore_quiz_bundle(folder, bases)
    if not (DATA_DIR / "fx_usdkrw.json").is_file():
        restore_optional_live_file("fx_usdkrw.json", bases)
    else:
        print("[hydrate] fx_usdkrw.json: use Actions cache")

    print(f"[hydrate] restored={restored or 'none'}")
    print(f"[hydrate] missing={missing or 'none'}")
    # Missing previous snapshots are non-fatal here.
    # scanner.py gets a chance to generate them from current market data.


if __name__ == "__main__":
    main()
