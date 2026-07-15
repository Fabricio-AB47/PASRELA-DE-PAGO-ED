from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .academic_rules import is_passing_grade
from .continuing_education import (
    complement_connection,
    complement_database_name,
    complement_version,
    is_complement_available,
)
from .inscription_certificate import (
    build_inscription_certificate_preview_image,
    create_stored_certificate_record,
    load_signed_certificate_payload,
    load_or_create_stored_certificate,
    send_certificate_email,
)


class StudentDashboardError(Exception):
    pass


WEEKDAY_LABELS = {
    1: 'Lunes',
    2: 'Martes',
    3: 'Miércoles',
    4: 'Jueves',
    5: 'Viernes',
    6: 'Sábado',
    7: 'Domingo',
}
SCHEDULE_ONLINE_MODALITIES = {'EN LINEA', 'ONLINE', 'VIRTUAL', 'HIBRIDA', 'HIBRIDO'}


def get_student_schedule_dashboard(session_user: dict[str, Any]) -> dict[str, Any]:
    student = _resolve_student_from_session(session_user)
    if not student:
        raise StudentDashboardError('No se encontró el registro estudiantil asociado a tu sesión.')

    status = _student_schedule_status()
    if not status['available']:
        return {
            'student': student,
            'courses': [],
            'metrics': _build_schedule_metrics([]),
            'continuing_education': status,
        }

    rows = _fetch_student_schedule_rows(student)
    courses = _group_schedule_courses(rows)
    return {
        'student': student,
        'courses': courses,
        'metrics': _build_schedule_metrics(courses),
        'continuing_education': status,
    }


def get_student_grades_dashboard(session_user: dict[str, Any]) -> dict[str, Any]:
    student = _resolve_student_from_session(session_user)
    if not student:
        raise StudentDashboardError('No se encontró el registro estudiantil asociado a tu sesión.')

    status = _student_grades_status()
    if not status['available']:
        return {
            'student': student,
            'courses': [],
            'metrics': _build_grade_metrics([]),
            'continuing_education': status,
        }

    rows = _fetch_student_grade_rows(student)
    courses = [_normalize_grade_course(row) for row in rows]
    return {
        'student': student,
        'courses': courses,
        'metrics': _build_grade_metrics(courses),
        'continuing_education': status,
    }


def build_student_certificate(
    session_user: dict[str, Any],
    estudiante_corte_id: Any,
) -> tuple[bytes, str]:
    student, course = _student_certificate_course(session_user, estudiante_corte_id)
    certificate_record = create_stored_certificate_record(_build_student_certificate_payload(student, course))
    return load_or_create_stored_certificate(load_signed_certificate_payload(certificate_record['token']))


def preview_student_certificate(
    session_user: dict[str, Any],
    estudiante_corte_id: Any,
) -> tuple[bytes, str]:
    student, course = _student_certificate_course(
        session_user,
        estudiante_corte_id,
        # La vista previa no emite ni entrega el certificado. Se permite para
        # que el estudiante revise el formato; descargar y enviar continúan
        # sujetos a nota aprobatoria y pago completo.
        require_approved=False,
        require_email=False,
    )
    payload = _build_student_certificate_payload(student, course)
    payload['codigo_certificado'] = 'VISTA-PREVIA'
    payload['certificate_code'] = 'VISTA-PREVIA'
    return build_inscription_certificate_preview_image(payload)


def send_student_certificate(
    session_user: dict[str, Any],
    estudiante_corte_id: Any,
) -> dict[str, Any]:
    student, course = _student_certificate_course(session_user, estudiante_corte_id)
    certificate_payload = _build_student_certificate_payload(student, course)
    certificate_record = create_stored_certificate_record(certificate_payload)
    recipient_email = _student_certificate_email(student, course)
    return send_certificate_email(
        recipient_email=recipient_email,
        recipient_name=course.get('nombre') or student.get('nombre') or recipient_email,
        certificate_record=certificate_record,
    )


def _student_schedule_status() -> dict[str, Any]:
    required = [
        ('edu', 'VW_MatriculaEstudianteCompleta', 'V'),
        ('edu', 'VW_CorteCursoDetalle', 'V'),
        ('edu', 'VW_MatriculaDocenteCompleta', 'V'),
        ('edu', 'HorarioCorte', 'U'),
        ('edu', 'SesionCorte', 'U'),
    ]
    available = complement_version() == 'v5' and is_complement_available(required)
    return {
        'available': available,
        'database': complement_database_name(),
        'version': complement_version(),
        'message': (
            'Base complementaria lista para consultar horario estudiantil.'
            if available
            else 'Ejecuta el módulo v5 de INTECEDUCONTINUA para consultar horarios.'
        ),
    }


