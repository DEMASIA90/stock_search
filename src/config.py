from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENCRYPTED_DATA_FILE = ROOT / "docs" / "data" / "portfolio.enc.json"
WATCHLIST_DATA_FILE = ROOT / "docs" / "data" / "watchlist.json"
WATCHLIST_UNIVERSE_FILE = ROOT / "config" / "watchlist_universe.json"

LIVE_BASE = "https://api.nhplug.com:8443"
MOCK_BASE = "https://moapi.nhplug.com:8443"
AUTH_BASE = "https://api.nhplug.com:8443"
INSTRUMENTS_BASE = "https://www.nhplug.com/instruments"

KEYRING_SERVICE = "PersonalAssetWeb_v2"
LEGACY_KEYRING_SERVICE = "NamuhPLUG_HTS_v1"
ENVELOPE_AAD = b"PersonalAssetWeb:v1"
PBKDF2_ITERATIONS = 310_000


@dataclass(frozen=True)
class ConnectionProfile:
    app_key: str
    app_secret: str
    environment: str

    @property
    def base_url(self) -> str:
        return MOCK_BASE if self.environment == "mock" else LIVE_BASE

    @property
    def required_account_types(self) -> set[str]:
        return {"03"} if self.environment == "mock" else {"01", "02"}
