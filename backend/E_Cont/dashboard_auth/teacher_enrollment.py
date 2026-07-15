from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from django.db import connection, transaction

from .continuing_education import complement_database_name, sync_teacher_assignment_to_complement
from .microsoft365 import (
    Microsoft365Error,
    Microsoft365ValidationError,
    build_intec_account_identity,
    create_microsoft365_teacher_user,
)
from .payments import PaymentGatewayError, _build_intec_logo_attachment, _send_graph_mail


class TeacherEnrollmentError(Exception):
    pass


DEFAULT_TEACHER_USER_TYPE = 2
DEFAULT_TEACHER_STATUS = 'A'
DEFAULT_PARALLEL = 'A'
DEFAULT_JOURNEY_CODE = 1


def create_teacher_entry_and_send_credentials(
    payload: dict[str, Any],
    *,
    user_login: str = '',
) -> dict[str, Any]:
    _ensure_teacher_schema()
    teacher_payload = _clean_teacher_profile_payload(payload)

    try:
        local_identity = build_intec_account_identity(
            nombre=teacher_payload['nombre'],
            cedula=teacher_payload['cedula'],
        )
    except Microsoft365ValidationError as exc:
        raise TeacherEnrollmentError(str(exc)) from exc

    try:
        microsoft365_user = create_microsoft365_teacher_user(
            {
                'nombre_completo': teacher_payload['nombre'],
                'cedula': teacher_payload['cedula'],
            }
        )
    except Microsoft365Error as exc:
        raise TeacherEnrollmentError(str(exc)) from exc

    institutional_email = str(microsoft365_user.get('correo') or local_identity['correo']).strip()
    password_temporal = local_identity['password_temporal']

    with transaction.atomic():
        teacher_record = _upsert_teacher_record(
            teacher_payload,
            institutional_email=institutional_email,
        )
        user_record = _upsert_teacher_user(
            cedula=teacher_payload['cedula'],
            login=institutional_email,
            password=password_temporal,
            user_login=user_login,
        )

    email_result = {'sent': False, 'message': 'No ejecutado.'}
    try:
        email_result = _send_teacher_credentials_email(
            recipient_email=teacher_payload['email'],
            recipient_name=teacher_payload['nombre'],
            intec_email=institutional_email,
            password=password_temporal,
            assignment={},
        )
    except PaymentGatewayError as exc:
        email_result = {
            'sent': False,
            'message': f'Credenciales generadas, pero no fue posible enviarlas por correo: {str(exc)}',
        }

    return {
        'teacher': teacher_record,
        'user': user_record,
        'credentials': {
            'correo_intec': institutional_email,
            'password_temporal': password_temporal,
        },
        'microsoft365': {
            'ok': True,
            'message': 'Usuario Microsoft 365 creado y licenciado como profesor.',
            'user': microsoft365_user,
        },
        'email_result': email_result,
    }


def list_teacher_candidates(search: Any = '', limit: Any = 100) -> list[dict[str, Any]]:
    _ensure_teacher_schema()
    clean_search = _clean_text(search)
    clean_digits = re.sub(r'\D+', '', clean_search)
    max_results = min(max(_safe_int(limit, default=100), 1), 300)
    tipo_usuario = _teacher_user_type()
    params: list[Any] = [tipo_usuario]
    filters = ["NULLIF(LTRIM(RTRIM(ISNULL(D.apellidos_nombre, ''))), '') IS NOT NULL"]

    if clean_search:
        like_value = f'%{clean_search}%'
        filters.append(
            """
            (
                LTRIM(RTRIM(ISNULL(D.apellidos_nombre, ''))) LIKE %s
                OR REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(D.cedula_doc, ''))), '-', ''), ' ', '') LIKE %s
                OR LTRIM(RTRIM(ISNULL(D.correo, ''))) LIKE %s
                OR LTRIM(RTRIM(ISNULL(D.correop, ''))) LIKE %s
            )
            """
        )
        params.extend([like_value, f'%{clean_digits or clean_search}%', like_value, like_value])

    query = f"""
        SELECT TOP ({max_results})
            CAST(D.codigo_doc AS varchar(50)) AS codigo_doc,
            REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(D.cedula_doc, ''))), '-', ''), ' ', '') AS cedula,
            LTRIM(RTRIM(ISNULL(D.apellidos_nombre, ''))) AS nombre,
            LTRIM(RTRIM(ISNULL(D.correop, ''))) AS correo_personal,
            LTRIM(RTRIM(ISNULL(D.correo, ''))) AS correo_intec,
            LTRIM(RTRIM(ISNULL(D.telefono, ''))) AS telefono,
            LTRIM(RTRIM(ISNULL(D.movil, ''))) AS movil,
            LTRIM(RTRIM(ISNULL(CAST(D.Direccion AS varchar(500)), ''))) AS direccion,
            CAST(U.Codigo_Usuario AS varchar(50)) AS codigo_usuario,
            LTRIM(RTRIM(ISNULL(U.login, ''))) AS login,
            LTRIM(RTRIM(ISNULL(U.Estado, ''))) AS estado_usuario
        FROM dbo.DATOSDOCENTE D
        LEFT JOIN dbo.USUARIOS U
          ON LTRIM(RTRIM(U.cedula)) = LTRIM(RTRIM(D.cedula_doc))
         AND CAST(U.tipo_usuario AS int) = %s
        WHERE {' AND '.join(filters)}
        ORDER BY D.apellidos_nombre ASC, D.codigo_doc DESC
    """
    return [_serialize_teacher_candidate(row) for row in _fetch_all(query, params)]


