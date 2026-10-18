@echo off
REM 株価変動アラート — Windows 用 起動スクリプト
REM 使い方:  start.bat をダブルクリック
setlocal
cd /d "%~dp0"

echo ==============================================
echo  株価変動アラート セットアップ / 起動
echo ==============================================
echo.

REM ---- Python の確認 ----
set PY=
where py >nul 2>&1 && set PY=py -3
if not defined PY (
  where python >nul 2>&1 && set PY=python
)
if not defined PY (
  echo [エラー] Python が見つかりません。
  echo   https://www.python.org/downloads/ からインストールしてください。
  echo   インストール時に「Add python.exe to PATH」に必ずチェックを入れてください。
  echo.
  pause
  exit /b 1
)

echo [1/4] Python を検出しました

REM ---- 仮想環境の作成 ----
if not exist ".venv" (
  echo [2/4] 専用の仮想環境 ^(.venv^) を作成しています...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [エラー] 仮想環境を作成できませんでした。
    pause
    exit /b 1
  )
) else (
  echo [2/4] 仮想環境 ^(.venv^) は作成済みです
)

call .venv\Scripts\activate.bat

REM ---- 依存パッケージ ----
echo [3/4] 必要なライブラリをインストールしています（初回のみ数分かかります）...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo [エラー] ライブラリのインストールに失敗しました。
  pause
  exit /b 1
)

REM ---- 起動 ----
if not defined PORT set PORT=8000
echo [4/4] 起動します。ブラウザで http://localhost:%PORT% を開いてください。
echo.
echo   ・終了するときは  Ctrl + C  を押してください
echo   ・このウィンドウを閉じると監視も止まります
echo.

start "" "http://localhost:%PORT%"
python run.py

pause
