from base64 import b64encode
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.payments import (
    CONTINUING_EDUCATION_DISCOUNT_TYPES,
    EXCEL_ENROLLMENT_NET_AMOUNT,
    _calculate_percentage_discount,
    _generated_payment_link_metrics,
    _fetch_payment_rows,
    _ensure_all_digital_payment_receipt,
    _find_existing_student_credentials,
    _find_existing_teacher_credentials,
    _list_generated_payment_links,
    _provision_or_reuse_student_credentials,
    _registered_payment_metrics,
    _resolve_student_enrollment_identity,
    _serialize_registered_user_payment,
    _sync_excel_course_charge_adjustments,
    _store_continuing_education_voucher,
    _store_continuing_education_invoice,
    PaymentGatewayError,
    admin_cancel_payment,
    admin_get_payment_info,
    correct_continuing_education_discount,
    register_continuing_education_payment,
    reconcile_pending_all_digital_payments,
)
from dashboard_auth.payment_receipt import build_all_digital_payment_receipt


class _MultiResultCursor:
    def __init__(self):
        self.description = None

    def execute(self, _query, _params):
        return None

    def nextset(self):
        self.description = [('value',)]
        return True

    def fetchall(self):
        return [(7,)]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class PaymentSqlResultTests(TestCase):
    @patch('dashboard_auth.payments.connection_for_query')
    def test_skips_rowcount_result_before_reading_procedure_output(self, select_connection):
        select_connection.return_value.cursor.return_value = _MultiResultCursor()

        rows = _fetch_payment_rows('EXEC fin.usp_Prueba', [])

        self.assertEqual(rows, [{'value': 7}])

    @patch('dashboard_auth.payments.cache.get', return_value=None)
    @patch('dashboard_auth.payments.cache.set')
    @patch('dashboard_auth.payments._fetch_payment_rows')
    def test_payment_metrics_include_total_pending_collection(self, fetch_rows, _cache_set, _cache_get):
        fetch_rows.return_value = [{
            'registered_users': 26,
            'users_with_payments': 24,
            'payment_records': 25,
            'uploaded_invoices': 6,
            'total_value': '9600.00',
            'registered_value': '8280.00',
            'discount_value': '40.00',
            'pending_collection_value': '2280.00',
        }]

        result = _registered_payment_metrics('edu.CorteEstudiante', 'fin.Cuenta', 'fin.Movimiento', 'fin.Factura')

        self.assertEqual(result['pending_collection_value'], '1280.00')
        self.assertIn('EffectiveSummary', fetch_rows.call_args.args[0])


class AdminPaymentPayloadTests(TestCase):
    @patch('dashboard_auth.payments._get_payment_provider_transaction')
    def test_accepts_unaccented_transaction_id_for_information(self, provider_call):
        provider_call.return_value = {'ok': True}

        admin_get_payment_info({'transaccion_id': 'TX-123'})

        provider_call.assert_called_once_with('TX-123')


