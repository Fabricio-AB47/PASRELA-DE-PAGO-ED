from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.continuing_education import connection_for_query, ensure_student_course_charge


class ComplementConnectionRoutingTests(TestCase):
    @patch('dashboard_auth.continuing_education.complement_connection')
    def test_routes_qualified_complement_query_to_db1_alias(self, complement_connection):
        marker = object()
        complement_connection.return_value = marker

        selected = connection_for_query(
            'SELECT 1 FROM [INTECEDUCONTINUA].[edu].[CorteEstudiante]'
        )

        self.assertIs(selected, marker)

    @patch('dashboard_auth.continuing_education.complement_connection')
    def test_routes_object_name_passed_as_parameter_to_db1_alias(self, complement_connection):
        marker = object()
        complement_connection.return_value = marker

        selected = connection_for_query(
            'SELECT OBJECT_ID(%s, %s)',
            ['[INTECEDUCONTINUA].[edu].[CorteEstudiante]', 'U'],
        )

        self.assertIs(selected, marker)


class StudentCourseChargeTests(TestCase):
    @patch('dashboard_auth.continuing_education._fetch_one')
    @patch('dashboard_auth.continuing_education.is_complement_available', return_value=True)
    def test_reduces_previous_excel_value_with_an_auditable_discount(self, _available, fetch_one):
        fetch_one.side_effect = [
            {
                'CuentaId': 7,
                'TotalCargo': '550.00',
                'TotalDescuento': '0.00',
            },
            {'MovimientoId': 12},
        ]

        result = ensure_student_course_charge(
            corte_id='1',
            codigo_estud='1954',
            target_value='400.00',
            origin='EXCEL',
            usuario_registro='SISTEMA_AJUSTE_EXCEL',
        )

        movement_params = fetch_one.call_args_list[1].args[1]
        self.assertTrue(result['adjusted'])
        self.assertEqual(result['adjustment_type'], 'DISCOUNT')
        self.assertEqual(result['discount_value'], '150.00')
        self.assertEqual(movement_params[1], 'HABER')
        self.assertEqual(str(movement_params[3]), '150.00')
        self.assertEqual(movement_params[4], 'DESCUENTO')

    @patch('dashboard_auth.continuing_education._fetch_one')
    @patch('dashboard_auth.continuing_education.is_complement_available', return_value=True)
    def test_creates_exact_charge_when_account_has_no_initial_value(self, _available, fetch_one):
        fetch_one.side_effect = [
            {
                'CuentaId': 8,
                'TotalCargo': '0.00',
                'TotalDescuento': '0.00',
            },
            {'MovimientoId': 13},
        ]

        result = ensure_student_course_charge(
            corte_id='1',
            codigo_estud='1955',
            target_value='400.00',
            origin='EXCEL',
        )

        movement_params = fetch_one.call_args_list[1].args[1]
        self.assertEqual(result['adjustment_type'], 'CHARGE')
        self.assertEqual(result['added_value'], '400.00')
        self.assertEqual(movement_params[1], 'DEBE')
        self.assertEqual(str(movement_params[3]), '400.00')
        self.assertEqual(movement_params[4], 'AJUSTE_VALOR_CURSO')
