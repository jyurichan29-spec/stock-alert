"""SQLite による永続化レイヤ."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Iterable

from .config import DB_PATH, DEFAULT_SETTINGS, env_overrides

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS watchlist (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT    NOT NULL UNIQUE,
    name              TEXT    NOT NULL DEFAULT '',
    base_price        REAL    NOT NULL,
    base_date         TEXT    NOT NULL DEFAULT '',
    thresholds        TEXT    NOT NULL DEFAULT '15,20,25,30',
    direction         TEXT    NOT NULL DEFAULT 'both',   -- both | up | down
    enabled           INTEGER NOT NULL DEFAULT 1,
    note              TEXT    NOT NULL DEFAULT '',
    up_level          INTEGER NOT NULL DEFAULT 0,        -- 上昇方向で通知済みの最大閾値
    down_level        INTEGER NOT NULL DEFAULT 0,        -- 下降方向で通知済みの最大閾値
    up_notified_at    REAL    NOT NULL DEFAULT 0,
    down_notified_at  REAL    NOT NULL DEFAULT 0,
    last_price        REAL,
    last_change_pct   REAL,
    last_checked_at   REAL    NOT NULL DEFAULT 0,
    last_error        TEXT    NOT NULL DEFAULT '',
    created_at        REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    name        TEXT    NOT NULL DEFAULT '',
    base_price  REAL    NOT NULL,
    price       REAL    NOT NULL,
    change_pct  REAL    NOT NULL,
    level       INTEGER NOT NULL,
    direction   TEXT    NOT NULL,          -- up | down
    event       TEXT    NOT NULL,          -- escalate | repeat | reset | manual
    title       TEXT    NOT NULL DEFAULT '',
    message     TEXT    NOT NULL DEFAULT '',
    channels    TEXT    NOT NULL DEFAULT '',  -- 送信結果の要約
    created_at  REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS price_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    price       REAL    NOT NULL,
    change_pct  REAL    NOT NULL,
    fetched_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pricelog_symbol ON price_log(symbol, fetched_at DESC);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
def get_settings(db_path: str | None = None) -> dict[str, str]:
    """既定値 → DB → 環境変数 の順にマージした設定を返す。"""
    merged = dict(DEFAULT_SETTINGS)
    conn = connect(db_path)
    try:
        for row in conn.execute("SELECT key, value FROM settings"):
            merged[row["key"]] = row["value"]
    finally:
        conn.close()
    merged.update(env_overrides())
    return merged


def save_settings(values: dict[str, Any], db_path: str | None = None) -> None:
    conn = connect(db_path)
    try:
        for key, value in values.items():
            if key not in DEFAULT_SETTINGS:
                continue
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
        conn.commit()
    finally:
        conn.close()


def set_setting(key: str, value: Any, db_path: str | None = None) -> None:
    save_settings({key: value}, db_path)


# --------------------------------------------------------------------------
# watchlist
# --------------------------------------------------------------------------
def list_watchlist(db_path: str | None = None, only_enabled: bool = False) -> list[dict]:
    conn = connect(db_path)
    try:
        sql = "SELECT * FROM watchlist"
        if only_enabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY symbol"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def get_watch(symbol: str, db_path: str | None = None) -> dict | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE symbol=?", (symbol.upper(),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_watch(
    symbol: str,
    base_price: float,
    name: str = "",
    thresholds: str = "15,20,25,30",
    direction: str = "both",
    note: str = "",
    base_date: str = "",
    db_path: str | None = None,
) -> int:
    conn = connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO watchlist(symbol, name, base_price, base_date, thresholds,
                                  direction, note, created_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name,
                base_price=excluded.base_price,
                base_date=excluded.base_date,
                thresholds=excluded.thresholds,
                direction=excluded.direction,
                note=excluded.note
            """,
            (
                symbol.upper(),
                name,
                float(base_price),
                base_date,
                thresholds,
                direction,
                note,
                time.time(),
            ),
        )
        conn.commit()
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = conn.execute(
            "SELECT id FROM watchlist WHERE symbol=?", (symbol.upper(),)
        ).fetchone()
        return int(row["id"])
    finally:
        conn.close()


