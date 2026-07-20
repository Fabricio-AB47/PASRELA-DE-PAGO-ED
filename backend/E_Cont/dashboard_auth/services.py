from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.auth.hashers import check_password, identify_hasher
from django.utils.crypto import constant_time_compare
from django.db import connection


class AuthError(Exception):
    pass


class InvalidScopeError(AuthError):
    pass


class InactiveUserError(AuthError):
    pass


class RoleSelectionRequired(AuthError):
    def __init__(self, roles: list[dict[str, str]]):
        self.roles = roles
        super().__init__('Selecciona si deseas ingresar como docente o administrativo.')


VALID_SCOPES = {'auto', 'student', 'teacher', 'staff'}


@dataclass(frozen=True)
class AuthenticatedUser:
    category: str
    category_label: str
    display_name: str
    login: str
    email: str | None
    status: str
    role_code: int | None
    role_name: str
    summary: list[dict[str, str]]
    modules: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            'category': self.category,
            'category_label': self.category_label,
            'display_name': self.display_name,
            'login': self.login,
            'email': self.email,
            'status': self.status,
            'role': {
                'code': self.role_code,
                'name': self.role_name,
            },
            'summary': self.summary,
            'modules': self.modules,
        }


def authenticate_user(identifier: str, password: str, scope: str = 'auto') -> AuthenticatedUser:
    clean_identifier = _clean_text(identifier)
    clean_password = password or ''
    clean_scope = (scope or 'auto').strip().lower()

    if clean_scope not in VALID_SCOPES:
        raise InvalidScopeError('El tipo de acceso solicitado no es válido.')

    if not clean_identifier or not clean_password:
        raise AuthError('Debes enviar el usuario y la contraseña.')

    matchers = {
        'student': _find_student,
        'teacher': _find_teacher,
        'staff': _find_staff,
    }

    if clean_scope == 'auto':
        staff_user = _find_staff(clean_identifier, clean_password)
        teacher_user = _find_teacher(clean_identifier, clean_password)
        if staff_user is not None and teacher_user is not None:
            raise RoleSelectionRequired([
                {'scope': 'staff', 'label': 'Administrativo'},
                {'scope': 'teacher', 'label': 'Docente'},
            ])
        if staff_user is not None:
            return staff_user
        if teacher_user is not None:
            return teacher_user
        student_user = _find_student(clean_identifier, clean_password)
        if student_user is not None:
            return student_user
        raise AuthError('No encontramos un usuario válido con esas credenciales.')

    scope_order = {
        'student': ('student',),
        'teacher': ('teacher',),
        'staff': ('staff',),
    }[clean_scope]

    for current_scope in scope_order:
        user = matchers[current_scope](clean_identifier, clean_password)
        if user is not None:
            return user

    raise AuthError('No encontramos un usuario válido con esas credenciales.')


def _find_student(identifier: str, password: str) -> AuthenticatedUser | None:
    query = """
        SELECT
            CAST(codestud AS varchar(50)) AS codestud,
            Nombres,
            CorreoPersonal,
            CorreoIntec,
            Estado,
            CAST(Periodo AS varchar(50)) AS Periodo,
            Password AS stored_password
        FROM dbo.CorreosEstudIntec
        WHERE (
              LOWER(LTRIM(RTRIM(ISNULL(CorreoPersonal, '')))) = LOWER(%s)
              OR LOWER(LTRIM(RTRIM(ISNULL(CorreoIntec, '')))) = LOWER(%s)
              OR LTRIM(RTRIM(CAST(codestud AS varchar(50)))) = %s
          )
        ORDER BY
            CASE
                WHEN LOWER(LTRIM(RTRIM(ISNULL(CorreoIntec, '')))) = LOWER(%s) THEN 0
                WHEN LOWER(LTRIM(RTRIM(ISNULL(CorreoPersonal, '')))) = LOWER(%s) THEN 1
                ELSE 2
            END,
            Periodo DESC
    """
    rows = [
        row
        for row in _fetch_all(query, [identifier, identifier, identifier, identifier, identifier])
        if _password_matches(row.get('stored_password'), password)
    ]
    if not rows:
        return None

    row = _select_active_student_row(rows)
    if row is None:
        raise InactiveUserError(_build_student_inactive_message(rows))

    status = _clean_text(row.get('Estado')) or 'Sin estado'
    intec_email = _clean_text(row.get('CorreoIntec'))
    personal_email = _clean_text(row.get('CorreoPersonal'))
    student_code = _clean_text(row.get('codestud')) or identifier
    display_name = _clean_text(row.get('Nombres')) or student_code

    return AuthenticatedUser(
        category='student',
        category_label='Estudiante',
        display_name=display_name,
        login=intec_email or personal_email or student_code,
        email=intec_email or personal_email,
        status=status,
        role_code=None,
        role_name='ESTUDIANTE',
        summary=[
            {'label': 'Matrícula', 'value': student_code},
            {'label': 'Correo INTEC', 'value': intec_email or 'No disponible'},
            {'label': 'Período', 'value': _clean_text(row.get('Periodo')) or 'No disponible'},
        ],
        modules=[
            {
                'title': 'Estado de cuenta',
                'description': 'Consulta balances, cargos pendientes y movimientos asociados a tus pagos.',
            },
            {
                'title': 'Comprobantes',
                'description': 'Descarga recibos y confirma transacciones aplicadas a tu perfil estudiantil.',
            },
            {
                'title': 'Solicitudes',
                'description': 'Gestiona incidencias, validaciones y seguimiento administrativo desde un solo lugar.',
            },
        ],
    )