def enroll_existing_teacher(
    payload: dict[str, Any],
    *,
    user_login: str = '',
) -> dict[str, Any]:
    _ensure_teacher_schema()
    assignment_payload = _clean_teacher_assignment_payload(payload)
    teacher = _fetch_teacher_for_assignment(payload)

    with transaction.atomic():
        if assignment_payload.get('skip_primary_assignment'):
            assignment = _build_cut_only_assignment(
                codigo_doc=teacher['codigo_doc'],
                teacher_payload=assignment_payload,
            )
        else:
            assignment = _upsert_teacher_assignment(
                codigo_doc=teacher['codigo_doc'],
                teacher_payload=assignment_payload,
            )

    continuing_education = _sync_teacher_assignment_to_complement(
        teacher=teacher,
        assignment=assignment,
        user_login=user_login,
    )

    return {
        'teacher': teacher,
        'assignment': assignment,
        'continuing_education': continuing_education,
        'user_login': user_login or 'SISTEMA',
    }


def enroll_teacher_and_send_credentials(
    payload: dict[str, Any],
    *,
    user_login: str = '',
) -> dict[str, Any]:
    _ensure_teacher_schema()
    teacher_payload = _clean_teacher_payload(payload)

    try:
        local_identity = build_intec_account_identity(
            nombre=teacher_payload['nombre'],
            cedula=teacher_payload['cedula'],
        )
    except Microsoft365ValidationError as exc:
        raise TeacherEnrollmentError(str(exc)) from exc

    try:
        microsoft365_user = create_microsoft365_teacher_user(
            {
                'nombre_completo': teacher_payload['nombre'],
                'cedula': teacher_payload['cedula'],
            }
        )
    except Microsoft365Error as exc:
        raise TeacherEnrollmentError(str(exc)) from exc

    institutional_email = str(microsoft365_user.get('correo') or local_identity['correo']).strip()
    password_temporal = local_identity['password_temporal']

    with transaction.atomic():
        teacher_record = _upsert_teacher_record(
            teacher_payload,
            institutional_email=institutional_email,
        )
        user_record = _upsert_teacher_user(
            cedula=teacher_payload['cedula'],
            login=institutional_email,
            password=password_temporal,
            user_login=user_login,
        )
        assignment = _upsert_teacher_assignment(
            codigo_doc=teacher_record['codigo_doc'],
            teacher_payload=teacher_payload,
        )

    continuing_education = _sync_teacher_assignment_to_complement(
        teacher=teacher_record,
        assignment=assignment,
        user_login=user_login,
    )

    email_result = {'sent': False, 'message': 'No ejecutado.'}
    try:
        email_result = _send_teacher_credentials_email(
            recipient_email=teacher_payload['email'],
            recipient_name=teacher_payload['nombre'],
            intec_email=institutional_email,
            password=password_temporal,
            assignment=assignment,
        )
    except PaymentGatewayError as exc:
        email_result = {
            'sent': False,
            'message': f'Credenciales generadas, pero no fue posible enviarlas por correo: {str(exc)}',
        }

    return {
        'teacher': teacher_record,
        'user': user_record,
        'assignment': assignment,
        'continuing_education': continuing_education,
        'credentials': {
            'correo_intec': institutional_email,
            'password_temporal': password_temporal,
        },
        'microsoft365': {
            'ok': True,
            'message': 'Usuario Microsoft 365 creado y licenciado como profesor.',
            'user': microsoft365_user,
        },
        'email_result': email_result,
    }


