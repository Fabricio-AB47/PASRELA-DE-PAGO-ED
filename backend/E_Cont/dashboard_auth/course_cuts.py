from __future__ import annotations

import os
import re
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from django.db import connection, transaction

from .continuing_education import (
    connection_for_query,
    complement_database_name,
    complement_version,
    configure_cut_in_complement,
    is_complement_available,
    sync_student_enrollment_to_complement,
)
from .notifications import create_notification_safely


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
SCHEDULE_MODALITIES = {'EN LÍNEA', 'PRESENCIAL'}


def list_course_cuts() -> list[dict[str, Any]]:
    _ensure_course_cut_schema()
    query = """
        SELECT TOP (300)
            R.CorteId,
            R.TipoOferta,
            R.NumeroCorte,
            R.NombreCorte,
            R.FechaInicio,
            R.FechaFin,
            R.EstadoCorte,
            R.Cod_AnioBasica,
            R.Carrera,
            R.CodigoPeriodo,
            R.Periodo,
            R.CodigoMateria,
            R.MateriaPensum,
            R.CodCurso,
            R.CursoEduContinua,
            R.CupoEsperado,
            R.TotalEstudiantes,
            R.TotalInscritos,
            R.TotalCursando,
            R.TotalRetirados,
            R.TotalAprobados,
            R.TotalReprobados,
            R.TotalFinalizados,
            CC.Horas,
            CC.Observacion
        FROM dbo.VW_CORTE_RESUMEN R
        INNER JOIN dbo.CORTE_CURSO CC ON CC.CorteId = R.CorteId
        ORDER BY R.FechaInicio DESC, R.NumeroCorte DESC, R.CorteId DESC
    """
    return [_normalize_cut_summary(row) for row in _fetch_all(query, [])]


