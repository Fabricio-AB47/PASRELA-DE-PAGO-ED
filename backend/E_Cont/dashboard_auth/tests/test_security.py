from django.test import RequestFactory, SimpleTestCase

from dashboard_auth.security import (
    _financial_request_is_allowed,
    _staff_request_is_allowed,
    _staff_role_name,
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
