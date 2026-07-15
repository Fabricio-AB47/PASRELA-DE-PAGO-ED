from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.cache import cache
from django.db import DEFAULT_DB_ALIAS, connections


DEFAULT_COMPLEMENT_DB_NAME = 'INTECEDUCONTINUA'
COMPLEMENT_DATABASE_ALIAS = 'continuing_education'
IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class ContinuingEducationError(Exception):
    pass


def complement_enabled() -> bool:
    value = os.getenv('EDU_CONTINUA_ENABLED', 'true').strip().lower()
    return value not in {'0', 'false', 'no', 'off'}


def complement_database_name() -> str:
    return _safe_identifier(
        os.getenv('EDU_CONTINUA_DB_NAME')
        or os.getenv('CONTINUING_EDUCATION_DB_NAME')
        or DEFAULT_COMPLEMENT_DB_NAME
    )


def complement_database_alias() -> str:
    """Return the dedicated alias when DB_*1 is configured, else legacy default."""
    return (
        COMPLEMENT_DATABASE_ALIAS
        if COMPLEMENT_DATABASE_ALIAS in connections.databases
        else DEFAULT_DB_ALIAS
    )


def complement_connection():
    return connections[complement_database_alias()]


def connection_for_query(
    query: str,
    params: list[Any] | tuple[Any, ...] | None = None,
):
    """Route explicitly-qualified INTECEDUCONTINUA SQL to DB_*1."""
    database_token = f'[{complement_database_name()}]'.lower()
    query_context = f'{query or ""} {params or ""}'.lower()
    if database_token in query_context:
        return complement_connection()
    return connections[DEFAULT_DB_ALIAS]


def complement_status() -> dict[str, Any]:
    db_name = complement_database_name()
    status = {
        'enabled': complement_enabled(),
        'database': db_name,
        'version': '',
        'available': False,
        'missing': [],
    }
    if not status['enabled']:
        status['message'] = 'Integración de educación continua deshabilitada por configuración.'
        return status

    missing: list[str] = []
    try:
        if not _database_exists(db_name):
            missing.append(db_name)
        else:
            version = _detect_complement_version(db_name)
            status['version'] = version
            if not version:
                missing = _missing_objects(db_name, _v5_core_required_objects())
    except Exception as exc:
        status['message'] = str(exc)
        status['missing'] = missing
        return status

    status['missing'] = missing
    status['available'] = not missing
    status['message'] = (
        'Base complementaria disponible.'
        if status['available']
        else 'Base complementaria no instalada o incompleta.'
    )
    return status


def is_complement_available(required: list[tuple[str, str, str]] | None = None) -> bool:
    if not complement_enabled():
        return False
    db_name = complement_database_name()
    try:
        if not _database_exists(db_name):
            return False
        if required is None:
            return bool(_detect_complement_version(db_name))
        if not _objects_available(db_name, required):
            return False
    except Exception:
        return False
    return True


def complement_version() -> str:
    if not complement_enabled():
        return ''
    db_name = complement_database_name()
    try:
        if not _database_exists(db_name):
            return ''
        return _detect_complement_version(db_name)
    except Exception:
        return ''


def configure_cut_in_complement(
    corte_id: Any,
    *,
    cupo_maximo: Any = None,
    usuario_registro: str = '',
) -> dict[str, Any]:
    version = complement_version()
    if version == 'v5':
        required = [('edu', 'usp_ConfigurarCorteDesdePrincipal', 'P'), ('edu', 'VW_CorteCursoDetalle', 'V')]
    else:
        required = [('edu', 'usp_ConfigurarCorteCurso', 'P'), ('edu', 'VW_CursosPrincipal', 'V')]
    if not version or not is_complement_available(required):
        return _skipped_result('Base complementaria no disponible para configurar cortes.')

    numeric_corte_id = _safe_int(corte_id)
    if numeric_corte_id <= 0:
        return _skipped_result('CorteId inválido para configurar educación continua.')

    cupo = _safe_int(cupo_maximo, default=50)
    if cupo <= 0:
        cupo = 50

    if version == 'v5':
        primary_cut = _fetch_primary_one(
            """
            SELECT TOP (1)
                Cod_AnioBasica,
                CodigoPeriodo,
                CodigoMateria,
                CodCurso,
                CupoEsperado,
                NombreCorte,
                FechaInicio,
                FechaFin
            FROM dbo.CORTE_CURSO
            WHERE CorteId = %s
            """,
            [numeric_corte_id],
        )
        if not primary_cut:
            return _skipped_result('La corte no existe en la base principal INTECBDD.')

        effective_cupo = cupo or _safe_int(primary_cut.get('CupoEsperado'), default=50) or 50
        rows = _fetch_all(
            f"""
            MERGE {_qualified('edu', 'CorteCurso')} AS T
            USING (SELECT %s AS CorteId) AS S
               ON T.CorteId = S.CorteId
            WHEN MATCHED THEN UPDATE SET
                Cod_AnioBasica = %s,
                CodigoPeriodo = %s,
                CodigoMateria = %s,
                CodCurso = %s,
                CupoMaximo = %s,
                FechaInicioOverride = %s,
                FechaFinOverride = %s,
                Observacion = %s,
                EstadoCorteEdu = CASE
                    WHEN T.EstadoCorteEdu = 'CONFIGURADO' THEN 'ABIERTO'
                    ELSE T.EstadoCorteEdu
                END,
                UsuarioModifica = %s,
                FechaModifica = sysdatetime()
            WHEN NOT MATCHED THEN INSERT (
                CorteId, Cod_AnioBasica, CodigoPeriodo, CodigoMateria, CodCurso,
                CupoMaximo, PermiteSobrecupo, ValorCurso, NotaMinima,
                PorcentajeMinAsistencia, RequierePagoCompleto, RequierePaseNotas,
                GeneraCertificado, UsaTeams, EstadoCorteEdu,
                FechaInicioOverride, FechaFinOverride, Observacion, UsuarioRegistro
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, 0, 0, 7.00,
                70.00, 1, 1,
                1, 1, 'ABIERTO',
                %s, %s, %s, %s
            );

            SELECT *
            FROM {_qualified('edu', 'VW_CorteCursoDetalle')}
            WHERE CorteId = %s;
            """,
            [
                numeric_corte_id,
                primary_cut.get('Cod_AnioBasica'),
                primary_cut.get('CodigoPeriodo'),
                primary_cut.get('CodigoMateria'),
                primary_cut.get('CodCurso'),
                effective_cupo,
                primary_cut.get('FechaInicio'),
                primary_cut.get('FechaFin'),
                _trim_to_max(primary_cut.get('NombreCorte'), 500),
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
                numeric_corte_id,
                primary_cut.get('Cod_AnioBasica'),
                primary_cut.get('CodigoPeriodo'),
                primary_cut.get('CodigoMateria'),
                primary_cut.get('CodCurso'),
                effective_cupo,
                primary_cut.get('FechaInicio'),
                primary_cut.get('FechaFin'),
                _trim_to_max(primary_cut.get('NombreCorte'), 500),
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
                numeric_corte_id,
            ],
        )
    else:
        rows = _fetch_all(
            f"""
            EXEC {_qualified('edu', 'usp_ConfigurarCorteCurso')}
                @CorteId = %s,
                @CupoMaximo = %s,
                @PermiteSobrecupo = 0,
                @ActualizarCupoPrincipal = 1,
                @UsuarioRegistro = %s,
                @Observacion = N'Sincronizado desde dashboard administrativo.'
            """,
            [numeric_corte_id, cupo, _trim_to_max(usuario_registro or 'SISTEMA', 50)],
        )
    return {
        'synced': True,
        'database': complement_database_name(),
        'version': version,
        'corte': _lower_keys(rows[0]) if rows else {'corteid': numeric_corte_id},
    }


