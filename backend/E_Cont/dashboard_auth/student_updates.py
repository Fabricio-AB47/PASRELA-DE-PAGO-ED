from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DEFAULT_DB_ALIAS, connection, transaction

from .continuing_education import (
    complement_connection,
    complement_database_alias,
    complement_database_name,
    is_complement_available,
)


class StudentUpdateError(Exception):
    pass


EDITABLE_FIELDS = (
    'nombre',
    'cedula',
    'correo_personal',
    'correo_intec',
    'telefono',
    'movil',
    'ciudad',
    'direccion',
    'fecha_nacimiento',
    'sexo',
)


def list_students_for_update(
    corte_id: Any,
    *,
    search: Any = '',
    limit: Any = 300,
) -> dict[str, Any]:
    normalized_cut_id = _safe_int(corte_id)
    if normalized_cut_id <= 0:
        raise StudentUpdateError('Selecciona una corte para consultar estudiantes matriculados.')

    safe_limit = min(max(_safe_int(limit, default=300), 1), 500)
    search_text = _clean_text(search)
    params: list[Any] = [normalized_cut_id]
    search_clause = ''
    if search_text:
        search_clause = """
          AND (
                UPPER(LTRIM(RTRIM(ISNULL(DE.Apellidos_nombre, CCE.ApellidosNombre)))) LIKE UPPER(%s)
             OR LTRIM(RTRIM(ISNULL(DE.Cedula_Est, CCE.CedulaEst))) LIKE %s
             OR LTRIM(RTRIM(CAST(CCE.CodigoEstud AS varchar(50)))) LIKE %s
             OR UPPER(LTRIM(RTRIM(ISNULL(DE.correo, '')))) LIKE UPPER(%s)
             OR UPPER(LTRIM(RTRIM(ISNULL(DE.correointec, '')))) LIKE UPPER(%s)
          )
        """
        pattern = f'%{search_text}%'
        params.extend([pattern] * 5)

    rows = _fetch_all(
        f"""
        SELECT TOP ({safe_limit})
            CCE.CorteEstudianteId,
            CCE.CorteId,
            CCE.CodigoEstud,
            COALESCE(
                NULLIF(LTRIM(RTRIM(DE.Cedula_Est)), ''),
                NULLIF(LTRIM(RTRIM(CCE.CedulaEst)), '')
            ) AS Cedula,
            COALESCE(
                NULLIF(LTRIM(RTRIM(DE.Apellidos_nombre)), ''),
                NULLIF(LTRIM(RTRIM(CCE.ApellidosNombre)), '')
            ) AS Nombre,
            NULLIF(LTRIM(RTRIM(DE.correo)), '') AS CorreoPersonal,
            NULLIF(LTRIM(RTRIM(DE.correointec)), '') AS CorreoIntec,
            NULLIF(LTRIM(RTRIM(DE.telefono)), '') AS Telefono,
            NULLIF(LTRIM(RTRIM(DE.movil)), '') AS Movil,
            NULLIF(LTRIM(RTRIM(DE.ciudad)), '') AS Ciudad,
            NULLIF(LTRIM(RTRIM(DE.calle_principal)), '') AS Direccion,
            DE.Fecha_Nac AS FechaNacimiento,
            NULLIF(LTRIM(RTRIM(CAST(DE.Sexo AS varchar(10)))), '') AS Sexo,
            CCE.EstadoParticipacion,
            CCE.EstadoRegistro,
            CCE.FechaModifica,
            CCE.UsuarioModifica
        FROM dbo.CORTE_CURSO_ESTUDIANTE CCE
        INNER JOIN dbo.DATOS_ESTUD DE
          ON LTRIM(RTRIM(CAST(DE.codigo_estud AS varchar(50)))) =
             LTRIM(RTRIM(CAST(CCE.CodigoEstud AS varchar(50))))
        WHERE CCE.CorteId = %s
          AND ISNULL(CCE.EstadoRegistro, 'A') <> 'I'
          {search_clause}
        ORDER BY Nombre, CCE.CorteEstudianteId
        """,
        params,
    )
    students = [_serialize_student(row) for row in rows]
    return {
        'corte_id': str(normalized_cut_id),
        'students': students,
        'total': len(students),
        'editable_fields': list(EDITABLE_FIELDS),
    }


