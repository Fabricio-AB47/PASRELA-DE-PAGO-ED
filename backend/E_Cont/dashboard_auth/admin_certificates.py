from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.db import connection

from .academic_rules import is_passing_grade
from .course_cuts import _format_date_label, list_enrolled_students
from .continuing_education import complement_database_name, connection_for_query
from .inscription_certificate import (
    CERTIFICATE_STORAGE_DIR_NAME,
    build_inscription_certificate,
    create_stored_certificate_record,
    load_or_create_stored_certificate,
    load_signed_certificate_payload,
    send_certificate_email,
)


class AdminCertificateError(Exception):
    pass


def list_admin_certificate_students(corte_id: Any, *, search: str = '', limit: Any = 300) -> dict[str, Any]:
    _ensure_certificate_link_table()
    result = list_enrolled_students(corte_id, search=search, limit=limit)
    links = _fetch_certificate_links(result['cut']['corte_id'])
    payment_accounts = _certificate_payment_accounts(result['cut']['corte_id'])
    students = [
        _attach_certificate_info(student, links, payment_accounts)
        for student in result['students']
    ]
    metrics = {
        **result.get('metrics', {}),
        'certificados_generados': len([student for student in students if student.get('certificado')]),
        'certificados_disponibles': len([student for student in students if student.get('certificado_disponible')]),
        'pendientes_certificado': len(
            [
                student
                for student in students
                if student.get('certificado_disponible') and not student.get('certificado')
            ]
        ),
    }
    return {
        **result,
        'students': students,
        'metrics': metrics,
    }


def generate_admin_certificates(payload: dict[str, Any], *, user_login: str = '') -> dict[str, Any]:
    _ensure_certificate_link_table()
    corte_id = _safe_int(payload.get('corte_id') or payload.get('CorteId'), default=0)
    if corte_id <= 0:
        raise AdminCertificateError('Debes seleccionar una corte para generar certificados.')

    selected_ids = _selected_student_ids(payload)
    include_all = bool(payload.get('all') or payload.get('todos'))
    if not selected_ids and not include_all:
        raise AdminCertificateError('Selecciona al menos un estudiante para generar el certificado.')

    result = list_enrolled_students(corte_id, search='', limit=1000)
    cut = _enrich_certificate_course_details(result['cut'])
    links = _fetch_certificate_links(corte_id)
    payment_accounts = _certificate_payment_accounts(corte_id)
    students = [
        _attach_certificate_info(student, links, payment_accounts)
        for student in result['students']
        if include_all or student.get('corte_estudiante_id') in selected_ids
    ]
    if not students:
        raise AdminCertificateError('No se encontraron estudiantes válidos para generar certificados.')

    generated = 0
    emails_sent = 0
    email_errors = 0
    errors = 0
    results: list[dict[str, Any]] = []
    for student in students:
        try:
            certificate = _generate_student_certificate(cut, student, user_login=user_login)
            generated += 1
            personal_email = _clean_text(student.get('correo_personal')).lower()
            if personal_email:
                try:
                    email_result = send_certificate_email(
                        recipient_email=personal_email,
                        recipient_name=_clean_text(student.get('nombre')) or personal_email,
                        certificate_record=certificate,
                    )
                    if email_result.get('sent'):
                        emails_sent += 1
                    else:
                        email_errors += 1
                except Exception as email_exc:
                    email_errors += 1
                    email_result = {'sent': False, 'message': str(email_exc)}
            else:
                email_errors += 1
                email_result = {
                    'sent': False,
                    'message': 'El estudiante no tiene correo personal registrado.',
                }
            results.append(
                {
                    'ok': True,
                    'corte_estudiante_id': student.get('corte_estudiante_id'),
                    'codigo_estud': student.get('codigo_estud'),
                    'nombre': student.get('nombre'),
                    'message': (
                        'Certificado generado y enviado al correo personal.'
                        if email_result.get('sent')
                        else 'Certificado generado; no fue posible enviarlo al correo personal.'
                    ),
                    'certificate': certificate,
                    'email': personal_email,
                    'email_result': email_result,
                }
            )
        except Exception as exc:
            errors += 1
            results.append(
                {
                    'ok': False,
                    'corte_estudiante_id': student.get('corte_estudiante_id'),
                    'codigo_estud': student.get('codigo_estud'),
                    'nombre': student.get('nombre'),
                    'message': str(exc),
                }
            )

    updated = list_admin_certificate_students(corte_id, search=payload.get('q') or payload.get('search') or '')
    return {
        'cut': cut,
        'summary': {
            'procesados': len(students),
            'generados': generated,
            'respaldados': generated,
            'errores': errors,
            'correos_enviados': emails_sent,
            'errores_correo': email_errors,
        },
        'results': results,
        'updated': updated,
    }


