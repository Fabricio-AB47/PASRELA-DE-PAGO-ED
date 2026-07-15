from unittest import TestCase

from dashboard_auth.notifications import _fetch_first_result_row


class _MultiResultCursor:
    def __init__(self):
        self.description = None

    def nextset(self):
        self.description = [('created',)]
        return True

    def fetchone(self):
        return (1,)


class NotificationSqlResultTests(TestCase):
    def test_skips_rowcount_result_before_reading_insert_output(self):
        self.assertEqual(_fetch_first_result_row(_MultiResultCursor()), (1,))