class TeacherCredentialReuseTests(TestCase):
    @patch('dashboard_auth.payments.find_microsoft365_user_by_email')
    @patch('dashboard_auth.payments._find_existing_teacher_credentials')
    @patch('dashboard_auth.payments._find_existing_student_credentials', return_value=None)
    def test_validates_and_reuses_teacher_credentials_by_cedula(
        self, _student_credentials, teacher_credentials, office_lookup
    ):
        teacher_credentials.return_value = {
            'correo': 'docente@intec.edu.ec',
            'password_temporal': 'ClaveExistente',
            'credentials_reused': True,
            'reused_teacher_credentials': True,
            'credential_source': 'USUARIOS_DOCENTE',
        }
        office_lookup.return_value = {
            'exists': True,
            'correo': 'docente@intec.edu.ec',
            'employee_id': '1104371859',
            'user_id': 'office-user-1',
        }

        result = _resolve_student_enrollment_identity(
            nombre='Docente Estudiante',
            cedula='1104371859',
        )

        office_lookup.assert_called_once_with('docente@intec.edu.ec')
        self.assertTrue(result['reused_teacher_credentials'])
        self.assertTrue(result['office365_reused'])
        self.assertEqual(result['password_temporal'], 'ClaveExistente')

    @patch('dashboard_auth.payments.find_microsoft365_user_by_email')
    @patch('dashboard_auth.payments._fetch_payment_rows', return_value=[])
    @patch('dashboard_auth.payments._find_existing_teacher_credentials', return_value=None)
    @patch('dashboard_auth.payments._find_existing_student_credentials', return_value=None)
    def test_reuses_existing_office_account_without_modifying_it(
        self, _student_credentials, _teacher_credentials, _rows, office_lookup
    ):
        office_lookup.return_value = {
            'exists': True,
            'correo': 'juan.perez@intec.edu.ec',
            'display_name': 'Juan Perez',
            'employee_id': '1104371859',
            'user_id': 'office-user-2',
        }

        result = _resolve_student_enrollment_identity(
            nombre='Juan Perez',
            cedula='1104371859',
        )

        self.assertTrue(result['office365_reused'])
        self.assertTrue(result['office365_read_only'])
        self.assertEqual(result['credential_source'], 'MICROSOFT365_EXISTENTE_SOLO_LECTURA')

    @patch('dashboard_auth.payments._fetch_payment_rows')
    def test_resolves_existing_student_credentials_by_normalized_identity(self, fetch_rows):
        fetch_rows.return_value = [{
            'codigo_estud': '1954',
            'correo_intec': 'estudiante@intec.edu.ec',
            'password': 'ClaveExistente',
        }]

        result = _find_existing_student_credentials('110-437-1859')

        self.assertTrue(result['credentials_reused'])
        self.assertTrue(result['reused_student_credentials'])
        self.assertFalse(result['reused_teacher_credentials'])
        self.assertEqual(result['correo'], 'estudiante@intec.edu.ec')
        self.assertEqual(fetch_rows.call_args.args[1], ['1104371859', '1104371859'])

    @patch('dashboard_auth.payments._fetch_payment_rows')
    def test_resolves_existing_teacher_credentials_by_normalized_identity(self, fetch_rows):
        fetch_rows.return_value = [{
            'codigo_doc': '15',
            'codigo_usuario': '81',
            'login': 'docente@intec.edu.ec',
            'password': 'ClaveExistente',
        }]

        result = _find_existing_teacher_credentials('110-437-1859')

        self.assertTrue(result['reused_teacher_credentials'])
        self.assertEqual(result['correo'], 'docente@intec.edu.ec')
        self.assertEqual(result['password_temporal'], 'ClaveExistente')
        self.assertEqual(fetch_rows.call_args.args[1], ['1104371859', '2'])

    @patch('dashboard_auth.payments._fetch_payment_rows')
    def test_rejects_ambiguous_teacher_credentials(self, fetch_rows):
        fetch_rows.return_value = [
            {'login': 'uno@intec.edu.ec', 'password': 'Clave1'},
            {'login': 'dos@intec.edu.ec', 'password': 'Clave2'},
        ]

        with self.assertRaisesRegex(PaymentGatewayError, 'más de una credencial'):
            _find_existing_teacher_credentials('1104371859')

    @patch('dashboard_auth.payments.create_microsoft365_user')
    @patch('dashboard_auth.payments._mark_correos_estud_intec_sent')
    @patch('dashboard_auth.payments._send_intec_welcome_email')
    @patch('dashboard_auth.payments._update_official_intec_credentials')
    def test_reuses_teacher_identity_without_creating_microsoft_account(
        self, update_credentials, send_email, mark_sent, create_microsoft_user
    ):
        send_email.return_value = {'sent': True}
        identity = {
            'correo': 'docente@intec.edu.ec',
            'password_temporal': 'ClaveExistente',
            'reused_teacher_credentials': True,
            'codigo_doc': '15',
            'codigo_usuario': '81',
        }

        microsoft_result, email_result = _provision_or_reuse_student_credentials(
            identity=identity,
            nombre='Docente Estudiante',
            cedula='1104371859',
            recipient_email='personal@example.com',
            course_name='Educación continua',
            official_record={'codigo_estud': '1954'},
            require_microsoft365=True,
        )

        create_microsoft_user.assert_not_called()
        update_credentials.assert_called_once_with(
            codigo_estud='1954',
            correo_intec='docente@intec.edu.ec',
            password_temporal='ClaveExistente',
        )
        self.assertTrue(send_email.call_args.kwargs['credentials_reused'])
        mark_sent.assert_called_once_with('1954')
        self.assertTrue(microsoft_result['reused'])
        self.assertTrue(email_result['sent'])

    @patch('dashboard_auth.payments.create_microsoft365_user')
    @patch('dashboard_auth.payments._mark_correos_estud_intec_sent')
    @patch('dashboard_auth.payments._send_intec_welcome_email')
    @patch('dashboard_auth.payments._update_official_intec_credentials')
    def test_reuses_student_identity_without_creating_microsoft_account(
        self, update_credentials, send_email, mark_sent, create_microsoft_user
    ):
        send_email.return_value = {'sent': True}
        identity = {
            'correo': 'estudiante@intec.edu.ec',
            'password_temporal': 'ClaveExistente',
            'credentials_reused': True,
            'reused_student_credentials': True,
            'credential_source': 'CORREOS_ESTUD_INTEC',
            'codigo_estud': '1954',
        }

        microsoft_result, _ = _provision_or_reuse_student_credentials(
            identity=identity,
            nombre='Estudiante Existente',
            cedula='1104371859',
            recipient_email='personal@example.com',
            course_name='Educación continua',
            official_record={'codigo_estud': '1954'},
            require_microsoft365=True,
        )

        create_microsoft_user.assert_not_called()
        self.assertEqual(send_email.call_args.kwargs['credential_source'], 'CORREOS_ESTUD_INTEC')
        self.assertTrue(microsoft_result['reused'])


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

    @patch('dashboard_auth.payments.upload_continuing_education_voucher')
    def test_stores_invoice_in_dedicated_onedrive_folder(self, upload_document):
        upload_document.return_value = {
            'file_name': 'FACTURA_001_6ca13d52.pdf',
            'relative_path': 'EDUCACION_CONTINUA/Curso/Corte/1954 - Estudiante/FACTURAS/FACTURA_001.pdf',
            'web_url': 'https://example.test/factura',
        }

        result = _store_continuing_education_invoice(
            {
                'invoice_base64': b64encode(b'%PDF-1.7\nfactura\n%%EOF').decode('ascii'),
                'invoice_name': 'factura.pdf',
                'numero_factura': '001',
            },
            codigo_estud='1954', course_name='Curso', cut_name='Corte', student_name='Estudiante',
        )

        self.assertEqual(upload_document.call_args.kwargs['document_folder'], 'FACTURAS')
        self.assertEqual(result['web_url'], 'https://example.test/factura')


