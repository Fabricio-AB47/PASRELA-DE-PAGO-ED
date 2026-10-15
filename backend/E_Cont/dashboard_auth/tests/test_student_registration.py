from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.student_registration import (
    RegisteredUserExistsError,
    ensure_user_is_not_registered,
    lookup_registered_user_by_number,
)


class StudentRegistrationDuplicateTests(TestCase):
    @patch('dashboard_auth.student_registration._find_course_enrollment')
    @patch('dashboard_auth.student_registration._find_datos_estud')
    def test_rejects_an_existing_enrollment_in_the_same_course(self, find_student, find_enrollment):
        find_student.return_value = {'codigo_estud': '28907', 'cedula': '1104371859'}
        find_enrollment.return_value = {'corte_estudiante_id': '7'}

        with self.assertRaises(RegisteredUserExistsError):
            ensure_user_is_not_registered(
                '1104371859',
                cod_anio_basica='13',
                codigo_materia='CURSO-NUEVO',
                codigo_periodo='2026-2',
            )

    @patch('dashboard_auth.student_registration._find_course_enrollment', return_value=None)
    @patch('dashboard_auth.student_registration._find_datos_estud')
    def test_allows_existing_identity_when_not_enrolled_in_selected_course(
        self,
        find_student,
        _find_enrollment,
    ):
        find_student.return_value = {'codigo_estud': '1935', 'cedula': '1003249768'}

        result = ensure_user_is_not_registered(
            '1003249768',
            cod_anio_basica='13',
            codigo_materia='340',
            codigo_periodo='1046',
        )

        self.assertFalse(result['exists'])
        self.assertTrue(result['identity_exists'])
        self.assertEqual(result['codigo_estud'], '1935')

    @patch('dashboard_auth.student_registration._find_datos_estud')
    def test_normalizes_punctuation_before_lookup(self, find_student):
        find_student.return_value = None

        result = lookup_registered_user_by_number('110-437-1859')

        find_student.assert_called_once_with('1104371859')
        self.assertFalse(result['exists'])
        self.assertEqual(result['cedula'], '1104371859')
