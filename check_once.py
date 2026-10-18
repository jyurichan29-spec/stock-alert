#!/usr/bin/env python3
"""1 回だけチェックを実行する（GitHub Actions / cron / systemd timer 用）.

使い方:
    python3 scripts/check_once.py                       # 取引時間のみチェック
    python3 scripts/check_once.py --force               # 取引時間外でも実行
    python3 scripts/check_once.py --dry-run             # 通知を送らず判定だけ
    python3 scripts/check_once.py --json                # 結果を JSON で出力
    python3 scripts/check_once.py --state-file state/state.json
    python3 scripts/check_once.py --seed-file watchlist.txt --seed-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stockalert import db  # noqa: E402
from stockalert.checker import run_check  # noqa: E402
from stockalert.quotes import fetch_quote, normalize_symbol  # noqa: E402

DEFAULT_THRESHOLDS = "15,20,25,30"


def _parse_seed_line(line: str) -> tuple[str, str] | None:
    """watchlist.txt の 1 行を (銘柄コード, 基準株価文字列) に分解する。"""
    text = line.split("#", 1)[0].strip()
    if not text:
        return None
    if "=" in text:
        symbol, price = text.split("=", 1)
        return symbol.strip(), price.strip()
    return text, ""


def seed_from_file(path: str) -> list[str]:
    """watchlist.txt を読み、監視リストへ登録／更新する。

    ルール（何度実行しても同じ結果になる）:
      - 未登録の銘柄            -> 登録する（基準株価の指定が無ければ現在値を使う）
      - 登録済み + 基準株価が違う -> 基準株価を更新し、通知済みレベルをリセット
      - 登録済み + 同じ          -> 何もしない
    """
    changes: list[str] = []
    if not path or not os.path.exists(path):
        return changes

    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()

    for line in lines:
        parsed = _parse_seed_line(line)
        if parsed is None:
            continue
        symbol_raw, price_raw = parsed

        try:
            symbol = normalize_symbol(symbol_raw)
        except Exception as exc:  # noqa: BLE001
            print(f"  スキップ（銘柄コードが不正）: {symbol_raw} ({exc})", file=sys.stderr)
            continue

        base: float | None = None
        if price_raw:
            try:
                base = float(price_raw.replace(",", ""))
            except ValueError:
                print(f"  スキップ（基準株価が数値でない）: {line.strip()}", file=sys.stderr)
                continue

        existing = db.get_watch(symbol)

        if existing is None:
            name = ""
            if base is None:
                try:
                    quote = fetch_quote(symbol)
                    base, name = quote.price, quote.name
                except Exception as exc:  # noqa: BLE001
                    print(f"  スキップ（現在値を取得できず）: {symbol} ({exc})", file=sys.stderr)
                    continue
            db.add_watch(symbol, base, name=name, thresholds=DEFAULT_THRESHOLDS)
            db.reset_levels(symbol)
            changes.append(f"追加 {symbol} 基準株価={base:,.1f}")
            continue

        if base is not None and abs(float(existing["base_price"]) - base) > 0.001:
            db.update_watch(symbol, base_price=base)
            db.reset_levels(symbol)
            changes.append(
                f"更新 {symbol} 基準株価 {existing['base_price']:,.1f} → {base:,.1f}（通知済みレベルをリセット）"
            )

    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description="株価変動チェックを1回実行する")
    parser.add_argument("--force", action="store_true", help="取引時間外でも実行する")
    parser.add_argument("--dry-run", action="store_true", help="通知を送らず判定のみ")
    parser.add_argument("--json", action="store_true", help="結果を JSON で標準出力")
    parser.add_argument("--add", action="append", default=[],
                        metavar="SYMBOL=BASEPRICE", help="銘柄と基準株価を登録する")
    parser.add_argument("--seed-file", default=None,
                        help="監視銘柄を書いたテキストファイル（例 watchlist.txt）")
    parser.add_argument("--seed-only", action="store_true",
                        help="監視銘柄の登録だけを行い、株価チェックは行わない")
    parser.add_argument("--state-file", default=os.environ.get("STOCKALERT_STATE_FILE"),
                        help="状態を JSON ファイルへ保存／復元（サーバーレス実行用）")
    args = parser.parse_args()

    db.init_db()

    # 前回の状態を復元（通知済みレベルを引き継ぐ）
    if args.state_file and os.path.exists(args.state_file):
        try:
            with open(args.state_file, encoding="utf-8") as fh:
                db.import_state(json.load(fh))
            print(f"状態を復元しました: {args.state_file}")
        except Exception as exc:  # noqa: BLE001
            print(f"警告: 状態ファイルを読み込めませんでした: {exc}", file=sys.stderr)

    # 監視銘柄の登録／更新
    seed_changes: list[str] = []
    if args.seed_file:
        seed_changes = seed_from_file(args.seed_file)
        for change in seed_changes:
            print(f"  {change}")

    for spec in args.add:
        if "=" not in spec:
            print(f"書式エラー（SYMBOL=BASEPRICE）: {spec}", file=sys.stderr)
            continue
        sym_raw, price_raw = spec.split("=", 1)
        try:
            symbol = normalize_symbol(sym_raw)
            db.add_watch(symbol, float(price_raw))
            db.reset_levels(symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"登録失敗 {spec}: {exc}", file=sys.stderr)

    def save_state() -> None:
        if not args.state_file:
            return
        directory = os.path.dirname(os.path.abspath(args.state_file))
        os.makedirs(directory, exist_ok=True)
        with open(args.state_file, "w", encoding="utf-8") as fh:
            json.dump(db.export_state(), fh, ensure_ascii=False, indent=1)

    if args.seed_only:
        save_state()
        print(f"監視銘柄の登録のみ完了（変更 {len(seed_changes)} 件）")
        return 0

    result = run_check(force=args.force, dry_run=args.dry_run)
    save_state()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["summary"])
        for rec in result["results"]:
            if rec.get("error"):
                print(f"  [ERR] {rec['symbol']}: {rec['error']}")
            elif rec.get("skipped"):
                print(f"  [--]  {rec['symbol']} 取引時間外 ({rec.get('market')})")
            elif rec.get("event") not in (None, "none"):
                print(f"  [!!]  {rec.get('title', rec['symbol'])} -> {rec.get('channels', '')}")
            else:
                pct = rec.get("change_pct")
                print(f"  [ok]  {rec['symbol']} {pct:+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
