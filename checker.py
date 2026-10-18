"""監視サイクル本体.

15分ごとのスケジューラからも、Web の「今すぐチェック」からも、
cron / GitHub Actions の単発実行からも、この 1 関数を呼ぶ。
"""

from __future__ import annotations

import time
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from . import db
from .alerts import (
    EVENT_ESCALATE,
    EVENT_NONE,
    EVENT_RESET,
    change_pct,
    decide,
    format_message,
    is_allowed,
    parse_thresholds,
)
from .config import as_bool, as_int
from .notifier import detail_json, dispatch, summarize
from .quotes import QuoteError, fetch_quotes, guess_market

# 市場ごとの取引時間（現地時間）
SESSIONS = {
    "JP": ("Asia/Tokyo", [(dtime(9, 0), dtime(11, 30)), (dtime(12, 30), dtime(15, 0))]),
    "US": ("America/New_York", [(dtime(9, 30), dtime(16, 0))]),
}


def market_status(symbol: str, now_utc: datetime | None = None) -> tuple[bool, str]:
    """取引時間内かどうかを返す。判定できない市場は常に True（＝チェックする）。"""
    market = guess_market(symbol)
    if market not in SESSIONS:
        return True, "unknown"
    tz_name, windows = SESSIONS[market]
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        return True, "unknown"
    now_local = (now_utc or datetime.now(tz=ZoneInfo("UTC"))).astimezone(tz)
    if now_local.weekday() >= 5:
        return False, "weekend"
    current = now_local.time()
    for start, end in windows:
        if start <= current <= end:
            return True, "open"
    return False, "closed"