def _student_grades_status() -> dict[str, Any]:
    required = [
        ('edu', 'VW_MatriculaEstudianteCompleta', 'V'),
        ('edu', 'VW_CorteCursoDetalle', 'V'),
        ('edu', 'VW_MatriculaDocenteCompleta', 'V'),
        ('edu', 'CalificacionCorte', 'U'),
        ('edu', 'VW_AsistenciaResumen', 'V'),
        ('fin', 'CuentaEstudiante', 'U'),
        ('fin', 'MovimientoCuenta', 'U'),
    ]
    available = complement_version() == 'v5' and is_complement_available(required)
    return {
        'available': available,
        'database': complement_database_name(),
        'version': complement_version(),
        'message': (
            'Base complementaria lista para consultar calificaciones.'
            if available
            else 'Ejecuta el módulo v5 de INTECEDUCONTINUA para consultar calificaciones.'
        ),
    }


def _resolve_student_from_session(session_user: dict[str, Any]) -> dict[str, Any] | None:
    identifiers = _session_identifiers(session_user)
    if not identifiers:
        return None

    where_parts: list[str] = []
    params: list[Any] = []
    for identifier in identifiers:
        where_parts.append(
            """
            LOWER(LTRIM(RTRIM(ISNULL(CorreoPersonal, '')))) = %s
            OR LOWER(LTRIM(RTRIM(ISNULL(CorreoIntec, '')))) = %s
            OR LTRIM(RTRIM(CAST(codestud AS varchar(50)))) = %s
            """
        )
        params.extend([identifier, identifier, identifier])

    rows = _fetch_all(
        f"""
        SELECT
            CAST(codestud AS varchar(50)) AS codestud,
            LTRIM(RTRIM(ISNULL(Nombres, ''))) AS Nombres,
            LTRIM(RTRIM(ISNULL(CorreoPersonal, ''))) AS CorreoPersonal,
            LTRIM(RTRIM(ISNULL(CorreoIntec, ''))) AS CorreoIntec,
            LTRIM(RTRIM(ISNULL(Estado, ''))) AS Estado,
            CAST(Periodo AS varchar(50)) AS Periodo
        FROM dbo.CorreosEstudIntec
        WHERE {' OR '.join(f'({part})' for part in where_parts)}
        ORDER BY Periodo DESC
        """,
        params,
    )
    if not rows:
        numeric_codes = [identifier for identifier in identifiers if identifier.isdigit()]
        if not numeric_codes:
            return None
        return {
            'codigo_estud': numeric_codes[0],
            'codigos_estud': numeric_codes,
            'nombre': _clean_text(session_user.get('login')) or numeric_codes[0],
            'correo_intec': _clean_text(session_user.get('email')).lower(),
            'correo_personal': '',
            'estado': _clean_text(session_user.get('status')),
            'periodo': '',
            'identifiers': identifiers,
        }

    codes: list[str] = []
    emails: list[str] = []
    for row in rows:
        code = _clean_text(row.get('codestud'))
        if code and code not in codes:
            codes.append(code)
        for key in ('CorreoIntec', 'CorreoPersonal'):
            email = _clean_text(row.get(key)).lower()
            if email and email not in emails:
                emails.append(email)

    primary = rows[0]
    return {
        'codigo_estud': codes[0] if codes else '',
        'codigos_estud': codes,
        'nombre': _clean_text(primary.get('Nombres')) or _clean_text(session_user.get('login')),
        'correo_intec': _clean_text(primary.get('CorreoIntec')).lower(),
        'correo_personal': _clean_text(primary.get('CorreoPersonal')).lower(),
        'estado': _clean_text(primary.get('Estado')),
        'periodo': _clean_text(primary.get('Periodo')),
        'identifiers': [*identifiers, *emails],
    }