def _find_teacher(identifier: str, password: str) -> AuthenticatedUser | None:
    query = """
        SELECT
            CAST(Codigo_Usuario AS varchar(50)) AS Codigo_Usuario,
            cedula,
            login,
            Estado,
            CAST(tipo_usuario AS varchar(50)) AS tipo_usuario,
            password AS stored_password
        FROM dbo.USUARIOS
        WHERE (
              LOWER(LTRIM(RTRIM(ISNULL(login, '')))) = LOWER(%s)
              OR LTRIM(RTRIM(ISNULL(cedula, ''))) = %s
              OR LTRIM(RTRIM(CAST(Codigo_Usuario AS varchar(50)))) = %s
          )
        ORDER BY
            CASE
                WHEN LOWER(LTRIM(RTRIM(ISNULL(login, '')))) = LOWER(%s) THEN 0
                WHEN LTRIM(RTRIM(ISNULL(cedula, ''))) = %s THEN 1
                ELSE 2
            END
    """
    rows = _fetch_all(query, [identifier, identifier, identifier, identifier, identifier])
    row = next((candidate for candidate in rows if _password_matches(candidate.get('stored_password'), password)), None)
    if row is None:
        return None

    status = _clean_text(row.get('Estado')) or 'Sin estado'
    if not _is_active_teacher(status):
        raise InactiveUserError('La cuenta docente no está activa para ingresar al dashboard.')

    teacher_login = _clean_text(row.get('login')) or identifier
    teacher_code = _clean_text(row.get('Codigo_Usuario')) or 'No disponible'
    teacher_id = _clean_text(row.get('cedula')) or 'No disponible'

    return AuthenticatedUser(
        category='teacher',
        category_label='Docente',
        display_name=teacher_login,
        login=teacher_login,
        email=teacher_login if '@' in teacher_login else None,
        status=status,
        role_code=_to_int(row.get('tipo_usuario')),
        role_name='DOCENTE',
        summary=[
            {'label': 'Código', 'value': teacher_code},
            {'label': 'Cédula', 'value': teacher_id},
            {'label': 'Tipo docente', 'value': _clean_text(row.get('tipo_usuario')) or 'No disponible'},
        ],
        modules=[
            {
                'title': 'Cobros asignados',
                'description': 'Visualiza operaciones, cuotas y transacciones vinculadas a tu actividad docente.',
            },
            {
                'title': 'Seguimiento',
                'description': 'Monitorea pagos pendientes, validaciones y confirmaciones dentro del ciclo académico.',
            },
            {
                'title': 'Historial',
                'description': 'Consulta registros de acceso y movimientos consolidados en SQL Server.',
            },
        ],
    )


