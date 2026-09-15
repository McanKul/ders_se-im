import json
import unittest
from datetime import datetime, timedelta

from bot2 import parse_crns, parse_target
from browser_automation import (
    BrowserRegistrationConfig,
    LoginCredentials,
    build_login_script,
    build_scheduler,
)


class Bot2Tests(unittest.TestCase):
    def test_parse_crns_deduplicates_and_preserves_order(self):
        self.assertEqual(parse_crns("12345, 67890 12345"), ("12345", "67890"))

    def test_parse_crns_rejects_non_numeric_values(self):
        with self.assertRaisesRegex(ValueError, "yalnızca rakamlardan"):
            parse_crns("12345 abc")

    def test_parse_target_accepts_four_fractional_digits(self):
        future = datetime.now() + timedelta(days=2)
        epoch_ms, label = parse_target(future.strftime("%Y-%m-%d"), "10:00:00.0000")
        self.assertGreater(epoch_ms, 0)
        self.assertTrue(label.endswith("10:00:00.000"))

    def test_scheduler_keeps_add_and_drop_mapping_separate(self):
        config = BrowserRegistrationConfig(
            add_crns=("11111", "22222"),
            drop_crns=("99999",),
            target_epoch_ms=2_000_000_000_000,
            target_label="2033-05-18 06:33:20.000",
            clock_offset_ms=-7.25,
            clock_uncertainty_ms=12.5,
            send_delay_ms=0,
            dry_run=False,
        )
        scheduler = build_scheduler(config, "Bearer fake-token")
        raw_config = scheduler.split("const CONFIG = ", 1)[1].split(";", 1)[0]
        embedded = json.loads(raw_config)
        self.assertEqual(embedded["addCrns"], ["11111", "22222"])
        self.assertEqual(embedded["dropCrns"], ["99999"])
        self.assertIn("{ECRN: CONFIG.addCrns, SCRN: CONFIG.dropCrns}", scheduler)
        self.assertNotIn("{ECRN: CONFIG.dropCrns, SCRN: CONFIG.addCrns}", scheduler)

    def test_scheduler_has_no_preflight_or_time_check_request(self):
        config = BrowserRegistrationConfig(
            add_crns=("11111",),
            drop_crns=(),
            target_epoch_ms=2_000_000_000_000,
            target_label="2033-05-18 06:33:20.000",
            clock_offset_ms=0,
            clock_uncertainty_ms=10,
            send_delay_ms=0,
            dry_run=True,
        )
        scheduler = build_scheduler(config, "fake-token")
        self.assertEqual(scheduler.count("nativeFetch("), 1)
        self.assertNotIn("KayitZamaniKontrolu", scheduler)

    def test_login_credentials_are_json_escaped(self):
        script = build_login_script(LoginCredentials('user"name', "p\\ass"))
        self.assertIn('user\\"name', script)
        self.assertIn('p\\\\ass', script)
        self.assertNotIn("__CREDENTIALS__", script)


if __name__ == "__main__":
    unittest.main()
