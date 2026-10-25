import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from admin.routers.proxy import _looks_login_dead

NOW = datetime(2026, 10, 25, 16, 0, 0)
MARK = "登录态被上游拒绝（HTTP 401），需重新登录：<html>401 Authorization Required</html>"


def account(err_age=None, status="active", msg=MARK, used_age=None):
    return SimpleNamespace(
        status=status, last_err_msg=msg, last_upstream_error="",
        session_dead_fails=1,
        last_err_at=None if err_age is None else NOW - err_age,
        last_used_at=None if used_age is None else NOW - used_age)


class LoginDeadRecheckTests(unittest.TestCase):
    def test_fresh_401_marker_still_excludes(self):
        self.assertTrue(_looks_login_dead(account(timedelta(minutes=5)), NOW))

    def test_stale_401_marker_allows_one_recheck(self):
        # 10-23 deadlock: excluded accounts were never used again, so the marker never cleared.
        self.assertFalse(_looks_login_dead(account(timedelta(days=2), used_age=timedelta(days=3)), NOW))

    def test_recheck_starts_exactly_at_interval(self):
        self.assertTrue(_looks_login_dead(account(timedelta(minutes=29, seconds=59)), NOW))
        self.assertFalse(_looks_login_dead(account(timedelta(minutes=30)), NOW))

    def test_disabled_account_is_never_rechecked(self):
        self.assertTrue(_looks_login_dead(account(timedelta(days=2), status="disabled"), NOW))

    def test_marker_without_timestamp_keeps_excluding(self):
        self.assertTrue(_looks_login_dead(account(None), NOW))


if __name__ == "__main__":
    unittest.main()