def _find_staff(identifier: str, password: str) -> AuthenticatedUser | None:
    query = """
        SELECT
            S.login,
            S.nombres,
            S.estado,
            S.email,
            CAST(S.id_usuarios AS varchar(50)) AS id_usuarios,
            CAST(S.coordcarrera AS varchar(50)) AS coordcarrera,
            CAST(S.codprovincia AS varchar(50)) AS codprovincia,
            NULLIF(LTRIM(RTRIM(S.tp_us)), '') AS tp_us,
            RTRIM(TU.detalle_tipo_us) AS role_name,
            S.password AS stored_password
        FROM dbo.USUARIO_SIS AS S
        LEFT JOIN dbo.TIPO_USUARIO AS TU
          ON TU.Codigo_tipo_us = TRY_CONVERT(int, NULLIF(LTRIM(RTRIM(S.tp_us)), ''))
        WHERE (
              LOWER(LTRIM(RTRIM(ISNULL(S.login, '')))) = LOWER(%s)
              OR LOWER(LTRIM(RTRIM(ISNULL(S.email, '')))) = LOWER(%s)
              OR LTRIM(RTRIM(CAST(S.id_usuarios AS varchar(50)))) = %s
          )
        ORDER BY
            CASE
                WHEN LOWER(LTRIM(RTRIM(ISNULL(S.login, '')))) = LOWER(%s) THEN 0
                WHEN LOWER(LTRIM(RTRIM(ISNULL(S.email, '')))) = LOWER(%s) THEN 1
                ELSE 2
            END
    """
    rows = _fetch_all(
        query,
        [
            identifier,
            identifier,
            identifier,
            identifier,
            identifier,
        ],
    )
    row = next((candidate for candidate in rows if _password_matches(candidate.get('stored_password'), password)), None)
    if row is None:
        return None

    status = _clean_text(row.get('estado')) or 'Sin estado'
    if not _is_active_staff(status):
        raise InactiveUserError('La cuenta administrativa no está activa para ingresar al dashboard.')

    role_code = _resolve_staff_role_code(row)
    role_name = _clean_text(row.get('role_name')) or _resolve_staff_role_name(role_code)
    staff_login = _clean_text(row.get('login')) or identifier
    display_name = _clean_text(row.get('nombres')) or staff_login
    email = _clean_text(row.get('email')) or (staff_login if '@' in staff_login else None)

    return AuthenticatedUser(
        category='staff',
        category_label='Administrativo',
        display_name=display_name,
        login=staff_login,
        email=email,
        status=status,
        role_code=role_code,
        role_name=role_name,
        summary=[
            {'label': 'ID interno', 'value': _clean_text(row.get('id_usuarios')) or 'No disponible'},
            {'label': 'Rol', 'value': role_name},
            {'label': 'Coordinacion', 'value': _clean_text(row.get('coordcarrera')) or 'No disponible'},
        ],
        modules=_staff_modules(role_name),
    )


def _resolve_staff_role_code(row: dict[str, Any]) -> int | None:
    parsed = _to_int(row.get('tp_us'))
    return parsed if parsed is not None and parsed > 0 else None


def _resolve_staff_role_name(role_code: int | None) -> str:
    if role_code is None:
        return 'SIN ROL ASIGNADO'

    query = """
        SELECT TOP (1) RTRIM(detalle_tipo_us) AS detalle_tipo_us
        FROM dbo.TIPO_USUARIO
        WHERE Codigo_tipo_us = %s
    """
    row = _fetch_one(query, [role_code])
    if row is None:
        return 'SIN ROL ASIGNADO'
    return _clean_text(row.get('detalle_tipo_us')) or 'SIN ROL ASIGNADO'