def _fetch_student_schedule_rows(student: dict[str, Any]) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    codes = [code for code in student.get('codigos_estud', []) if code]
    identifiers = [value.lower() for value in student.get('identifiers', []) if value]

    if codes:
        placeholders = ', '.join(['%s'] * len(codes))
        clauses.append(f"LTRIM(RTRIM(CAST(M.[CodigoEstud] AS varchar(50)))) IN ({placeholders})")
        params.extend(codes)

    if identifiers:
        placeholders = ', '.join(['%s'] * len(identifiers))
        clauses.append(
            f"""
            (
                LOWER(LTRIM(RTRIM(ISNULL(M.[CorreoIntec], '')))) COLLATE DATABASE_DEFAULT IN ({placeholders})
                OR LOWER(LTRIM(RTRIM(ISNULL(M.[CorreoPersonal], '')))) COLLATE DATABASE_DEFAULT IN ({placeholders})
                OR LOWER(LTRIM(RTRIM(ISNULL(M.[UsuarioLogin], '')))) COLLATE DATABASE_DEFAULT IN ({placeholders})
                OR LOWER(LTRIM(RTRIM(ISNULL(M.[UsuarioSisLogin], '')))) COLLATE DATABASE_DEFAULT IN ({placeholders})
            )
            """
        )
        params.extend([*identifiers, *identifiers, *identifiers, *identifiers])

    if not clauses:
        return []

    db_name = complement_database_name()
    return _fetch_all(
        f"""
        SELECT
            M.[EstudianteCorteId],
            M.[CorteId],
            M.[CodigoEstud],
            M.[EstadoMatricula],
            M.[FechaMatricula],
            D.[TipoOferta],
            D.[Cod_AnioBasica],
            D.[CodigoPeriodo],
            D.[CodigoMateria],
            D.[CodCurso],
            D.[NombreCursoMateria],
            D.[NombreCorte],
            D.[FechaInicio],
            D.[FechaFin],
            D.[EstadoCorteEdu],
            DOC.[ApellidosNombre] AS [DocenteNombre],
            DOC.[CorreoIntec] AS [DocenteCorreo],
            H.[HorarioId],
            H.[DiaSemana],
            CONVERT(varchar(5), H.[HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), H.[HoraFin], 108) AS [HoraFin],
            H.[Modalidad],
            H.[Aula],
            H.[EnlaceVirtual],
            H.[EstadoHorario],
            SG.[TotalSesiones],
            SG.[PrimeraSesion],
            SG.[UltimaSesion]
        FROM [{db_name}].[edu].[VW_MatriculaEstudianteCompleta] M
        INNER JOIN [{db_name}].[edu].[VW_CorteCursoDetalle] D
          ON D.[CorteId] = M.[CorteId]
        LEFT JOIN [{db_name}].[edu].[HorarioCorte] H
          ON H.[CorteId] = M.[CorteId]
         AND (H.[EstadoHorario] COLLATE DATABASE_DEFAULT) = 'ACTIVO'
        OUTER APPLY (
            SELECT TOP (1)
                MD.[ApellidosNombre],
                MD.[CorreoIntec]
            FROM [{db_name}].[edu].[VW_MatriculaDocenteCompleta] MD
            WHERE MD.[CorteId] = M.[CorteId]
              AND (MD.[EstadoDocenteCorte] COLLATE DATABASE_DEFAULT) = 'ACTIVO'
            ORDER BY
                CASE WHEN MD.[RolDocente] = 'TITULAR' THEN 0 ELSE 1 END,
                MD.[DocenteCorteId]
        ) DOC
        OUTER APPLY (
            SELECT
                COUNT(1) AS [TotalSesiones],
                MIN(S.[FechaClase]) AS [PrimeraSesion],
                MAX(S.[FechaClase]) AS [UltimaSesion]
            FROM [{db_name}].[edu].[SesionCorte] S
            WHERE S.[HorarioId] = H.[HorarioId]
              AND (S.[EstadoSesion] COLLATE DATABASE_DEFAULT) <> 'CANCELADA'
        ) SG
        WHERE ({' OR '.join(clauses)})
          AND (M.[EstadoMatricula] COLLATE DATABASE_DEFAULT) NOT IN ('ANULADO','RETIRADO')
        ORDER BY
            D.[FechaInicio] DESC,
            M.[CorteId] DESC,
            H.[DiaSemana],
            H.[HoraInicio]
        """,
        params,
    )


