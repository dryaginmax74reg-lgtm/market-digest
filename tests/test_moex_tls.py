import ssl
import unittest
import urllib.request
from datetime import date, datetime
from unittest.mock import patch

import common
import digest


class MoexTLSRegression(unittest.TestCase):
    def test_moex_uses_verified_context_with_official_root(self):
        handler = common._MoexHTTPSHandler()
        with patch.object(handler, "do_open", return_value="ok") as opened:
            self.assertEqual(handler.https_open(urllib.request.Request(
                "https://iss.moex.com/iss/securities.json")), "ok")
        context = opened.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertTrue(any(any(value == "Russian Trusted Root CA" for _, value in group)
                            for cert in context.get_ca_certs() for group in cert["subject"]))

    def test_other_hosts_and_redirect_destinations_use_system_trust(self):
        for host in ("api.telegram.org", "iss.moex.com.example.org", "example.org"):
            with self.subTest(host=host), patch.object(
                    urllib.request.HTTPSHandler, "https_open", return_value="system") as normal:
                self.assertEqual(common._MoexHTTPSHandler().https_open(
                    urllib.request.Request("https://" + host + "/")), "system")
                normal.assert_called_once()

    def test_missing_data_and_old_reference_are_visible(self):
        today = date(2026, 11, 25)
        d = digest.demo_data()
        d["bmeta"]["updated"] = "2026-11-23 16:28"
        d["zcyc"] = None
        d["IMOEX"] = None
        warnings = digest.data_warnings(d, today)
        self.assertEqual(len(warnings), 2)
        now = datetime(2026, 11, 25, 7, 40, tzinfo=common.MSK)
        cap = digest.caption(d, now)
        self.assertIn("Данные неполные", cap)
        self.assertIn("2026-11-23", cap)
        self.assertLessEqual(len(digest._plain(cap)), 1000)
        with patch.object(digest.events, "daily_blocks", return_value=[]):
            public = digest.public_sections(d, now)
        self.assertEqual(public["data_warnings"], warnings)
        self.assertIn("Данные неполные", public["details"][0])

    def test_previous_trading_day_reference_is_accepted_on_monday(self):
        d = digest.demo_data()
        d["bmeta"]["updated"] = "2026-11-20 16:28"
        self.assertEqual(digest.data_warnings(d, date(2026, 11, 23)), [])


if __name__ == "__main__":
    unittest.main()
