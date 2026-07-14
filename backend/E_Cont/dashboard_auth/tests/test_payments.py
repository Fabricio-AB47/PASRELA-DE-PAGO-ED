from base64 import b64encode
from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.payments import (
    EXCEL_ENROLLMENT_NET_AMOUNT,
    _generated_payment_link_metrics,
    _ensure_all_digital_payment_receipt,
    _list_generated_payment_links,
    _serialize_registered_user_payment,
    _sync_excel_course_charge_adjustments,
    _store_continuing_education_voucher,
    PaymentGatewayError,
    admin_cancel_payment,
    admin_get_payment_info,
    register_continuing_education_payment,
    reconcile_pending_all_digital_payments,
)
from dashboard_auth.payment_receipt import build_all_digital_payment_receipt


class AdminPaymentPayloadTests(TestCase):
    @patch('dashboard_auth.payments._get_payment_provider_transaction')
    def test_accepts_unaccented_transaction_id_for_information(self, provider_call):
        provider_call.return_value = {'ok': True}

        admin_get_payment_info({'transaccion_id': 'TX-123'})

        provider_call.assert_called_once_with('TX-123')


class AutomaticPaymentReconciliationTests(TestCase):
    @patch('dashboard_auth.payments._list_generated_payment_links', return_value=[])
    @patch('dashboard_auth.payments._update_inscription_provider_status')
    @patch('dashboard_auth.payments._get_payment_provider_transaction')
    @patch('dashboard_auth.payments._pending_all_digital_candidates')
    def test_updates_and_counts_confirmed_inscription_payment(
        self, candidates, provider_call, update_status, _confirmed_links
    ):
        candidates.return_value = [
            {
                'request_id': '8', 'transaction_id': '126046', 'origin': 'INSCRIPCION',
                'cedula': '1104371859', 'expected_amount': '500.00',
            }
        ]
        provider_call.return_value = {
            'data': {
                'id': 126046,
                'monto': '500.00',
                'cliente': {'identificacion': '1104371859'},
                'moneda': {'codigo': 'USD'},
                'estado': {'nombre': 'Pagada'},
            }
        }

        result = reconcile_pending_all_digital_payments(force=True)

        self.assertEqual(result['processed'], 1)
        self.assertEqual(result['paid'], 1)
        self.assertEqual(result['errors'], 0)
        update_status.assert_called_once_with('8', provider_call.return_value)

    @patch('dashboard_auth.payments._mark_payment_request_cancelled')
    @patch('dashboard_auth.payments._delete_payment_provider_transaction')
    def test_accepts_unaccented_transaction_id_for_cancelation(self, provider_call, mark_cancelled):
        provider_call.return_value = {'ok': True}

        admin_cancel_payment({'transaccion_id': 'TX-123', 'motivo': 'Prueba'})

        provider_call.assert_called_once_with('TX-123', 'Prueba')
        mark_cancelled.assert_called_once_with('TX-123')


class GeneratedPaymentLinkTests(TestCase):
    @patch('dashboard_auth.payments._fetch_payment_rows')
    def test_marks_link_as_paid_when_it_has_a_registered_payment(self, fetch_rows):
        fetch_rows.return_value = [
            {
                'inscription_payment_id': '2',
                'nombre': 'Estudiante',
                'amount': '500.00',
                'registered_value': '500.00',
                'provider_status': 'Generada',
                'is_paid': 1,
            }
        ]

        result = _list_generated_payment_links('', 'all')

        self.assertTrue(result[0]['is_paid'])
        self.assertEqual(result[0]['display_status'], 'PAGO CONFIRMADO')
        self.assertIn("'generada'", fetch_rows.call_args.args[0])

    @patch('dashboard_auth.payments._fetch_payment_rows')
    def test_reports_generated_and_confirmed_link_totals(self, fetch_rows):
        fetch_rows.return_value = [
            {
                'generated_links': 2,
                'paid_links': 1,
                'generated_pending_links': 1,
                'generated_value': '980.00',
                'paid_value': '500.00',
            }
        ]

        self.assertEqual(
            _generated_payment_link_metrics(),
            {
                'generated_links': 2,
                'paid_links': 1,
                'generated_pending_links': 1,
                'generated_value': '980.00',
                'paid_value': '500.00',
            },
        )

    @patch('dashboard_auth.payments.upload_continuing_education_voucher')
    def test_does_not_duplicate_an_existing_all_digital_receipt(self, upload_voucher):
        payment = {
            'is_paid': True,
            'receipt_web_url': 'https://example.test/existing.pdf',
        }

        _ensure_all_digital_payment_receipt(payment)

        upload_voucher.assert_not_called()
        self.assertEqual(payment['receipt_status'], 'GUARDADO')

    def test_builds_a_pdf_only_from_confirmed_payment_data(self):
        document = build_all_digital_payment_receipt({
            'provider_transaction_id': '126046',
            'inscription_payment_id': '2',
            'nombre': 'Juan Carlos Cabrera Morocho',
            'cedula': '1104371859',
            'codigo_estud': '1954',
            'matricula': '28591',
            'course_name': 'Diplomado de protección de datos',
            'cut_name': 'Primera corte',
            'payment_record_number': '1',
            'paid_at': '2026-06-16',
            'registered_value': '500.00',
        })

        self.assertTrue(document.startswith(b'%PDF'))
        self.assertGreater(len(document), 1000)