def create_course_cut(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_course_cut_schema()
    tipo_oferta = _clean_text(payload.get('tipo_oferta') or 'CARRERA').upper()
    if tipo_oferta not in {'CARRERA', 'EDUCONTINUA'}:
        raise CourseCutError('El tipo de oferta debe ser CARRERA o EDUCONTINUA.')

    fecha_inicio = _clean_text(payload.get('fecha_inicio'))
    if not fecha_inicio:
        raise CourseCutError('Debes ingresar la fecha de inicio de la cohorte.')
    if not _coerce_date(fecha_inicio):
        raise CourseCutError('La fecha de inicio de la cohorte no es válida.')

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
    nombre_corte = _clean_text(payload.get('nombre_corte')) or f'Cohorte {numero_corte}'

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
        raise CourseCutError('No fue posible crear la cohorte.')

    for cut in created_cuts:
        cut['continuing_education'] = _sync_cut_to_complement(cut, user_login=user_login)

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


def update_course_cut(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_course_cut_schema()
    corte_id = _safe_int(payload.get('corte_id') or payload.get('CorteId'), default=0)
    if corte_id <= 0:
        raise CourseCutError('Selecciona la cohorte que deseas actualizar.')

    current = _fetch_cut_by_id(corte_id)
    if not current:
        raise CourseCutError('No se encontró la cohorte seleccionada.')

    numero_corte = _safe_int(
        payload.get('numero_corte') if 'numero_corte' in payload else current.get('numero_corte'),
        default=0,
    )
    if numero_corte <= 0:
        raise CourseCutError('El número de cohorte debe ser mayor a cero.')

    nombre_corte = _trim_to_max(
        payload.get('nombre_corte') if 'nombre_corte' in payload else current.get('nombre_corte'),
        150,
    )
    if not nombre_corte:
        raise CourseCutError('El nombre de la cohorte es obligatorio.')

    fecha_inicio_text = _clean_text(
        payload.get('fecha_inicio') if 'fecha_inicio' in payload else current.get('fecha_inicio_iso')
    )
    fecha_inicio = _coerce_date(fecha_inicio_text)
    if not fecha_inicio:
        raise CourseCutError('La fecha de inicio de la cohorte no es válida.')

    fecha_fin_text = _clean_text(
        payload.get('fecha_fin') if 'fecha_fin' in payload else current.get('fecha_fin_iso')
    )
    fecha_fin = _coerce_date(fecha_fin_text) if fecha_fin_text else None
    if fecha_fin_text and not fecha_fin:
        raise CourseCutError('La fecha final de inscripción no es válida.')
    if fecha_fin and fecha_fin < fecha_inicio:
        raise CourseCutError('La fecha final no puede ser anterior al inicio de la cohorte.')

    cupo_esperado = _int_or_none(
        payload.get('cupo_esperado') if 'cupo_esperado' in payload else current.get('cupo_esperado')
    )
    if cupo_esperado is not None and cupo_esperado < 0:
        raise CourseCutError('El cupo esperado no puede ser negativo.')
    total_estudiantes = _safe_int(current.get('total_estudiantes'), default=0)
    if cupo_esperado is not None and cupo_esperado < total_estudiantes:
        raise CourseCutError(
            f'El cupo no puede ser menor a los {total_estudiantes} estudiantes ya registrados.'
        )

    horas = _int_or_none(
        payload.get('horas') if 'horas' in payload else current.get('horas')
    )
    if horas is not None and horas < 0:
        raise CourseCutError('Las horas no pueden ser negativas.')
    observacion = _trim_to_max(
        payload.get('observacion') if 'observacion' in payload else current.get('observacion'),
        500,
    ) or None

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.CORTE_CURSO
            SET NumeroCorte = %s,
                NombreCorte = %s,
                FechaInicio = %s,
                FechaFin = %s,
                CupoEsperado = %s,
                Horas = %s,
                Observacion = %s,
                UsuarioModifica = %s,
                FechaModifica = SYSDATETIME()
            WHERE CorteId = %s
            """,
            [
                numero_corte,
                nombre_corte,
                fecha_inicio,
                fecha_fin,
                cupo_esperado,
                horas,
                observacion,
                _trim_to_max(user_login, 50) or 'SISTEMA',
                corte_id,
            ],
        )
        if cursor.rowcount == 0:
            raise CourseCutError('No fue posible actualizar la cohorte.')

    updated = _fetch_cut_by_id(corte_id)
    if not updated:
        raise CourseCutError('La cohorte fue actualizada, pero no pudo volver a consultarse.')
    updated['continuing_education'] = _sync_cut_to_complement(updated, user_login=user_login)
    return updated


def assign_matricula_to_open_cut(
    *,
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    num_matricula: int,
    usuario_registro: str = 'SISTEMA',
    observacion: str = '',
    valor_total_curso: Any = None,
    origen_matricula: str = '',
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

    continuing_education = _sync_student_to_complement(
        corte_id=cut['corte_id'],
        codigo_estud=codigo_estud,
        usuario_registro=usuario_registro,
        valor_total_curso=valor_total_curso,
        origen_matricula=origen_matricula,
    )

    return {
        **cut,
        'assigned': True,
        'continuing_education': continuing_education,
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


def list_course_cut_students(corte_id: Any) -> dict[str, Any]:
    _ensure_course_cut_schema()
    normalized_corte_id = _safe_int(corte_id, default=0)
    if normalized_corte_id <= 0:
        raise CourseCutError('Debes seleccionar una corte para consultar estudiantes.')

    cut = _fetch_cut_by_id(normalized_corte_id)
    if not cut:
        raise CourseCutError('No se encontró la corte seleccionada.')

    complement_status = _course_cut_complement_status()
    complement_index = _fetch_complement_student_index(normalized_corte_id) if complement_status['available'] else {}
    students = [
        _normalize_cut_student(row, complement_index)
        for row in _fetch_course_cut_student_rows(normalized_corte_id)
    ]
    active_students = [student for student in students if student['estado_registro'] == 'A']
    synced_students = [student for student in students if student['continuing_education']['synced']]

    return {
        'cut': cut,
        'students': students,
        'metrics': {
            'total': len(students),
            'activos': len(active_students),
            'sincronizados': len(synced_students),
            'pendientes': max(len(active_students) - len(synced_students), 0),
        },
        'continuing_education': complement_status,
    }


def sync_course_cut_students(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_course_cut_schema()
    corte_id = _safe_int(payload.get('corte_id') or payload.get('CorteId'), default=0)
    if corte_id <= 0:
        raise CourseCutError('Debes seleccionar una corte para matricular estudiantes en educación continua.')

    if not _course_cut_complement_status()['available']:
        raise CourseCutError(
            'La base complementaria INTECEDUCONTINUA no está disponible o no tiene el módulo v5 completo.'
        )

    cut = _fetch_cut_by_id(corte_id)
    if not cut:
        raise CourseCutError('No se encontró la corte seleccionada.')

    requested_ids = _student_ids_from_payload(payload)
    rows = _fetch_course_cut_student_rows(corte_id)
    if requested_ids:
        rows = [
            row for row in rows
            if str(row.get('CorteEstudianteId') or '').strip() in requested_ids
        ]

    rows = [
        row for row in rows
        if str(row.get('EstadoRegistro') or '').strip().upper() == 'A'
    ]
    if not rows:
        raise CourseCutError('No hay estudiantes activos seleccionados para sincronizar.')

    cut_sync = configure_cut_in_complement(
        corte_id,
        cupo_maximo=cut.get('cupo_esperado') or 50,
        usuario_registro=user_login or 'SISTEMA',
    )

    results = []
    synced_count = 0
    error_count = 0
    for row in rows:
        student = _normalize_cut_student(row, {})
        try:
            sync_result = sync_student_enrollment_to_complement(
                corte_id=corte_id,
                codigo_estud=student['codigo_estud'],
                usuario_registro=user_login or 'SISTEMA',
                registrar_cargo_inicial=True,
            )
            synced = bool(sync_result.get('synced'))
            synced_count += 1 if synced else 0
            error_count += 0 if synced else 1
            results.append(
                {
                    'student': student,
                    'ok': synced,
                    'message': sync_result.get('message') or 'Sincronizado.',
                    'result': sync_result,
                }
            )
        except Exception as exc:
            error_count += 1
            results.append(
                {
                    'student': student,
                    'ok': False,
                    'message': str(exc),
                }
            )

    updated = list_course_cut_students(corte_id)
    return {
        'cut': cut,
        'cut_sync': cut_sync,
        'summary': {
            'procesados': len(rows),
            'sincronizados': synced_count,
            'errores': error_count,
        },
        'results': results,
        'updated': updated,
    }


def list_enrolled_students(corte_id: Any, *, search: str = '', limit: Any = 300) -> dict[str, Any]:
    _ensure_course_cut_schema()
    normalized_corte_id = _safe_int(corte_id, default=0)
    if normalized_corte_id <= 0:
        raise CourseCutError('Debes seleccionar una corte para consultar estudiantes matriculados.')

    cut = _fetch_cut_by_id(normalized_corte_id)
    if not cut:
        raise CourseCutError('No se encontró la corte seleccionada.')

    complement_status = _grade_transfer_complement_status(require_write=False)
    if not complement_status['available']:
        return {
            'cut': cut,
            'students': [],
            'metrics': _build_enrolled_student_metrics([]),
            'continuing_education': complement_status,
        }

    students = [
        _normalize_enrolled_student(row)
        for row in _fetch_complement_enrollment_rows(
            normalized_corte_id,
            search=search,
            limit=limit,
        )
    ]
    return {
        'cut': cut,
        'students': students,
        'metrics': _build_enrolled_student_metrics(students),
        'continuing_education': complement_status,
    }


def list_grade_transfer_students(corte_id: Any, *, search: str = '', limit: Any = 300) -> dict[str, Any]:
    result = list_enrolled_students(corte_id, search=search, limit=limit)
    result['continuing_education'] = _grade_transfer_complement_status(require_write=True)
    return result


def save_grade_transfer(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_course_cut_schema()
    corte_id = _safe_int(payload.get('corte_id') or payload.get('CorteId'), default=0)
    if corte_id <= 0:
        raise CourseCutError('Debes seleccionar una corte para realizar el pase de notas.')

    complement_status = _grade_transfer_complement_status(require_write=True)
    if not complement_status['available']:
        raise CourseCutError(
            'La base complementaria INTECEDUCONTINUA no está disponible para el pase de notas.'
        )

    cut = _fetch_cut_by_id(corte_id)
    if not cut:
        raise CourseCutError('No se encontró la corte seleccionada.')

    configure_cut_in_complement(
        corte_id,
        cupo_maximo=cut.get('cupo_esperado') or 50,
        usuario_registro=user_login or 'SISTEMA',
    )

    records = _grade_records_from_payload(payload)
    if not records:
        raise CourseCutError('Debes enviar al menos un estudiante con nota final.')

    enrollment_rows = _fetch_complement_enrollment_rows(corte_id, search='', limit=1000)
    enrollment_index = {
        str(row.get('CorteEstudianteId') or '').strip(): row
        for row in enrollment_rows
        if str(row.get('CorteEstudianteId') or '').strip()
    }
    can_evaluate = is_complement_available([('edu', 'usp_EvaluarCertificacionCorte', 'P')])

    results: list[dict[str, Any]] = []
    saved_count = 0
    passed_count = 0
    primary_synced_count = 0
    error_count = 0
    for record in records:
        corte_estudiante_id = record['corte_estudiante_id']
        enrollment = enrollment_index.get(corte_estudiante_id)
        if not enrollment:
            error_count += 1
            results.append(
                {
                    'corte_estudiante_id': corte_estudiante_id,
                    'ok': False,
                    'message': 'El estudiante no pertenece a la corte seleccionada.',
                }
            )
            continue

        try:
            nota_final = _grade_from_record(record, enrollment)
            _register_complement_grade(
                corte_estudiante_id=corte_estudiante_id,
                nota_final=nota_final,
                usuario_registro=user_login or 'SISTEMA',
                observacion=record.get('observacion') or 'Pase de notas desde dashboard administrativo.',
            )
            saved_count += 1
            local_pass = _pass_grade_in_complement(
                corte_id=corte_id,
                usuario_registro=user_login or 'SISTEMA',
            )
            try:
                primary_sync = _sync_grade_to_primary_optional(
                    enrollment,
                    nota_final=nota_final,
                    usuario_registro=user_login or 'SISTEMA',
                )
            except Exception as sync_exc:
                primary_sync = {
                    'synced': False,
                    'updated_rows': 0,
                    'message': str(sync_exc),
                }
            if can_evaluate:
                _evaluate_complement_certification(corte_estudiante_id)
            local_synced = bool(local_pass.get('synced'))
            primary_synced = bool(primary_sync.get('synced'))
            passed_count += 1 if local_synced else 0
            primary_synced_count += 1 if primary_synced else 0
            error_count += 0 if primary_synced else 1
            results.append(
                {
                    'corte_estudiante_id': corte_estudiante_id,
                    'codigo_estud': str(enrollment.get('CodigoEstud') or '').strip(),
                    'nombre': str(enrollment.get('ApellidosNombre') or '').strip(),
                    'nota_final': _decimal_to_number(nota_final),
                    'ok': local_synced and primary_synced,
                    'message': (
                        'Nota sincronizada en INTECEDUCONTINUA e INTECBDD.'
                        if local_synced and primary_synced
                        else primary_sync.get('message') or local_pass.get('message') or 'Nota procesada parcialmente.'
                    ),
                    'complement_sync': local_pass,
                    'primary_sync': primary_sync,
                }
            )
            student_login = _clean_text(enrollment.get('UsuarioLogin')) or _clean_text(enrollment.get('CorreoIntec')) or _clean_text(enrollment.get('CorreoPersonal'))
            if student_login:
                course_name = _clean_text(enrollment.get('NombreCurso')) or _clean_text(cut.get('nombre_curso')) or 'Educación Continua'
                create_notification_safely(
                    event_key=f'grade-saved:{corte_estudiante_id}:{nota_final}:student',
                    notification_type='GRADE_REGISTERED',
                    title='Nueva nota registrada',
                    message=f'Se registró tu nota final de {nota_final} en {course_name}.',
                    recipient_category='student',
                    recipient_login=student_login,
                    route='#student-grades',
                    data={
                        'corte_id': corte_id,
                        'corte_estudiante_id': corte_estudiante_id,
                        'course_name': course_name,
                        'grade': str(nota_final),
                    },
                )
        except Exception as exc:
            error_count += 1
            results.append(
                {
                    'corte_estudiante_id': corte_estudiante_id,
                    'ok': False,
                    'message': str(exc),
                }
            )

    updated = list_grade_transfer_students(corte_id, search=payload.get('q') or payload.get('search') or '')
    return {
        'cut': cut,
        'summary': {
            'procesados': len(records),
            'notas_guardadas': saved_count,
            'notas_pasadas': passed_count,
            'sincronizadas_intecbdd': primary_synced_count,
            'errores': error_count,
        },
        'results': results,
        'updated': updated,
    }


def list_attendance_students(corte_id: Any, *, attendance_date: Any = None, hour: Any = None) -> dict[str, Any]:
    _ensure_course_cut_schema()
    normalized_corte_id = _safe_int(corte_id, default=0)
    if normalized_corte_id <= 0:
        raise CourseCutError('Debes seleccionar una corte para consultar asistencia.')

    cut = _fetch_cut_by_id(normalized_corte_id)
    if not cut:
        raise CourseCutError('No se encontró la corte seleccionada.')

    complement_status = _attendance_complement_status(require_write=False)
    if not complement_status['available']:
        return {
            'cut': cut,
            'session': None,
            'students': [],
            'metrics': _build_attendance_metrics([]),
            'continuing_education': complement_status,
        }

    parsed_date = _coerce_date(attendance_date) or _today_ecuador()
    parsed_hour = _coerce_time(hour, default=None) or _current_ecuador_time()
    session = _fetch_attendance_session(normalized_corte_id, parsed_date, parsed_hour)
    students = [
        _normalize_attendance_student(row)
        for row in _fetch_attendance_student_rows(
            normalized_corte_id,
            session_id=session.get('sesion_id') if session else None,
        )
    ]

    return {
        'cut': cut,
        'session': session,
        'fecha': parsed_date.isoformat(),
        'hora': parsed_hour.strftime('%H:%M'),
        'students': students,
        'metrics': _build_attendance_metrics(students),
        'continuing_education': complement_status,
    }


def save_attendance_records(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_course_cut_schema()
    corte_id = _safe_int(payload.get('corte_id') or payload.get('CorteId'), default=0)
    if corte_id <= 0:
        raise CourseCutError('Debes seleccionar una corte para guardar asistencia.')

    complement_status = _attendance_complement_status(require_write=True)
    if not complement_status['available']:
        raise CourseCutError('La base complementaria INTECEDUCONTINUA no está disponible para asistencia.')

    cut = _fetch_cut_by_id(corte_id)
    if not cut:
        raise CourseCutError('No se encontró la corte seleccionada.')

    configure_cut_in_complement(
        corte_id,
        cupo_maximo=cut.get('cupo_esperado') or 50,
        usuario_registro=user_login or 'SISTEMA',
    )

    attendance_date = _coerce_date(payload.get('fecha') or payload.get('date')) or _today_ecuador()
    hour_start = _coerce_time(payload.get('hora') or payload.get('hora_inicio') or payload.get('time'), default=None) or _current_ecuador_time()
    hour_end = _coerce_time(payload.get('hora_fin') or payload.get('time_end'), default=None)
    session = _ensure_attendance_session(
        corte_id,
        attendance_date,
        hour_start,
        hour_end=hour_end,
        usuario_registro=user_login or 'SISTEMA',
    )

    records = _attendance_records_from_payload(payload)
    if not records:
        raise CourseCutError('Debes enviar al menos un estudiante para guardar asistencia.')

    valid_ids = _fetch_valid_attendance_student_ids(corte_id)
    saved_count = 0
    error_count = 0
    results: list[dict[str, Any]] = []
    for record in records:
        estudiante_corte_id = record['corte_estudiante_id']
        if estudiante_corte_id not in valid_ids:
            error_count += 1
            results.append(
                {
                    'corte_estudiante_id': estudiante_corte_id,
                    'ok': False,
                    'message': 'El estudiante no pertenece a la corte seleccionada.',
                }
            )
            continue
        try:
            _fetch_all(
                f"""
                EXEC [{complement_database_name()}].[edu].[usp_RegistrarAsistenciaCorte]
                    @SesionId = %s,
                    @EstudianteCorteId = %s,
                    @EstadoAsistencia = %s,
                    @MinutosRetraso = %s,
                    @Justificacion = %s,
                    @UsuarioRegistro = %s
                """,
                [
                    _safe_int(session['sesion_id'], default=0),
                    _safe_int(estudiante_corte_id, default=0),
                    record['estado_asistencia'],
                    record.get('minutos_retraso'),
                    _trim_to_max(record.get('justificacion'), 500) or None,
                    _trim_to_max(user_login or 'SISTEMA', 50),
                ],
            )
            saved_count += 1
            results.append(
                {
                    'corte_estudiante_id': estudiante_corte_id,
                    'ok': True,
                    'message': 'Asistencia guardada.',
                }
            )
        except Exception as exc:
            error_count += 1
            results.append(
                {
                    'corte_estudiante_id': estudiante_corte_id,
                    'ok': False,
                    'message': str(exc),
                }
            )

    updated = list_attendance_students(corte_id, attendance_date=attendance_date, hour=hour_start)
    return {
        'cut': cut,
        'session': session,
        'summary': {
            'procesados': len(records),
            'guardados': saved_count,
            'errores': error_count,
        },
        'results': results,
        'updated': updated,
    }


def list_course_cut_schedule(corte_id: Any) -> dict[str, Any]:
    _ensure_course_cut_schema()
    normalized_corte_id = _safe_int(corte_id, default=0)
    if normalized_corte_id <= 0:
        raise CourseCutError('Debes seleccionar una corte para consultar el horario.')

    cut = _fetch_cut_by_id(normalized_corte_id)
    if not cut:
        raise CourseCutError('No se encontró la corte seleccionada.')

    source_students = [
        _normalize_cut_student(row, {})
        for row in _fetch_course_cut_student_rows(normalized_corte_id)
        if _clean_text(row.get('EstadoRegistro')).upper() == 'A'
    ]

    schedule_status = _schedule_complement_status(require_write=False)
    teams_status = _teams_complement_status(require_write=False)
    teachers = _fetch_schedule_teachers(normalized_corte_id)
    if not schedule_status['available']:
        return {
            'cut': cut,
            'schedules': [],
            'sessions': [],
            'source_students': source_students,
            'teachers': teachers,
            'team': None,
            'team_members': [],
            'graph_queue': [],
            'metrics': _build_schedule_metrics([], []),
            'continuing_education': schedule_status,
            'teams': teams_status,
        }

    schedules = [_normalize_schedule_row(row) for row in _fetch_schedule_rows(normalized_corte_id)]
    sessions = [_normalize_session_row(row) for row in _fetch_schedule_session_rows(normalized_corte_id)]
    team = _normalize_team_row(_fetch_team_corte(normalized_corte_id)) if teams_status['available'] else None
    team_members = (
        [_normalize_team_member_row(row) for row in _fetch_team_member_rows(normalized_corte_id)]
        if teams_status['available']
        else []
    )
    graph_queue = (
        [_normalize_graph_queue_row(row) for row in _fetch_graph_queue_rows(normalized_corte_id)]
        if teams_status['available']
        else []
    )
    additional_owners = _fetch_team_additional_owners(normalized_corte_id) if teams_status['available'] else []

    return {
        'cut': cut,
        'schedules': schedules,
        'sessions': sessions,
        'source_students': source_students,
        'teachers': teachers,
        'team': team,
        'team_members': team_members,
        'graph_queue': graph_queue,
        'additional_owners': additional_owners,
        'metrics': _build_schedule_metrics(schedules, sessions),
        'continuing_education': schedule_status,
        'teams': {
            **teams_status,
            'team': team,
            'members': _build_team_member_metrics(team_members),
            'queue': _build_graph_queue_metrics(graph_queue),
        },
    }


def save_course_cut_schedule(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_course_cut_schema()
    corte_id = _safe_int(payload.get('corte_id') or payload.get('CorteId'), default=0)
    if corte_id <= 0:
        raise CourseCutError('Debes seleccionar una corte para crear el horario.')

    schedule_status = _schedule_complement_status(require_write=True)
    if not schedule_status['available']:
        raise CourseCutError('La base complementaria no está disponible para crear horarios y sesiones.')

    cut = _fetch_cut_by_id(corte_id)
    if not cut:
        raise CourseCutError('No se encontró la corte seleccionada.')

    selected_teacher = _resolve_schedule_teacher(corte_id, payload)

    configure_cut_in_complement(
        corte_id,
        cupo_maximo=cut.get('cupo_esperado') or 50,
        usuario_registro=user_login or 'SISTEMA',
    )

    fechas_clase = _class_dates_from_payload(payload)
    dia_semana = _safe_int(payload.get('dia_semana') or payload.get('DiaSemana'), default=0)
    if fechas_clase:
        dia_semana = _sql_weekday(fechas_clase[0])
    if dia_semana < 1 or dia_semana > 7:
        raise CourseCutError('El día del horario debe estar entre lunes y domingo.')

    hora_inicio = _coerce_time(payload.get('hora_inicio') or payload.get('HoraInicio'), default=None)
    hora_fin = _coerce_time(payload.get('hora_fin') or payload.get('HoraFin'), default=None)
    if not hora_inicio or not hora_fin:
        raise CourseCutError('Debes ingresar hora de inicio y hora de fin.')
    if hora_inicio >= hora_fin:
        raise CourseCutError('La hora de inicio debe ser menor a la hora de fin.')

    modalidad = _normalize_schedule_modality(payload.get('modalidad') or payload.get('Modalidad'), default='EN LÍNEA')
    if modalidad not in SCHEDULE_MODALITIES:
        raise CourseCutError('La modalidad debe ser EN LÍNEA o PRESENCIAL.')

    aula = _trim_to_max(payload.get('aula') or payload.get('Aula'), 100) or None
    enlace_virtual = _trim_to_max(
        payload.get('enlace_virtual') or payload.get('web_url') or payload.get('EnlaceVirtual'),
        600,
    ) or None
    fecha_desde = (
        _coerce_date(payload.get('fecha_desde') or payload.get('FechaDesde') or payload.get('fecha_inicio'))
        or _coerce_date(cut.get('fecha_inicio_raw'))
    )
    fecha_hasta = (
        _coerce_date(payload.get('fecha_hasta') or payload.get('FechaHasta') or payload.get('fecha_fin'))
        or _coerce_date(cut.get('fecha_fin_raw'))
    )
    generar_sesiones = _truthy_value(payload.get('generar_sesiones'), default=True)

    horario_id = _safe_int(payload.get('horario_id') or payload.get('HorarioId'), default=0)
    if horario_id > 0:
        schedule = _update_course_cut_schedule(
            corte_id=corte_id,
            horario_id=horario_id,
            dia_semana=dia_semana,
            hora_inicio=hora_inicio,
            hora_fin=hora_fin,
            modalidad=modalidad,
            aula=aula,
            enlace_virtual=enlace_virtual,
            usuario_registro=user_login or 'SISTEMA',
            docente_responsable=selected_teacher,
        )
    else:
        schedule = _create_course_cut_schedule(
            corte_id=corte_id,
            dia_semana=dia_semana,
            hora_inicio=hora_inicio,
            hora_fin=hora_fin,
            modalidad=modalidad,
            aula=aula,
            enlace_virtual=enlace_virtual,
            usuario_registro=user_login or 'SISTEMA',
            docente_responsable=selected_teacher,
        )

    if modalidad == 'EN LÍNEA' or enlace_virtual:
        _set_cut_uses_teams(corte_id)

    generated_sessions: list[dict[str, Any]] = []
    if fechas_clase:
        horario_id = _safe_int((schedule or {}).get('HorarioId') or (schedule or {}).get('horario_id'), default=0)
        if horario_id <= 0:
            raise CourseCutError('No fue posible identificar el horario para crear las fechas de clase.')
        generated_sessions = _sync_calendar_schedule_sessions(
            corte_id=corte_id,
            primary_horario_id=horario_id,
            fechas_clase=fechas_clase,
            hora_inicio=hora_inicio,
            hora_fin=hora_fin,
            modalidad=modalidad,
            aula=aula,
            enlace_virtual=enlace_virtual,
            usuario_registro=user_login or 'SISTEMA',
            docente_responsable=selected_teacher,
        )
    elif generar_sesiones:
        if not fecha_desde or not fecha_hasta:
            raise CourseCutError('Debes definir fecha desde y fecha hasta para generar sesiones.')
        if fecha_desde > fecha_hasta:
            raise CourseCutError('La fecha desde no puede ser mayor a la fecha hasta.')
        generated_sessions = _generate_schedule_sessions(
            corte_id=corte_id,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            usuario_registro=user_login or 'SISTEMA',
        )
        horario_id = _safe_int((schedule or {}).get('HorarioId') or (schedule or {}).get('horario_id'), default=0)
        if horario_id > 0:
            _attach_teacher_to_schedule(
                corte_id=corte_id,
                horario_id=horario_id,
                docente_responsable=selected_teacher,
                usuario_registro=user_login or 'SISTEMA',
            )

    updated = list_course_cut_schedule(corte_id)
    return {
        'cut': cut,
        'teacher': selected_teacher,
        'schedule': _normalize_schedule_row(schedule) if schedule else None,
        'generated_sessions': [_normalize_session_row(row) for row in generated_sessions],
        'generated_count': len(generated_sessions),
        'updated': updated,
    }


def sync_course_cut_teams(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_course_cut_schema()
    corte_id = _safe_int(payload.get('corte_id') or payload.get('CorteId'), default=0)
    if corte_id <= 0:
        raise CourseCutError('Debes seleccionar una corte para matricular por Teams.')

    teams_status = _teams_complement_status(require_write=True)
    if not teams_status['available']:
        raise CourseCutError('La base complementaria no tiene instalado el módulo graph.* para Teams.')

    cut = _fetch_cut_by_id(corte_id)
    if not cut:
        raise CourseCutError('No se encontró la corte seleccionada.')

    selected_teacher = _resolve_schedule_teacher(corte_id, payload)
    selected_schedule = _resolve_schedule_for_teams(corte_id, payload)

    configure_cut_in_complement(
        corte_id,
        cupo_maximo=cut.get('cupo_esperado') or 50,
        usuario_registro=user_login or 'SISTEMA',
    )
    _set_cut_uses_teams(corte_id)

    # Teams always uses the complementary enrollment. Synchronize the active
    # students from the selected course before the membership queue is built.
    student_sync = sync_course_cut_students(
        {'corte_id': corte_id},
        user_login=user_login or 'SISTEMA',
    )

    visibility = _clean_text(
        payload.get('visibility')
        or payload.get('visibilidad')
        or os.getenv('MICROSOFT_TEAMS_DEFAULT_VISIBILITY')
        or 'Private'
    )
    if visibility not in {'Private', 'Public'}:
        visibility = 'Private'

    team = _enqueue_team_creation(
        corte_id=corte_id,
        visibility=visibility,
        usuario_registro=user_login or 'SISTEMA',
    )
    team_corte_id = _safe_int((team or {}).get('TeamCorteId'), default=0)
    requested_name = _trim_to_max(
        payload.get('team_name') or payload.get('display_name') or payload.get('nombre_equipo'),
        256,
    )
    if team_corte_id > 0 and requested_name:
        team = _update_team_display_name(
            corte_id=corte_id,
            team_corte_id=team_corte_id,
            display_name=requested_name,
        )

    additional_owners = _normalize_additional_owner_emails(
        payload.get('additional_owner_emails') or payload.get('administradores_adicionales')
    )
    if team_corte_id > 0:
        _save_team_additional_owners(
            corte_id=corte_id,
            team_corte_id=team_corte_id,
            emails=additional_owners,
            usuario_registro=user_login or 'SISTEMA',
        )

    team_id = _trim_to_max(payload.get('team_id') or payload.get('TeamId'), 100)
    group_id = _trim_to_max(payload.get('group_id') or payload.get('GroupId'), 100) or None
    web_url = _trim_to_max(
        payload.get('web_url')
        or payload.get('enlace_virtual')
        or payload.get('WebUrl')
        or selected_schedule.get('enlace_virtual'),
        1000,
    ) or None
    if team_corte_id > 0 and team_id:
        team = _confirm_team_corte(
            team_corte_id=team_corte_id,
            team_id=team_id,
            web_url=web_url,
            group_id=group_id,
        )

    if web_url:
        _apply_virtual_link_to_schedule(corte_id, web_url, horario_id=selected_schedule.get('horario_id'))

    current_team = _fetch_team_corte(corte_id) or team or {}
    effective_team_id = _trim_to_max(current_team.get('TeamId') or team_id, 100)
    members: list[dict[str, Any]] = []
    members_message = 'Team encolado. Cuando Graph confirme el TeamId, vuelve a matricular miembros.'
    if effective_team_id:
        members = _enqueue_team_members(
            corte_id=corte_id,
            usuario_registro=user_login or 'SISTEMA',
        )
        members_message = 'Matrícula de miembros en Teams encolada.'

    updated = list_course_cut_schedule(corte_id)
    return {
        'cut': cut,
        'teacher': selected_teacher,
        'schedule': selected_schedule,
        'team': _normalize_team_row(current_team),
        'team_members': [_normalize_team_member_row(row) for row in members],
        'additional_owners': _fetch_team_additional_owners(corte_id),
        'student_sync': student_sync.get('summary', {}),
        'members_message': members_message,
        'updated': updated,
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
    nombre_corte = _clean_text(payload.get('nombre_corte')) or f'Cohorte {numero_corte}'

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
    created = created or {'corte_id': str(corte_id), 'nombre_corte': nombre_corte, 'cupo_esperado': cupo_esperado}
    created['continuing_education'] = _sync_cut_to_complement(created, user_login=user_login)
    return created


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
            R.CorteId,
            R.TipoOferta,
            R.NumeroCorte,
            R.NombreCorte,
            R.FechaInicio,
            R.FechaFin,
            R.EstadoCorte,
            R.Cod_AnioBasica,
            R.Carrera,
            R.CodigoPeriodo,
            R.Periodo,
            R.CodigoMateria,
            R.MateriaPensum,
            R.CodCurso,
            R.CursoEduContinua,
            R.CupoEsperado,
            R.TotalEstudiantes,
            R.TotalInscritos,
            R.TotalCursando,
            R.TotalRetirados,
            R.TotalAprobados,
            R.TotalReprobados,
            R.TotalFinalizados,
            CC.Horas,
            CC.Observacion
        FROM dbo.VW_CORTE_RESUMEN R
        INNER JOIN dbo.CORTE_CURSO CC ON CC.CorteId = R.CorteId
        WHERE R.CorteId = %s
        """,
        [corte_id],
    )
    return _normalize_cut_summary(row) if row else None


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
            'horas': str(row.get('Horas') or '').strip(),
            'observacion': str(row.get('Observacion') or '').strip(),
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
    nombre_corte = _cohort_display_name(row.get('NombreCorte'))
    return {
        'corte_id': str(row.get('CorteId') or '').strip(),
        'tipo_oferta': str(row.get('TipoOferta') or '').strip(),
        'numero_corte': str(row.get('NumeroCorte') or '').strip(),
        'nombre_corte': nombre_corte,
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


def _cohort_display_name(value: Any) -> str:
    name = str(value or '').strip()
    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        replacement = 'cohortes' if original.lower().endswith('s') else 'cohorte'
        if original.isupper():
            return replacement.upper()
        if original[:1].isupper():
            return replacement.capitalize()
        return replacement

    return re.sub(r'\bcortes?\b', replace, name, flags=re.IGNORECASE)


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
    with connection_for_query(query, params).cursor() as cursor:
        cursor.execute(query, params)
        if cursor.description is None:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fetch_course_cut_student_rows(corte_id: int) -> list[dict[str, Any]]:
    return _fetch_all(
        """
        SELECT
            CCE.CorteEstudianteId,
            CCE.CorteId,
            CCE.CodigoEstud,
            COALESCE(
                NULLIF(LTRIM(RTRIM(CAST(CCE.CedulaEst AS varchar(50)))), ''),
                NULLIF(LTRIM(RTRIM(CAST(DE.Cedula_Est AS varchar(50)))), ''),
                NULLIF(LTRIM(RTRIM(CAST(EE.Cedula_Est AS varchar(50)))), '')
            ) AS CedulaEst,
            COALESCE(
                NULLIF(LTRIM(RTRIM(CAST(CCE.ApellidosNombre AS nvarchar(150)))), ''),
                NULLIF(LTRIM(RTRIM(CAST(DE.Apellidos_nombre AS nvarchar(150)))), ''),
                NULLIF(LTRIM(RTRIM(CAST(EE.Apellidos_nombre AS nvarchar(150)))), '')
            ) AS ApellidosNombre,
            COALESCE(
                NULLIF(LTRIM(RTRIM(CAST(DE.correo AS varchar(150)))), ''),
                NULLIF(LTRIM(RTRIM(CAST(EE.correo AS varchar(150)))), '')
            ) AS CorreoPersonal,
            CCE.Num_Matricula,
            CCE.FechaInicioEstudiante,
            CCE.EstadoParticipacion,
            CCE.EstadoRegistro,
            CCE.Cod_AnioBasica,
            CCE.CodigoPeriodo,
            CCE.CodigoMateria,
            CCE.CodCurso,
            Grupo.Paralelo,
            Grupo.CodJornada,
            Grupo.Jornada
        FROM dbo.CORTE_CURSO_ESTUDIANTE CCE
        LEFT JOIN dbo.DATOS_ESTUD DE
          ON LTRIM(RTRIM(CAST(DE.codigo_estud AS varchar(30)))) = LTRIM(RTRIM(CAST(CCE.CodigoEstud AS varchar(30))))
        LEFT JOIN dbo.EstudiantesEdContinua EE
          ON LTRIM(RTRIM(CAST(EE.codigo_estud AS varchar(30)))) = LTRIM(RTRIM(CAST(CCE.CodigoEstud AS varchar(30))))
        OUTER APPLY (
            SELECT TOP (1)
                UPPER(LTRIM(RTRIM(ISNULL(CE.paralelo, '')))) AS Paralelo,
                CAST(ISNULL(CE.NumGrupo, 1) AS varchar(30)) AS CodJornada,
                '' AS Jornada
            FROM dbo.CARRERAXESTUD CE
            WHERE LTRIM(RTRIM(CAST(CE.codigo_estud AS varchar(30)))) = LTRIM(RTRIM(CAST(CCE.CodigoEstud AS varchar(30))))
              AND LTRIM(RTRIM(CAST(CE.cod_anio_Basica AS varchar(30)))) = LTRIM(RTRIM(CAST(CCE.Cod_AnioBasica AS varchar(30))))
              AND LTRIM(RTRIM(CAST(CE.codigo_materia AS varchar(30)))) = LTRIM(RTRIM(CAST(CCE.CodigoMateria AS varchar(30))))
              AND LTRIM(RTRIM(CAST(CE.codigo_periodo AS varchar(30)))) = LTRIM(RTRIM(CAST(CCE.CodigoPeriodo AS varchar(30))))
            ORDER BY
                CASE
                    WHEN CCE.Num_Matricula IS NOT NULL
                     AND LTRIM(RTRIM(CAST(CE.Num_Matricula AS varchar(30)))) = LTRIM(RTRIM(CAST(CCE.Num_Matricula AS varchar(30))))
                    THEN 0
                    ELSE 1
                END,
                CE.Fecha_Matricula DESC
        ) Grupo
        WHERE CCE.CorteId = %s
        ORDER BY ApellidosNombre ASC, CCE.CorteEstudianteId ASC
        """,
        [corte_id],
    )


def _course_cut_complement_status() -> dict[str, Any]:
    version = complement_version()
    required = [
        ('edu', 'CorteEstudiante', 'U'),
        ('edu', 'usp_MatricularEstudianteCorte', 'P'),
        ('edu', 'usp_ConfigurarCorteDesdePrincipal', 'P'),
        ('edu', 'VW_MatriculaEstudianteCompleta', 'V'),
    ] if version == 'v5' else [
        ('edu', 'EstudianteCorteControl', 'U'),
        ('edu', 'usp_MatricularEstudiantePrincipal', 'P'),
        ('edu', 'usp_ConfigurarCorteCurso', 'P'),
    ]
    available = bool(version) and is_complement_available(required)
    return {
        'available': available,
        'database': complement_database_name(),
        'version': version,
        'message': (
            'Base complementaria lista para recibir matrículas.'
            if available
            else 'Ejecuta el módulo v5 de INTECEDUCONTINUA antes de sincronizar.'
        ),
    }


def _grade_transfer_complement_status(*, require_write: bool) -> dict[str, Any]:
    version = complement_version()
    if version == 'v5':
        required = [
            ('edu', 'VW_MatriculaEstudianteCompleta', 'V'),
            ('edu', 'VW_AsistenciaResumen', 'V'),
            ('edu', 'CalificacionCorte', 'U'),
            ('edu', 'PaseNotaControl', 'U'),
        ]
        if require_write:
            required.extend(
                [
                    ('edu', 'usp_RegistrarNotaFinalCorte', 'P'),
                    ('edu', 'usp_PasarNotasCorte', 'P'),
                ]
            )
    else:
        required = [
            ('edu', 'VW_MatriculasPrincipal', 'V'),
            ('edu', 'VW_AsistenciaResumen', 'V'),
            ('edu', 'CalificacionCorte', 'U'),
        ]
        if require_write:
            required.append(('edu', 'usp_RegistrarNotaFinalCorte', 'P'))

    available = bool(version) and is_complement_available(required)
    return {
        'available': available,
        'database': complement_database_name(),
        'version': version,
        'message': (
            'Base complementaria lista para consultar matrículas y procesar notas.'
            if available
            else 'Ejecuta el módulo v5 de INTECEDUCONTINUA antes de consultar o pasar notas.'
        ),
    }


def _attendance_complement_status(*, require_write: bool) -> dict[str, Any]:
    version = complement_version()
    required = [
        ('edu', 'VW_MatriculaEstudianteCompleta', 'V'),
        ('edu', 'SesionCorte', 'U'),
        ('edu', 'AsistenciaCorte', 'U'),
        ('edu', 'VW_AsistenciaResumen', 'V'),
    ]
    if require_write:
        required.append(('edu', 'usp_RegistrarAsistenciaCorte', 'P'))

    available = version == 'v5' and is_complement_available(required)
    return {
        'available': available,
        'database': complement_database_name(),
        'version': version,
        'message': (
            'Base complementaria lista para asistencia.'
            if available
            else 'Ejecuta el módulo v5 de INTECEDUCONTINUA antes de registrar asistencia.'
        ),
    }


def _schedule_complement_status(*, require_write: bool) -> dict[str, Any]:
    version = complement_version()
    required = [
        ('edu', 'VW_CorteCursoDetalle', 'V'),
        ('edu', 'HorarioCorte', 'U'),
        ('edu', 'SesionCorte', 'U'),
    ]
    if require_write:
        required.append(('edu', 'usp_CrearHorarioCorte', 'P'))

    available = version == 'v5' and is_complement_available(required)
    if require_write and available and not _schedule_session_procedure_name():
        available = False
    return {
        'available': available,
        'database': complement_database_name(),
        'version': version,
        'message': (
            'Base complementaria lista para horarios y sesiones.'
            if available
            else 'Ejecuta el módulo v5 de INTECEDUCONTINUA antes de crear horarios.'
        ),
    }


def _teams_complement_status(*, require_write: bool) -> dict[str, Any]:
    version = complement_version()
    required = [
        ('graph', 'TeamCorte', 'U'),
        ('graph', 'TeamMiembroCorte', 'U'),
        ('graph', 'OperacionQueue', 'U'),
        ('graph', 'VW_OperacionPendiente', 'V'),
    ]
    if require_write:
        required.extend(
            [
                ('graph', 'usp_EncolarCrearTeamCorte', 'P'),
                ('graph', 'usp_ConfirmarTeamCorte', 'P'),
                ('graph', 'usp_EncolarMatriculaTeamsCorte', 'P'),
            ]
        )

    available = version == 'v5' and is_complement_available(required)
    return {
        'available': available,
        'database': complement_database_name(),
        'version': version,
        'message': (
            'Módulo graph.* listo para Teams.'
            if available
            else 'Ejecuta el módulo v5 con el esquema graph.* antes de matricular por Teams.'
        ),
    }


def _schedule_teacher_view_available() -> bool:
    return complement_version() == 'v5' and is_complement_available(
        [('edu', 'VW_MatriculaDocenteCompleta', 'V')]
    )


def _schedule_session_procedure_name() -> str:
    for procedure_name in ('usp_GenerarSesionesDesdeHorario', 'usp_GenerarSesionesCorte'):
        if _complement_object_exists('edu', procedure_name, 'P'):
            return procedure_name
    return ''


def _complement_object_exists(schema_name: str, object_name: str, object_type: str) -> bool:
    row = _fetch_one(
        "SELECT OBJECT_ID(%s, %s) AS [ObjectId]",
        [f'[{complement_database_name()}].[{schema_name}].[{object_name}]', object_type],
    )
    return bool(row and row.get('ObjectId') is not None)


def _complement_table_column_exists(schema_name: str, table_name: str, column_name: str) -> bool:
    row = _fetch_one(
        f"""
        SELECT TOP (1) 1 AS [ColumnExists]
        FROM [{complement_database_name()}].sys.columns C
        INNER JOIN [{complement_database_name()}].sys.objects O
          ON O.object_id = C.object_id
        INNER JOIN [{complement_database_name()}].sys.schemas S
          ON S.schema_id = O.schema_id
        WHERE S.name = %s
          AND O.name = %s
          AND C.name = %s
        """,
        [schema_name, table_name, column_name],
    )
    return bool(row and row.get('ColumnExists'))


def _normalize_schedule_modality(value: Any, *, default: str = '') -> str:
    raw_value = _clean_text(value).upper().replace('Í', 'I')
    if not raw_value:
        return default
    if raw_value in SCHEDULE_ONLINE_MODALITIES:
        return 'EN LÍNEA'
    if raw_value == 'PRESENCIAL':
        return 'PRESENCIAL'
    return raw_value


def _schedule_teacher_observation(docente_responsable: dict[str, Any] | None) -> str | None:
    teacher = docente_responsable or {}
    nombre = _clean_text(teacher.get('nombre'))
    docente_corte_id = _clean_text(teacher.get('docente_corte_id'))
    codigo_docente = _clean_text(teacher.get('codigo_docente'))
    cedula = _clean_text(teacher.get('cedula'))
    correo = _clean_text(teacher.get('correo_intec') or teacher.get('correo_personal'))
    if not any([nombre, docente_corte_id, codigo_docente, cedula, correo]):
        return None

    parts = [f'Docente responsable: {nombre or "Sin nombre"}']
    if codigo_docente:
        parts.append(f'CodigoDocente: {codigo_docente}')
    if docente_corte_id:
        parts.append(f'DocenteCorteId: {docente_corte_id}')
    if cedula:
        parts.append(f'Cedula: {cedula}')
    if correo:
        parts.append(f'Correo: {correo}')
    return _trim_to_max(' | '.join(parts), 500)


def _parse_schedule_teacher_observation(observacion: Any) -> dict[str, str]:
    text = _clean_text(observacion)
    if not text or 'Docente responsable:' not in text:
        return {}

    result: dict[str, str] = {}
    for index, raw_part in enumerate(text.split('|')):
        part = raw_part.strip()
        if not part or ':' not in part:
            continue
        key, value = [item.strip() for item in part.split(':', 1)]
        if not value:
            continue
        normalized_key = key.lower()
        if index == 0 and normalized_key == 'docente responsable':
            result['nombre'] = value
        elif normalized_key == 'codigodocente':
            result['codigo_docente'] = value
        elif normalized_key == 'docentecorteid':
            result['docente_corte_id'] = value
        elif normalized_key == 'cedula':
            result['cedula'] = value
        elif normalized_key == 'correo':
            result['correo'] = value
    return result


def _attach_teacher_to_schedule(
    *,
    corte_id: int,
    horario_id: int,
    docente_responsable: dict[str, Any] | None,
    usuario_registro: str,
) -> None:
    if horario_id <= 0:
        return

    observation = _schedule_teacher_observation(docente_responsable)
    if not observation:
        return

    docente_corte_id = _safe_int((docente_responsable or {}).get('docente_corte_id'), default=0) or None
    codigo_docente = _clean_text((docente_responsable or {}).get('codigo_docente')) or None
    clean_user = _trim_to_max(usuario_registro, 50) or 'SISTEMA'

    def update_table(table_name: str, where_sql: str, where_params: list[Any]) -> None:
        set_clauses = ['[Observacion] = %s', '[UsuarioRegistro] = %s']
        params: list[Any] = [observation, clean_user]
        if _complement_table_column_exists('edu', table_name, 'DocenteCorteId'):
            set_clauses.append('[DocenteCorteId] = %s')
            params.append(docente_corte_id)
        if _complement_table_column_exists('edu', table_name, 'CodigoDocente'):
            set_clauses.append('[CodigoDocente] = %s')
            params.append(codigo_docente)

        _fetch_all(
            f"""
            UPDATE [{complement_database_name()}].[edu].[{table_name}]
            SET {', '.join(set_clauses)}
            WHERE {where_sql}
            """,
            params + where_params,
        )

    update_table('HorarioCorte', '[HorarioId] = %s AND [CorteId] = %s', [horario_id, corte_id])
    update_table(
        'SesionCorte',
        "[HorarioId] = %s AND [CorteId] = %s AND ([EstadoSesion] COLLATE DATABASE_DEFAULT) <> 'CANCELADA'",
        [horario_id, corte_id],
    )


def _fetch_schedule_rows(corte_id: int) -> list[dict[str, Any]]:
    return _fetch_all(
        f"""
        SELECT
            H.[HorarioId],
            H.[CorteId],
            H.[DiaSemana],
            CONVERT(varchar(5), H.[HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), H.[HoraFin], 108) AS [HoraFin],
            H.[Modalidad],
            H.[Aula],
            H.[EnlaceVirtual],
            H.[EstadoHorario],
            H.[UsuarioRegistro],
            H.[FechaRegistro],
            H.[Observacion],
            COUNT(CASE WHEN S.[EstadoSesion] <> 'CANCELADA' THEN 1 END) AS [TotalSesiones],
            SUM(CASE WHEN S.[EstadoSesion] = 'PROGRAMADA' THEN 1 ELSE 0 END) AS [SesionesProgramadas],
            SUM(CASE WHEN S.[EstadoSesion] = 'REALIZADA' THEN 1 ELSE 0 END) AS [SesionesRealizadas],
            MIN(CASE WHEN S.[EstadoSesion] <> 'CANCELADA' THEN S.[FechaClase] END) AS [PrimeraSesion],
            MAX(CASE WHEN S.[EstadoSesion] <> 'CANCELADA' THEN S.[FechaClase] END) AS [UltimaSesion]
        FROM [{complement_database_name()}].[edu].[HorarioCorte] H
        LEFT JOIN [{complement_database_name()}].[edu].[SesionCorte] S
          ON S.[HorarioId] = H.[HorarioId]
        WHERE H.[CorteId] = %s
        GROUP BY
            H.[HorarioId],
            H.[CorteId],
            H.[DiaSemana],
            H.[HoraInicio],
            H.[HoraFin],
            H.[Modalidad],
            H.[Aula],
            H.[EnlaceVirtual],
            H.[EstadoHorario],
            H.[UsuarioRegistro],
            H.[FechaRegistro],
            H.[Observacion]
        ORDER BY H.[DiaSemana], H.[HoraInicio], H.[HorarioId]
        """,
        [corte_id],
    )


def _fetch_schedule_session_rows(corte_id: int, *, limit: Any = 200) -> list[dict[str, Any]]:
    safe_limit = max(1, min(_safe_int(limit, default=200), 500))
    return _fetch_all(
        f"""
        SELECT TOP ({safe_limit})
            S.[SesionId],
            S.[CorteId],
            S.[HorarioId],
            S.[FechaClase],
            CONVERT(varchar(5), S.[HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), S.[HoraFin], 108) AS [HoraFin],
            S.[TemaClase],
            S.[Modalidad],
            S.[Aula],
            S.[EnlaceVirtual],
            S.[EstadoSesion]
        FROM [{complement_database_name()}].[edu].[SesionCorte] S
        WHERE S.[CorteId] = %s
          AND S.[EstadoSesion] <> 'CANCELADA'
        ORDER BY S.[FechaClase], S.[HoraInicio], S.[SesionId]
        """,
        [corte_id],
    )


def _fetch_schedule_teacher_rows(corte_id: int) -> list[dict[str, Any]]:
    if not _schedule_teacher_view_available():
        return []
    return _fetch_all(
        f"""
        SELECT
            [DocenteCorteId],
            [CorteId],
            [CodigoDocente],
            [RolDocente],
            [EstadoDocenteCorte],
            [FechaMatricula],
            [CedulaDoc],
            [ApellidosNombre],
            [CorreoPersonal],
            [CorreoIntec],
            [UsuarioLogin],
            [UsuarioSisLogin]
        FROM [{complement_database_name()}].[edu].[VW_MatriculaDocenteCompleta]
        WHERE [CorteId] = %s
          AND ([EstadoDocenteCorte] COLLATE DATABASE_DEFAULT) NOT IN ('ANULADO', 'RETIRADO', 'INACTIVO')
        ORDER BY
            CASE WHEN ([RolDocente] COLLATE DATABASE_DEFAULT) = 'TITULAR' THEN 0 ELSE 1 END,
            [DocenteCorteId] ASC
        """,
        [corte_id],
    )


def _fetch_schedule_teachers(corte_id: int) -> list[dict[str, Any]]:
    return [_normalize_schedule_teacher_row(row) for row in _fetch_schedule_teacher_rows(corte_id)]


def _resolve_schedule_teacher(corte_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if not _schedule_teacher_view_available():
        return {}

    teachers = _fetch_schedule_teachers(corte_id)
    if not teachers:
        raise CourseCutError(
            'La corte seleccionada no tiene docente matriculado. Primero matrícula el docente correspondiente.'
        )

    docente_corte_id = _clean_text(
        payload.get('docente_corte_id')
        or payload.get('DocenteCorteId')
        or payload.get('teacher_assignment_id')
    )
    codigo_docente = _clean_text(
        payload.get('codigo_docente')
        or payload.get('CodigoDocente')
        or payload.get('codigo_doc')
        or payload.get('teacher_id')
    )

    if not docente_corte_id and not codigo_docente:
        if len(teachers) == 1:
            return teachers[0]
        raise CourseCutError('Selecciona el docente correspondiente antes de guardar el horario.')

    for teacher in teachers:
        if docente_corte_id and _clean_text(teacher.get('docente_corte_id')) == docente_corte_id:
            return teacher
        if codigo_docente and _clean_text(teacher.get('codigo_docente')) == codigo_docente:
            return teacher

    raise CourseCutError('El docente seleccionado no está matriculado en la corte elegida.')


def _resolve_schedule_for_teams(corte_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    schedule_status = _schedule_complement_status(require_write=False)
    if not schedule_status['available']:
        raise CourseCutError('La base complementaria no está disponible para validar el horario de Teams.')

    schedules = [_normalize_schedule_row(row) for row in _fetch_schedule_rows(corte_id)]
    if not schedules:
        raise CourseCutError('Primero crea y guarda el horario antes de matricular por Teams.')

    horario_id = _clean_text(
        payload.get('horario_id')
        or payload.get('HorarioId')
        or payload.get('teams_horario_id')
    )
    if not horario_id and len(schedules) == 1:
        selected_schedule = schedules[0]
    elif horario_id:
        selected_schedule = next(
            (schedule for schedule in schedules if _clean_text(schedule.get('horario_id')) == horario_id),
            None,
        )
    else:
        raise CourseCutError('Selecciona el horario base para matricular por Teams.')

    if not selected_schedule:
        raise CourseCutError('El horario seleccionado no pertenece a la corte elegida.')

    if _safe_int(selected_schedule.get('total_sesiones'), default=0) <= 0:
        raise CourseCutError('El horario seleccionado no tiene sesiones programadas para Teams.')

    return selected_schedule


def _create_course_cut_schedule(
    *,
    corte_id: int,
    dia_semana: int,
    hora_inicio: time,
    hora_fin: time,
    modalidad: str,
    aula: str | None,
    enlace_virtual: str | None,
    usuario_registro: str,
    docente_responsable: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    rows = _fetch_all(
        f"""
        EXEC [{complement_database_name()}].[edu].[usp_CrearHorarioCorte]
            @CorteId = %s,
            @DiaSemana = %s,
            @HoraInicio = %s,
            @HoraFin = %s,
            @Modalidad = %s,
            @Aula = %s,
            @EnlaceVirtual = %s,
            @UsuarioRegistro = %s
        """,
        [
            corte_id,
            dia_semana,
            hora_inicio,
            hora_fin,
            modalidad,
            aula,
            enlace_virtual,
            _trim_to_max(usuario_registro, 50),
        ],
    )
    schedule = rows[0] if rows else None
    horario_id = _safe_int((schedule or {}).get('HorarioId'), default=0)
    if horario_id > 0:
        _attach_teacher_to_schedule(
            corte_id=corte_id,
            horario_id=horario_id,
            docente_responsable=docente_responsable,
            usuario_registro=usuario_registro,
        )
    return schedule


def _update_course_cut_schedule(
    *,
    corte_id: int,
    horario_id: int,
    dia_semana: int,
    hora_inicio: time,
    hora_fin: time,
    modalidad: str,
    aula: str | None,
    enlace_virtual: str | None,
    usuario_registro: str,
    docente_responsable: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    docente_observacion = _schedule_teacher_observation(docente_responsable)
    row = _fetch_one(
        f"""
        UPDATE [{complement_database_name()}].[edu].[HorarioCorte]
        SET [DiaSemana] = %s,
            [HoraInicio] = %s,
            [HoraFin] = %s,
            [Modalidad] = %s,
            [Aula] = %s,
            [EnlaceVirtual] = %s,
            [UsuarioRegistro] = %s,
            [Observacion] = COALESCE(%s, [Observacion])
        OUTPUT
            INSERTED.[HorarioId],
            INSERTED.[CorteId],
            INSERTED.[DiaSemana],
            CONVERT(varchar(5), INSERTED.[HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), INSERTED.[HoraFin], 108) AS [HoraFin],
            INSERTED.[Modalidad],
            INSERTED.[Aula],
            INSERTED.[EnlaceVirtual],
            INSERTED.[EstadoHorario],
            INSERTED.[UsuarioRegistro],
            INSERTED.[FechaRegistro],
            INSERTED.[Observacion]
        WHERE [HorarioId] = %s
          AND [CorteId] = %s
        """,
        [
            dia_semana,
            hora_inicio,
            hora_fin,
            modalidad,
            aula,
            enlace_virtual,
            _trim_to_max(usuario_registro, 50),
            docente_observacion,
            horario_id,
            corte_id,
        ],
    )
    if not row:
        raise CourseCutError('No se encontró el horario seleccionado para actualizar.')

    _fetch_all(
        f"""
        UPDATE [{complement_database_name()}].[edu].[SesionCorte]
        SET [HoraInicio] = %s,
            [HoraFin] = %s,
            [Modalidad] = %s,
            [Aula] = %s,
            [EnlaceVirtual] = %s,
            [UsuarioRegistro] = %s,
            [Observacion] = COALESCE(%s, [Observacion])
        WHERE [HorarioId] = %s
          AND [CorteId] = %s
          AND [EstadoSesion] = 'PROGRAMADA'
        """,
        [
            hora_inicio,
            hora_fin,
            modalidad,
            aula,
            enlace_virtual,
            _trim_to_max(usuario_registro, 50),
            docente_observacion,
            horario_id,
            corte_id,
        ],
    )
    _attach_teacher_to_schedule(
        corte_id=corte_id,
        horario_id=horario_id,
        docente_responsable=docente_responsable,
        usuario_registro=usuario_registro,
    )
    return row


def _generate_schedule_sessions(
    *,
    corte_id: int,
    fecha_desde: date,
    fecha_hasta: date,
    usuario_registro: str,
) -> list[dict[str, Any]]:
    procedure_name = _schedule_session_procedure_name()
    if not procedure_name:
        raise CourseCutError('No existe el procedimiento para generar sesiones desde horario.')

    return _fetch_all(
        f"""
        EXEC [{complement_database_name()}].[edu].[{procedure_name}]
            @CorteId = %s,
            @FechaDesde = %s,
            @FechaHasta = %s,
            @UsuarioRegistro = %s
        """,
        [
            corte_id,
            fecha_desde,
            fecha_hasta,
            _trim_to_max(usuario_registro, 50),
        ],
    )


def _sync_calendar_schedule_sessions(
    *,
    corte_id: int,
    primary_horario_id: int,
    fechas_clase: list[date],
    hora_inicio: time,
    hora_fin: time,
    modalidad: str,
    aula: str | None,
    enlace_virtual: str | None,
    usuario_registro: str,
    docente_responsable: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    grouped_dates: dict[int, list[date]] = {}
    for fecha_clase in fechas_clase:
        grouped_dates.setdefault(_sql_weekday(fecha_clase), []).append(fecha_clase)

    generated: list[dict[str, Any]] = []
    primary_used = False
    for dia_semana, dates_for_day in grouped_dates.items():
        if not primary_used:
            horario_id = primary_horario_id
            _update_course_cut_schedule(
                corte_id=corte_id,
                horario_id=horario_id,
                dia_semana=dia_semana,
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
                modalidad=modalidad,
                aula=aula,
                enlace_virtual=enlace_virtual,
                usuario_registro=usuario_registro,
                docente_responsable=docente_responsable,
            )
            primary_used = True
        else:
            schedule = (
                _fetch_matching_course_cut_schedule(
                    corte_id=corte_id,
                    dia_semana=dia_semana,
                    hora_inicio=hora_inicio,
                    hora_fin=hora_fin,
                )
                or _create_course_cut_schedule(
                    corte_id=corte_id,
                    dia_semana=dia_semana,
                    hora_inicio=hora_inicio,
                    hora_fin=hora_fin,
                    modalidad=modalidad,
                    aula=aula,
                    enlace_virtual=enlace_virtual,
                    usuario_registro=usuario_registro,
                    docente_responsable=docente_responsable,
                )
            )
            horario_id = _safe_int((schedule or {}).get('HorarioId'), default=0)
            if horario_id <= 0:
                raise CourseCutError('No fue posible crear el horario para las fechas seleccionadas.')
            _update_course_cut_schedule(
                corte_id=corte_id,
                horario_id=horario_id,
                dia_semana=dia_semana,
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
                modalidad=modalidad,
                aula=aula,
                enlace_virtual=enlace_virtual,
                usuario_registro=usuario_registro,
                docente_responsable=docente_responsable,
            )

        generated.extend(
            _sync_schedule_sessions_for_dates(
                corte_id=corte_id,
                horario_id=horario_id,
                fechas_clase=dates_for_day,
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
                modalidad=modalidad,
                aula=aula,
                enlace_virtual=enlace_virtual,
                usuario_registro=usuario_registro,
            )
        )
        _attach_teacher_to_schedule(
            corte_id=corte_id,
            horario_id=horario_id,
            docente_responsable=docente_responsable,
            usuario_registro=usuario_registro,
        )

    return generated


def _fetch_matching_course_cut_schedule(
    *,
    corte_id: int,
    dia_semana: int,
    hora_inicio: time,
    hora_fin: time,
) -> dict[str, Any] | None:
    return _fetch_one(
        f"""
        SELECT TOP (1)
            [HorarioId],
            [CorteId],
            [DiaSemana],
            CONVERT(varchar(5), [HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), [HoraFin], 108) AS [HoraFin],
            [Modalidad],
            [Aula],
            [EnlaceVirtual],
            [EstadoHorario],
            [UsuarioRegistro],
            [FechaRegistro],
            [Observacion]
        FROM [{complement_database_name()}].[edu].[HorarioCorte]
        WHERE [CorteId] = %s
          AND [DiaSemana] = %s
          AND [HoraInicio] = %s
          AND [HoraFin] = %s
          AND ([EstadoHorario] COLLATE DATABASE_DEFAULT) = 'ACTIVO'
        ORDER BY [HorarioId] ASC
        """,
        [corte_id, dia_semana, hora_inicio, hora_fin],
    )


def _sync_schedule_sessions_for_dates(
    *,
    corte_id: int,
    horario_id: int,
    fechas_clase: list[date],
    hora_inicio: time,
    hora_fin: time,
    modalidad: str,
    aula: str | None,
    enlace_virtual: str | None,
    usuario_registro: str,
) -> list[dict[str, Any]]:
    selected = set(fechas_clase)
    existing_rows = _fetch_schedule_sessions_for_horario(corte_id, horario_id)
    existing_by_date = {
        parsed_date: row
        for row in existing_rows
        if (parsed_date := _coerce_date(row.get('FechaClase')))
    }

    synced: list[dict[str, Any]] = []
    for fecha_clase in fechas_clase:
        existing = existing_by_date.get(fecha_clase)
        if existing:
            synced.append(
                _update_schedule_session(
                    sesion_id=_safe_int(existing.get('SesionId'), default=0),
                    corte_id=corte_id,
                    horario_id=horario_id,
                    fecha_clase=fecha_clase,
                    hora_inicio=hora_inicio,
                    hora_fin=hora_fin,
                    modalidad=modalidad,
                    aula=aula,
                    enlace_virtual=enlace_virtual,
                    usuario_registro=usuario_registro,
                )
            )
        else:
            synced.append(
                _insert_schedule_session(
                    corte_id=corte_id,
                    horario_id=horario_id,
                    fecha_clase=fecha_clase,
                    hora_inicio=hora_inicio,
                    hora_fin=hora_fin,
                    modalidad=modalidad,
                    aula=aula,
                    enlace_virtual=enlace_virtual,
                    usuario_registro=usuario_registro,
                )
            )

    for existing in existing_rows:
        fecha_existente = _coerce_date(existing.get('FechaClase'))
        if not fecha_existente or fecha_existente in selected:
            continue
        if _clean_text(existing.get('EstadoSesion')).upper() != 'PROGRAMADA':
            continue
        _cancel_schedule_session(
            sesion_id=_safe_int(existing.get('SesionId'), default=0),
            corte_id=corte_id,
            horario_id=horario_id,
            usuario_registro=usuario_registro,
        )

    return synced


def _fetch_schedule_sessions_for_horario(corte_id: int, horario_id: int) -> list[dict[str, Any]]:
    return _fetch_all(
        f"""
        SELECT
            [SesionId],
            [CorteId],
            [HorarioId],
            [FechaClase],
            CONVERT(varchar(5), [HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), [HoraFin], 108) AS [HoraFin],
            [TemaClase],
            [Modalidad],
            [Aula],
            [EnlaceVirtual],
            [EstadoSesion]
        FROM [{complement_database_name()}].[edu].[SesionCorte]
        WHERE [CorteId] = %s
          AND [HorarioId] = %s
          AND ([EstadoSesion] COLLATE DATABASE_DEFAULT) <> 'CANCELADA'
        ORDER BY [FechaClase], [HoraInicio], [SesionId]
        """,
        [corte_id, horario_id],
    )


def _insert_schedule_session(
    *,
    corte_id: int,
    horario_id: int,
    fecha_clase: date,
    hora_inicio: time,
    hora_fin: time,
    modalidad: str,
    aula: str | None,
    enlace_virtual: str | None,
    usuario_registro: str,
) -> dict[str, Any]:
    row = _fetch_one(
        f"""
        INSERT INTO [{complement_database_name()}].[edu].[SesionCorte]
            ([CorteId], [HorarioId], [FechaClase], [HoraInicio], [HoraFin], [TemaClase], [Modalidad], [Aula], [EnlaceVirtual], [EstadoSesion], [UsuarioRegistro], [Observacion])
        OUTPUT
            INSERTED.[SesionId],
            INSERTED.[CorteId],
            INSERTED.[HorarioId],
            INSERTED.[FechaClase],
            CONVERT(varchar(5), INSERTED.[HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), INSERTED.[HoraFin], 108) AS [HoraFin],
            INSERTED.[TemaClase],
            INSERTED.[Modalidad],
            INSERTED.[Aula],
            INSERTED.[EnlaceVirtual],
            INSERTED.[EstadoSesion]
        VALUES
            (%s, %s, %s, %s, %s, NULL, %s, %s, %s, 'PROGRAMADA', %s, N'Sesión creada desde calendario administrativo.')
        """,
        [
            corte_id,
            horario_id,
            fecha_clase,
            hora_inicio,
            hora_fin,
            modalidad,
            aula,
            enlace_virtual,
            _trim_to_max(usuario_registro, 50),
        ],
    )
    if not row:
        raise CourseCutError('No fue posible crear una sesión del calendario.')
    return row


def _update_schedule_session(
    *,
    sesion_id: int,
    corte_id: int,
    horario_id: int,
    fecha_clase: date,
    hora_inicio: time,
    hora_fin: time,
    modalidad: str,
    aula: str | None,
    enlace_virtual: str | None,
    usuario_registro: str,
) -> dict[str, Any]:
    row = _fetch_one(
        f"""
        UPDATE [{complement_database_name()}].[edu].[SesionCorte]
        SET [FechaClase] = %s,
            [HoraInicio] = %s,
            [HoraFin] = %s,
            [Modalidad] = %s,
            [Aula] = %s,
            [EnlaceVirtual] = %s,
            [EstadoSesion] = 'PROGRAMADA',
            [UsuarioRegistro] = %s
        OUTPUT
            INSERTED.[SesionId],
            INSERTED.[CorteId],
            INSERTED.[HorarioId],
            INSERTED.[FechaClase],
            CONVERT(varchar(5), INSERTED.[HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), INSERTED.[HoraFin], 108) AS [HoraFin],
            INSERTED.[TemaClase],
            INSERTED.[Modalidad],
            INSERTED.[Aula],
            INSERTED.[EnlaceVirtual],
            INSERTED.[EstadoSesion]
        WHERE [SesionId] = %s
          AND [CorteId] = %s
          AND [HorarioId] = %s
        """,
        [
            fecha_clase,
            hora_inicio,
            hora_fin,
            modalidad,
            aula,
            enlace_virtual,
            _trim_to_max(usuario_registro, 50),
            sesion_id,
            corte_id,
            horario_id,
        ],
    )
    if not row:
        raise CourseCutError('No fue posible actualizar una sesión del calendario.')
    return row


def _cancel_schedule_session(*, sesion_id: int, corte_id: int, horario_id: int, usuario_registro: str) -> None:
    if sesion_id <= 0:
        return
    _fetch_all(
        f"""
        UPDATE [{complement_database_name()}].[edu].[SesionCorte]
        SET [EstadoSesion] = 'CANCELADA',
            [UsuarioRegistro] = %s
        WHERE [SesionId] = %s
          AND [CorteId] = %s
          AND [HorarioId] = %s
          AND ([EstadoSesion] COLLATE DATABASE_DEFAULT) = 'PROGRAMADA'
        """,
        [_trim_to_max(usuario_registro, 50), sesion_id, corte_id, horario_id],
    )


def _set_cut_uses_teams(corte_id: int) -> None:
    _fetch_all(
        f"""
        UPDATE [{complement_database_name()}].[edu].[CorteCurso]
        SET [UsaTeams] = 1
        WHERE [CorteId] = %s
        """,
        [corte_id],
    )


def _apply_virtual_link_to_schedule(corte_id: int, web_url: str, *, horario_id: Any = None) -> None:
    clean_url = _trim_to_max(web_url, 600)
    if not clean_url:
        return
    normalized_horario_id = _safe_int(horario_id, default=0)
    schedule_filter = 'AND [HorarioId] = %s' if normalized_horario_id > 0 else ''
    params: list[Any] = [clean_url, corte_id]
    if normalized_horario_id > 0:
        params.append(normalized_horario_id)
    _fetch_all(
        f"""
        UPDATE [{complement_database_name()}].[edu].[HorarioCorte]
        SET [EnlaceVirtual] = %s
        WHERE [CorteId] = %s
          AND [EstadoHorario] = 'ACTIVO'
          {schedule_filter}
        """,
        params,
    )
    params = [clean_url, corte_id]
    if normalized_horario_id > 0:
        params.append(normalized_horario_id)
    _fetch_all(
        f"""
        UPDATE [{complement_database_name()}].[edu].[SesionCorte]
        SET [EnlaceVirtual] = %s
        WHERE [CorteId] = %s
          AND [EstadoSesion] = 'PROGRAMADA'
          {schedule_filter}
        """,
        params,
    )


def _enqueue_team_creation(*, corte_id: int, visibility: str, usuario_registro: str) -> dict[str, Any] | None:
    rows = _fetch_all(
        f"""
        DECLARE @TeamCorteId int;
        EXEC [{complement_database_name()}].[graph].[usp_EncolarCrearTeamCorte]
            @CorteId = %s,
            @Visibility = %s,
            @UsuarioRegistro = %s,
            @TeamCorteId = @TeamCorteId OUTPUT;
        """,
        [
            corte_id,
            visibility,
            _trim_to_max(usuario_registro, 50),
        ],
    )
    return rows[0] if rows else _fetch_team_corte(corte_id)


def _confirm_team_corte(
    *,
    team_corte_id: int,
    team_id: str,
    web_url: str | None,
    group_id: str | None,
) -> dict[str, Any] | None:
    rows = _fetch_all(
        f"""
        EXEC [{complement_database_name()}].[graph].[usp_ConfirmarTeamCorte]
            @TeamCorteId = %s,
            @TeamId = %s,
            @WebUrl = %s,
            @GroupId = %s
        """,
        [
            team_corte_id,
            _trim_to_max(team_id, 100),
            _trim_to_max(web_url, 1000) or None,
            _trim_to_max(group_id, 100) or None,
        ],
    )
    return rows[0] if rows else None


def _enqueue_team_members(*, corte_id: int, usuario_registro: str) -> list[dict[str, Any]]:
    return _fetch_all(
        f"""
        EXEC [{complement_database_name()}].[graph].[usp_EncolarMatriculaTeamsCorte]
            @CorteId = %s,
            @UsuarioRegistro = %s
        """,
        [
            corte_id,
            _trim_to_max(usuario_registro, 50),
        ],
    )


def _update_team_display_name(*, corte_id: int, team_corte_id: int, display_name: str) -> dict[str, Any] | None:
    """Keep the local Team definition and its queued Graph payload aligned."""
    clean_name = _trim_to_max(display_name, 256)
    if not clean_name:
        return _fetch_team_corte(corte_id)

    description = _trim_to_max(
        f'Aula Teams de Educación Continua. CorteId: {corte_id}. Fuente académica: INTECBDD.',
        1024,
    )
    request_json = json.dumps(
        {
            "template@odata.bind": "https://graph.microsoft.com/v1.0/teamsTemplates('standard')",
            'visibility': (_fetch_team_corte(corte_id) or {}).get('Visibility') or 'Private',
            'displayName': clean_name,
            'description': description,
            'firstChannelName': 'General',
        },
        ensure_ascii=False,
    )
    _fetch_all(
        f"""
        UPDATE [{complement_database_name()}].[graph].[TeamCorte]
        SET [DisplayName] = %s,
            [Description] = %s,
            [RequestJson] = %s,
            [FechaActualizacion] = sysdatetime()
        WHERE [TeamCorteId] = %s AND [CorteId] = %s;

        UPDATE [{complement_database_name()}].[graph].[OperacionQueue]
        SET [RequestJson] = %s
        WHERE [TipoOperacion] = 'CREAR_TEAM'
          AND [Entidad] = 'graph.TeamCorte'
          AND [EntidadId] = %s
          AND [EstadoOperacion] IN ('PENDIENTE', 'PROCESANDO');
        """,
        [clean_name, description, request_json, team_corte_id, corte_id, request_json, team_corte_id],
    )
    return _fetch_team_corte(corte_id)


def _ensure_team_additional_owner_schema() -> bool:
    """Small additive table; it never changes the source academic database."""
    if not _teams_complement_status(require_write=False)['available']:
        return False
    try:
        _fetch_all(
            f"""
            IF OBJECT_ID(N'[{complement_database_name()}].[graph].[TeamAdministradorAdicional]', N'U') IS NULL
            BEGIN
                CREATE TABLE [{complement_database_name()}].[graph].[TeamAdministradorAdicional] (
                    [TeamAdministradorId] int IDENTITY(1,1) NOT NULL PRIMARY KEY,
                    [TeamCorteId] int NOT NULL,
                    [CorteId] int NOT NULL,
                    [Correo] nvarchar(150) NOT NULL,
                    [EstadoGraph] varchar(30) NOT NULL CONSTRAINT [DF_graph_TeamAdminExtra_Estado] DEFAULT ('PENDIENTE'),
                    [ErrorGraph] nvarchar(1000) NULL,
                    [UsuarioRegistro] varchar(50) NULL,
                    [FechaRegistro] datetime2(0) NOT NULL CONSTRAINT [DF_graph_TeamAdminExtra_Fecha] DEFAULT (sysdatetime()),
                    [FechaEnvioGraph] datetime2(0) NULL,
                    CONSTRAINT [UQ_graph_TeamAdminExtra_CorteCorreo] UNIQUE ([CorteId], [Correo])
                );
            END
            """
        )
        return True
    except Exception:
        # The principal feature remains available when the SQL account lacks DDL permission.
        return False


def _normalize_additional_owner_emails(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else re.split(r'[;,\n]+', _clean_text(value))
    emails: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        email = _clean_text(raw).lower()
        if not email:
            continue
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise CourseCutError(f'El correo de administrador "{email}" no es válido.')
        if email not in seen:
            seen.add(email)
            emails.append(email)
    return emails[:20]


def _save_team_additional_owners(*, corte_id: int, team_corte_id: int, emails: list[str], usuario_registro: str) -> None:
    if not _ensure_team_additional_owner_schema():
        if emails:
            raise CourseCutError(
                'La base complementaria no permite guardar administradores adicionales de Teams. '
                'Ejecuta la actualización del módulo de Teams con una cuenta que tenga permiso CREATE TABLE.'
            )
        return
    _fetch_all(
        f"DELETE FROM [{complement_database_name()}].[graph].[TeamAdministradorAdicional] WHERE [CorteId] = %s",
        [corte_id],
    )
    for email in emails:
        _fetch_all(
            f"""
            INSERT INTO [{complement_database_name()}].[graph].[TeamAdministradorAdicional]
                ([TeamCorteId], [CorteId], [Correo], [UsuarioRegistro])
            VALUES (%s, %s, %s, %s)
            """,
            [team_corte_id, corte_id, email, _trim_to_max(usuario_registro, 50)],
        )


def _fetch_team_additional_owners(corte_id: int) -> list[dict[str, Any]]:
    if not _ensure_team_additional_owner_schema():
        return []
    rows = _fetch_all(
        f"""
        SELECT [TeamAdministradorId], [Correo], [EstadoGraph], [ErrorGraph], [FechaEnvioGraph]
        FROM [{complement_database_name()}].[graph].[TeamAdministradorAdicional]
        WHERE [CorteId] = %s
        ORDER BY [Correo]
        """,
        [corte_id],
    )
    return [
        {
            'id': _clean_text(row.get('TeamAdministradorId')),
            'email': _clean_text(row.get('Correo')).lower(),
            'estado_graph': _clean_text(row.get('EstadoGraph')),
            'error_graph': _clean_text(row.get('ErrorGraph')),
            'fecha_envio_graph': _date_iso(row.get('FechaEnvioGraph')),
        }
        for row in rows
    ]


def _fetch_team_corte(corte_id: int) -> dict[str, Any] | None:
    if not _teams_complement_status(require_write=False)['available']:
        return None
    return _fetch_one(
        f"""
        SELECT TOP (1)
            [TeamCorteId],
            [CorteId],
            [DisplayName],
            [Description],
            [Visibility],
            [TeamId],
            [GroupId],
            [WebUrl],
            [GraphOperationUrl],
            [EstadoGraph],
            [ErrorGraph],
            [FechaRegistro],
            [FechaCreacionGraph],
            [FechaActualizacion]
        FROM [{complement_database_name()}].[graph].[TeamCorte]
        WHERE [CorteId] = %s
        """,
        [corte_id],
    )


def _fetch_team_member_rows(corte_id: int) -> list[dict[str, Any]]:
    return _fetch_all(
        f"""
        SELECT
            TM.[TeamMiembroId],
            TM.[TeamCorteId],
            TM.[CorteId],
            TM.[TipoMiembro],
            TM.[EstudianteCorteId],
            TM.[DocenteCorteId],
            TM.[CodigoEstud],
            TM.[CodigoDocente],
            TM.[UserPrincipalName],
            TM.[GraphUserId],
            TM.[RolTeams],
            TM.[EstadoGraph],
            TM.[ErrorGraph],
            TM.[FechaRegistro],
            TM.[FechaEnvioGraph]
        FROM [{complement_database_name()}].[graph].[TeamMiembroCorte] TM
        WHERE TM.[CorteId] = %s
        ORDER BY
            CASE TM.[TipoMiembro] WHEN 'DOCENTE' THEN 0 ELSE 1 END,
            TM.[UserPrincipalName],
            TM.[TeamMiembroId]
        """,
        [corte_id],
    )


def _fetch_graph_queue_rows(corte_id: int) -> list[dict[str, Any]]:
    return _fetch_all(
        f"""
        SELECT TOP (200)
            OQ.[OperacionId],
            OQ.[TipoOperacion],
            OQ.[Entidad],
            OQ.[EntidadId],
            OQ.[HttpMethod],
            OQ.[Endpoint],
            OQ.[EstadoOperacion],
            OQ.[Intentos],
            OQ.[FechaProgramada],
            OQ.[FechaRegistro],
            OQ.[ErrorOperacion]
        FROM [{complement_database_name()}].[graph].[OperacionQueue] OQ
        WHERE (
              OQ.[Entidad] = 'graph.TeamCorte'
              AND OQ.[EntidadId] IN (
                    SELECT [TeamCorteId]
                    FROM [{complement_database_name()}].[graph].[TeamCorte]
                    WHERE [CorteId] = %s
              )
          )
          OR (
              OQ.[Entidad] = 'graph.TeamMiembroCorte'
              AND OQ.[EntidadId] IN (
                    SELECT [TeamMiembroId]
                    FROM [{complement_database_name()}].[graph].[TeamMiembroCorte]
                    WHERE [CorteId] = %s
              )
          )
        ORDER BY OQ.[FechaProgramada] DESC, OQ.[OperacionId] DESC
        """,
        [corte_id, corte_id],
    )


def _normalize_schedule_row(row: dict[str, Any]) -> dict[str, Any]:
    dia_semana = _safe_int(row.get('DiaSemana'), default=0)
    return {
        'horario_id': _clean_text(row.get('HorarioId')),
        'corte_id': _clean_text(row.get('CorteId')),
        'dia_semana': dia_semana,
        'dia_semana_label': WEEKDAY_LABELS.get(dia_semana, ''),
        'hora_inicio': _clean_text(row.get('HoraInicio')),
        'hora_fin': _clean_text(row.get('HoraFin')),
        'modalidad': _normalize_schedule_modality(row.get('Modalidad')),
        'aula': _clean_text(row.get('Aula')),
        'enlace_virtual': _clean_text(row.get('EnlaceVirtual')),
        'estado': _clean_text(row.get('EstadoHorario')),
        'usuario_registro': _clean_text(row.get('UsuarioRegistro')),
        'fecha_registro': _date_iso(row.get('FechaRegistro')),
        'observacion': _clean_text(row.get('Observacion')),
        'docente_responsable': _parse_schedule_teacher_observation(row.get('Observacion')),
        'total_sesiones': _safe_int(row.get('TotalSesiones'), default=0),
        'sesiones_programadas': _safe_int(row.get('SesionesProgramadas'), default=0),
        'sesiones_realizadas': _safe_int(row.get('SesionesRealizadas'), default=0),
        'primera_sesion': _date_iso(row.get('PrimeraSesion')),
        'ultima_sesion': _date_iso(row.get('UltimaSesion')),
    }


def _normalize_session_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'sesion_id': _clean_text(row.get('SesionId')),
        'corte_id': _clean_text(row.get('CorteId')),
        'horario_id': _clean_text(row.get('HorarioId')),
        'fecha': _date_iso(row.get('FechaClase')),
        'hora_inicio': _clean_text(row.get('HoraInicio')),
        'hora_fin': _clean_text(row.get('HoraFin')),
        'tema': _clean_text(row.get('TemaClase')),
        'modalidad': _normalize_schedule_modality(row.get('Modalidad')),
        'aula': _clean_text(row.get('Aula')),
        'enlace_virtual': _clean_text(row.get('EnlaceVirtual')),
        'estado': _clean_text(row.get('EstadoSesion')),
    }


def _normalize_schedule_teacher_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'docente_corte_id': _clean_text(row.get('DocenteCorteId')),
        'corte_id': _clean_text(row.get('CorteId')),
        'codigo_docente': _clean_text(row.get('CodigoDocente')),
        'rol_docente': _clean_text(row.get('RolDocente')),
        'estado': _clean_text(row.get('EstadoDocenteCorte')),
        'fecha_matricula': _date_iso(row.get('FechaMatricula')),
        'cedula': _clean_text(row.get('CedulaDoc')),
        'nombre': _clean_text(row.get('ApellidosNombre')) or 'Docente sin nombre',
        'correo_personal': _clean_text(row.get('CorreoPersonal')).lower(),
        'correo_intec': _clean_text(row.get('CorreoIntec')).lower(),
        'usuario_login': _clean_text(row.get('UsuarioLogin')).lower(),
        'usuario_sis_login': _clean_text(row.get('UsuarioSisLogin')).lower(),
    }


def _normalize_team_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        'team_corte_id': _clean_text(row.get('TeamCorteId')),
        'corte_id': _clean_text(row.get('CorteId')),
        'display_name': _clean_text(row.get('DisplayName')),
        'description': _clean_text(row.get('Description')),
        'visibility': _clean_text(row.get('Visibility')),
        'team_id': _clean_text(row.get('TeamId')),
        'group_id': _clean_text(row.get('GroupId')),
        'web_url': _clean_text(row.get('WebUrl')),
        'graph_operation_url': _clean_text(row.get('GraphOperationUrl')),
        'estado_graph': _clean_text(row.get('EstadoGraph')),
        'error_graph': _clean_text(row.get('ErrorGraph')),
        'fecha_registro': _date_iso(row.get('FechaRegistro')),
        'fecha_creacion_graph': _date_iso(row.get('FechaCreacionGraph')),
        'fecha_actualizacion': _date_iso(row.get('FechaActualizacion')),
    }


def _normalize_team_member_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'team_miembro_id': _clean_text(row.get('TeamMiembroId')),
        'team_corte_id': _clean_text(row.get('TeamCorteId')),
        'corte_id': _clean_text(row.get('CorteId')),
        'tipo_miembro': _clean_text(row.get('TipoMiembro')),
        'estudiante_corte_id': _clean_text(row.get('EstudianteCorteId')),
        'docente_corte_id': _clean_text(row.get('DocenteCorteId')),
        'codigo_estud': _clean_text(row.get('CodigoEstud')),
        'codigo_docente': _clean_text(row.get('CodigoDocente')),
        'user_principal_name': _clean_text(row.get('UserPrincipalName')),
        'graph_user_id': _clean_text(row.get('GraphUserId')),
        'rol_teams': _clean_text(row.get('RolTeams')),
        'estado_graph': _clean_text(row.get('EstadoGraph')),
        'error_graph': _clean_text(row.get('ErrorGraph')),
        'fecha_registro': _date_iso(row.get('FechaRegistro')),
        'fecha_envio_graph': _date_iso(row.get('FechaEnvioGraph')),
    }


def _normalize_graph_queue_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'operacion_id': _clean_text(row.get('OperacionId')),
        'tipo_operacion': _clean_text(row.get('TipoOperacion')),
        'entidad': _clean_text(row.get('Entidad')),
        'entidad_id': _clean_text(row.get('EntidadId')),
        'http_method': _clean_text(row.get('HttpMethod')),
        'endpoint': _clean_text(row.get('Endpoint')),
        'estado_operacion': _clean_text(row.get('EstadoOperacion')),
        'intentos': _safe_int(row.get('Intentos'), default=0),
        'fecha_programada': _date_iso(row.get('FechaProgramada')),
        'fecha_registro': _date_iso(row.get('FechaRegistro')),
        'error_operacion': _clean_text(row.get('ErrorOperacion')),
    }


def _build_schedule_metrics(schedules: list[dict[str, Any]], sessions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        'horarios': len(schedules),
        'sesiones': len(sessions),
        'programadas': len([session for session in sessions if session.get('estado') == 'PROGRAMADA']),
        'realizadas': len([session for session in sessions if session.get('estado') == 'REALIZADA']),
    }


def _build_team_member_metrics(members: list[dict[str, Any]]) -> dict[str, int]:
    return {
        'total': len(members),
        'docentes': len([member for member in members if member.get('tipo_miembro') == 'DOCENTE']),
        'estudiantes': len([member for member in members if member.get('tipo_miembro') == 'ESTUDIANTE']),
        'encolados': len([member for member in members if member.get('estado_graph') == 'ENCOLADO']),
        'agregados': len([member for member in members if member.get('estado_graph') == 'AGREGADO']),
        'sin_correo': len([member for member in members if member.get('estado_graph') == 'SIN_CORREO']),
        'errores': len([member for member in members if member.get('estado_graph') == 'ERROR']),
    }


def _build_graph_queue_metrics(queue_rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        'total': len(queue_rows),
        'pendientes': len([row for row in queue_rows if row.get('estado_operacion') == 'PENDIENTE']),
        'procesando': len([row for row in queue_rows if row.get('estado_operacion') == 'PROCESANDO']),
        'completados': len([row for row in queue_rows if row.get('estado_operacion') == 'COMPLETADO']),
        'errores': len([row for row in queue_rows if row.get('estado_operacion') == 'ERROR']),
    }


def _fetch_complement_enrollment_rows(corte_id: int, *, search: Any = '', limit: Any = 300) -> list[dict[str, Any]]:
    safe_limit = max(1, min(_safe_int(limit, default=300), 1000))
    clean_search = _clean_text(search)
    params: list[Any] = [corte_id]
    search_clause = ''
    search_clause_v5 = ''
    if clean_search:
        search_value = f'%{clean_search}%'
        search_clause = """
          AND (
              LTRIM(RTRIM(CAST(MP.[CodigoEstud] AS varchar(30)))) LIKE %s
              OR (MP.[CedulaEst] COLLATE DATABASE_DEFAULT) LIKE %s
              OR (MP.[ApellidosNombre] COLLATE DATABASE_DEFAULT) LIKE %s
          )
        """
        search_clause_v5 = """
          AND (
              LTRIM(RTRIM(CAST(M.[CodigoEstud] AS varchar(30)))) LIKE %s
              OR (M.[CedulaEst] COLLATE DATABASE_DEFAULT) LIKE %s
              OR (M.[ApellidosNombre] COLLATE DATABASE_DEFAULT) LIKE %s
          )
        """
        params.extend([search_value, search_value, search_value])

    if complement_version() == 'v5':
        return _fetch_all(
            f"""
            SELECT TOP ({safe_limit})
                M.[EstudianteCorteId] AS [CorteEstudianteId],
                M.[EstudianteCorteId],
                M.[CorteId],
                D.[TipoOferta],
                D.[Cod_AnioBasica],
                CAST(NULL AS nvarchar(150)) AS [NombreCarrera],
                D.[CodigoPeriodo],
                D.[CodigoMateria],
                D.[CodCurso],
                D.[NombreCursoMateria] AS [NombreCurso],
                D.[NombreCorte],
                M.[CodigoEstud],
                M.[CorteEstudianteIdPrincipal],
                M.[CedulaEst],
                M.[ApellidosNombre],
                M.[CorreoIntec],
                M.[CorreoPersonal],
                M.[UsuarioLogin],
                M.[FechaMatricula] AS [FechaInicioEstudiante],
                M.[EstadoMatricula] AS [EstadoParticipacion],
                M.[EstadoMatricula] AS [EstadoRegistro],
                M.[EstadoMatricula],
                CAST(NULL AS decimal(18,0)) AS [Num_Matricula],
                CAL.[CalificacionId],
                CAL.[NotaFinal],
                CAL.[EstadoNota],
                CAL.[FechaCalificacion],
                CAL.[FechaModifica] AS [FechaModificaNota],
                CAL.[FechaPase],
                AR.[PorcentajeAsistencia],
                AR.[TotalSesionesRealizadas] AS [TotalSesiones],
                CAST(NULL AS int) AS [CarreraEstudId],
                CAST(NULL AS decimal(4,2)) AS [PromedioFinalPrincipal],
                CAST(NULL AS decimal(4,2)) AS [PromedioPrincipal],
                CAST(NULL AS decimal(4,2)) AS [PromedioAuxPrincipal],
                PNC.[EstadoPase],
                PNC.[MensajePase],
                PNC.[FechaPase] AS [FechaPasePrincipal]
            FROM [{complement_database_name()}].[edu].[VW_MatriculaEstudianteCompleta] M
            INNER JOIN [{complement_database_name()}].[edu].[VW_CorteCursoDetalle] D
              ON D.[CorteId] = M.[CorteId]
            LEFT JOIN [{complement_database_name()}].[edu].[CalificacionCorte] CAL
              ON CAL.[EstudianteCorteId] = M.[EstudianteCorteId]
            LEFT JOIN [{complement_database_name()}].[edu].[VW_AsistenciaResumen] AR
              ON AR.[EstudianteCorteId] = M.[EstudianteCorteId]
            OUTER APPLY (
                SELECT TOP 1 P0.[EstadoPase], P0.[MensajePase], P0.[FechaPase]
                FROM [{complement_database_name()}].[edu].[PaseNotaControl] P0
                WHERE P0.[EstudianteCorteId] = M.[EstudianteCorteId]
                  AND P0.[DestinoPase] = 'LOCAL_EDUCONTINUA'
                ORDER BY P0.[PaseNotaId] DESC
            ) PNC
            WHERE M.[CorteId] = %s
              AND (M.[EstadoMatricula] COLLATE DATABASE_DEFAULT) NOT IN ('ANULADO','RETIRADO')
              {search_clause_v5}
            ORDER BY M.[ApellidosNombre] ASC, M.[EstudianteCorteId] ASC
            """,
            params,
        )

    return _fetch_all(
        f"""
        SELECT TOP ({safe_limit})
            MP.[CorteEstudianteId],
            MP.[CorteId],
            MP.[TipoOferta],
            MP.[Cod_AnioBasica],
            MP.[NombreCarrera],
            MP.[CodigoPeriodo],
            MP.[CodigoMateria],
            MP.[CodCurso],
            MP.[NombreCurso],
            MP.[NombreCorte],
            MP.[CodigoEstud],
            MP.[CedulaEst],
            MP.[ApellidosNombre],
            MP.[CorreoIntec],
            MP.[CorreoPersonal],
            MP.[UsuarioLogin],
            MP.[FechaInicioEstudiante],
            MP.[EstadoParticipacion],
            MP.[EstadoRegistro],
            MP.[Num_Matricula],
            MP.[EstadoComplemento],
            CAL.[NotaFinal],
            CAL.[FechaCalificacion],
            CAL.[FechaModifica] AS [FechaModificaNota],
            AR.[PorcentajeAsistencia],
            AR.[TotalSesiones],
            CX.[num] AS [CarreraEstudId],
            CX.[PromedioFinal] AS [PromedioFinalPrincipal],
            CX.[Promedio] AS [PromedioPrincipal],
            CX.[PromedioAux] AS [PromedioAuxPrincipal]
        FROM [{complement_database_name()}].[edu].[VW_MatriculasPrincipal] MP
        LEFT JOIN [{complement_database_name()}].[edu].[CalificacionCorte] CAL
          ON CAL.[CorteEstudianteId] = MP.[CorteEstudianteId]
        LEFT JOIN [{complement_database_name()}].[edu].[VW_AsistenciaResumen] AR
          ON AR.[CorteEstudianteId] = MP.[CorteEstudianteId]
        LEFT JOIN dbo.CARRERAXESTUD CX
          ON LTRIM(RTRIM(CAST(CX.[codigo_estud] AS varchar(30)))) = LTRIM(RTRIM(CAST(MP.[CodigoEstud] AS varchar(30))))
         AND LTRIM(RTRIM(CAST(CX.[cod_anio_Basica] AS varchar(30)))) = LTRIM(RTRIM(CAST(MP.[Cod_AnioBasica] AS varchar(30))))
         AND LTRIM(RTRIM(CAST(CX.[codigo_periodo] AS varchar(30)))) = LTRIM(RTRIM(CAST(MP.[CodigoPeriodo] AS varchar(30))))
         AND LTRIM(RTRIM(CAST(CX.[codigo_materia] AS varchar(30)))) = LTRIM(RTRIM(CAST(MP.[CodigoMateria] AS varchar(30))))
         AND (
              MP.[Num_Matricula] IS NULL
              OR LTRIM(RTRIM(CAST(CX.[Num_Matricula] AS varchar(30)))) = LTRIM(RTRIM(CAST(MP.[Num_Matricula] AS varchar(30))))
         )
        WHERE MP.[CorteId] = %s
          AND (MP.[EstadoRegistro] COLLATE DATABASE_DEFAULT) = 'A'
          {search_clause}
        ORDER BY MP.[ApellidosNombre] ASC, MP.[CorteEstudianteId] ASC
        """,
        params,
    )


def _normalize_enrolled_student(row: dict[str, Any]) -> dict[str, Any]:
    nota_final = _decimal_to_number(row.get('NotaFinal'))
    nota_principal = _decimal_to_number(row.get('PromedioFinalPrincipal'))
    has_primary_row = bool(_clean_text(row.get('CarreraEstudId')))
    estado_pase = _clean_text(row.get('EstadoPase')).upper()
    estado_nota = _clean_text(row.get('EstadoNota')).upper()
    grade_passed = estado_nota in {'PASADA', 'CERRADA'}
    if nota_final is None:
        grade_status = 'SIN_NOTA'
        grade_status_label = 'Sin nota'
    elif grade_passed:
        grade_status = 'PASADA'
        grade_status_label = 'Pasada'
    else:
        grade_status = estado_nota or 'BORRADOR'
        grade_status_label = 'Borrador' if grade_status == 'BORRADOR' else grade_status.title()

    return {
        'corte_estudiante_id': _clean_text(row.get('CorteEstudianteId')),
        'corte_id': _clean_text(row.get('CorteId')),
        'tipo_oferta': _clean_text(row.get('TipoOferta')),
        'codigo_estud': _clean_text(row.get('CodigoEstud')),
        'cedula': _clean_text(row.get('CedulaEst')),
        'nombre': _clean_text(row.get('ApellidosNombre')) or 'Sin nombre',
        'correo_intec': _clean_text(row.get('CorreoIntec')),
        'correo_personal': _clean_text(row.get('CorreoPersonal')),
        'usuario_login': _clean_text(row.get('UsuarioLogin')),
        'cod_anio_basica': _clean_text(row.get('Cod_AnioBasica')),
        'carrera': _clean_text(row.get('NombreCarrera')),
        'codigo_periodo': _clean_text(row.get('CodigoPeriodo')),
        'codigo_materia': _clean_text(row.get('CodigoMateria')),
        'cod_curso': _clean_text(row.get('CodCurso')),
        'nombre_curso': _clean_text(row.get('NombreCurso')),
        'nombre_corte': _clean_text(row.get('NombreCorte')),
        'num_matricula': _clean_text(row.get('Num_Matricula')),
        'fecha_inicio': _date_iso(row.get('FechaInicioEstudiante')),
        'estado_participacion': _clean_text(row.get('EstadoParticipacion')),
        'estado_registro': _clean_text(row.get('EstadoRegistro')).upper(),
        'estado_complemento': _clean_text(row.get('EstadoComplemento')),
        'estado_matricula': _clean_text(row.get('EstadoMatricula')),
        'nota_final': nota_final,
        'estado_nota': estado_nota,
        'fecha_calificacion': _date_iso(row.get('FechaCalificacion') or row.get('FechaModificaNota')),
        'fecha_pase': _date_iso(row.get('FechaPasePrincipal') or row.get('FechaPase')),
        'porcentaje_asistencia': _decimal_to_number(row.get('PorcentajeAsistencia')),
        'total_sesiones': _safe_int(row.get('TotalSesiones'), default=0),
        'tiene_registro_principal': has_primary_row,
        'carrera_estud_id': _clean_text(row.get('CarreraEstudId')),
        'nota_final_principal': nota_principal,
        'promedio_principal': _decimal_to_number(row.get('PromedioPrincipal')),
        'promedio_aux_principal': _decimal_to_number(row.get('PromedioAuxPrincipal')),
        'nota_pasada': grade_passed,
        'estado_pase': estado_pase,
        'mensaje_pase': _clean_text(row.get('MensajePase')),
        'pase_estado': grade_status,
        'pase_estado_label': grade_status_label,
    }


def _build_enrolled_student_metrics(students: list[dict[str, Any]]) -> dict[str, int]:
    with_grade = [student for student in students if student.get('nota_final') is not None]
    passed = [student for student in students if student.get('nota_pasada')]
    pending = [
        student
        for student in students
        if student.get('nota_final') is not None and not student.get('nota_pasada')
    ]
    return {
        'total': len(students),
        'con_nota': len(with_grade),
        'notas_pasadas': len(passed),
        'pendientes_pase': len(pending),
    }


def _fetch_attendance_session(corte_id: int, attendance_date: date, hour_start: time) -> dict[str, Any] | None:
    row = _fetch_one(
        f"""
        SELECT TOP (1)
            [SesionId],
            [CorteId],
            [FechaClase],
            CONVERT(varchar(5), [HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), [HoraFin], 108) AS [HoraFin],
            [EstadoSesion]
        FROM [{complement_database_name()}].[edu].[SesionCorte]
        WHERE [CorteId] = %s
          AND [FechaClase] = %s
          AND [HoraInicio] = %s
          AND [EstadoSesion] <> 'CANCELADA'
        ORDER BY [SesionId] ASC
        """,
        [corte_id, attendance_date, hour_start],
    )
    return _normalize_attendance_session(row) if row else None


def _ensure_attendance_session(
    corte_id: int,
    attendance_date: date,
    hour_start: time,
    *,
    hour_end: time | None,
    usuario_registro: str,
) -> dict[str, Any]:
    existing = _fetch_attendance_session(corte_id, attendance_date, hour_start)
    if existing:
        return existing

    safe_hour_end = hour_end
    if not safe_hour_end or safe_hour_end <= hour_start:
        safe_hour_end = (datetime.combine(attendance_date, hour_start) + timedelta(hours=1)).time().replace(second=0, microsecond=0)

    row = _fetch_one(
        f"""
        INSERT INTO [{complement_database_name()}].[edu].[SesionCorte]
            ([CorteId], [HorarioId], [FechaClase], [HoraInicio], [HoraFin], [TemaClase], [Modalidad], [EstadoSesion], [UsuarioRegistro], [Observacion])
        OUTPUT
            INSERTED.[SesionId],
            INSERTED.[CorteId],
            INSERTED.[FechaClase],
            CONVERT(varchar(5), INSERTED.[HoraInicio], 108) AS [HoraInicio],
            CONVERT(varchar(5), INSERTED.[HoraFin], 108) AS [HoraFin],
            INSERTED.[EstadoSesion]
        VALUES
            (%s, NULL, %s, %s, %s, NULL, 'PRESENCIAL', 'PROGRAMADA', %s, N'Sesión creada desde dashboard administrativo.')
        """,
        [corte_id, attendance_date, hour_start, safe_hour_end, _trim_to_max(usuario_registro, 50)],
    )
    if not row:
        raise CourseCutError('No fue posible crear la sesión de asistencia.')
    return _normalize_attendance_session(row)


def _fetch_attendance_student_rows(corte_id: int, *, session_id: Any = None) -> list[dict[str, Any]]:
    return _fetch_all(
        f"""
        SELECT
            M.[EstudianteCorteId],
            M.[EstudianteCorteId] AS [CorteEstudianteId],
            M.[CorteId],
            M.[CodigoEstud],
            M.[CedulaEst],
            M.[ApellidosNombre],
            M.[CorreoIntec],
            M.[EstadoMatricula],
            A.[EstadoAsistencia],
            A.[CuentaParaAsistencia],
            A.[MinutosRetraso],
            A.[Justificacion],
            AR.[PorcentajeAsistencia],
            AR.[TotalSesionesRealizadas]
        FROM [{complement_database_name()}].[edu].[VW_MatriculaEstudianteCompleta] M
        LEFT JOIN [{complement_database_name()}].[edu].[AsistenciaCorte] A
          ON A.[SesionId] = %s
         AND A.[EstudianteCorteId] = M.[EstudianteCorteId]
        LEFT JOIN [{complement_database_name()}].[edu].[VW_AsistenciaResumen] AR
          ON AR.[EstudianteCorteId] = M.[EstudianteCorteId]
        WHERE M.[CorteId] = %s
          AND (M.[EstadoMatricula] COLLATE DATABASE_DEFAULT) NOT IN ('ANULADO','RETIRADO')
        ORDER BY M.[ApellidosNombre] ASC, M.[EstudianteCorteId] ASC
        """,
        [_safe_int(session_id, default=0), corte_id],
    )


def _normalize_attendance_student(row: dict[str, Any]) -> dict[str, Any]:
    estado = _clean_text(row.get('EstadoAsistencia')).upper()
    present = estado in {'PRESENTE', 'TARDANZA'}
    return {
        'corte_estudiante_id': _clean_text(row.get('EstudianteCorteId') or row.get('CorteEstudianteId')),
        'corte_id': _clean_text(row.get('CorteId')),
        'codigo_estud': _clean_text(row.get('CodigoEstud')),
        'cedula': _clean_text(row.get('CedulaEst')),
        'nombre': _clean_text(row.get('ApellidosNombre')) or 'Sin nombre',
        'correo_intec': _clean_text(row.get('CorreoIntec')),
        'estado_matricula': _clean_text(row.get('EstadoMatricula')),
        'estado_asistencia': estado or 'AUSENTE',
        'presente': present,
        'cuenta_para_asistencia': bool(row.get('CuentaParaAsistencia')) if estado else False,
        'minutos_retraso': _safe_int(row.get('MinutosRetraso'), default=0),
        'justificacion': _clean_text(row.get('Justificacion')),
        'porcentaje_asistencia': _decimal_to_number(row.get('PorcentajeAsistencia')),
        'total_sesiones': _safe_int(row.get('TotalSesionesRealizadas'), default=0),
    }


def _normalize_attendance_session(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'sesion_id': _clean_text(row.get('SesionId')),
        'corte_id': _clean_text(row.get('CorteId')),
        'fecha': _date_iso(row.get('FechaClase')),
        'hora_inicio': _clean_text(row.get('HoraInicio')),
        'hora_fin': _clean_text(row.get('HoraFin')),
        'estado': _clean_text(row.get('EstadoSesion')),
    }


def _build_attendance_metrics(students: list[dict[str, Any]]) -> dict[str, int]:
    present = [student for student in students if student.get('estado_asistencia') in {'PRESENTE', 'TARDANZA'}]
    justified = [student for student in students if student.get('estado_asistencia') == 'JUSTIFICADO']
    absent = [student for student in students if student.get('estado_asistencia') == 'AUSENTE']
    return {
        'total': len(students),
        'presentes': len(present),
        'justificados': len(justified),
        'ausentes': len(absent),
    }


def _attendance_records_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_records = payload.get('records') or payload.get('students') or []
    if not isinstance(raw_records, list):
        raise CourseCutError('El detalle de asistencia debe enviarse como una lista.')

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_records:
        if not isinstance(item, dict):
            continue
        estudiante_corte_id = _clean_text(
            item.get('corte_estudiante_id')
            or item.get('estudiante_corte_id')
            or item.get('CorteEstudianteId')
            or item.get('student_id')
        )
        if not estudiante_corte_id or estudiante_corte_id in seen:
            continue
        seen.add(estudiante_corte_id)
        estado = _clean_text(item.get('estado_asistencia') or item.get('estado') or item.get('asistencia')).upper()
        if estado in {'1', 'TRUE', 'PRESENTE'}:
            estado = 'PRESENTE'
        elif estado in {'0', 'FALSE', 'AUSENTE', ''}:
            estado = 'AUSENTE'
        if estado not in {'PRESENTE', 'AUSENTE', 'TARDANZA', 'JUSTIFICADO'}:
            raise CourseCutError('El estado de asistencia debe ser PRESENTE, AUSENTE, TARDANZA o JUSTIFICADO.')
        records.append(
            {
                'corte_estudiante_id': estudiante_corte_id,
                'estado_asistencia': estado,
                'minutos_retraso': _safe_int(item.get('minutos_retraso'), default=0) if estado == 'TARDANZA' else None,
                'justificacion': _clean_text(item.get('justificacion')),
            }
        )
    return records


def _fetch_valid_attendance_student_ids(corte_id: int) -> set[str]:
    rows = _fetch_all(
        f"""
        SELECT CAST([EstudianteCorteId] AS varchar(30)) AS [EstudianteCorteId]
        FROM [{complement_database_name()}].[edu].[CorteEstudiante]
        WHERE [CorteId] = %s
          AND ([EstadoMatricula] COLLATE DATABASE_DEFAULT) NOT IN ('ANULADO','RETIRADO')
        """,
        [corte_id],
    )
    return {_clean_text(row.get('EstudianteCorteId')) for row in rows}


def _grade_records_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_records = payload.get('records') or payload.get('grades') or payload.get('students') or []
    if not isinstance(raw_records, list):
        raise CourseCutError('El detalle de notas debe enviarse como una lista.')

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_records:
        if not isinstance(item, dict):
            continue
        corte_estudiante_id = _clean_text(
            item.get('corte_estudiante_id')
            or item.get('CorteEstudianteId')
            or item.get('student_id')
        )
        if not corte_estudiante_id or corte_estudiante_id in seen:
            continue
        seen.add(corte_estudiante_id)
        records.append(
            {
                'corte_estudiante_id': corte_estudiante_id,
                'nota_final': item.get('nota_final') if 'nota_final' in item else item.get('NotaFinal'),
                'observacion': _clean_text(item.get('observacion')),
            }
        )
    return records


def _grade_from_record(record: dict[str, Any], enrollment: dict[str, Any]) -> Decimal:
    raw_grade = record.get('nota_final')
    if raw_grade is None or _clean_text(raw_grade) == '':
        raw_grade = enrollment.get('NotaFinal')
    return _coerce_grade(raw_grade)


def _register_complement_grade(
    *,
    corte_estudiante_id: str,
    nota_final: Decimal,
    usuario_registro: str,
    observacion: str,
) -> None:
    student_param = '@EstudianteCorteId' if complement_version() == 'v5' else '@CorteEstudianteId'
    _fetch_all(
        f"""
        EXEC [{complement_database_name()}].[edu].[usp_RegistrarNotaFinalCorte]
            {student_param} = %s,
            @NotaFinal = %s,
            @UsuarioRegistro = %s,
            @Observacion = %s
        """,
        [
            _safe_int(corte_estudiante_id, default=0),
            nota_final,
            _trim_to_max(usuario_registro, 50) or 'SISTEMA',
            _trim_to_max(observacion, 500) or None,
        ],
    )


def _evaluate_complement_certification(corte_estudiante_id: str) -> None:
    if complement_version() == 'v5':
        _fetch_all(
            f"""
            EXEC [{complement_database_name()}].[edu].[usp_EvaluarCertificacionCorte]
                @EstudianteCorteId = %s
            """,
            [_safe_int(corte_estudiante_id, default=0)],
        )
        return

    _fetch_all(
        f"""
        EXEC [{complement_database_name()}].[edu].[usp_EvaluarCertificacionCorte]
            @CorteEstudianteId = %s,
            @ActualizarEstadoPrincipal = 1
        """,
        [_safe_int(corte_estudiante_id, default=0)],
    )


def _pass_grade_in_complement(*, corte_id: Any, usuario_registro: str) -> dict[str, Any]:
    if complement_version() != 'v5':
        return {
            'synced': True,
            'message': 'Nota registrada en base complementaria.',
        }

    _fetch_all(
        f"""
        EXEC [{complement_database_name()}].[edu].[usp_PasarNotasCorte]
            @CorteId = %s,
            @UsuarioPase = %s
        """,
        [
            _safe_int(corte_id, default=0),
            _trim_to_max(usuario_registro or 'SISTEMA', 50),
        ],
    )
    return {
        'synced': True,
        'message': 'Nota registrada y pasada localmente en INTECEDUCONTINUA.',
    }


def _sync_grade_to_primary_optional(
    enrollment: dict[str, Any],
    *,
    nota_final: Decimal,
    usuario_registro: str,
) -> dict[str, Any]:
    if complement_version() == 'v5' and is_complement_available(
        [('edu', 'usp_PasarNotaPrincipalOpcional', 'P')]
    ):
        rows = _fetch_all(
            f"""
            EXEC [{complement_database_name()}].[edu].[usp_PasarNotaPrincipalOpcional]
                @EstudianteCorteId = %s,
                @UsuarioPase = %s
            """,
            [
                _safe_int(enrollment.get('EstudianteCorteId') or enrollment.get('CorteEstudianteId'), default=0),
                _trim_to_max(usuario_registro or 'SISTEMA', 50),
            ],
        )
        result = rows[0] if rows else {}
        estado = _clean_text(result.get('EstadoPase')).upper()
        return {
            'synced': estado == 'PASADO',
            'updated_rows': 1 if estado == 'PASADO' else 0,
            'estado_pase': estado,
            'message': _clean_text(result.get('MensajePase')) or (
                'Nota sincronizada en INTECBDD.'
                if estado == 'PASADO'
                else 'No existe matrícula académica equivalente en INTECBDD.'
            ),
        }

    carrera_estud_id = _safe_int(enrollment.get('CarreraEstudId'), default=0)
    user_value = _trim_to_max(usuario_registro or 'SISTEMA', 10)

    if carrera_estud_id > 0:
        params = [
            nota_final,
            nota_final,
            nota_final,
            _safe_int(enrollment.get('CorteId'), default=0) or None,
            user_value,
            carrera_estud_id,
        ]
        where_clause = "CX.[num] = %s"
    else:
        codigo_estud = _clean_text(enrollment.get('CodigoEstud'))
        cod_anio_basica = _clean_text(enrollment.get('Cod_AnioBasica'))
        codigo_periodo = _clean_text(enrollment.get('CodigoPeriodo'))
        codigo_materia = _clean_text(enrollment.get('CodigoMateria'))
        if not all([codigo_estud, cod_anio_basica, codigo_periodo, codigo_materia]):
            return {
                'synced': False,
                'message': 'No hay datos académicos suficientes para ubicar INTECBDD.',
            }

        params = [
            nota_final,
            nota_final,
            nota_final,
            _safe_int(enrollment.get('CorteId'), default=0) or None,
            user_value,
            codigo_estud,
            cod_anio_basica,
            codigo_periodo,
            codigo_materia,
        ]
        where_clause = """
            LTRIM(RTRIM(CAST(CX.[codigo_estud] AS varchar(30)))) = %s
            AND LTRIM(RTRIM(CAST(CX.[cod_anio_Basica] AS varchar(30)))) = %s
            AND LTRIM(RTRIM(CAST(CX.[codigo_periodo] AS varchar(30)))) = %s
            AND LTRIM(RTRIM(CAST(CX.[codigo_materia] AS varchar(30)))) = %s
        """
        num_matricula = _clean_text(enrollment.get('Num_Matricula'))
        if num_matricula:
            where_clause += "\n            AND LTRIM(RTRIM(CAST(CX.[Num_Matricula] AS varchar(30)))) = %s"
            params.append(num_matricula)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE CX
            SET CX.[PromedioFinal] = %s,
                CX.[Promedio] = %s,
                CX.[PromedioAux] = %s,
                CX.[CorteId] = COALESCE(%s, CX.[CorteId]),
                CX.[Usuario] = %s
            OUTPUT INSERTED.[num]
            FROM dbo.CARRERAXESTUD CX
            WHERE {where_clause}
            """,
            params,
        )
        updated = len(cursor.fetchall())

    if updated <= 0:
        return {
            'synced': False,
            'updated_rows': 0,
            'message': 'No se encontró la fila de CARRERAXESTUD para sincronizar la nota.',
        }
    return {
        'synced': True,
        'updated_rows': updated,
        'message': 'Nota sincronizada en INTECBDD.',
    }


def _coerce_grade(value: Any) -> Decimal:
    clean_value = _clean_text(value).replace(',', '.')
    if not clean_value:
        raise CourseCutError('La nota final es obligatoria.')
    try:
        grade_value = Decimal(clean_value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CourseCutError('La nota final debe ser numérica.') from exc
    if grade_value < Decimal('0.00') or grade_value > Decimal('10.00'):
        raise CourseCutError('La nota final debe estar entre 0.00 y 10.00.')
    return grade_value.quantize(Decimal('0.01'))


def _decimal_to_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)).quantize(Decimal('0.01')))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _grades_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    try:
        return abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal('0.01')
    except (InvalidOperation, TypeError, ValueError):
        return False


def _truthy_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    clean_value = _clean_text(value).lower()
    if clean_value in {'1', 'true', 'yes', 'si', 'sí', 'on'}:
        return True
    if clean_value in {'0', 'false', 'no', 'off'}:
        return False
    return default


def _fetch_complement_student_index(corte_id: int) -> dict[str, dict[str, Any]]:
    if complement_version() == 'v5':
        rows = _fetch_all(
            f"""
            SELECT
                CAST(E.[CorteEstudianteIdPrincipal] AS varchar(30)) AS CorteEstudianteId,
                CAST(E.[EstudianteCorteId] AS varchar(30)) AS EstudianteCorteId,
                E.[CodigoEstud],
                E.[EstadoMatricula],
                E.[FechaMatricula]
            FROM [{complement_database_name()}].[edu].[CorteEstudiante] E
            WHERE E.[CorteId] = %s
            """,
            [corte_id],
        )
        index: dict[str, dict[str, Any]] = {}
        for row in rows:
            primary_id = str(row.get('CorteEstudianteId') or '').strip()
            if primary_id:
                index[primary_id] = row
            codigo_estud = str(row.get('CodigoEstud') or '').strip()
            if codigo_estud:
                index[f'codigo:{codigo_estud}'] = row
        return index

    rows = _fetch_all(
        f"""
        SELECT
            CAST([CorteEstudianteId] AS varchar(30)) AS CorteEstudianteId,
            [EstadoComplemento],
            [FechaVinculacion]
        FROM [{complement_database_name()}].[edu].[EstudianteCorteControl]
        WHERE [CorteId] = %s
        """,
        [corte_id],
    )
    return {
        str(row.get('CorteEstudianteId') or '').strip(): row
        for row in rows
        if str(row.get('CorteEstudianteId') or '').strip()
    }


def _normalize_cut_student(
    row: dict[str, Any],
    complement_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    corte_estudiante_id = str(row.get('CorteEstudianteId') or '').strip()
    codigo_estud = str(row.get('CodigoEstud') or '').strip()
    complement_row = complement_index.get(corte_estudiante_id) or complement_index.get(f'codigo:{codigo_estud}') or {}
    estado_registro = str(row.get('EstadoRegistro') or '').strip().upper()
    return {
        'corte_estudiante_id': corte_estudiante_id,
        'corte_id': str(row.get('CorteId') or '').strip(),
        'codigo_estud': codigo_estud,
        'cedula': str(row.get('CedulaEst') or '').strip(),
        'nombre': str(row.get('ApellidosNombre') or '').strip() or 'Sin nombre',
        'correo_personal': str(row.get('CorreoPersonal') or '').strip(),
        'num_matricula': str(row.get('Num_Matricula') or '').strip(),
        'fecha_inicio': _date_iso(row.get('FechaInicioEstudiante')),
        'estado_participacion': str(row.get('EstadoParticipacion') or '').strip(),
        'estado_registro': estado_registro,
        'activo': estado_registro == 'A',
        'cod_anio_basica': str(row.get('Cod_AnioBasica') or '').strip(),
        'codigo_periodo': str(row.get('CodigoPeriodo') or '').strip(),
        'codigo_materia': str(row.get('CodigoMateria') or '').strip(),
        'cod_curso': str(row.get('CodCurso') or '').strip(),
        'paralelo': str(row.get('Paralelo') or '').strip(),
        'cod_jornada': str(row.get('CodJornada') or '').strip(),
        'jornada': str(row.get('Jornada') or '').strip(),
        'continuing_education': {
            'synced': bool(complement_row),
            'estado': str(complement_row.get('EstadoComplemento') or complement_row.get('EstadoMatricula') or '').strip(),
            'estudiante_corte_id': str(complement_row.get('EstudianteCorteId') or '').strip(),
            'fecha_vinculacion': _date_iso(complement_row.get('FechaVinculacion') or complement_row.get('FechaMatricula')),
        },
    }


def _student_ids_from_payload(payload: dict[str, Any]) -> set[str]:
    raw_values = payload.get('student_ids') or payload.get('students') or payload.get('corte_estudiante_ids') or []
    if isinstance(raw_values, (str, int, float)):
        raw_values = [raw_values]
    if not isinstance(raw_values, list):
        return set()
    return {
        str(value.get('corte_estudiante_id') if isinstance(value, dict) else value).strip()
        for value in raw_values
        if str(value.get('corte_estudiante_id') if isinstance(value, dict) else value).strip()
    }


def _sync_cut_to_complement(cut: dict[str, Any], *, user_login: str) -> dict[str, Any]:
    try:
        return configure_cut_in_complement(
            cut.get('corte_id') or cut.get('CorteId'),
            cupo_maximo=cut.get('cupo_esperado') or cut.get('CupoEsperado') or 50,
            usuario_registro=user_login or 'SISTEMA',
        )
    except Exception as exc:
        return {
            'synced': False,
            'database': complement_database_name(),
            'message': f'No se pudo sincronizar la corte con educación continua: {str(exc)}',
        }


def _sync_student_to_complement(
    *,
    corte_id: Any,
    codigo_estud: Any,
    usuario_registro: str,
    valor_total_curso: Any = None,
    origen_matricula: str = '',
) -> dict[str, Any]:
    try:
        has_explicit_course_value = valor_total_curso not in (None, '')
        return sync_student_enrollment_to_complement(
            corte_id=corte_id,
            codigo_estud=codigo_estud,
            usuario_registro=usuario_registro or 'SISTEMA',
            registrar_cargo_inicial=not has_explicit_course_value,
            valor_total_curso=valor_total_curso,
            origen_matricula=origen_matricula,
        )
    except Exception as exc:
        return {
            'synced': False,
            'database': complement_database_name(),
            'message': f'No se pudo sincronizar matrícula estudiantil con educación continua: {str(exc)}',
        }


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
    return _now_ecuador().date()


def _current_ecuador_time() -> time:
    return _now_ecuador().time().replace(second=0, microsecond=0)


def _now_ecuador() -> datetime:
    return datetime.now(ECUADOR_TIMEZONE)


def _is_registration_deadline_expired(value: Any) -> bool:
    parsed = _coerce_date(value)
    return bool(parsed and parsed < _today_ecuador())


def _registration_status_label(estado_corte: str, fecha_fin: Any) -> str:
    if estado_corte != 'ABIERTO':
        return 'CERRADA'
    if _is_registration_deadline_expired(fecha_fin):
        return 'CERRADA POR FECHA'
    return 'DISPONIBLE'


def _class_dates_from_payload(payload: dict[str, Any]) -> list[date]:
    raw_value = (
        payload.get('fechas_clase')
        or payload.get('fechasClase')
        or payload.get('class_dates')
        or payload.get('selected_dates')
        or []
    )
    if isinstance(raw_value, str):
        raw_dates = [item.strip() for item in raw_value.replace(';', ',').split(',')]
    elif isinstance(raw_value, (list, tuple, set)):
        raw_dates = list(raw_value)
    else:
        raw_dates = []

    dates: list[date] = []
    seen: set[date] = set()
    for raw_date in raw_dates:
        if not _clean_text(raw_date):
            continue
        parsed = _coerce_date(raw_date)
        if not parsed:
            raise CourseCutError(f'La fecha de clase {raw_date} no es válida.')
        if parsed in seen:
            continue
        seen.add(parsed)
        dates.append(parsed)
    dates.sort()
    return dates


def _sql_weekday(value: date) -> int:
    return value.isoweekday()


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


def _coerce_time(value: Any, *, default: time | None = None) -> time | None:
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value or '').strip()
    if not text:
        return default
    try:
        if 'T' in text:
            return datetime.fromisoformat(text).time().replace(second=0, microsecond=0)
        return time.fromisoformat(text[:8]).replace(second=0, microsecond=0)
    except ValueError:
        return default


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
