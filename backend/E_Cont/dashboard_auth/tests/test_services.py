from unittest import TestCase
from unittest.mock import patch

from django.contrib.auth.hashers import make_password

from dashboard_auth.services import (
    _password_matches,
    _resolve_staff_role_code,
    _resolve_staff_role_name,
    RoleSelectionRequired,
    authenticate_user,
)


class DualRoleAuthenticationTests(TestCase):
    @patch('dashboard_auth.services._find_staff')
    @patch('dashboard_auth.services._find_teacher')
    @patch('dashboard_auth.services._find_student')
    def test_auto_requests_choice_only_for_staff_and_teacher_duplicate(
        self, find_student, find_teacher, find_staff
    ):
        find_staff.return_value = object()
        find_teacher.return_value = object()

        with self.assertRaises(RoleSelectionRequired) as context:
            authenticate_user('usuario@intec.edu.ec', 'ClaveExistente', scope='auto')

        self.assertEqual([role['scope'] for role in context.exception.roles], ['staff', 'teacher'])
        find_student.assert_not_called()

    @patch('dashboard_auth.services._find_staff', return_value=None)
    @patch('dashboard_auth.services._find_teacher')
    @patch('dashboard_auth.services._find_student')
    def test_auto_enters_teacher_when_there_is_no_administrative_duplicate(
        self, find_student, find_teacher, _find_staff
    ):
        teacher_session = object()
        find_teacher.return_value = teacher_session

        result = authenticate_user('docente@intec.edu.ec', 'ClaveExistente', scope='auto')

        self.assertIs(result, teacher_session)
        find_student.assert_not_called()

    @patch('dashboard_auth.services._find_staff')
    @patch('dashboard_auth.services._find_teacher')
    @patch('dashboard_auth.services._find_student')
    def test_teacher_enrolled_as_student_can_choose_student_access(
        self, find_student, find_teacher, find_staff
    ):
        student_session = object()
        find_student.return_value = student_session

        result = authenticate_user(
            'docente@intec.edu.ec',
            'ClaveExistente',
            scope='student',
        )

        self.assertIs(result, student_session)
        find_student.assert_called_once_with('docente@intec.edu.ec', 'ClaveExistente')
        find_teacher.assert_not_called()
        find_staff.assert_not_called()


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
