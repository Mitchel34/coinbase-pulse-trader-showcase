"""Indicator window checks using an arithmetic synthetic candle series."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from pulse_trader.indicators import IndicatorError, calculate
from pulse_trader.models import Candle

NOW = datetime(2000, 1, 1, 0, 0, tzinfo=timezone.utc)


def candles(count=320, *, end=int(NOW.timestamp())):
    """``count`` consecutive completed daily candles ending two days before ``end``, rising in price and volume."""
    base = end - (count + 2) * 86400
    result = []
    for index in range(count):
        start = base + index * 86400
        price = Decimal(100 + 3 * index)
        volume = Decimal(100 + 2 * index)
        result.append(Candle(start, price, price, price, price, volume))
    return result


class IndicatorWindowTests(unittest.TestCase):
    def test_spot_indicators_include_required_windows(self):
        values = calculate(candles())
        self.assertEqual(values.completed_candles, 320)
        self.assertGreater(values.ma7, values.ma200)
        self.assertGreaterEqual(values.return_7d, Decimal("0.02"))
        self.assertGreater(values.average_volume_7d, values.average_volume_30d)

    def test_incomplete_ma_window_is_refused(self):
        with self.assertRaises(IndicatorError):
            calculate(candles(200), required=300)


if __name__ == "__main__":
    unittest.main()
