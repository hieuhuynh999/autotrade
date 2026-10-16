from local_trader.models import Candle


def closed_candles(candles: list[Candle], now_ms: int) -> list[Candle]:
    """Chỉ giữ nến đã đóng. Nến đang chạy có close_time nằm phía trước now."""
    closed = [candle for candle in candles if candle.close_time < now_ms]
    closed.sort(key=lambda candle: candle.open_time)
    return closed


def momentum(closes: list[float], lookback: int) -> float | None:
    if lookback < 1 or len(closes) < lookback + 1:
        return None
    past = closes[-1 - lookback]
    last = closes[-1]
    if past <= 0:
        return None
    return last / past - 1.0


def entry_side(
    mom: float | None,
    funding: float | None,
    entry_threshold: float,
    max_abs_funding: float,
) -> str | None:
    if mom is None or funding is None:
        return None
    if mom >= entry_threshold and funding <= max_abs_funding:
        return "long"
    if mom <= -entry_threshold and funding >= -max_abs_funding:
        return "short"
    return None


def entry_block_reason(
    mom: float | None,
    funding: float | None,
    entry_threshold: float,
    max_abs_funding: float,
) -> str | None:
    if mom is None:
        return "chưa đủ nến đóng để tính momentum"
    if funding is None:
        return "chưa có funding, bỏ qua vào lệnh"
    side = entry_side(mom, funding, entry_threshold, max_abs_funding)
    if side is not None:
        return None
    if abs(mom) < entry_threshold:
        return "momentum chưa chạm ngưỡng vào lệnh"
    if mom >= entry_threshold and funding > max_abs_funding:
        return "bỏ qua long: funding quá cao, phe long đang trả phí"
    if mom <= -entry_threshold and funding < -max_abs_funding:
        return "bỏ qua short: funding quá âm, phe short đang trả phí"
    return "không đủ điều kiện vào lệnh"


def channel_exit(candles: list[Candle], side: str, lookback: int) -> bool:
    """Long thoát khi đóng cửa thủng đáy 24 nến. Short thoát khi đóng cửa vượt đỉnh 24 nến."""
    if len(candles) < lookback + 1:
        return False
    prior = candles[-1 - lookback : -1]
    close = candles[-1].close
    if side == "long":
        return close < min(candle.low for candle in prior)
    if side == "short":
        return close > max(candle.high for candle in prior)
    return False


def evaluate_entry(
    candles: list[Candle],
    funding: float | None,
    *,
    lookback: int,
    entry_threshold: float,
    max_abs_funding: float,
) -> tuple[str | None, str, list[str]]:
    """Vào lệnh khi đóng cửa phá đỉnh hoặc phá đáy lookback nến, và funding không ngược chiều."""
    _ = entry_threshold
    breakout_long, breakout_short, breakout_text = _breakout(candles, lookback)
    funding_long = funding is not None and funding <= max_abs_funding
    funding_short = funding is not None and funding >= -max_abs_funding
    lines = [breakout_text, _funding_line(funding, max_abs_funding)]
    if breakout_long and funding_long:
        lines.append("tín hiệu vào lệnh: long")
        return "long", "", lines
    if breakout_short and funding_short:
        lines.append("tín hiệu vào lệnh: short")
        return "short", "", lines
    missing: list[str] = []
    if breakout_long or breakout_short:
        missing.append("funding")
    else:
        missing.append("phá đỉnh/đáy")
        if funding is None:
            missing.append("funding")
    reason = "thiếu điều kiện: " + ", ".join(missing)
    lines.append(f"tín hiệu vào lệnh: không — {reason}")
    return None, reason, lines


def _funding_line(funding: float | None, max_abs_funding: float) -> str:
    if funding is None:
        return "funding: chưa có"
    return (
        f"funding: {funding * 100:.4f}% | long khi <= {max_abs_funding * 100:.4f}%"
        f", short khi >= -{max_abs_funding * 100:.4f}%"
    )


def _breakout(candles: list[Candle], lookback: int) -> tuple[bool, bool, str]:
    if len(candles) < lookback + 1:
        return False, False, "phá đỉnh/đáy: chưa đủ nến"
    prior = candles[-1 - lookback : -1]
    ceiling = max(candle.high for candle in prior)
    floor = min(candle.low for candle in prior)
    close = candles[-1].close
    return (
        close > ceiling,
        close < floor,
        f"phá đỉnh/đáy: đóng {close:.4f} | đỉnh {lookback} nến {ceiling:.4f} | đáy {floor:.4f}",
    )


def exit_needed(side: str, mom: float | None, exit_threshold: float) -> bool:
    if mom is None:
        return False
    if side == "long":
        return mom <= exit_threshold
    if side == "short":
        return mom >= -exit_threshold
    return False
