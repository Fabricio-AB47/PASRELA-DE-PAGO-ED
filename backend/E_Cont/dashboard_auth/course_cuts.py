from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from django.db import connection, transaction


class CourseCutError(Exception):
    pass


SPANISH_MONTHS = {
    1: 'enero',
    2: 'febrero',
    3: 'marzo',
    4: 'abril',
    5: 'mayo',
    6: 'junio',
    7: 'julio',
    8: 'agosto',
    9: 'septiembre',
    10: 'octubre',
    11: 'noviembre',
    12: 'diciembre',
}
ECUADOR_TIMEZONE = ZoneInfo('America/Guayaquil')


def list_course_cuts() -> list[dict[str, Any]]:
    _ensure_course_cut_schema()
    query = """
        SELECT TOP (300)
            CorteId,
            TipoOferta,
            NumeroCorte,
            NombreCorte,
            FechaInicio,
            FechaFin,
            EstadoCorte,
            Cod_AnioBasica,
            Carrera,
            CodigoPeriodo,
            Periodo,
            CodigoMateria,
            MateriaPensum,
            CodCurso,
            CursoEduContinua,
            CupoEsperado,
            TotalEstudiantes,
            TotalInscritos,
            TotalCursando,
            TotalRetirados,
            TotalAprobados,
            TotalReprobados,
            TotalFinalizados
        FROM dbo.VW_CORTE_RESUMEN
        ORDER BY FechaInicio DESC, NumeroCorte DESC, CorteId DESC
    """
    return [_normalize_cut_summary(row) for row in _fetch_all(query, [])]


