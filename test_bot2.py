import unittest
from datetime import datetime, timedelta

from bot2 import RegistrationConfig, build_snippet, parse_crns, parse_target


class Bot2Tests(unittest.TestCase):
    def test_parse_crns_deduplicates_and_preserves_order(self):
        self.assertEqual(parse_crns("12345, 67890 12345"), ("12345", "67890"))

    def test_parse_crns_rejects_non_numeric_values(self):
        with self.assertRaisesRegex(ValueError, "yalnızca rakamlardan"):
            parse_crns("12345 abc")

    def test_parse_target_accepts_fractional_seconds(self):
        future = datetime.now() + timedelta(days=2)
        epoch_ms, label = parse_target(future.strftime("%Y-%m-%d"), "10:00:00.0000")
        self.assertGreater(epoch_ms, 0)
        self.assertTrue(label.endswith("10:00:00.000"))

    def test_build_snippet_embeds_registration_configuration(self):
        config = RegistrationConfig(
            ecrn=("12345",),
            scrn=("67890",),
            target_epoch_ms=2_000_000_000_000,
            target_label="2033-05-18 06:33:20.000",
            clock_offset_ms=-7.25,
            clock_uncertainty_ms=12.5,
            send_delay_ms=0,
            dry_run=True,
        )
        snippet = build_snippet(config)
        self.assertNotIn("__CONFIG_JSON__", snippet)
        self.assertIn('\"ecrn\":[\"12345\"]', snippet)
        self.assertIn('\"dryRun\":true', snippet)
        self.assertNotIn("username", snippet.lower())
        self.assertIn("const QUIET_WINDOW_MS = 30000", snippet)
        self.assertIn("const MINIMUM_ARM_LEAD_MS = 40000", snippet)
        self.assertEqual(snippet.count("checkedFetch(TIME_URL"), 1)


if __name__ == "__main__":
    unittest.main()
