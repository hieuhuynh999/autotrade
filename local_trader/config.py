import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    mode: str
    symbol: str
    interval: str
    lookback: int
    entry_threshold: float
    exit_threshold: float
    position_pct: float
    stop_pct: float
    daily_loss_pct: float
    max_abs_funding: float
    fee_rate: float
    paper_cash: float
    leverage: int
    loop_seconds: int
    db_path: Path
    api_key: str
    api_secret: str


def _required_float(name: str, default: str) -> float:
    raw = os.environ.get(name, default)
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} phải là số, nhận được {raw!r}") from exc


def _required_int(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} phải là số nguyên, nhận được {raw!r}") from exc


def load_settings(env_file: Path | None = None) -> Settings:
    load_env_file(env_file or Path.cwd() / ".env")
    mode = os.environ.get("MODE", "paper").strip().lower()
    if mode not in {"paper", "demo"}:
        raise ValueError("MODE chỉ nhận paper hoặc demo")
    symbol = os.environ.get("SYMBOL", "BTCUSDT").strip().upper()
    if not symbol.isalnum() or not (5 <= len(symbol) <= 20):
        raise ValueError("SYMBOL không hợp lệ")
    interval = os.environ.get("INTERVAL", "15m").strip()
    if interval not in {"15m", "1h"}:
        raise ValueError("INTERVAL chỉ nhận 15m hoặc 1h")
    lookback = _required_int("LOOKBACK", "24")
    entry_threshold = _required_float("ENTRY_THRESHOLD", "0.015")
    exit_threshold = _required_float("EXIT_THRESHOLD", "0")
    position_pct = _required_float("POSITION_PCT", "0.10")
    stop_pct = _required_float("STOP_PCT", "0.02")
    daily_loss_pct = _required_float("DAILY_LOSS_PCT", "0.03")
    max_abs_funding = _required_float("MAX_ABS_FUNDING", "0.0003")
    fee_rate = _required_float("FEE_RATE", "0.0005")
    paper_cash = _required_float("PAPER_CASH", "10000")
    leverage = _required_int("LEVERAGE", "1")
    loop_seconds = _required_int("LOOP_SECONDS", "60")
    db_path = Path(os.environ.get("DB_PATH", "data/trader.db"))
    if lookback < 2:
        raise ValueError("LOOKBACK phải từ 2 trở lên")
    if not 0 < entry_threshold < 1:
        raise ValueError("ENTRY_THRESHOLD phải nằm trong (0, 1)")
    if not -0.5 <= exit_threshold < entry_threshold:
        raise ValueError("EXIT_THRESHOLD phải nhỏ hơn ENTRY_THRESHOLD")
    if not 0 < position_pct <= 1:
        raise ValueError("POSITION_PCT phải nằm trong (0, 1]")
    if not 0 < stop_pct < 1:
        raise ValueError("STOP_PCT phải nằm trong (0, 1)")
    if not 0 < daily_loss_pct < 1:
        raise ValueError("DAILY_LOSS_PCT phải nằm trong (0, 1)")
    if max_abs_funding < 0:
        raise ValueError("MAX_ABS_FUNDING không được âm")
    if fee_rate < 0:
        raise ValueError("FEE_RATE không được âm")
    if paper_cash <= 0:
        raise ValueError("PAPER_CASH phải lớn hơn 0")
    if leverage != 1:
        raise ValueError("LEVERAGE chỉ được bằng 1. Khối lượng đã là % vốn, không cộng thêm đòn bẩy")
    if loop_seconds < 5:
        raise ValueError("LOOP_SECONDS phải từ 5 trở lên")
    return Settings(
        mode=mode,
        symbol=symbol,
        interval=interval,
        lookback=lookback,
        entry_threshold=entry_threshold,
        exit_threshold=exit_threshold,
        position_pct=position_pct,
        stop_pct=stop_pct,
        daily_loss_pct=daily_loss_pct,
        max_abs_funding=max_abs_funding,
        fee_rate=fee_rate,
        paper_cash=paper_cash,
        leverage=leverage,
        loop_seconds=loop_seconds,
        db_path=db_path,
        api_key=os.environ.get("BINANCE_API_KEY", "").strip(),
        api_secret=os.environ.get("BINANCE_API_SECRET", "").strip(),
    )