def _fetch_student_grade_rows(student: dict[str, Any]) -> list[dict[str, Any]]:
    clauses, params = _student_match_clauses(student)
    if not clauses:
        return []

    db_name = complement_database_name()
    return _fetch_all(
        f"""
        SELECT
            M.[EstudianteCorteId],
            M.[CorteId],
            M.[CodigoEstud],
            M.[CedulaEst],
            M.[ApellidosNombre],
            M.[CorreoIntec],
            M.[CorreoPersonal],
            M.[UsuarioLogin],
            M.[FechaMatricula],
            M.[EstadoMatricula],
            D.[TipoOferta],
            D.[Cod_AnioBasica],
            D.[CodigoPeriodo],
            D.[CodigoMateria],
            D.[CodCurso],
            D.[NombreCursoMateria],
            D.[NombreCorte],
            D.[FechaInicio],
            D.[FechaFin],
            D.[EstadoCorteEdu],
            DOC.[ApellidosNombre] AS [DocenteNombre],
            DOC.[CorreoIntec] AS [DocenteCorreo],
            CAL.[NotaFinal],
            CAL.[EstadoNota],
            CAL.[FechaCalificacion],
            CAL.[FechaModifica] AS [FechaModificaNota],
            CAL.[FechaPase],
            AR.[PorcentajeAsistencia],
            AR.[TotalSesionesRealizadas],
            ISNULL(FIN.[TotalCargo], 0) AS [TotalCargo],
            ISNULL(FIN.[TotalPagado], 0) AS [TotalPagado],
            ISNULL(FIN.[TotalDescuento], 0) AS [TotalDescuento]
        FROM [{db_name}].[edu].[VW_MatriculaEstudianteCompleta] M
        INNER JOIN [{db_name}].[edu].[VW_CorteCursoDetalle] D
          ON D.[CorteId] = M.[CorteId]
        LEFT JOIN [{db_name}].[edu].[CalificacionCorte] CAL
          ON CAL.[EstudianteCorteId] = M.[EstudianteCorteId]
        LEFT JOIN [{db_name}].[edu].[VW_AsistenciaResumen] AR
          ON AR.[EstudianteCorteId] = M.[EstudianteCorteId]
        OUTER APPLY (
            SELECT
                SUM(CASE WHEN MOV.[EstadoMovimiento] = 'ACTIVO' AND MOV.[TipoMovimiento] = 'DEBE' THEN MOV.[Valor] ELSE 0 END) AS [TotalCargo],
                SUM(CASE WHEN MOV.[EstadoMovimiento] = 'ACTIVO' AND MOV.[TipoMovimiento] = 'HABER' AND UPPER(ISNULL(MOV.[FormaPago], '')) <> 'DESCUENTO' THEN MOV.[Valor] ELSE 0 END) AS [TotalPagado],
                SUM(CASE WHEN MOV.[EstadoMovimiento] = 'ACTIVO' AND MOV.[TipoMovimiento] = 'HABER' AND UPPER(ISNULL(MOV.[FormaPago], '')) = 'DESCUENTO' THEN MOV.[Valor] ELSE 0 END) AS [TotalDescuento]
            FROM [{db_name}].[fin].[CuentaEstudiante] CTA
            LEFT JOIN [{db_name}].[fin].[MovimientoCuenta] MOV ON MOV.[CuentaId] = CTA.[CuentaId]
            WHERE CTA.[EstudianteCorteId] = M.[EstudianteCorteId]
        ) FIN
        OUTER APPLY (
            SELECT TOP (1)
                MD.[ApellidosNombre],
                MD.[CorreoIntec]
            FROM [{db_name}].[edu].[VW_MatriculaDocenteCompleta] MD
            WHERE MD.[CorteId] = M.[CorteId]
              AND (MD.[EstadoDocenteCorte] COLLATE DATABASE_DEFAULT) = 'ACTIVO'
            ORDER BY
                CASE WHEN MD.[RolDocente] = 'TITULAR' THEN 0 ELSE 1 END,
                MD.[DocenteCorteId]
        ) DOC
        WHERE ({' OR '.join(clauses)})
          AND (M.[EstadoMatricula] COLLATE DATABASE_DEFAULT) NOT IN ('ANULADO','RETIRADO')
        ORDER BY D.[FechaInicio] DESC, M.[CorteId] DESC, M.[EstudianteCorteId] DESC
        """,
        params,
    )