def run_check(
    db_path: str | None = None,
    force: bool = False,
    now: float | None = None,
    dry_run: bool = False,
) -> dict:
    """監視対象をすべてチェックし、必要なら通知する。

    Args:
        force: True なら取引時間外でもチェックする（手動実行用）
        dry_run: True なら通知を送らず、判定結果だけ返す
    """
    settings = db.get_settings(db_path)
    thresholds_default = parse_thresholds(settings.get("default_thresholds"))
    respect_hours = as_bool(settings.get("respect_market_hours"), True)
    cooldown_sec = as_int(settings.get("renotify_cooldown_minutes"), 60) * 60
    notify_on_reset = as_bool(settings.get("notify_on_reset"), True)
    now_ts = now if now is not None else time.time()
    now_utc = datetime.fromtimestamp(now_ts, tz=ZoneInfo("UTC"))

    watchlist = db.list_watchlist(db_path, only_enabled=True)
    results: list[dict] = []

    if not watchlist:
        return {"checked": 0, "notified": 0, "errors": 0, "skipped": 0,
                "results": [], "summary": "監視対象が登録されていません"}

    # 取引時間の判定
    to_check: list[dict] = []
    for item in watchlist:
        if respect_hours and not force:
            is_open, state = market_status(item["symbol"], now_utc)
            if not is_open:
                results.append({
                    "symbol": item["symbol"], "name": item.get("name", ""),
                    "skipped": True, "market": state, "event": EVENT_NONE,
                })
                continue
        to_check.append(item)

    quotes, fetch_errors = fetch_quotes([i["symbol"] for i in to_check]) if to_check else ({}, {})

    notified = 0
    fired = 0
    errors = 0
    for item in to_check:
        symbol = item["symbol"]
        quote = quotes.get(symbol)
        if quote is None:
            msg = fetch_errors.get(symbol, "取得失敗")
            errors += 1
            db.update_watch(symbol, db_path=db_path, last_error=msg,
                            last_checked_at=now_ts)
            results.append({"symbol": symbol, "name": item.get("name", ""),
                            "error": msg, "event": EVENT_NONE})
            continue

        base_price = float(item["base_price"] or 0)
        if base_price <= 0:
            errors += 1
            db.update_watch(symbol, db_path=db_path, last_error="基準株価が未設定",
                            last_checked_at=now_ts)
            results.append({"symbol": symbol, "error": "基準株価が未設定",
                            "event": EVENT_NONE})
            continue

        pct = change_pct(quote.price, base_price)
        thresholds = parse_thresholds(item.get("thresholds")) or thresholds_default
        direction_setting = item.get("direction") or "both"

        # 上昇・下降それぞれ独立に判定する
        best_event: str | None = None
        best_level = 0
        new_up, new_down = item["up_level"], item["down_level"]
        new_up_at, new_down_at = item["up_notified_at"], item["down_notified_at"]

        for direction in ("up", "down"):
            # 逆向きに振れている場合・通知対象外の方向はレベルを 0 に戻す
            # （次にその方向へ再到達したときに改めて通知するため）
            if direction == "up":
                if pct <= 0 or not is_allowed("up", direction_setting):
                    new_up, new_up_at = 0, 0
                    continue
                prev_level, last_at = item["up_level"], item["up_notified_at"]
            else:
                if pct >= 0 or not is_allowed("down", direction_setting):
                    new_down, new_down_at = 0, 0
                    continue
                prev_level, last_at = item["down_level"], item["down_notified_at"]

            decision = decide(
                pct=pct,          # 上昇なら正、下降なら負のまま渡す
                thresholds=thresholds,
                prev_level=prev_level,
                last_notified_at=last_at,
                now=now_ts,
                cooldown_sec=cooldown_sec,
                notify_on_reset=notify_on_reset,
            )
            if direction == "up":
                new_up, new_up_at = decision.new_level, (
                    now_ts if decision.should_notify else item["up_notified_at"]
                )
            else:
                new_down, new_down_at = decision.new_level, (
                    now_ts if decision.should_notify else item["down_notified_at"]
                )

            if decision.should_notify and (best_event is None or decision.new_level > best_level):
                best_event, best_level = decision.event, decision.new_level
                best_direction = direction

        db.update_watch(
            symbol, db_path=db_path,
            last_price=quote.price, last_change_pct=pct, last_checked_at=now_ts,
            last_error="", up_level=new_up, down_level=new_down,
            up_notified_at=new_up_at, down_notified_at=new_down_at,
        )
        db.log_price(symbol, quote.price, pct, db_path=db_path)

        record = {
            "symbol": symbol,
            "name": item.get("name", "") or quote.name,
            "base_price": base_price,
            "price": quote.price,
            "change_pct": round(pct, 2),
            "currency": quote.currency,
            "level": best_level,
            "event": best_event or EVENT_NONE,
        }

        if best_event:
            title, body = format_message(
                symbol=symbol,
                name=item.get("name", "") or quote.name,
                price=quote.price,
                base_price=base_price,
                pct=pct,
                level=best_level,
                direction=best_direction,
                event=best_event,
                currency=quote.currency,
            )
            record["title"] = title
            fired += 1
            if dry_run:
                record["channels"] = "dry-run（送信なし）"
            else:
                sends = dispatch(settings, title, body, level=best_level)
                record["channels"] = summarize(sends)
                record["channel_detail"] = detail_json(sends)
                notified += 1
            db.log_alert(
                symbol=symbol, name=item.get("name", "") or quote.name,
                base_price=base_price, price=quote.price, change_pct=pct,
                level=best_level, direction=best_direction, event=best_event,
                title=title, message=body,
                channels=record.get("channels", ""), db_path=db_path,
            )

        results.append(record)

    db.prune_price_log(db_path=db_path)

    skipped = len([r for r in results if r.get("skipped")])
    send_note = "（ドライラン: 送信なし）" if dry_run else f"（送信 {notified} 件）"
    summary = (
        f"チェック {len(to_check)} 銘柄 / 閾値検知 {fired} 件{send_note} / "
        f"エラー {errors} 件 / 取引時間外スキップ {skipped} 件"
    )
    return {
        "checked": len(to_check),
        "fired": fired,
        "notified": notified,
        "errors": errors,
        "skipped": len([r for r in results if r.get("skipped")]),
        "results": results,
        "summary": summary,
        "ran_at": now_ts,
    }


def describe_market_state(now_utc: datetime | None = None) -> str:
    now_utc = now_utc or datetime.now(tz=ZoneInfo("UTC"))
    parts = []
    for market in ("JP", "US"):
        tz_name, _ = SESSIONS[market]
        local = now_utc.astimezone(ZoneInfo(tz_name))
        sample = "7203.T" if market == "JP" else "AAPL"
        is_open, state = market_status(sample, now_utc)
        parts.append(f"{market}: {'開場' if is_open else '閉場'} ({local:%m/%d %H:%M} {state})")
    return " / ".join(parts)