def _clean_teacher_payload(payload: dict[str, Any]) -> dict[str, str]:
    nombre = _clean_text(payload.get('nombre') or payload.get('nombre_completo'))
    cedula = re.sub(r'\D+', '', _clean_text(payload.get('cedula')))
    email = _clean_text(payload.get('email') or payload.get('correo_personal')).lower()
    telefono = _clean_text(payload.get('telefono'))
    movil = _clean_text(payload.get('movil') or telefono)
    direccion = _clean_text(payload.get('direccion'))
    cod_anio_basica = _clean_text(payload.get('cod_anio_basica'))
    codigo_materia = _clean_text(payload.get('codigo_materia'))
    codigo_periodo = _clean_text(payload.get('codigo_periodo'))
    paralelo = (_clean_text(payload.get('paralelo')) or DEFAULT_PARALLEL).upper()
    cod_jornada = _safe_int(payload.get('cod_jornada') or payload.get('codigo_jornada'), default=DEFAULT_JOURNEY_CODE)

    if not nombre:
        raise TeacherEnrollmentError('Debes ingresar el nombre completo del docente.')
    if not cedula or not re.fullmatch(r'\d{6,20}', cedula):
        raise TeacherEnrollmentError('La cédula del docente debe contener solo números (entre 6 y 20 dígitos).')
    if not email or '@' not in email:
        raise TeacherEnrollmentError('Debes ingresar un correo personal válido para enviar credenciales.')
    if not cod_anio_basica:
        raise TeacherEnrollmentError('Debes seleccionar la carrera para matricular al docente.')
    if not codigo_materia:
        raise TeacherEnrollmentError('Debes seleccionar la materia para matricular al docente.')
    if not codigo_periodo:
        raise TeacherEnrollmentError('Debes seleccionar el período para matricular al docente.')
    if len(paralelo) > 4:
        raise TeacherEnrollmentError('El paralelo no puede superar 4 caracteres.')
    if cod_jornada <= 0:
        raise TeacherEnrollmentError('El código de jornada debe ser numérico y mayor que cero.')

    return {
        'nombre': nombre,
        'cedula': cedula,
        'email': email,
        'telefono': telefono,
        'movil': movil,
        'direccion': direccion,
        'cod_anio_basica': cod_anio_basica,
        'codigo_materia': codigo_materia,
        'codigo_periodo': codigo_periodo,
        'paralelo': paralelo,
        'cod_jornada': str(cod_jornada),
    }


def _clean_teacher_profile_payload(payload: dict[str, Any]) -> dict[str, str]:
    nombre = _clean_text(payload.get('nombre') or payload.get('nombre_completo'))
    cedula = re.sub(r'\D+', '', _clean_text(payload.get('cedula')))
    email = _clean_text(payload.get('email') or payload.get('correo_personal')).lower()
    telefono = _clean_text(payload.get('telefono'))
    movil = _clean_text(payload.get('movil') or telefono)
    direccion = _clean_text(payload.get('direccion'))

    if not nombre:
        raise TeacherEnrollmentError('Debes ingresar el nombre completo del docente.')
    if not cedula or not re.fullmatch(r'\d{6,20}', cedula):
        raise TeacherEnrollmentError('La cédula del docente debe contener solo números (entre 6 y 20 dígitos).')
    if not email or '@' not in email:
        raise TeacherEnrollmentError('Debes ingresar un correo personal válido para enviar credenciales.')

    return {
        'nombre': nombre,
        'cedula': cedula,
        'email': email,
        'telefono': telefono,
        'movil': movil,
        'direccion': direccion,
    }


def _clean_teacher_assignment_payload(payload: dict[str, Any]) -> dict[str, str]:
    corte_id = _clean_text(payload.get('corte_id') or payload.get('CorteId'))
    cut = _fetch_assignment_cut(corte_id) if corte_id else None
    cod_anio_basica = _clean_text(payload.get('cod_anio_basica'))
    codigo_materia = _clean_text(payload.get('codigo_materia'))
    codigo_periodo = _clean_text(payload.get('codigo_periodo'))
    paralelo = (_clean_text(payload.get('paralelo')) or DEFAULT_PARALLEL).upper()
    cod_jornada = _safe_int(payload.get('cod_jornada') or payload.get('codigo_jornada'), default=DEFAULT_JOURNEY_CODE)

    if cut:
        tipo_oferta = _clean_text(cut.get('TipoOferta')).upper()
        cut_cod_anio = _clean_text(cut.get('Cod_AnioBasica'))
        cut_codigo_materia = _clean_text(cut.get('CodigoMateria'))
        cut_codigo_periodo = _clean_text(cut.get('CodigoPeriodo'))
        cut_cod_curso = _clean_text(cut.get('CodCurso'))

        cod_anio_basica = cod_anio_basica or cut_cod_anio
        codigo_materia = codigo_materia or cut_codigo_materia or cut_cod_curso
        codigo_periodo = codigo_periodo or cut_codigo_periodo

        can_write_primary_assignment = bool(cut_cod_anio and cut_codigo_materia and cut_codigo_periodo)
        if tipo_oferta == 'CARRERA' and not can_write_primary_assignment:
            raise TeacherEnrollmentError('La corte seleccionada no tiene carrera, materia y período completos.')
        if len(paralelo) > 4:
            raise TeacherEnrollmentError('El paralelo no puede superar 4 caracteres.')
        if cod_jornada <= 0:
            raise TeacherEnrollmentError('El código de jornada debe ser numérico y mayor que cero.')

        return {
            'corte_id': corte_id,
            'tipo_oferta': tipo_oferta,
            'cod_anio_basica': cod_anio_basica,
            'codigo_materia': codigo_materia,
            'codigo_periodo': codigo_periodo,
            'cod_curso': cut_cod_curso,
            'carrera': _clean_text(cut.get('Carrera')),
            'materia': _clean_text(cut.get('MateriaPensum') or cut.get('CursoEduContinua') or cut.get('NombreCorte')),
            'periodo': _clean_text(cut.get('Periodo') or cut.get('NombreCorte')),
            'nombre_corte': _clean_text(cut.get('NombreCorte')),
            'estado_corte': _clean_text(cut.get('EstadoCorte')),
            'paralelo': paralelo,
            'cod_jornada': str(cod_jornada),
            'skip_primary_assignment': not can_write_primary_assignment,
        }

    if not cod_anio_basica:
        raise TeacherEnrollmentError('Debes seleccionar la carrera para matricular al docente.')
    if not codigo_materia:
        raise TeacherEnrollmentError('Debes seleccionar la materia para matricular al docente.')
    if not codigo_periodo:
        raise TeacherEnrollmentError('Debes seleccionar el período para matricular al docente.')
    if len(paralelo) > 4:
        raise TeacherEnrollmentError('El paralelo no puede superar 4 caracteres.')
    if cod_jornada <= 0:
        raise TeacherEnrollmentError('El código de jornada debe ser numérico y mayor que cero.')

    return {
        'cod_anio_basica': cod_anio_basica,
        'codigo_materia': codigo_materia,
        'codigo_periodo': codigo_periodo,
        'paralelo': paralelo,
        'cod_jornada': str(cod_jornada),
    }


