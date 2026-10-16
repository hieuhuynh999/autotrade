from local_trader.models import Candle, SymbolFilters
from local_trader.risk import (
    daily_loss_hit,
    floor_to_step,
    position_quantity,
    stop_fill_price,
    stop_price,
)

FILTERS = SymbolFilters(step_size=0.001, min_qty=0.001, tick_size=0.1, min_notional=50)


def test_quantity_floors_to_step_and_respects_min_notional():
    assert floor_to_step(1.239, 0.01) == 1.23
    qty = position_quantity(10_000, 10_000, 102, 0.10, FILTERS)
    assert qty == 9.803
    assert position_quantity(100, 100, 100, 0.10, FILTERS) == 0.0


def test_stop_and_gap_fill():
    assert stop_price(100, "long", 0.02) == 98
    assert stop_price(100, "short", 0.02) == 102
    bar = Candle(0, 90, 101, 89, 95, 1, 1)
    assert stop_fill_price("long", 98, bar) == 90
    assert stop_fill_price("short", 110, bar) is None


def test_daily_loss_threshold():
    assert daily_loss_hit(100, 96, 0.03) is True
    assert daily_loss_hit(100, 98, 0.03) is False