def sync_student_enrollment_to_complement(
    *,
    corte_id: Any,
    codigo_estud: Any,
    usuario_registro: str = '',
    registrar_cargo_inicial: bool = True,
    valor_total_curso: Any = None,
    origen_matricula: str = '',
) -> dict[str, Any]:
    version = complement_version()
    if version == 'v5':
        required = [
            ('edu', 'usp_MatricularEstudianteCorte', 'P'),
            ('edu', 'VW_MatriculaEstudianteCompleta', 'V'),
        ]
    else:
        required = [
            ('edu', 'usp_MatricularEstudiantePrincipal', 'P'),
            ('edu', 'VW_MapeoCursoCompleto', 'V'),
        ]
    if not version or not is_complement_available(required):
        return _skipped_result('Base complementaria no disponible para matrícula de estudiantes.')

    numeric_corte_id = _safe_int(corte_id)
    numeric_codigo_estud = _safe_int(codigo_estud)
    if numeric_corte_id <= 0 or numeric_codigo_estud <= 0:
        return _skipped_result('Datos insuficientes para sincronizar matrícula estudiantil.')

    if version == 'v5':
        primary_enrollment = _fetch_primary_one(
            """
            SELECT TOP (1)
                E.CorteEstudianteId,
                COALESCE(NULLIF(LTRIM(RTRIM(E.CedulaEst)), ''), NULLIF(LTRIM(RTRIM(D.Cedula_Est)), '')) AS CedulaEst,
                COALESCE(NULLIF(LTRIM(RTRIM(E.ApellidosNombre)), ''), NULLIF(LTRIM(RTRIM(D.Apellidos_nombre)), '')) AS ApellidosNombre,
                NULLIF(LTRIM(RTRIM(D.correo)), '') AS CorreoPersonal,
                NULLIF(LTRIM(RTRIM(D.correointec)), '') AS CorreoIntec,
                NULLIF(LTRIM(RTRIM(D.Usuario)), '') AS UsuarioLogin
            FROM dbo.CORTE_CURSO_ESTUDIANTE E
            LEFT JOIN dbo.DATOS_ESTUD D ON D.codigo_estud = E.CodigoEstud
            WHERE E.CorteId = %s AND E.CodigoEstud = %s
            ORDER BY E.CorteEstudianteId DESC
            """,
            [numeric_corte_id, numeric_codigo_estud],
        )
        if not primary_enrollment:
            return _skipped_result('La matrícula no existe en la base principal INTECBDD.')

        _fetch_all(
            f"""
            SET XACT_ABORT ON;

            MERGE {_qualified('edu', 'EstudiantePrincipalSnapshot')} AS T
            USING (SELECT %s AS CodigoEstud) AS S
               ON T.CodigoEstud = S.CodigoEstud
            WHEN MATCHED THEN UPDATE SET
                CedulaEst = %s,
                ApellidosNombre = %s,
                CorreoPersonal = %s,
                CorreoIntec = %s,
                UsuarioLogin = %s,
                FechaSincronizacion = sysdatetime()
            WHEN NOT MATCHED THEN INSERT (
                CodigoEstud, CedulaEst, ApellidosNombre,
                CorreoPersonal, CorreoIntec, UsuarioLogin
            ) VALUES (%s, %s, %s, %s, %s, %s);

            IF NOT EXISTS (
                SELECT 1 FROM {_qualified('edu', 'CorteCurso')} WHERE CorteId = %s
            )
                THROW 53120, 'La corte debe configurarse antes de matricular.', 1;

            IF NOT EXISTS (
                SELECT 1 FROM {_qualified('edu', 'CorteEstudiante')}
                WHERE CorteId = %s AND CodigoEstud = %s
            ) AND EXISTS (
                SELECT 1
                FROM {_qualified('edu', 'VW_CupoCorte')}
                WHERE CorteId = %s
                  AND PermiteSobrecupo = 0
                  AND CuposDisponibles <= 0
            )
                THROW 53123, 'No hay cupos disponibles para este corte.', 1;

            MERGE {_qualified('edu', 'CorteEstudiante')} AS T
            USING (SELECT %s AS CorteId, %s AS CodigoEstud) AS S
               ON T.CorteId = S.CorteId AND T.CodigoEstud = S.CodigoEstud
            WHEN MATCHED THEN UPDATE SET
                CorteEstudianteIdPrincipal = %s,
                EstadoMatricula = CASE
                    WHEN T.EstadoMatricula IN ('RETIRADO','ANULADO') THEN 'INSCRITO'
                    ELSE T.EstadoMatricula
                END,
                UsuarioModifica = %s,
                FechaModifica = sysdatetime()
            WHEN NOT MATCHED THEN INSERT (
                CorteId, CodigoEstud, CorteEstudianteIdPrincipal,
                EstadoMatricula, TipoIngreso, UsuarioRegistro
            ) VALUES (%s, %s, %s, 'INSCRITO', 'IMPORTADO', %s);

            DECLARE @EstudianteCorteId int;
            DECLARE @CuentaId int;
            DECLARE @ValorCurso decimal(18,2);

            SELECT @EstudianteCorteId = EstudianteCorteId
            FROM {_qualified('edu', 'CorteEstudiante')}
            WHERE CorteId = %s AND CodigoEstud = %s;

            SELECT @ValorCurso = ValorCurso
            FROM {_qualified('edu', 'CorteCurso')}
            WHERE CorteId = %s;

            IF NOT EXISTS (
                SELECT 1 FROM {_qualified('fin', 'CuentaEstudiante')}
                WHERE EstudianteCorteId = @EstudianteCorteId
            )
                INSERT INTO {_qualified('fin', 'CuentaEstudiante')}
                    (EstudianteCorteId, CorteId, CodigoEstud, UsuarioRegistro, Observacion)
                VALUES
                    (@EstudianteCorteId, %s, %s, %s, N'Cuenta creada desde la base principal.');

            SELECT @CuentaId = CuentaId
            FROM {_qualified('fin', 'CuentaEstudiante')}
            WHERE EstudianteCorteId = @EstudianteCorteId;

            IF %s = 1 AND ISNULL(@ValorCurso, 0) > 0
               AND NOT EXISTS (
                   SELECT 1 FROM {_qualified('fin', 'MovimientoCuenta')}
                   WHERE CuentaId = @CuentaId
                     AND EstadoMovimiento = 'ACTIVO'
                     AND TipoMovimiento = 'DEBE'
                     AND Concepto = N'VALOR CURSO'
               )
                INSERT INTO {_qualified('fin', 'MovimientoCuenta')}
                    (CuentaId, TipoMovimiento, Concepto, Valor, UsuarioRegistro, Observacion)
                VALUES
                    (@CuentaId, 'DEBE', N'VALOR CURSO', @ValorCurso, %s,
                     N'Cargo automático generado por matrícula sincronizada.');
            """,
            [
                numeric_codigo_estud,
                primary_enrollment.get('CedulaEst'),
                primary_enrollment.get('ApellidosNombre'),
                primary_enrollment.get('CorreoPersonal'),
                primary_enrollment.get('CorreoIntec'),
                primary_enrollment.get('UsuarioLogin'),
                numeric_codigo_estud,
                primary_enrollment.get('CedulaEst'),
                primary_enrollment.get('ApellidosNombre'),
                primary_enrollment.get('CorreoPersonal'),
                primary_enrollment.get('CorreoIntec'),
                primary_enrollment.get('UsuarioLogin'),
                numeric_corte_id,
                numeric_corte_id,
                numeric_codigo_estud,
                numeric_corte_id,
                numeric_corte_id,
                numeric_codigo_estud,
                primary_enrollment.get('CorteEstudianteId'),
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
                numeric_corte_id,
                numeric_codigo_estud,
                primary_enrollment.get('CorteEstudianteId'),
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
                numeric_corte_id,
                numeric_codigo_estud,
                numeric_corte_id,
                numeric_corte_id,
                numeric_codigo_estud,
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
                1 if registrar_cargo_inicial else 0,
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
            ],
        )
        row = _fetch_one(
            f"""
            SELECT TOP (1) *
            FROM {_qualified('edu', 'VW_MatriculaEstudianteCompleta')}
            WHERE CorteId = %s AND CodigoEstud = %s
            """,
            [numeric_corte_id, numeric_codigo_estud],
        )
        rows = [row] if row else []
    else:
        rows = _fetch_all(
            f"""
            EXEC {_qualified('edu', 'usp_MatricularEstudiantePrincipal')}
                @CorteId = %s,
                @CodigoEstud = %s,
                @RegistrarCargoInicial = %s,
                @UsuarioRegistro = %s
            """,
            [
                numeric_corte_id,
                numeric_codigo_estud,
                1 if registrar_cargo_inicial else 0,
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
            ],
        )
    charge_result = None
    if valor_total_curso not in (None, ''):
        charge_result = ensure_student_course_charge(
            corte_id=numeric_corte_id,
            codigo_estud=numeric_codigo_estud,
            target_value=valor_total_curso,
            origin=origen_matricula,
            usuario_registro=usuario_registro,
        )
    return {
        'synced': True,
        'database': complement_database_name(),
        'version': version,
        'matricula': _lower_keys(rows[0]) if rows else {},
        'course_charge': charge_result,
    }