def _fetch_assignment_cut(corte_id: str) -> dict[str, Any] | None:
    if not corte_id:
        return None
    row = _fetch_one(
        """
        SELECT TOP (1)
            CorteId,
            TipoOferta,
            NombreCorte,
            EstadoCorte,
            Cod_AnioBasica,
            Carrera,
            CodigoPeriodo,
            Periodo,
            CodigoMateria,
            MateriaPensum,
            CodCurso,
            CursoEduContinua
        FROM dbo.VW_CORTE_RESUMEN
        WHERE CAST(CorteId AS varchar(30)) = %s
        """,
        [corte_id],
    )
    if not row:
        raise TeacherEnrollmentError('No se encontró la corte seleccionada.')
    return row


def _build_cut_only_assignment(
    *,
    codigo_doc: str,
    teacher_payload: dict[str, str],
) -> dict[str, str]:
    return {
        'action': 'sincronizada_corte',
        'codigo_doc': str(codigo_doc),
        'corte_id': teacher_payload.get('corte_id', ''),
        'tipo_oferta': teacher_payload.get('tipo_oferta', ''),
        'cod_anio_basica': teacher_payload.get('cod_anio_basica', ''),
        'carrera': teacher_payload.get('carrera', ''),
        'codigo_materia': teacher_payload.get('codigo_materia', ''),
        'materia': teacher_payload.get('materia', ''),
        'codigo_periodo': teacher_payload.get('codigo_periodo', ''),
        'periodo': teacher_payload.get('periodo', ''),
        'cod_curso': teacher_payload.get('cod_curso', ''),
        'nombre_corte': teacher_payload.get('nombre_corte', ''),
        'paralelo': teacher_payload.get('paralelo', DEFAULT_PARALLEL),
        'cod_jornada': teacher_payload.get('cod_jornada', str(DEFAULT_JOURNEY_CODE)),
        'primary_assignment_skipped': True,
    }


def _upsert_teacher_record(
    teacher_payload: dict[str, str],
    *,
    institutional_email: str,
) -> dict[str, str]:
    cedula = teacher_payload['cedula']
    existing = _fetch_one(
        """
        SELECT TOP (1)
            CAST(codigo_doc AS varchar(50)) AS codigo_doc
        FROM dbo.DATOSDOCENTE
        WHERE REPLACE(REPLACE(LTRIM(RTRIM(cedula_doc)), '-', ''), ' ', '') = %s
        ORDER BY codigo_doc DESC
        """,
        [cedula],
    )

    if existing:
        codigo_doc = str(existing.get('codigo_doc') or '').strip()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE dbo.DATOSDOCENTE
                SET apellidos_nombre = %s,
                    correo = %s,
                    correop = %s,
                    telefono = %s,
                    movil = %s,
                    Direccion = %s
                WHERE CAST(codigo_doc AS varchar(50)) = %s
                """,
                [
                    _trim_to_max(teacher_payload['nombre'], 80),
                    _trim_to_max(institutional_email, 50),
                    _trim_to_max(teacher_payload['email'], 100),
                    _trim_to_max(teacher_payload['telefono'], 20),
                    _trim_to_max(teacher_payload['movil'], 40),
                    _trim_to_max(teacher_payload['direccion'], 500),
                    codigo_doc,
                ],
            )
        action = 'actualizado'
    else:
        codigo_doc = _next_numeric_code('DATOSDOCENTE', 'codigo_doc')
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dbo.DATOSDOCENTE (
                    codigo_doc,
                    cedula_doc,
                    apellidos_nombre,
                    correo,
                    telefono,
                    movil,
                    Direccion,
                    evaluador,
                    correop
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, N'NO', %s)
                """,
                [
                    codigo_doc,
                    _trim_to_max(cedula, 15),
                    _trim_to_max(teacher_payload['nombre'], 80),
                    _trim_to_max(institutional_email, 50),
                    _trim_to_max(teacher_payload['telefono'], 20),
                    _trim_to_max(teacher_payload['movil'], 40),
                    _trim_to_max(teacher_payload['direccion'], 500),
                    _trim_to_max(teacher_payload['email'], 100),
                ],
            )
        action = 'creado'

    return {
        'action': action,
        'codigo_doc': str(codigo_doc),
        'cedula': cedula,
        'nombre': teacher_payload['nombre'],
        'correo_personal': teacher_payload['email'],
        'correo_intec': institutional_email,
    }


