from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.student_registration import (
    RegisteredUserExistsError,
    ensure_user_is_not_registered,
    lookup_registered_user_by_number,
)


class StudentRegistrationDuplicateTests(TestCase):
    @patch('dashboard_auth.student_registration._find_datos_estud')
    def test_rejects_an_existing_id_regardless_of_course_context(self, find_student):
        find_student.return_value = {'codigo_estud': '28907', 'cedula': '1104371859'}

        with self.assertRaises(RegisteredUserExistsError):
            ensure_user_is_not_registered(
                '1104371859',
                cod_anio_basica='13',
                codigo_materia='CURSO-NUEVO',
                codigo_periodo='2026-2',
            )

    @patch('dashboard_auth.student_registration._find_datos_estud')
    def test_normalizes_punctuation_before_lookup(self, find_student):
        find_student.return_value = None

        result = lookup_registered_user_by_number('110-437-1859')

        find_student.assert_called_once_with('1104371859')
        self.assertFalse(result['exists'])
        self.assertEqual(result['cedula'], '1104371859')
