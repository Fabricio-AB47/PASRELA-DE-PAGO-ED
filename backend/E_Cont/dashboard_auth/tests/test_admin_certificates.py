from io import BytesIO
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from dashboard_auth.admin_certificates import (
    AdminCertificateError,
    _attach_certificate_info,
    _certificate_payload_from_student,
    _certificate_payment_accounts,
    _enrich_certificate_course_details,
    _generate_student_certificate,
    download_admin_certificates_batch,
    generate_admin_certificates,
)
from dashboard_auth.inscription_certificate import _certificate_storage_subpath


class AdminCertificateEligibilityTests(TestCase):
    def setUp(self):
        self.student = {
            'corte_estudiante_id': '7',
            'codigo_estud': '1954',
            'corte_id': '1',
            'nombre': 'Estudiante Ejemplo',
            'correo_personal': 'personal@example.com',
            'nota_final': '9.00',
        }

    def test_passing_student_with_pending_balance_is_not_selectable(self):
        attached = _attach_certificate_info(
            self.student,
            {},
            {
                ('1954', '1'): {
                    'saldo_pendiente': '100.00',
                    'pago_completo': False,
                },
            },
        )

        self.assertFalse(attached['certificado_disponible'])
        self.assertEqual(attached['estado_financiero'], 'PENDIENTE')
        self.assertEqual(attached['motivo_no_disponible'], 'Saldo pendiente')

    def test_passing_student_with_paid_balance_is_selectable(self):
        attached = _attach_certificate_info(
            self.student,
            {},
            {
                ('1954', '1'): {
                    'saldo_pendiente': '0.00',
                    'pago_completo': True,
                },
            },
        )

        self.assertTrue(attached['certificado_disponible'])
        self.assertEqual(attached['estado_financiero'], 'PAGADO')

    def test_backend_rejects_generation_when_balance_is_pending(self):
        with self.assertRaisesRegex(AdminCertificateError, 'completar el pago total'):
            _generate_student_certificate(
                {'corte_id': '1'},
                {**self.student, 'pago_completo': False},
                user_login='admin',
            )

    @patch('dashboard_auth.admin_certificates._fetch_complement_all')
    def test_certificate_uses_module_timeline_and_configured_course_hours(self, fetch_all):
        fetch_all.return_value = [{
            'FechaInicioCurso': '2026-07-20',
            'FechaFinCurso': '2026-09-10',
        }]
        cut = _enrich_certificate_course_details({
            'corte_id': '1',
            'fecha_inicio': '20 de julio de 2026',
            'fecha_fin': '8 de agosto de 2026',
            'horas': '160',
        })

        payload = _certificate_payload_from_student(cut, self.student, user_login='admin')

        self.assertEqual(payload['fecha_inicio'], '20 de julio de 2026')
        self.assertEqual(payload['fecha_fin'], '10 de septiembre de 2026')
        self.assertEqual(payload['horas_curso'], '160')

    @patch('dashboard_auth.admin_certificates.list_admin_certificate_students')
    @patch('dashboard_auth.admin_certificates.send_certificate_email')
    @patch('dashboard_auth.admin_certificates._generate_student_certificate')
    @patch('dashboard_auth.admin_certificates._certificate_payment_accounts')
    @patch('dashboard_auth.admin_certificates._fetch_certificate_links', return_value={})
    @patch('dashboard_auth.admin_certificates.list_enrolled_students')
    @patch('dashboard_auth.admin_certificates._ensure_certificate_link_table')
    def test_generation_sends_certificate_to_personal_email(
        self,
        _ensure_table,
        list_students,
        _links,
        payment_accounts,
        generate_certificate,
        send_email,
        refreshed_students,
    ):
        list_students.return_value = {
            'cut': {'corte_id': 1, 'nombre_corte': 'Primera corte'},
            'students': [self.student],
        }
        payment_accounts.return_value = {
            ('1954', '1'): {
                'saldo_pendiente': '0.00',
                'pago_completo': True,
            },
        }
        generate_certificate.return_value = {
            'certificate_id': 21,
            'certificate_code': 'CERT-21',
        }
        send_email.return_value = {'sent': True}
        refreshed_students.return_value = {'students': [], 'metrics': {}}

        result = generate_admin_certificates(
            {'corte_id': '1', 'corte_estudiante_ids': ['7']},
            user_login='admin',
        )

        send_email.assert_called_once_with(
            recipient_email='personal@example.com',
            recipient_name='Estudiante Ejemplo',
            certificate_record=generate_certificate.return_value,
        )
        self.assertEqual(result['summary']['generados'], 1)
        self.assertEqual(result['summary']['respaldados'], 1)
        self.assertEqual(result['summary']['correos_enviados'], 1)
        self.assertEqual(result['summary']['errores_correo'], 0)

    def test_admin_certificate_is_stored_in_student_folder(self):
        subpath = _certificate_storage_subpath(
            {
                'source': 'admin_corte',
                'codigo_estud': '1954',
                'nombre': 'Estudiante Ejemplo',
            },
            'certificado.pdf',
        )

        self.assertEqual(
            subpath.as_posix(),
            'estudiantes/1954_estudiante-ejemplo/certificado.pdf',
        )

    @patch('dashboard_auth.admin_certificates.download_admin_certificate')
    def test_batch_download_returns_zip_with_generated_certificates(self, download_certificate):
        download_certificate.side_effect = [
            (b'pdf-one', 'certificado_1.pdf'),
            (b'pdf-two', 'certificado_2.pdf'),
        ]

        content, filename, content_type = download_admin_certificates_batch(8, ['1', '2'])

        self.assertEqual(filename, 'certificados_corte_8.zip')
        self.assertEqual(content_type, 'application/zip')
        with ZipFile(BytesIO(content)) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                ['certificado_1.pdf', 'certificado_2.pdf'],
            )
            self.assertEqual(archive.read('certificado_1.pdf'), b'pdf-one')

    @patch('dashboard_auth.admin_certificates.download_admin_certificate')
    def test_batch_download_returns_pdf_when_only_one_was_generated(self, download_certificate):
        download_certificate.return_value = (b'pdf-one', 'certificado_1.pdf')

        content, filename, content_type = download_admin_certificates_batch(8, ['1'])

        self.assertEqual(content, b'pdf-one')
        self.assertEqual(filename, 'certificado_1.pdf')
        self.assertEqual(content_type, 'application/pdf')

    @patch('dashboard_auth.admin_certificates._fetch_complement_all')
    def test_payment_accounts_use_balance_after_payments_and_discounts(self, fetch_all):
        fetch_all.return_value = [{
            'CodigoEstud': '1954',
            'CorteId': '1',
            'EsMatriculaExcel': 0,
            'TotalCurso': '400.00',
            'TotalPagado': '300.00',
            'TotalDescuentos': '100.00',
            'AjusteExcel': '0.00',
        }]

        accounts = _certificate_payment_accounts(1)

        self.assertTrue(accounts[('1954', '1')]['pago_completo'])
        self.assertEqual(accounts[('1954', '1')]['saldo_pendiente'], '0.00')
