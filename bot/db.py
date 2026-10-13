import json
import time
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id          INTEGER PRIMARY KEY,
    username         TEXT,
    full_name        TEXT,
    source           TEXT,
    subscribed       INTEGER NOT NULL DEFAULT 0,
    paid             INTEGER NOT NULL DEFAULT 0,
    paid_at          INTEGER,
    blocked          INTEGER NOT NULL DEFAULT 0,
    -- дожимающие сообщения: какие уже отправлены (JSON-список ключей) и когда следующее
    followups_sent   TEXT    NOT NULL DEFAULT '[]',
    next_followup_at INTEGER,
    -- текущий экзамен
    exam_ids         TEXT,
    exam_mode        TEXT,              -- 'exam' или 'ticket:N'
    exam_index       INTEGER NOT NULL DEFAULT 0,
    exam_correct     INTEGER NOT NULL DEFAULT 0,
    exam_errors      INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL,
    last_seen        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_followup ON users(next_followup_at);

-- Первое наступление каждого события воронки для пользователя
CREATE TABLE IF NOT EXISTS events (
    user_id INTEGER NOT NULL,
    event   TEXT    NOT NULL,
    ts      INTEGER NOT NULL,
    PRIMARY KEY (user_id, event)
);
CREATE INDEX IF NOT EXISTS idx_events ON events(event, ts);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Отзывы, которые админ добавляет через админку
CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    author     TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    created_at INTEGER NOT NULL
);

