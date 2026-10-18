"""株価取得モジュール.

メイン: Yahoo Finance の chart API (API キー不要)
予備  : yfinance ライブラリ（インストールされていれば）
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Iterable

import requests

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


class QuoteError(RuntimeError):
    """株価取得に失敗したときに送出する。"""


@dataclass
class Quote:
    symbol: str
    price: float
    currency: str = ""
    name: str = ""
    exchange: str = ""
    timezone: str = ""
    previous_close: float | None = None
    day_change_pct: float | None = None
    market_time: float | None = None
    source: str = "yahoo"
    raw: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# 銘柄コードの正規化
# --------------------------------------------------------------------------
def normalize_symbol(raw: str) -> str:
    """入力された銘柄コードを Yahoo Finance 形式に正規化する。

    受け付ける書式:
      - "7203" / "7203.T" / "7203.jp" / "7203T" / "7203 JT"  -> "7203.T" (東証)
      - "160A"                                               -> "160A.T" (東証の新コード)
      - "AAPL" / "aapl"                                      -> "AAPL"   (米国)
      - "BRK-B" / "0700.HK" / "005930.KS"                    -> そのまま（大文字化）
    """
    s = (raw or "").strip().upper()
    s = re.sub(r"[\s\u3000]+", "", s)  # 半角・全角スペースを除去
    if not s:
        raise QuoteError("銘柄コードが空です")

    if s.endswith(".JP"):
        s = s[:-3] + ".T"

    # 東証の4桁コード + 国コード表記 (例 "7203T" / "7203JT" / "7203JP")
    m = re.fullmatch(r"(\d{4})(T|JT|JP)", s)
    if m:
        return m.group(1) + ".T"

    # 東証の英数字コード (例 "160A" / "130A")
    if re.fullmatch(r"\d{3}[A-Z]", s):
        return s + ".T"

    # 数字4桁のみ -> 東証
    # ※ 香港市場にも4桁コードがあるため、その場合は "0700.HK" と明示する必要がある
    if re.fullmatch(r"\d{4}", s):
        return s + ".T"

    return s


def guess_market(symbol: str) -> str:
    """銘柄コードから市場区分を推定する（取引時間判定に使用）。"""
    s = symbol.upper()
    if s.endswith(".T"):
        return "JP"
    if s.endswith((".HK", ".SS", ".SZ", ".KS", ".KQ", ".TW", ".TWO")):
        return "OTHER"
    if s.endswith((".L", ".DE", ".PA", ".AS", ".MI", ".SW", ".ST", ".CO", ".OL",
                   ".HE", ".VI", ".BR", ".LS", ".IR", ".MC", ".F", ".SG", ".AX",
                   ".NZ", ".TO", ".V", ".SA", ".MX", ".IS")):
        return "OTHER"
    return "US"


# --------------------------------------------------------------------------
# 取得
# --------------------------------------------------------------------------
def _yahoo_chart(symbol: str, interval: str, rng: str, timeout: float) -> dict:
    url = YAHOO_CHART.format(symbol=requests.utils.quote(symbol, safe=""))
    params = {
        "interval": interval,
        "range": rng,
        "includePrePost": "false",
        "events": "div,split",
    }
    resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
    if resp.status_code == 404:
        raise QuoteError(f"銘柄が見つかりません: {symbol}")
    if resp.status_code == 429:
        raise QuoteError("Yahoo Finance のレート制限に達しました（時間をおいて再試行）")
    resp.raise_for_status()
    payload = resp.json()
    chart = payload.get("chart") or {}
    if chart.get("error"):
        err = chart["error"]
        raise QuoteError(f"Yahoo API エラー: {err.get('description') or err}")
    results = chart.get("result")
    if not results:
        raise QuoteError(f"株価データが取得できませんでした: {symbol}")
    return results[0]


def _quote_from_yahoo(symbol: str, timeout: float = 15.0) -> Quote:
    last_err: Exception | None = None
    for interval, rng in (("1m", "1d"), ("5m", "5d"), ("1d", "1mo")):
        try:
            result = _yahoo_chart(symbol, interval, rng, timeout)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue

        meta = result.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            # フォールバック: 終値の最後の値を使う
            closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]
                      .get("close") or [])
            closes = [c for c in closes if c is not None]
            if closes:
                price = closes[-1]
        if price is None:
            last_err = QuoteError(f"現在値が取得できませんでした: {symbol}")
            continue

        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        change_pct = meta.get("regularMarketChangePercent")
        if change_pct is None and prev:
            change_pct = (float(price) - float(prev)) / float(prev) * 100.0

        return Quote(
            symbol=meta.get("symbol") or symbol,
            price=float(price),
            currency=meta.get("currency") or "",
            name=meta.get("longName") or meta.get("shortName") or "",
            exchange=meta.get("fullExchangeName") or meta.get("exchangeName") or "",
            timezone=meta.get("exchangeTimezoneName") or "",
            previous_close=float(prev) if prev else None,
            day_change_pct=float(change_pct) if change_pct is not None else None,
            market_time=meta.get("regularMarketTime"),
            source=f"yahoo:{interval}/{rng}",
            raw={"meta": meta},
        )
    raise QuoteError(str(last_err) if last_err else f"株価取得に失敗: {symbol}")


def _quote_from_yfinance(symbol: str) -> Quote:
    """yfinance がインストールされている場合のみ使うフォールバック。"""
    try:
        import yfinance as yf  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise QuoteError("yfinance が利用できません") from exc

    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="5d", interval="1d")
    if hist is None or hist.empty:
        raise QuoteError(f"yfinance: データなし ({symbol})")
    price = float(hist["Close"].iloc[-1])
    prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
    info: dict = {}
    try:
        info = ticker.fast_info or {}
    except Exception:  # noqa: BLE001
        info = {}
    return Quote(
        symbol=symbol,
        price=price,
        currency=str(getattr(info, "get", lambda *_: "")("currency") or ""),
        name="",
        previous_close=prev,
        day_change_pct=((price - prev) / prev * 100.0) if prev else None,
        source="yfinance",
    )


def fetch_quote(symbol: str, timeout: float = 15.0, allow_yfinance: bool = True) -> Quote:
    """1銘柄の現在値を取得する。"""
    sym = normalize_symbol(symbol)
    try:
        return _quote_from_yahoo(sym, timeout=timeout)
    except Exception as yahoo_err:  # noqa: BLE001
        if allow_yfinance:
            try:
                return _quote_from_yfinance(sym)
            except Exception:  # noqa: BLE001
                pass
        msg = str(yahoo_err)
        # 東証として解決できなかった場合、他市場のサフィックスを案内する
        if sym.endswith(".T") and ("見つかりません" in msg or "Not Found" in msg):
            msg += ("（東証以外の銘柄は市場サフィックスを付けて入力してください: "
                    "香港=0700.HK / 韓国=005930.KS / ロンドン=VOD.L）")
        raise QuoteError(msg) from yahoo_err


def fetch_quotes(
    symbols: Iterable[str],
    timeout: float = 15.0,
    sleep_sec: float = 0.35,
    allow_yfinance: bool = True,
) -> tuple[dict[str, Quote], dict[str, str]]:
    """複数銘柄をまとめて取得する。

    Returns:
        (成功した Quote の dict, 失敗した symbol -> エラーメッセージ の dict)
    """
    quotes: dict[str, Quote] = {}
    errors: dict[str, str] = {}
    seen: set[str] = set()
    for raw in symbols:
        try:
            sym = normalize_symbol(raw)
        except QuoteError as exc:
            errors[str(raw)] = str(exc)
            continue
        if sym in seen:
            continue
        seen.add(sym)
        try:
            quotes[sym] = fetch_quote(sym, timeout=timeout, allow_yfinance=allow_yfinance)
        except Exception as exc:  # noqa: BLE001
            errors[sym] = str(exc)
        time.sleep(sleep_sec)
    return quotes, errors