def create_course_cut(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_course_cut_schema()
    tipo_oferta = _clean_text(payload.get('tipo_oferta') or 'CARRERA').upper()
    if tipo_oferta not in {'CARRERA', 'EDUCONTINUA'}:
        raise CourseCutError('El tipo de oferta debe ser CARRERA o EDUCONTINUA.')

    fecha_inicio = _clean_text(payload.get('fecha_inicio'))
    if not fecha_inicio:
        raise CourseCutError('Debes ingresar la fecha de inicio de la corte.')
    if not _coerce_date(fecha_inicio):
        raise CourseCutError('La fecha de inicio de la corte no es válida.')

    fecha_fin = _clean_text(payload.get('fecha_fin')) or None
    if fecha_fin:
        parsed_fecha_fin = _coerce_date(fecha_fin)
        if not parsed_fecha_fin:
            raise CourseCutError('La fecha final de inscripción no es válida.')
        if parsed_fecha_fin < _today_ecuador():
            raise CourseCutError('La fecha final de inscripción no puede ser anterior a la fecha actual.')

    cupo_esperado = _int_or_none(payload.get('cupo_esperado'))
    horas = _int_or_none(payload.get('horas'))
    observacion = _clean_text(payload.get('observacion')) or None

    if tipo_oferta == 'EDUCONTINUA':
        return _create_educontinua_cut(
            payload,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            cupo_esperado=cupo_esperado,
            horas=horas,
            observacion=observacion,
            user_login=user_login,
        )

    cod_anio_basica = _clean_text(payload.get('cod_anio_basica'))
    codigo_periodo = _clean_text(payload.get('codigo_periodo'))
    subject_codes = _subject_codes_from_payload(payload)

    if not cod_anio_basica:
        raise CourseCutError('Debes seleccionar la carrera para crear la corte.')
    if not codigo_periodo:
        raise CourseCutError('Debes seleccionar el período para crear la corte.')
    if not subject_codes:
        raise CourseCutError('Debes seleccionar al menos una materia para crear la corte.')

    open_cut = _find_open_cut_subject_overlap(
        cod_anio_basica=cod_anio_basica,
        codigo_periodo=codigo_periodo,
        subject_codes=subject_codes,
    )
    if open_cut:
        subject = open_cut.get('MateriaPensum') or open_cut.get('CodigoMateria') or ''
        raise CourseCutError(
            f"Ya existe una corte abierta para la materia {subject}: "
            f"{open_cut.get('NombreCorte') or open_cut.get('CorteId')}."
        )

    numero_corte = _safe_int(payload.get('numero_corte'), default=0)
    if numero_corte <= 0:
        numero_corte = _next_batch_cut_number(cod_anio_basica, codigo_periodo, subject_codes)
    nombre_corte = _clean_text(payload.get('nombre_corte')) or f'Corte {numero_corte}'

    created_cuts: list[dict[str, Any]] = []
    with transaction.atomic():
        for subject_code in subject_codes:
            subject_hours = _resolve_pensum_hours(cod_anio_basica, subject_code)
            corte_id = _insert_course_cut(
                tipo_oferta='CARRERA',
                cod_anio_basica=cod_anio_basica,
                codigo_periodo=codigo_periodo,
                codigo_materia=subject_code,
                cod_curso=None,
                numero_corte=numero_corte,
                nombre_corte=nombre_corte,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                cupo_esperado=cupo_esperado,
                horas=subject_hours,
                observacion=observacion,
                user_login=user_login,
            )
            cut = _fetch_cut_by_id(corte_id)
            if cut:
                created_cuts.append(cut)

    if not created_cuts:
        raise CourseCutError('No fue posible crear la corte.')

    return {
        **created_cuts[0],
        'created_cuts': created_cuts,
        'created_count': len(created_cuts),
    }


def close_course_cut(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_course_cut_schema()
    corte_id = _safe_int(payload.get('corte_id') or payload.get('CorteId'), default=0)
    if corte_id <= 0:
        raise CourseCutError('Debes seleccionar la corte que deseas cerrar.')

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.CORTE_CURSO
            SET EstadoCorte = 'CERRADO',
                UsuarioModifica = %s,
                FechaModifica = SYSDATETIME()
            WHERE CorteId = %s
              AND EstadoCorte = 'ABIERTO'
            """,
            [_trim_to_max(user_login, 50) or 'SISTEMA', corte_id],
        )
        if cursor.rowcount <= 0:
            raise CourseCutError('No se pudo cerrar la corte. Verifica que exista y esté abierta.')

    closed = _fetch_cut_by_id(corte_id)
    return closed or {'corte_id': str(corte_id), 'estado_corte': 'CERRADO'}


def assign_matricula_to_open_cut(
    *,
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    num_matricula: int,
    usuario_registro: str = 'SISTEMA',
    observacion: str = '',
) -> dict[str, Any]:
    _ensure_course_cut_schema()
    cut = _find_open_cut(
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
    )
    if not cut:
        raise CourseCutError(
            'No existe una corte abierta para esta materia y período. '
            'Crea una corte abierta antes de matricular estudiantes.'
        )
    _ensure_cut_accepts_registrations(cut)

    student = _fetch_student_identity(codigo_estud)
    if cut['tipo_oferta'] == 'EDUCONTINUA':
        _merge_educontinua_student(cut, codigo_estud, codigo_materia, student, usuario_registro, observacion)
    else:
        _merge_carrera_student(
            cut,
            codigo_estud,
            cod_anio_basica,
            codigo_materia,
            codigo_periodo,
            num_matricula,
            student,
            usuario_registro,
            observacion,
        )

    _update_current_enrollment_cut(
        corte_id=cut['corte_id'],
        codigo_estud=codigo_estud,
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
        num_matricula=num_matricula,
    )

    return {
        **cut,
        'assigned': True,
        'fecha_inicio': _format_date_label(cut.get('fecha_inicio_raw')),
        'fecha_inicio_iso': _date_iso(cut.get('fecha_inicio_raw')),
    }


def ensure_open_cut_for_enrollment(
    *,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
) -> dict[str, Any]:
    _ensure_course_cut_schema()
    cut = _find_open_cut(
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
    )
    if not cut:
        raise CourseCutError(
            'No existe una corte abierta para esta materia y período. '
            'Crea una corte abierta antes de matricular estudiantes.'
        )
    _ensure_cut_accepts_registrations(cut)
    return {
        **cut,
        'fecha_inicio': _format_date_label(cut.get('fecha_inicio_raw')),
        'fecha_inicio_iso': _date_iso(cut.get('fecha_inicio_raw')),
    }


def _create_educontinua_cut(
    payload: dict[str, Any],
    *,
    fecha_inicio: str,
    fecha_fin: str | None,
    cupo_esperado: int | None,
    horas: int | None,
    observacion: str | None,
    user_login: str,
) -> dict[str, Any]:
    cod_curso = _clean_text(payload.get('cod_curso') or payload.get('codigo_materia'))
    if not cod_curso:
        raise CourseCutError('Debes seleccionar el curso de educación continua para crear la corte.')

    open_cut = _fetch_one(
        """
        SELECT TOP (1) CorteId, NombreCorte
        FROM dbo.CORTE_CURSO
        WHERE EstadoCorte = 'ABIERTO'
          AND TipoOferta = 'EDUCONTINUA'
          AND LTRIM(RTRIM(CAST(CodCurso AS varchar(20)))) = %s
        ORDER BY FechaInicio DESC, CorteId DESC
        """,
        [cod_curso],
    )
    if open_cut:
        raise CourseCutError(
            f"Ya existe una corte abierta para esta oferta: {open_cut.get('NombreCorte') or open_cut.get('CorteId')}."
        )

    numero_corte = _safe_int(payload.get('numero_corte'), default=0)
    if numero_corte <= 0:
        numero_corte = _next_educontinua_cut_number(cod_curso)
    nombre_corte = _clean_text(payload.get('nombre_corte')) or f'Corte {numero_corte}'

    corte_id = _insert_course_cut(
        tipo_oferta='EDUCONTINUA',
        cod_anio_basica=None,
        codigo_periodo=None,
        codigo_materia=None,
        cod_curso=cod_curso,
        numero_corte=numero_corte,
        nombre_corte=nombre_corte,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        cupo_esperado=cupo_esperado,
        horas=horas,
        observacion=observacion,
        user_login=user_login,
    )
    created = _fetch_cut_by_id(corte_id)
    return created or {'corte_id': str(corte_id), 'nombre_corte': nombre_corte}


def _insert_course_cut(
    *,
    tipo_oferta: str,
    cod_anio_basica: str | None,
    codigo_periodo: str | None,
    codigo_materia: str | None,
    cod_curso: str | None,
    numero_corte: int,
    nombre_corte: str,
    fecha_inicio: str,
    fecha_fin: str | None,
    cupo_esperado: int | None,
    horas: int | None,
    observacion: str | None,
    user_login: str,
) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO dbo.CORTE_CURSO (
                TipoOferta,
                Cod_AnioBasica,
                CodigoPeriodo,
                CodigoMateria,
                CodCurso,
                NumeroCorte,
                NombreCorte,
                FechaInicio,
                FechaFin,
                CupoEsperado,
                Horas,
                EstadoCorte,
                UsuarioRegistro,
                Observacion
            )
            OUTPUT INSERTED.CorteId
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ABIERTO', %s, %s)
            """,
            [
                tipo_oferta,
                _numeric_or_none(cod_anio_basica),
                _numeric_or_none(codigo_periodo),
                _numeric_or_none(codigo_materia),
                _numeric_or_none(cod_curso),
                numero_corte,
                nombre_corte,
                fecha_inicio,
                fecha_fin,
                cupo_esperado,
                horas,
                _trim_to_max(user_login, 50) or 'SISTEMA',
                observacion,
            ],
        )
        row = cursor.fetchone()
    if not row or row[0] is None:
        raise CourseCutError('No fue posible crear la corte.')
    return _safe_int(row[0], default=0)


def _find_open_cut(*, cod_anio_basica: str, codigo_materia: str, codigo_periodo: str) -> dict[str, Any] | None:
    query = """
        SELECT TOP (1)
            CC.CorteId,
            CC.TipoOferta,
            CC.NumeroCorte,
            CC.NombreCorte,
            CC.FechaInicio,
            CC.FechaFin,
            CC.EstadoCorte,
            CC.Cod_AnioBasica,
            CC.CodigoPeriodo,
            CC.CodigoMateria,
            CC.CodCurso,
            LTRIM(RTRIM(ISNULL(P.Nomb_Materia, ''))) AS MateriaPensum
        FROM dbo.CORTE_CURSO CC
        LEFT JOIN dbo.PENSUM P
          ON LTRIM(RTRIM(CAST(P.Cod_AnioBasica AS varchar(20)))) =
             LTRIM(RTRIM(CAST(CC.Cod_AnioBasica AS varchar(20))))
         AND LTRIM(RTRIM(CAST(P.codigo_materia AS varchar(50)))) =
             LTRIM(RTRIM(CAST(CC.CodigoMateria AS varchar(50))))
        WHERE CC.EstadoCorte = 'ABIERTO'
          AND (
              (
                  CC.TipoOferta = 'CARRERA'
                  AND LTRIM(RTRIM(CAST(CC.Cod_AnioBasica AS varchar(20)))) = %s
                  AND LTRIM(RTRIM(CAST(CC.CodigoPeriodo AS varchar(20)))) = %s
                  AND LTRIM(RTRIM(CAST(CC.CodigoMateria AS varchar(50)))) = %s
              )
              OR (
                  CC.TipoOferta = 'EDUCONTINUA'
                  AND LTRIM(RTRIM(CAST(CC.CodCurso AS varchar(20)))) = %s
              )
          )
        ORDER BY
            CASE WHEN CC.TipoOferta = 'CARRERA' THEN 0 ELSE 1 END,
            CC.FechaInicio ASC,
            CC.CorteId ASC
    """
    row = _fetch_one(query, [cod_anio_basica, codigo_periodo, codigo_materia, codigo_materia])
    return _normalize_cut(row) if row else None


def _ensure_cut_accepts_registrations(cut: dict[str, Any]) -> None:
    if cut.get('estado_corte') != 'ABIERTO':
        raise CourseCutError('La corte seleccionada no está abierta para recibir inscripciones.')

    fecha_fin = cut.get('fecha_fin_raw')
    if not _is_registration_deadline_expired(fecha_fin):
        return

    deadline = _format_date_label(fecha_fin)
    suffix = f' el {deadline}' if deadline else ''
    raise CourseCutError(
        f'La fecha final de inscripción para esta corte venció{suffix}. '
        'Cierra la corte o abre una nueva para permitir más ingresos.'
    )


def _merge_carrera_student(
    cut: dict[str, Any],
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    num_matricula: int,
    student: dict[str, str],
    usuario_registro: str,
    observacion: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            MERGE dbo.CORTE_CURSO_ESTUDIANTE AS T
            USING (
                SELECT
                    %s AS CorteId,
                    %s AS CodigoEstud,
                    %s AS CedulaEst,
                    %s AS ApellidosNombre,
                    %s AS Cod_AnioBasica,
                    %s AS CodigoPeriodo,
                    %s AS CodigoMateria,
                    %s AS Num_Matricula,
                    %s AS FechaInicioEstudiante
            ) AS S
            ON T.CorteId = S.CorteId AND T.CodigoEstud = S.CodigoEstud
            WHEN MATCHED THEN
                UPDATE SET
                    T.CedulaEst = S.CedulaEst,
                    T.ApellidosNombre = S.ApellidosNombre,
                    T.Cod_AnioBasica = S.Cod_AnioBasica,
                    T.CodigoPeriodo = S.CodigoPeriodo,
                    T.CodigoMateria = S.CodigoMateria,
                    T.Num_Matricula = S.Num_Matricula,
                    T.CodCurso = NULL,
                    T.FechaInicioEstudiante = S.FechaInicioEstudiante,
                    T.EstadoParticipacion = 'INSCRITO',
                    T.EstadoRegistro = 'A',
                    T.Observacion = %s,
                    T.UsuarioModifica = %s,
                    T.FechaModifica = SYSDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (
                    CorteId,
                    CodigoEstud,
                    CedulaEst,
                    ApellidosNombre,
                    Cod_AnioBasica,
                    CodigoPeriodo,
                    CodigoMateria,
                    Num_Matricula,
                    CodCurso,
                    FechaInicioEstudiante,
                    EstadoParticipacion,
                    EstadoRegistro,
                    Observacion,
                    UsuarioRegistro
                )
                VALUES (
                    S.CorteId,
                    S.CodigoEstud,
                    S.CedulaEst,
                    S.ApellidosNombre,
                    S.Cod_AnioBasica,
                    S.CodigoPeriodo,
                    S.CodigoMateria,
                    S.Num_Matricula,
                    NULL,
                    S.FechaInicioEstudiante,
                    'INSCRITO',
                    'A',
                    %s,
                    %s
                );
            """,
            [
                _safe_int(cut['corte_id'], default=0),
                _safe_int(codigo_estud, default=0),
                student.get('cedula'),
                student.get('nombre'),
                _safe_int(cod_anio_basica, default=0),
                _safe_int(codigo_periodo, default=0),
                _safe_int(codigo_materia, default=0),
                _safe_int(num_matricula, default=0),
                _date_iso(cut.get('fecha_inicio_raw')),
                _trim_to_max(observacion, 500) or None,
                _trim_to_max(usuario_registro, 50) or 'SISTEMA',
                _trim_to_max(observacion, 500) or None,
                _trim_to_max(usuario_registro, 50) or 'SISTEMA',
            ],
        )


def _merge_educontinua_student(
    cut: dict[str, Any],
    codigo_estud: str,
    codigo_materia: str,
    student: dict[str, str],
    usuario_registro: str,
    observacion: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            MERGE dbo.CORTE_CURSO_ESTUDIANTE AS T
            USING (
                SELECT
                    %s AS CorteId,
                    %s AS CodigoEstud,
                    %s AS CedulaEst,
                    %s AS ApellidosNombre,
                    %s AS CodCurso,
                    %s AS FechaInicioEstudiante
            ) AS S
            ON T.CorteId = S.CorteId AND T.CodigoEstud = S.CodigoEstud
            WHEN MATCHED THEN
                UPDATE SET
                    T.CedulaEst = S.CedulaEst,
                    T.ApellidosNombre = S.ApellidosNombre,
                    T.Cod_AnioBasica = NULL,
                    T.CodigoPeriodo = NULL,
                    T.CodigoMateria = NULL,
                    T.Num_Matricula = NULL,
                    T.CodCurso = S.CodCurso,
                    T.FechaInicioEstudiante = S.FechaInicioEstudiante,
                    T.EstadoParticipacion = 'INSCRITO',
                    T.EstadoRegistro = 'A',
                    T.Observacion = %s,
                    T.UsuarioModifica = %s,
                    T.FechaModifica = SYSDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (
                    CorteId,
                    CodigoEstud,
                    CedulaEst,
                    ApellidosNombre,
                    Cod_AnioBasica,
                    CodigoPeriodo,
                    CodigoMateria,
                    Num_Matricula,
                    CodCurso,
                    FechaInicioEstudiante,
                    EstadoParticipacion,
                    EstadoRegistro,
                    Observacion,
                    UsuarioRegistro
                )
                VALUES (
                    S.CorteId,
                    S.CodigoEstud,
                    S.CedulaEst,
                    S.ApellidosNombre,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    S.CodCurso,
                    S.FechaInicioEstudiante,
                    'INSCRITO',
                    'A',
                    %s,
                    %s
                );
            """,
            [
                _safe_int(cut['corte_id'], default=0),
                _safe_int(codigo_estud, default=0),
                student.get('cedula'),
                student.get('nombre'),
                _safe_int(codigo_materia, default=0),
                _date_iso(cut.get('fecha_inicio_raw')),
                _trim_to_max(observacion, 500) or None,
                _trim_to_max(usuario_registro, 50) or 'SISTEMA',
                _trim_to_max(observacion, 500) or None,
                _trim_to_max(usuario_registro, 50) or 'SISTEMA',
            ],
        )


def _update_current_enrollment_cut(
    *,
    corte_id: str,
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    num_matricula: int,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.CABECERA_MATRICULA
            SET CorteId = %s
            WHERE CAST(codigo_estud AS varchar(50)) = %s
              AND CAST(cod_anio_Basica AS varchar(20)) = %s
              AND CAST(codigo_periodo AS varchar(20)) = %s
              AND CAST(Num_Matricula AS varchar(20)) = %s
            """,
            [corte_id, str(codigo_estud), str(cod_anio_basica), str(codigo_periodo), str(num_matricula)],
        )
        cursor.execute(
            """
            UPDATE dbo.CARRERAXESTUD
            SET CorteId = %s
            WHERE CAST(codigo_estud AS varchar(50)) = %s
              AND CAST(cod_anio_Basica AS varchar(20)) = %s
              AND CAST(codigo_periodo AS varchar(20)) = %s
              AND CAST(codigo_materia AS varchar(50)) = %s
              AND CAST(Num_Matricula AS varchar(20)) = %s
            """,
            [
                corte_id,
                str(codigo_estud),
                str(cod_anio_basica),
                str(codigo_periodo),
                str(codigo_materia),
                str(num_matricula),
            ],
        )


def _fetch_student_identity(codigo_estud: str) -> dict[str, str]:
    row = _fetch_one(
        """
        SELECT TOP (1)
            LTRIM(RTRIM(ISNULL(Cedula_Est, ''))) AS cedula,
            LTRIM(RTRIM(ISNULL(Apellidos_nombre, ''))) AS nombre
        FROM dbo.DATOS_ESTUD
        WHERE CAST(codigo_estud AS varchar(50)) = %s
        """,
        [str(codigo_estud)],
    )
    return {
        'cedula': str((row or {}).get('cedula') or '').strip(),
        'nombre': str((row or {}).get('nombre') or '').strip(),
    }


def _find_open_cut_subject_overlap(
    *,
    cod_anio_basica: str,
    codigo_periodo: str,
    subject_codes: list[str],
) -> dict[str, Any] | None:
    placeholders = ', '.join(['%s'] * len(subject_codes))
    return _fetch_one(
        f"""
        SELECT TOP (1)
            CC.CorteId,
            CC.NombreCorte,
            CC.CodigoMateria,
            LTRIM(RTRIM(ISNULL(P.Nomb_Materia, ''))) AS MateriaPensum
        FROM dbo.CORTE_CURSO CC
        LEFT JOIN dbo.PENSUM P
          ON P.Cod_AnioBasica = CC.Cod_AnioBasica
         AND P.codigo_materia = CC.CodigoMateria
        WHERE CC.EstadoCorte = 'ABIERTO'
          AND CC.TipoOferta = 'CARRERA'
          AND LTRIM(RTRIM(CAST(CC.Cod_AnioBasica AS varchar(20)))) = %s
          AND LTRIM(RTRIM(CAST(CC.CodigoPeriodo AS varchar(20)))) = %s
          AND LTRIM(RTRIM(CAST(CC.CodigoMateria AS varchar(50)))) IN ({placeholders})
        ORDER BY CC.FechaInicio DESC, CC.CorteId DESC
        """,
        [cod_anio_basica, codigo_periodo, *subject_codes],
    )


def _next_batch_cut_number(cod_anio_basica: str, codigo_periodo: str, subject_codes: list[str]) -> int:
    placeholders = ', '.join(['%s'] * len(subject_codes))
    row = _fetch_one(
        f"""
        SELECT ISNULL(MAX(NumeroCorte), 0) + 1 AS next_number
        FROM dbo.CORTE_CURSO
        WHERE EstadoCorte <> 'ANULADO'
          AND TipoOferta = 'CARRERA'
          AND LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
          AND LTRIM(RTRIM(CAST(CodigoPeriodo AS varchar(20)))) = %s
          AND LTRIM(RTRIM(CAST(CodigoMateria AS varchar(50)))) IN ({placeholders})
        """,
        [cod_anio_basica, codigo_periodo, *subject_codes],
    )
    return _safe_int((row or {}).get('next_number'), default=1)


def _next_educontinua_cut_number(cod_curso: str) -> int:
    row = _fetch_one(
        """
        SELECT ISNULL(MAX(NumeroCorte), 0) + 1 AS next_number
        FROM dbo.CORTE_CURSO
        WHERE EstadoCorte <> 'ANULADO'
          AND TipoOferta = 'EDUCONTINUA'
          AND LTRIM(RTRIM(CAST(CodCurso AS varchar(20)))) = %s
        """,
        [cod_curso],
    )
    return _safe_int((row or {}).get('next_number'), default=1)


def _resolve_pensum_hours(cod_anio_basica: str, codigo_materia: str) -> int:
    row = _fetch_one(
        """
        SELECT CAST(ISNULL(Horas, 0) AS decimal(18, 0)) AS horas
        FROM dbo.PENSUM
        WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
          AND LTRIM(RTRIM(CAST(codigo_materia AS varchar(50)))) = %s
        """,
        [cod_anio_basica, codigo_materia],
    )
    if not row:
        raise CourseCutError('No se encontró en PENSUM la materia seleccionada para asignar las horas.')
    return _safe_int(row.get('horas'), default=0)


def _fetch_cut_by_id(corte_id: Any) -> dict[str, Any] | None:
    row = _fetch_one(
        """
        SELECT TOP (1)
            CC.CorteId,
            CC.TipoOferta,
            CC.NumeroCorte,
            CC.NombreCorte,
            CC.FechaInicio,
            CC.FechaFin,
            CC.EstadoCorte,
            CC.Cod_AnioBasica,
            CC.CodigoPeriodo,
            CC.CodigoMateria,
            LTRIM(RTRIM(ISNULL(P.Nomb_Materia, ''))) AS MateriaPensum,
            CC.CodCurso
        FROM dbo.CORTE_CURSO CC
        LEFT JOIN dbo.PENSUM P
          ON P.Cod_AnioBasica = CC.Cod_AnioBasica
         AND P.codigo_materia = CC.CodigoMateria
        WHERE CC.CorteId = %s
        """,
        [corte_id],
    )
    return _normalize_cut(row) if row else None


def _normalize_cut_summary(row: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_cut(row)
    normalized.update(
        {
            'carrera': str(row.get('Carrera') or '').strip(),
            'periodo': str(row.get('Periodo') or '').strip(),
            'materia_pensum': str(row.get('MateriaPensum') or normalized.get('materia_pensum') or '').strip(),
            'curso_educontinua': str(row.get('CursoEduContinua') or '').strip(),
            'cupo_esperado': str(row.get('CupoEsperado') or '').strip(),
            'total_estudiantes': _safe_int(row.get('TotalEstudiantes'), default=0),
            'total_inscritos': _safe_int(row.get('TotalInscritos'), default=0),
            'total_cursando': _safe_int(row.get('TotalCursando'), default=0),
            'total_retirados': _safe_int(row.get('TotalRetirados'), default=0),
            'total_aprobados': _safe_int(row.get('TotalAprobados'), default=0),
            'total_reprobados': _safe_int(row.get('TotalReprobados'), default=0),
            'total_finalizados': _safe_int(row.get('TotalFinalizados'), default=0),
        }
    )
    normalized['materias_label'] = normalized['materia_pensum']
    return normalized


def _normalize_cut(row: dict[str, Any]) -> dict[str, Any]:
    fecha_inicio = row.get('FechaInicio')
    fecha_fin = row.get('FechaFin')
    codigo_materia = str(row.get('CodigoMateria') or '').strip()
    materia_pensum = str(row.get('MateriaPensum') or '').strip()
    materias = []
    if codigo_materia:
        materias.append({'codigo_materia': codigo_materia, 'nombre_materia': materia_pensum})
    estado_corte = str(row.get('EstadoCorte') or '').strip()
    fecha_fin_vencida = _is_registration_deadline_expired(fecha_fin)
    ingresos_disponibles = estado_corte == 'ABIERTO' and not fecha_fin_vencida
    return {
        'corte_id': str(row.get('CorteId') or '').strip(),
        'tipo_oferta': str(row.get('TipoOferta') or '').strip(),
        'numero_corte': str(row.get('NumeroCorte') or '').strip(),
        'nombre_corte': str(row.get('NombreCorte') or '').strip(),
        'fecha_inicio': _format_date_label(fecha_inicio),
        'fecha_inicio_iso': _date_iso(fecha_inicio),
        'fecha_inicio_raw': fecha_inicio,
        'fecha_fin': _format_date_label(fecha_fin) if fecha_fin else '',
        'fecha_fin_iso': _date_iso(fecha_fin),
        'fecha_fin_raw': fecha_fin,
        'fecha_fin_vencida': fecha_fin_vencida,
        'ingresos_disponibles': ingresos_disponibles,
        'estado_inscripcion': _registration_status_label(estado_corte, fecha_fin),
        'estado_corte': estado_corte,
        'cod_anio_basica': str(row.get('Cod_AnioBasica') or '').strip(),
        'codigo_periodo': str(row.get('CodigoPeriodo') or '').strip(),
        'codigo_materia': codigo_materia,
        'materia_pensum': materia_pensum,
        'materias': materias,
        'codigo_materias': [codigo_materia] if codigo_materia else [],
        'materias_label': materia_pensum,
        'cod_curso': str(row.get('CodCurso') or '').strip(),
    }


def _subject_codes_from_payload(payload: dict[str, Any]) -> list[str]:
    raw_values = payload.get('codigo_materias')
    if raw_values is None:
        raw_values = payload.get('materias')
    if raw_values is None:
        raw_values = payload.get('codigo_materia')

    if not isinstance(raw_values, list):
        raw_values = [raw_values]

    subject_codes: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        if isinstance(value, dict):
            value = value.get('codigo_materia') or value.get('codigo')
        subject_code = _clean_text(value)
        if not subject_code or subject_code in seen:
            continue
        seen.add(subject_code)
        subject_codes.append(subject_code)
    return subject_codes


def _ensure_course_cut_schema() -> None:
    required_objects = ['CORTE_CURSO', 'CORTE_CURSO_ESTUDIANTE', 'VW_CORTE_RESUMEN']
    required_columns = {
        'CORTE_CURSO': ['CodigoMateria'],
        'CORTE_CURSO_ESTUDIANTE': ['CodigoMateria'],
        'CERTIFICADOS_GENERADOS': ['CodigoMateria'],
    }
    with connection.cursor() as cursor:
        for object_name in required_objects:
            cursor.execute("SELECT OBJECT_ID(%s)", [f'dbo.{object_name}'])
            row = cursor.fetchone()
            if not row or row[0] is None:
                raise CourseCutError(
                    'Los objetos de cortes no están instalados en INTECBDD. '
                    'Ejecuta primero el script actualizado de CORTE_CURSO.'
                )
        for table_name, columns in required_columns.items():
            for column_name in columns:
                cursor.execute("SELECT COL_LENGTH(%s, %s)", [f'dbo.{table_name}', column_name])
                row = cursor.fetchone()
                if not row or row[0] is None:
                    raise CourseCutError(
                        f'La columna {column_name} no existe en {table_name}. '
                        'Ejecuta el script actualizado de cortes.'
                    )


def _fetch_one(query: str, params: list[Any]) -> dict[str, Any] | None:
    rows = _fetch_all(query, params)
    return rows[0] if rows else None


def _fetch_all(query: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        if cursor.description is None:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _format_date_label(value: Any) -> str:
    parsed = _coerce_date(value)
    if not parsed:
        return ''
    month = SPANISH_MONTHS.get(parsed.month, str(parsed.month))
    return f'{parsed.day} de {month} de {parsed.year}'


def _date_iso(value: Any) -> str:
    parsed = _coerce_date(value)
    return parsed.isoformat() if parsed else ''


def _today_ecuador() -> date:
    return datetime.now(ECUADOR_TIMEZONE).date()


def _is_registration_deadline_expired(value: Any) -> bool:
    parsed = _coerce_date(value)
    return bool(parsed and parsed < _today_ecuador())


def _registration_status_label(estado_corte: str, fecha_fin: Any) -> str:
    if estado_corte != 'ABIERTO':
        return 'CERRADA'
    if _is_registration_deadline_expired(fecha_fin):
        return 'CERRADA POR FECHA'
    return 'DISPONIBLE'


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _clean_text(value: Any) -> str:
    return ' '.join(str(value or '').strip().split())


def _numeric_or_none(value: Any) -> int | None:
    text = _clean_text(value)
    if not text:
        return None
    number = _safe_int(text, default=-1)
    return number if number >= 0 else None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> int | None:
    text = _clean_text(value)
    if not text:
        return None
    parsed = _safe_int(text, default=-1)
    return parsed if parsed >= 0 else None


def _trim_to_max(value: Any, max_length: int) -> str:
    return str(value or '').strip()[:max_length]
