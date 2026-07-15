from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.microsoft365 import _get_access_token
from dashboard_auth.payments import _get_graph_access_token


class GraphTokenCacheTests(TestCase):
    @patch('dashboard_auth.microsoft365.urlopen')
    @patch('dashboard_auth.microsoft365.cache.get', return_value='directory-token')
    def test_reuses_directory_token(self, _cache_get, urlopen):
        token = _get_access_token(
            {
                'tenant_id': 'tenant',
                'client_id': 'client',
                'client_secret': 'secret',
                'scope': 'https://graph.microsoft.com/.default',
            }
        )

        self.assertEqual(token, 'directory-token')
        urlopen.assert_not_called()

    @patch('dashboard_auth.payments.urlopen')
    @patch('dashboard_auth.payments.cache.get', return_value='mail-token')
    def test_reuses_mail_token(self, _cache_get, urlopen):
        token = _get_graph_access_token('tenant', 'client', 'secret')

        self.assertEqual(token, 'mail-token')
        urlopen.assert_not_called()
