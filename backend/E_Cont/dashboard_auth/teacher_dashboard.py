from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from django.db import connection, transaction

from .continuing_education import (
    ContinuingEducationError,
    complement_database_name,
    fetch_attendance_roster_from_complement,
    fetch_teacher_course_from_complement,
    fetch_teacher_courses_from_complement,
    is_complement_available,
    save_attendance_to_complement,
)
from .course_cuts import CourseCutError, list_course_cut_schedule, save_course_cut_schedule


class TeacherDashboardError(Exception):
    pass


SCHEDULE_ONLINE_MODALITIES = {'EN LÍNEA', 'EN LINEA', 'VIRTUAL', 'HIBRIDA'}


def get_teacher_course_dashboard(session_user: dict[str, Any]) -> dict[str, Any]:
    teacher = _resolve_teacher_from_session(session_user)
    if not teacher:
        raise TeacherDashboardError('No se encontró el registro docente asociado a tu sesión.')

    courses = _merge_teacher_courses(
        _fetch_complement_teacher_courses(teacher),
        _fetch_teacher_attendance_summary(teacher['codigo_doc']),
    )

    return {
        'teacher': teacher,
        'metrics': _build_teacher_course_metrics(courses),
        'courses': courses,
    }


def get_teacher_attendance_dashboard(session_user: dict[str, Any]) -> dict[str, Any]:
    teacher = _resolve_teacher_from_session(session_user)
    if not teacher:
        raise TeacherDashboardError('No se encontró el registro docente asociado a tu sesión.')

    courses = _merge_teacher_courses(
        _fetch_complement_teacher_courses(teacher),
        _fetch_teacher_attendance_summary(teacher['codigo_doc']),
    )

    return {
        'teacher': teacher,
        'metrics': {
            'cursos_asignados': len(courses),
            'estudiantes': sum(row['estudiantes'] for row in courses),
            'clases_registradas': sum(row['clases_registradas'] for row in courses),
            'registros_asistencia': sum(row['registros_asistencia'] for row in courses),
        },
        'courses': courses,
    }