def _upsert_teacher_user(
    *,
    cedula: str,
    login: str,
    password: str,
    user_login: str,
) -> dict[str, str]:
    tipo_usuario = _teacher_user_type()
    existing = _fetch_one(
        """
        SELECT TOP (1)
            CAST(Codigo_Usuario AS varchar(50)) AS Codigo_Usuario
        FROM dbo.USUARIOS
        WHERE LTRIM(RTRIM(cedula)) = %s
          AND CAST(tipo_usuario AS varchar(20)) = %s
        """,
        [cedula, str(tipo_usuario)],
    )
    description = _trim_to_max(
        f'Credenciales docentes generadas desde dashboard por {user_login or "SISTEMA"}',
        500,
    )

    if existing:
        codigo_usuario = str(existing.get('Codigo_Usuario') or '').strip()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE dbo.USUARIOS
                SET login = %s,
                    password = %s,
                    fecha_ingreso = GETDATE(),
                    CambioClave = 0,
                    Estado = %s,
                    Descripcion = %s
                WHERE LTRIM(RTRIM(cedula)) = %s
                  AND CAST(tipo_usuario AS varchar(20)) = %s
                """,
                [
                    _trim_to_max(login, 100),
                    _trim_to_max(password, 50),
                    DEFAULT_TEACHER_STATUS,
                    description,
                    cedula,
                    str(tipo_usuario),
                ],
            )
        action = 'actualizado'
    else:
        codigo_usuario = _next_numeric_code('USUARIOS', 'Codigo_Usuario')
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dbo.USUARIOS (
                    Codigo_Usuario,
                    cedula,
                    login,
                    password,
                    fecha_ingreso,
                    tipo_usuario,
                    CambioClave,
                    Estado,
                    Descripcion
                )
                VALUES (%s, %s, %s, %s, GETDATE(), %s, 0, %s, %s)
                """,
                [
                    codigo_usuario,
                    _trim_to_max(cedula, 15),
                    _trim_to_max(login, 100),
                    _trim_to_max(password, 50),
                    tipo_usuario,
                    DEFAULT_TEACHER_STATUS,
                    description,
                ],
            )
        action = 'creado'

    return {
        'action': action,
        'codigo_usuario': str(codigo_usuario),
        'tipo_usuario': str(tipo_usuario),
        'login': login,
        'estado': DEFAULT_TEACHER_STATUS,
    }


