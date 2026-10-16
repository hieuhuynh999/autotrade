import time
from datetime import datetime, timezone

from local_trader.binance_client import BinanceDemoClient, BinanceError
from local_trader.config import Settings
from local_trader.engine import equity_at, process_bar, utc_day
from local_trader.models import BotState, Candle, Fill, Position
from local_trader.risk import (
    daily_loss_hit,
    format_step,
    mark_hits_stop,
    position_quantity,
    round_stop,
    stop_price,
)
from local_trader.storage import Store
from local_trader.strategy import (
    closed_candles,
    entry_block_reason,
    entry_side,
    exit_needed,
    momentum,
)


def sync_market(settings: Settings, store: Store, client: BinanceDemoClient, *, full: bool) -> str:
    if full:
        candles, funding = client.download_history(settings.symbol, settings.interval)
    else:
        candles = client.klines(settings.symbol, settings.interval, limit=200)
        funding = client.funding_history(settings.symbol, limit=50)
    filters = client.symbol_filters(settings.symbol)
    store.upsert_candles(settings.symbol, settings.interval, candles)
    store.upsert_funding(settings.symbol, funding)
    store.save_filters(settings.symbol, filters)
    candle_count, funding_count = store.counts(settings.symbol, settings.interval)
    return (
        f"đã lưu {candle_count} nến {settings.interval} và {funding_count} điểm funding "
        f"cho {settings.symbol}"
    )


def describe_signal(settings: Settings, store: Store, now_ms: int) -> str:
    closed, funding, mom = _context(settings, store, now_ms)
    if not closed:
        return "chưa có nến đã đóng. Chạy sync trước."
    lines = [
        f"symbol {settings.symbol}  khung {settings.interval}",
        f"nến đóng gần nhất { _fmt_time(closed[-1].close_time)}  giá {closed[-1].close:.4f}",
        "momentum: chưa đủ nến" if mom is None else f"momentum {lookback_label(settings)}: {mom * 100:.2f}%",
        "funding: chưa có" if funding is None else f"funding gần nhất: {funding * 100:.4f}%",
    ]
    block = entry_block_reason(
        mom,
        funding,
        settings.entry_threshold,
        settings.max_abs_funding,
    )
    side = entry_side(mom, funding, settings.entry_threshold, settings.max_abs_funding)
    if side is None:
        lines.append(f"tín hiệu vào lệnh: không — {block}")
    else:
        lines.append(f"tín hiệu vào lệnh: {side}")
    lines.append("Quyết định này chỉ dùng nến đã đóng.")
    return "\n".join(lines)


def run_paper_once(settings: Settings, store: Store, now_ms: int) -> str:
    closed = closed_candles(store.candles(settings.symbol, settings.interval), now_ms)
    if len(closed) < settings.lookback + 1:
        return "chưa đủ nến đóng. Chạy sync trước."
    filters = store.filters(settings.symbol)
    if filters is None:
        return "chưa có bước giá của symbol. Chạy sync trước."
    points = store.funding(settings.symbol)
    state = store.load_paper(settings.paper_cash, now_ms)
    pending = _pending_bars(closed, state.last_bar_open_time)
    if not pending:
        return _status_line(settings, state, equity_at(state, closed[-1].close), "đã xử lý nến này, chờ nến 1 giờ đóng tiếp")
    messages: list[str] = []
    for bar in pending:
        history = [candle for candle in closed if candle.open_time <= bar.open_time]
        funding = _funding_asof(points, bar.close_time)
        fills = process_bar(state, history, funding, **_bar_kwargs(settings), filters=filters)
        state.last_bar_open_time = bar.open_time
        for fill in fills:
            messages.append(_format_fill(fill))
            store.add_event(
                ts=fill.time,
                mode="paper",
                kind=fill.kind,
                symbol=settings.symbol,
                side=fill.side,
                qty=fill.qty,
                price=fill.price,
                reason=fill.reason,
                momentum=fill.momentum,
                equity=equity_at(state, fill.price),
            )
        if not fills and state.position is None and not state.halted:
            mom = momentum([candle.close for candle in history], settings.lookback)
            reason = entry_block_reason(
                mom, funding, settings.entry_threshold, settings.max_abs_funding
            )
            if reason:
                messages.append(f"nến {_fmt_time(bar.close_time)}: {reason}")
            elif entry_side(mom, funding, settings.entry_threshold, settings.max_abs_funding):
                messages.append(
                    f"nến {_fmt_time(bar.close_time)}: khối lượng nhỏ hơn mức tối thiểu của sàn"
                )
    store.save_paper(state)
    equity = equity_at(state, closed[-1].close)
    messages.append(_status_line(settings, state, equity, "paper"))
    return "\n".join(messages)


