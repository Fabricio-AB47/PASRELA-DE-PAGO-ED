from unittest import TestCase
from unittest.mock import MagicMock, patch

from dashboard_auth.course_cuts import (
    CourseCutError,
    _fetch_complement_student_index,
    _sync_student_to_complement,
    update_course_cut,
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


class CourseCutUpdateTests(TestCase):
    def setUp(self):
        self.current = {
            'corte_id': '7',
            'numero_corte': '1',
            'nombre_corte': 'Cohorte inicial',
            'fecha_inicio_iso': '2026-07-20',
            'fecha_fin_iso': '2026-08-20',
            'cupo_esperado': '30',
            'horas': '40',
            'observacion': '',
            'total_estudiantes': 12,
            'tipo_oferta': 'EDUCONTINUA',
            'cod_curso': '9',
        }

    @patch('dashboard_auth.course_cuts._sync_cut_to_complement')
    @patch('dashboard_auth.course_cuts._fetch_cut_by_id')
    @patch('dashboard_auth.course_cuts.connection')
    @patch('dashboard_auth.course_cuts._ensure_course_cut_schema')
    def test_updates_only_safe_cohort_metadata(
        self,
        _ensure_schema,
        db_connection,
        fetch_cut,
        sync_complement,
    ):
        updated = {**self.current, 'nombre_corte': 'Cohorte corregida'}
        fetch_cut.side_effect = [self.current, updated]
        cursor = MagicMock()
        cursor.rowcount = 1
        db_connection.cursor.return_value.__enter__.return_value = cursor
        sync_complement.return_value = {'synced': True}

        result = update_course_cut(
            {
                'corte_id': 7,
                'numero_corte': 2,
                'nombre_corte': 'Cohorte corregida',
                'fecha_inicio': '2026-07-21',
                'fecha_fin': '2026-08-21',
                'cupo_esperado': 35,
                'horas': 42,
                'observacion': 'Corrección administrativa.',
                'codigo_materia': '999',
            },
            user_login='ADMIN',
        )

        query = cursor.execute.call_args.args[0]
        self.assertIn('UPDATE dbo.CORTE_CURSO', query)
        self.assertNotIn('CodigoMateria =', query)
        self.assertNotIn('CodigoPeriodo =', query)
        self.assertEqual(result['nombre_corte'], 'Cohorte corregida')
        sync_complement.assert_called_once()

    @patch('dashboard_auth.course_cuts._fetch_cut_by_id')
    @patch('dashboard_auth.course_cuts._ensure_course_cut_schema')
    def test_rejects_capacity_below_registered_students(self, _ensure_schema, fetch_cut):
        fetch_cut.return_value = self.current

        with self.assertRaisesRegex(CourseCutError, '12 estudiantes'):
            update_course_cut(
                {
                    'corte_id': 7,
                    'cupo_esperado': 10,
                }
            )

    @patch('dashboard_auth.course_cuts._fetch_cut_by_id')
    @patch('dashboard_auth.course_cuts._ensure_course_cut_schema')
    def test_rejects_end_date_before_start_date(self, _ensure_schema, fetch_cut):
        fetch_cut.return_value = self.current

        with self.assertRaisesRegex(CourseCutError, 'anterior al inicio'):
            update_course_cut(
                {
                    'corte_id': 7,
                    'fecha_inicio': '2026-08-20',
                    'fecha_fin': '2026-08-19',
                }
            )