def download_admin_certificates_batch(
    corte_id: Any,
    corte_estudiante_ids: Any,
) -> tuple[bytes, str, str]:
    safe_corte_id = _safe_int(corte_id, default=0)
    selected_ids = _selected_student_ids({'corte_estudiante_ids': corte_estudiante_ids})
    if safe_corte_id <= 0 or not selected_ids:
        raise AdminCertificateError('Debes seleccionar una corte y al menos un certificado generado.')

    downloaded: list[tuple[bytes, str, str]] = []
    for student_id in sorted(selected_ids, key=lambda value: _safe_int(value, default=0)):
        try:
            content, filename = download_admin_certificate(safe_corte_id, student_id)
        except AdminCertificateError:
            continue
        downloaded.append((content, filename, student_id))

    if not downloaded:
        raise AdminCertificateError('No se encontraron certificados generados para descargar.')
    if len(downloaded) == 1:
        content, filename, _student_id = downloaded[0]
        return content, filename, 'application/pdf'

    output = BytesIO()
    used_names: set[str] = set()
    with ZipFile(output, mode='w', compression=ZIP_DEFLATED) as archive:
        for content, filename, student_id in downloaded:
            safe_name = Path(filename).name or f'certificado_{student_id}.pdf'
            if safe_name in used_names:
                stem = Path(safe_name).stem
                suffix = Path(safe_name).suffix or '.pdf'
                safe_name = f'{stem}_{student_id}{suffix}'
            used_names.add(safe_name)
            archive.writestr(safe_name, content)

    return output.getvalue(), f'certificados_corte_{safe_corte_id}.zip', 'application/zip'


def download_admin_certificate(corte_id: Any, corte_estudiante_id: Any) -> tuple[bytes, str]:
    _ensure_certificate_link_table()
    safe_corte_id = _safe_int(corte_id, default=0)
    safe_student_id = _safe_int(corte_estudiante_id, default=0)
    if safe_corte_id <= 0 or safe_student_id <= 0:
        raise AdminCertificateError('Debes seleccionar una corte y un estudiante con certificado.')

    link = _fetch_certificate_link(safe_corte_id, safe_student_id)
    if not link:
        raise AdminCertificateError('Primero debes generar el certificado del estudiante.')

    enrollment = list_enrolled_students(safe_corte_id, search='', limit=1000)
    student = next(
        (
            item for item in enrollment.get('students', [])
            if _safe_int(item.get('corte_estudiante_id'), default=0) == safe_student_id
        ),
        None,
    )
    if not student or not _student_payment_is_complete(student):
        raise AdminCertificateError('El certificado no puede descargarse mientras exista saldo pendiente.')

    stored_path = _safe_stored_certificate_path(link.get('RutaArchivo'))
    if not stored_path.exists():
        raise AdminCertificateError('El archivo del certificado no se encontró en el almacenamiento local.')

    rebuilt = _rebuild_admin_certificate_file(safe_corte_id, safe_student_id, link, stored_path)
    if rebuilt:
        return rebuilt

    filename = stored_path.name or 'certificado_aprobacion.pdf'
    return stored_path.read_bytes(), filename


def _generate_student_certificate(cut: dict[str, Any], student: dict[str, Any], *, user_login: str) -> dict[str, Any]:
    if not _is_passing_grade(student.get('nota_final')):
        raise AdminCertificateError('El estudiante no tiene nota aprobatoria para generar certificado.')
    if not bool(student.get('pago_completo')):
        raise AdminCertificateError('El estudiante debe completar el pago total antes de generar el certificado.')

    payload = _certificate_payload_from_student(cut, student, user_login=user_login)
    certificate_record = create_stored_certificate_record(payload)
    certificate_payload = load_signed_certificate_payload(certificate_record['token'])
    load_or_create_stored_certificate(certificate_payload)
    _insert_certificate_link(cut, student, certificate_record, user_login=user_login)
    return certificate_record