def run_demo_once(settings: Settings, store: Store, client: BinanceDemoClient, now_ms: int) -> str:
    if not settings.api_key or not settings.api_secret:
        raise BinanceError(None, "MODE=demo cần API key tạo tại https://demo.binance.com")
    sync_market(settings, store, client, full=False)
    closed = closed_candles(store.candles(settings.symbol, settings.interval), now_ms)
    if len(closed) < settings.lookback + 1:
        return "chưa đủ nến đóng trên demo."
    filters = store.filters(settings.symbol)
    if filters is None:
        return "không lấy được bộ lọc khối lượng."
    account = client.account()
    equity = float(account["totalMarginBalance"])
    available = float(account["availableBalance"])
    if account.get("canWithdraw"):
        print("cảnh báo: key đang bật quyền rút. Với demo thì tiền là tiền ảo, nhưng đừng dùng key sàn thật.")
    position = _position_from_account(account, settings.symbol)
    state = store.load_demo_state(now_ms, equity)
    saved_stop, saved_entry, leverage_ready = store.demo_extra()
    if not leverage_ready:
        client.prepare_symbol(settings.symbol, settings.leverage)
        leverage_ready = True
    if position is not None and saved_stop is not None:
        position = Position(
            position.side,
            position.qty,
            position.entry_price,
            saved_stop,
            saved_entry or position.entry_time,
        )
    elif position is not None:
        position = Position(
            position.side,
            position.qty,
            position.entry_price,
            stop_price(position.entry_price, position.side, settings.stop_pct),
            position.entry_time,
        )
    state.position = position
    state.cash = available
    mark = client.mark_price(settings.symbol)
    messages: list[str] = []
    _roll_demo_day(state, now_ms, equity)

    if state.position is not None and mark_hits_stop(state.position.side, state.position.stop_price, mark):
        fill_price = _close_demo(settings, store, client, state, filters, "cắt lỗ cứng", now_ms)
        messages.append(f"cắt lỗ cứng tại {fill_price:.4f}")
        equity = equity_at(state, fill_price)

    if daily_loss_hit(state.day_start_equity, equity, settings.daily_loss_pct):
        state.halted = True
        state.halt_reason = "lỗ trong ngày chạm ngưỡng, bot dừng mở lệnh mới"
        if state.position is not None:
            fill_price = _close_demo(
                settings,
                store,
                client,
                state,
                filters,
                "đóng vị thế vì lỗ trong ngày chạm ngưỡng",
                now_ms,
            )
            messages.append(f"đóng vì lỗ ngày tại {fill_price:.4f}")

    latest = closed[-1]
    if state.last_bar_open_time != latest.open_time:
        history = closed
        mom = momentum([candle.close for candle in history], settings.lookback)
        funding = _funding_asof(store.funding(settings.symbol), latest.close_time)
        if state.position is not None and exit_needed(state.position.side, mom, settings.exit_threshold):
            fill_price = _close_demo(settings, store, client, state, filters, "thoát vì momentum gãy", now_ms)
            messages.append(f"thoát vì momentum gãy tại {fill_price:.4f}")
        if state.position is None and not state.halted:
            side = entry_side(mom, funding, settings.entry_threshold, settings.max_abs_funding)
            block = entry_block_reason(mom, funding, settings.entry_threshold, settings.max_abs_funding)
            if side is None:
                messages.append(block or "không vào lệnh")
            else:
                account = client.account()
                equity = float(account["totalMarginBalance"])
                available = float(account["availableBalance"])
                qty = position_quantity(equity, available, mark, settings.position_pct, filters)
                if qty <= 0:
                    messages.append("không vào lệnh: khối lượng nhỏ hơn mức tối thiểu của sàn")
                else:
                    opened = _open_demo(settings, store, client, state, filters, side, qty, mom, now_ms)
                    messages.append(opened)
        state.last_bar_open_time = latest.open_time
    else:
        messages.append("đã xử lý nến này, vẫn kiểm tra cắt lỗ theo mark price")

    equity = float(client.account()["totalMarginBalance"])
    store.save_demo_state(state, leverage_ready=leverage_ready)
    messages.append(_status_line(settings, state, equity, "demo"))
    return "\n".join(messages)