def _fetch_student_grade_row(student: dict[str, Any], estudiante_corte_id: Any) -> dict[str, Any] | None:
    normalized_id = _clean_text(estudiante_corte_id)
    if not normalized_id.isdigit():
        raise StudentDashboardError('Debes seleccionar una matrícula válida para generar el certificado.')

    clauses, params = _student_match_clauses(student)
    if not clauses:
        return None

    db_name = complement_database_name()
    rows = _fetch_all(
        f"""
        SELECT
            M.[EstudianteCorteId],
            M.[CorteId],
            M.[CodigoEstud],
            M.[CedulaEst],
            M.[ApellidosNombre],
            M.[CorreoIntec],
            M.[CorreoPersonal],
            M.[UsuarioLogin],
            M.[FechaMatricula],
            M.[EstadoMatricula],
            D.[TipoOferta],
            D.[Cod_AnioBasica],
            D.[CodigoPeriodo],
            D.[CodigoMateria],
            D.[CodCurso],
            D.[NombreCursoMateria],
            D.[NombreCorte],
            D.[FechaInicio],
            D.[FechaFin],
            D.[EstadoCorteEdu],
            DOC.[ApellidosNombre] AS [DocenteNombre],
            DOC.[CorreoIntec] AS [DocenteCorreo],
            CAL.[NotaFinal],
            CAL.[EstadoNota],
            CAL.[FechaCalificacion],
            CAL.[FechaModifica] AS [FechaModificaNota],
            CAL.[FechaPase],
            AR.[PorcentajeAsistencia],
            AR.[TotalSesionesRealizadas],
            ISNULL(FIN.[TotalCargo], 0) AS [TotalCargo],
            ISNULL(FIN.[TotalPagado], 0) AS [TotalPagado],
            ISNULL(FIN.[TotalDescuento], 0) AS [TotalDescuento]
        FROM [{db_name}].[edu].[VW_MatriculaEstudianteCompleta] M
        INNER JOIN [{db_name}].[edu].[VW_CorteCursoDetalle] D
          ON D.[CorteId] = M.[CorteId]
        LEFT JOIN [{db_name}].[edu].[CalificacionCorte] CAL
          ON CAL.[EstudianteCorteId] = M.[EstudianteCorteId]
        LEFT JOIN [{db_name}].[edu].[VW_AsistenciaResumen] AR
          ON AR.[EstudianteCorteId] = M.[EstudianteCorteId]
        OUTER APPLY (
            SELECT
                SUM(CASE WHEN MOV.[EstadoMovimiento] = 'ACTIVO' AND MOV.[TipoMovimiento] = 'DEBE' THEN MOV.[Valor] ELSE 0 END) AS [TotalCargo],
                SUM(CASE WHEN MOV.[EstadoMovimiento] = 'ACTIVO' AND MOV.[TipoMovimiento] = 'HABER' AND UPPER(ISNULL(MOV.[FormaPago], '')) <> 'DESCUENTO' THEN MOV.[Valor] ELSE 0 END) AS [TotalPagado],
                SUM(CASE WHEN MOV.[EstadoMovimiento] = 'ACTIVO' AND MOV.[TipoMovimiento] = 'HABER' AND UPPER(ISNULL(MOV.[FormaPago], '')) = 'DESCUENTO' THEN MOV.[Valor] ELSE 0 END) AS [TotalDescuento]
            FROM [{db_name}].[fin].[CuentaEstudiante] CTA
            LEFT JOIN [{db_name}].[fin].[MovimientoCuenta] MOV ON MOV.[CuentaId] = CTA.[CuentaId]
            WHERE CTA.[EstudianteCorteId] = M.[EstudianteCorteId]
        ) FIN
        OUTER APPLY (
            SELECT TOP (1)
                MD.[ApellidosNombre],
                MD.[CorreoIntec]
            FROM [{db_name}].[edu].[VW_MatriculaDocenteCompleta] MD
            WHERE MD.[CorteId] = M.[CorteId]
              AND (MD.[EstadoDocenteCorte] COLLATE DATABASE_DEFAULT) = 'ACTIVO'
            ORDER BY
                CASE WHEN MD.[RolDocente] = 'TITULAR' THEN 0 ELSE 1 END,
                MD.[DocenteCorteId]
        ) DOC
        WHERE M.[EstudianteCorteId] = %s
          AND ({' OR '.join(clauses)})
          AND (M.[EstadoMatricula] COLLATE DATABASE_DEFAULT) NOT IN ('ANULADO','RETIRADO')
        """,
        [normalized_id, *params],
    )
    return rows[0] if rows else None


