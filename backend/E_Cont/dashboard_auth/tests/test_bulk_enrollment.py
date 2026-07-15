from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.bulk_enrollment import (
    STUDENT_ALREADY_REGISTERED_MESSAGE,
    process_bulk_enrollment_excel,
)
from dashboard_auth.student_registration import RegisteredUserExistsError


DEFAULTS = {
    'cod_anio_basica': '13',
    'codigo_materia': 'CURSO-1',
    'codigo_periodo': '2026-1',
    'estado_periodo': 'activo',
    'nombre_materia': 'Curso de prueba',
}


def _row(fila, cedula, nombre='Estudiante Prueba'):
    return {
        'fila': fila,
        'nombre_completo': nombre,
        'cedula': cedula,
        'email': f'estudiante{fila}@example.com',
        'telefono': '0999999999',
        'localidad': 'Quito',
        'direccion': 'Dirección de prueba',
    }


def _processed(payload, fila, **_kwargs):
    return {
        'ok': True,
        'fila': fila,
        'nombre': payload['nombre'],
        'cedula': payload['cedula'],
        'email': payload['email'],
        'message': 'Procesado',
    }


class BulkEnrollmentDuplicateTests(TestCase):
    def setUp(self):
        self.upload = SimpleNamespace(name='estudiantes.xlsx', size=100)

    @patch('dashboard_auth.bulk_enrollment._process_matriculation_payload', side_effect=_processed)
    @patch('dashboard_auth.bulk_enrollment.ensure_user_is_not_registered', return_value={'exists': False})
    @patch('dashboard_auth.bulk_enrollment._read_excel_rows')
    def test_skips_repeated_id_inside_excel_and_continues(self, read_rows, ensure_registered, process):
        read_rows.return_value = [
            _row(2, '1104371859', 'Primer estudiante'),
            _row(3, '1104371859', 'Estudiante repetido'),
            _row(4, '0704024298', 'Tercer estudiante'),
        ]

        result = process_bulk_enrollment_excel(self.upload, DEFAULTS)

        self.assertEqual(result['exitosos'], 2)
        self.assertEqual(result['omitidos'], 1)
        self.assertEqual(result['fallidos'], 0)
        self.assertEqual(result['results'][1]['message'], STUDENT_ALREADY_REGISTERED_MESSAGE)
        self.assertTrue(result['results'][1]['duplicate_in_file'])
        self.assertEqual(ensure_registered.call_count, 2)
        self.assertEqual(process.call_count, 2)

    @patch('dashboard_auth.bulk_enrollment._process_matriculation_payload', side_effect=_processed)
    @patch('dashboard_auth.bulk_enrollment.ensure_user_is_not_registered')
    @patch('dashboard_auth.bulk_enrollment._read_excel_rows')
    def test_skips_database_student_and_processes_remaining_rows(self, read_rows, ensure_registered, process):
        read_rows.return_value = [
            _row(2, '1104371859', 'Ya registrado'),
            _row(3, '0704024298', 'Estudiante nuevo'),
        ]
        ensure_registered.side_effect = [
            RegisteredUserExistsError({'exists': True, 'cedula': '1104371859'}),
            {'exists': False},
        ]

        result = process_bulk_enrollment_excel(self.upload, DEFAULTS)

        self.assertEqual(result['exitosos'], 1)
        self.assertEqual(result['omitidos'], 1)
        self.assertEqual(result['fallidos'], 0)
        self.assertTrue(result['results'][0]['registered'])
        self.assertFalse(result['results'][0]['duplicate_in_file'])
        self.assertEqual(process.call_count, 1)

    @patch('dashboard_auth.bulk_enrollment._process_matriculation_payload', side_effect=_processed)
    @patch('dashboard_auth.bulk_enrollment.ensure_user_is_not_registered', return_value={'exists': False})
    @patch('dashboard_auth.bulk_enrollment._read_excel_rows')
    def test_invalid_id_is_reported_and_next_row_is_processed(self, read_rows, _ensure_registered, process):
        read_rows.return_value = [
            _row(2, '12345', 'Cédula inválida'),
            _row(3, '0704024298', 'Estudiante nuevo'),
        ]

        result = process_bulk_enrollment_excel(self.upload, DEFAULTS)

        self.assertEqual(result['exitosos'], 1)
        self.assertEqual(result['omitidos'], 0)
        self.assertEqual(result['fallidos'], 1)
        self.assertIn('10 dígitos', result['results'][0]['message'])
        self.assertEqual(process.call_count, 1)