def update_enrolled_student(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    corte_id = _safe_int(payload.get('corte_id') or payload.get('CorteId'))
    codigo_estud = _safe_int(payload.get('codigo_estud') or payload.get('CodigoEstud'))
    if corte_id <= 0 or codigo_estud <= 0:
        raise StudentUpdateError('Selecciona una matrícula válida para actualizar.')

    current = _fetch_student(corte_id, codigo_estud)
    if not current:
        raise StudentUpdateError('El estudiante no está matriculado en la corte seleccionada.')

    clean = _validate_student_payload(payload, current)
    _ensure_identity_is_unique(clean['cedula'], codigo_estud)

    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        _update_primary_student(codigo_estud, clean)
        _update_enrollment_references(codigo_estud, clean, user_login=user_login)

    complement_sync = _sync_student_snapshot(codigo_estud, clean)
    updated = _fetch_student(corte_id, codigo_estud)
    return {
        'student': _serialize_student(updated or current),
        'complement_sync': complement_sync,
        'message': (
            'Información del estudiante actualizada y sincronizada.'
            if complement_sync['synced']
            else 'Información actualizada en INTECBDD; la sincronización complementaria quedó pendiente.'
        ),
    }


def get_student_migration_credentials(corte_id: Any, codigo_estud: Any) -> dict[str, Any]:
    normalized_cut_id = _safe_int(corte_id)
    normalized_student_code = _safe_int(codigo_estud)
    if normalized_cut_id <= 0 or normalized_student_code <= 0:
        raise StudentUpdateError('Selecciona una matrícula válida para consultar credenciales.')
    if not _fetch_student(normalized_cut_id, normalized_student_code):
        raise StudentUpdateError('El estudiante no está matriculado en la cohorte seleccionada.')

    row = _fetch_one(
        """
        SELECT TOP (1)
            codestud,
            Nombres,
            CorreoPersonal,
            CorreoIntec,
            Password,
            fecha,
            Periodo,
            CorreoEnviado,
            Estado,
            CONVERT(nvarchar(max), Descripcion) AS Descripcion,
            ultAccesoMoodle,
            NumMigracion,
            TipoCursoMigra
        FROM dbo.CorreosEstudIntec
        WHERE LTRIM(RTRIM(CAST(codestud AS varchar(50)))) = %s
        ORDER BY fecha DESC, Periodo DESC
        """,
        [str(normalized_student_code)],
    )
    if not row:
        raise StudentUpdateError('El estudiante no tiene una cuenta registrada en CorreosEstudIntec.')
    return {
        'codigo_estud': _text(row.get('codestud')),
        'nombres': _text(row.get('Nombres')),
        'correo_personal': _text(row.get('CorreoPersonal')),
        'correo_intec': _text(row.get('CorreoIntec')),
        'password': str(row.get('Password') or ''),
        'fecha': _date_text(row.get('fecha')),
        'periodo': _text(row.get('Periodo')),
        'correo_enviado': _text(row.get('CorreoEnviado')),
        'estado': _text(row.get('Estado')),
        'descripcion': _text(row.get('Descripcion')),
        'ultimo_acceso_moodle': _date_text(row.get('ultAccesoMoodle')),
        'numero_migracion': _text(row.get('NumMigracion')),
        'tipo_curso_migracion': _text(row.get('TipoCursoMigra')),
    }


def _validate_student_payload(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    values = {
        'nombre': _trim(payload.get('nombre', current.get('Nombre')), 70),
        'cedula': _trim(payload.get('cedula', current.get('Cedula')), 50).upper(),
        'correo_personal': _trim(payload.get('correo_personal', current.get('CorreoPersonal')), 80).lower(),
        'correo_intec': _trim(payload.get('correo_intec', current.get('CorreoIntec')), 100).lower(),
        'telefono': _trim(payload.get('telefono', current.get('Telefono')), 30),
        'movil': _trim(payload.get('movil', current.get('Movil')), 15),
        'ciudad': _trim(payload.get('ciudad', current.get('Ciudad')), 70),
        'direccion': _trim(payload.get('direccion', current.get('Direccion')), 150),
        'fecha_nacimiento': _date_value(
            payload.get('fecha_nacimiento', current.get('FechaNacimiento'))
        ),
        'sexo': _normalize_sex(payload.get('sexo', current.get('Sexo'))),
    }
    if not values['nombre']:
        raise StudentUpdateError('El nombre del estudiante es obligatorio.')
    if len(values['cedula']) < 6 or not re.fullmatch(r'[A-Z0-9-]+', values['cedula']):
        raise StudentUpdateError('La identificación debe contener entre 6 y 50 caracteres válidos.')
    for key, label in (
        ('correo_personal', 'correo personal'),
        ('correo_intec', 'correo INTEC'),
    ):
        if values[key]:
            try:
                validate_email(values[key])
            except ValidationError as exc:
                raise StudentUpdateError(f'El {label} no tiene un formato válido.') from exc
    return values


def _ensure_identity_is_unique(cedula: str, codigo_estud: int) -> None:
    row = _fetch_one(
        """
        SELECT TOP (1) codigo_estud
        FROM dbo.DATOS_ESTUD
        WHERE UPPER(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(Cedula_Est, ''))), ' ', ''), '.', '')) =
              UPPER(REPLACE(REPLACE(%s, ' ', ''), '.', ''))
          AND LTRIM(RTRIM(CAST(codigo_estud AS varchar(50)))) <> %s
        """,
        [cedula, str(codigo_estud)],
    )
    if row:
        raise StudentUpdateError('La identificación ya pertenece a otro estudiante registrado.')


def _update_primary_student(codigo_estud: int, values: dict[str, Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.DATOS_ESTUD
            SET Cedula_Est = %s,
                Cedula = CASE
                    WHEN TRY_CONVERT(decimal(18, 0), %s) IS NOT NULL
                    THEN TRY_CONVERT(decimal(18, 0), %s)
                    ELSE Cedula
                END,
                Apellidos_nombre = %s,
                correo = %s,
                correointec = %s,
                telefono = %s,
                movil = %s,
                ciudad = %s,
                calle_principal = %s,
                Fecha_Nac = %s,
                Sexo = %s
            WHERE LTRIM(RTRIM(CAST(codigo_estud AS varchar(50)))) = %s
            """,
            [
                values['cedula'],
                values['cedula'],
                values['cedula'],
                values['nombre'],
                values['correo_personal'],
                values['correo_intec'],
                values['telefono'],
                values['movil'],
                values['ciudad'],
                values['direccion'],
                values['fecha_nacimiento'],
                values['sexo'],
                str(codigo_estud),
            ],
        )
        if cursor.rowcount == 0:
            raise StudentUpdateError('No se encontró el registro principal del estudiante.')

        cursor.execute(
            """
            UPDATE dbo.EstudiantesEdContinua
            SET Cedula_Est = %s,
                Apellidos_nombre = %s,
                correo = %s,
                telefono = %s,
                movil = %s,
                calle_principal = %s,
                Fecha_Nac = %s
            WHERE LTRIM(RTRIM(CAST(codigo_estud AS varchar(50)))) = %s
            """,
            [
                values['cedula'][:20],
                values['nombre'],
                values['correo_personal'][:50],
                values['telefono'][:20],
                values['movil'][:20],
                values['direccion'][:100],
                values['fecha_nacimiento'],
                str(codigo_estud),
            ],
        )
        cursor.execute(
            """
            UPDATE dbo.CorreosEstudIntec
            SET Nombres = %s,
                CorreoPersonal = %s,
                CorreoIntec = %s
            WHERE LTRIM(RTRIM(CAST(codestud AS varchar(50)))) = %s
            """,
            [
                values['nombre'][:100],
                values['correo_personal'][:100],
                values['correo_intec'],
                str(codigo_estud),
            ],
        )


def _update_enrollment_references(
    codigo_estud: int,
    values: dict[str, Any],
    *,
    user_login: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.CORTE_CURSO_ESTUDIANTE
            SET CedulaEst = %s,
                ApellidosNombre = %s,
                UsuarioModifica = %s,
                FechaModifica = sysdatetime()
            WHERE LTRIM(RTRIM(CAST(CodigoEstud AS varchar(50)))) = %s
            """,
            [
                values['cedula'],
                values['nombre'],
                _trim(user_login or 'SISTEMA', 50),
                str(codigo_estud),
            ],
        )
        cursor.execute(
            """
            UPDATE dbo.CERTIFICADOS_GENERADOS
            SET CedulaEst = %s,
                ApellidosNombre = %s
            WHERE LTRIM(RTRIM(CAST(CodigoEstud AS varchar(50)))) = %s
            """,
            [values['cedula'], values['nombre'], str(codigo_estud)],
        )
        cursor.execute(
            """
            UPDATE dbo.CERTIFICADO_CORTE_ESTUDIANTE
            SET CedulaEst = %s
            WHERE LTRIM(RTRIM(CAST(CodigoEstud AS varchar(50)))) = %s
            """,
            [values['cedula'], str(codigo_estud)],
        )
        cursor.execute(
            """
            UPDATE dbo.FIN_SOLICITUD_PAGO_TARJETA
            SET Cedula = LEFT(%s, 20),
                NombreEstudiante = %s,
                Email = COALESCE(NULLIF(%s, ''), NULLIF(%s, ''), Email)
            WHERE LTRIM(RTRIM(CAST(CodigoEstud AS varchar(50)))) = %s
            """,
            [
                values['cedula'],
                values['nombre'],
                values['correo_personal'],
                values['correo_intec'],
                str(codigo_estud),
            ],
        )
        cursor.execute(
            """
            UPDATE dbo.DATOSFACTURA
            SET CEDESTUD = CASE WHEN LEN(%s) <= 10 THEN %s ELSE CEDESTUD END,
                NOMBRES = LEFT(%s, 100),
                DIRECCION = LEFT(%s, 150),
                TELELFONO = LEFT(COALESCE(NULLIF(%s, ''), %s), 60),
                CORREO = LEFT(COALESCE(NULLIF(%s, ''), NULLIF(%s, ''), CORREO), 100)
            WHERE LTRIM(RTRIM(CAST(CODESTUD AS varchar(50)))) = %s
            """,
            [
                values['cedula'],
                values['cedula'],
                values['nombre'],
                values['direccion'],
                values['movil'],
                values['telefono'],
                values['correo_personal'],
                values['correo_intec'],
                str(codigo_estud),
            ],
        )
        cursor.execute(
            """
            UPDATE dbo.PREINSCRIPCION
            SET Cedula = CASE WHEN LEN(%s) <= 10 THEN %s ELSE Cedula END,
                Apellidos_nombre = LEFT(%s, 100),
                correo = LEFT(COALESCE(NULLIF(%s, ''), NULLIF(%s, ''), correo), 100),
                telefono = LEFT(COALESCE(NULLIF(%s, ''), %s), 100)
            WHERE LTRIM(RTRIM(CAST(Codestu AS varchar(50)))) = %s
            """,
            [
                values['cedula'],
                values['cedula'],
                values['nombre'],
                values['correo_personal'],
                values['correo_intec'],
                values['movil'],
                values['telefono'],
                str(codigo_estud),
            ],
        )
        cursor.execute(
            """
            UPDATE dbo.prematricula_homologacion
            SET Cedula = LEFT(%s, 20),
                Apellidos_nombre = LEFT(%s, 150),
                correo = LEFT(COALESCE(NULLIF(%s, ''), NULLIF(%s, ''), correo), 100),
                telefono = LEFT(COALESCE(NULLIF(%s, ''), %s), 20)
            WHERE LTRIM(RTRIM(CAST(Codestu AS varchar(50)))) = %s
            """,
            [
                values['cedula'],
                values['nombre'],
                values['correo_personal'],
                values['correo_intec'],
                values['movil'],
                values['telefono'],
                str(codigo_estud),
            ],
        )
        cursor.execute(
            """
            UPDATE dbo.SEGUIMIENTO_ESTUDIANTE
            SET nombres = LEFT(%s, 200),
                usuario_modificacion = %s,
                fecha_modificacion = sysdatetime()
            WHERE LTRIM(RTRIM(CAST(codigo_estud AS varchar(50)))) = %s
            """,
            [
                values['nombre'],
                _trim(user_login or 'SISTEMA', 100),
                str(codigo_estud),
            ],
        )


def _sync_student_snapshot(codigo_estud: int, values: dict[str, Any]) -> dict[str, Any]:
    required = [('edu', 'EstudiantePrincipalSnapshot', 'U')]
    try:
        if not is_complement_available(required):
            return {'synced': False, 'message': 'Base complementaria no disponible.'}
        target = (
            '[edu].[EstudiantePrincipalSnapshot]'
            if complement_database_alias() != DEFAULT_DB_ALIAS
            else f'[{complement_database_name()}].[edu].[EstudiantePrincipalSnapshot]'
        )
        with complement_connection().cursor() as cursor:
            cursor.execute(
                f"""
                MERGE {target} AS T
                USING (SELECT %s AS CodigoEstud) AS S
                   ON T.CodigoEstud = S.CodigoEstud
                WHEN MATCHED THEN UPDATE SET
                    CedulaEst = %s,
                    ApellidosNombre = %s,
                    CorreoPersonal = %s,
                    CorreoIntec = %s,
                    FechaSincronizacion = sysdatetime()
                WHEN NOT MATCHED THEN INSERT (
                    CodigoEstud, CedulaEst, ApellidosNombre,
                    CorreoPersonal, CorreoIntec
                ) VALUES (%s, %s, %s, %s, %s);
                """,
                [
                    codigo_estud,
                    values['cedula'],
                    values['nombre'],
                    values['correo_personal'],
                    values['correo_intec'],
                    codigo_estud,
                    values['cedula'],
                    values['nombre'],
                    values['correo_personal'],
                    values['correo_intec'],
                ],
            )
        return {'synced': True, 'message': 'Snapshot complementario actualizado.'}
    except Exception:
        return {
            'synced': False,
            'message': 'No fue posible sincronizar el snapshot complementario.',
        }


def _fetch_student(corte_id: int, codigo_estud: int) -> dict[str, Any] | None:
    return _fetch_one(
        """
        SELECT TOP (1)
            CCE.CorteEstudianteId,
            CCE.CorteId,
            CCE.CodigoEstud,
            COALESCE(NULLIF(LTRIM(RTRIM(DE.Cedula_Est)), ''), CCE.CedulaEst) AS Cedula,
            COALESCE(NULLIF(LTRIM(RTRIM(DE.Apellidos_nombre)), ''), CCE.ApellidosNombre) AS Nombre,
            NULLIF(LTRIM(RTRIM(DE.correo)), '') AS CorreoPersonal,
            NULLIF(LTRIM(RTRIM(DE.correointec)), '') AS CorreoIntec,
            NULLIF(LTRIM(RTRIM(DE.telefono)), '') AS Telefono,
            NULLIF(LTRIM(RTRIM(DE.movil)), '') AS Movil,
            NULLIF(LTRIM(RTRIM(DE.ciudad)), '') AS Ciudad,
            NULLIF(LTRIM(RTRIM(DE.calle_principal)), '') AS Direccion,
            DE.Fecha_Nac AS FechaNacimiento,
            NULLIF(LTRIM(RTRIM(CAST(DE.Sexo AS varchar(10)))), '') AS Sexo,
            CCE.EstadoParticipacion,
            CCE.EstadoRegistro,
            CCE.FechaModifica,
            CCE.UsuarioModifica
        FROM dbo.CORTE_CURSO_ESTUDIANTE CCE
        INNER JOIN dbo.DATOS_ESTUD DE
          ON LTRIM(RTRIM(CAST(DE.codigo_estud AS varchar(50)))) =
             LTRIM(RTRIM(CAST(CCE.CodigoEstud AS varchar(50))))
        WHERE CCE.CorteId = %s
          AND LTRIM(RTRIM(CAST(CCE.CodigoEstud AS varchar(50)))) = %s
        ORDER BY CCE.CorteEstudianteId DESC
        """,
        [corte_id, str(codigo_estud)],
    )


def _serialize_student(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'corte_estudiante_id': _text(row.get('CorteEstudianteId')),
        'corte_id': _text(row.get('CorteId')),
        'codigo_estud': _text(row.get('CodigoEstud')),
        'nombre': _text(row.get('Nombre')),
        'cedula': _text(row.get('Cedula')),
        'correo_personal': _text(row.get('CorreoPersonal')),
        'correo_intec': _text(row.get('CorreoIntec')),
        'telefono': _text(row.get('Telefono')),
        'movil': _text(row.get('Movil')),
        'ciudad': _text(row.get('Ciudad')),
        'direccion': _text(row.get('Direccion')),
        'fecha_nacimiento': _date_text(row.get('FechaNacimiento')),
        'sexo': _text(row.get('Sexo')),
        'estado_participacion': _text(row.get('EstadoParticipacion')),
        'estado_registro': _text(row.get('EstadoRegistro')),
        'fecha_modifica': _date_text(row.get('FechaModifica'), include_time=True),
        'usuario_modifica': _text(row.get('UsuarioModifica')),
    }


def _fetch_all(query: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [column[0] for column in cursor.description or []]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fetch_one(query: str, params: list[Any]) -> dict[str, Any] | None:
    rows = _fetch_all(query, params)
    return rows[0] if rows else None


def _normalize_sex(value: Any) -> int | None:
    text = _clean_text(value).upper()
    aliases = {
        'MASCULINO': '1',
        'HOMBRE': '1',
        'M': '1',
        'FEMENINO': '2',
        'MUJER': '2',
        'F': '2',
        'OTRO': '3',
        'O': '3',
    }
    normalized = aliases.get(text, text)
    if normalized not in {'', '1', '2', '3'}:
        raise StudentUpdateError('Selecciona un sexo válido.')
    return int(normalized) if normalized else None


def _date_value(value: Any) -> date | None:
    if value in (None, ''):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_clean_text(value)[:10])
    except ValueError as exc:
        raise StudentUpdateError('La fecha de nacimiento no tiene un formato válido.') from exc


def _date_text(value: Any, *, include_time: bool = False) -> str:
    if not value:
        return ''
    if isinstance(value, datetime):
        return value.isoformat(sep=' ', timespec='seconds') if include_time else value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _clean_text(value)


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(str(value or '').strip())
    except (TypeError, ValueError):
        return default


def _trim(value: Any, max_length: int) -> str:
    return _clean_text(value)[:max_length]


def _clean_text(value: Any) -> str:
    return str(value or '').strip()


def _text(value: Any) -> str:
    return _clean_text(value)