def ensure_student_course_charge(
    *,
    corte_id: Any,
    codigo_estud: Any,
    target_value: Any,
    origin: str = '',
    usuario_registro: str = '',
) -> dict[str, Any]:
    numeric_corte_id = _safe_int(corte_id)
    numeric_codigo_estud = _safe_int(codigo_estud)
    try:
        target = Decimal(str(target_value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ContinuingEducationError('El valor total del curso no es válido.') from exc
    if numeric_corte_id <= 0 or numeric_codigo_estud <= 0 or target <= 0:
        raise ContinuingEducationError('Datos insuficientes para ajustar el valor del curso.')
    required = [
        ('edu', 'CorteEstudiante', 'U'),
        ('fin', 'CuentaEstudiante', 'U'),
        ('fin', 'MovimientoCuenta', 'U'),
        ('fin', 'usp_RegistrarMovimientoCuenta', 'P'),
    ]
    if not is_complement_available(required):
        raise ContinuingEducationError('El módulo financiero complementario no está disponible.')

    row = _fetch_one(
        f"""
        SELECT TOP (1)
            CE.[EstudianteCorteId],
            C.[CuentaId],
            ISNULL(SUM(CASE
                WHEN M.[EstadoMovimiento] = 'ACTIVO' AND M.[TipoMovimiento] = 'DEBE'
                THEN M.[Valor] ELSE 0 END), 0) AS [TotalCargo]
            ,ISNULL(SUM(CASE
                WHEN M.[EstadoMovimiento] = 'ACTIVO'
                 AND M.[TipoMovimiento] = 'HABER'
                 AND UPPER(ISNULL(M.[FormaPago], '')) = 'DESCUENTO'
                THEN M.[Valor] ELSE 0 END), 0) AS [TotalDescuento]
        FROM {_qualified('edu', 'CorteEstudiante')} CE
        INNER JOIN {_qualified('fin', 'CuentaEstudiante')} C
            ON C.[EstudianteCorteId] = CE.[EstudianteCorteId]
        LEFT JOIN {_qualified('fin', 'MovimientoCuenta')} M
            ON M.[CuentaId] = C.[CuentaId]
        WHERE CE.[CorteId] = %s AND CE.[CodigoEstud] = %s
        GROUP BY CE.[EstudianteCorteId], C.[CuentaId]
        """,
        [numeric_corte_id, numeric_codigo_estud],
    )
    if not row:
        raise ContinuingEducationError('No se encontró la cuenta financiera de la matrícula.')
    current = Decimal(str(row.get('TotalCargo') or 0)).quantize(Decimal('0.01'))
    current_discount = Decimal(str(row.get('TotalDescuento') or 0)).quantize(Decimal('0.01'))
    current_net = max(current - current_discount, Decimal('0.00'))
    charge_difference = target - current
    discount_difference = current_net - target
    if charge_difference <= 0 and discount_difference <= 0:
        return {
            'adjusted': False,
            'origin': _clean_text(origin).upper(),
            'previous_value': str(current),
            'previous_net_value': str(current_net),
            'target_value': str(target),
            'added_value': '0.00',
            'discount_value': '0.00',
        }

    clean_origin = _clean_text(origin).upper() or 'SISTEMA'
    is_downward_adjustment = discount_difference > 0
    movement_value = discount_difference if is_downward_adjustment else charge_difference
    movement_type = 'HABER' if is_downward_adjustment else 'DEBE'
    payment_method = 'DESCUENTO' if is_downward_adjustment else 'AJUSTE_VALOR_CURSO'
    concept = (
        f'AJUSTE VALOR NETO CURSO - {clean_origin}'
        if is_downward_adjustment
        else f'AJUSTE VALOR CURSO - {clean_origin}'
    )
    movement = _fetch_one(
        f"""
        EXEC {_qualified('fin', 'usp_RegistrarMovimientoCuenta')}
            @CuentaId = %s,
            @TipoMovimiento = %s,
            @Concepto = %s,
            @Valor = %s,
            @FormaPago = %s,
            @UsuarioRegistro = %s,
            @Observacion = %s
        """,
        [
            row.get('CuentaId'),
            movement_type,
            _trim_to_max(concept, 200),
            movement_value,
            payment_method,
            _trim_to_max(usuario_registro or 'SISTEMA', 50),
            _trim_to_max(
                (
                    f'Valor neto ajustado de {current_net:.2f} a {target:.2f} '
                    f'según origen de matrícula {clean_origin}.'
                    if is_downward_adjustment
                    else f'Cargo ajustado de {current:.2f} a {target:.2f} '
                    f'según origen de matrícula {clean_origin}.'
                ),
                500,
            ),
        ],
    )
    cache.delete(f'dashboard:continuing-education-payment-metrics:v5:{complement_database_name()}')
    return {
        'adjusted': True,
        'origin': clean_origin,
        'previous_value': str(current),
        'previous_net_value': str(current_net),
        'target_value': str(target),
        'added_value': str(movement_value) if not is_downward_adjustment else '0.00',
        'discount_value': str(movement_value) if is_downward_adjustment else '0.00',
        'adjustment_type': 'DISCOUNT' if is_downward_adjustment else 'CHARGE',
        'movement': _lower_keys(movement or {}),
    }


def sync_teacher_assignment_to_complement(
    *,
    codigo_doc: Any,
    cedula_doc: str = '',
    assignment: dict[str, Any],
    usuario_registro: str = '',
) -> dict[str, Any]:
    version = complement_version()
    if version == 'v5':
        required = [
            ('edu', 'usp_MatricularDocenteCorte', 'P'),
            ('edu', 'VW_CorteCursoDetalle', 'V'),
            ('edu', 'VW_MatriculaDocenteCompleta', 'V'),
        ]
    else:
        required = [
            ('edu', 'usp_AsignarDocenteCorte', 'P'),
            ('edu', 'VW_CursosPrincipal', 'V'),
            ('edu', 'VW_DocentesCorte', 'V'),
        ]
    if not version or not is_complement_available(required):
        return _skipped_result('Base complementaria no disponible para matrícula docente.')

    cut = resolve_cut_for_assignment(assignment)
    if not cut:
        return _skipped_result('No existe CorteId equivalente para la materia/período seleccionado.')

    if version == 'v5':
        numeric_codigo_doc = _safe_int(codigo_doc)
        primary_teacher = _fetch_primary_one(
            """
            SELECT TOP (1)
                D.codigo_doc AS CodigoDocente,
                LTRIM(RTRIM(D.cedula_doc)) AS CedulaDoc,
                LTRIM(RTRIM(D.apellidos_nombre)) AS ApellidosNombre,
                NULLIF(LTRIM(RTRIM(D.correop)), '') AS CorreoPersonal,
                NULLIF(LTRIM(RTRIM(D.correo)), '') AS CorreoIntec,
                NULLIF(LTRIM(RTRIM(U.login)), '') AS UsuarioLogin
            FROM dbo.DATOSDOCENTE D
            LEFT JOIN dbo.USUARIOS U
              ON LTRIM(RTRIM(U.cedula)) = LTRIM(RTRIM(D.cedula_doc))
            WHERE D.codigo_doc = %s
            ORDER BY U.fecha_ingreso DESC
            """,
            [numeric_codigo_doc],
        )
        if not primary_teacher:
            return _skipped_result('El docente no existe en la base principal INTECBDD.')

        _fetch_all(
            f"""
            SET XACT_ABORT ON;

            MERGE {_qualified('edu', 'DocentePrincipalSnapshot')} AS T
            USING (SELECT %s AS CodigoDocente) AS S
               ON T.CodigoDocente = S.CodigoDocente
            WHEN MATCHED THEN UPDATE SET
                CedulaDoc = %s,
                ApellidosNombre = %s,
                CorreoPersonal = %s,
                CorreoIntec = %s,
                UsuarioLogin = %s,
                FechaSincronizacion = sysdatetime()
            WHEN NOT MATCHED THEN INSERT (
                CodigoDocente, CedulaDoc, ApellidosNombre,
                CorreoPersonal, CorreoIntec, UsuarioLogin
            ) VALUES (%s, %s, %s, %s, %s, %s);

            MERGE {_qualified('edu', 'CorteDocente')} AS T
            USING (SELECT %s AS CorteId, %s AS CodigoDocente) AS S
               ON T.CorteId = S.CorteId AND T.CodigoDocente = S.CodigoDocente
            WHEN MATCHED THEN UPDATE SET
                RolDocente = 'TITULAR',
                EstadoDocenteCorte = 'ACTIVO',
                UsuarioModifica = %s,
                FechaModifica = sysdatetime()
            WHEN NOT MATCHED THEN INSERT (
                CorteId, CodigoDocente, RolDocente,
                EstadoDocenteCorte, UsuarioRegistro
            ) VALUES (%s, %s, 'TITULAR', 'ACTIVO', %s);
            """,
            [
                numeric_codigo_doc,
                primary_teacher.get('CedulaDoc'),
                primary_teacher.get('ApellidosNombre'),
                primary_teacher.get('CorreoPersonal'),
                primary_teacher.get('CorreoIntec'),
                primary_teacher.get('UsuarioLogin'),
                numeric_codigo_doc,
                primary_teacher.get('CedulaDoc'),
                primary_teacher.get('ApellidosNombre'),
                primary_teacher.get('CorreoPersonal'),
                primary_teacher.get('CorreoIntec'),
                primary_teacher.get('UsuarioLogin'),
                _safe_int(cut.get('corte_id')),
                numeric_codigo_doc,
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
                _safe_int(cut.get('corte_id')),
                numeric_codigo_doc,
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
            ],
        )
        row = _fetch_one(
            f"""
            SELECT TOP (1) *
            FROM {_qualified('edu', 'VW_MatriculaDocenteCompleta')}
            WHERE CorteId = %s AND CodigoDocente = %s
            """,
            [_safe_int(cut.get('corte_id')), numeric_codigo_doc],
        )
        rows = [row] if row else []
    else:
        rows = _fetch_all(
            f"""
            EXEC {_qualified('edu', 'usp_AsignarDocenteCorte')}
                @CorteId = %s,
                @CodigoDocente = %s,
                @CedulaDoc = %s,
                @RolDocente = 'TITULAR',
                @UsuarioRegistro = %s
            """,
            [
                _safe_int(cut.get('corte_id')),
                _safe_int(codigo_doc) or None,
                _trim_to_max(cedula_doc, 20) or None,
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
            ],
        )
    return {
        'synced': True,
        'database': complement_database_name(),
        'version': version,
        'corte': cut,
        'docente_corte': _lower_keys(rows[0]) if rows else {},
    }


def resolve_cut_for_assignment(assignment: dict[str, Any]) -> dict[str, Any] | None:
    version = complement_version()
    required = [('edu', 'VW_CorteCursoDetalle', 'V')] if version == 'v5' else [('edu', 'VW_CursosPrincipal', 'V')]
    if not version or not is_complement_available(required):
        return None

    corte_id = _safe_int(assignment.get('corte_id') or assignment.get('CorteId'))
    params: list[Any] = []
    column_prefix = 'D.' if version == 'v5' else ''
    where = [f"{column_prefix}[EstadoCorteEdu] <> 'ANULADO'"] if version == 'v5' else ["[EstadoCorte] <> 'ANULADO'"]
    if corte_id > 0:
        where.append(f'{column_prefix}[CorteId] = %s')
        params.append(corte_id)
    else:
        cod_anio_basica = _clean_text(assignment.get('cod_anio_basica'))
        codigo_materia = _clean_text(assignment.get('codigo_materia'))
        codigo_periodo = _clean_text(assignment.get('codigo_periodo'))
        if not codigo_materia or not codigo_periodo:
            return None
        where.append(f"LTRIM(RTRIM(CAST({column_prefix}[CodigoMateria] AS varchar(30)))) = %s")
        where.append(f"LTRIM(RTRIM(CAST({column_prefix}[CodigoPeriodo] AS varchar(30)))) = %s")
        params.extend([codigo_materia, codigo_periodo])
        if cod_anio_basica:
            where.append(f"LTRIM(RTRIM(CAST({column_prefix}[Cod_AnioBasica] AS varchar(30)))) = %s")
            params.append(cod_anio_basica)

    if version == 'v5':
        row = _fetch_one(
            f"""
            SELECT TOP (1)
                D.[CorteId],
                D.[TipoOferta],
                D.[Cod_AnioBasica],
                D.[CodigoPeriodo],
                D.[CodigoMateria],
                D.[CodCurso],
                D.[NombreCursoMateria] AS [NombreCurso],
                D.[NombreCorte],
                D.[EstadoCorteEdu] AS [EstadoCorte],
                D.[CupoMaximo],
                CU.[TotalMatriculadosActivos]
            FROM {_qualified('edu', 'VW_CorteCursoDetalle')} D
            LEFT JOIN {_qualified('edu', 'VW_CupoCorte')} CU
                ON CU.[CorteId] = D.[CorteId]
            WHERE {' AND '.join(where)}
            ORDER BY
                CASE WHEN D.[EstadoCorteEdu] = 'ABIERTO' THEN 0 ELSE 1 END,
                D.[FechaInicio] DESC,
                D.[CorteId] DESC
            """,
            params,
        )
    else:
        row = _fetch_one(
            f"""
            SELECT TOP (1)
                [CorteId],
                [TipoOferta],
                [Cod_AnioBasica],
                [CodigoPeriodo],
                [CodigoMateria],
                [CodCurso],
                [NombreCurso],
                [NombreCorte],
                [EstadoCorte],
                [CupoMaximo],
                [TotalMatriculadosActivos]
            FROM {_qualified('edu', 'VW_CursosPrincipal')}
            WHERE {' AND '.join(where)}
            ORDER BY
                CASE WHEN [EstadoCorte] = 'ABIERTO' THEN 0 ELSE 1 END,
                [FechaInicio] DESC,
                [CorteId] DESC
            """,
            params,
        )
    return _serialize_cut(row) if row else None


def fetch_teacher_courses_from_complement(teacher: dict[str, Any]) -> list[dict[str, Any]] | None:
    required = [
        ('edu', 'VW_DocentesCorte', 'V'),
        ('edu', 'VW_CursosPrincipal', 'V'),
        ('edu', 'VW_MatriculasPrincipal', 'V'),
        ('edu', 'SesionCorte', 'U'),
        ('edu', 'AsistenciaCorte', 'U'),
        ('edu', 'CalificacionCorte', 'U'),
    ]
    if not is_complement_available(required):
        return None

    rows = _fetch_all(
        f"""
        SELECT
            CP.[CorteId],
            CP.[TipoOferta],
            CP.[Cod_AnioBasica],
            CP.[NombreCarrera],
            CP.[CodigoPeriodo],
            CP.[PeriodoDetalle],
            CP.[CodigoMateria],
            CP.[CodigoMateriaTexto],
            CP.[CodCurso],
            CP.[NombreCurso],
            CP.[NombreCorte],
            CP.[EstadoCorte],
            DC.[RolDocente],
            DC.[EstadoDocenteCorte],
            COUNT(DISTINCT MP.[CorteEstudianteId]) AS [Estudiantes],
            COUNT(DISTINCT S.[SesionCorteId]) AS [TotalSesiones],
            COUNT(A.[AsistenciaCorteId]) AS [RegistrosAsistencia],
            COUNT(DISTINCT CASE WHEN A.[AsistenciaCorteId] IS NOT NULL THEN A.[SesionCorteId] END) AS [ClasesRegistradas],
            SUM(CASE WHEN A.[CuentaParaAsistencia] = 1 THEN 1 ELSE 0 END) AS [AsistenciasMarcadas],
            COUNT(CAL.[CalificacionCorteId]) AS [RegistrosCalificados],
            AVG(CAST(CAL.[NotaFinal] AS float)) AS [PromedioFinal]
        FROM {_qualified('edu', 'VW_DocentesCorte')} DC
        INNER JOIN {_qualified('edu', 'VW_CursosPrincipal')} CP
            ON CP.[CorteId] = DC.[CorteId]
        LEFT JOIN {_qualified('edu', 'VW_MatriculasPrincipal')} MP
            ON MP.[CorteId] = CP.[CorteId]
           AND (MP.[EstadoRegistro] COLLATE DATABASE_DEFAULT) = 'A'
        LEFT JOIN {_qualified('edu', 'SesionCorte')} S
            ON S.[CorteId] = CP.[CorteId]
           AND S.[EstadoSesion] IN ('PROGRAMADA','REALIZADA')
        LEFT JOIN {_qualified('edu', 'AsistenciaCorte')} A
            ON A.[SesionCorteId] = S.[SesionCorteId]
           AND A.[CorteEstudianteId] = MP.[CorteEstudianteId]
        LEFT JOIN {_qualified('edu', 'CalificacionCorte')} CAL
            ON CAL.[CorteEstudianteId] = MP.[CorteEstudianteId]
        WHERE DC.[EstadoDocenteCorte] = 'ACTIVO'
          AND (
            LTRIM(RTRIM(CAST(DC.[CodigoDocente] AS varchar(50)))) = %s
            OR (DC.[CedulaDoc] COLLATE DATABASE_DEFAULT) = %s
          )
        GROUP BY
            CP.[CorteId],
            CP.[TipoOferta],
            CP.[Cod_AnioBasica],
            CP.[NombreCarrera],
            CP.[CodigoPeriodo],
            CP.[PeriodoDetalle],
            CP.[CodigoMateria],
            CP.[CodigoMateriaTexto],
            CP.[CodCurso],
            CP.[NombreCurso],
            CP.[NombreCorte],
            CP.[EstadoCorte],
            DC.[RolDocente],
            DC.[EstadoDocenteCorte],
            CP.[FechaInicio]
        ORDER BY
            CP.[FechaInicio] DESC,
            CP.[CorteId] DESC
        """,
        [_clean_text(teacher.get('codigo_doc')), _clean_text(teacher.get('cedula'))],
    )
    return [_serialize_teacher_course(row) for row in rows]


def fetch_attendance_roster_from_complement(
    teacher: dict[str, Any],
    course_payload: dict[str, Any],
    attendance_date: date,
) -> dict[str, Any] | None:
    required = [
        ('edu', 'VW_DocentesCorte', 'V'),
        ('edu', 'VW_CursosPrincipal', 'V'),
        ('edu', 'VW_MatriculasPrincipal', 'V'),
        ('edu', 'SesionCorte', 'U'),
        ('edu', 'AsistenciaCorte', 'U'),
    ]
    if not is_complement_available(required):
        return None

    course = fetch_teacher_course_from_complement(teacher, course_payload)
    if not course:
        return None

    session = _fetch_session_for_date(course['corte_id'], attendance_date)
    students = _fetch_complement_students(course['corte_id'], session_id=session.get('sesion_corte_id') if session else None)
    return {
        'course': course,
        'fecha': attendance_date.isoformat(),
        'session': session,
        'students': students,
    }


def save_attendance_to_complement(
    teacher: dict[str, Any],
    course_payload: dict[str, Any],
    attendance_date: date,
    attendance_time: time,
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    required = [
        ('edu', 'VW_DocentesCorte', 'V'),
        ('edu', 'VW_CursosPrincipal', 'V'),
        ('edu', 'VW_MatriculasPrincipal', 'V'),
        ('edu', 'SesionCorte', 'U'),
        ('edu', 'AsistenciaCorte', 'U'),
        ('edu', 'usp_RegistrarAsistenciaCorte', 'P'),
    ]
    if not is_complement_available(required):
        return None

    course = fetch_teacher_course_from_complement(teacher, course_payload)
    if not course:
        return None

    valid_ids = _fetch_valid_corte_estudiante_ids(course['corte_id'])
    clean_records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        corte_estudiante_id = _clean_text(
            record.get('corte_estudiante_id')
            or record.get('codigo_estud')
            or record.get('student_id')
        )
        if not corte_estudiante_id or corte_estudiante_id in seen or corte_estudiante_id not in valid_ids:
            continue
        seen.add(corte_estudiante_id)
        clean_records.append(
            {
                'corte_estudiante_id': corte_estudiante_id,
                'estado': 'PRESENTE' if _truthy_attendance(record.get('asistencia')) else 'AUSENTE',
            }
        )

    if not clean_records:
        raise ContinuingEducationError('No hay estudiantes válidos para guardar asistencia.')

    session = _ensure_session_for_attendance(
        course['corte_id'],
        attendance_date,
        attendance_time,
        usuario_registro=_clean_text(teacher.get('login')) or 'DOCENTE',
    )
    for record in clean_records:
        _fetch_all(
            f"""
            EXEC {_qualified('edu', 'usp_RegistrarAsistenciaCorte')}
                @SesionCorteId = %s,
                @CorteEstudianteId = %s,
                @EstadoAsistencia = %s,
                @UsuarioRegistro = %s
            """,
            [
                _safe_int(session['sesion_corte_id']),
                _safe_int(record['corte_estudiante_id']),
                record['estado'],
                _trim_to_max(_clean_text(teacher.get('login')) or 'DOCENTE', 50),
            ],
        )

    students = _fetch_complement_students(course['corte_id'], session_id=session['sesion_corte_id'])
    return {
        'course': course,
        'fecha': attendance_date.isoformat(),
        'hora': attendance_time.strftime('%H:%M'),
        'session': session,
        'saved': len(clean_records),
        'students': students,
    }


def fetch_teacher_course_from_complement(
    teacher: dict[str, Any],
    course_payload: dict[str, Any],
) -> dict[str, Any] | None:
    corte_id = _safe_int(course_payload.get('corte_id') or course_payload.get('CorteId'))
    if corte_id <= 0:
        return None

    row = _fetch_one(
        f"""
        SELECT TOP (1)
            CP.[CorteId],
            CP.[TipoOferta],
            CP.[Cod_AnioBasica],
            CP.[NombreCarrera],
            CP.[CodigoPeriodo],
            CP.[PeriodoDetalle],
            CP.[CodigoMateria],
            CP.[CodigoMateriaTexto],
            CP.[CodCurso],
            CP.[NombreCurso],
            CP.[NombreCorte],
            CP.[EstadoCorte],
            DC.[RolDocente],
            DC.[EstadoDocenteCorte],
            CAST(0 AS int) AS [Estudiantes],
            CAST(0 AS int) AS [RegistrosAsistencia],
            CAST(0 AS int) AS [ClasesRegistradas],
            CAST(0 AS int) AS [AsistenciasMarcadas],
            CAST(0 AS int) AS [RegistrosCalificados],
            CAST(NULL AS float) AS [PromedioFinal]
        FROM {_qualified('edu', 'VW_DocentesCorte')} DC
        INNER JOIN {_qualified('edu', 'VW_CursosPrincipal')} CP
            ON CP.[CorteId] = DC.[CorteId]
        WHERE CP.[CorteId] = %s
          AND DC.[EstadoDocenteCorte] = 'ACTIVO'
          AND (
            LTRIM(RTRIM(CAST(DC.[CodigoDocente] AS varchar(50)))) = %s
            OR (DC.[CedulaDoc] COLLATE DATABASE_DEFAULT) = %s
          )
        """,
        [corte_id, _clean_text(teacher.get('codigo_doc')), _clean_text(teacher.get('cedula'))],
    )
    return _serialize_teacher_course(row) if row else None


def _fetch_session_for_date(corte_id: Any, attendance_date: date) -> dict[str, str] | None:
    row = _fetch_one(
        f"""
        SELECT TOP (1)
            [SesionCorteId],
            [FechaClase],
            CONVERT(varchar(5), [HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), [HoraFin], 108) AS [HoraFin],
            [EstadoSesion]
        FROM {_qualified('edu', 'SesionCorte')}
        WHERE [CorteId] = %s
          AND [FechaClase] = %s
          AND [EstadoSesion] <> 'CANCELADA'
        ORDER BY [HoraInicio] ASC, [SesionCorteId] ASC
        """,
        [_safe_int(corte_id), attendance_date],
    )
    return _serialize_session(row) if row else None


def _ensure_session_for_attendance(
    corte_id: Any,
    attendance_date: date,
    attendance_time: time,
    *,
    usuario_registro: str,
) -> dict[str, str]:
    existing = _fetch_one(
        f"""
        SELECT TOP (1)
            [SesionCorteId],
            [FechaClase],
            CONVERT(varchar(5), [HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), [HoraFin], 108) AS [HoraFin],
            [EstadoSesion]
        FROM {_qualified('edu', 'SesionCorte')}
        WHERE [CorteId] = %s
          AND [FechaClase] = %s
          AND [HoraInicio] = %s
          AND [EstadoSesion] <> 'CANCELADA'
        ORDER BY [SesionCorteId] ASC
        """,
        [_safe_int(corte_id), attendance_date, attendance_time],
    )
    if existing:
        return _serialize_session(existing)

    end_time = (datetime.combine(attendance_date, attendance_time) + timedelta(hours=1)).time().replace(second=0, microsecond=0)
    row = _fetch_one(
        f"""
        INSERT INTO {_qualified('edu', 'SesionCorte')}
            ([CorteId], [HorarioCorteId], [FechaClase], [HoraInicio], [HoraFin], [Modalidad], [EstadoSesion], [UsuarioRegistro], [Observacion])
        OUTPUT
            INSERTED.[SesionCorteId],
            INSERTED.[FechaClase],
            CONVERT(varchar(5), INSERTED.[HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), INSERTED.[HoraFin], 108) AS [HoraFin],
            INSERTED.[EstadoSesion]
        VALUES
            (%s, NULL, %s, %s, %s, 'PRESENCIAL', 'PROGRAMADA', %s, N'Sesión creada desde dashboard docente.')
        """,
        [_safe_int(corte_id), attendance_date, attendance_time, end_time, _trim_to_max(usuario_registro, 50)],
    )
    if not row:
        raise ContinuingEducationError('No fue posible crear la sesión de asistencia.')
    return _serialize_session(row)


def _fetch_complement_students(corte_id: Any, *, session_id: Any = None) -> list[dict[str, Any]]:
    rows = _fetch_all(
        f"""
        SELECT
            MP.[CorteEstudianteId],
            MP.[CodigoEstud],
            MP.[CedulaEst],
            MP.[ApellidosNombre],
            A.[EstadoAsistencia],
            A.[CuentaParaAsistencia],
            CONVERT(varchar(5), S.[HoraInicio], 108) AS [Hora]
        FROM {_qualified('edu', 'VW_MatriculasPrincipal')} MP
        LEFT JOIN {_qualified('edu', 'SesionCorte')} S
            ON S.[SesionCorteId] = %s
        LEFT JOIN {_qualified('edu', 'AsistenciaCorte')} A
            ON A.[SesionCorteId] = S.[SesionCorteId]
           AND A.[CorteEstudianteId] = MP.[CorteEstudianteId]
        WHERE MP.[CorteId] = %s
          AND (MP.[EstadoRegistro] COLLATE DATABASE_DEFAULT) = 'A'
        ORDER BY MP.[ApellidosNombre] ASC
        """,
        [_safe_int(session_id) if session_id else None, _safe_int(corte_id)],
    )
    return [_serialize_complement_student(row) for row in rows]


def _fetch_valid_corte_estudiante_ids(corte_id: Any) -> set[str]:
    rows = _fetch_all(
        f"""
        SELECT CAST([CorteEstudianteId] AS varchar(30)) AS [CorteEstudianteId]
        FROM {_qualified('edu', 'VW_MatriculasPrincipal')}
        WHERE [CorteId] = %s
          AND (EstadoRegistro COLLATE DATABASE_DEFAULT) = 'A'
        """,
        [_safe_int(corte_id)],
    )
    return {_clean_text(row.get('CorteEstudianteId')) for row in rows}


def _serialize_teacher_course(row: dict[str, Any]) -> dict[str, Any]:
    codigo_materia = _clean_text(row.get('CodigoMateria') or row.get('CodCurso') or row.get('CorteId'))
    cod_materia = _clean_text(row.get('CodigoMateriaTexto') or row.get('CodigoMateria') or row.get('CodCurso'))
    estado_corte = _clean_text(row.get('EstadoCorte')).upper()
    periodo = _clean_text(row.get('PeriodoDetalle') or row.get('NombreCorte') or row.get('CodigoPeriodo'))
    return {
        'source': 'continuing_education',
        'corte_id': _clean_text(row.get('CorteId')),
        'tipo_oferta': _clean_text(row.get('TipoOferta')),
        'cod_anio_basica': _clean_text(row.get('Cod_AnioBasica')),
        'carrera': _clean_text(row.get('NombreCarrera')) or 'Educación continua',
        'codigo_materia': codigo_materia,
        'cod_materia': cod_materia,
        'materia': _clean_text(row.get('NombreCurso') or row.get('NombreCorte')) or 'Sin materia',
        'codigo_periodo': _clean_text(row.get('CodigoPeriodo') or row.get('CorteId')),
        'periodo': periodo or 'Sin período',
        'estado_periodo': 'A' if estado_corte == 'ABIERTO' else estado_corte,
        'paralelo': 'A',
        'cod_jornada': '0',
        'jornada': 'N/D',
        'estudiantes': _safe_int(row.get('Estudiantes')),
        'registros_asistencia': _safe_int(row.get('RegistrosAsistencia')),
        'clases_registradas': _safe_int(row.get('ClasesRegistradas')),
        'asistencias_marcadas': _safe_int(row.get('AsistenciasMarcadas')),
        'registros_calificados': _safe_int(row.get('RegistrosCalificados')),
        'promedio_p1': None,
        'promedio_p2': None,
        'promedio_p3': None,
        'promedio_final': _round_float(row.get('PromedioFinal')),
    }


def _serialize_cut(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'corte_id': _clean_text(row.get('CorteId')),
        'tipo_oferta': _clean_text(row.get('TipoOferta')),
        'cod_anio_basica': _clean_text(row.get('Cod_AnioBasica')),
        'codigo_periodo': _clean_text(row.get('CodigoPeriodo')),
        'codigo_materia': _clean_text(row.get('CodigoMateria')),
        'cod_curso': _clean_text(row.get('CodCurso')),
        'nombre_curso': _clean_text(row.get('NombreCurso')),
        'nombre_corte': _clean_text(row.get('NombreCorte')),
        'estado_corte': _clean_text(row.get('EstadoCorte')),
        'cupo_maximo': _safe_int(row.get('CupoMaximo')),
        'total_matriculados': _safe_int(row.get('TotalMatriculadosActivos')),
    }


def _serialize_session(row: dict[str, Any]) -> dict[str, str]:
    return {
        'sesion_corte_id': _clean_text(row.get('SesionCorteId')),
        'fecha': _date_iso(row.get('FechaClase')),
        'hora_inicio': _clean_text(row.get('HoraInicio')),
        'hora_fin': _clean_text(row.get('HoraFin')),
        'estado': _clean_text(row.get('EstadoSesion')),
    }


def _serialize_complement_student(row: dict[str, Any]) -> dict[str, Any]:
    estado = _clean_text(row.get('EstadoAsistencia')).upper()
    present = bool(row.get('CuentaParaAsistencia')) or estado in {'PRESENTE', 'TARDANZA'}
    corte_estudiante_id = _clean_text(row.get('CorteEstudianteId'))
    return {
        'codigo_estud': corte_estudiante_id,
        'codigo_estud_principal': _clean_text(row.get('CodigoEstud')),
        'corte_estudiante_id': corte_estudiante_id,
        'cedula': _clean_text(row.get('CedulaEst')),
        'nombre': _clean_text(row.get('ApellidosNombre')) or 'Sin nombre',
        'estado_asistencia': estado,
        'asistencia': 1 if present else 0,
        'presente': present,
        'hora': _clean_text(row.get('Hora')),
    }


def _qualified(schema: str, object_name: str) -> str:
    return f'[{complement_database_name()}].[{_safe_identifier(schema)}].[{_safe_identifier(object_name)}]'


def _database_exists(db_name: str) -> bool:
    with complement_connection().cursor() as cursor:
        cursor.execute('SELECT DB_ID(%s)', [db_name])
        row = cursor.fetchone()
    return bool(row and row[0])


def _object_exists(db_name: str, schema: str, object_name: str, object_type: str) -> bool:
    object_path = f'{db_name}.{schema}.{object_name}'
    with complement_connection().cursor() as cursor:
        cursor.execute('SELECT OBJECT_ID(%s, %s)', [object_path, object_type])
        row = cursor.fetchone()
    return bool(row and row[0])


def _detect_complement_version(db_name: str) -> str:
    if _objects_available(db_name, _v5_core_required_objects()):
        return 'v5'
    if _objects_available(db_name, _v4_core_required_objects()):
        return 'v4'
    return ''


def _objects_available(db_name: str, required: list[tuple[str, str, str]]) -> bool:
    return not _missing_objects(db_name, required)


def _missing_objects(db_name: str, required: list[tuple[str, str, str]]) -> list[str]:
    missing: list[str] = []
    for schema, object_name, object_type in required:
        if not _object_exists(db_name, schema, object_name, object_type):
            missing.append(f'{db_name}.{schema}.{object_name}')
    return missing


def _v5_core_required_objects() -> list[tuple[str, str, str]]:
    return [
        ('edu', 'VW_CorteCursoDetalle', 'V'),
        ('edu', 'VW_MatriculaEstudianteCompleta', 'V'),
        ('edu', 'VW_MatriculaDocenteCompleta', 'V'),
        ('edu', 'VW_CupoCorte', 'V'),
        ('edu', 'CorteEstudiante', 'U'),
        ('edu', 'CorteDocente', 'U'),
    ]


def _v4_core_required_objects() -> list[tuple[str, str, str]]:
    return [
        ('edu', 'VW_CursosPrincipal', 'V'),
        ('edu', 'VW_MatriculasPrincipal', 'V'),
        ('edu', 'VW_DocentesCorte', 'V'),
        ('edu', 'VW_CupoDisponible', 'V'),
    ]


def _fetch_one(query: str, params: list[Any] | tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    rows = _fetch_all(query, params)
    return rows[0] if rows else None


def _fetch_primary_one(
    query: str,
    params: list[Any] | tuple[Any, ...] | None = None,
) -> dict[str, Any] | None:
    with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
        cursor.execute(query, params or [])
        row = cursor.fetchone()
        if row is None or cursor.description is None:
            return None
        columns = [column[0] for column in cursor.description]
        return dict(zip(columns, row))


def _fetch_all(query: str, params: list[Any] | tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with complement_connection().cursor() as cursor:
        cursor.execute(query, params or [])
        selected_rows: list[dict[str, Any]] | None = None
        while True:
            if cursor.description is not None:
                columns = [column[0] for column in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                if selected_rows is None:
                    selected_rows = rows
            if not cursor.nextset():
                break
        return selected_rows or []


def _safe_identifier(value: Any) -> str:
    clean_value = _clean_text(value)
    if not IDENTIFIER_PATTERN.fullmatch(clean_value):
        raise ContinuingEducationError(f'Identificador SQL no permitido: {clean_value}')
    return clean_value


def _skipped_result(message: str) -> dict[str, Any]:
    return {
        'synced': False,
        'database': complement_database_name(),
        'message': message,
    }


def _lower_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}


def _truthy_attendance(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    clean_value = _clean_text(value).lower()
    return clean_value not in {'', '0', 'false', 'no', 'ausente', 'falta'}


def _clean_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())


def _trim_to_max(value: Any, max_length: int) -> str:
    return str(value or '').strip()[:max_length]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _round_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _date_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _clean_text(value)
