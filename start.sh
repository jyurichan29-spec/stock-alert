#!/usr/bin/env bash
# 株価変動アラート — macOS / Linux 用 起動スクリプト
# 使い方:  ターミナルで  ./start.sh   を実行
set -e

cd "$(dirname "$0")"

echo "=============================================="
echo " 株価変動アラート セットアップ / 起動"
echo "=============================================="
echo

# ---- Python の確認 ----
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "[エラー] Python が見つかりません。"
  echo "  macOS : https://www.python.org/downloads/ からインストール"
  echo "  Ubuntu: sudo apt install python3 python3-venv python3-pip"
  exit 1
fi

VER=$($PY -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "[1/4] Python $VER を検出しました ($PY)"

# ---- 仮想環境の作成 ----
if [ ! -d ".venv" ]; then
  echo "[2/4] 専用の仮想環境 (.venv) を作成しています..."
  $PY -m venv .venv 2>/dev/null || {
    echo "[エラー] venv を作成できませんでした。"
    echo "  Ubuntu/Debian の場合: sudo apt install python3-venv"
    exit 1
  }
else
  echo "[2/4] 仮想環境 (.venv) は作成済みです"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# ---- 依存パッケージ ----
echo "[3/4] 必要なライブラリをインストールしています（初回のみ数分かかります）..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

# ---- 起動 ----
PORT="${PORT:-8000}"
echo "[4/4] 起動します。ブラウザで http://localhost:${PORT} を開いてください。"
echo
echo "  ・終了するときは  Ctrl + C  を押してください"
echo "  ・このウィンドウを閉じると監視も止まります"
echo
sleep 1

# ブラウザを自動で開く（可能な場合）
(sleep 2 && {
  if command -v open >/dev/null 2>&1; then open "http://localhost:${PORT}"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:${PORT}"
  fi
}) >/dev/null 2>&1 &

exec python run.py
