from __future__ import annotations

import statistics
from decimal import Decimal

from .models import Candle, IndicatorSet


class IndicatorError(RuntimeError):
    pass


def _stdev(values: list[Decimal]) -> Decimal:
    return Decimal(str(statistics.pstdev(float(value) for value in values)))


def calculate(candles: list[Candle], *, required: int = 300) -> IndicatorSet:
    if len(candles) < max(required, 207):
        raise IndicatorError(f"At least {max(required, 207)} completed daily candles are required.")
    ordered = sorted(candles, key=lambda item: item.start)
    if len({candle.start for candle in ordered}) != len(ordered):
        raise IndicatorError("Duplicate daily candles are not permitted.")
    closes = [candle.close for candle in ordered]
    volumes = [candle.volume for candle in ordered]
    if any(value <= 0 for value in closes) or any(value < 0 for value in volumes):
        raise IndicatorError("Candle price and volume values are out of range.")
    daily_returns = [closes[index] / closes[index - 1] - Decimal(1) for index in range(1, len(closes))]
    rolling_7d = [_stdev(daily_returns[index - 6:index + 1]) for index in range(6, len(daily_returns))]
    current_vol = rolling_7d[-1]
    median_90 = Decimal(str(statistics.median(float(value) for value in rolling_7d[-90:])))
    avg7 = sum(volumes[-7:]) / Decimal(7)
    avg30 = sum(volumes[-30:]) / Decimal(30)
    return IndicatorSet(
        price=closes[-1], ma7=sum(closes[-7:]) / Decimal(7),
        ma200=sum(closes[-200:]) / Decimal(200),
        return_7d=closes[-1] / closes[-8] - Decimal(1),
        average_volume_7d=avg7, average_volume_30d=avg30,
        volume_ratio=avg7 / avg30 if avg30 else Decimal(0),
        realized_volatility_7d=current_vol, median_volatility_90d=median_90,
        volatility_ratio=current_vol / median_90 if median_90 else Decimal("Infinity"),
        completed_candles=len(ordered), latest_candle_start=ordered[-1].start,
    )
