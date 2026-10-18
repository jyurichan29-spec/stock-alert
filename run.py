#!/usr/bin/env python3
"""株価変動アラート — Web 画面 + 15分間隔スケジューラをまとめて起動する。"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stockalert import db  # noqa: E402
from stockalert.config import as_bool  # noqa: E402
from stockalert.scheduler import start_scheduler  # noqa: E402
from stockalert.web import create_app  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    app = create_app()

    if as_bool(os.environ.get("STOCKALERT_ENABLE_SCHEDULER", "true"), True):
        start_scheduler()
    else:
        logging.info("スケジューラは無効化されています（Web 画面のみ）")

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    logging.info("管理画面: http://%s:%d", "localhost" if host == "0.0.0.0" else host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
