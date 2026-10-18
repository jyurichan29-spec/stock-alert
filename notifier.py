"""プッシュ通知モジュール.

対応チャネル
------------
ntfy.sh        : スマホアプリを入れてトピックを購読するだけ。最も簡単（推奨）
Telegram       : Bot Token + chat_id
Discord        : Webhook URL
Slack          : Incoming Webhook URL
LINE           : Messaging API (Channel Access Token + userId)
汎用 Webhook   : Zapier / Make / IFTTT などの JSON POST
メール         : SMTP
"""

from __future__ import annotations

import json
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import requests

from .config import as_bool, as_int

TIMEOUT = 15


@dataclass
class SendResult:
    channel: str
    ok: bool
    detail: str = ""


def _priority(level: int) -> int:
    """閾値の大きさを ntfy の優先度（1〜5）に変換する。"""
    if level >= 30:
        return 5   # max / urgent : 強いバイブ + ポップオーバー
    if level >= 20:
        return 4   # high        : 長いバイブ + ポップオーバー
    return 3       # default     : 標準の音とバイブ


def send_ntfy(settings: dict, title: str, body: str, level: int) -> SendResult:
    """ntfy へ通知を送る。

    日本語タイトルを HTTP ヘッダで送ると ASCII 制約（RFC 2047 の折り返し）で
    失敗するため、ntfy の JSON パブリッシュ API を使う。
    参考: https://docs.ntfy.sh/publish/#publish-as-json
    """
    topic = (settings.get("ntfy_topic") or "").strip()
    if not topic:
        return SendResult("ntfy", False, "ntfy_topic が未設定です")
    server = (settings.get("ntfy_server") or "https://ntfy.sh").rstrip("/")

    headers = {"Content-Type": "application/json"}
    token = (settings.get("ntfy_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "topic": topic,
        "title": title,
        "message": body,
        "priority": _priority(level),
        # 方向（上昇/下降）はここでは受け取らないため、向きを断定しないタグにする
        "tags": ["rotating_light"] if level >= 25 else ["bell"],
    }
    try:
        resp = requests.post(server, json=payload, headers=headers, timeout=TIMEOUT)
        if resp.status_code >= 400:
            return SendResult("ntfy", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
        return SendResult("ntfy", True, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return SendResult("ntfy", False, str(exc))


def send_telegram(settings: dict, title: str, body: str, level: int) -> SendResult:
    token = (settings.get("telegram_bot_token") or "").strip()
    chat_id = (settings.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        return SendResult("telegram", False, "bot token / chat_id が未設定です")
    text = f"<b>{title}</b>\n{body}"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_notification": level < 20,
            },
            timeout=TIMEOUT,
        )
        data = resp.json() if resp.content else {}
        if not data.get("ok"):
            return SendResult("telegram", False, str(data.get("description") or resp.status_code))
        return SendResult("telegram", True, "sent")
    except Exception as exc:  # noqa: BLE001
        return SendResult("telegram", False, str(exc))


def send_discord(settings: dict, title: str, body: str, level: int) -> SendResult:
    url = (settings.get("discord_webhook_url") or "").strip()
    if not url:
        return SendResult("discord", False, "webhook URL が未設定です")
    color = 0xE5484D if level >= 25 else (0xF5A524 if level >= 20 else 0x4C8DFF)
    try:
        resp = requests.post(
            url,
            json={"embeds": [{"title": title, "description": body, "color": color}]},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            return SendResult("discord", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
        return SendResult("discord", True, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return SendResult("discord", False, str(exc))


def send_slack(settings: dict, title: str, body: str, level: int) -> SendResult:
    url = (settings.get("slack_webhook_url") or "").strip()
    if not url:
        return SendResult("slack", False, "webhook URL が未設定です")
    try:
        resp = requests.post(
            url, json={"text": f"*{title}*\n{body}"}, timeout=TIMEOUT
        )
        if resp.status_code >= 400:
            return SendResult("slack", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
        return SendResult("slack", True, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return SendResult("slack", False, str(exc))


def send_line(settings: dict, title: str, body: str, level: int) -> SendResult:
    token = (settings.get("line_channel_access_token") or "").strip()
    to = (settings.get("line_to") or "").strip()
    if not token or not to:
        return SendResult("line", False, "channel access token / 送信先ID が未設定です")
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"to": to, "messages": [{"type": "text", "text": f"{title}\n{body}"}]},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            return SendResult("line", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
        return SendResult("line", True, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return SendResult("line", False, str(exc))


def send_generic_webhook(settings: dict, title: str, body: str, level: int) -> SendResult:
    url = (settings.get("generic_webhook_url") or "").strip()
    if not url:
        return SendResult("webhook", False, "URL が未設定です")
    try:
        resp = requests.post(
            url,
            json={"title": title, "message": body, "level": level, "source": "stock-alert"},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            return SendResult("webhook", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
        return SendResult("webhook", True, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return SendResult("webhook", False, str(exc))


def send_email(settings: dict, title: str, body: str, level: int) -> SendResult:
    host = (settings.get("smtp_host") or "").strip()
    to_addr = (settings.get("smtp_to") or "").strip()
    from_addr = (settings.get("smtp_from") or settings.get("smtp_user") or "").strip()
    if not host or not to_addr or not from_addr:
        return SendResult("email", False, "SMTP 設定が不足しています")
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)
    port = as_int(settings.get("smtp_port"), 587)
    try:
        if as_bool(settings.get("smtp_starttls"), True):
            with smtplib.SMTP(host, port, timeout=TIMEOUT) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                user = (settings.get("smtp_user") or "").strip()
                if user:
                    smtp.login(user, settings.get("smtp_password") or "")
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT,
                                  context=ssl.create_default_context()) as smtp:
                user = (settings.get("smtp_user") or "").strip()
                if user:
                    smtp.login(user, settings.get("smtp_password") or "")
                smtp.send_message(msg)
        return SendResult("email", True, "sent")
    except Exception as exc:  # noqa: BLE001
        return SendResult("email", False, str(exc))


CHANNELS = {
    "ntfy": ("channel_ntfy", send_ntfy),
    "telegram": ("channel_telegram", send_telegram),
    "discord": ("channel_discord", send_discord),
    "slack": ("channel_slack", send_slack),
    "line": ("channel_line", send_line),
    "webhook": ("channel_webhook", send_generic_webhook),
    "email": ("channel_email", send_email),
}


def enabled_channels(settings: dict) -> list[str]:
    return [name for name, (flag, _) in CHANNELS.items() if as_bool(settings.get(flag))]


def dispatch(settings: dict, title: str, body: str, level: int = 20) -> list[SendResult]:
    """有効な全チャネルへ通知を送信する。"""
    results: list[SendResult] = []
    for name in enabled_channels(settings):
        _, func = CHANNELS[name]
        try:
            results.append(func(settings, title, body, level))
        except Exception as exc:  # noqa: BLE001
            results.append(SendResult(name, False, str(exc)))
    if not results:
        results.append(SendResult("none", False, "有効な通知チャネルがありません"))
    return results


def summarize(results: list[SendResult]) -> str:
    parts = []
    for r in results:
        mark = "OK" if r.ok else "NG"
        parts.append(f"{r.channel}:{mark}")
    return " / ".join(parts)


def detail_json(results: list[SendResult]) -> str:
    return json.dumps(
        [{"channel": r.channel, "ok": r.ok, "detail": r.detail} for r in results],
        ensure_ascii=False,
    )
