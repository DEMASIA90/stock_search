from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    account_no: str
    account_type: str

    @property
    def environment_label(self) -> str:
        return "모의" if self.account_type == "03" else "실계좌"


@dataclass
class Holding:
    market: str
    code: str
    name: str
    qty: float
    avg_price: float
    current_price: float
    eval_amount_krw: float
    pnl_amount_krw: float
    pnl_pct: float
    price_currency: str
    sellable_qty: float | None = None
    position_verified: bool = False