def _student_payment_is_complete(student: dict[str, Any]) -> bool:
    # Importación local para evitar un ciclo entre pagos, cortes y certificados.
    from .payments import list_registered_user_payments

    result = list_registered_user_payments(
        search=student.get('codigo_estud') or student.get('cedula') or '',
        payment_status='all',
        page=1,
        page_size=100,
    )
    target_code = _clean_text(student.get('codigo_estud'))
    target_cut = _clean_text(student.get('corte_id'))
    account = next(
        (
            item for item in result.get('users', [])
            if _clean_text(item.get('codigo_estud')) == target_code
            and _clean_text(item.get('corte_id')) == target_cut
        ),
        None,
    )
    return bool(account and account.get('payment_status') == 'PAGADO')


def _rebuild_admin_certificate_file(
    corte_id: int,
    corte_estudiante_id: int,
    link: dict[str, Any],
    stored_path: Path,
) -> tuple[bytes, str] | None:
    try:
        result = list_enrolled_students(corte_id, search='', limit=1000)
    except Exception:
        return None

    student = next(
        (
            item
            for item in result.get('students', [])
            if _safe_int(item.get('corte_estudiante_id'), default=0) == corte_estudiante_id
        ),
        None,
    )
    if not student:
        return None

    payload = _certificate_payload_from_student(
        _enrich_certificate_course_details(result.get('cut') or {}),
        student,
        user_login=_clean_text(link.get('UsuarioRegistro')) or 'SISTEMA',
    )
    certificate_number = _clean_text(link.get('NumeroCertificado'))
    if certificate_number:
        payload['codigo_certificado'] = certificate_number
        payload['certificate_code'] = certificate_number
        generation = _fetch_certificate_generation(certificate_number)
        if generation:
            verification_code = _clean_text(generation.get('CodigoVerificacion'))
            if verification_code:
                payload['codigo_verificacion'] = verification_code
                payload['verification_code'] = verification_code

    content, generated_filename = build_inscription_certificate(payload)
    try:
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_bytes(content)
    except OSError:
        return content, generated_filename
    return content, stored_path.name or generated_filename


def _fetch_certificate_generation(certificate_number: str) -> dict[str, Any] | None:
    if not _clean_text(certificate_number):
        return None
    rows = _fetch_all(
        """
        SELECT TOP (1)
            CertificadoId,
            NumeroCertificado,
            CodigoVerificacion,
            Estado
        FROM dbo.CERTIFICADOS_GENERADOS
        WHERE NumeroCertificado = %s
        ORDER BY FechaGeneracion DESC, CertificadoId DESC
        """,
        [certificate_number],
    )
    return rows[0] if rows else None


def _certificate_payload_from_student(
    cut: dict[str, Any],
    student: dict[str, Any],
    *,
    user_login: str,
) -> dict[str, Any]:
    email = _first_non_empty(student.get('correo_intec'), student.get('correo_personal'), 'sin-correo@intec.edu.ec')
    return {
        'source': 'admin_corte',
        'tipo_certificado': 'APROBACION',
        'certificate_type': 'APROBACION',
        'nombre_materia': _certificate_course_name(cut, student),
        'codigo_materia': student.get('codigo_materia'),
        'matricula': _first_non_empty(student.get('codigo_estud'), student.get('corte_estudiante_id')),
        'codigo_estud': student.get('codigo_estud'),
        'numero_matricula': student.get('num_matricula'),
        'fecha_inscripcion': _first_non_empty(student.get('fecha_calificacion'), _today_label()),
        'fecha_inicio': _first_non_empty(cut.get('fecha_inicio'), student.get('fecha_inicio'), _today_label()),
        'fecha_fin': _first_non_empty(cut.get('fecha_fin_curso'), cut.get('fecha_fin')),
        'horas_curso': _first_non_empty(cut.get('horas_curso'), cut.get('horas')),
        'nombre': student.get('nombre'),
        'cedula': student.get('cedula'),
        'email': email,
        'codigo_periodo': student.get('codigo_periodo'),
        'cod_anio_basica': student.get('cod_anio_basica'),
        'cod_curso': student.get('cod_curso'),
        'corte_id': cut.get('corte_id'),
        'nombre_corte': _first_non_empty(student.get('nombre_corte'), cut.get('nombre_corte')),
        'modalidad': 'Educación continua',
        'nota_final': _number_label(student.get('nota_final')),
        'porcentaje_asistencia': _number_label(student.get('porcentaje_asistencia'), suffix='%'),
        'observaciones_internas': (
            f"Certificado generado por {user_login or 'SISTEMA'} para CorteId {cut.get('corte_id')} "
            f"y CorteEstudianteId {student.get('corte_estudiante_id')}."
        ),
        'skip_default_cc': True,
    }