-- Заявки на ручное подтверждение оплаты (временная оплата по СБП)
CREATE TABLE IF NOT EXISTS payment_requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    file_id    TEXT,
    status     TEXT    NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self._migrate()
        await self.conn.commit()

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA table_info(users)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        if "followups_sent" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN followups_sent TEXT NOT NULL DEFAULT '[]'")
        if "exam_mode" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN exam_mode TEXT")

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------ users

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    async def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM users WHERE lower(username) = lower(?)", (username.lstrip("@"),)
        )

    async def touch_user(
        self, user_id: int, username: str | None, full_name: str, first_followup_at: int
    ) -> tuple[dict[str, Any], bool]:
        """Создаёт пользователя или обновляет его данные. Возвращает (user, is_new)."""
        now = int(time.time())
        cur = await self.conn.execute(
            """INSERT OR IGNORE INTO users
               (user_id, username, full_name, next_followup_at, created_at, last_seen)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, username, full_name, first_followup_at, now, now),
        )
        is_new = cur.rowcount > 0
        if not is_new:
            await self.conn.execute(
                "UPDATE users SET username = ?, full_name = ?, blocked = 0, last_seen = ? WHERE user_id = ?",
                (username, full_name, now, user_id),
            )
        await self.conn.commit()
        if is_new:
            await self.add_event(user_id, "start")
        return await self.get_user(user_id), is_new

    async def update_user(self, user_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(f"UPDATE users SET {cols} WHERE user_id = ?", (*fields.values(), user_id))
        await self.conn.commit()

    async def user_ids(self, audience: str) -> list[int]:
        where = {"all": "", "unpaid": "AND paid = 0", "paid": "AND paid = 1"}[audience]
        rows = await self._all(f"SELECT user_id FROM users WHERE blocked = 0 {where}")
        return [r["user_id"] for r in rows]

    async def list_users(self, kind: str, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        if kind == "checkout":
            base = ("FROM users u JOIN events e ON e.user_id = u.user_id AND e.event = 'checkout' "
                    "WHERE u.paid = 0", "e.ts")
        elif kind == "paid":
            base = ("FROM users u WHERE u.paid = 1", "u.paid_at")
        else:
            base = ("FROM users u", "u.created_at")
        rows = await self._all(
            f"SELECT u.*, {base[1]} AS ts {base[0]} ORDER BY {base[1]} DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        total = (await self._one(f"SELECT COUNT(*) AS n {base[0]}"))["n"]
        return rows, total

    async def delete_user(self, user_id: int) -> None:
        """Удаляет пользователя вместе с его событиями и заявками на оплату."""
        for table in ("events", "payment_requests", "users"):
            await self.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def wipe_users_and_stats(self) -> int:
        """Удаляет всех пользователей, статистику и заявки. Отзывы, видео и настройки остаются."""
        n = (await self._one("SELECT COUNT(*) AS n FROM users"))["n"]
        for table in ("events", "payment_requests", "users"):
            await self.conn.execute(f"DELETE FROM {table}")
        await self.conn.commit()
        return n

    async def find_user(self, value: str) -> dict[str, Any] | None:
        """Поиск по ID или @username."""
        value = value.strip()
        if value.lstrip("-").isdigit():
            return await self.get_user(int(value))
        return await self.find_user_by_username(value)

    # ----------------------------------------------------------- events

    async def add_event(self, user_id: int, event: str) -> bool:
        """Записывает событие, если его ещё не было. True — событие новое."""
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO events (user_id, event, ts) VALUES (?, ?, ?)",
            (user_id, event, int(time.time())),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def has_event(self, user_id: int, event: str) -> bool:
        return await self._one("SELECT 1 FROM events WHERE user_id = ? AND event = ?", (user_id, event)) is not None

    async def event_counts(self, since: int) -> dict[str, int]:
        rows = await self._all("SELECT event, COUNT(*) AS n FROM events WHERE ts >= ? GROUP BY event", (since,))
        return {r["event"]: r["n"] for r in rows}

    async def checkout_not_paid_count(self) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS n FROM users u JOIN events e ON e.user_id = u.user_id "
            "AND e.event = 'checkout' WHERE u.paid = 0"
        )
        return row["n"]

    async def source_counts(self) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT COALESCE(source, '—') AS src, COUNT(*) AS total, SUM(paid) AS paid "
            "FROM users GROUP BY src ORDER BY total DESC LIMIT 15"
        )

    # -------------------------------------------------------- followups

    async def due_followups(self, now: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM users WHERE next_followup_at IS NOT NULL AND next_followup_at <= ? "
            "AND blocked = 0 AND paid = 0",
            (now,),
        )

    # --------------------------------------------------------- settings

    async def get_setting(self, key: str, default: Any = None) -> Any:
        row = await self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    async def get_settings(self, keys: tuple[str, ...]) -> dict[str, Any]:
        marks = ", ".join("?" * len(keys))
        rows = await self._all(f"SELECT key, value FROM settings WHERE key IN ({marks})", keys)
        return {r["key"]: json.loads(r["value"]) for r in rows}

    async def delete_setting(self, key: str) -> None:
        await self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        await self.conn.commit()

    async def set_setting(self, key: str, value: Any) -> None:
        await self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        await self.conn.commit()

    # ------------------------------------------------------------ reviews

    async def add_review(self, author: str, text: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO reviews (author, text, created_at) VALUES (?, ?, ?)", (author, text, int(time.time()))
        )
        await self.conn.commit()
        return cur.lastrowid

    async def delete_review(self, review_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def reviews(self, limit: int = 1000, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        rows = await self._all("SELECT * FROM reviews ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        total = (await self._one("SELECT COUNT(*) AS n FROM reviews"))["n"]
        return rows, total

    # ------------------------------------------------- payment requests

    async def create_payment_request(self, user_id: int, file_id: str | None) -> int:
        cur = await self.conn.execute(
            "INSERT INTO payment_requests (user_id, file_id, created_at) VALUES (?, ?, ?)",
            (user_id, file_id, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_payment_request(self, request_id: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM payment_requests WHERE id = ?", (request_id,))

    async def set_payment_request_status(self, request_id: int, status: str) -> bool:
        """Меняет статус только у ожидающей заявки (защита от двойного нажатия)."""
        cur = await self.conn.execute(
            "UPDATE payment_requests SET status = ? WHERE id = ? AND status = 'pending'", (status, request_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def pending_request_for(self, user_id: int) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM payment_requests WHERE user_id = ? AND status = 'pending'", (user_id,)
        )

    async def pending_payment_requests(self) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT p.*, u.full_name, u.username FROM payment_requests p "
            "JOIN users u ON u.user_id = p.user_id WHERE p.status = 'pending' ORDER BY p.id"
        )
