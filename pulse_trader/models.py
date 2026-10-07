from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Asset = Literal["BTC", "ETH", "SOL"]
Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class Product:
    asset: Asset
    product_id: str
    spot_product_id: str
    price: Decimal
    index_price: Decimal
    contract_size: Decimal
    long_margin_rate: Decimal
    short_margin_rate: Decimal
    funding_rate: Decimal | None
    funding_interval_seconds: int
    tradable: bool = True
    price_increment: Decimal | None = None   # venue tick size; None when the product payload lacks it
    quote_increment: Decimal | None = None   # venue quote increment as observed; never substituted for the tick

    @property
    def notional_per_contract(self) -> Decimal:
        return self.price * self.contract_size

    @property
    def basis_percent(self) -> Decimal:
        return (self.price / self.index_price - Decimal(1)) * Decimal(100) if self.index_price else Decimal(0)

    def margin_per_contract(self, direction: Direction) -> Decimal:
        rate = self.long_margin_rate if direction == "LONG" else self.short_margin_rate
        return self.notional_per_contract * rate

    def projected_funding_24h_percent(self, direction: Direction = "LONG") -> Decimal | None:
        """Current-rate daily expense scenario; positive paid, negative received.

        This is not historical accrued funding or a forecast of future rates.
        Missing rate/interval data remains unknown rather than becoming zero.
        """
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("Funding direction must be LONG or SHORT.")
        if self.funding_rate is None or not self.funding_rate.is_finite() or self.funding_interval_seconds <= 0:
            return None
        intervals = Decimal(86400) / Decimal(self.funding_interval_seconds)
        sign = Decimal(1) if direction == "LONG" else Decimal(-1)
        return self.funding_rate * intervals * Decimal(100) * sign

    def projected_funding_24h_usd(
        self, contracts: Decimal = Decimal(1), direction: Direction = "LONG",
    ) -> Decimal | None:
        """Signed expense projection using contract-adjusted current notional."""
        if not contracts.is_finite() or contracts < 0:
            raise ValueError("Funding contract quantity must be finite and nonnegative.")
        percent = self.projected_funding_24h_percent(direction)
        return None if percent is None else self.notional_per_contract * contracts * percent / Decimal(100)

    def adverse_funding_exceeds(
        self, threshold: Decimal = Decimal("0.50"), direction: Direction = "LONG",
    ) -> bool | None:
        """Use the same strict boundary for new entries and existing holdings."""
        percent = self.projected_funding_24h_percent(direction)
        return None if percent is None else percent > threshold


@dataclass(frozen=True)
class Position:
    asset: Asset
    product_id: str
    direction: Direction
    contracts: Decimal
    average_entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    opened_at: str | None = None
    provenance: str | None = None


@dataclass(frozen=True)
class Candle:
    start: int
    low: Decimal
    high: Decimal
    open: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class IndicatorSet:
    price: Decimal
    ma7: Decimal
    ma200: Decimal
    return_7d: Decimal
    average_volume_7d: Decimal
    average_volume_30d: Decimal
    volume_ratio: Decimal
    realized_volatility_7d: Decimal
    median_volatility_90d: Decimal
    volatility_ratio: Decimal
    completed_candles: int
    latest_candle_start: int


@dataclass(frozen=True)
class Score:
    asset: Asset
    technical: int
    narrative: Decimal
    total: Decimal
    components: dict[str, int]