def _open_demo(
    settings: Settings,
    store: Store,
    client: BinanceDemoClient,
    state: BotState,
    filters,
    side: str,
    qty: float,
    mom: float | None,
    now_ms: int,
) -> str:
    order_side = "BUY" if side == "long" else "SELL"
    order = client.market_order(settings.symbol, order_side, format_step(qty, filters.step_size))
    fill = float(order.get("avgPrice") or 0)
    if fill <= 0:
        fill = client.mark_price(settings.symbol)
    raw_stop = stop_price(fill, side, settings.stop_pct)
    trigger = round_stop(raw_stop, filters.tick_size, side)
    state.position = Position(side, qty, fill, trigger, now_ms)
    state.cash = max(0.0, state.cash - fill * qty)
    stop_side = "SELL" if side == "long" else "BUY"
    stop_note = f"cắt lỗ sàn đặt tại {format_step(trigger, filters.tick_size)}"
    try:
        client.place_stop(settings.symbol, stop_side, format_step(trigger, filters.tick_size))
    except BinanceError as exc:
        stop_note = f"không đặt được STOP_MARKET trên sàn ({exc}). Bot vẫn cắt lỗ bằng mark price mỗi vòng."
    store.add_event(
        ts=now_ms,
        mode="demo",
        kind="entry",
        symbol=settings.symbol,
        side=side,
        qty=qty,
        price=fill,
        reason="vào lệnh demo theo momentum",
        momentum=mom,
        equity=None,
    )
    return f"vào {side} {qty} tại {fill:.4f}. {stop_note}"


def _close_demo(
    settings: Settings,
    store: Store,
    client: BinanceDemoClient,
    state: BotState,
    filters,
    reason: str,
    now_ms: int,
) -> float:
    position = state.position
    if position is None:
        raise RuntimeError("không có vị thế demo để đóng")
    try:
        client.cancel_stops(settings.symbol)
    except BinanceError:
        pass
    close_side = "SELL" if position.side == "long" else "BUY"
    try:
        order = client.market_order(
            settings.symbol,
            close_side,
            format_step(position.qty, filters.step_size),
            reduce_only=True,
        )
    except BinanceError as exc:
        if exc.code not in {-2022, -4118}:
            raise
        state.position = None
        return client.mark_price(settings.symbol)
    fill = float(order.get("avgPrice") or 0)
    if fill <= 0:
        fill = client.mark_price(settings.symbol)
    state.position = None
    store.add_event(
        ts=now_ms,
        mode="demo",
        kind="exit",
        symbol=settings.symbol,
        side=position.side,
        qty=position.qty,
        price=fill,
        reason=reason,
        momentum=None,
        equity=None,
    )
    return fill


def _roll_demo_day(state: BotState, now_ms: int, equity: float) -> None:
    day = utc_day(now_ms)
    if state.day_key != day:
        state.day_key = day
        state.day_start_equity = equity
        state.halted = False
        state.halt_reason = None


def _position_from_account(account: dict, symbol: str) -> Position | None:
    for row in account.get("positions", []):
        if row.get("symbol") != symbol:
            continue
        if row.get("positionSide") not in (None, "BOTH"):
            raise BinanceError(
                None,
                "tài khoản demo đang ở hedge mode. Hãy chuyển về One-way mode rồi chạy lại.",
            )
        qty = float(row.get("positionAmt") or 0)
        if qty == 0:
            continue
        entry = float(row.get("entryPrice") or 0)
        side = "long" if qty > 0 else "short"
        return Position(side, abs(qty), entry, 0.0, int(time.time() * 1000))
    return None


