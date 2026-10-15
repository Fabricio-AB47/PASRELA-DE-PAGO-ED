from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from dashboard_auth.security import (
    _financial_request_is_allowed,
    _staff_request_is_allowed,
    _staff_role_name,
    _student_session_has_active_access,
)


class FinancialDashboardAccessTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_normalizes_financial_role_name(self):
        self.assertEqual(_staff_role_name({'role': {'name': ' financiero '}}), 'FINANCIERO')

    def test_allows_financial_payment_and_enrollment_reads(self):
        self.assertTrue(_financial_request_is_allowed(self.factory.get('/api/auth/admin/payments/')))
        self.assertTrue(_financial_request_is_allowed(self.factory.get('/api/auth/admin/enrolled-students/')))
        self.assertTrue(_financial_request_is_allowed(self.factory.get('/api/auth/admin/course-cuts/')))

    def test_blocks_financial_access_to_other_admin_modules(self):
        self.assertFalse(_financial_request_is_allowed(self.factory.get('/api/auth/admin/academic-catalogs/')))
        self.assertFalse(_financial_request_is_allowed(self.factory.post('/api/auth/admin/course-cuts/create/')))

    def test_denies_unknown_staff_roles_by_default(self):
        request = self.factory.post('/api/auth/admin/payment-cancel/')
        self.assertFalse(_staff_request_is_allowed(request, 'INVITADO_SOP'))

    def test_only_full_administrator_bypasses_route_allowlists(self):
        request = self.factory.post('/api/auth/admin/payment-cancel/')
        self.assertTrue(_staff_request_is_allowed(request, 'ADMINISTRADOR'))
        self.assertFalse(_staff_request_is_allowed(request, 'ACADEMICO'))

    def test_student_credentials_are_restricted_to_full_administrator(self):
        request = self.factory.post('/api/auth/admin/student-updates/credentials/')
        self.assertTrue(_staff_request_is_allowed(request, 'ADMINISTRADOR'))
        self.assertFalse(_staff_request_is_allowed(request, 'ACADEMICO'))
        self.assertFalse(_staff_request_is_allowed(request, 'SECRETARIA'))
        self.assertFalse(_staff_request_is_allowed(request, 'FINANCIERO'))


class StudentSessionAccessTests(SimpleTestCase):
    @patch('dashboard_auth.security.connection')
    def test_rejects_an_inactive_student_session(self, database_connection):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        database_connection.cursor.return_value.__enter__.return_value = cursor

        allowed = _student_session_has_active_access({
            'login': 'estudiante@intec.edu.ec',
            'email': 'estudiante@intec.edu.ec',
        })

        self.assertFalse(allowed)

    @patch('dashboard_auth.security.connection')
    def test_accepts_an_active_student_session(self, database_connection):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        database_connection.cursor.return_value.__enter__.return_value = cursor

        allowed = _student_session_has_active_access({
            'login': 'estudiante@intec.edu.ec',
            'email': 'estudiante@intec.edu.ec',
        })

        self.assertTrue(allowed)
        query, params = cursor.execute.call_args.args
        # django-mssql formats % placeholders before sending the query. Literal
        # LIKE wildcards must therefore remain escaped as %% in the SQL source.
        query % tuple('?' * len(params))
        self.assertIn("LIKE 'activo%%'", query)
        self.assertIn("LIKE 'e continua%%'", query)