def get_teacher_attendance_roster(session_user: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    teacher = _resolve_teacher_from_session(session_user)
    if not teacher:
        raise TeacherDashboardError('No se encontró el registro docente asociado a tu sesión.')

    course_payload = _clean_attendance_course_payload(params)
    attendance_date = _parse_date(params.get('fecha') or params.get('date'), default=date.today())
    if course_payload.get('corte_id'):
        try:
            complement_result = fetch_attendance_roster_from_complement(teacher, course_payload, attendance_date)
        except ContinuingEducationError as exc:
            raise TeacherDashboardError(str(exc)) from exc
        if complement_result:
            return {
                'teacher': teacher,
                **complement_result,
            }
        raise TeacherDashboardError('No se pudo cargar el corte desde la base complementaria de educación continua.')

    course = _fetch_teacher_course_or_fail(teacher['codigo_doc'], course_payload)
    students = _fetch_attendance_students(course_payload, attendance_date)

    return {
        'teacher': teacher,
        'course': course,
        'fecha': attendance_date.isoformat(),
        'students': students,
    }


def save_teacher_attendance(session_user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    teacher = _resolve_teacher_from_session(session_user)
    if not teacher:
        raise TeacherDashboardError('No se encontró el registro docente asociado a tu sesión.')

    course_payload = _clean_attendance_course_payload(payload)
    attendance_date = _parse_date(payload.get('fecha') or payload.get('date'), default=date.today())
    attendance_time = _parse_time(payload.get('hora') or payload.get('time'), default=datetime.now().time())
    records = payload.get('records') if isinstance(payload.get('records'), list) else []
    if not records:
        raise TeacherDashboardError('Debes enviar al menos un estudiante para guardar asistencia.')

    if course_payload.get('corte_id'):
        try:
            complement_result = save_attendance_to_complement(
                teacher,
                course_payload,
                attendance_date,
                attendance_time,
                records,
            )
        except ContinuingEducationError as exc:
            raise TeacherDashboardError(str(exc)) from exc
        if complement_result:
            return {
                'teacher': teacher,
                **complement_result,
            }
        raise TeacherDashboardError('No se pudo guardar asistencia en la base complementaria de educación continua.')

    course = _fetch_teacher_course_or_fail(teacher['codigo_doc'], course_payload)
    valid_students = _fetch_attendance_students(course_payload, attendance_date)
    valid_student_ids = {student['codigo_estud'] for student in valid_students}
    clean_records = _clean_attendance_records(records, valid_student_ids)
    if not clean_records:
        raise TeacherDashboardError('No hay estudiantes válidos para guardar asistencia.')

    jornada = course.get('jornada') or course_payload['cod_jornada']
    fecha_hora = datetime.combine(attendance_date, attendance_time)
    paralelo_asistencia = _attendance_parallel(course_payload['paralelo'])

    with transaction.atomic():
        with connection.cursor() as cursor:
            for record in clean_records:
                cursor.execute(
                    """
                    UPDATE dbo.ASISTENCIAESTUD
                    SET
                        FechaHora = %s,
                        jornada = %s,
                        Hora = %s,
                        Asistencia = %s
                    WHERE CAST(codigo_estud AS varchar(30)) = %s
                      AND CAST(cod_anio_Basica AS varchar(30)) = %s
                      AND CAST(codigo_materia AS varchar(30)) = %s
                      AND CAST(codigo_periodo AS varchar(30)) = %s
                      AND LTRIM(RTRIM(ISNULL(paralelo, ''))) = %s
                      AND Fecha = %s
                    """,
                    [
                        fecha_hora,
                        jornada,
                        attendance_time,
                        record['asistencia'],
                        record['codigo_estud'],
                        course_payload['cod_anio_basica'],
                        course_payload['codigo_materia'],
                        course_payload['codigo_periodo'],
                        paralelo_asistencia,
                        attendance_date,
                    ],
                )
                if cursor.rowcount:
                    continue

                cursor.execute(
                    """
                    INSERT INTO dbo.ASISTENCIAESTUD (
                        codigo_estud,
                        cod_anio_Basica,
                        codigo_materia,
                        codigo_periodo,
                        paralelo,
                        FechaHora,
                        Fecha,
                        jornada,
                        Hora,
                        Asistencia
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        _to_int(record['codigo_estud']),
                        _to_int(course_payload['cod_anio_basica']),
                        _to_int(course_payload['codigo_materia']),
                        _to_int(course_payload['codigo_periodo']),
                        paralelo_asistencia,
                        fecha_hora,
                        attendance_date,
                        jornada,
                        attendance_time,
                        record['asistencia'],
                    ],
                )

    students = _fetch_attendance_students(course_payload, attendance_date)

    return {
        'teacher': teacher,
        'course': course,
        'fecha': attendance_date.isoformat(),
        'hora': attendance_time.strftime('%H:%M'),
        'saved': len(clean_records),
        'students': students,
    }


def get_teacher_grades_dashboard(session_user: dict[str, Any]) -> dict[str, Any]:
    teacher = _resolve_teacher_from_session(session_user)
    if not teacher:
        raise TeacherDashboardError('No se encontró el registro docente asociado a tu sesión.')

    courses = _merge_teacher_courses(
        _fetch_complement_teacher_courses(teacher),
        _fetch_teacher_grade_summary(teacher['codigo_doc']),
    )
    final_average_values = [
        row['promedio_final']
        for row in courses
        if row['promedio_final'] is not None
    ]

    return {
        'teacher': teacher,
        'metrics': {
            'cursos_asignados': len(courses),
            'estudiantes': sum(row['estudiantes'] for row in courses),
            'registros_calificados': sum(row['registros_calificados'] for row in courses),
            'promedio_final': _round_float(
                sum(final_average_values) / len(final_average_values)
                if final_average_values
                else None
            ),
        },
        'courses': courses,
    }


def get_teacher_schedule_dashboard(session_user: dict[str, Any]) -> dict[str, Any]:
    teacher = _resolve_teacher_from_session(session_user)
    if not teacher:
        raise TeacherDashboardError('No se encontró el registro docente asociado a tu sesión.')

    courses = _fetch_teacher_schedule_courses(teacher)
    return {
        'teacher': teacher,
        'metrics': _build_teacher_schedule_metrics(courses),
        'courses': courses,
    }


def save_teacher_schedule(session_user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    teacher = _resolve_teacher_from_session(session_user)
    if not teacher:
        raise TeacherDashboardError('No se encontró el registro docente asociado a tu sesión.')

    corte_id = _clean_text(payload.get('corte_id') or payload.get('CorteId'))
    if not corte_id:
        raise TeacherDashboardError('Debes seleccionar una corte para cargar el horario.')

    course = _fetch_teacher_schedule_course(teacher, corte_id)
    if not course:
        raise TeacherDashboardError('La corte seleccionada no pertenece al docente autenticado.')

    try:
        result = save_course_cut_schedule(
            {
                **payload,
                'corte_id': corte_id,
            },
            user_login=teacher.get('login') or teacher.get('correo_intec') or 'DOCENTE',
        )
    except CourseCutError as exc:
        raise TeacherDashboardError(str(exc)) from exc

    return {
        'teacher': teacher,
        'course': course,
        'schedule': result,
        'dashboard': get_teacher_schedule_dashboard(session_user),
    }


def _resolve_teacher_from_session(session_user: dict[str, Any]) -> dict[str, str] | None:
    login = _clean_text(session_user.get('login')).lower()
    email = _clean_text(session_user.get('email')).lower()
    role = session_user.get('role') if isinstance(session_user.get('role'), dict) else {}
    role_code = _clean_text(role.get('code'))

    identifiers = [value for value in (login, email) if value]
    if not identifiers:
        return None

    login_filters = ' OR '.join(['LOWER(LTRIM(RTRIM(ISNULL(U.login, \'\')))) = %s' for _ in identifiers])
    params: list[Any] = [*identifiers]
    role_filter = ''
    if role_code:
        role_filter = 'AND LTRIM(RTRIM(CAST(U.tipo_usuario AS varchar(50)))) = %s'
        params.append(role_code)

    row = _fetch_one(
        f"""
        SELECT TOP (1)
            CAST(D.codigo_doc AS varchar(50)) AS codigo_doc,
            REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(D.cedula_doc, ''))), '-', ''), ' ', '') AS cedula,
            LTRIM(RTRIM(ISNULL(D.apellidos_nombre, ''))) AS nombre,
            LTRIM(RTRIM(ISNULL(D.correo, ''))) AS correo_intec,
            LTRIM(RTRIM(ISNULL(D.correop, ''))) AS correo_personal,
            LTRIM(RTRIM(ISNULL(U.login, ''))) AS login,
            LTRIM(RTRIM(ISNULL(U.Estado, ''))) AS estado_usuario
        FROM dbo.USUARIOS U
        INNER JOIN dbo.DATOSDOCENTE D
          ON REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(U.cedula, ''))), '-', ''), ' ', '')
           = REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(D.cedula_doc, ''))), '-', ''), ' ', '')
        WHERE ({login_filters})
          {role_filter}
        ORDER BY D.codigo_doc DESC
        """,
        params,
    )
    if not row:
        return None

    return {
        'codigo_doc': _clean_text(row.get('codigo_doc')),
        'cedula': _clean_text(row.get('cedula')),
        'nombre': _clean_text(row.get('nombre')),
        'correo_intec': _clean_text(row.get('correo_intec') or row.get('login')).lower(),
        'correo_personal': _clean_text(row.get('correo_personal')).lower(),
        'login': _clean_text(row.get('login')).lower(),
        'estado_usuario': _clean_text(row.get('estado_usuario')),
    }


def _fetch_teacher_attendance_summary(codigo_doc: str) -> list[dict[str, Any]]:
    rows = _fetch_all(
        """
        ;WITH asistencia AS (
            SELECT
                CAST(cod_anio_Basica AS varchar(30)) AS cod_anio_basica,
                CAST(codigo_materia AS varchar(30)) AS codigo_materia,
                CAST(codigo_periodo AS varchar(30)) AS codigo_periodo,
                LTRIM(RTRIM(ISNULL(paralelo, ''))) AS paralelo,
                LOWER(LTRIM(RTRIM(ISNULL(jornada, '')))) AS jornada,
                COUNT(1) AS registros_asistencia,
                COUNT(DISTINCT Fecha) AS clases_registradas,
                SUM(CASE WHEN ISNULL(Asistencia, 0) > 0 THEN 1 ELSE 0 END) AS asistencias_marcadas
            FROM dbo.ASISTENCIAESTUD
            GROUP BY
                CAST(cod_anio_Basica AS varchar(30)),
                CAST(codigo_materia AS varchar(30)),
                CAST(codigo_periodo AS varchar(30)),
                LTRIM(RTRIM(ISNULL(paralelo, ''))),
                LOWER(LTRIM(RTRIM(ISNULL(jornada, ''))))
        )
        SELECT
            CAST(CXD.cod_Anio_Basica AS varchar(30)) AS cod_anio_basica,
            LTRIM(RTRIM(ISNULL(C.Nombre_Basica, ''))) AS carrera,
            CAST(CXD.codigo_materia AS varchar(30)) AS codigo_materia,
            LTRIM(RTRIM(ISNULL(P.Nomb_Materia, ''))) AS materia,
            LTRIM(RTRIM(ISNULL(P.cod_materia, ''))) AS cod_materia,
            CAST(CXD.codigo_periodo AS varchar(30)) AS codigo_periodo,
            LTRIM(RTRIM(ISNULL(PE.Detalle_Periodo, ''))) AS periodo,
            LTRIM(RTRIM(ISNULL(PE.Estado, ''))) AS estado_periodo,
            LTRIM(RTRIM(ISNULL(CXD.Paralelo, ''))) AS paralelo,
            CAST(CXD.Cod_Jornada AS varchar(30)) AS cod_jornada,
            LTRIM(RTRIM(ISNULL(J.DetalleJ, ''))) AS jornada,
            COUNT(DISTINCT CAST(CE.codigo_estud AS varchar(30))) AS estudiantes,
            ISNULL(MAX(A.registros_asistencia), 0) AS registros_asistencia,
            ISNULL(MAX(A.clases_registradas), 0) AS clases_registradas,
            ISNULL(MAX(A.asistencias_marcadas), 0) AS asistencias_marcadas
        FROM dbo.CARRERAXDOCENTE CXD
        LEFT JOIN dbo.PENSUM P
          ON CAST(P.codigo_materia AS varchar(30)) = CAST(CXD.codigo_materia AS varchar(30))
         AND CAST(P.Cod_AnioBasica AS varchar(30)) = CAST(CXD.cod_Anio_Basica AS varchar(30))
        LEFT JOIN dbo.CARRERAS C
          ON CAST(C.Cod_AnioBasica AS varchar(30)) = CAST(CXD.cod_Anio_Basica AS varchar(30))
        LEFT JOIN dbo.PERIODO PE
          ON CAST(PE.cod_periodo AS varchar(30)) = CAST(CXD.codigo_periodo AS varchar(30))
        LEFT JOIN dbo.JORNADA J
          ON CAST(J.NumJ AS varchar(30)) = CAST(CXD.Cod_Jornada AS varchar(30))
        LEFT JOIN dbo.CARRERAXESTUD CE
          ON CAST(CE.cod_anio_Basica AS varchar(30)) = CAST(CXD.cod_Anio_Basica AS varchar(30))
         AND CAST(CE.codigo_materia AS varchar(30)) = CAST(CXD.codigo_materia AS varchar(30))
         AND CAST(CE.codigo_periodo AS varchar(30)) = CAST(CXD.codigo_periodo AS varchar(30))
         AND LTRIM(RTRIM(ISNULL(CE.paralelo, ''))) = LTRIM(RTRIM(ISNULL(CXD.Paralelo, '')))
        LEFT JOIN asistencia A
          ON A.cod_anio_basica = CAST(CXD.cod_Anio_Basica AS varchar(30))
         AND A.codigo_materia = CAST(CXD.codigo_materia AS varchar(30))
         AND A.codigo_periodo = CAST(CXD.codigo_periodo AS varchar(30))
         AND (
            A.paralelo = LTRIM(RTRIM(ISNULL(CXD.Paralelo, '')))
            OR A.paralelo = LEFT(LTRIM(RTRIM(ISNULL(CXD.Paralelo, ''))), 1)
         )
         AND (
            A.jornada = LOWER(LTRIM(RTRIM(ISNULL(J.DetalleJ, ''))))
            OR A.jornada = LOWER(LTRIM(RTRIM(CAST(CXD.Cod_Jornada AS varchar(30)))))
         )
        WHERE CAST(CXD.codigo_doc AS varchar(50)) = %s
        GROUP BY
            CXD.cod_Anio_Basica,
            C.Nombre_Basica,
            CXD.codigo_materia,
            P.Nomb_Materia,
            P.cod_materia,
            CXD.codigo_periodo,
            PE.Detalle_Periodo,
            PE.Estado,
            CXD.Paralelo,
            CXD.Cod_Jornada,
            J.DetalleJ,
            PE.Orden
        ORDER BY
            ISNULL(PE.Orden, 0) DESC,
            CXD.codigo_periodo DESC,
            C.Nombre_Basica ASC,
            P.Nomb_Materia ASC,
            CXD.Paralelo ASC
        """,
        [codigo_doc],
    )
    return [_serialize_attendance_course(row) for row in rows]


def _fetch_teacher_course_or_fail(codigo_doc: str, course_payload: dict[str, str]) -> dict[str, Any]:
    row = _fetch_one(
        """
        SELECT TOP (1)
            CAST(CXD.cod_Anio_Basica AS varchar(30)) AS cod_anio_basica,
            LTRIM(RTRIM(ISNULL(C.Nombre_Basica, ''))) AS carrera,
            CAST(CXD.codigo_materia AS varchar(30)) AS codigo_materia,
            LTRIM(RTRIM(ISNULL(P.Nomb_Materia, ''))) AS materia,
            LTRIM(RTRIM(ISNULL(P.cod_materia, ''))) AS cod_materia,
            CAST(CXD.codigo_periodo AS varchar(30)) AS codigo_periodo,
            LTRIM(RTRIM(ISNULL(PE.Detalle_Periodo, ''))) AS periodo,
            LTRIM(RTRIM(ISNULL(PE.Estado, ''))) AS estado_periodo,
            LTRIM(RTRIM(ISNULL(CXD.Paralelo, ''))) AS paralelo,
            CAST(CXD.Cod_Jornada AS varchar(30)) AS cod_jornada,
            LTRIM(RTRIM(ISNULL(J.DetalleJ, ''))) AS jornada
        FROM dbo.CARRERAXDOCENTE CXD
        LEFT JOIN dbo.PENSUM P
          ON CAST(P.codigo_materia AS varchar(30)) = CAST(CXD.codigo_materia AS varchar(30))
         AND CAST(P.Cod_AnioBasica AS varchar(30)) = CAST(CXD.cod_Anio_Basica AS varchar(30))
        LEFT JOIN dbo.CARRERAS C
          ON CAST(C.Cod_AnioBasica AS varchar(30)) = CAST(CXD.cod_Anio_Basica AS varchar(30))
        LEFT JOIN dbo.PERIODO PE
          ON CAST(PE.cod_periodo AS varchar(30)) = CAST(CXD.codigo_periodo AS varchar(30))
        LEFT JOIN dbo.JORNADA J
          ON CAST(J.NumJ AS varchar(30)) = CAST(CXD.Cod_Jornada AS varchar(30))
        WHERE CAST(CXD.codigo_doc AS varchar(50)) = %s
          AND CAST(CXD.cod_Anio_Basica AS varchar(30)) = %s
          AND CAST(CXD.codigo_materia AS varchar(30)) = %s
          AND CAST(CXD.codigo_periodo AS varchar(30)) = %s
          AND LTRIM(RTRIM(ISNULL(CXD.Paralelo, ''))) = %s
          AND CAST(CXD.Cod_Jornada AS varchar(30)) = %s
        """,
        [
            codigo_doc,
            course_payload['cod_anio_basica'],
            course_payload['codigo_materia'],
            course_payload['codigo_periodo'],
            course_payload['paralelo'],
            course_payload['cod_jornada'],
        ],
    )
    if not row:
        raise TeacherDashboardError('La materia seleccionada no pertenece al docente autenticado.')
    return _serialize_base_course(row)


def _fetch_attendance_students(course_payload: dict[str, str], attendance_date: date) -> list[dict[str, Any]]:
    rows = _fetch_all(
        """
        SELECT
            CAST(CE.codigo_estud AS varchar(30)) AS codigo_estud,
            LTRIM(RTRIM(ISNULL(DE.Cedula_Est, ''))) AS cedula,
            LTRIM(RTRIM(ISNULL(DE.Apellidos_nombre, ''))) AS nombre,
            CAST(A.Asistencia AS varchar(10)) AS asistencia,
            CONVERT(varchar(5), A.Hora, 108) AS hora
        FROM dbo.CARRERAXESTUD CE
        INNER JOIN dbo.DATOS_ESTUD DE
          ON CAST(DE.codigo_estud AS varchar(30)) = CAST(CE.codigo_estud AS varchar(30))
        LEFT JOIN dbo.ASISTENCIAESTUD A
          ON CAST(A.codigo_estud AS varchar(30)) = CAST(CE.codigo_estud AS varchar(30))
         AND CAST(A.cod_anio_Basica AS varchar(30)) = CAST(CE.cod_anio_Basica AS varchar(30))
         AND CAST(A.codigo_materia AS varchar(30)) = CAST(CE.codigo_materia AS varchar(30))
         AND CAST(A.codigo_periodo AS varchar(30)) = CAST(CE.codigo_periodo AS varchar(30))
         AND (
            LTRIM(RTRIM(ISNULL(A.paralelo, ''))) = LTRIM(RTRIM(ISNULL(CE.paralelo, '')))
            OR LTRIM(RTRIM(ISNULL(A.paralelo, ''))) = LEFT(LTRIM(RTRIM(ISNULL(CE.paralelo, ''))), 1)
         )
         AND A.Fecha = %s
        WHERE CAST(CE.cod_anio_Basica AS varchar(30)) = %s
          AND CAST(CE.codigo_materia AS varchar(30)) = %s
          AND CAST(CE.codigo_periodo AS varchar(30)) = %s
          AND LTRIM(RTRIM(ISNULL(CE.paralelo, ''))) = %s
        ORDER BY DE.Apellidos_nombre ASC
        """,
        [
            attendance_date,
            course_payload['cod_anio_basica'],
            course_payload['codigo_materia'],
            course_payload['codigo_periodo'],
            course_payload['paralelo'],
        ],
    )
    return [_serialize_attendance_student(row) for row in rows]


def _fetch_teacher_grade_summary(codigo_doc: str) -> list[dict[str, Any]]:
    rows = _fetch_all(
        """
        SELECT
            CAST(CXD.cod_Anio_Basica AS varchar(30)) AS cod_anio_basica,
            LTRIM(RTRIM(ISNULL(C.Nombre_Basica, ''))) AS carrera,
            CAST(CXD.codigo_materia AS varchar(30)) AS codigo_materia,
            LTRIM(RTRIM(ISNULL(P.Nomb_Materia, ''))) AS materia,
            LTRIM(RTRIM(ISNULL(P.cod_materia, ''))) AS cod_materia,
            CAST(CXD.codigo_periodo AS varchar(30)) AS codigo_periodo,
            LTRIM(RTRIM(ISNULL(PE.Detalle_Periodo, ''))) AS periodo,
            LTRIM(RTRIM(ISNULL(PE.Estado, ''))) AS estado_periodo,
            LTRIM(RTRIM(ISNULL(CXD.Paralelo, ''))) AS paralelo,
            CAST(CXD.Cod_Jornada AS varchar(30)) AS cod_jornada,
            LTRIM(RTRIM(ISNULL(J.DetalleJ, ''))) AS jornada,
            COUNT(DISTINCT CAST(CE.codigo_estud AS varchar(30))) AS estudiantes,
            SUM(CASE
                WHEN (
                    CE.P1Tareas IS NOT NULL
                    OR CE.P1Proyectos IS NOT NULL
                    OR CE.P1Examen IS NOT NULL
                    OR CE.promP1 IS NOT NULL
                    OR CE.P2Tareas IS NOT NULL
                    OR CE.P2Proyectos IS NOT NULL
                    OR CE.P2Examen IS NOT NULL
                    OR CE.promP2 IS NOT NULL
                    OR CE.P3Tareas IS NOT NULL
                    OR CE.P3Proyectos IS NOT NULL
                    OR CE.P3Examen IS NOT NULL
                    OR CE.promP3 IS NOT NULL
                    OR CE.PromedioFinal IS NOT NULL
                )
                THEN 1 ELSE 0
            END) AS registros_calificados,
            AVG(CAST(CE.promP1 AS float)) AS promedio_p1,
            AVG(CAST(CE.promP2 AS float)) AS promedio_p2,
            AVG(CAST(CE.promP3 AS float)) AS promedio_p3,
            AVG(CAST(CE.PromedioFinal AS float)) AS promedio_final
        FROM dbo.CARRERAXDOCENTE CXD
        LEFT JOIN dbo.PENSUM P
          ON CAST(P.codigo_materia AS varchar(30)) = CAST(CXD.codigo_materia AS varchar(30))
         AND CAST(P.Cod_AnioBasica AS varchar(30)) = CAST(CXD.cod_Anio_Basica AS varchar(30))
        LEFT JOIN dbo.CARRERAS C
          ON CAST(C.Cod_AnioBasica AS varchar(30)) = CAST(CXD.cod_Anio_Basica AS varchar(30))
        LEFT JOIN dbo.PERIODO PE
          ON CAST(PE.cod_periodo AS varchar(30)) = CAST(CXD.codigo_periodo AS varchar(30))
        LEFT JOIN dbo.JORNADA J
          ON CAST(J.NumJ AS varchar(30)) = CAST(CXD.Cod_Jornada AS varchar(30))
        LEFT JOIN dbo.CARRERAXESTUD CE
          ON CAST(CE.cod_anio_Basica AS varchar(30)) = CAST(CXD.cod_Anio_Basica AS varchar(30))
         AND CAST(CE.codigo_materia AS varchar(30)) = CAST(CXD.codigo_materia AS varchar(30))
         AND CAST(CE.codigo_periodo AS varchar(30)) = CAST(CXD.codigo_periodo AS varchar(30))
         AND LTRIM(RTRIM(ISNULL(CE.paralelo, ''))) = LTRIM(RTRIM(ISNULL(CXD.Paralelo, '')))
        WHERE CAST(CXD.codigo_doc AS varchar(50)) = %s
        GROUP BY
            CXD.cod_Anio_Basica,
            C.Nombre_Basica,
            CXD.codigo_materia,
            P.Nomb_Materia,
            P.cod_materia,
            CXD.codigo_periodo,
            PE.Detalle_Periodo,
            PE.Estado,
            CXD.Paralelo,
            CXD.Cod_Jornada,
            J.DetalleJ,
            PE.Orden
        ORDER BY
            ISNULL(PE.Orden, 0) DESC,
            CXD.codigo_periodo DESC,
            C.Nombre_Basica ASC,
            P.Nomb_Materia ASC,
            CXD.Paralelo ASC
        """,
        [codigo_doc],
    )
    return [_serialize_grade_course(row) for row in rows]


def _fetch_teacher_course_metrics(codigo_doc: str) -> dict[str, int]:
    row = _fetch_one(
        """
        SELECT
            COUNT(1) AS total_cursos,
            COUNT(DISTINCT CONCAT(
                CAST(CXD.cod_Anio_Basica AS varchar(30)),
                ':',
                CAST(CXD.codigo_materia AS varchar(30))
            )) AS materias_distintas,
            COUNT(DISTINCT CAST(CXD.codigo_periodo AS varchar(30))) AS periodos_distintos,
            SUM(CASE WHEN LTRIM(RTRIM(ISNULL(PE.Estado, ''))) = 'A' THEN 1 ELSE 0 END) AS cursos_periodo_activo
        FROM dbo.CARRERAXDOCENTE CXD
        LEFT JOIN dbo.PERIODO PE
          ON CAST(PE.cod_periodo AS varchar(30)) = CAST(CXD.codigo_periodo AS varchar(30))
        WHERE CAST(CXD.codigo_doc AS varchar(50)) = %s
        """,
        [codigo_doc],
    ) or {}

    return {
        'total_cursos': _to_int(row.get('total_cursos')),
        'materias_distintas': _to_int(row.get('materias_distintas')),
        'periodos_distintos': _to_int(row.get('periodos_distintos')),
        'cursos_periodo_activo': _to_int(row.get('cursos_periodo_activo')),
    }


def _fetch_complement_teacher_courses(teacher: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return fetch_teacher_courses_from_complement(teacher) or []
    except Exception:
        return []


def _fetch_teacher_schedule_courses(teacher: dict[str, Any]) -> list[dict[str, Any]]:
    required = [
        ('edu', 'VW_MatriculaDocenteCompleta', 'V'),
        ('edu', 'VW_CorteCursoDetalle', 'V'),
        ('edu', 'HorarioCorte', 'U'),
        ('edu', 'SesionCorte', 'U'),
    ]
    if not is_complement_available(required):
        return []

    rows = _fetch_all(
        f"""
        SELECT
            D.[DocenteCorteId],
            D.[CorteId],
            D.[CodigoDocente],
            D.[RolDocente],
            D.[EstadoDocenteCorte],
            C.[TipoOferta],
            C.[Cod_AnioBasica],
            C.[CodigoPeriodo],
            C.[CodigoMateria],
            C.[CodCurso],
            C.[NombreCursoMateria],
            C.[NombreCorte],
            C.[FechaInicio],
            C.[FechaFin],
            C.[EstadoCorteEdu],
            COUNT(DISTINCT M.[EstudianteCorteId]) AS [Estudiantes],
            COUNT(DISTINCT H.[HorarioId]) AS [Horarios],
            COUNT(DISTINCT CASE WHEN H.[Modalidad] IN (N'EN LÍNEA','EN LINEA','VIRTUAL','HIBRIDA') THEN H.[HorarioId] END) AS [HorariosVirtuales],
            COUNT(DISTINCT S.[SesionId]) AS [Sesiones]
        FROM [{complement_database_name()}].[edu].[VW_MatriculaDocenteCompleta] D
        INNER JOIN [{complement_database_name()}].[edu].[VW_CorteCursoDetalle] C
          ON C.[CorteId] = D.[CorteId]
        LEFT JOIN [{complement_database_name()}].[edu].[VW_MatriculaEstudianteCompleta] M
          ON M.[CorteId] = D.[CorteId]
         AND (M.[EstadoMatricula] COLLATE DATABASE_DEFAULT) NOT IN ('ANULADO','RETIRADO')
        LEFT JOIN [{complement_database_name()}].[edu].[HorarioCorte] H
          ON H.[CorteId] = D.[CorteId]
         AND (H.[EstadoHorario] COLLATE DATABASE_DEFAULT) = 'ACTIVO'
        LEFT JOIN [{complement_database_name()}].[edu].[SesionCorte] S
          ON S.[HorarioId] = H.[HorarioId]
         AND (S.[EstadoSesion] COLLATE DATABASE_DEFAULT) <> 'CANCELADA'
        WHERE (D.[EstadoDocenteCorte] COLLATE DATABASE_DEFAULT) = 'ACTIVO'
          AND (
              LTRIM(RTRIM(CAST(D.[CodigoDocente] AS varchar(50)))) = %s
              OR (D.[CedulaDoc] COLLATE DATABASE_DEFAULT) = %s
              OR LOWER(LTRIM(RTRIM(ISNULL(D.[CorreoIntec], '')))) COLLATE DATABASE_DEFAULT = %s
              OR LOWER(LTRIM(RTRIM(ISNULL(D.[UsuarioLogin], '')))) COLLATE DATABASE_DEFAULT = %s
              OR LOWER(LTRIM(RTRIM(ISNULL(D.[UsuarioSisLogin], '')))) COLLATE DATABASE_DEFAULT = %s
          )
        GROUP BY
            D.[DocenteCorteId],
            D.[CorteId],
            D.[CodigoDocente],
            D.[RolDocente],
            D.[EstadoDocenteCorte],
            C.[TipoOferta],
            C.[Cod_AnioBasica],
            C.[CodigoPeriodo],
            C.[CodigoMateria],
            C.[CodCurso],
            C.[NombreCursoMateria],
            C.[NombreCorte],
            C.[FechaInicio],
            C.[FechaFin],
            C.[EstadoCorteEdu]
        ORDER BY C.[FechaInicio] DESC, D.[CorteId] DESC
        """,
        [
            _clean_text(teacher.get('codigo_doc')),
            _clean_text(teacher.get('cedula')),
            _clean_text(teacher.get('correo_intec') or teacher.get('login')).lower(),
            _clean_text(teacher.get('login')).lower(),
            _clean_text(teacher.get('login')).lower(),
        ],
    )

    courses: list[dict[str, Any]] = []
    for row in rows:
        course = _serialize_teacher_schedule_course(row)
        try:
            schedule_result = list_course_cut_schedule(course['corte_id'])
        except Exception:
            schedule_result = {'schedules': [], 'metrics': {}}
        course['schedules'] = schedule_result.get('schedules') or []
        course['schedule_metrics'] = schedule_result.get('metrics') or {}
        courses.append(course)
    return courses


def _fetch_teacher_schedule_course(teacher: dict[str, Any], corte_id: Any) -> dict[str, Any] | None:
    try:
        course = fetch_teacher_course_from_complement(teacher, {'corte_id': corte_id})
    except Exception:
        course = None
    if course:
        return course

    for current in _fetch_teacher_schedule_courses(teacher):
        if _clean_text(current.get('corte_id')) == _clean_text(corte_id):
            return current
    return None


def _serialize_teacher_schedule_course(row: dict[str, Any]) -> dict[str, Any]:
    codigo_materia = _clean_text(row.get('CodigoMateria') or row.get('CodCurso') or row.get('CorteId'))
    estado_corte = _clean_text(row.get('EstadoCorteEdu')).upper()
    return {
        'source': 'continuing_education',
        'corte_id': _clean_text(row.get('CorteId')),
        'docente_corte_id': _clean_text(row.get('DocenteCorteId')),
        'tipo_oferta': _clean_text(row.get('TipoOferta')),
        'cod_anio_basica': _clean_text(row.get('Cod_AnioBasica')),
        'carrera': 'Educación continua',
        'codigo_materia': codigo_materia,
        'cod_materia': codigo_materia,
        'materia': _clean_text(row.get('NombreCursoMateria')) or 'Sin materia',
        'codigo_periodo': _clean_text(row.get('CodigoPeriodo') or row.get('CorteId')),
        'periodo': _clean_text(row.get('NombreCorte') or row.get('CodigoPeriodo')) or 'Sin período',
        'fecha_inicio': _date_iso(row.get('FechaInicio')),
        'fecha_fin': _date_iso(row.get('FechaFin')),
        'estado_periodo': 'A' if estado_corte == 'ABIERTO' else estado_corte,
        'paralelo': 'A',
        'cod_jornada': '0',
        'jornada': 'N/D',
        'rol_docente': _clean_text(row.get('RolDocente')),
        'estudiantes': _to_int(row.get('Estudiantes')),
        'horarios': _to_int(row.get('Horarios')),
        'horarios_virtuales': _to_int(row.get('HorariosVirtuales')),
        'sesiones': _to_int(row.get('Sesiones')),
    }


def _merge_teacher_courses(
    complement_courses: list[dict[str, Any]],
    legacy_courses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for course in [*complement_courses, *legacy_courses]:
        key = _teacher_course_match_key(course)
        if key in seen:
            continue
        seen.add(key)
        merged.append(course)

    return merged


def _teacher_course_match_key(course: dict[str, Any]) -> str:
    academic_key = '|'.join(
        [
            _clean_text(course.get('cod_anio_basica')),
            _clean_text(course.get('codigo_materia')),
            _clean_text(course.get('codigo_periodo')),
            _clean_text(course.get('paralelo')).upper(),
        ]
    )
    if academic_key.replace('|', ''):
        return f'academic:{academic_key}'
    return f"corte:{_clean_text(course.get('corte_id')) or id(course)}"


def _build_teacher_course_metrics(courses: list[dict[str, Any]]) -> dict[str, int]:
    subjects = {
        (
            _clean_text(course.get('cod_anio_basica')),
            _clean_text(course.get('codigo_materia')),
        )
        for course in courses
        if _clean_text(course.get('codigo_materia')) or _clean_text(course.get('materia'))
    }
    periods = {
        _clean_text(course.get('codigo_periodo') or course.get('periodo'))
        for course in courses
        if _clean_text(course.get('codigo_periodo') or course.get('periodo'))
    }
    active_courses = [
        course for course in courses
        if _clean_text(course.get('estado_periodo')).upper() in {'A', 'ABIERTO', 'ACTIVO'}
    ]
    return {
        'total_cursos': len(courses),
        'materias_distintas': len(subjects),
        'periodos_distintos': len(periods),
        'cursos_periodo_activo': len(active_courses),
    }


def _build_teacher_schedule_metrics(courses: list[dict[str, Any]]) -> dict[str, int]:
    schedules = [schedule for course in courses for schedule in course.get('schedules', [])]
    return {
        'cursos_asignados': len(courses),
        'horarios': len(schedules),
        'sesiones': sum(_to_int(schedule.get('total_sesiones')) for schedule in schedules),
        'virtuales': len([schedule for schedule in schedules if schedule.get('modalidad') in SCHEDULE_ONLINE_MODALITIES]),
    }


def _serialize_base_course(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'source': 'legacy',
        'cod_anio_basica': _clean_text(row.get('cod_anio_basica')),
        'carrera': _clean_text(row.get('carrera')) or 'Sin carrera',
        'codigo_materia': _clean_text(row.get('codigo_materia')),
        'cod_materia': _clean_text(row.get('cod_materia')),
        'materia': _clean_text(row.get('materia')) or 'Sin materia',
        'codigo_periodo': _clean_text(row.get('codigo_periodo')),
        'periodo': _clean_text(row.get('periodo')) or 'Sin período',
        'estado_periodo': _clean_text(row.get('estado_periodo')),
        'paralelo': _clean_text(row.get('paralelo')) or 'N/D',
        'cod_jornada': _clean_text(row.get('cod_jornada')),
        'jornada': _clean_text(row.get('jornada')) or _clean_text(row.get('cod_jornada')) or 'N/D',
    }


def _serialize_attendance_course(row: dict[str, Any]) -> dict[str, Any]:
    course = _serialize_base_course(row)
    course.update(
        {
            'estudiantes': _to_int(row.get('estudiantes')),
            'registros_asistencia': _to_int(row.get('registros_asistencia')),
            'clases_registradas': _to_int(row.get('clases_registradas')),
            'asistencias_marcadas': _to_int(row.get('asistencias_marcadas')),
        }
    )
    return course


def _serialize_attendance_student(row: dict[str, Any]) -> dict[str, Any]:
    asistencia = _clean_text(row.get('asistencia'))
    present = asistencia not in {'', '0'}
    return {
        'codigo_estud': _clean_text(row.get('codigo_estud')),
        'cedula': _clean_text(row.get('cedula')),
        'nombre': _clean_text(row.get('nombre')) or 'Sin nombre',
        'asistencia': 1 if present else 0,
        'presente': present,
        'hora': _clean_text(row.get('hora')),
    }


def _serialize_grade_course(row: dict[str, Any]) -> dict[str, Any]:
    course = _serialize_base_course(row)
    course.update(
        {
            'estudiantes': _to_int(row.get('estudiantes')),
            'registros_calificados': _to_int(row.get('registros_calificados')),
            'promedio_p1': _round_float(row.get('promedio_p1')),
            'promedio_p2': _round_float(row.get('promedio_p2')),
            'promedio_p3': _round_float(row.get('promedio_p3')),
            'promedio_final': _round_float(row.get('promedio_final')),
        }
    )
    return course


def _clean_attendance_course_payload(payload: dict[str, Any]) -> dict[str, str]:
    nested_course = payload.get('course') if isinstance(payload.get('course'), dict) else {}
    corte_id = _clean_text(payload.get('corte_id') or payload.get('CorteId') or nested_course.get('corte_id'))
    if corte_id:
        return {
            'source': 'continuing_education',
            'corte_id': corte_id,
            'cod_anio_basica': _clean_text(payload.get('cod_anio_basica') or nested_course.get('cod_anio_basica')),
            'codigo_materia': _clean_text(payload.get('codigo_materia') or nested_course.get('codigo_materia')),
            'codigo_periodo': _clean_text(payload.get('codigo_periodo') or nested_course.get('codigo_periodo')),
            'paralelo': _clean_text(payload.get('paralelo') or nested_course.get('paralelo')) or 'A',
            'cod_jornada': _clean_text(payload.get('cod_jornada') or nested_course.get('cod_jornada')) or '0',
        }

    cod_anio_basica = _clean_text(payload.get('cod_anio_basica') or nested_course.get('cod_anio_basica'))
    codigo_materia = _clean_text(payload.get('codigo_materia') or nested_course.get('codigo_materia'))
    codigo_periodo = _clean_text(payload.get('codigo_periodo') or nested_course.get('codigo_periodo'))
    paralelo = _clean_text(payload.get('paralelo') or nested_course.get('paralelo'))
    cod_jornada = _clean_text(payload.get('cod_jornada') or nested_course.get('cod_jornada'))

    if not cod_anio_basica:
        raise TeacherDashboardError('Debes seleccionar la carrera de la materia.')
    if not codigo_materia:
        raise TeacherDashboardError('Debes seleccionar la materia.')
    if not codigo_periodo:
        raise TeacherDashboardError('Debes seleccionar el período.')
    if not paralelo:
        raise TeacherDashboardError('Debes seleccionar el paralelo.')
    if not cod_jornada:
        raise TeacherDashboardError('Debes seleccionar la jornada.')

    return {
        'cod_anio_basica': cod_anio_basica,
        'codigo_materia': codigo_materia,
        'codigo_periodo': codigo_periodo,
        'paralelo': paralelo,
        'cod_jornada': cod_jornada,
    }


def _clean_attendance_records(records: list[Any], valid_student_ids: set[str]) -> list[dict[str, Any]]:
    clean_records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for record in records:
        if not isinstance(record, dict):
            continue
        codigo_estud = _clean_text(record.get('codigo_estud') or record.get('student_id'))
        if not codigo_estud or codigo_estud not in valid_student_ids or codigo_estud in seen:
            continue
        seen.add(codigo_estud)
        raw_value = record.get('asistencia')
        if raw_value is None:
            raw_value = record.get('presente')
        asistencia = 1 if _truthy_attendance(raw_value) else 0
        clean_records.append(
            {
                'codigo_estud': codigo_estud,
                'asistencia': asistencia,
            }
        )

    return clean_records


def _parse_date(value: Any, *, default: date) -> date:
    clean_value = _clean_text(value)
    if not clean_value:
        return default
    try:
        return date.fromisoformat(clean_value[:10])
    except ValueError as exc:
        raise TeacherDashboardError('La fecha de asistencia no es valida.') from exc


def _parse_time(value: Any, *, default: time) -> time:
    clean_value = _clean_text(value)
    if not clean_value:
        return default.replace(second=0, microsecond=0)
    try:
        return time.fromisoformat(clean_value[:5])
    except ValueError as exc:
        raise TeacherDashboardError('La hora de asistencia no es valida.') from exc


def _attendance_parallel(value: Any) -> str:
    clean_value = _clean_text(value)
    return (clean_value[:1] or 'A').upper()


def _truthy_attendance(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    clean_value = _clean_text(value).lower()
    return clean_value not in {'', '0', 'false', 'no', 'ausente', 'falta'}


def _fetch_one(query: str, params: list[Any] | tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    rows = _fetch_all(query, params)
    return rows[0] if rows else None


def _fetch_all(query: str, params: list[Any] | tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params or [])
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _clean_text(value: Any) -> str:
    return str(value or '').strip()


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _round_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _date_iso(value: Any) -> str:
    if not value:
        return ''
    if hasattr(value, 'date'):
        return value.date().isoformat()
    return str(value)[:10]
