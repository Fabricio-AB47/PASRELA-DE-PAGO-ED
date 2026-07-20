from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import MagicMock, patch

from dashboard_auth.teacher_enrollment import (
    _prepare_teacher_credentials,
    _ensure_complement_teacher_capacity,
    TeacherEnrollmentError,
    create_teacher_entry_and_send_credentials,
    inspect_teacher_identity_by_cedula,
)


class TeacherEnrollmentCredentialReuseTests(TestCase):
    @patch('dashboard_auth.teacher_enrollment.create_microsoft365_teacher_user')
    @patch('dashboard_auth.teacher_enrollment._find_existing_office365_identity')
    @patch('dashboard_auth.teacher_enrollment._find_existing_student_credentials', return_value=None)
    @patch('dashboard_auth.teacher_enrollment._find_existing_teacher_credentials', return_value=None)
    def test_existing_office_email_is_reused_without_modifying_or_creating_account(
        self, _find_teacher, _find_student, find_office, create_user
    ):
        find_office.return_value = (
            {
                'correo': 'existente@intec.edu.ec',
                'password_temporal': 'ClaveDashboard',
                'credentials_reused': False,
                'reused_teacher_credentials': False,
                'office365_reused': True,
                'office365_read_only': True,
            },
            {'ok': True, 'created': False, 'reused': True, 'read_only': True},
        )

        identity, microsoft_result = _prepare_teacher_credentials(
            nombre='Docente Existente',
            cedula='1104371859',
        )

        create_user.assert_not_called()
        self.assertEqual(identity['correo'], 'existente@intec.edu.ec')
        self.assertTrue(identity['office365_read_only'])
        self.assertFalse(microsoft_result['created'])
        self.assertTrue(microsoft_result['read_only'])

    @patch('dashboard_auth.teacher_enrollment._find_existing_student_credentials', return_value=None)
    @patch('dashboard_auth.teacher_enrollment._find_existing_teacher_credentials')
    @patch('dashboard_auth.teacher_enrollment._fetch_one')
    @patch('dashboard_auth.teacher_enrollment._ensure_teacher_schema')
    def test_identity_check_reports_teacher_credentials_without_exposing_password(
        self, _ensure_schema, fetch_one, find_teacher, _find_student
    ):
        fetch_one.side_effect = [
            {'codigo_doc': '15', 'nombre': 'Docente Existente', 'correo_personal': 'personal@example.com'},
            None,
        ]
        find_teacher.return_value = {
            'correo': 'docente@intec.edu.ec',
            'password_temporal': 'ClaveSecreta',
            'credentials_reused': True,
            'credential_source': 'USUARIOS_DOCENTE',
        }

        result = inspect_teacher_identity_by_cedula('1104371859')

        self.assertEqual(result['profiles'], ['DOCENTE'])
        self.assertTrue(result['credentials_found'])
        self.assertEqual(result['correo_intec'], 'docente@intec.edu.ec')
        self.assertNotIn('password_temporal', result)

    @patch('dashboard_auth.teacher_enrollment._find_existing_student_credentials')
    @patch('dashboard_auth.teacher_enrollment._find_existing_teacher_credentials', return_value=None)
    @patch('dashboard_auth.teacher_enrollment._fetch_one')
    @patch('dashboard_auth.teacher_enrollment._ensure_teacher_schema')
    def test_identity_check_detects_student_and_reuses_credentials(
        self, _ensure_schema, fetch_one, _find_teacher, find_student
    ):
        fetch_one.side_effect = [None, {'codigo_estud': '1954'}]
        find_student.return_value = {
            'correo': 'estudiante@intec.edu.ec',
            'password_temporal': 'ClaveExistente',
            'credentials_reused': True,
            'credential_source': 'CORREOS_ESTUD_INTEC',
        }

        result = inspect_teacher_identity_by_cedula('1104371859')

        self.assertEqual(result['profiles'], ['ESTUDIANTE'])
        self.assertTrue(result['credentials_reused'])
        self.assertEqual(result['correo_intec'], 'estudiante@intec.edu.ec')

    @patch('dashboard_auth.teacher_enrollment.complement_version', return_value='v5')
    @patch('dashboard_auth.teacher_enrollment.complement_connection')
    def test_rejects_more_than_three_teachers_in_course(self, complement_db, _version):
        cursor = MagicMock()
        cursor.fetchone.return_value = (3, 0)
        complement_db.return_value.cursor.return_value.__enter__.return_value = cursor

        with self.assertRaisesRegex(TeacherEnrollmentError, 'máximo de tres'):
            _ensure_complement_teacher_capacity(corte_id=7, codigo_doc=99)

    @patch('dashboard_auth.teacher_enrollment.create_microsoft365_teacher_user')
    @patch('dashboard_auth.teacher_enrollment._find_existing_teacher_credentials')
    def test_existing_identity_does_not_create_microsoft_account(self, find_credentials, create_user):
        find_credentials.return_value = {
            'correo': 'docente@intec.edu.ec',
            'password_temporal': 'ClaveExistente',
            'credentials_reused': True,
            'reused_teacher_credentials': True,
            'codigo_doc': '15',
            'codigo_usuario': '81',
        }

        identity, microsoft_result = _prepare_teacher_credentials(
            nombre='Docente Existente',
            cedula='1104371859',
        )

        create_user.assert_not_called()
        self.assertEqual(identity['password_temporal'], 'ClaveExistente')
        self.assertTrue(microsoft_result['reused'])
        self.assertFalse(microsoft_result['created'])

    @patch('dashboard_auth.teacher_enrollment.create_microsoft365_teacher_user')
    @patch('dashboard_auth.teacher_enrollment._find_existing_student_credentials')
    @patch('dashboard_auth.teacher_enrollment._find_existing_teacher_credentials', return_value=None)
    def test_teacher_reuses_student_credentials_without_creating_microsoft_account(
        self, _find_teacher, find_student, create_user
    ):
        find_student.return_value = {
            'correo': 'estudiante@intec.edu.ec',
            'password_temporal': 'ClaveExistente',
            'credentials_reused': True,
            'reused_student_credentials': True,
            'reused_teacher_credentials': False,
            'codigo_estud': '1954',
        }

        identity, microsoft_result = _prepare_teacher_credentials(
            nombre='Estudiante Docente',
            cedula='1104371859',
        )

        create_user.assert_not_called()
        self.assertEqual(identity['correo'], 'estudiante@intec.edu.ec')
        self.assertTrue(microsoft_result['reused'])

    @patch('dashboard_auth.teacher_enrollment._send_teacher_credentials_email')
    @patch('dashboard_auth.teacher_enrollment._upsert_teacher_user')
    @patch('dashboard_auth.teacher_enrollment._upsert_teacher_record')
    @patch('dashboard_auth.teacher_enrollment._prepare_teacher_credentials')
    @patch('dashboard_auth.teacher_enrollment._clean_teacher_profile_payload')
    @patch('dashboard_auth.teacher_enrollment._ensure_teacher_schema')
    @patch('dashboard_auth.teacher_enrollment.transaction.atomic', return_value=nullcontext())
    def test_teacher_entry_reuses_and_emails_existing_credentials(
        self,
        _atomic,
        _ensure_schema,
        clean_payload,
        prepare_credentials,
        upsert_teacher,
        upsert_user,
        send_email,
    ):
        clean_payload.return_value = {
            'nombre': 'Docente Existente',
            'cedula': '1104371859',
            'email': 'personal@example.com',
        }
        prepare_credentials.return_value = (
            {
                'correo': 'docente@intec.edu.ec',
                'password_temporal': 'ClaveExistente',
                'credentials_reused': True,
                'reused_teacher_credentials': True,
                'codigo_usuario': '81',
            },
            {'ok': True, 'created': False, 'reused': True},
        )
        upsert_teacher.return_value = {'codigo_doc': '15'}
        send_email.return_value = {'sent': True}

        result = create_teacher_entry_and_send_credentials({})

        upsert_user.assert_not_called()
        self.assertTrue(send_email.call_args.kwargs['credentials_reused'])
        self.assertEqual(send_email.call_args.kwargs['password'], 'ClaveExistente')
        self.assertTrue(result['credentials_reused'])
        self.assertTrue(result['email_result']['sent'])