def _upsert_teacher_assignment(
    *,
    codigo_doc: str,
    teacher_payload: dict[str, str],
) -> dict[str, str]:
    context = _fetch_assignment_context(teacher_payload)
    existing = _fetch_one(
        """
        SELECT TOP (1) 1 AS found
        FROM dbo.CARRERAXDOCENTE
        WHERE CAST(codigo_doc AS varchar(50)) = %s
          AND CAST(cod_Anio_Basica AS varchar(20)) = %s
          AND CAST(codigo_materia AS varchar(50)) = %s
          AND CAST(codigo_periodo AS varchar(20)) = %s
          AND LTRIM(RTRIM(Paralelo)) = %s
          AND CAST(Cod_Jornada AS varchar(20)) = %s
        """,
        [
            str(codigo_doc),
            teacher_payload['cod_anio_basica'],
            teacher_payload['codigo_materia'],
            teacher_payload['codigo_periodo'],
            teacher_payload['paralelo'],
            teacher_payload['cod_jornada'],
        ],
    )

    if existing:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE dbo.CARRERAXDOCENTE
                SET estadoMoodleDoc = 0
                WHERE CAST(codigo_doc AS varchar(50)) = %s
                  AND CAST(cod_Anio_Basica AS varchar(20)) = %s
                  AND CAST(codigo_materia AS varchar(50)) = %s
                  AND CAST(codigo_periodo AS varchar(20)) = %s
                  AND LTRIM(RTRIM(Paralelo)) = %s
                  AND CAST(Cod_Jornada AS varchar(20)) = %s
                """,
                [
                    str(codigo_doc),
                    teacher_payload['cod_anio_basica'],
                    teacher_payload['codigo_materia'],
                    teacher_payload['codigo_periodo'],
                    teacher_payload['paralelo'],
                    teacher_payload['cod_jornada'],
                ],
            )
        action = 'actualizada'
    else:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dbo.CARRERAXDOCENTE (
                    codigo_doc,
                    cod_Anio_Basica,
                    codigo_materia,
                    Paralelo,
                    codigo_periodo,
                    Cod_Jornada,
                    estadoMoodleDoc
                )
                VALUES (%s, %s, %s, %s, %s, %s, 0)
                """,
                [
                    _safe_int(codigo_doc, default=0),
                    _safe_int(teacher_payload['cod_anio_basica'], default=0),
                    _safe_int(teacher_payload['codigo_materia'], default=0),
                    _trim_to_max(teacher_payload['paralelo'], 4),
                    _safe_int(teacher_payload['codigo_periodo'], default=0),
                    _safe_int(teacher_payload['cod_jornada'], default=DEFAULT_JOURNEY_CODE),
                ],
            )
        action = 'creada'

    return {
        'action': action,
        'codigo_doc': str(codigo_doc),
        'corte_id': teacher_payload.get('corte_id', ''),
        'tipo_oferta': teacher_payload.get('tipo_oferta', ''),
        'cod_anio_basica': teacher_payload['cod_anio_basica'],
        'carrera': context.get('carrera', ''),
        'codigo_materia': teacher_payload['codigo_materia'],
        'materia': context.get('materia', ''),
        'codigo_periodo': teacher_payload['codigo_periodo'],
        'periodo': context.get('periodo', ''),
        'cod_curso': teacher_payload.get('cod_curso', ''),
        'nombre_corte': teacher_payload.get('nombre_corte', ''),
        'paralelo': teacher_payload['paralelo'],
        'cod_jornada': teacher_payload['cod_jornada'],
    }


def _fetch_assignment_context(teacher_payload: dict[str, str]) -> dict[str, str]:
    course = _fetch_one(
        """
        SELECT TOP (1)
            LTRIM(RTRIM(ISNULL(P.Nomb_Materia, ''))) AS materia,
            LTRIM(RTRIM(ISNULL(C.Nombre_Basica, ''))) AS carrera
        FROM dbo.PENSUM P
        LEFT JOIN dbo.CARRERAS C
          ON CAST(C.Cod_AnioBasica AS varchar(20)) = CAST(P.Cod_AnioBasica AS varchar(20))
        WHERE CAST(P.Cod_AnioBasica AS varchar(20)) = %s
          AND CAST(P.codigo_materia AS varchar(50)) = %s
        """,
        [teacher_payload['cod_anio_basica'], teacher_payload['codigo_materia']],
    )
    if not course:
        raise TeacherEnrollmentError('No se encontró la materia seleccionada en PENSUM.')

    period = _fetch_one(
        """
        SELECT TOP (1)
            LTRIM(RTRIM(ISNULL(Detalle_Periodo, ''))) AS periodo
        FROM dbo.PERIODO
        WHERE CAST(cod_periodo AS varchar(20)) = %s
        """,
        [teacher_payload['codigo_periodo']],
    )
    if not period:
        raise TeacherEnrollmentError('No se encontró el período seleccionado.')

    return {
        'materia': str(course.get('materia') or '').strip(),
        'carrera': str(course.get('carrera') or '').strip(),
        'periodo': str(period.get('periodo') or '').strip(),
    }


