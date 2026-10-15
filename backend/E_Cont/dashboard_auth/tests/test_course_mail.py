from copy import deepcopy
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, RequestFactory

from dashboard_auth.course_mail import (
    CourseMailError, allowed_types, list_mail_students, preview_mail, send_mail_preview,
)
from dashboard_auth.course_mail_views import preview_view, send_view, students_view
from dashboard_auth.security import create_session_token


ADMIN = {'category': 'staff', 'login': 'admin', 'role': {'name': 'ADMINISTRADOR'}}
FINANCE = {**ADMIN, 'login': 'finance', 'role': {'name': 'FINANCIERO'}}
STUDENT = {'corte_estudiante_id': '10', 'codigo_estud': '100', 'corte_id': '1',
           'nombre': 'Estudiante de prueba', 'correo_personal': 'student@example.com',
           'nombre_curso': 'Curso de prueba', 'nota_final': 8}


def protected_text(kind='approval', text='Indicaciones'):
    fields = '{nombre} · {saldo}' if kind == 'balance' else '{nombre} · {nota}'
    if kind == 'approval':
        fields = '{nombre} · {curso} · {nota}'
        fields += ' · {estado_pagos}'
    if kind == 'failed':
        fields += ' · {curso}'
    return fields + '\n\n' + text


class CourseMailTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.enrollment = patch('dashboard_auth.course_mail.list_enrolled_students',
                                return_value={'cut': {'corte_id': '1'}, 'students': [deepcopy(STUDENT)]}).start()
        self.accounts = patch('dashboard_auth.course_mail._certificate_payment_accounts', return_value={
            ('100', '1'): {'saldo_pendiente': '25.50', 'pago_completo': False},
        }).start()
        self.mail = patch('dashboard_auth.course_mail._send_graph_mail').start()
        self.addCleanup(patch.stopall)
        self.payload = {'corte_id': '1', 'kind': 'approval', 'student_ids': ['10']}

    def test_approval_can_be_notified_with_debt_without_promising_certificate(self):
        preview = preview_mail(self.payload, ADMIN)
        self.assertIn('25.50', preview['messages'][0]['body'])
        self.assertIn('se encuentra en proceso de gestión', preview['messages'][0]['body'])
        self.assertIn('será enviado al correo electrónico', preview['messages'][0]['body'])
        self.mail.assert_not_called()

    def test_approval_includes_course_and_notices_have_separate_paragraphs(self):
        for kind, grade in [('approval', 8), ('failed', 5), ('balance', 8)]:
            self.enrollment.return_value['students'][0]['nota_final'] = grade
            message = preview_mail({**self.payload, 'kind': kind}, ADMIN)['messages'][0]
            self.assertNotIn('Curso de prueba', message['subject'])
            if kind == 'approval':
                self.assertIn('satisfactoriamente Curso de prueba, con una calificación final de 8/10.', message['body'])
            elif kind == 'failed':
                self.assertIn('su resultado en Curso de prueba es reprobado', message['body'])
                self.assertNotIn('7/10', message['body'])
            else:
                self.assertNotIn('Curso de prueba', message['body'])
                self.assertNotIn('curso', message['body'].lower())
            self.assertGreaterEqual(len(message['body'].split('\n\n')), 5)

    def test_student_without_grade_is_neither_approved_nor_failed(self):
        self.enrollment.return_value['students'][0]['nota_final'] = None
        for kind in ['approval', 'failed']:
            self.assertFalse(list_mail_students('1', kind, ADMIN)['students'][0]['eligible'])

    def test_grade_boundaries(self):
        for grade, approval, failed in [(0, False, True), (6.99, False, True),
                                         (7, True, False), (10, True, False), (11, False, False)]:
            self.enrollment.return_value['students'][0]['nota_final'] = grade
            self.assertEqual(list_mail_students('1', 'approval', ADMIN)['students'][0]['eligible'], approval)
            self.assertEqual(list_mail_students('1', 'failed', ADMIN)['students'][0]['eligible'], failed)

    def test_finance_can_only_send_balance_notices(self):
        self.assertEqual(allowed_types(FINANCE), ['balance'])
        with self.assertRaises(CourseMailError):
            preview_mail(self.payload, FINANCE)
        preview = preview_mail({**self.payload, 'kind': 'balance'}, FINANCE)
        self.assertIn('25.50', preview['messages'][0]['body'])

    def test_balance_without_debt_is_disabled(self):
        self.accounts.return_value[('100', '1')]['saldo_pendiente'] = '0'
        self.assertFalse(list_mail_students('1', 'balance', ADMIN)['students'][0]['eligible'])

    def test_invalid_email_is_disabled(self):
        self.enrollment.return_value['students'][0]['correo_personal'] = 'invalid'
        with self.assertRaises(CourseMailError):
            preview_mail(self.payload, ADMIN)

    def test_all_types_send_to_both_registered_addresses(self):
        self.enrollment.return_value['students'][0]['correo_intec'] = 'student@intec.edu.ec'
        for kind, grade in [('approval', 8), ('failed', 5), ('balance', 8)]:
            self.enrollment.return_value['students'][0]['nota_final'] = grade
            preview = preview_mail({**self.payload, 'kind': kind}, ADMIN)
            self.assertEqual(preview['messages'][0]['emails'], ['student@intec.edu.ec', 'student@example.com'])
            send_mail_preview(preview['token'], ADMIN)
            recipients = self.mail.call_args.args[0]['message']['toRecipients']
            self.assertEqual([item['emailAddress']['address'] for item in recipients],
                             ['student@intec.edu.ec', 'student@example.com'])

    def test_equal_addresses_are_not_duplicated(self):
        self.enrollment.return_value['students'][0]['correo_intec'] = ' STUDENT@example.com '
        message = preview_mail(self.payload, ADMIN)['messages'][0]
        self.assertEqual(message['emails'], ['STUDENT@example.com'])

    def test_valid_institutional_address_is_used_when_personal_is_invalid(self):
        self.enrollment.return_value['students'][0].update(
            correo_intec='student@intec.edu.ec', correo_personal='invalid')
        message = preview_mail(self.payload, ADMIN)['messages'][0]
        self.assertEqual(message['emails'], ['student@intec.edu.ec'])

    def test_changed_secondary_email_requires_new_preview(self):
        self.enrollment.return_value['students'][0]['correo_intec'] = 'student@intec.edu.ec'
        preview = preview_mail(self.payload, ADMIN)
        self.enrollment.return_value['students'][0]['correo_personal'] = 'new@example.com'
        with self.assertRaises(CourseMailError):
            send_mail_preview(preview['token'], ADMIN)
        self.mail.assert_not_called()

    def test_balance_notice_directs_payment_verification_to_collections(self):
        body = preview_mail({**self.payload, 'kind': 'balance'}, ADMIN)['messages'][0]['body']
        self.assertIn('área financiera de INTEC', body)
        self.assertIn('reenvíelo a DeptCobranzas@intec.edu.ec', body)
        self.assertIn('nombre completo y número de cédula', body)

    def test_unknown_student_is_rejected(self):
        with self.assertRaises(CourseMailError):
            preview_mail({**self.payload, 'student_ids': ['999']}, ADMIN)

    def test_send_matches_preview_and_is_not_repeated(self):
        preview = preview_mail({**self.payload, 'extra': '<b>Información adicional</b>'}, ADMIN)
        result = send_mail_preview(preview['token'], ADMIN)
        self.assertEqual(result['accepted'], 1)
        mail = self.mail.call_args.args[0]
        self.assertEqual(mail['message']['body']['contentType'], 'HTML')
        self.assertEqual(mail['message']['body']['content'], preview['messages'][0]['html'])
        self.assertIn('&lt;b&gt;Información adicional&lt;/b&gt;', mail['message']['body']['content'])
        self.assertNotIn('<b>Información adicional</b>', mail['message']['body']['content'])
        self.assertTrue(mail['_skip_default_cc'])
        with self.assertRaises(CourseMailError):
            send_mail_preview(preview['token'], ADMIN)
        self.mail.assert_called_once()

    def test_all_notice_types_use_institutional_html_and_inline_logo(self):
        for kind, grade in [('approval', 8), ('failed', 5), ('balance', 8)]:
            self.enrollment.return_value['students'][0]['nota_final'] = grade
            preview = preview_mail({**self.payload, 'kind': kind}, ADMIN)
            html = preview['messages'][0]['html']
            self.assertIn('background:#9B0E0E', html)
            self.assertIn('max-width:620px', html)
            self.assertIn('Segoe UI,Arial,sans-serif', html)
            self.assertIn('cid:intec-logo.png', html)
            self.assertTrue(preview['logo_url'].startswith('data:image/png;base64,'))
            send_mail_preview(preview['token'], ADMIN)
            logo = self.mail.call_args.args[0]['message']['attachments'][0]
            self.assertTrue(logo['isInline'])
            self.assertEqual(logo['contentId'], 'intec-logo.png')

    @patch('dashboard_auth.course_mail._build_intec_logo_attachment', return_value=None)
    def test_missing_logo_still_produces_valid_message(self, _logo):
        preview = preview_mail(self.payload, ADMIN)
        self.assertNotIn('cid:', preview['messages'][0]['html'])
        send_mail_preview(preview['token'], ADMIN)
        self.assertNotIn('attachments', self.mail.call_args.args[0]['message'])

    def test_changed_balance_requires_new_preview(self):
        preview = preview_mail({**self.payload, 'kind': 'balance'}, ADMIN)
        self.accounts.return_value[('100', '1')]['saldo_pendiente'] = '10'
        with self.assertRaises(CourseMailError):
            send_mail_preview(preview['token'], ADMIN)
        self.mail.assert_not_called()

    def test_edited_subject_and_body_are_previewed_and_sent_exactly(self):
        body = protected_text(text='Indicaciones personalizadas <script>alert(1)</script>\nGracias.')
        preview = preview_mail({**self.payload, 'edits': {'10': {
            'subject': 'Gestión personalizada', 'body': body,
        }}}, ADMIN)
        self.assertIn('Estudiante de prueba', preview['messages'][0]['body'])
        self.assertIn('25.50', preview['messages'][0]['body'])
        self.assertNotIn('<script>', preview['messages'][0]['html'])
        self.assertIn('&lt;script&gt;', preview['messages'][0]['html'])
        send_mail_preview(preview['token'], ADMIN)
        sent = self.mail.call_args.args[0]['message']
        self.assertEqual(sent['subject'], 'Gestión personalizada')
        self.assertEqual(sent['body']['content'], preview['messages'][0]['html'])

    def test_custom_body_does_not_hide_changed_financial_data(self):
        preview = preview_mail({**self.payload, 'kind': 'balance', 'edits': {
            '10': {'subject': 'Saldo', 'body': protected_text('balance', 'Por favor revise su cuenta.')},
        }}, ADMIN)
        self.accounts.return_value[('100', '1')]['saldo_pendiente'] = '12.00'
        with self.assertRaises(CourseMailError):
            send_mail_preview(preview['token'], ADMIN)
        self.mail.assert_not_called()

    def test_invalid_edits_are_rejected(self):
        for edits in [[], {'999': {'subject': 'Asunto', 'body': 'Texto'}},
                      {'10': 'texto'}, {'10': {'subject': '', 'body': 'Texto'}},
                      {'10': {'subject': 'Asunto\nOtro', 'body': 'Texto'}},
                      {'10': {'subject': 'a' * 251, 'body': 'Texto'}},
                      {'10': {'subject': 'Asunto', 'body': ' '}},
                      {'10': {'subject': 'Asunto', 'body': 'a' * 12001}}]:
            with self.subTest(edits_type=type(edits).__name__):
                with self.assertRaises(CourseMailError):
                    preview_mail({**self.payload, 'edits': edits}, ADMIN)

    def test_each_recipient_has_independent_text(self):
        self.enrollment.return_value['students'].append({**STUDENT, 'corte_estudiante_id': '11'})
        preview = preview_mail({**self.payload, 'student_ids': ['10', '11'], 'edits': {
            '10': {'subject': 'Mensaje individual', 'body': protected_text(text='Solo para el primer estudiante.')},
        }}, ADMIN)
        self.assertIn('Solo para el primer estudiante.', preview['messages'][0]['body'])
        self.assertIn('ha aprobado satisfactoriamente Curso de prueba', preview['messages'][1]['body'])

    def test_template_is_personalized_and_sent_with_institutional_design(self):
        preview = preview_mail({**self.payload, 'kind': 'balance', 'template': {
            'subject': 'Regularización de valores pendientes',
            'body': 'Hola {nombre}, su saldo es USD {saldo}. Contacto: Secretaría.',
        }}, ADMIN)
        message = preview['messages'][0]
        self.assertEqual(message['subject'], 'Regularización de valores pendientes')
        self.assertIn('USD 25.50', message['body'])
        self.assertEqual(message['email'], 'student@example.com')
        self.assertNotIn('{nombre}', message['body'])
        send_mail_preview(preview['token'], ADMIN)
        self.assertEqual(self.mail.call_args.args[0]['message']['body']['content'], message['html'])

    def test_template_and_individual_edit_can_be_combined(self):
        preview = preview_mail({**self.payload, 'template': {'subject': 'Gestión de certificado', 'body': protected_text()},
                               'edits': {'10': {'subject': 'Asunto individual', 'body': protected_text(text='Texto individual')}}}, ADMIN)
        self.assertIn('Texto individual', preview['messages'][0]['body'])
        send_mail_preview(preview['token'], ADMIN)
        self.assertEqual(self.mail.call_args.args[0]['message']['subject'], 'Asunto individual')

    def test_unknown_template_fields_and_invalid_templates_are_rejected(self):
        for template in ['invalid', {'subject': '', 'body': 'Texto'},
                         {'subject': 'Asunto', 'body': ''},
                         {'subject': 'Asunto', 'body': '{campo_inexistente}'},
                         {'subject': 'Asunto\nOtro', 'body': 'Texto'}]:
            with self.assertRaises(CourseMailError):
                preview_mail({**self.payload, 'template': template}, ADMIN)

    def test_template_does_not_allow_sending_when_balance_has_changed(self):
        preview = preview_mail({**self.payload, 'kind': 'balance', 'template': {
            'subject': 'Valores', 'body': protected_text('balance', 'Comuníquese con Finanzas.'),
        }}, ADMIN)
        self.accounts.return_value[('100', '1')]['saldo_pendiente'] = '5'
        with self.assertRaises(CourseMailError):
            send_mail_preview(preview['token'], ADMIN)
        self.mail.assert_not_called()

    def test_automatic_fields_cannot_be_removed_replaced_or_duplicated(self):
        for kind in ['approval', 'failed', 'balance']:
            if kind == 'failed':
                self.enrollment.return_value['students'][0]['nota_final'] = 5
            valid = protected_text(kind)
            fields = ['nombre', 'saldo' if kind == 'balance' else 'nota']
            if kind in {'approval', 'failed'}:
                fields.append('curso')
            for field in fields:
                for replacement in ['', 'Dato falso', '{correo}', '{' + field + '}{' + field + '}']:
                    body = valid.replace('{' + field + '}', replacement)
                    for channel in ['template', 'edits']:
                        changed = {'subject': 'Aviso', 'body': body}
                        payload = {**self.payload, 'kind': kind, channel: changed if channel == 'template' else {'10': changed}}
                        with self.subTest(kind=kind, field=field, channel=channel):
                            with self.assertRaises(CourseMailError):
                                preview_mail(payload, ADMIN)
        self.mail.assert_not_called()

    def test_tampered_and_other_user_tokens_are_rejected(self):
        preview = preview_mail(self.payload, ADMIN)
        for token, user in [(preview['token'] + 'bad', ADMIN),
                             (preview['token'], {**ADMIN, 'login': 'someone_else'})]:
            with self.assertRaises(CourseMailError):
                send_mail_preview(token, user)
        self.mail.assert_not_called()

    def test_batch_reports_partial_failures_without_resending(self):
        self.enrollment.return_value['students'].append({**STUDENT, 'corte_estudiante_id': '11'})
        preview = preview_mail({**self.payload, 'student_ids': ['10', '11']}, ADMIN)
        self.mail.side_effect = [None, RuntimeError('provider unavailable')]
        with self.assertLogs('dashboard_auth.course_mail', level='ERROR'):
            result = send_mail_preview(preview['token'], ADMIN)
        self.assertEqual((result['accepted'], result['failed']), (1, 1))

    def test_limits_and_duplicates(self):
        for ids in [[], list(range(26)), '10', [{}]]:
            with self.assertRaises(CourseMailError):
                preview_mail({**self.payload, 'student_ids': ids}, ADMIN)
        self.assertEqual(len(preview_mail({**self.payload, 'student_ids': ['10', '10']}, ADMIN)['messages']), 1)

    def test_endpoints_require_staff_session_and_validate_json(self):
        factory = RequestFactory()
        self.assertEqual(students_view(factory.get('/api/auth/admin/course-mail/students/')).status_code, 401)
        token = create_session_token({'category': 'student', 'login': 'student'})
        request = factory.get('/api/auth/admin/course-mail/students/', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(students_view(request).status_code, 403)
        for view, path in [(preview_view, 'preview'), (send_view, 'send')]:
            request = factory.post(f'/api/auth/admin/course-mail/{path}/', data='[]',
                                   content_type='application/json',
                                   HTTP_AUTHORIZATION=f'Bearer {create_session_token(ADMIN)}')
            self.assertEqual(view(request).status_code, 400)
