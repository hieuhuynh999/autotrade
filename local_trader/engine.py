from datetime import datetime, timezone

from local_trader.models import (
    BacktestResult,
    BotState,
    Candle,
    Fill,
    Position,
    SymbolFilters,
    Trade,
)
from local_trader.risk import daily_loss_hit, position_quantity, stop_fill_price, stop_price
from local_trader.strategy import entry_side, exit_needed, momentum


def utc_day(time_ms: int) -> str:
    return datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def new_state(cash: float, time_ms: int) -> BotState:
    return BotState(
        cash=cash,
        position=None,
        day_key=utc_day(time_ms),
        day_start_equity=cash,
        halted=False,
        halt_reason=None,
        last_bar_open_time=None,
    )


def equity_at(state: BotState, price: float) -> float:
    position = state.position
    if position is None:
        return state.cash
    if position.side == "long":
        pnl = (price - position.entry_price) * position.qty
    else:
        pnl = (position.entry_price - price) * position.qty
    margin = position.entry_price * position.qty
    return state.cash + margin + pnl


def _fee(notional: float, fee_rate: float) -> float:
    return abs(notional) * fee_rate


def apply_entry(
    state: BotState,
    side: str,
    qty: float,
    price: float,
    time_ms: int,
    stop: float,
    fee_rate: float,
    reason: str,
    mom: float | None,
) -> Fill:
    notional = price * qty
    fee = _fee(notional, fee_rate)
    state.cash -= notional + fee
    state.position = Position(
        side=side,
        qty=qty,
        entry_price=price,
        stop_price=stop,
        entry_time=time_ms,
    )
    return Fill("entry", side, price, qty, time_ms, fee, 0.0, reason, mom)


def apply_exit(
    state: BotState,
    price: float,
    time_ms: int,
    fee_rate: float,
    reason: str,
    mom: float | None,
) -> Fill:
    position = state.position
    if position is None:
        raise RuntimeError("không có vị thế để thoát")
    if position.side == "long":
        pnl = (price - position.entry_price) * position.qty
    else:
        pnl = (position.entry_price - price) * position.qty
    fee = _fee(price * position.qty, fee_rate)
    margin = position.entry_price * position.qty
    state.cash += margin + pnl - fee
    state.position = None
    return Fill(
        "exit",
        position.side,
        price,
        position.qty,
        time_ms,
        fee,
        pnl - fee,
        reason,
        mom,
    )


def _roll_day(state: BotState, time_ms: int, equity_now: float) -> None:
    day = utc_day(time_ms)
    if state.day_key == day:
        return
    state.day_key = day
    state.day_start_equity = equity_now
    state.halted = False
    state.halt_reason = None


def _mark_halt(state: BotState, equity_now: float, daily_loss_pct: float) -> None:
    if state.halted:
        return
    if daily_loss_hit(state.day_start_equity, equity_now, daily_loss_pct):
        state.halted = True
        state.halt_reason = "lỗ trong ngày chạm ngưỡng, bot dừng mở lệnh mới"


def process_bar(
    state: BotState,
    history: list[Candle],
    funding: float | None,
    *,
    lookback: int,
    entry_threshold: float,
    exit_threshold: float,
    max_abs_funding: float,
    position_pct: float,
    stop_pct: float,
    daily_loss_pct: float,
    fee_rate: float,
    filters: SymbolFilters,
) -> list[Fill]:
    """Xử lý đúng một nến đã đóng. Không vào rồi thoát trên cùng nến."""
    if not history:
        return []
    bar = history[-1]
    _roll_day(state, bar.close_time, equity_at(state, bar.open))
    closes = [candle.close for candle in history]
    mom = momentum(closes, lookback)
    fills: list[Fill] = []
    exited = False

    if state.position is not None:
        stop_fill = stop_fill_price(state.position.side, state.position.stop_price, bar)
        if stop_fill is not None:
            fills.append(
                apply_exit(state, stop_fill, bar.close_time, fee_rate, "cắt lỗ cứng", mom)
            )
            exited = True
        elif exit_needed(state.position.side, mom, exit_threshold):
            fills.append(
                apply_exit(
                    state,
                    bar.close,
                    bar.close_time,
                    fee_rate,
                    "thoát vì momentum gãy",
                    mom,
                )
            )
            exited = True

    _mark_halt(state, equity_at(state, bar.close), daily_loss_pct)
    if state.halted and state.position is not None:
        fills.append(
            apply_exit(
                state,
                bar.close,
                bar.close_time,
                fee_rate,
                "đóng vị thế vì lỗ trong ngày chạm ngưỡng",
                mom,
            )
        )
        exited = True

    if state.position is None and not state.halted and not exited:
        side = entry_side(mom, funding, entry_threshold, max_abs_funding)
        if side is not None:
            qty = position_quantity(
                equity_at(state, bar.close),
                state.cash,
                bar.close,
                position_pct,
                filters,
            )
            if qty > 0:
                raw_stop = stop_price(bar.close, side, stop_pct)
                fills.append(
                    apply_entry(
                        state,
                        side,
                        qty,
                        bar.close,
                        bar.close_time,
                        raw_stop,
                        fee_rate,
                        f"vào {side} theo momentum",
                        mom,
                    )
                )
    return fills