def _enrich_certificate_course_details(cut: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(cut)
    corte_id = _safe_int(cut.get('corte_id'), default=0)
    if corte_id <= 0:
        return enriched
    db_name = complement_database_name()
    try:
        rows = _fetch_complement_all(
            f"""
            SELECT
                MIN(FechaInicio) AS FechaInicioCurso,
                MAX(COALESCE(FechaFinalizacion, FechaFin)) AS FechaFinCurso
            FROM [{db_name}].[edu].[ModuloCorte]
            WHERE CorteId = %s AND EstadoModulo = 'ACTIVO'
            """,
            [corte_id],
        )
    except Exception:
        rows = []
    timeline = rows[0] if rows else {}
    start_date = _format_date_label(timeline.get('FechaInicioCurso'))
    end_date = _format_date_label(timeline.get('FechaFinCurso'))
    if start_date:
        enriched['fecha_inicio'] = start_date
    if end_date:
        enriched['fecha_fin_curso'] = end_date
    enriched['horas_curso'] = _first_non_empty(cut.get('horas_curso'), cut.get('horas'))
    return enriched


def _certificate_course_name(cut: dict[str, Any], student: dict[str, Any]) -> str:
    candidates = [
        student.get('nombre_curso'),
        cut.get('curso_educontinua'),
        cut.get('materias_label'),
        cut.get('materia_pensum'),
        _strip_cut_prefix(cut.get('nombre_corte')),
    ]
    cleaned = [_clean_text(candidate) for candidate in candidates if _clean_text(candidate)]
    if not cleaned:
        return 'Curso de Educación Continua'
    return max(cleaned, key=len)


def _strip_cut_prefix(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ''
    parts = text.split('-', 1)
    if len(parts) == 2 and 'corte' in parts[0].lower():
        return parts[1].strip()
    return text


def _attach_certificate_info(
    student: dict[str, Any],
    links: dict[str, dict[str, Any]],
    payment_accounts: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    attached = dict(student)
    link = links.get(_clean_text(student.get('corte_estudiante_id')))
    account = payment_accounts.get(
        (_clean_text(student.get('codigo_estud')), _clean_text(student.get('corte_id'))),
        {},
    )
    passed = _is_passing_grade(student.get('nota_final'))
    payment_complete = bool(account.get('pago_completo'))
    available = passed and payment_complete
    attached.update(account)
    attached.setdefault('total_curso', '0.00')
    attached.setdefault('total_pagado', '0.00')
    attached.setdefault('total_descuentos', '0.00')
    attached.setdefault('saldo_pendiente', '0.00')
    attached['pago_completo'] = payment_complete
    attached['estado_financiero'] = 'PAGADO' if payment_complete else 'PENDIENTE'
    attached['certificado_disponible'] = available
    attached['certificado'] = _normalize_certificate_link(link) if link else None
    if not passed:
        unavailable_reason = 'Pendiente de aprobación'
    elif not payment_complete:
        unavailable_reason = 'Saldo pendiente'
    else:
        unavailable_reason = ''
    attached['motivo_no_disponible'] = unavailable_reason
    attached['certificado_estado'] = (
        'Generado' if link and available
        else 'Bloqueado: saldo pendiente' if link and not payment_complete
        else 'Disponible' if available
        else unavailable_reason or 'Pendiente'
    )
    return attached


def _certificate_payment_accounts(corte_id: Any) -> dict[tuple[str, str], dict[str, Any]]:
    db_name = complement_database_name()
    query = f"""
        SELECT
            CONVERT(varchar(50), E.CodigoEstud) AS CodigoEstud,
            CONVERT(varchar(50), E.CorteId) AS CorteId,
            MAX(CASE WHEN LOWER(LTRIM(RTRIM(ISNULL(E.Observacion, '')))) LIKE N'matrícula masiva%%' THEN 1 ELSE 0 END) AS EsMatriculaExcel,
            ISNULL(SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'DEBE' THEN M.Valor ELSE 0 END), 0) AS TotalCurso,
            ISNULL(SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) <> 'DESCUENTO' THEN M.Valor ELSE 0 END), 0) AS TotalPagado,
            ISNULL(SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) = 'DESCUENTO' THEN M.Valor ELSE 0 END), 0) AS TotalDescuentos,
            ISNULL(SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) = 'DESCUENTO' AND UPPER(LTRIM(RTRIM(ISNULL(M.Concepto, '')))) LIKE 'AJUSTE VALOR NETO CURSO - EXCEL%%' THEN M.Valor ELSE 0 END), 0) AS AjusteExcel
        FROM [{db_name}].[edu].[VW_MatriculaEstudianteCompleta] E
        LEFT JOIN [{db_name}].[fin].[CuentaEstudiante] C ON C.EstudianteCorteId = E.EstudianteCorteId
        LEFT JOIN [{db_name}].[fin].[MovimientoCuenta] M ON M.CuentaId = C.CuentaId
        WHERE E.CorteId = %s
        GROUP BY E.CodigoEstud, E.CorteId
        """
    rows = _fetch_complement_all(query, [_safe_int(corte_id, default=0)])
    accounts: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        total = _to_decimal(row.get('TotalCurso'))
        paid = _to_decimal(row.get('TotalPagado'))
        discounts = _to_decimal(row.get('TotalDescuentos'))
        if _safe_int(row.get('EsMatriculaExcel'), default=0) == 1:
            total = Decimal('400.00')
            discounts = max(Decimal('0.00'), discounts - _to_decimal(row.get('AjusteExcel')))
        if total <= 0 and paid > 0:
            total = paid
        pending = max(Decimal('0.00'), total - paid - discounts)
        payment_complete = total > 0 and pending <= 0
        accounts[(_clean_text(row.get('CodigoEstud')), _clean_text(row.get('CorteId')))] = {
            'total_curso': f'{total:.2f}',
            'total_pagado': f'{paid:.2f}',
            'total_descuentos': f'{discounts:.2f}',
            'saldo_pendiente': f'{pending:.2f}',
            'pago_completo': payment_complete,
        }
    return accounts


def _normalize_certificate_link(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        'certificado_corte_id': _clean_text(row.get('CertificadoCorteId')),
        'certificado_id': _clean_text(row.get('CertificadoId')),
        'numero_certificado': _clean_text(row.get('NumeroCertificado')),
        'ruta_archivo': _clean_text(row.get('RutaArchivo')),
        'estado': _clean_text(row.get('EstadoCertificado')),
        'fecha_registro': _date_iso(row.get('FechaRegistro')),
        'usuario_registro': _clean_text(row.get('UsuarioRegistro')),
    }


def _fetch_certificate_links(corte_id: Any) -> dict[str, dict[str, Any]]:
    rows = _fetch_all(
        """
        WITH Ultimos AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY CorteId, CorteEstudianteId
                    ORDER BY FechaRegistro DESC, CertificadoCorteId DESC
                ) AS rn
            FROM dbo.CERTIFICADO_CORTE_ESTUDIANTE
            WHERE CorteId = %s
              AND EstadoCertificado <> 'ANULADO'
        )
        SELECT *
        FROM Ultimos
        WHERE rn = 1
        """,
        [_safe_int(corte_id, default=0)],
    )
    return {
        _clean_text(row.get('CorteEstudianteId')): row
        for row in rows
        if _clean_text(row.get('CorteEstudianteId'))
    }


def _fetch_certificate_link(corte_id: int, corte_estudiante_id: int) -> dict[str, Any] | None:
    rows = _fetch_all(
        """
        SELECT TOP (1) *
        FROM dbo.CERTIFICADO_CORTE_ESTUDIANTE
        WHERE CorteId = %s
          AND CorteEstudianteId = %s
          AND EstadoCertificado <> 'ANULADO'
        ORDER BY FechaRegistro DESC, CertificadoCorteId DESC
        """,
        [corte_id, corte_estudiante_id],
    )
    return rows[0] if rows else None


def _insert_certificate_link(
    cut: dict[str, Any],
    student: dict[str, Any],
    certificate: dict[str, Any],
    *,
    user_login: str,
) -> None:
    _fetch_all(
        """
        INSERT INTO dbo.CERTIFICADO_CORTE_ESTUDIANTE (
            CorteId,
            CorteEstudianteId,
            EstudianteCorteId,
            CodigoEstud,
            CedulaEst,
            CertificadoId,
            NumeroCertificado,
            RutaArchivo,
            EstadoCertificado,
            UsuarioRegistro,
            Observacion
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'GENERADO', %s, %s)
        """,
        [
            _safe_int(cut.get('corte_id'), default=0),
            _safe_int(student.get('corte_estudiante_id'), default=0),
            _safe_int(student.get('estudiante_corte_id') or student.get('corte_estudiante_id'), default=0),
            _safe_int(student.get('codigo_estud'), default=0) or None,
            _trim(student.get('cedula'), 50) or None,
            _safe_int(certificate.get('certificate_id'), default=0) or None,
            _trim(certificate.get('certificate_code'), 100),
            _trim(certificate.get('stored_path'), 500),
            _trim(user_login or 'SISTEMA', 120),
            _trim(f"{cut.get('nombre_corte') or ''} - {student.get('nombre_curso') or ''}", 500),
        ],
    )


def _ensure_certificate_link_table() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            IF OBJECT_ID('dbo.CERTIFICADO_CORTE_ESTUDIANTE', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.CERTIFICADO_CORTE_ESTUDIANTE (
                    CertificadoCorteId INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                    CorteId INT NOT NULL,
                    CorteEstudianteId INT NULL,
                    EstudianteCorteId INT NULL,
                    CodigoEstud DECIMAL(18,0) NULL,
                    CedulaEst VARCHAR(50) NULL,
                    CertificadoId INT NULL,
                    NumeroCertificado VARCHAR(100) NOT NULL,
                    RutaArchivo VARCHAR(500) NULL,
                    EstadoCertificado VARCHAR(30) NOT NULL CONSTRAINT DF_CERT_CORTE_EST_Estado DEFAULT('GENERADO'),
                    UsuarioRegistro VARCHAR(120) NULL,
                    FechaRegistro DATETIME2(0) NOT NULL CONSTRAINT DF_CERT_CORTE_EST_Fecha DEFAULT(SYSDATETIME()),
                    Observacion VARCHAR(500) NULL
                )
            END
            IF NOT EXISTS (
                SELECT 1
                FROM sys.indexes
                WHERE name = 'IX_CERT_CORTE_EST_CorteEstudiante'
                  AND object_id = OBJECT_ID('dbo.CERTIFICADO_CORTE_ESTUDIANTE')
            )
            BEGIN
                CREATE INDEX IX_CERT_CORTE_EST_CorteEstudiante
                ON dbo.CERTIFICADO_CORTE_ESTUDIANTE (CorteId, CorteEstudianteId, FechaRegistro DESC)
            END
            """
        )


def _safe_stored_certificate_path(value: Any) -> Path:
    stored_path = _clean_text(value).replace('\\', '/')
    if not stored_path:
        raise AdminCertificateError('El certificado no tiene ruta de archivo registrada.')
    base_dir = (settings.BASE_DIR / CERTIFICATE_STORAGE_DIR_NAME).resolve()
    path = (settings.BASE_DIR / stored_path).resolve()
    try:
        path.relative_to(base_dir)
    except ValueError as exc:
        raise AdminCertificateError('La ruta del certificado no es válida.') from exc
    return path


def _selected_student_ids(payload: dict[str, Any]) -> set[str]:
    raw_values = payload.get('corte_estudiante_ids') or payload.get('students') or []
    if not isinstance(raw_values, list):
        raw_values = [raw_values]
    return {_clean_text(value) for value in raw_values if _clean_text(value)}


def _fetch_all(query: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params or [])
        if cursor.description is None:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fetch_complement_all(query: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    safe_params = params or []
    with connection_for_query(query, safe_params).cursor() as cursor:
        cursor.execute(query, safe_params)
        if cursor.description is None:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _is_passing_grade(value: Any) -> bool:
    return is_passing_grade(value)


def _number_label(value: Any, *, suffix: str = '') -> str:
    if value is None or value == '':
        return f'0.00{suffix}'
    try:
        decimal_value = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return f'{_clean_text(value)}{suffix}'
    return f'{decimal_value}{suffix}'


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _date_iso(value: Any) -> str:
    if not value:
        return ''
    if hasattr(value, 'date'):
        return value.date().isoformat()
    return str(value)[:10]


def _today_label() -> str:
    today = date.today()
    return today.isoformat()


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ''


def _clean_text(value: Any) -> str:
    return ' '.join(str(value or '').strip().split())


def _trim(value: Any, max_length: int) -> str:
    return _clean_text(value)[:max_length]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return default
