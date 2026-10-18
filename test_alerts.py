"""閾値判定エンジンの単体テスト.

実行:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stockalert import db  # noqa: E402
from stockalert.alerts import (  # noqa: E402
    EVENT_ESCALATE, EVENT_NONE, EVENT_REPEAT, EVENT_RESET,
    change_pct, decide, direction_of, highest_level, is_allowed,
    parse_thresholds,
)
from stockalert.quotes import normalize_symbol  # noqa: E402

T = [15, 20, 25, 30]


class TestParseThresholds(unittest.TestCase):
    def test_string(self):
        self.assertEqual(parse_thresholds("15,20,25,30"), T)

    def test_fullwidth_comma_and_spaces(self):
        self.assertEqual(parse_thresholds("15、20, 25 , 30"), T)

    def test_dedup_and_sort(self):
        self.assertEqual(parse_thresholds("30,15,15,20"), [15, 20, 30])

    def test_invalid(self):
        self.assertEqual(parse_thresholds("abc,,0,-5"), [5])


class TestLevels(unittest.TestCase):
    def test_highest_level(self):
        self.assertEqual(highest_level(0.0, T), 0)
        self.assertEqual(highest_level(14.9, T), 0)
        self.assertEqual(highest_level(15.0, T), 15)
        self.assertEqual(highest_level(-19.9, T), 15)
        self.assertEqual(highest_level(-30.0, T), 30)
        self.assertEqual(highest_level(-99.0, T), 30)

    def test_direction(self):
        self.assertEqual(direction_of(3.0), "up")
        self.assertEqual(direction_of(-3.0), "down")
        self.assertEqual(direction_of(0.0), "flat")

    def test_is_allowed(self):
        self.assertTrue(is_allowed("up", "both"))
        self.assertTrue(is_allowed("down", "down"))
        self.assertFalse(is_allowed("up", "down"))
        self.assertFalse(is_allowed("flat", "both"))

    def test_change_pct(self):
        self.assertAlmostEqual(change_pct(1150, 1000), 15.0)
        self.assertAlmostEqual(change_pct(800, 1000), -20.0)


class TestDecide(unittest.TestCase):
    def test_first_breach_notifies(self):
        d = decide(16.0, T, prev_level=0, last_notified_at=0, now=1000)
        self.assertEqual(d.event, EVENT_ESCALATE)
        self.assertEqual(d.new_level, 15)
        self.assertEqual(d.direction, "up")
        self.assertTrue(d.should_notify)

    def test_no_notify_below_threshold(self):
        d = decide(14.9, T, prev_level=0, last_notified_at=0, now=1000)
        self.assertEqual(d.event, EVENT_NONE)
        self.assertFalse(d.should_notify)

    def test_escalation_always_notifies(self):
        d = decide(31.0, T, prev_level=20, last_notified_at=999, now=1000,
                   cooldown_sec=3600)
        self.assertEqual(d.event, EVENT_ESCALATE)
        self.assertEqual(d.new_level, 30)

    def test_same_level_within_cooldown_is_silent(self):
        d = decide(21.0, T, prev_level=20, last_notified_at=900, now=1000,
                   cooldown_sec=3600)
        self.assertEqual(d.event, EVENT_NONE)
        self.assertEqual(d.new_level, 20)

    def test_same_level_after_cooldown_repeats(self):
        d = decide(21.0, T, prev_level=20, last_notified_at=1000, now=1000 + 3601,
                   cooldown_sec=3600)
        self.assertEqual(d.event, EVENT_REPEAT)
        self.assertTrue(d.should_notify)

    def test_step_down_inside_band_is_silent(self):
        d = decide(16.0, T, prev_level=20, last_notified_at=1000, now=2000)
        self.assertEqual(d.event, EVENT_NONE)
        self.assertEqual(d.new_level, 15)

    def test_reset_notifies(self):
        d = decide(3.0, T, prev_level=15, last_notified_at=1000, now=2000)
        self.assertEqual(d.event, EVENT_RESET)
        self.assertEqual(d.new_level, 0)

    def test_reset_can_be_disabled(self):
        d = decide(3.0, T, prev_level=15, last_notified_at=1000, now=2000,
                   notify_on_reset=False)
        self.assertEqual(d.event, EVENT_NONE)

    def test_downward_uses_abs(self):
        d = decide(-26.0, T, prev_level=20, last_notified_at=0, now=1000)
        self.assertEqual(d.event, EVENT_ESCALATE)
        self.assertEqual(d.new_level, 25)
        self.assertEqual(d.direction, "down")


class TestSymbols(unittest.TestCase):
    def test_jp_4digit(self):
        self.assertEqual(normalize_symbol("7203"), "7203.T")
        self.assertEqual(normalize_symbol("7203.jp"), "7203.T")
        self.assertEqual(normalize_symbol("1301"), "1301.T")

    def test_us(self):
        self.assertEqual(normalize_symbol("aapl"), "AAPL")
        self.assertEqual(normalize_symbol("BRK-B"), "BRK-B")

    def test_explicit_suffix(self):
        self.assertEqual(normalize_symbol("6758.T"), "6758.T")
        self.assertEqual(normalize_symbol("0700.HK"), "0700.HK")

    def test_bloomberg_style_suffix(self):
        self.assertEqual(normalize_symbol("7203T"), "7203.T")
        self.assertEqual(normalize_symbol("7203 JT"), "7203.T")
        self.assertEqual(normalize_symbol("7203JP"), "7203.T")
        self.assertEqual(normalize_symbol("7203 JP"), "7203.T")

    def test_tse_alphanumeric_code(self):
        self.assertEqual(normalize_symbol("160A"), "160A.T")

    def test_fullwidth_space_and_lowercase(self):
        self.assertEqual(normalize_symbol("　7203　"), "7203.T")
        self.assertEqual(normalize_symbol("aapl"), "AAPL")

    def test_other_markets_untouched(self):
        self.assertEqual(normalize_symbol("005930.KS"), "005930.KS")
        self.assertEqual(normalize_symbol("BRK-B"), "BRK-B")

    def test_empty(self):
        with self.assertRaises(Exception):
            normalize_symbol("  ")


class TestDatabaseAndCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        db.init_db(self.db_path)

    def test_add_and_list(self):
        db.add_watch("7203.T", 3000.0, name="トヨタ", db_path=self.db_path)
        rows = db.list_watchlist(db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "7203.T")
        self.assertEqual(rows[0]["base_price"], 3000.0)

    def test_upsert_updates_base_price(self):
        db.add_watch("7203.T", 3000.0, db_path=self.db_path)
        db.add_watch("7203.T", 3100.0, db_path=self.db_path)
        rows = db.list_watchlist(db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["base_price"], 3100.0)

    def test_level_persistence(self):
        db.add_watch("7203.T", 3000.0, db_path=self.db_path)
        db.update_watch("7203.T", db_path=self.db_path, up_level=20,
                        last_price=3600.0, last_change_pct=20.0)
        row = db.get_watch("7203.T", db_path=self.db_path)
        self.assertEqual(row["up_level"], 20)
        db.reset_levels("7203.T", db_path=self.db_path)
        self.assertEqual(db.get_watch("7203.T", db_path=self.db_path)["up_level"], 0)

    def test_alert_log_and_settings(self):
        db.log_alert("7203.T", 3000, 3600, 20.0, 20, "up", EVENT_ESCALATE,
                     title="t", message="m", db_path=self.db_path)
        alerts = db.list_alerts(db_path=self.db_path)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["level"], 20)

        db.save_settings({"poll_interval_minutes": "5"}, db_path=self.db_path)
        self.assertEqual(db.get_settings(self.db_path)["poll_interval_minutes"], "5")

    def test_run_check_dry_run(self):
        """ネットワークを使う統合テスト（失敗時はスキップ）。"""
        from stockalert.checker import run_check
        db.add_watch("AAPL", 1.0, name="Apple", db_path=self.db_path)
        try:
            result = run_check(db_path=self.db_path, force=True, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"ネットワーク取得に失敗: {exc}")
        self.assertIn("summary", result)
        self.assertEqual(result["checked"], 1)


class TestCheckerDirections(unittest.TestCase):
    """run_check が上昇・下降を正しく割り当てるかの回帰テスト.

    過去に方向判定の条件が反転し、「上昇時に判定されない」「下降時に
    『上昇』と表示される」不具合があったため、それを固定化する。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "check.db")
        db.init_db(self.db_path)

    def _run(self, price: float, base: float, direction: str = "both") -> dict:
        import stockalert.checker as checker
        from stockalert.quotes import Quote

        db.add_watch("TEST.T", base, direction=direction, db_path=self.db_path)
        original = checker.fetch_quotes
        checker.fetch_quotes = lambda symbols, **kw: (
            {"TEST.T": Quote(symbol="TEST.T", price=price, currency="JPY")}, {}
        )
        try:
            return checker.run_check(db_path=self.db_path, force=True, dry_run=True)
        finally:
            checker.fetch_quotes = original

    def test_up_move_is_detected_and_labelled_up(self):
        rec = self._run(price=125.0, base=100.0)["results"][0]
        self.assertEqual(rec["level"], 25)
        self.assertEqual(rec["event"], EVENT_ESCALATE)
        self.assertIn("上昇", rec["title"])

    def test_down_move_is_detected_and_labelled_down(self):
        rec = self._run(price=80.0, base=100.0)["results"][0]
        self.assertEqual(rec["level"], 20)
        self.assertEqual(rec["event"], EVENT_ESCALATE)
        self.assertIn("下降", rec["title"])

    def test_inside_band_does_not_fire(self):
        result = self._run(price=110.0, base=100.0)
        self.assertEqual(result["fired"], 0)
        self.assertEqual(result["results"][0]["event"], EVENT_NONE)

    def test_up_only_setting_ignores_downward_move(self):
        result = self._run(price=70.0, base=100.0, direction="up")
        self.assertEqual(result["fired"], 0)

    def test_escalation_after_breach(self):
        """15% 到達 → 20% 到達でエスカレーション通知、30% 到達でさらに通知。"""
        import stockalert.checker as checker
        from stockalert.quotes import Quote

        db.add_watch("TEST.T", 100.0, db_path=self.db_path)

        def run(price):
            original = checker.fetch_quotes
            checker.fetch_quotes = lambda symbols, **kw: (
                {"TEST.T": Quote(symbol="TEST.T", price=price, currency="JPY")}, {}
            )
            try:
                return checker.run_check(db_path=self.db_path, force=True,
                                         dry_run=True, now=1_700_000_000)
            finally:
                checker.fetch_quotes = original

        self.assertEqual(run(116.0)["results"][0]["level"], 15)
        self.assertEqual(run(116.0)["results"][0]["event"], EVENT_NONE)   # 同一レベル
        self.assertEqual(run(121.0)["results"][0]["level"], 20)
        self.assertEqual(run(121.0)["results"][0]["event"], EVENT_NONE)
        self.assertEqual(run(131.0)["results"][0]["level"], 30)
        # 基準値圏内へ復帰
        reset = run(101.0)["results"][0]
        self.assertEqual(reset["event"], EVENT_RESET)
        self.assertEqual(reset["level"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