class ContinuingEducationDiscountTests(TestCase):
    def test_accepts_referred_discount_type(self):
        self.assertIn('DESCUENTO_REFERIDO', CONTINUING_EDUCATION_DISCOUNT_TYPES)
        self.assertIn('BECA_INTEC', CONTINUING_EDUCATION_DISCOUNT_TYPES)

    def test_calculates_discount_percentage_from_course_value(self):
        value = _calculate_percentage_discount(
            percentage=Decimal('25'),
            course_value=Decimal('400'),
            pending_balance=Decimal('400'),
        )

        self.assertEqual(value, Decimal('100.00'))

    def test_caps_full_scholarship_at_pending_balance(self):
        value = _calculate_percentage_discount(
            percentage=Decimal('100'),
            course_value=Decimal('500'),
            pending_balance=Decimal('300'),
        )

        self.assertEqual(value, Decimal('300.00'))

    def test_rejects_negative_percentage(self):
        with self.assertRaisesRegex(PaymentGatewayError, 'no puede ser menor que 0 %'):
            _calculate_percentage_discount(
                percentage=Decimal('-1'),
                course_value=Decimal('500'),
                pending_balance=Decimal('500'),
            )

    def test_rejects_zero_when_applying_benefit(self):
        with self.assertRaisesRegex(PaymentGatewayError, 'debe ser mayor que 0 %'):
            _calculate_percentage_discount(
                percentage=Decimal('0'),
                course_value=Decimal('500'),
                pending_balance=Decimal('500'),
            )

    def test_rejects_percentage_over_one_hundred(self):
        with self.assertRaisesRegex(PaymentGatewayError, 'no puede superar el 100 %'):
            _calculate_percentage_discount(
                percentage=Decimal('101'),
                course_value=Decimal('500'),
                pending_balance=Decimal('500'),
            )

    @patch('dashboard_auth.payments.create_notification_safely')
    @patch('dashboard_auth.payments.cache.delete')
    @patch('dashboard_auth.payments._fetch_payment_rows')
    @patch('dashboard_auth.payments._ensure_continuing_education_payments_available')
    def test_corrects_discount_even_when_account_payment_status_is_not_consulted(
        self, _ensure_available, fetch_rows, _cache_delete, _notify
    ):
        fetch_rows.side_effect = [
            [{
                'MovimientoId': 10,
                'CuentaId': 5,
                'ValorOriginal': Decimal('100.00'),
                'ValorCurso': Decimal('400.00'),
                'OtrosDescuentos': Decimal('0.00'),
                'ObservacionMatricula': '',
            }],
            [{
                'movimiento_id': '11',
                'movimiento_relacionado_id': '10',
                'valor': Decimal('200.00'),
            }],
        ]

        result = correct_continuing_education_discount(
            {
                'movimiento_id': '10',
                'tipo_descuento': 'BECA',
                'porcentaje': '50',
                'motivo': 'Beca autorizada',
                'motivo_correccion': 'Porcentaje registrado incorrectamente',
            },
            user_login='admin',
        )

        self.assertEqual(result['value'], '200.00')
        self.assertEqual(result['replacement']['movimiento_relacionado_id'], '10')
        self.assertIn("EstadoMovimiento = 'ANULADO'", fetch_rows.call_args_list[1].args[0])


