from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.course_cuts import (
    _fetch_complement_student_index,
    _sync_student_to_complement,
)


class ComplementStudentIndexTests(TestCase):
    @patch('dashboard_auth.course_cuts._fetch_all')
    @patch('dashboard_auth.course_cuts.complement_version', return_value='v5')
    def test_does_not_cross_query_primary_dbo_from_complement_server(self, _version, fetch_all):
        fetch_all.return_value = [
            {
                'CorteEstudianteId': '24',
                'EstudianteCorteId': '4',
                'CodigoEstud': '2004',
            }
        ]

        result = _fetch_complement_student_index(1)

        query = fetch_all.call_args.args[0]
        self.assertNotIn('dbo.CORTE_CURSO_ESTUDIANTE', query)
        self.assertEqual(result['24']['EstudianteCorteId'], '4')
        self.assertEqual(result['codigo:2004']['CorteEstudianteId'], '24')


class ComplementCourseChargeTests(TestCase):
    @patch('dashboard_auth.course_cuts.sync_student_enrollment_to_complement')
    def test_explicit_excel_value_does_not_create_default_charge_first(self, sync_enrollment):
        sync_enrollment.return_value = {'synced': True}

        _sync_student_to_complement(
            corte_id='1',
            codigo_estud='1954',
            usuario_registro='SISTEMA',
            valor_total_curso='400.00',
            origen_matricula='EXCEL',
        )

        self.assertFalse(sync_enrollment.call_args.kwargs['registrar_cargo_inicial'])
        self.assertEqual(sync_enrollment.call_args.kwargs['valor_total_curso'], '400.00')

    @patch('dashboard_auth.course_cuts.sync_student_enrollment_to_complement')
    def test_enrollment_without_explicit_value_keeps_default_charge(self, sync_enrollment):
        sync_enrollment.return_value = {'synced': True}

        _sync_student_to_complement(
            corte_id='1',
            codigo_estud='1954',
            usuario_registro='SISTEMA',
        )

        self.assertTrue(sync_enrollment.call_args.kwargs['registrar_cargo_inicial'])
