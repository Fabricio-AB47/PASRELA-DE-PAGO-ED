from unittest import TestCase
from unittest.mock import MagicMock, patch

from dashboard_auth.course_cuts import (
    CourseCutError,
    _fetch_course_modules,
    _ensure_four_course_modules,
    _fetch_complement_student_index,
    _normalize_additional_owner_emails,
    _resolve_schedule_module,
    _schedule_database_modality,
    _sync_student_to_complement,
    update_course_cut,
)


class CourseModuleTests(TestCase):
    def test_additional_owners_accept_only_institutional_accounts(self):
        self.assertEqual(
            _normalize_additional_owner_emails(['Coordinador@intec.edu.ec']),
            ['coordinador@intec.edu.ec'],
        )
        with self.assertRaisesRegex(CourseCutError, '@intec.edu.ec'):
            _normalize_additional_owner_emails(['coordinador@gmail.com'])

    def test_translates_online_label_to_complement_database_value(self):
        self.assertEqual(_schedule_database_modality('EN LÍNEA'), 'VIRTUAL')
        self.assertEqual(_schedule_database_modality('VIRTUAL'), 'VIRTUAL')
        self.assertEqual(_schedule_database_modality('PRESENCIAL'), 'PRESENCIAL')

    @patch('dashboard_auth.course_cuts._fetch_all')
    def test_configures_four_modules_across_eight_weeks(self, fetch_all):
        _ensure_four_course_modules(7, {'fecha_inicio_iso': '2026-07-20'})

        self.assertEqual(fetch_all.call_count, 4)
        calls = [item.args[1] for item in fetch_all.call_args_list]
        self.assertEqual([(params[3], params[4]) for params in calls], [(1, 2), (3, 4), (5, 6), (7, 8)])
        self.assertEqual(calls[0][5].isoformat(), '2026-07-20')
        self.assertEqual(calls[-1][6].isoformat(), '2026-09-13')

    @patch('dashboard_auth.course_cuts._fetch_all')
    def test_groups_multiple_teachers_inside_same_module(self, fetch_all):
        fetch_all.return_value = [
            {'ModuloId': 10, 'CorteId': 7, 'NumeroModulo': 1, 'NombreModulo': 'MÓDULO I',
             'TemaModulo': 'Fundamentos', 'FechaFinalizacion': '2026-08-02',
             'ActividadesFinales': 'Evaluación y proyecto final.',
             'EstadoModulo': 'ACTIVO', 'ModuloDocenteId': 1, 'DocenteCorteId': 21, 'RolModulo': 'COORDINADOR'},
            {'ModuloId': 10, 'CorteId': 7, 'NumeroModulo': 1, 'NombreModulo': 'MÓDULO I',
             'EstadoModulo': 'ACTIVO', 'ModuloDocenteId': 2, 'DocenteCorteId': 22, 'RolModulo': 'DOCENTE'},
        ]

        modules = _fetch_course_modules(7)

        self.assertEqual(len(modules), 1)
        self.assertEqual(modules[0]['docente_corte_ids'], ['21', '22'])
        self.assertEqual(modules[0]['tema_modulo'], 'Fundamentos')
        self.assertEqual(modules[0]['fecha_finalizacion'], '2026-08-02')
        self.assertEqual(modules[0]['actividades_finales'], 'Evaluación y proyecto final.')

    @patch('dashboard_auth.course_cuts._fetch_course_modules')
    def test_same_teacher_can_be_selected_in_different_modules(self, fetch_modules):
        fetch_modules.return_value = [
            {'modulo_id': '10', 'docente_corte_ids': ['21']},
            {'modulo_id': '11', 'docente_corte_ids': ['21', '22']},
        ]

        selected = _resolve_schedule_module(
            7,
            {'modulo_id': '11'},
            {'docente_corte_id': '21'},
        )

        self.assertEqual(selected['modulo_id'], '11')

    @patch('dashboard_auth.course_cuts._fetch_course_modules')
    def test_rejects_teacher_not_assigned_to_selected_module(self, fetch_modules):
        fetch_modules.return_value = [{'modulo_id': '10', 'docente_corte_ids': ['21']}]

        with self.assertRaisesRegex(CourseCutError, 'no está asignado'):
            _resolve_schedule_module(7, {'modulo_id': '10'}, {'docente_corte_id': '22'})


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