def _student_match_clauses(student: dict[str, Any]) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    codes = [code for code in student.get('codigos_estud', []) if code]
    identifiers = [value.lower() for value in student.get('identifiers', []) if value]

    if codes:
        placeholders = ', '.join(['%s'] * len(codes))
        clauses.append(f"LTRIM(RTRIM(CAST(M.[CodigoEstud] AS varchar(50)))) IN ({placeholders})")
        params.extend(codes)

    if identifiers:
        placeholders = ', '.join(['%s'] * len(identifiers))
        clauses.append(
            f"""
            (
                LOWER(LTRIM(RTRIM(ISNULL(M.[CorreoIntec], '')))) COLLATE DATABASE_DEFAULT IN ({placeholders})
                OR LOWER(LTRIM(RTRIM(ISNULL(M.[CorreoPersonal], '')))) COLLATE DATABASE_DEFAULT IN ({placeholders})
                OR LOWER(LTRIM(RTRIM(ISNULL(M.[UsuarioLogin], '')))) COLLATE DATABASE_DEFAULT IN ({placeholders})
                OR LOWER(LTRIM(RTRIM(ISNULL(M.[UsuarioSisLogin], '')))) COLLATE DATABASE_DEFAULT IN ({placeholders})
            )
            """
        )
        params.extend([*identifiers, *identifiers, *identifiers, *identifiers])

    return clauses, params


def _group_schedule_courses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    courses: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    schedule_seen: set[str] = set()

    for row in rows:
        corte_id = _clean_text(row.get('CorteId'))
        if not corte_id:
            continue
        course = index.get(corte_id)
        if course is None:
            course = {
                'corte_id': corte_id,
                'estudiante_corte_id': _clean_text(row.get('EstudianteCorteId')),
                'codigo_estud': _clean_text(row.get('CodigoEstud')),
                'estado_matricula': _clean_text(row.get('EstadoMatricula')),
                'fecha_matricula': _date_iso(row.get('FechaMatricula')),
                'tipo_oferta': _clean_text(row.get('TipoOferta')),
                'cod_anio_basica': _clean_text(row.get('Cod_AnioBasica')),
                'codigo_periodo': _clean_text(row.get('CodigoPeriodo')),
                'codigo_materia': _clean_text(row.get('CodigoMateria') or row.get('CodCurso')),
                'cod_curso': _clean_text(row.get('CodCurso')),
                'materia': _clean_text(row.get('NombreCursoMateria')) or 'Sin materia',
                'nombre_corte': _clean_text(row.get('NombreCorte')),
                'fecha_inicio': _date_iso(row.get('FechaInicio')),
                'fecha_fin': _date_iso(row.get('FechaFin')),
                'estado_corte': _clean_text(row.get('EstadoCorteEdu')),
                'docente': _clean_text(row.get('DocenteNombre')) or 'Sin docente asignado',
                'docente_correo': _clean_text(row.get('DocenteCorreo')).lower(),
                'schedules': [],
            }
            index[corte_id] = course
            courses.append(course)

        horario_id = _clean_text(row.get('HorarioId'))
        if not horario_id or horario_id in schedule_seen:
            continue
        schedule_seen.add(horario_id)
        dia_semana = _safe_int(row.get('DiaSemana'))
        course['schedules'].append(
            {
                'horario_id': horario_id,
                'dia_semana': dia_semana,
                'dia_semana_label': WEEKDAY_LABELS.get(dia_semana, ''),
                'hora_inicio': _clean_text(row.get('HoraInicio')),
                'hora_fin': _clean_text(row.get('HoraFin')),
                'modalidad': _normalize_schedule_modality(row.get('Modalidad')),
                'aula': _clean_text(row.get('Aula')),
                'enlace_virtual': _clean_text(row.get('EnlaceVirtual')),
                'estado': _clean_text(row.get('EstadoHorario')),
                'total_sesiones': _safe_int(row.get('TotalSesiones')),
                'primera_sesion': _date_iso(row.get('PrimeraSesion')),
                'ultima_sesion': _date_iso(row.get('UltimaSesion')),
            }
        )

    return courses


def _build_schedule_metrics(courses: list[dict[str, Any]]) -> dict[str, int]:
    schedules = [schedule for course in courses for schedule in course.get('schedules', [])]
    virtual = [schedule for schedule in schedules if schedule.get('modalidad') == 'EN LÍNEA']
    return {
        'cursos': len(courses),
        'horarios': len(schedules),
        'virtuales': len(virtual),
        'sesiones': sum(_safe_int(schedule.get('total_sesiones')) for schedule in schedules),
    }


