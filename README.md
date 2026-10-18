# 株価変動アラート (Stock Volatility Monitor)

基準株価に対して **15% / 20% / 25% / 30% 以上** の変動が出た銘柄を検知し、
スマートフォンへ **プッシュ通知** するアプリです。

- ① 銘柄と基準株価を手動登録（現在値の自動取得も可）
- ② **15分ごと** に自動でブラウジング（株価取得）して変動をチェック
- ③ 15 / 20 / 25 / 30 % 以上の閾値到達でアラートを発報（エスカレーション対応）
- ④ ntfy / Telegram / LINE / Discord / Slack / Webhook / メール で **スマホへプッシュ通知**

---

## 0. ダウンロードして使う手順

### GitHub Actions（サーバー不要・無料）

`.github/workflows/stock-check.yml` を同梱しています。リポジトリの
**Settings → Secrets and variables → Actions** に以下を登録して push するだけです。

| Secret 名 | 値 |
|---|---|
| `STOCKALERT_NTFY_TOPIC` | ntfy のトピック名 |
| `STOCKALERT_TELEGRAM_BOT_TOKEN` | （Telegram を使う場合） |
| `STOCKALERT_TELEGRAM_CHAT_ID` | （Telegram を使う場合） |

**監視銘柄の登録は2通り**あります。

1. **GitHub のフォームから登録**（ブラウザだけで完結）
   **Issues → New issue → 「銘柄を登録・削除」** を選び、銘柄コードと基準株価を入力して送信します。
   数十秒後に自動で結果がコメントされ、issue が閉じられます。
2. **`watchlist.txt` を直接編集**
   `7203=2900`（基準株価あり）または `7203`（現在値を基準にする）の形式で1行ずつ書きます。

どちらの方法も、次回のチェック（15分ごと）から反映されます。
基準株価を変更すると、通知済みの状態は自動でリセットされます。
cron の起動は GitHub 側の混雑で数分〜十数分遅れることがあります。

詳しい手順は **GITHUB_ACTIONS_SETUP.md** に記載しています。

