"""`pixel-connector pair` right after a service restart (installer re-run) must not grab the run lock."""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import __main__ as cli
from pixel_connector.approvals import ControlServer


class PairRetry(unittest.TestCase):
    def test_retries_control_api_before_inprocess_fallback(self):
        calls = []

        def ctl(home, method, path):
            calls.append(path)
            if len(calls) < 3:
                raise ConnectionRefusedError
            return 503, {}

        with mock.patch.object(cli, "ctl_request", side_effect=ctl), \
                mock.patch.object(cli, "_pair_inprocess") as inproc, \
                mock.patch.object(cli.time, "sleep") as sleep, \
                mock.patch.object(cli, "_err", return_value=1):
            rc = cli.cmd_pair(SimpleNamespace(home="/tmp/x"))
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)
        inproc.assert_not_called()
        self.assertEqual(rc, 1)                      # 503 → "not connected to the relay"

    def test_falls_back_after_five_failures(self):
        with mock.patch.object(cli, "ctl_request", side_effect=ConnectionRefusedError) as ctl, \
                mock.patch.object(cli, "_pair_inprocess", return_value=0) as inproc, \
                mock.patch.object(cli.time, "sleep"):
            self.assertEqual(cli.cmd_pair(SimpleNamespace(home="/tmp/x")), 0)
        self.assertEqual(ctl.call_count, 5)
        inproc.assert_called_once()


class ReuseAddress(unittest.TestCase):
    def test_reuse_address_only_off_windows(self):
        self.assertEqual(ControlServer.allow_reuse_address, os.name != "nt")


if __name__ == "__main__":
    unittest.main()