def _normalize_grade_course(row: dict[str, Any]) -> dict[str, Any]:
    nota_final = _decimal_to_number(row.get('NotaFinal'))
    asistencia = _decimal_to_number(row.get('PorcentajeAsistencia'))
    aprobado = _is_passing_grade(nota_final)
    total_curso = _to_decimal(row.get('TotalCargo'))
    total_pagado = _to_decimal(row.get('TotalPagado'))
    total_descuento = _to_decimal(row.get('TotalDescuento'))
    saldo_pendiente = max(Decimal('0.00'), total_curso - total_pagado - total_descuento)
    pago_completo = total_curso > 0 and saldo_pendiente <= 0
    certificado_disponible = aprobado and pago_completo
    estado_nota = _clean_text(row.get('EstadoNota')).upper()
    if certificado_disponible:
        certificado_estado = 'Disponible'
    elif not aprobado and not pago_completo:
        certificado_estado = 'No disponible: curso no aprobado y pago pendiente'
    elif not aprobado:
        certificado_estado = 'No disponible: curso no aprobado'
    else:
        certificado_estado = 'No disponible: pago total pendiente'
    return {
        'estudiante_corte_id': _clean_text(row.get('EstudianteCorteId')),
        'corte_id': _clean_text(row.get('CorteId')),
        'codigo_estud': _clean_text(row.get('CodigoEstud')),
        'cedula': _clean_text(row.get('CedulaEst')),
        'nombre': _clean_text(row.get('ApellidosNombre')) or 'Sin nombre',
        'correo_intec': _clean_text(row.get('CorreoIntec')).lower(),
        'correo_personal': _clean_text(row.get('CorreoPersonal')).lower(),
        'usuario_login': _clean_text(row.get('UsuarioLogin')).lower(),
        'estado_matricula': _clean_text(row.get('EstadoMatricula')),
        'fecha_matricula': _date_iso(row.get('FechaMatricula')),
        'tipo_oferta': _clean_text(row.get('TipoOferta')),
        'cod_anio_basica': _clean_text(row.get('Cod_AnioBasica')),
        'codigo_periodo': _clean_text(row.get('CodigoPeriodo')),
        'codigo_materia': _clean_text(row.get('CodigoMateria') or row.get('CodCurso')),
        'cod_curso': _clean_text(row.get('CodCurso')),
        'materia': _clean_text(row.get('NombreCursoMateria')) or 'Sin materia',
        'nombre_corte': _clean_text(row.get('NombreCorte')),
        'fecha_inicio': _date_iso(row.get('FechaInicio')),
        'fecha_fin': _date_iso(row.get('FechaFin')),
        'estado_corte': _clean_text(row.get('EstadoCorteEdu')),
        'docente': _clean_text(row.get('DocenteNombre')) or 'Sin docente asignado',
        'docente_correo': _clean_text(row.get('DocenteCorreo')).lower(),
        'nota_final': nota_final,
        'estado_nota': estado_nota or ('APROBADO' if aprobado else 'SIN_NOTA' if nota_final is None else 'REPROBADO'),
        'estado_nota_label': _grade_status_label(nota_final, estado_nota),
        'fecha_calificacion': _date_iso(row.get('FechaCalificacion') or row.get('FechaModificaNota')),
        'fecha_pase': _date_iso(row.get('FechaPase')),
        'porcentaje_asistencia': asistencia,
        'total_sesiones': _safe_int(row.get('TotalSesionesRealizadas')),
        'aprobado': aprobado,
        'total_curso': str(total_curso),
        'total_pagado': str(total_pagado),
        'total_descuento': str(total_descuento),
        'saldo_pendiente': str(saldo_pendiente),
        'pago_completo': pago_completo,
        'estado_financiero': 'PAGADO' if pago_completo else 'PENDIENTE',
        'certificado_disponible': certificado_disponible,
        'certificado_estado': certificado_estado,
        'culminacion_pendiente': not aprobado,
        'culminacion_estado': 'Curso culminado' if aprobado else 'Pendiente de culminación del curso',
    }


def _build_grade_metrics(courses: list[dict[str, Any]]) -> dict[str, int]:
    with_grade = [course for course in courses if course.get('nota_final') is not None]
    approved = [course for course in courses if course.get('aprobado')]
    certificates = [course for course in courses if course.get('certificado_disponible')]
    paid = [course for course in courses if course.get('pago_completo')]
    pending_completion = [course for course in courses if course.get('culminacion_pendiente')]
    return {
        'cursos': len(courses),
        'con_nota': len(with_grade),
        'aprobados': len(approved),
        'certificados': len(certificates),
        'pagados': len(paid),
        'pendientes_pago': max(len(courses) - len(paid), 0),
        'pendientes_culminacion': len(pending_completion),
    }


