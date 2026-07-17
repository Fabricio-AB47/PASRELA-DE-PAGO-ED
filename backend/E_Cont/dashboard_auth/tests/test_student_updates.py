from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from dashboard_auth.student_updates import (
    StudentUpdateError,
    _update_enrollment_references,
    get_student_migration_credentials,
    list_students_for_update,
    update_enrolled_student,
)


CURRENT_STUDENT = {
    'CorteEstudianteId': 15,
    'CorteId': 3,
    'CodigoEstud': 1954,
    'Cedula': '1104371859',
    'Nombre': 'ESTUDIANTE ORIGINAL',
    'CorreoPersonal': 'original@example.com',
    'CorreoIntec': 'original@intec.edu.ec',
    'Telefono': '072000000',
    'Movil': '0990000000',
    'Ciudad': 'Loja',
    'Direccion': 'Dirección original',
    'FechaNacimiento': None,
    'Sexo': '1',
    'EstadoParticipacion': 'INSCRITO',
    'EstadoRegistro': 'A',
}


class StudentUpdatesTests(SimpleTestCase):
    @patch('dashboard_auth.student_updates._fetch_all')
    def test_lists_only_serialized_editable_information(self, fetch_all):
        fetch_all.return_value = [CURRENT_STUDENT]

        result = list_students_for_update(3, search='original')

        self.assertEqual(result['total'], 1)
        self.assertEqual(result['students'][0]['codigo_estud'], '1954')
        self.assertEqual(result['students'][0]['correo_personal'], 'original@example.com')
        query, params = fetch_all.call_args.args
        self.assertIn('CORTE_CURSO_ESTUDIANTE', query)
        self.assertEqual(params[0], 3)

    @patch('dashboard_auth.student_updates._sync_student_snapshot')
    @patch('dashboard_auth.student_updates._update_enrollment_references')
    @patch('dashboard_auth.student_updates._update_primary_student')
    @patch('dashboard_auth.student_updates._ensure_identity_is_unique')
    @patch('dashboard_auth.student_updates.transaction.atomic', return_value=nullcontext())
    @patch('dashboard_auth.student_updates._fetch_student')
    def test_updates_primary_identity_without_academic_or_financial_fields(
        self,
        fetch_student,
        _atomic,
        ensure_unique,
        update_primary,
        update_references,
        sync_snapshot,
    ):
        updated = {**CURRENT_STUDENT, 'Nombre': 'ESTUDIANTE ACTUALIZADO'}
        fetch_student.side_effect = [CURRENT_STUDENT, updated]
        sync_snapshot.return_value = {'synced': True, 'message': 'Sincronizado.'}

        result = update_enrolled_student(
            {
                'corte_id': 3,
                'codigo_estud': 1954,
                'nombre': 'ESTUDIANTE ACTUALIZADO',
                'cedula': '1104371859',
                'correo_personal': 'nuevo@example.com',
                'sexo': '2',
            },
            user_login='ADMIN',
        )

        ensure_unique.assert_called_once_with('1104371859', 1954)
        values = update_primary.call_args.args[1]
        self.assertEqual(values['nombre'], 'ESTUDIANTE ACTUALIZADO')
        self.assertNotIn('nota', values)
        self.assertNotIn('pago', values)
        update_references.assert_called_once()
        self.assertTrue(result['complement_sync']['synced'])

    @patch('dashboard_auth.student_updates._fetch_student', return_value=CURRENT_STUDENT)
    def test_rejects_invalid_email(self, _fetch_student):
        with self.assertRaisesMessage(StudentUpdateError, 'correo personal'):
            update_enrolled_student(
                {
                    'corte_id': 3,
                    'codigo_estud': 1954,
                    'correo_personal': 'correo-invalido',
                }
            )

    @patch('dashboard_auth.student_updates._fetch_student', return_value=None)
    def test_rejects_student_outside_selected_cut(self, _fetch_student):
        with self.assertRaisesMessage(StudentUpdateError, 'no está matriculado'):
            update_enrolled_student({'corte_id': 99, 'codigo_estud': 1954})

    @patch('dashboard_auth.student_updates._fetch_one')
    @patch('dashboard_auth.student_updates._fetch_student', return_value=CURRENT_STUDENT)
    def test_loads_migration_credentials_only_for_enrolled_student(self, _fetch_student, fetch_one):
        fetch_one.return_value = {
            'codestud': 1954,
            'Nombres': 'ESTUDIANTE ORIGINAL',
            'CorreoPersonal': 'original@example.com',
            'CorreoIntec': 'original@intec.edu.ec',
            'Password': 'ClaveTemporal',
            'Estado': 'ACTIVO',
        }

        result = get_student_migration_credentials(3, 1954)

        self.assertEqual(result['correo_intec'], 'original@intec.edu.ec')
        self.assertEqual(result['password'], 'ClaveTemporal')
        self.assertIn('CorreosEstudIntec', fetch_one.call_args.args[0])

    @patch('dashboard_auth.student_updates.connection')
    def test_propagates_identity_to_operational_references(self, db_connection):
        cursor = MagicMock()
        db_connection.cursor.return_value.__enter__.return_value = cursor
        values = {
            'nombre': 'ESTUDIANTE ACTUALIZADO',
            'cedula': '1104371859',
            'correo_personal': 'nuevo@example.com',
            'correo_intec': 'nuevo@intec.edu.ec',
            'telefono': '072000000',
            'movil': '0990000000',
            'direccion': 'Dirección actualizada',
        }

        _update_enrollment_references(1954, values, user_login='ADMIN')

        executed_sql = '\n'.join(call.args[0] for call in cursor.execute.call_args_list)
        for table_name in (
            'CORTE_CURSO_ESTUDIANTE',
            'CERTIFICADOS_GENERADOS',
            'CERTIFICADO_CORTE_ESTUDIANTE',
            'FIN_SOLICITUD_PAGO_TARJETA',
            'DATOSFACTURA',
            'PREINSCRIPCION',
            'prematricula_homologacion',
            'SEGUIMIENTO_ESTUDIANTE',
        ):
            self.assertIn(table_name, executed_sql)
