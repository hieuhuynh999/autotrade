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


def exit_needed(side: str, mom: float | None, exit_threshold: float) -> bool:
    if mom is None:
        return False
    if side == "long":
        return mom <= exit_threshold
    if side == "short":
        return mom >= -exit_threshold
    return False
