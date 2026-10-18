"""Flask 管理画面."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from . import db
from .alerts import change_pct, direction_of, highest_level, parse_thresholds
from .checker import describe_market_state, run_check
from .config import DEFAULT_SETTINGS, as_bool, as_int
from .notifier import CHANNELS, dispatch, enabled_channels
from .quotes import QuoteError, fetch_quote, normalize_symbol
from .scheduler import restart_scheduler, scheduler_status


def _tz(settings: dict) -> ZoneInfo:
    try:
        return ZoneInfo(settings.get("timezone") or "Asia/Tokyo")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Tokyo")


def _fmt(ts, settings: dict, fmt: str = "%m/%d %H:%M:%S") -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(float(ts), tz=_tz(settings)).strftime(fmt)


def build_rows(settings: dict | None = None) -> list[dict]:
    """画面表示用に監視銘柄を整形する。"""
    settings = settings or db.get_settings()
    default_thresholds = parse_thresholds(settings.get("default_thresholds"))
    rows = []
    for item in db.list_watchlist():
        thresholds = parse_thresholds(item.get("thresholds")) or default_thresholds
        pct = item.get("last_change_pct")
        level = highest_level(pct, thresholds) if pct is not None else 0
        dirn = direction_of(pct) if pct is not None else "flat"
        notified_level = (
            item["up_level"] if dirn == "up" else item["down_level"]
        ) if dirn in ("up", "down") else 0
        rows.append({
            **item,
            "threshold_list": thresholds,
            "change_pct": None if pct is None else round(pct, 2),
            "level": level,
            "dir": dirn,
            "notified_level": notified_level,
            "last_checked_str": _fmt(item.get("last_checked_at"), settings, "%H:%M:%S"),
            "base_price_str": f"{item['base_price']:,.1f}",
            "last_price_str": "—" if item.get("last_price") is None
                              else f"{item['last_price']:,.1f}",
            "change_str": "—" if pct is None else f"{pct:+.2f}%",
        })
    return rows


def create_app(db_path: str | None = None) -> Flask:
    db.init_db(db_path)
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("STOCKALERT_SECRET_KEY", "stock-alert-local-secret")
    app.config["DB_PATH"] = db_path

    def dbp():
        return app.config.get("DB_PATH")

    # ------------------------------------------------------------------
    # フィルタ
    # ------------------------------------------------------------------
    @app.template_filter("dt")
    def dt_filter(ts, fmt="%m/%d %H:%M:%S"):
        return _fmt(ts, db.get_settings(dbp()), fmt)

    # ------------------------------------------------------------------
    # 画面
    # ------------------------------------------------------------------
    @app.route("/")
    def index():
        settings = db.get_settings(dbp())
        rows = build_rows(settings)
        alerts = db.list_alerts(limit=8, db_path=dbp())
        return render_template(
            "index.html",
            rows=rows,
            settings=settings,
            alerts=alerts,
            channels=enabled_channels(settings),
            sched=scheduler_status(),
            market=describe_market_state(),
            default_thresholds=settings.get("default_thresholds"),
            default_direction=settings.get("default_direction", "both"),
        )

    @app.route("/alerts")
    def alerts_page():
        settings = db.get_settings(dbp())
        return render_template(
            "alerts.html",
            alerts=db.list_alerts(limit=300, db_path=dbp()),
            settings=settings,
        )

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        if request.method == "POST":
            values = {}
            for key in DEFAULT_SETTINGS:
                if key.startswith("channel_"):
                    values[key] = "true" if request.form.get(key) else "false"
                elif key in request.form:
                    values[key] = request.form.get(key, "").strip()
            db.save_settings(values, db_path=dbp())
            interval = as_int(values.get("poll_interval_minutes"), 15)
            restart_scheduler(max(1, interval))
            flash("設定を保存しました。次回チェック間隔を反映しました。", "ok")
            return redirect(url_for("settings_page"))

        settings = db.get_settings(dbp())
        return render_template("settings.html", settings=settings,
                               sched=scheduler_status())

    # ------------------------------------------------------------------
    # 監視リスト操作
    # ------------------------------------------------------------------
    @app.post("/watchlist")
    def add_watch():
        raw_symbol = (request.form.get("symbol") or "").strip()
        name = (request.form.get("name") or "").strip()
        note = (request.form.get("note") or "").strip()
        base_raw = (request.form.get("base_price") or "").strip()
        thresholds = (request.form.get("thresholds") or "").strip()
        direction = request.form.get("direction") or "both"
        try:
            symbol = normalize_symbol(raw_symbol)
        except QuoteError as exc:
            flash(f"銘柄コードが不正です: {exc}", "err")
            return redirect(url_for("index"))

        if not base_raw:
            # 未入力なら現在値を基準株価として自動設定
            try:
                quote = fetch_quote(symbol)
                base_price = quote.price
                name = name or quote.name
                flash(f"{symbol} の基準株価を現在値 {base_price:,.1f} で登録しました。", "ok")
            except Exception as exc:  # noqa: BLE001
                flash(f"基準株価を取得できませんでした。手入力してください: {exc}", "err")
                return redirect(url_for("index"))
        else:
            try:
                base_price = float(base_raw.replace(",", ""))
            except ValueError:
                flash("基準株価は数値で入力してください。", "err")
                return redirect(url_for("index"))

        if not thresholds:
            thresholds = db.get_settings(dbp()).get("default_thresholds", "15,20,25,30")
        if not parse_thresholds(thresholds):
            flash("閾値の書式が不正です（例: 15,20,25,30）。", "err")
            return redirect(url_for("index"))

        db.add_watch(
            symbol=symbol, base_price=base_price, name=name,
            thresholds=",".join(str(t) for t in parse_thresholds(thresholds)),
            direction=direction, note=note,
            base_date=datetime.now(tz=_tz(db.get_settings(dbp()))).strftime("%Y-%m-%d"),
            db_path=dbp(),
        )
        db.reset_levels(symbol, db_path=dbp())
        flash(f"{symbol} を監視リストに登録しました（基準株価 {base_price:,.1f}）。", "ok")
        return redirect(url_for("index"))

    @app.post("/watchlist/<symbol>/delete")
    def delete_watch(symbol: str):
        db.delete_watch(symbol, db_path=dbp())
        flash(f"{symbol} を監視リストから削除しました。", "ok")
        return redirect(url_for("index"))

    @app.post("/watchlist/<symbol>/toggle")
    def toggle_watch(symbol: str):
        item = db.get_watch(symbol, db_path=dbp())
        if item:
            db.update_watch(symbol, db_path=dbp(),
                            enabled=0 if item["enabled"] else 1)
            flash(f"{symbol} を{'停止' if item['enabled'] else '再開'}しました。", "ok")
        return redirect(url_for("index"))

    @app.post("/watchlist/<symbol>/reset")
    def reset_watch(symbol: str):
        db.reset_levels(symbol, db_path=dbp())
        flash(f"{symbol} の通知済みレベルをリセットしました。", "ok")
        return redirect(url_for("index"))

    # ------------------------------------------------------------------
    # チェック実行
    # ------------------------------------------------------------------
    @app.post("/check")
    def check_now():
        force = as_bool(request.form.get("force"), True)
        dry = as_bool(request.form.get("dry_run"), False)
        try:
            result = run_check(db_path=dbp(), force=force, dry_run=dry)
        except Exception as exc:  # noqa: BLE001
            flash(f"チェックに失敗しました: {exc}", "err")
            return redirect(url_for("index"))
        flash(result["summary"], "ok" if result["fired"] else "info")
        for rec in result["results"]:
            if rec.get("error"):
                flash(f"{rec['symbol']}: {rec['error']}", "err")
        return redirect(url_for("index"))

    @app.post("/notify/test")
    def notify_test():
        settings = db.get_settings(dbp())
        title = "🔔 テスト通知 / 株価変動アラート"
        body = "通知チャネルの接続テストです。このメッセージが届けば設定は正常です。"
        sends = dispatch(settings, title, body, level=15)
        ok = [s for s in sends if s.ok]
        ng = [s for s in sends if not s.ok]
        if ok:
            flash("送信成功: " + ", ".join(s.channel for s in ok), "ok")
        for s in ng:
            flash(f"{s.channel} 送信失敗: {s.detail}", "err")
        return redirect(request.referrer or url_for("settings_page"))

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    @app.get("/api/status")
    def api_status():
        settings = db.get_settings(dbp())
        rows = build_rows(settings)
        active = [r for r in rows if r["level"] > 0]
        return jsonify({
            "now": datetime.now(tz=_tz(settings)).strftime("%H:%M:%S"),
            "market": describe_market_state(),
            "scheduler": scheduler_status(),
            "counts": {
                "watch": len(rows),
                "active": len(active),
                "channels": len(enabled_channels(settings)),
            },
            "rows": rows,
        })

    @app.get("/api/quote")
    def api_quote():
        raw = request.args.get("symbol", "")
        try:
            symbol = normalize_symbol(raw)
            quote = fetch_quote(symbol)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({
            "ok": True,
            "symbol": quote.symbol,
            "name": quote.name,
            "price": quote.price,
            "currency": quote.currency,
            "exchange": quote.exchange,
            "day_change_pct": quote.day_change_pct,
        })

    @app.get("/api/history/<symbol>")
    def api_history(symbol: str):
        return jsonify({"symbol": symbol,
                        "history": db.price_history(symbol, 120, db_path=dbp())})

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    return app
