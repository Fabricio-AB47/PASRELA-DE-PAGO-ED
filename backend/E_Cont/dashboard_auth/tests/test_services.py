from unittest import TestCase
from unittest.mock import patch

from django.contrib.auth.hashers import make_password

from dashboard_auth.services import _password_matches, _resolve_staff_role_code, _resolve_staff_role_name


class StaffRoleResolutionTests(TestCase):
    def test_uses_tp_us_as_the_role_code(self):
        self.assertEqual(_resolve_staff_role_code({'tp_us': '10', 'tipousuario': '0'}), 10)

    def test_does_not_fallback_to_legacy_tipousuario(self):
        self.assertIsNone(_resolve_staff_role_code({'tp_us': None, 'tipousuario': '1'}))

    def test_resolves_secretaria_from_tipo_usuario(self):
        with patch(
            'dashboard_auth.services._fetch_one',
            return_value={'detalle_tipo_us': 'SECRETARIA   '},
        ):
            self.assertEqual(_resolve_staff_role_name(10), 'SECRETARIA')

    def test_missing_tp_us_has_no_assigned_role(self):
        self.assertEqual(_resolve_staff_role_name(None), 'SIN ROL ASIGNADO')


class PasswordCompatibilityTests(TestCase):
    def test_accepts_django_password_hashes(self):
        self.assertTrue(_password_matches(make_password('Clave-segura-2026'), 'Clave-segura-2026'))

    def test_rejects_wrong_password_with_legacy_record(self):
        self.assertFalse(_password_matches('clave-antigua', 'otra-clave'))