class ExcelEnrollmentValueTests(TestCase):
    def test_full_scholarship_marks_account_as_paid_without_cash_payment(self):
        result = _serialize_registered_user_payment({
            'codigo_estud': '2001',
            'estudiante_corte_id': '10',
            'corte_id': '1',
            'nombre': 'Estudiante con beca',
            'total_value': '400.00',
            'registered_value': '0.00',
            'discount_value': '400.00',
            'payment_count': 0,
            'invoice_count': 0,
            'is_excel_enrollment': 0,
        })

        self.assertEqual(result['pending_balance'], '0.00')
        self.assertEqual(result['payment_status'], 'PAGADO')
        self.assertTrue(result['certificate_payment_ready'])

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

    def test_reports_pending_invoice_when_a_payment_has_no_document(self):
        result = _serialize_registered_user_payment({'payment_count': 2, 'invoice_count': 1})

        self.assertEqual(result['invoice_status'], 'PENDIENTE')
        self.assertEqual(result['pending_invoice_count'], 1)

    def test_marks_all_digital_payment_as_paid_when_legacy_charge_is_missing(self):
        result = _serialize_registered_user_payment({
            'total_value': '0.00',
            'registered_value': '500.00',
            'discount_value': '0.00',
            'payment_count': 1,
        })

        self.assertEqual(result['total_value'], '500.00')
        self.assertEqual(result['pending_balance'], '0.00')
        self.assertEqual(result['payment_status'], 'PAGADO')
        self.assertTrue(result['certificate_payment_ready'])
