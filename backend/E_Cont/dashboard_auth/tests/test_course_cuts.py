from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.course_cuts import _sync_student_to_complement


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
