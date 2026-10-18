"""設定値の定義.

優先順位: 環境変数 > DB の settings テーブル > DEFAULT_SETTINGS

DB に保存された設定は Web 画面から編集できる。トークンなどの秘密情報は
環境変数で渡すこともできる（環境変数が指定されていればそちらを優先）。
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("STOCKALERT_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("STOCKALERT_DB", DATA_DIR / "stockalert.db"))

# 通知チャネルとその既定値
DEFAULT_SETTINGS: dict[str, str] = {
    # --- 監視動作 ---
    "poll_interval_minutes": "15",
    "default_thresholds": "15,20,25,30",
    "default_direction": "both",
    "respect_market_hours": "true",
    "renotify_cooldown_minutes": "60",
    "notify_on_reset": "true",
    "timezone": "Asia/Tokyo",
    # --- ntfy (推奨 / スマホにアプリを入れるだけ) ---
    "channel_ntfy": "true",
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
    "ntfy_token": "",
    # --- Telegram ---
    "channel_telegram": "false",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    # --- Discord ---
    "channel_discord": "false",
    "discord_webhook_url": "",
    # --- Slack ---
    "channel_slack": "false",
    "slack_webhook_url": "",
    # --- LINE Messaging API ---
    "channel_line": "false",
    "line_channel_access_token": "",
    "line_to": "",
    # --- 汎用 Webhook (Zapier / Make / IFTTT など) ---
    "channel_webhook": "false",
    "generic_webhook_url": "",
    # --- メール ---
    "channel_email": "false",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_to": "",
    "smtp_starttls": "true",
}

# 環境変数で上書きできるキー
ENV_MAP: dict[str, str] = {
    "poll_interval_minutes": "STOCKALERT_POLL_INTERVAL",
    "default_thresholds": "STOCKALERT_THRESHOLDS",
    "respect_market_hours": "STOCKALERT_RESPECT_MARKET_HOURS",
    "ntfy_server": "STOCKALERT_NTFY_SERVER",
    "ntfy_topic": "STOCKALERT_NTFY_TOPIC",
    "ntfy_token": "STOCKALERT_NTFY_TOKEN",
    "telegram_bot_token": "STOCKALERT_TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "STOCKALERT_TELEGRAM_CHAT_ID",
    "discord_webhook_url": "STOCKALERT_DISCORD_WEBHOOK_URL",
    "slack_webhook_url": "STOCKALERT_SLACK_WEBHOOK_URL",
    "line_channel_access_token": "STOCKALERT_LINE_TOKEN",
    "line_to": "STOCKALERT_LINE_TO",
    "generic_webhook_url": "STOCKALERT_GENERIC_WEBHOOK_URL",
    "smtp_host": "STOCKALERT_SMTP_HOST",
    "smtp_port": "STOCKALERT_SMTP_PORT",
    "smtp_user": "STOCKALERT_SMTP_USER",
    "smtp_password": "STOCKALERT_SMTP_PASSWORD",
    "smtp_from": "STOCKALERT_SMTP_FROM",
    "smtp_to": "STOCKALERT_SMTP_TO",
}


def env_overrides() -> dict[str, str]:
    """環境変数に設定されている値だけを dict で返す。"""
    out: dict[str, str] = {}
    for key, env_name in ENV_MAP.items():
        val = os.environ.get(env_name)
        if val is not None and val != "":
            out[key] = val
    # 環境変数が入っていれば該当チャネルを有効化する
    if out.get("ntfy_topic"):
        out["channel_ntfy"] = "true"
    if out.get("telegram_bot_token") and out.get("telegram_chat_id"):
        out["channel_telegram"] = "true"
    if out.get("discord_webhook_url"):
        out["channel_discord"] = "true"
    if out.get("slack_webhook_url"):
        out["channel_slack"] = "true"
    if out.get("line_channel_access_token") and out.get("line_to"):
        out["channel_line"] = "true"
    if out.get("generic_webhook_url"):
        out["channel_webhook"] = "true"
    if out.get("smtp_host") and out.get("smtp_to"):
        out["channel_email"] = "true"
    return out


def as_bool(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def as_int(value: str | int | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default
