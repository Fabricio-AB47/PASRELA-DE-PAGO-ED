from __future__ import annotations

import re
from typing import Any

from django.db import connection


USER_REGISTERED_MESSAGE = 'Usuario registrado'


class RegisteredUserExistsError(Exception):
    def __init__(self, registration: dict[str, Any]):
        self.registration = registration
        super().__init__(USER_REGISTERED_MESSAGE)


def ensure_user_is_not_registered(
    cedula: Any,
    *,
    cod_anio_basica: Any = '',
    codigo_materia: Any = '',
    codigo_periodo: Any = '',
) -> dict[str, Any]:
    registration = lookup_registered_user_by_number(
        cedula,
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
    )
    if registration.get('exists'):
        raise RegisteredUserExistsError(registration)
    return registration


def lookup_registered_user_by_number(
    cedula: Any,
    *,
    cod_anio_basica: Any = '',
    codigo_materia: Any = '',
    codigo_periodo: Any = '',
) -> dict[str, Any]:
    clean_cedula = _normalize_id_number(cedula)
    if not clean_cedula:
        return {'exists': False, 'cedula': '', 'message': ''}

    course_context = {
        'cod_anio_basica': _clean_text(cod_anio_basica),
        'codigo_materia': _clean_text(codigo_materia),
        'codigo_periodo': _clean_text(codigo_periodo),
    }

    student = _find_datos_estud(clean_cedula)
    if student:
        return _registered_response(
            clean_cedula,
            source='DATOS_ESTUD',
            course_context=course_context,
        )

    return {
        'exists': False,
        'cedula': clean_cedula,
        'message': '',
        **course_context,
    }


def _registered_response(
    cedula: str,
    *,
    source: str,
    course_context: dict[str, str],
) -> dict[str, Any]:
    return {
        'exists': True,
        'message': USER_REGISTERED_MESSAGE,
        'cedula': cedula,
        'source': source,
        'already_enrolled': True,
        **course_context,
    }


def _find_datos_estud(cedula: str) -> dict[str, Any] | None:
    query = """
        SELECT TOP (1)
            CAST(codigo_estud AS varchar(50)) AS codigo_estud,
            CAST(codigo_estud AS varchar(50)) AS matricula,
            LTRIM(RTRIM(ISNULL(Cedula_Est, ''))) AS cedula,
            LTRIM(RTRIM(ISNULL(Apellidos_nombre, ''))) AS nombre,
            LTRIM(RTRIM(ISNULL(correo, ''))) AS email,
            LTRIM(RTRIM(ISNULL(telefono, ''))) AS telefono
        FROM dbo.DATOS_ESTUD
        WHERE TRY_CONVERT(
                  decimal(20, 0),
                  REPLACE(REPLACE(REPLACE(REPLACE(
                      LTRIM(RTRIM(ISNULL(Cedula_Est, ''))), '-', ''
                  ), ' ', ''), '.', ''), ',', '')
              ) = TRY_CONVERT(decimal(20, 0), %s)
           OR TRY_CONVERT(decimal(20, 0), Cedula) = TRY_CONVERT(decimal(20, 0), %s)
        ORDER BY codigo_estud DESC
    """
    return _fetch_one(query, [cedula, cedula])


def _fetch_one(query: str, params: list[Any]) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row is None:
            return None
        columns = [column[0] for column in cursor.description]
    return {columns[index]: row[index] for index in range(len(columns))}


def _normalize_id_number(value: Any) -> str:
    return re.sub(r'\D+', '', str(value or '').strip())


def _clean_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())