class ContinuingEducationManualPaymentTests(TestCase):
    @patch('dashboard_auth.payments._ensure_continuing_education_payments_available')
    def test_requires_voucher_for_voucher_payment(self, _ensure_available):
        with self.assertRaisesRegex(PaymentGatewayError, 'Debes adjuntar el voucher'):
            register_continuing_education_payment(
                {
                    'codigo_estud': '1954',
                    'corte_id': '1',
                    'estudiante_corte_id': '7',
                    'valor': '50.00',
                    'forma_pago': 'VOUCHER',
                },
                user_login='financiero',
            )

    @patch('dashboard_auth.payments.upload_continuing_education_voucher')
    def test_stores_voucher_using_course_cut_and_student_hierarchy(self, upload_voucher):
        upload_voucher.return_value = {
            'file_name': '2026-07-13_COMPROBANTE_ABC_6ca13d52.pdf',
            'relative_path': (
                'EDUCACION_CONTINUA/Inglés A1/Corte Julio/'
                '1954 - Christian Castro/2026-07-13_COMPROBANTE_ABC_6ca13d52.pdf'
            ),
            'web_url': 'https://example.test/comprobante',
        }

        result = _store_continuing_education_voucher(
            {
                'voucher_base64': b64encode(b'%PDF-1.7\ncomprobante\n%%EOF').decode('ascii'),
                'voucher_name': 'voucher.pdf',
                'fecha_deposito': '2026-07-13',
                'numero_comprobante': 'ABC',
            },
            codigo_estud='1954',
            course_name='Inglés A1',
            cut_name='Corte Julio',
            student_name='Christian Castro',
        )

        upload_voucher.assert_called_once()
        self.assertEqual(
            result['folder_path'],
            'EDUCACION_CONTINUA/Inglés A1/Corte Julio/1954 - Christian Castro',
        )
        self.assertEqual(result['relative_path'], 'https://example.test/comprobante')


class ExcelEnrollmentValueTests(TestCase):
    def test_excel_enrollment_net_amount_is_four_hundred(self):
        self.assertEqual(str(EXCEL_ENROLLMENT_NET_AMOUNT), '400.00')

    @patch('dashboard_auth.payments.ensure_student_course_charge')
    @patch('dashboard_auth.payments._fetch_payment_rows')
    def test_reconciliation_uses_excel_net_amount(self, fetch_rows, ensure_charge):
        fetch_rows.return_value = [{'corte_id': '1', 'codigo_estud': '1954'}]
        ensure_charge.return_value = {'adjusted': True}

        result = _sync_excel_course_charge_adjustments()

        self.assertEqual(result['adjusted'], 1)
        self.assertEqual(ensure_charge.call_args.kwargs['target_value'], EXCEL_ENROLLMENT_NET_AMOUNT)

    def test_excel_student_is_serialized_with_four_hundred_net_value(self):
        result = _serialize_registered_user_payment(
            {
                'codigo_estud': '1954',
                'is_excel_enrollment': 1,
                'total_value': '550.00',
                'registered_value': '0.00',
                'discount_value': '150.00',
                'excel_net_adjustment': '150.00',
            }
        )

        self.assertEqual(result['total_value'], '400.00')
        self.assertEqual(result['discount_value'], '0.00')
        self.assertEqual(result['pending_balance'], '400.00')
        self.assertEqual(result['enrollment_origin'], 'EXCEL')
