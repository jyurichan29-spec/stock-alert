#!/usr/bin/env python3
"""GitHub Issue（銘柄登録フォーム）の内容を watchlist.txt に反映する。

Issue Form が生成する本文は次の形式になる:

    ### 操作

    追加・更新

    ### 銘柄コード

    7203

    ### 基準株価

    2900

このスクリプトは見出し（= フォームのラベル）で値を取り出し、
watchlist.txt を追加・更新・削除する。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stockalert.quotes import QuoteError, normalize_symbol  # noqa: E402

LABEL_ACTION = "操作"
LABEL_SYMBOL = "銘柄コード"
LABEL_PRICE = "基準株価"
LABEL_NOTE = "メモ"

ACTION_DELETE = "削除"
ACTION_ADD = "追加"

NO_RESPONSE = {"_no response_", "_noresponse_", ""}


# --------------------------------------------------------------------------
# Issue 本文の解析
# --------------------------------------------------------------------------
def parse_sections(body: str) -> dict[str, str]:
    """'### 見出し' 単位で本文を辞書にする。"""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in (body or "").replace("\r\n", "\n").splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def clean(value: str) -> str:
    text = (value or "").strip()
    if text.lower().replace(" ", "") in NO_RESPONSE:
        return ""
    return text


def find_value(sections: dict[str, str], keyword: str) -> str:
    """ラベルに keyword を含む項目の値を返す（『（任意）』等の揺れを許容）。"""
    for label, value in sections.items():
        if keyword in label:
            return clean(value)
    return ""


# --------------------------------------------------------------------------
# watchlist.txt の編集
# --------------------------------------------------------------------------
def _symbol_of(line: str) -> str | None:
    """watchlist.txt の 1 行から正規化済みの銘柄コードを取り出す。"""
    text = line.split("#", 1)[0].strip()
    if not text:
        return None
    raw = text.split("=", 1)[0].strip()
    if not raw:
        return None
    try:
        return normalize_symbol(raw)
    except QuoteError:
        return None


def apply_change(lines: list[str], action: str, symbol: str,
                 price: str) -> tuple[list[str], str]:
    """1 件の追加・更新・削除を適用し、(新しい行リスト, 説明) を返す。"""
    target = normalize_symbol(symbol)
    kept: list[str] = []
    removed: list[str] = []

    for line in lines:
        found = _symbol_of(line)
        if found is not None and found == target:
            removed.append(line.strip())
            continue
        kept.append(line)

    if action == ACTION_DELETE:
        if not removed:
            raise ValueError(f"{symbol} は監視リストに登録されていません")
        return kept, f"🗑 **{target} を削除しました**（元の行: `{removed[0]}`）"

    new_line = f"{symbol.strip()}={price}" if price else symbol.strip()
    while kept and not kept[-1].strip():
        kept.pop()
    kept.append(new_line)

    if removed:
        detail = f"（旧: `{removed[0]}` → 新: `{new_line}`）"
        return kept, f"♻️ **{target} を更新しました**{detail} 基準株価を変更したため、通知済みの状態はリセットされます。"
    return kept, f"➕ **{target} を追加しました**（`{new_line}`）"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Issue の内容を watchlist.txt に反映する")
    parser.add_argument("--body-file", required=True, help="Issue 本文を書いたファイル")
    parser.add_argument("--watchlist", default="watchlist.txt", help="watchlist.txt のパス")
    parser.add_argument("--summary", default=None, help="結果の要約を書き出すファイル")
    args = parser.parse_args()

    with open(args.body_file, encoding="utf-8") as fh:
        body = fh.read()

    sections = parse_sections(body)
    action_raw = find_value(sections, LABEL_ACTION)
    symbol = find_value(sections, LABEL_SYMBOL)
    price_raw = find_value(sections, LABEL_PRICE)
    note = find_value(sections, LABEL_NOTE)

    summary_lines: list[str] = []

    if not symbol:
        summary_lines.append("❌ **銘柄コードが空です。** issue を編集して銘柄コードを入力してください。")
        _write_summary(args, summary_lines)
        return 1

    action = ACTION_DELETE if ACTION_DELETE in action_raw else ACTION_ADD

    price = ""
    if price_raw:
        try:
            price = f"{float(price_raw.replace(',', '')):g}"
        except ValueError:
            summary_lines.append(f"❌ **基準株価が数値ではありません**: `{price_raw}`")
            _write_summary(args, summary_lines)
            return 1

    path = args.watchlist
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().replace("\r\n", "\n").split("\n")

    try:
        new_lines, message = apply_change(lines, action, symbol, price)
    except (ValueError, QuoteError) as exc:
        summary_lines.append(f"❌ **反映できませんでした**: {exc}")
        _write_summary(args, summary_lines)
        return 1

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(new_lines).rstrip("\n") + "\n")

    summary_lines.append(message)
    if action != ACTION_DELETE:
        if price:
            summary_lines.append(f"- 基準株価: `{price}`")
        else:
            summary_lines.append("- 基準株価: 未指定 → 次回チェック時に**そのときの株価**を基準にします")
    if note:
        summary_lines.append(f"- メモ: {note}")
    summary_lines.append("")
    if action == ACTION_DELETE:
        summary_lines.append("次回の定期チェックから監視対象外になります。")
    else:
        summary_lines.append("次回の定期チェック（15分ごと）から監視対象になります。")

    _write_summary(args, summary_lines)
    for line in summary_lines:
        print(line)
    return 0


def _write_summary(args, summary_lines: list[str]) -> None:
    if args.summary:
        with open(args.summary, "w", encoding="utf-8") as fh:
            fh.write("\n".join(summary_lines))


if __name__ == "__main__":
    raise SystemExit(main())