def _context(settings: Settings, store: Store, now_ms: int):
    closed = closed_candles(store.candles(settings.symbol, settings.interval), now_ms)
    funding = _funding_asof(store.funding(settings.symbol), now_ms)
    mom = None
    if closed:
        mom = momentum([candle.close for candle in closed], settings.lookback)
    return closed, funding, mom


def _pending_bars(closed: list[Candle], last_open_time: int | None) -> list[Candle]:
    if last_open_time is None:
        return closed[-1:]
    return [candle for candle in closed if candle.open_time > last_open_time]


def _funding_asof(points: list[tuple[int, float]], time_ms: int) -> float | None:
    rate = None
    for funding_time, funding_rate in points:
        if funding_time <= time_ms:
            rate = funding_rate
        else:
            break
    return rate


def _bar_kwargs(settings: Settings) -> dict:
    return {
        "lookback": settings.lookback,
        "entry_threshold": settings.entry_threshold,
        "exit_threshold": settings.exit_threshold,
        "max_abs_funding": settings.max_abs_funding,
        "position_pct": settings.position_pct,
        "stop_pct": settings.stop_pct,
        "daily_loss_pct": settings.daily_loss_pct,
        "fee_rate": settings.fee_rate,
    }


def _format_fill(fill: Fill) -> str:
    mom = "" if fill.momentum is None else f", momentum {fill.momentum * 100:.2f}%"
    pnl = "" if fill.kind == "entry" else f", pnl {fill.pnl:.4f}"
    return (
        f"{fill.reason}: {fill.side} {fill.qty} tại {fill.price:.4f} "
        f"(phí {fill.fee:.4f}{pnl}{mom})"
    )


def _status_line(settings: Settings, state: BotState, equity: float, note: str) -> str:
    if state.position is None:
        position = "không có vị thế"
    else:
        position = (
            f"{state.position.side} {state.position.qty} "
            f"giá vào {state.position.entry_price:.4f} "
            f"cắt lỗ {state.position.stop_price:.4f}"
        )
    halt = "đang dừng mở lệnh mới" if state.halted else "đang chạy"
    day_pnl = equity - state.day_start_equity
    return (
        f"{note} | {settings.symbol} | vốn {equity:.4f} | ngày {state.day_key} "
        f"lãi/lỗ ngày {day_pnl:.4f} | {halt} | {position}"
    )


def lookback_label(settings: Settings) -> str:
    return f"{settings.lookback} nến {settings.interval}"


def _fmt_time(time_ms: int) -> str:
    return datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def status_report(settings: Settings, store: Store, now_ms: int) -> str:
    candle_count, funding_count = store.counts(settings.symbol, settings.interval)
    lines = [
        f"mode {settings.mode} | {settings.symbol} | db {settings.db_path}",
        f"nến {candle_count} | funding {funding_count}",
        f"ngưỡng vào {settings.entry_threshold * 100:.2f}% / thoát {settings.exit_threshold * 100:.2f}%",
        f"khối lượng {settings.position_pct * 100:.1f}% vốn | cắt lỗ {settings.stop_pct * 100:.1f}% | lỗ ngày {settings.daily_loss_pct * 100:.1f}%",
    ]
    if settings.mode == "paper" and candle_count:
        state = store.load_paper(settings.paper_cash, now_ms)
        closed = closed_candles(store.candles(settings.symbol, settings.interval), now_ms)
        price = closed[-1].close if closed else state.cash
        lines.append(_status_line(settings, state, equity_at(state, price) if closed else state.cash, "paper"))
    events = store.recent_events(5)
    if events:
        lines.append("sự kiện gần nhất:")
        for row in events:
            lines.append(f"- {row['mode']} {row['kind']} {row['reason']} @ {row['price']}")
    return "\n".join(lines)