def delete_watch(symbol: str, db_path: str | None = None) -> None:
    conn = connect(db_path)
    try:
        conn.execute("DELETE FROM watchlist WHERE symbol=?", (symbol.upper(),))
        conn.commit()
    finally:
        conn.close()


def update_watch(symbol: str, db_path: str | None = None, **fields: Any) -> None:
    allowed = {
        "name", "base_price", "base_date", "thresholds", "direction",
        "enabled", "note", "up_level", "down_level", "up_notified_at",
        "down_notified_at", "last_price", "last_change_pct",
        "last_checked_at", "last_error",
    }
    keys = [k for k in fields if k in allowed]
    if not keys:
        return
    conn = connect(db_path)
    try:
        sets = ", ".join(f"{k}=?" for k in keys)
        conn.execute(
            f"UPDATE watchlist SET {sets} WHERE symbol=?",
            [fields[k] for k in keys] + [symbol.upper()],
        )
        conn.commit()
    finally:
        conn.close()


def reset_levels(symbol: str, db_path: str | None = None) -> None:
    """基準株価を入れ直したときに通知済みレベルをクリアする。"""
    update_watch(symbol, db_path=db_path, up_level=0, down_level=0,
                 up_notified_at=0, down_notified_at=0)


# --------------------------------------------------------------------------
# alerts / price_log
# --------------------------------------------------------------------------
def log_alert(
    symbol: str,
    base_price: float,
    price: float,
    change_pct: float,
    level: int,
    direction: str,
    event: str,
    title: str = "",
    message: str = "",
    channels: str = "",
    name: str = "",
    db_path: str | None = None,
) -> int:
    conn = connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO alerts(symbol, name, base_price, price, change_pct, level,
                               direction, event, title, message, channels, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (symbol.upper(), name, float(base_price), float(price), float(change_pct),
             int(level), direction, event, title, message, channels, time.time()),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def list_alerts(limit: int = 100, db_path: str | None = None) -> list[dict]:
    conn = connect(db_path)
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        ]
    finally:
        conn.close()


def log_price(symbol: str, price: float, change_pct: float,
              db_path: str | None = None) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO price_log(symbol, price, change_pct, fetched_at) VALUES(?,?,?,?)",
            (symbol.upper(), float(price), float(change_pct), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def price_history(symbol: str, limit: int = 200,
                  db_path: str | None = None) -> list[dict]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT price, change_pct, fetched_at FROM price_log "
            "WHERE symbol=? ORDER BY fetched_at DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def prune_price_log(keep_per_symbol: int = 500, db_path: str | None = None) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            """
            DELETE FROM price_log WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY symbol ORDER BY fetched_at DESC
                    ) AS rn FROM price_log
                ) WHERE rn <= ?
            )
            """,
            (keep_per_symbol,),
        )
        conn.commit()
    finally:
        conn.close()


def export_state(db_path: str | None = None) -> dict:
    """GitHub Actions などサーバーレス実行向けに状態を JSON 化する。"""
    conn = connect(db_path)
    try:
        return {
            "watchlist": [dict(r) for r in conn.execute("SELECT * FROM watchlist")],
            "alerts": [dict(r) for r in conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 500")],
        }
    finally:
        conn.close()


def import_state(state: dict, db_path: str | None = None) -> None:
    for item in state.get("watchlist", []):
        add_watch(
            symbol=item["symbol"],
            base_price=item["base_price"],
            name=item.get("name", ""),
            thresholds=item.get("thresholds", "15,20,25,30"),
            direction=item.get("direction", "both"),
            note=item.get("note", ""),
            base_date=item.get("base_date", ""),
            db_path=db_path,
        )
        update_watch(
            item["symbol"], db_path=db_path,
            enabled=item.get("enabled", 1),
            up_level=item.get("up_level", 0),
            down_level=item.get("down_level", 0),
            up_notified_at=item.get("up_notified_at", 0),
            down_notified_at=item.get("down_notified_at", 0),
            last_price=item.get("last_price"),
            last_change_pct=item.get("last_change_pct"),
            last_checked_at=item.get("last_checked_at", 0),
        )


def dump_json(value: Iterable) -> str:
    return json.dumps(list(value), ensure_ascii=False)