def backtest(
    candles: list[Candle],
    funding_points: list[tuple[int, float]],
    *,
    lookback: int,
    entry_threshold: float,
    exit_threshold: float,
    max_abs_funding: float,
    position_pct: float,
    stop_pct: float,
    daily_loss_pct: float,
    fee_rate: float,
    filters: SymbolFilters,
    cash: float,
) -> BacktestResult:
    if len(candles) < lookback + 2:
        raise ValueError("không đủ nến đóng để backtest")
    ordered = sorted(candles, key=lambda candle: candle.open_time)
    points = sorted(funding_points, key=lambda item: item[0])
    state = new_state(cash, ordered[0].close_time)
    peak = cash
    max_drawdown = 0.0
    fees = 0.0
    trades: list[Trade] = []
    open_fill: Fill | None = None

    for index in range(lookback, len(ordered)):
        history = ordered[: index + 1]
        bar = history[-1]
        funding = _funding_asof(points, bar.close_time)
        fills = process_bar(
            state,
            history,
            funding,
            lookback=lookback,
            entry_threshold=entry_threshold,
            exit_threshold=exit_threshold,
            max_abs_funding=max_abs_funding,
            position_pct=position_pct,
            stop_pct=stop_pct,
            daily_loss_pct=daily_loss_pct,
            fee_rate=fee_rate,
            filters=filters,
        )
        for fill in fills:
            fees += fill.fee
            if fill.kind == "entry":
                open_fill = fill
            elif fill.kind == "exit" and open_fill is not None:
                trades.append(
                    Trade(
                        side=fill.side,
                        entry_time=open_fill.time,
                        exit_time=fill.time,
                        entry_price=open_fill.price,
                        exit_price=fill.price,
                        qty=fill.qty,
                        pnl=fill.pnl,
                        reason=fill.reason,
                    )
                )
                open_fill = None
        marked = equity_at(state, bar.close)
        peak = max(peak, marked)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - marked) / peak)

    if state.position is not None:
        last = ordered[-1]
        fill = apply_exit(
            state,
            last.close,
            last.close_time,
            fee_rate,
            "đóng cuối kỳ backtest",
            momentum([candle.close for candle in ordered], lookback),
        )
        fees += fill.fee
        if open_fill is not None:
            trades.append(
                Trade(
                    side=fill.side,
                    entry_time=open_fill.time,
                    exit_time=fill.time,
                    entry_price=open_fill.price,
                    exit_price=fill.price,
                    qty=fill.qty,
                    pnl=fill.pnl,
                    reason=fill.reason,
                )
            )

    return BacktestResult(
        start_equity=cash,
        ending_equity=state.cash,
        fees=fees,
        max_drawdown=max_drawdown,
        trades=trades,
        halted=state.halted,
        halt_reason=state.halt_reason,
    )


def _funding_asof(points: list[tuple[int, float]], time_ms: int) -> float | None:
    rate: float | None = None
    for funding_time, funding_rate in points:
        if funding_time <= time_ms:
            rate = funding_rate
        else:
            break
    return rate
