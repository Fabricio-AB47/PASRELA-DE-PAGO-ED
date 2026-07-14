from unittest import TestCase
from unittest.mock import patch

from dashboard_auth.student_dashboard import (
    StudentDashboardError,
    _normalize_grade_course,
    _student_certificate_course,
    preview_student_certificate,
)


def _grade_row(*, grade='10.00', charge='400.00', paid='400.00', discount='0.00'):
    return {
        'EstudianteCorteId': '7',
        'CorteId': '1',
        'CodigoEstud': '1954',
        'ApellidosNombre': 'Estudiante Prueba',
        'CorreoPersonal': 'estudiante@example.com',
        'NombreCursoMateria': 'Curso de prueba',
        'NotaFinal': grade,
        'TotalCargo': charge,
        'TotalPagado': paid,
        'TotalDescuento': discount,
    }


class StudentCertificateEligibilityTests(TestCase):
    def test_certificate_requires_approved_grade_and_full_payment(self):
        course = _normalize_grade_course(_grade_row())

        self.assertTrue(course['aprobado'])
        self.assertTrue(course['pago_completo'])
        self.assertTrue(course['certificado_disponible'])
        self.assertEqual(course['estado_financiero'], 'PAGADO')

    def test_approved_student_with_balance_cannot_download(self):
        course = _normalize_grade_course(_grade_row(paid='250.00'))

        self.assertTrue(course['aprobado'])
        self.assertFalse(course['pago_completo'])
        self.assertFalse(course['certificado_disponible'])
        self.assertEqual(course['saldo_pendiente'], '150.00')
        self.assertIn('pago total pendiente', course['certificado_estado'])

    def test_fully_paid_student_below_seven_cannot_download(self):
        course = _normalize_grade_course(_grade_row(grade='6.99'))

        self.assertFalse(course['aprobado'])
        self.assertTrue(course['pago_completo'])
        self.assertFalse(course['certificado_disponible'])
        self.assertIn('curso no aprobado', course['certificado_estado'])

    def test_grade_seven_with_full_payment_can_download(self):
        course = _normalize_grade_course(_grade_row(grade='7.00'))

        self.assertTrue(course['aprobado'])
        self.assertTrue(course['pago_completo'])
        self.assertTrue(course['certificado_disponible'])

    def test_grade_above_maximum_is_not_accepted_as_passing(self):
        course = _normalize_grade_course(_grade_row(grade='10.01'))

        self.assertFalse(course['aprobado'])
        self.assertFalse(course['certificado_disponible'])

    @patch('dashboard_auth.student_dashboard.build_inscription_certificate_preview_image')
    @patch('dashboard_auth.student_dashboard._student_certificate_course')
    def test_preview_is_available_without_issuing_certificate(self, certificate_course, build_preview):
        certificate_course.return_value = (
            {'codigo_estud': '1954', 'nombre': 'Estudiante Prueba'},
            _normalize_grade_course(_grade_row(grade='6.00', paid='0.00')),
        )
        build_preview.return_value = (b'preview-image', 'vista_previa.png')

        content, filename = preview_student_certificate({'login': 'estudiante'}, '7')

        certificate_course.assert_called_once_with(
            {'login': 'estudiante'},
            '7',
            require_approved=False,
            require_email=False,
        )
        self.assertEqual(content, b'preview-image')
        self.assertEqual(filename, 'vista_previa.png')

    @patch('dashboard_auth.student_dashboard._fetch_student_grade_row')
    @patch('dashboard_auth.student_dashboard._student_grades_status')
    @patch('dashboard_auth.student_dashboard._resolve_student_from_session')
    def test_backend_rejects_download_when_payment_is_incomplete(self, resolve_student, status, fetch_row):
        resolve_student.return_value = {'codigo_estud': '1954', 'correo_personal': 'estudiante@example.com'}
        status.return_value = {'available': True, 'message': ''}
        fetch_row.return_value = _grade_row(paid='100.00')

        with self.assertRaisesRegex(StudentDashboardError, 'pago total'):
            _student_certificate_course({'login': 'estudiante'}, '7')