def _fetch_teacher_for_assignment(payload: dict[str, Any]) -> dict[str, Any]:
    nested_teacher = payload.get('teacher') if isinstance(payload.get('teacher'), dict) else {}
    codigo_doc = _clean_text(
        payload.get('codigo_doc')
        or payload.get('teacher_id')
        or nested_teacher.get('codigo_doc')
    )
    cedula = re.sub(
        r'\D+',
        '',
        _clean_text(payload.get('cedula') or nested_teacher.get('cedula')),
    )

    if codigo_doc:
        row = _fetch_one(
            """
            SELECT TOP (1)
                CAST(D.codigo_doc AS varchar(50)) AS codigo_doc,
                REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(D.cedula_doc, ''))), '-', ''), ' ', '') AS cedula,
                LTRIM(RTRIM(ISNULL(D.apellidos_nombre, ''))) AS nombre,
                LTRIM(RTRIM(ISNULL(D.correop, ''))) AS correo_personal,
                LTRIM(RTRIM(ISNULL(D.correo, ''))) AS correo_intec,
                LTRIM(RTRIM(ISNULL(D.telefono, ''))) AS telefono,
                LTRIM(RTRIM(ISNULL(D.movil, ''))) AS movil,
                CAST(U.Codigo_Usuario AS varchar(50)) AS codigo_usuario,
                LTRIM(RTRIM(ISNULL(U.login, ''))) AS login,
                LTRIM(RTRIM(ISNULL(U.Estado, ''))) AS estado_usuario
            FROM dbo.DATOSDOCENTE D
            LEFT JOIN dbo.USUARIOS U
              ON LTRIM(RTRIM(U.cedula)) = LTRIM(RTRIM(D.cedula_doc))
             AND CAST(U.tipo_usuario AS int) = %s
            WHERE CAST(D.codigo_doc AS varchar(50)) = %s
            ORDER BY D.codigo_doc DESC
            """,
            [_teacher_user_type(), codigo_doc],
        )
    elif cedula:
        row = _fetch_one(
            """
            SELECT TOP (1)
                CAST(D.codigo_doc AS varchar(50)) AS codigo_doc,
                REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(D.cedula_doc, ''))), '-', ''), ' ', '') AS cedula,
                LTRIM(RTRIM(ISNULL(D.apellidos_nombre, ''))) AS nombre,
                LTRIM(RTRIM(ISNULL(D.correop, ''))) AS correo_personal,
                LTRIM(RTRIM(ISNULL(D.correo, ''))) AS correo_intec,
                LTRIM(RTRIM(ISNULL(D.telefono, ''))) AS telefono,
                LTRIM(RTRIM(ISNULL(D.movil, ''))) AS movil,
                CAST(U.Codigo_Usuario AS varchar(50)) AS codigo_usuario,
                LTRIM(RTRIM(ISNULL(U.login, ''))) AS login,
                LTRIM(RTRIM(ISNULL(U.Estado, ''))) AS estado_usuario
            FROM dbo.DATOSDOCENTE D
            LEFT JOIN dbo.USUARIOS U
              ON LTRIM(RTRIM(U.cedula)) = LTRIM(RTRIM(D.cedula_doc))
             AND CAST(U.tipo_usuario AS int) = %s
            WHERE REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(D.cedula_doc, ''))), '-', ''), ' ', '') = %s
            ORDER BY D.codigo_doc DESC
            """,
            [_teacher_user_type(), cedula],
        )
    else:
        raise TeacherEnrollmentError('Debes seleccionar o buscar un docente registrado por nombre o cédula.')

    if not row:
        raise TeacherEnrollmentError('No se encontró el docente seleccionado en DATOSDOCENTE.')
    return _serialize_teacher_candidate(row)


def _serialize_teacher_candidate(row: dict[str, Any]) -> dict[str, Any]:
    correo_intec = _clean_text(row.get('correo_intec') or row.get('login'))
    return {
        'codigo_doc': _clean_text(row.get('codigo_doc')),
        'cedula': re.sub(r'\D+', '', _clean_text(row.get('cedula'))),
        'nombre': _clean_text(row.get('nombre')),
        'correo_personal': _clean_text(row.get('correo_personal')).lower(),
        'correo_intec': correo_intec.lower() if correo_intec else '',
        'telefono': _clean_text(row.get('telefono')),
        'movil': _clean_text(row.get('movil')),
        'direccion': _clean_text(row.get('direccion')),
        'codigo_usuario': _clean_text(row.get('codigo_usuario')),
        'login': _clean_text(row.get('login')),
        'estado_usuario': _clean_text(row.get('estado_usuario')),
        'tiene_credenciales': bool(_clean_text(row.get('login'))),
    }


def _sync_teacher_assignment_to_complement(
    *,
    teacher: dict[str, Any],
    assignment: dict[str, Any],
    user_login: str,
) -> dict[str, Any]:
    try:
        return sync_teacher_assignment_to_complement(
            codigo_doc=teacher.get('codigo_doc'),
            cedula_doc=teacher.get('cedula', ''),
            assignment=assignment,
            usuario_registro=user_login or 'SISTEMA',
        )
    except Exception as exc:
        return {
            'synced': False,
            'database': complement_database_name(),
            'message': f'No se pudo sincronizar matrícula docente con educación continua: {str(exc)}',
        }