def _staff_modules(role_name: str) -> list[dict[str, str]]:
    modules = {
        'ADMINISTRADOR': [
            {'title': 'Control general', 'description': 'Administra usuarios, accesos, catálogos y reglas del dashboard.'},
            {'title': 'Conciliacion', 'description': 'Supervisa pagos, estados y trazabilidad operativa en tiempo real.'},
            {'title': 'Configuracion', 'description': 'Ajusta parametros, conexiones y vistas institucionales.'},
        ],
        'FINANCIERO': [
            {'title': 'Tesoreria', 'description': 'Monitorea cobros, validaciones bancarias y conciliaciones pendientes.'},
            {'title': 'Cartera', 'description': 'Consulta balances, cuentas por cobrar y confirmaciones de pago.'},
            {'title': 'Reportes', 'description': 'Genera vistas consolidadas para cierres y seguimiento financiero.'},
        ],
        'BIENESTAR': [
            {'title': 'Seguimiento estudiantil', 'description': 'Visualiza estados, incidencias y apoyos asociados al estudiante.'},
            {'title': 'Alertas', 'description': 'Prioriza casos con bloqueos o validaciones pendientes.'},
            {'title': 'Atencion', 'description': 'Centraliza solicitudes y acompanamiento institucional.'},
        ],
        'ACADEMICO': [
            {'title': 'Carga académica', 'description': 'Relaciona períodos, inscripciones y eventos de pago por cohorte.'},
            {'title': 'Validaciones', 'description': 'Revisa estados y desbloqueos para continuidad académica.'},
            {'title': 'Indicadores', 'description': 'Consulta paneles de avance y cumplimiento por programa.'},
        ],
        'ADMISIONES': [
            {'title': 'Prospectos', 'description': 'Gestiona cobros y confirmaciones vinculadas a nuevos ingresos.'},
            {'title': 'Documentos', 'description': 'Centraliza revisiones, incidencias y pasos de validacion.'},
            {'title': 'Embudo comercial', 'description': 'Sigue conversiones por canal, sede y período.'},
        ],
        'RECTOR': [
            {'title': 'Vista ejecutiva', 'description': 'Resume indicadores criticos de pagos, cartera y operacion.'},
            {'title': 'Tendencias', 'description': 'Compara períodos y lectura institucional consolidada.'},
            {'title': 'Decisiones', 'description': 'Accede a focos de riesgo y oportunidades de mejora.'},
        ],
        'VICERRECTOR': [
            {'title': 'Operacion central', 'description': 'Supervisa flujos intermedios y cumplimiento de procesos clave.'},
            {'title': 'Analitica', 'description': 'Consulta cortes por unidad, estado y rendimiento.'},
            {'title': 'Seguimiento', 'description': 'Prioriza pendientes de alto impacto para cada equipo.'},
        ],
        'SOPORTE': [
            {'title': 'Incidentes', 'description': 'Atiende errores, caídas y validaciones técnicas del sistema.'},
            {'title': 'Bitácora', 'description': 'Consulta accesos, eventos y rastreo de transacciones.'},
            {'title': 'Salud del sistema', 'description': 'Monitorea integraciones y conectividad operativa.'},
        ],
        'INVITADO_SOP': [
            {'title': 'Consulta tecnica', 'description': 'Revisa estados operativos con permisos restringidos.'},
            {'title': 'Bitácora visible', 'description': 'Accede a eventos habilitados para soporte externo.'},
            {'title': 'Seguimiento guiado', 'description': 'Da soporte a incidencias bajo supervision interna.'},
        ],
        'SECRETARIA': [
            {'title': 'Gestión académica', 'description': 'Consulta y actualiza los procesos académicos habilitados para secretaría.'},
            {'title': 'Matrículas', 'description': 'Da seguimiento a estudiantes, matrículas y documentación académica.'},
            {'title': 'Certificados', 'description': 'Consulta el estado de certificados y solicitudes institucionales.'},
        ],
    }
    return modules.get(
        role_name,
        [
            {'title': 'Resumen', 'description': 'Consulta la información habilitada según tu cuenta administrativa.'},
            {'title': 'Actividad', 'description': 'Revisa movimientos y estado operativo del tablero.'},
            {'title': 'Accesos', 'description': 'Explora las vistas activas según tu configuración.'},
        ],
    )


def _fetch_one(query: str, params: list[Any]) -> dict[str, Any] | None:
    rows = _fetch_all(query, params)
    if not rows:
        return None
    return rows[0]


def _fetch_all(query: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace('\r', '').replace('\n', '').strip()
    return text or None


def _password_matches(stored_password: Any, supplied_password: str) -> bool:
    stored = str(stored_password or '')
    supplied = str(supplied_password or '')
    if not stored or not supplied:
        return False
    try:
        identify_hasher(stored)
    except ValueError:
        # Compatibilidad temporal con las tablas heredadas. Los registros deben
        # migrarse a hashes de Django/Argon2 y luego eliminar esta rama.
        return constant_time_compare(stored, supplied)
    return check_password(supplied, stored)


def _to_int(value: Any) -> int | None:
    clean_value = _clean_text(value)
    if clean_value is None:
        return None
    try:
        return int(Decimal(clean_value))
    except (InvalidOperation, ValueError):
        return None


def _is_active_student(status: str) -> bool:
    normalized = status.strip().lower()
    return normalized in {'a', 'd'} or normalized.startswith('activo') or normalized.startswith('e continua')


def _select_active_student_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        status = _clean_text(row.get('Estado')) or ''
        if _is_active_student(status):
            return row
    return None


def _build_student_inactive_message(rows: list[dict[str, Any]]) -> str:
    found_apertures = []
    seen = set()

    for row in rows:
        status = _clean_text(row.get('Estado')) or 'Sin estado'
        period = _clean_text(row.get('Periodo')) or 'Sin período'
        aperture = (status, period)
        if aperture in seen:
            continue
        seen.add(aperture)
        found_apertures.append(f'{status} ({period})')

    if not found_apertures:
        return 'La cuenta del estudiante no tiene una apertura habilitada para ingresar al dashboard.'

    apertures_text = ', '.join(found_apertures[:5])
    return (
        'La cuenta del estudiante no tiene una apertura habilitada para ingresar al dashboard. '
        f'Aperturas encontradas: {apertures_text}.'
    )


def _is_active_teacher(status: str) -> bool:
    normalized = status.strip().lower()
    return normalized in {'a', 'activo'}


def _is_active_staff(status: str) -> bool:
    return status.upper() == 'A'
