import sqlite3
from pathlib import Path

from local_trader.engine import new_state, utc_day
from local_trader.models import BotState, Candle, Fill, Position, SymbolFilters


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        self._conn.close()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                close_time INTEGER NOT NULL,
                PRIMARY KEY (symbol, interval, open_time)
            );
            CREATE TABLE IF NOT EXISTS funding (
                symbol TEXT NOT NULL,
                funding_time INTEGER NOT NULL,
                funding_rate REAL NOT NULL,
                PRIMARY KEY (symbol, funding_time)
            );
            CREATE TABLE IF NOT EXISTS symbol_filters (
                symbol TEXT PRIMARY KEY,
                step_size REAL NOT NULL,
                min_qty REAL NOT NULL,
                tick_size REAL NOT NULL,
                min_notional REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                mode TEXT NOT NULL,
                kind TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT,
                qty REAL,
                price REAL,
                reason TEXT NOT NULL,
                momentum REAL,
                equity REAL,
                pnl REAL
            );
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL NOT NULL,
                side TEXT,
                qty REAL,
                entry_price REAL,
                stop_price REAL,
                entry_time INTEGER,
                day_key TEXT NOT NULL,
                day_start_equity REAL NOT NULL,
                halted INTEGER NOT NULL,
                halt_reason TEXT,
                last_bar_open_time INTEGER,
                profit_day_done INTEGER NOT NULL DEFAULT 0,
                entries_blocked_until INTEGER,
                loss_pause_latched INTEGER NOT NULL DEFAULT 0,
                session_started_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS demo_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                day_key TEXT,
                day_start_equity REAL,
                halted INTEGER NOT NULL,
                halt_reason TEXT,
                last_bar_open_time INTEGER,
                stop_price REAL,
                entry_time INTEGER,
                leverage_ready INTEGER NOT NULL,
                profit_day_done INTEGER NOT NULL DEFAULT 0,
                entries_blocked_until INTEGER,
                loss_pause_latched INTEGER NOT NULL DEFAULT 0,
                session_started_ms INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._ensure_guard_columns("paper_account")
        self._ensure_guard_columns("demo_state")
        self._ensure_column("events", "pnl", "REAL")
        self._conn.commit()

    def _ensure_guard_columns(self, table: str) -> None:
        existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            return
        columns = {
            "profit_day_done": "INTEGER NOT NULL DEFAULT 0",
            "entries_blocked_until": "INTEGER",
            "loss_pause_latched": "INTEGER NOT NULL DEFAULT 0",
            "session_started_ms": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, decl in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _ensure_column(self, table: str, name: str, decl: str) -> None:
        existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if existing and name not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def upsert_candles(self, symbol: str, interval: str, candles: list[Candle]) -> int:
        self._conn.executemany(
            """
            INSERT INTO candles (
                symbol, interval, open_time, open, high, low, close, volume, close_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                close_time = excluded.close_time
            """,
            [
                (
                    symbol,
                    interval,
                    candle.open_time,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.close_time,
                )
                for candle in candles
            ],
        )
        self._conn.commit()
        return len(candles)

    def upsert_funding(self, symbol: str, points: list[tuple[int, float]]) -> int:
        self._conn.executemany(
            """
            INSERT INTO funding (symbol, funding_time, funding_rate)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol, funding_time) DO UPDATE SET
                funding_rate = excluded.funding_rate
            """,
            [(symbol, funding_time, rate) for funding_time, rate in points],
        )
        self._conn.commit()
        return len(points)

    def save_filters(self, symbol: str, filters: SymbolFilters) -> None:
        self._conn.execute(
            """
            INSERT INTO symbol_filters (symbol, step_size, min_qty, tick_size, min_notional)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                step_size = excluded.step_size,
                min_qty = excluded.min_qty,
                tick_size = excluded.tick_size,
                min_notional = excluded.min_notional
            """,
            (symbol, filters.step_size, filters.min_qty, filters.tick_size, filters.min_notional),
        )
        self._conn.commit()

    def filters(self, symbol: str) -> SymbolFilters | None:
        row = self._conn.execute(
            "SELECT * FROM symbol_filters WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return SymbolFilters(row["step_size"], row["min_qty"], row["tick_size"], row["min_notional"])

    def candles(self, symbol: str, interval: str) -> list[Candle]:
        rows = self._conn.execute(
            """
            SELECT open_time, open, high, low, close, volume, close_time
            FROM candles
            WHERE symbol = ? AND interval = ?
            ORDER BY open_time
            """,
            (symbol, interval),
        ).fetchall()
        return [
            Candle(
                row["open_time"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
                row["close_time"],
            )
            for row in rows
        ]

    def funding(self, symbol: str) -> list[tuple[int, float]]:
        rows = self._conn.execute(
            """
            SELECT funding_time, funding_rate
            FROM funding
            WHERE symbol = ?
            ORDER BY funding_time
            """,
            (symbol,),
        ).fetchall()
        return [(row["funding_time"], row["funding_rate"]) for row in rows]

    def counts(self, symbol: str, interval: str) -> tuple[int, int]:
        candles = self._conn.execute(
            "SELECT COUNT(*) AS n FROM candles WHERE symbol = ? AND interval = ?",
            (symbol, interval),
        ).fetchone()["n"]
        funding = self._conn.execute(
            "SELECT COUNT(*) AS n FROM funding WHERE symbol = ?",
            (symbol,),
        ).fetchone()["n"]
        return int(candles), int(funding)

    def load_paper(self, cash: float, now_ms: int) -> BotState:
        row = self._conn.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
        if row is None:
            state = new_state(cash, now_ms)
            self.save_paper(state)
            return state
        position = None
        if row["side"]:
            position = Position(
                row["side"],
                row["qty"],
                row["entry_price"],
                row["stop_price"],
                row["entry_time"],
            )
        return BotState(
            cash=row["cash"],
            position=position,
            day_key=row["day_key"],
            day_start_equity=row["day_start_equity"],
            halted=bool(row["halted"]),
            halt_reason=row["halt_reason"],
            last_bar_open_time=row["last_bar_open_time"],
            profit_day_done=bool(row["profit_day_done"]),
            entries_blocked_until=row["entries_blocked_until"],
            loss_pause_latched=bool(row["loss_pause_latched"]),
            session_started_ms=int(row["session_started_ms"] or 0),
        )

    def save_paper(self, state: BotState) -> None:
        position = state.position
        self._conn.execute(
            """
            INSERT INTO paper_account (
                id, cash, side, qty, entry_price, stop_price, entry_time,
                day_key, day_start_equity, halted, halt_reason, last_bar_open_time,
                profit_day_done, entries_blocked_until, loss_pause_latched, session_started_ms
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                cash = excluded.cash,
                side = excluded.side,
                qty = excluded.qty,
                entry_price = excluded.entry_price,
                stop_price = excluded.stop_price,
                entry_time = excluded.entry_time,
                day_key = excluded.day_key,
                day_start_equity = excluded.day_start_equity,
                halted = excluded.halted,
                halt_reason = excluded.halt_reason,
                last_bar_open_time = excluded.last_bar_open_time,
                profit_day_done = excluded.profit_day_done,
                entries_blocked_until = excluded.entries_blocked_until,
                loss_pause_latched = excluded.loss_pause_latched,
                session_started_ms = excluded.session_started_ms
            """,
            (
                state.cash,
                position.side if position else None,
                position.qty if position else None,
                position.entry_price if position else None,
                position.stop_price if position else None,
                position.entry_time if position else None,
                state.day_key,
                state.day_start_equity,
                int(state.halted),
                state.halt_reason,
                state.last_bar_open_time,
                int(state.profit_day_done),
                state.entries_blocked_until,
                int(state.loss_pause_latched),
                state.session_started_ms,
            ),
        )
        self._conn.commit()

    def reset_paper(self, cash: float, now_ms: int) -> BotState:
        self._conn.execute("DELETE FROM paper_account")
        self._conn.execute("DELETE FROM events WHERE mode = 'paper'")
        self._conn.commit()
        return self.load_paper(cash, now_ms)

    def load_demo_state(self, now_ms: int, equity: float) -> BotState:
        row = self._conn.execute("SELECT * FROM demo_state WHERE id = 1").fetchone()
        if row is None:
            state = new_state(equity, now_ms)
            self.save_demo_state(state, leverage_ready=False)
            return state
        return BotState(
            cash=equity,
            position=None,
            day_key=row["day_key"] or utc_day(now_ms),
            day_start_equity=row["day_start_equity"] if row["day_start_equity"] is not None else equity,
            halted=bool(row["halted"]),
            halt_reason=row["halt_reason"],
            last_bar_open_time=row["last_bar_open_time"],
            profit_day_done=bool(row["profit_day_done"]),
            entries_blocked_until=row["entries_blocked_until"],
            loss_pause_latched=bool(row["loss_pause_latched"]),
            session_started_ms=int(row["session_started_ms"] or 0),
        )

    def demo_extra(self) -> tuple[float | None, int | None, bool]:
        row = self._conn.execute("SELECT * FROM demo_state WHERE id = 1").fetchone()
        if row is None:
            return None, None, False
        return row["stop_price"], row["entry_time"], bool(row["leverage_ready"])

    def save_demo_state(
        self,
        state: BotState,
        *,
        leverage_ready: bool,
        stop_price: float | None = None,
        entry_time: int | None = None,
    ) -> None:
        position = state.position
        self._conn.execute(
            """
            INSERT INTO demo_state (
                id, day_key, day_start_equity, halted, halt_reason,
                last_bar_open_time, stop_price, entry_time, leverage_ready,
                profit_day_done, entries_blocked_until, loss_pause_latched, session_started_ms
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                day_key = excluded.day_key,
                day_start_equity = excluded.day_start_equity,
                halted = excluded.halted,
                halt_reason = excluded.halt_reason,
                last_bar_open_time = excluded.last_bar_open_time,
                stop_price = excluded.stop_price,
                entry_time = excluded.entry_time,
                leverage_ready = excluded.leverage_ready,
                profit_day_done = excluded.profit_day_done,
                entries_blocked_until = excluded.entries_blocked_until,
                loss_pause_latched = excluded.loss_pause_latched,
                session_started_ms = excluded.session_started_ms
            """,
            (
                state.day_key,
                state.day_start_equity,
                int(state.halted),
                state.halt_reason,
                state.last_bar_open_time,
                stop_price if stop_price is not None else (position.stop_price if position else None),
                entry_time if entry_time is not None else (position.entry_time if position else None),
                int(leverage_ready),
                int(state.profit_day_done),
                state.entries_blocked_until,
                int(state.loss_pause_latched),
                state.session_started_ms,
            ),
        )
        self._conn.commit()

    def add_event(
        self,
        *,
        ts: int,
        mode: str,
        kind: str,
        symbol: str,
        reason: str,
        side: str | None = None,
        qty: float | None = None,
        price: float | None = None,
        momentum: float | None = None,
        equity: float | None = None,
        pnl: float | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO events (ts, mode, kind, symbol, side, qty, price, reason, momentum, equity, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, mode, kind, symbol, side, qty, price, reason, momentum, equity, pnl),
        )
        self._conn.commit()

    def session_exit_counts(self, mode: str, symbol: str, since_ms: int) -> tuple[int, int]:
        rows = self._conn.execute(
            """
            SELECT pnl FROM events
            WHERE mode = ? AND symbol = ? AND kind = 'exit' AND ts >= ? AND pnl IS NOT NULL
            """,
            (mode, symbol, since_ms),
        ).fetchall()
        wins = sum(1 for row in rows if row["pnl"] > 0)
        losses = sum(1 for row in rows if row["pnl"] < 0)
        return wins, losses

    def recent_events(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