def _send_teacher_credentials_email(
    *,
    recipient_email: str,
    recipient_name: str,
    intec_email: str,
    password: str,
    assignment: dict[str, str],
) -> dict[str, Any]:
    safe_recipient = escape(recipient_name or recipient_email)
    safe_intec_email = escape(intec_email)
    safe_password = escape(password)
    if assignment.get('materia') or assignment.get('codigo_materia'):
        safe_course = escape(assignment.get('materia') or 'la materia asignada')
        safe_period = escape(assignment.get('periodo') or assignment.get('codigo_periodo') or 'el período seleccionado')
        safe_parallel = escape(assignment.get('paralelo') or DEFAULT_PARALLEL)
        detail_text = (
            f'Tu cuenta institucional docente ha sido creada y asignada a '
            f'<strong>{safe_course}</strong> para {safe_period}, paralelo {safe_parallel}.'
        )
    else:
        detail_text = 'Tu cuenta institucional docente ha sido creada correctamente.'
    logo_attachment = _build_intec_logo_attachment()
    logo_html = ''
    if logo_attachment:
        logo_html = """
            <tr>
              <td align="center" style="padding:24px 28px 8px 28px;background:#ffffff;">
                <img src="cid:intec-logo.png" width="230" alt="INTEC" style="display:block;width:230px;max-width:78%;height:auto;border:0;" />
              </td>
            </tr>
""".rstrip()

    html_content = f"""
<html>
  <body style="margin:0;padding:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f4f6;padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="620" cellspacing="0" cellpadding="0" style="max-width:620px;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 8px 26px rgba(15,23,42,0.12);">
            {logo_html}
            <tr>
              <td style="background:#9B0E0E;padding:20px 28px;color:#ffffff;">
                <h2 style="margin:0;font-size:22px;font-weight:700;">Credenciales docentes INTEC</h2>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 28px;color:#111827;">
                <p style="margin:0 0 12px 0;font-size:16px;">Hola {safe_recipient},</p>
                <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#374151;">{detail_text}</p>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 18px 0;border:1px solid #e5e7eb;border-radius:10px;">
                  <tr>
                    <td style="padding:14px 16px;font-size:14px;color:#111827;"><strong>Usuario:</strong> {safe_intec_email}</td>
                  </tr>
                  <tr>
                    <td style="padding:14px 16px;border-top:1px solid #e5e7eb;font-size:14px;color:#111827;"><strong>Contraseña:</strong> {safe_password}</td>
                  </tr>
                </table>
                <p style="margin:0;font-size:13px;line-height:1.6;color:#6b7280;">Conserva estas credenciales en un lugar seguro y no las compartas con terceros.</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()

    mail_payload = {
        '_skip_default_cc': True,
        'message': {
            'subject': 'Credenciales docentes INTEC',
            'body': {
                'contentType': 'HTML',
                'content': html_content,
            },
            'toRecipients': [
                {
                    'emailAddress': {
                        'address': recipient_email,
                    }
                }
            ],
        },
        'saveToSentItems': True,
    }
    if logo_attachment:
        mail_payload['message']['attachments'] = [logo_attachment]

    _send_graph_mail(mail_payload)
    return {
        'sent': True,
        'message': f'Credenciales docentes enviadas correctamente a {recipient_email}.',
    }


def _ensure_teacher_schema() -> None:
    required = {
        'DATOSDOCENTE': [
            'codigo_doc',
            'cedula_doc',
            'apellidos_nombre',
            'correo',
            'telefono',
            'movil',
            'Direccion',
            'correop',
        ],
        'USUARIOS': [
            'Codigo_Usuario',
            'cedula',
            'login',
            'password',
            'fecha_ingreso',
            'tipo_usuario',
            'CambioClave',
            'Estado',
            'Descripcion',
        ],
        'CARRERAXDOCENTE': [
            'codigo_doc',
            'cod_Anio_Basica',
            'codigo_materia',
            'Paralelo',
            'codigo_periodo',
            'Cod_Jornada',
            'estadoMoodleDoc',
        ],
    }
    with connection.cursor() as cursor:
        for table_name, columns in required.items():
            cursor.execute("SELECT OBJECT_ID(%s)", [f'dbo.{table_name}'])
            row = cursor.fetchone()
            if not row or row[0] is None:
                raise TeacherEnrollmentError(f'No existe la tabla dbo.{table_name} requerida para matrícula docente.')
            for column_name in columns:
                cursor.execute("SELECT COL_LENGTH(%s, %s)", [f'dbo.{table_name}', column_name])
                column_row = cursor.fetchone()
                if not column_row or column_row[0] is None:
                    raise TeacherEnrollmentError(f'No existe la columna {column_name} en dbo.{table_name}.')


def _next_numeric_code(table_name: str, column_name: str) -> str:
    if not table_name.replace('_', '').isalnum() or not column_name.replace('_', '').isalnum():
        raise TeacherEnrollmentError('Nombre de tabla o columna inválido para generar código.')
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT ISNULL(MAX(CAST([{column_name}] AS decimal(18,0))), 0) + 1 FROM dbo.[{table_name}]"
        )
        row = cursor.fetchone()
    return str(_safe_int(row[0] if row else 1, default=1))


def _teacher_user_type() -> int:
    raw_value = str(os.getenv('TEACHER_USER_TYPE') or DEFAULT_TEACHER_USER_TYPE).strip()
    return _safe_int(raw_value, default=DEFAULT_TEACHER_USER_TYPE)


def _fetch_all(query: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        if cursor.description is None:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fetch_one(query: str, params: list[Any]) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row is None:
            return None
        columns = [column[0] for column in cursor.description]
    return {columns[index]: row[index] for index in range(len(columns))}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _clean_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())


def _trim_to_max(value: Any, max_length: int) -> str:
    return str(value or '').strip()[:max_length]
