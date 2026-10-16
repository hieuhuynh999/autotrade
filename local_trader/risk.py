import math
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from local_trader.models import Candle, SymbolFilters


def floor_to_step(qty: float, step: float) -> float:
    if step <= 0:
        raise ValueError("step_size phải lớn hơn 0")
    quantum = Decimal(str(step))
    amount = Decimal(str(qty))
    units = (amount / quantum).to_integral_value(rounding=ROUND_DOWN)
    return float(units * quantum)


def format_step(value: float, step: float) -> str:
    quantum = Decimal(str(step))
    amount = Decimal(str(value)).quantize(quantum, rounding=ROUND_DOWN)
    return format(amount, "f")


def round_stop(price: float, tick: float, side: str) -> float:
    quantum = Decimal(str(tick))
    amount = Decimal(str(price))
    rounding = ROUND_DOWN if side == "long" else ROUND_UP
    units = (amount / quantum).to_integral_value(rounding=rounding)
    return float(units * quantum)


def position_quantity(
    equity: float,
    available: float,
    price: float,
    position_pct: float,
    filters: SymbolFilters,
) -> float:
    if equity <= 0 or available <= 0 or price <= 0 or position_pct <= 0:
        return 0.0
    notional = min(equity * position_pct, available * 0.98)
    if notional < filters.min_notional:
        return 0.0
    qty = floor_to_step(notional / price, filters.step_size)
    if qty < filters.min_qty or qty * price < filters.min_notional:
        return 0.0
    return qty


def stop_price(entry: float, side: str, stop_pct: float) -> float:
    if entry <= 0 or stop_pct <= 0 or stop_pct >= 1:
        raise ValueError("entry và stop_pct không hợp lệ")
    if side == "long":
        return entry * (1.0 - stop_pct)
    if side == "short":
        return entry * (1.0 + stop_pct)
    raise ValueError(f"side không hợp lệ: {side}")


def stop_fill_price(side: str, stop: float, bar: Candle) -> float | None:
    if side == "long" and bar.low <= stop:
        return bar.open if bar.open <= stop else stop
    if side == "short" and bar.high >= stop:
        return bar.open if bar.open >= stop else stop
    return None


def mark_hits_stop(side: str, stop: float, mark: float) -> bool:
    if side == "long":
        return mark <= stop
    if side == "short":
        return mark >= stop
    return False


def daily_loss_hit(day_start_equity: float, equity: float, daily_loss_pct: float) -> bool:
    if day_start_equity <= 0 or math.isnan(equity):
        return False
    return (equity - day_start_equity) / day_start_equity <= -daily_loss_pct
