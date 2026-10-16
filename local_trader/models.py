from dataclasses import dataclass, field


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int


@dataclass(frozen=True)
class SymbolFilters:
    step_size: float
    min_qty: float
    tick_size: float
    min_notional: float


@dataclass
class Position:
    side: str
    qty: float
    entry_price: float
    stop_price: float
    entry_time: int


@dataclass
class BotState:
    cash: float
    position: Position | None
    day_key: str
    day_start_equity: float
    halted: bool
    halt_reason: str | None
    last_bar_open_time: int | None
    profit_day_done: bool = False
    entries_blocked_until: int | None = None
    loss_pause_latched: bool = False
    logs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Fill:
    kind: str
    side: str
    price: float
    qty: float
    time: int
    fee: float
    pnl: float
    reason: str
    momentum: float | None


@dataclass(frozen=True)
class Trade:
    side: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    reason: str


@dataclass
class BacktestResult:
    start_equity: float
    ending_equity: float
    fees: float
    max_drawdown: float
    trades: list[Trade]
    halted: bool
    halt_reason: str | None
