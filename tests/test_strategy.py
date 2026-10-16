import pytest

from local_trader.models import Candle
from local_trader.strategy import (
    closed_candles,
    entry_block_reason,
    entry_side,
    exit_needed,
    momentum,
)


def _candle(close_time: int, close: float = 100.0) -> Candle:
    return Candle(close_time - 3_599_999, close, close, close, close, 1.0, close_time)


def test_closed_candles_drop_the_open_bar():
    now = 10_000
    closed = _candle(9_000, 100)
    still_open = _candle(11_000, 150)
    kept = closed_candles([still_open, closed], now)
    assert kept == [closed]


def test_momentum_uses_only_the_lookback_close():
    closes = [100.0] * 24 + [103.0]
    assert momentum(closes, 24) == pytest.approx(0.03)
    assert momentum(closes[:10], 24) is None


def test_entry_follows_momentum_both_ways():
    assert entry_side(0.02, 0.0001, 0.015, 0.0003) == "long"
    assert entry_side(-0.02, -0.0001, 0.015, 0.0003) == "short"


def test_extreme_funding_blocks_the_side_that_pays():
    assert entry_side(0.02, 0.001, 0.015, 0.0003) is None
    assert "funding quá cao" in entry_block_reason(0.02, 0.001, 0.015, 0.0003)
    assert entry_side(-0.02, -0.001, 0.015, 0.0003) is None
    assert "funding quá âm" in entry_block_reason(-0.02, -0.001, 0.015, 0.0003)


def test_missing_funding_blocks_entry():
    assert entry_side(0.05, None, 0.015, 0.0003) is None
    assert "chưa có funding" in entry_block_reason(0.05, None, 0.015, 0.0003)


def test_exit_when_momentum_breaks():
    assert exit_needed("long", 0.0, 0.0) is True
    assert exit_needed("long", 0.01, 0.0) is False
    assert exit_needed("short", 0.0, 0.0) is True
    assert exit_needed("short", -0.01, 0.0) is False