def _student_certificate_course(
    session_user: dict[str, Any],
    estudiante_corte_id: Any,
    *,
    require_approved: bool = True,
    require_email: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    student = _resolve_student_from_session(session_user)
    if not student:
        raise StudentDashboardError('No se encontró el registro estudiantil asociado a tu sesión.')

    status = _student_grades_status()
    if not status['available']:
        raise StudentDashboardError(status['message'])

    row = _fetch_student_grade_row(student, estudiante_corte_id)
    if not row:
        raise StudentDashboardError('No se encontró la matrícula seleccionada para tu usuario.')

    course = _normalize_grade_course(row)
    if require_approved and not course['aprobado']:
        raise StudentDashboardError(
            'El certificado estará disponible cuando la nota final esté entre 7.00 y 10.00.'
        )

    if require_approved and not course['pago_completo']:
        raise StudentDashboardError(
            f'El certificado estará disponible cuando completes el pago total del curso. '
            f'Saldo pendiente: ${course["saldo_pendiente"]}.'
        )

    if require_email and not _student_certificate_email(student, course):
        raise StudentDashboardError('No se encontró un correo válido para enviar el certificado.')

    return student, course


def _build_student_certificate_payload(student: dict[str, Any], course: dict[str, Any]) -> dict[str, Any]:
    return {
        'source': 'dashboard_estudiante',
        'tipo_certificado': 'APROBACION',
        'certificate_type': 'APROBACION',
        'nombre_materia': course.get('materia'),
        'codigo_materia': course.get('codigo_materia'),
        'matricula': course.get('codigo_estud') or student.get('codigo_estud') or course.get('estudiante_corte_id'),
        'codigo_estud': course.get('codigo_estud') or student.get('codigo_estud'),
        'fecha_inscripcion': course.get('fecha_calificacion') or course.get('fecha_matricula'),
        'fecha_inicio': course.get('fecha_inicio'),
        'nombre': course.get('nombre') or student.get('nombre'),
        'cedula': course.get('cedula'),
        'email': _student_certificate_email(student, course) or 'vista.previa@intec.edu.ec',
        'codigo_periodo': course.get('codigo_periodo'),
        'cod_anio_basica': course.get('cod_anio_basica'),
        'cod_curso': course.get('cod_curso'),
        'corte_id': course.get('corte_id'),
        'nombre_corte': course.get('nombre_corte'),
        'modalidad': 'Educación continua',
        'nota_final': _number_label(course.get('nota_final')),
        'porcentaje_asistencia': _number_label(course.get('porcentaje_asistencia'), suffix='%'),
        'skip_default_cc': True,
    }


def _student_certificate_email(student: dict[str, Any], course: dict[str, Any]) -> str:
    return _clean_text(
        course.get('correo_intec')
        or student.get('correo_intec')
        or course.get('correo_personal')
        or student.get('correo_personal')
    ).lower()


def _is_passing_grade(value: Any) -> bool:
    return is_passing_grade(value)


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _grade_status_label(nota_final: Any, estado_nota: str) -> str:
    if nota_final is None:
        return 'Sin nota'
    if _is_passing_grade(nota_final):
        return 'Aprobado'
    if estado_nota:
        return estado_nota.title()
    return 'Reprobado'


def _session_identifiers(session_user: dict[str, Any]) -> list[str]:
    values = [
        _clean_text(session_user.get('login')).lower(),
        _clean_text(session_user.get('email')).lower(),
    ]
    role = session_user.get('role') if isinstance(session_user.get('role'), dict) else {}
    values.append(_clean_text(role.get('code')).lower())
    seen: set[str] = set()
    identifiers: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            identifiers.append(value)
    return identifiers


def _fetch_all(query: str, params: list[Any]) -> list[dict[str, Any]]:
    with complement_connection().cursor() as cursor:
        cursor.execute(query, params)
        if cursor.description is None:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _clean_text(value: Any) -> str:
    return ' '.join(str(value or '').strip().split())


def _normalize_schedule_modality(value: Any) -> str:
    raw_value = _clean_text(value).upper().replace('Í', 'I')
    if raw_value in SCHEDULE_ONLINE_MODALITIES:
        return 'EN LÍNEA'
    return raw_value


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _decimal_to_number(value: Any) -> float | None:
    if value is None or value == '':
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return float(decimal_value)


def _number_label(value: Any, *, suffix: str = '') -> str:
    if value is None or value == '':
        return f'0.00{suffix}'
    try:
        decimal_value = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return f'{_clean_text(value)}{suffix}'
    return f'{decimal_value}{suffix}'


def _date_iso(value: Any) -> str:
    if not value:
        return ''
    if hasattr(value, 'date'):
        return value.date().isoformat()
    return str(value)[:10]
