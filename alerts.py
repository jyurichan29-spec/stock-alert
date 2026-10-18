"""閾値判定エンジン（純粋関数のみ。テストしやすさを優先）.

判定ルール
----------
- 変動率 = (現在値 - 基準株価) / 基準株価 * 100
- 上昇方向・下降方向それぞれで、到達した最大の閾値（15/20/25/30...）を追跡する
- 到達レベルが前回より **上がった** とき  -> "escalate"（新たな閾値到達。必ず通知）
- 同じレベルに留まりクールダウンを経過 -> "repeat"（再通知）
- レベルが 0 に戻ったとき（基準値圏内に復帰） -> "reset"（任意で通知）
- レベルが下がったが 0 ではない -> 通知せずレベルのみ更新
"""

from __future__ import annotations

from dataclasses import dataclass

EVENT_ESCALATE = "escalate"
EVENT_REPEAT = "repeat"
EVENT_RESET = "reset"
EVENT_NONE = "none"


@dataclass(frozen=True)
class Decision:
    new_level: int
    event: str
    direction: str  # up | down | flat

    @property
    def should_notify(self) -> bool:
        return self.event in (EVENT_ESCALATE, EVENT_REPEAT, EVENT_RESET)


def parse_thresholds(value) -> list[int]:
    """'15,20,25,30' や [15, 20] を [15, 20, 25, 30] に正規化する。"""
    if value is None:
        items: list = []
    elif isinstance(value, str):
        items = [p for p in value.replace("、", ",").replace(" ", "").split(",") if p]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]

    out: set[int] = set()
    for item in items:
        try:
            num = abs(float(item))
        except (TypeError, ValueError):
            continue
        if num <= 0:
            continue
        out.add(int(round(num)))
    return sorted(out)


def change_pct(price: float, base_price: float) -> float:
    if not base_price:
        return 0.0
    return (float(price) - float(base_price)) / float(base_price) * 100.0


def highest_level(pct: float, thresholds: list[int]) -> int:
    """変動率が到達している最大の閾値を返す（未到達なら 0）。"""
    reached = [t for t in thresholds if abs(pct) >= t]
    return max(reached) if reached else 0


def direction_of(pct: float, dead_zone: float = 0.0) -> str:
    if pct > dead_zone:
        return "up"
    if pct < -dead_zone:
        return "down"
    return "flat"


def is_allowed(direction: str, setting: str) -> bool:
    """設定値 (both / up / down) に対して、その方向の通知を許可するか。"""
    if direction == "flat":
        return False
    if setting == "both":
        return True
    return setting == direction


def decide(
    pct: float,
    thresholds: list[int],
    prev_level: int,
    last_notified_at: float,
    now: float,
    cooldown_sec: float = 3600,
    notify_on_reset: bool = True,
) -> Decision:
    """1 銘柄・1 方向の判定を行う。

    Args:
        pct: 基準株価に対する変動率(%)
        thresholds: 閾値リスト（例 [15, 20, 25, 30]）
        prev_level: 前回通知済みの最大レベル
        last_notified_at: 前回通知時刻（epoch 秒。未通知は 0）
        now: 現在時刻（epoch 秒）
        cooldown_sec: 同レベル再通知までの待ち時間
        notify_on_reset: 基準値圏内に戻ったときに通知するか
    """
    level = highest_level(pct, thresholds)
    direction = direction_of(pct)

    if level > prev_level:
        return Decision(level, EVENT_ESCALATE, direction)

    if level == prev_level and level > 0:
        if last_notified_at and (now - last_notified_at) >= cooldown_sec:
            return Decision(level, EVENT_REPEAT, direction)
        return Decision(level, EVENT_NONE, direction)

    if level < prev_level:
        if level == 0 and prev_level > 0 and notify_on_reset:
            return Decision(0, EVENT_RESET, direction)
        return Decision(level, EVENT_NONE, direction)

    return Decision(level, EVENT_NONE, direction)


def format_message(
    symbol: str,
    name: str,
    price: float,
    base_price: float,
    pct: float,
    level: int,
    direction: str,
    event: str,
    currency: str = "",
) -> tuple[str, str]:
    """通知のタイトルと本文を組み立てる。"""
    unit = f"{currency} " if currency else ""
    arrow = "▲" if direction == "up" else ("▼" if direction == "down" else "→")
    sign = "+" if pct >= 0 else ""
    label = f"{name} ({symbol})" if name and name != symbol else symbol

    if event == EVENT_RESET:
        title = f"⚪️ {label} 基準値圏内に復帰"
        body = (
            f"基準株価 {unit}{base_price:,.2f} に対して "
            f"{sign}{pct:.2f}% まで戻りました。\n"
            f"現在値: {unit}{price:,.2f}\n"
            f"閾値 {level}% 以上の状態は解消しています。"
        )
        return title, body

    word = "上昇" if direction == "up" else "下降"
    repeat = "（再通知）" if event == EVENT_REPEAT else ""
    title = f"{arrow} {label} {sign}{pct:.2f}% {word} / {level}%超{repeat}"
    body = (
        f"基準株価: {unit}{base_price:,.2f}\n"
        f"現在値  : {unit}{price:,.2f}\n"
        f"変動率  : {sign}{pct:.2f}%（{level}% 以上の{word}）\n"
        f"判定    : {level}% 閾値を突破"
        + ("（エスカレーション）" if event == EVENT_ESCALATE else "（継続中）")
    )
    return title, body


def summarize_decisions(results: list[dict]) -> str:
    """チェック結果を1行のサマリ文字列にする。"""
    fired = [r for r in results if r.get("event") not in (None, EVENT_NONE)]
    errors = [r for r in results if r.get("error")]
    return (
        f"チェック {len(results)} 銘柄 / 通知 {len(fired)} 件 / "
        f"エラー {len(errors)} 件"
    )
