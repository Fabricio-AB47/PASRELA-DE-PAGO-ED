from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from base64 import b64encode
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.db import connection
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Flowable, Image as ReportLabImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.pdfgen import canvas as reportlab_canvas
from PIL import Image, ImageDraw, ImageFont

from .certificate_template import (
    MAX_COMPLEMENT_LOGOS,
    certificate_template_background_path,
    certificate_template_complement_logo_paths,
    certificate_template_type,
    certificate_template_use_default_logo,
)


class InscriptionCertificateError(Exception):
    pass


CERTIFICATE_CONTENT_TYPE = 'application/pdf'
CERTIFICATE_PREVIEW_IMAGE_CONTENT_TYPE = 'image/png'
CERTIFICATE_STORAGE_DIR_NAME = 'certificados_inscripcion'
CERTIFICATE_INSTITUTION_NAME = 'Instituto Superior Tecnológico de Técnicas Empresariales y del Conocimiento INTEC'
CERTIFICATE_SIGNING_SALT = 'dashboard_auth.inscription_certificate'
CERTIFICATE_VERSION = '2026-07-20-logo-signature-code-db-cuts-v31'
DEFAULT_COURSE_START_DATE = '20 de julio de 2026'
LOGO_FILE_NAME = 'Intec-Logowithslogangray.svg'
EMAIL_LOGO_FILE_NAME = 'Intec-Logowithslogangray.png'
EMAIL_LOGO_CONTENT_ID = 'intec-logo.png'
SIGNATURE_FILE_NAME = 'firma veronica.jpeg'
CERTIFICATE_CODE_PREFIX = 'INTEC-VGA-CER'
CERTIFICATE_CODE_PADDING = 3
CERTIFICATE_VERIFICATION_PATH = '/api/auth/certificates/verify/'


def build_certificate_payload(
    payload: dict[str, Any],
    result: dict[str, Any] | None = None,
    *,
    source: str = 'inscripcion',
) -> dict[str, Any]:
    provider_payload = payload.get('provider_payload') if isinstance(payload.get('provider_payload'), dict) else {}
    official_record = (
        result.get('official_sync', {}).get('record')
        if isinstance(result, dict) and isinstance(result.get('official_sync'), dict)
        else {}
    )
    if not isinstance(official_record, dict):
        official_record = {}

    matricula = _first_non_empty(
        result.get('matricula') if isinstance(result, dict) else None,
        payload.get('matricula'),
        official_record.get('codigo_estud'),
    )
    monto = _first_non_empty(
        result.get('monto') if isinstance(result, dict) else None,
        payload.get('monto'),
        provider_payload.get('monto'),
        '0.00' if source in {'matricula_masiva', 'matricula_academica'} else None,
    )
    payment_link = _first_non_empty(
        result.get('payment_link') if isinstance(result, dict) else None,
        payload.get('payment_link'),
        provider_payload.get('payment_link'),
    )
    course_name = _first_non_empty(
        official_record.get('materia'),
        payload.get('nombre_materia'),
        payload.get('materia'),
        provider_payload.get('nombre_materia'),
        provider_payload.get('materia'),
        payload.get('descripcion'),
    )

    data_treatment_accepted = bool(
        payload.get('data_treatment_accepted')
        or payload.get('dataTreatment') == 'si'
        or provider_payload.get('data_treatment_accepted')
        or source in {'matricula_masiva', 'matricula_academica'}
    )

    return {
        'source': source,
        'institution_name': _clean_text(payload.get('institution_name')),
        'nombre_materia': _clean_text(course_name),
        'codigo_materia': _clean_text(
            _first_non_empty(
                official_record.get('codigo_materia'),
                payload.get('codigo_materia'),
                provider_payload.get('codigo_materia'),
            )
        ),
        'matricula': _clean_text(matricula),
        'codigo_estud': _clean_text(_first_non_empty(official_record.get('codigo_estud'), payload.get('codigo_estud'))),
        'numero_matricula': _clean_text(
            _first_non_empty(official_record.get('num_matricula'), payload.get('numero_matricula'))
        ),
        'fecha_inscripcion': _first_non_empty(payload.get('fecha_inscripcion'), _today_label()),
        'fecha_inicio': _first_non_empty(
            official_record.get('fecha_inicio'),
            official_record.get('fecha_inicio_corte'),
            payload.get('fecha_inicio'),
            DEFAULT_COURSE_START_DATE,
        ),
        'nombre': _clean_text(_first_non_empty(payload.get('nombre'), provider_payload.get('nombre'))),
        'cedula': _clean_text(_first_non_empty(payload.get('cedula'), provider_payload.get('cedula'))),
        'email': _clean_text(_first_non_empty(payload.get('email'), provider_payload.get('email'))),
        'telefono': _clean_text(_first_non_empty(payload.get('telefono'), provider_payload.get('telefono'))),
        'localidad': _clean_text(_first_non_empty(payload.get('localidad'), provider_payload.get('localidad'))),
        'direccion': _clean_text(_first_non_empty(payload.get('direccion'), provider_payload.get('direccion'))),
        'codigo_periodo': _clean_text(
            _first_non_empty(payload.get('codigo_periodo'), provider_payload.get('codigo_periodo'))
        ),
        'cod_anio_basica': _clean_text(
            _first_non_empty(
                official_record.get('cod_anio_basica'),
                payload.get('cod_anio_basica'),
                provider_payload.get('cod_anio_basica'),
            )
        ),
        'estado_periodo': _clean_text(
            _first_non_empty(payload.get('estado_periodo'), provider_payload.get('estado_periodo'))
        ),
        'monto': _format_money(monto),
        'modalidad': _clean_text(_first_non_empty(payload.get('modalidad'), provider_payload.get('modalidad'), 'Virtual')),
        'payment_link': _clean_text(payment_link),
        'data_treatment_accepted': data_treatment_accepted,
        'inscripcion_aprobada': 'Sí',
        'codigo_certificado': _clean_text(
            _first_non_empty(payload.get('codigo_certificado'), payload.get('certificate_code'))
        ),
        'codigo_verificacion': _clean_text(payload.get('codigo_verificacion')),
        'corte_id': _clean_text(_first_non_empty(official_record.get('corte_id'), payload.get('corte_id'))),
        'numero_corte': _clean_text(_first_non_empty(official_record.get('numero_corte'), payload.get('numero_corte'))),
        'nombre_corte': _clean_text(_first_non_empty(official_record.get('nombre_corte'), payload.get('nombre_corte'))),
        'responsable_recepcion': 'Sistema de inscripciones INTEC',
        'observaciones_internas': _default_internal_observations(source, payment_link),
    }


def create_stored_certificate_record(payload: dict[str, Any]) -> dict[str, str]:
    certificate_payload = _ensure_certificate_code(_normalize_certificate_payload(payload))
    certificate_payload = _ensure_certificate_verification_code(certificate_payload)
    content, filename = build_inscription_certificate(certificate_payload)
    stored_filename = _safe_filename(filename)
    storage_path = _certificate_storage_dir() / stored_filename
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(content)
    stored_relative_path = f'{CERTIFICATE_STORAGE_DIR_NAME}/{stored_filename}'
    registry_record = _register_certificate_generation(certificate_payload, stored_relative_path)
    certificate_payload.update(registry_record)

    signed_payload = {
        **certificate_payload,
        'stored_filename': stored_filename,
        'stored_relative_path': stored_relative_path,
        'stored_at': timezone.now().isoformat(),
        'certificate_version': CERTIFICATE_VERSION,
    }
    return {
        'filename': stored_filename,
        'token': sign_certificate_payload(signed_payload),
        'stored_path': signed_payload['stored_relative_path'],
        'certificate_code': certificate_payload.get('codigo_certificado', ''),
        'verification_code': certificate_payload.get('codigo_verificacion', ''),
        'certificate_id': str(certificate_payload.get('certificado_id') or ''),
    }


def send_certificate_email(
    recipient_email: str,
    recipient_name: str,
    certificate_record: dict[str, str],
) -> dict[str, Any]:
    certificate_payload = load_signed_certificate_payload(str(certificate_record.get('token') or ''))
    certificate_content, certificate_filename = load_or_create_stored_certificate(certificate_payload)
    recipient_label = _clean_text(recipient_name) or _clean_text(recipient_email)
    safe_recipient_label = _safe_html(recipient_label)
    safe_course = _safe_html(_fallback(certificate_payload.get('nombre_materia')))
    safe_start_date = _safe_html(_fallback(certificate_payload.get('fecha_inicio')))
    certificate_label = _certificate_title(certificate_payload).capitalize()
    safe_certificate_label = _safe_html(certificate_label)
    logo_attachment = _build_email_logo_attachment()
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
                <h2 style="margin:0;font-size:22px;font-weight:700;">{safe_certificate_label}</h2>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 28px;color:#111827;">
                <p style="margin:0 0 12px 0;font-size:16px;">Hola {safe_recipient_label},</p>
                <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#374151;">
                  Adjuntamos tu {safe_certificate_label.lower()}. El documento corresponde al curso
                  <strong>{safe_course}</strong>, con fecha de inicio <strong>{safe_start_date}</strong>.
                </p>
                <p style="margin:0;font-size:13px;line-height:1.6;color:#6b7280;">
                  Conserva este PDF como soporte de tu registro institucional.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()

    attachments = []
    if logo_attachment:
        attachments.append(logo_attachment)
    attachments.append(
        {
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': certificate_filename,
            'contentType': CERTIFICATE_CONTENT_TYPE,
            'contentBytes': b64encode(certificate_content).decode('ascii'),
        }
    )

    mail_payload = {
        'message': {
            'subject': f'{certificate_label} INTEC',
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
            'attachments': attachments,
        },
        'saveToSentItems': True,
    }
    if certificate_payload.get('skip_default_cc'):
        mail_payload['_skip_default_cc'] = True

    from .payments import _send_graph_mail

    _send_graph_mail(mail_payload)
    return {
        'sent': True,
        'message': f'{certificate_label} enviado correctamente a {recipient_email}.',
        'filename': certificate_filename,
    }


def build_inscription_certificate(payload: dict[str, Any]) -> tuple[bytes, str]:
    certificate_payload = _ensure_certificate_code(_normalize_certificate_payload(payload))
    _validate_certificate_payload(certificate_payload)
    if _is_approval_certificate(certificate_payload) and _certificate_background_path(certificate_payload):
        return _build_background_approval_certificate(certificate_payload)

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.64 * inch,
        leftMargin=0.64 * inch,
        topMargin=0.48 * inch,
        bottomMargin=0.48 * inch,
        title='Certificado de Inscripción',
        author=certificate_payload.get('institution_name') or 'INTEC',
    )
    story = _build_pdf_story(certificate_payload)
    document.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return output.getvalue(), build_certificate_filename(certificate_payload)


def build_inscription_certificate_preview_image(payload: dict[str, Any]) -> tuple[bytes, str]:
    certificate_payload = _ensure_certificate_code(_normalize_certificate_payload(payload))
    _validate_certificate_payload(certificate_payload)
    background_path = _certificate_background_path(certificate_payload)
    if background_path and background_path.exists():
        image = Image.open(background_path).convert('RGB')
    else:
        image = Image.new('RGB', (2000, 1414), 'white')

    draw = ImageDraw.Draw(image)
    width, height = image.size
    _draw_preview_certificate_header_logos(image, certificate_payload)

    student_name = _clean_text(certificate_payload.get('nombre')).upper()
    student_identity = _student_identity_label(certificate_payload)
    course_name = _certificate_course_display_name(certificate_payload).upper()
    cut_name = _certificate_cut_display_name(certificate_payload, course_name)
    template_type = certificate_template_type(certificate_payload.get('corte_id'))
    descriptor = 'curso de educación continua' if template_type == 'EDUCACION_CONTINUA' else 'programa académico'

    _draw_preview_centered_text(
        draw,
        student_name,
        center_x=width / 2,
        y=height * 0.405,
        max_width=width * 0.64,
        font=_preview_font('bold', 40),
        fill='#9B0E0E',
    )
    if student_identity:
        _draw_preview_centered_text(
            draw,
            student_identity,
            center_x=width / 2,
            y=height * 0.447,
            max_width=width * 0.58,
            font=_preview_font('regular', 17),
            fill='#333333',
        )

    _draw_preview_centered_text(
        draw,
        course_name,
        center_x=width / 2,
        y=height * 0.59,
        max_width=width * 0.76,
        font=_preview_font('bold', _preview_course_font_size(course_name)),
        fill='#111111',
        max_lines=None,
    )

    detail_parts = [descriptor]
    if cut_name:
        detail_parts.append(cut_name)
    detail = ' · '.join(detail_parts)
    if detail:
        _draw_preview_centered_text(
            draw,
            detail,
            center_x=width / 2,
            y=height * 0.705,
            max_width=width * 0.58,
            font=_preview_font('regular', 15),
            fill='#555555',
        )

    meta = _approval_certificate_meta(certificate_payload)
    if meta:
        _draw_preview_centered_text(
            draw,
            meta,
            center_x=width / 2,
            y=height * 0.755,
            max_width=width * 0.56,
            font=_preview_font('regular', 14),
            fill='#555555',
        )

    certificate_code = _certificate_code(certificate_payload)
    if certificate_code:
        _draw_preview_centered_text(
            draw,
            f'Certificado No. {certificate_code}',
            center_x=width / 2,
            y=height * 0.955,
            max_width=width * 0.5,
            font=_preview_font('regular', 13),
            fill='#777777',
        )

    _draw_preview_certificate_qr(image, certificate_payload)

    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue(), build_certificate_filename(certificate_payload).replace('.pdf', '.png')


def load_or_create_stored_certificate(payload: dict[str, Any]) -> tuple[bytes, str]:
    certificate_payload = _normalize_certificate_payload(payload)
    _validate_certificate_payload(certificate_payload)

    filename = _safe_filename(certificate_payload.get('stored_filename') or build_certificate_filename(certificate_payload))
    storage_path = _certificate_storage_dir() / filename
    if storage_path.exists() and certificate_payload.get('certificate_version') == CERTIFICATE_VERSION:
        return storage_path.read_bytes(), filename

    certificate_payload = _ensure_certificate_code(certificate_payload)
    content, generated_filename = build_inscription_certificate(certificate_payload)
    filename = _safe_filename(generated_filename)
    storage_path = _certificate_storage_dir() / filename
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(content)
    return content, filename


def build_certificate_filename(payload: dict[str, Any]) -> str:
    certificate_code = _slug_part(_certificate_code(payload))
    matricula = _slug_part(payload.get('matricula')) or 'sin-codigo'
    cedula = _slug_part(_last_four_digits(payload.get('cedula'))) or 'sin-cedula'
    prefix = 'certificado_aprobacion' if _is_approval_certificate(payload) else 'certificado_inscripcion'
    if certificate_code:
        return f'{prefix}_{certificate_code}_{matricula}_{cedula}.pdf'
    return f'{prefix}_{matricula}_{cedula}.pdf'


def sign_certificate_payload(payload: dict[str, Any]) -> str:
    return signing.dumps(payload, salt=CERTIFICATE_SIGNING_SALT, compress=True)


def load_signed_certificate_payload(token: str) -> dict[str, Any]:
    try:
        payload = signing.loads(token, salt=CERTIFICATE_SIGNING_SALT)
    except signing.BadSignature as exc:
        raise InscriptionCertificateError(
            'El certificado solicitado no coincide con un registro firmado por el sistema.'
        ) from exc

    if not isinstance(payload, dict):
        raise InscriptionCertificateError('El certificado solicitado no contiene datos válidos.')
    return payload


def verify_certificate_record(certificate_code: Any, verification_code: Any) -> dict[str, Any]:
    certificate_number = _clean_text(certificate_code)
    verification = _clean_text(verification_code)
    if not certificate_number or not verification:
        raise InscriptionCertificateError('Debes enviar el número de certificado y el código de verificación.')

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TOP (1)
                CertificadoId,
                TipoCertificado,
                TipoOrigen,
                NumeroCertificado,
                CodigoEstud,
                CedulaEst,
                ApellidosNombre,
                Cod_AnioBasica,
                CodigoPeriodo,
                CodigoMateria,
                Num_Matricula,
                CodCurso,
                CONVERT(varchar(19), FechaGeneracion, 120) AS FechaGeneracion,
                Estado,
                Observacion
            FROM dbo.CERTIFICADOS_GENERADOS
            WHERE NumeroCertificado = %s
              AND CodigoVerificacion = %s
            ORDER BY FechaGeneracion DESC, CertificadoId DESC
            """,
            [certificate_number, verification],
        )
        row = cursor.fetchone()
        if not row:
            return {
                'valid': False,
                'message': 'No se encontró un certificado activo con los datos de verificación enviados.',
                'certificate': None,
            }
        columns = [column[0] for column in cursor.description]

    record = {columns[index]: row[index] for index in range(len(columns))}
    status = _clean_text(record.get('Estado')).upper()
    is_valid = status == 'GENERADO'
    return {
        'valid': is_valid,
        'message': 'Certificado válido.' if is_valid else f'Certificado con estado {status or "NO REGISTRADO"}.',
        'certificate': {
            'certificado_id': _clean_text(record.get('CertificadoId')),
            'tipo_certificado': _clean_text(record.get('TipoCertificado')),
            'tipo_origen': _clean_text(record.get('TipoOrigen')),
            'numero_certificado': _clean_text(record.get('NumeroCertificado')),
            'codigo_estud': _clean_text(record.get('CodigoEstud')),
            'cedula_mascara': _masked_identity(record.get('CedulaEst')),
            'estudiante': _clean_text(record.get('ApellidosNombre')),
            'cod_anio_basica': _clean_text(record.get('Cod_AnioBasica')),
            'codigo_periodo': _clean_text(record.get('CodigoPeriodo')),
            'codigo_materia': _clean_text(record.get('CodigoMateria')),
            'num_matricula': _clean_text(record.get('Num_Matricula')),
            'cod_curso': _clean_text(record.get('CodCurso')),
            'fecha_generacion': _clean_text(record.get('FechaGeneracion')),
            'estado': status,
            'observacion': _clean_text(record.get('Observacion')),
        },
    }


def _normalize_certificate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    certificate_payload = build_certificate_payload(payload) if not payload.get('nombre') else dict(payload)
    certificate_payload.setdefault('fecha_inicio', DEFAULT_COURSE_START_DATE)
    certificate_payload.setdefault('responsable_recepcion', 'Sistema de inscripciones INTEC')
    certificate_payload.setdefault('observaciones_internas', _default_internal_observations(
        _clean_text(certificate_payload.get('source')) or 'inscripcion',
        _clean_text(certificate_payload.get('payment_link')),
    ))
    certificate_payload.setdefault('monto', '$ 0.00')
    certificate_payload.setdefault('modalidad', 'Virtual')
    certificate_code = _certificate_code(certificate_payload)
    if certificate_code:
        certificate_payload['codigo_certificado'] = certificate_code
        certificate_payload['certificate_code'] = certificate_code
    return certificate_payload


class SvgLogoFlowable(Flowable):
    def __init__(self, svg_path: Path, width: float):
        super().__init__()
        self.svg_path = svg_path
        self.view_width, self.view_height, self.paths = _parse_svg_paths(svg_path)
        self.width = width
        self.height = width * (self.view_height / self.view_width)
        self.hAlign = 'CENTER'

    def wrap(self, _avail_width: float, _avail_height: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        scale = self.width / self.view_width
        self.canv.saveState()
        self.canv.scale(scale, scale)
        for svg_path in self.paths:
            path = self.canv.beginPath()
            for command in svg_path['commands']:
                command_name = command[0]
                if command_name == 'M':
                    path.moveTo(command[1], self.view_height - command[2])
                elif command_name == 'L':
                    path.lineTo(command[1], self.view_height - command[2])
                elif command_name == 'C':
                    path.curveTo(
                        command[1],
                        self.view_height - command[2],
                        command[3],
                        self.view_height - command[4],
                        command[5],
                        self.view_height - command[6],
                    )
                elif command_name == 'Z':
                    path.close()

            self.canv.setFillColor(svg_path['fill'])
            self.canv.drawPath(path, fill=1, stroke=0)
        self.canv.restoreState()


def _build_background_approval_certificate(payload: dict[str, Any]) -> tuple[bytes, str]:
    output = BytesIO()
    page_size = landscape(A4)
    pdf = reportlab_canvas.Canvas(output, pagesize=page_size)
    width, height = page_size
    background_path = _certificate_background_path(payload)
    if background_path:
        pdf.drawImage(str(background_path), 0, 0, width=width, height=height, preserveAspectRatio=False, mask='auto')

    _draw_certificate_header_logos(pdf, payload, width, height)
    student_name = _clean_text(payload.get('nombre')).upper()
    student_identity = _student_identity_label(payload)
    course_name = _certificate_course_display_name(payload).upper()
    cut_name = _certificate_cut_display_name(payload, course_name)
    certificate_code = _certificate_code(payload)
    template_type = certificate_template_type(payload.get('corte_id'))
    descriptor = 'curso de educación continua' if template_type == 'EDUCACION_CONTINUA' else 'programa académico'

    _draw_centered_wrapped_text(
        pdf,
        student_name,
        center_x=width / 2,
        y=height * 0.585,
        max_width=width * 0.64,
        font_name='Helvetica-Bold',
        font_size=24,
        leading=28,
        color=colors.HexColor('#9B0E0E'),
    )
    if student_identity:
        _draw_centered_wrapped_text(
            pdf,
            student_identity,
            center_x=width / 2,
            y=height * 0.535,
            max_width=width * 0.58,
            font_name='Helvetica',
            font_size=11,
            leading=13,
            color=colors.HexColor('#333333'),
        )

    _draw_centered_wrapped_text(
        pdf,
        course_name,
        center_x=width / 2,
        y=height * 0.385,
        max_width=width * 0.76,
        font_name='Helvetica-Bold',
        font_size=_certificate_course_font_size(course_name),
        leading=_certificate_course_leading(course_name),
        max_lines=None,
        color=colors.HexColor('#111111'),
    )

    detail_parts = [descriptor]
    if cut_name:
        detail_parts.append(cut_name)
    detail = ' · '.join(detail_parts)
    if detail:
        _draw_centered_wrapped_text(
            pdf,
            detail,
            center_x=width / 2,
            y=height * 0.315,
            max_width=width * 0.58,
            font_name='Helvetica',
            font_size=10.5,
            leading=13,
            color=colors.HexColor('#555555'),
        )

    meta = _approval_certificate_meta(payload)
    if meta:
        _draw_centered_wrapped_text(
            pdf,
            meta,
            center_x=width / 2,
            y=height * 0.265,
            max_width=width * 0.56,
            font_name='Helvetica',
            font_size=9.5,
            leading=12,
            color=colors.HexColor('#555555'),
        )

    if certificate_code:
        pdf.setFillColor(colors.HexColor('#777777'))
        pdf.setFont('Helvetica', 8)
        pdf.drawCentredString(width / 2, 0.18 * inch, f'Certificado No. {certificate_code}')

    _draw_certificate_qr(pdf, payload, width, height)

    pdf.showPage()
    pdf.save()
    return output.getvalue(), build_certificate_filename(payload)


def _draw_certificate_header_logos(pdf, payload: dict[str, Any], width: float, height: float) -> None:
    _clear_pdf_embedded_default_logo(pdf, width, height)
    if _should_draw_default_header_logo(payload):
        _draw_pdf_primary_education_logo(pdf, payload, width, height)
    _draw_cut_logos(pdf, payload, width, height)


def _clear_pdf_embedded_default_logo(pdf, width: float, height: float) -> None:
    pdf.saveState()
    pdf.setFillColor(colors.white)
    pdf.rect(width * 0.62, height * 0.72, width * 0.36, height * 0.24, fill=1, stroke=0)
    pdf.restoreState()


def _draw_pdf_primary_education_logo(pdf, payload: dict[str, Any], width: float, height: float) -> None:
    logo_path = _email_logo_path()
    if not logo_path:
        return
    has_complement_logos = bool(certificate_template_complement_logo_paths(payload.get('corte_id')))
    text_center_x = width * (0.31 if has_complement_logos else 0.23)
    divider_x = width * (0.48 if has_complement_logos else 0.445)
    logo_x = width * (0.505 if has_complement_logos else 0.47)
    logo_y = height - 1.21 * inch

    pdf.saveState()
    pdf.setFillColor(colors.HexColor('#777777'))
    pdf.setFont('Helvetica', 15.5)
    pdf.drawCentredString(text_center_x, height - 0.64 * inch, 'Escuela de Educación en')
    pdf.drawCentredString(text_center_x, height - 0.93 * inch, 'Línea y Educación Continua')
    pdf.setStrokeColor(colors.HexColor('#333333'))
    pdf.setLineWidth(1.4)
    pdf.line(divider_x, height - 1.18 * inch, divider_x, height - 0.35 * inch)
    pdf.restoreState()

    _draw_image_fit_box(
        pdf,
        logo_path,
        logo_x,
        logo_y,
        2.32 * inch,
        0.86 * inch,
        horizontal='left',
        transparent_white=True,
    )


def _draw_cut_logos(pdf, payload: dict[str, Any], width: float, height: float) -> None:
    logos = certificate_template_complement_logo_paths(payload.get('corte_id'))
    if not logos:
        return
    left_logos, right_logos = _split_logos_for_extremes(logos[:MAX_COMPLEMENT_LOGOS])
    area_width = 1.95 * inch
    area_height = 0.86 * inch
    area_top = height - 0.36 * inch
    _draw_pdf_logo_grid(
        pdf,
        left_logos,
        x=0.38 * inch,
        area_top=area_top,
        area_width=area_width,
        area_height=area_height,
        horizontal='left',
    )
    _draw_pdf_logo_grid(
        pdf,
        right_logos,
        x=width - 0.38 * inch - area_width,
        area_top=area_top,
        area_width=area_width,
        area_height=area_height,
        horizontal='right',
    )


def _split_logos_for_extremes(logos: list[Path]) -> tuple[list[Path], list[Path]]:
    return logos[0::2], logos[1::2]


def _logo_grid_dimensions(count: int) -> tuple[int, int]:
    if count <= 1:
        return 1, 1
    columns = 2
    rows = (count + columns - 1) // columns
    return columns, rows


def _draw_pdf_logo_grid(
    pdf,
    logos: list[Path],
    *,
    x: float,
    area_top: float,
    area_width: float,
    area_height: float,
    horizontal: str,
) -> None:
    if not logos:
        return
    columns, rows = _logo_grid_dimensions(len(logos))
    gap_x = 0.08 * inch if columns > 1 else 0
    gap_y = 0.06 * inch if rows > 1 else 0
    cell_width = max(0.1 * inch, (area_width - (gap_x * (columns - 1))) / columns)
    cell_height = max(0.1 * inch, (area_height - (gap_y * (rows - 1))) / rows)
    for index, logo_path in enumerate(logos):
        row = index // columns
        column = index % columns
        cell_x = x + (column * (cell_width + gap_x))
        cell_y = area_top - cell_height - (row * (cell_height + gap_y))
        _draw_image_fit_box(pdf, logo_path, cell_x, cell_y, cell_width, cell_height, horizontal=horizontal)


def _draw_image_fit_box(
    pdf,
    image_path: Path,
    x: float,
    y: float,
    box_width: float,
    box_height: float,
    *,
    horizontal: str = 'left',
    transparent_white: bool = False,
) -> None:
    try:
        image_reader = ImageReader(_transparent_white_image(image_path) if transparent_white else str(image_path))
        image_width, image_height = image_reader.getSize()
        if image_width <= 0 or image_height <= 0:
            return
        scale = min(box_width / image_width, box_height / image_height)
        draw_width = image_width * scale
        draw_height = image_height * scale
        if horizontal == 'right':
            draw_x = x + box_width - draw_width
        elif horizontal == 'center':
            draw_x = x + ((box_width - draw_width) / 2)
        else:
            draw_x = x
        draw_y = y + ((box_height - draw_height) / 2)
        pdf.drawImage(
            image_reader,
            draw_x,
            draw_y,
            width=draw_width,
            height=draw_height,
            preserveAspectRatio=False,
            mask='auto',
        )
    except Exception:
        return


def _draw_certificate_qr(pdf, payload: dict[str, Any], width: float, height: float) -> None:
    qr_image = _certificate_qr_image(payload, box_size=8)
    qr_size = 0.92 * inch
    qr_x = (width - qr_size) / 2
    qr_y = 0.38 * inch
    pdf.drawImage(ImageReader(qr_image), qr_x, qr_y, width=qr_size, height=qr_size, mask='auto')


def _draw_preview_certificate_header_logos(image: Image.Image, payload: dict[str, Any]) -> None:
    _clear_preview_embedded_default_logo(image)
    if _should_draw_default_header_logo(payload):
        _draw_preview_primary_education_logo(image, payload)
    _draw_preview_complement_logos(image, payload)


def _draw_preview_certificate_qr(image: Image.Image, payload: dict[str, Any]) -> None:
    qr_image = _certificate_qr_image(payload, box_size=10)
    width, height = image.size
    qr_size = int(width * 0.09)
    qr_x = (width - qr_size) // 2
    qr_y = height - int(height * 0.225)
    qr_image = qr_image.resize((qr_size, qr_size), Image.Resampling.NEAREST)
    image.paste(qr_image, (qr_x, qr_y))


def _clear_preview_embedded_default_logo(image: Image.Image) -> None:
    width, height = image.size
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (
            int(width * 0.62),
            int(height * 0.04),
            int(width * 0.98),
            int(height * 0.28),
        ),
        fill='white',
    )


def _draw_preview_primary_education_logo(image: Image.Image, payload: dict[str, Any]) -> None:
    logo_path = _email_logo_path()
    if not logo_path:
        return
    width, height = image.size
    has_complement_logos = bool(certificate_template_complement_logo_paths(payload.get('corte_id')))
    text_center_x = int(width * (0.31 if has_complement_logos else 0.23))
    divider_x = int(width * (0.48 if has_complement_logos else 0.445))
    logo_x = int(width * (0.505 if has_complement_logos else 0.47))
    logo_y = int(height * 0.075)

    draw = ImageDraw.Draw(image)
    font = _preview_font('regular', 42)
    fill = '#777777'
    for offset, line in enumerate(('Escuela de Educación en', 'Línea y Educación Continua')):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        draw.text(
            (text_center_x - (line_width / 2), int(height * 0.07) + (offset * int(height * 0.044))),
            line,
            font=font,
            fill=fill,
        )

    draw.line(
        (
            divider_x,
            int(height * 0.055),
            divider_x,
            int(height * 0.155),
        ),
        fill='#333333',
        width=max(2, int(width * 0.002)),
    )
    _paste_preview_image_fit_box(
        image,
        logo_path,
        logo_x,
        logo_y,
        int(width * 0.22),
        int(height * 0.105),
        horizontal='left',
        transparent_white=True,
    )


def _draw_preview_complement_logos(image: Image.Image, payload: dict[str, Any]) -> None:
    logo_paths = certificate_template_complement_logo_paths(payload.get('corte_id'))
    if not logo_paths:
        return

    width, height = image.size
    left_logos, right_logos = _split_logos_for_extremes(logo_paths[:MAX_COMPLEMENT_LOGOS])
    area_width = int(width * 0.14)
    area_height = int(height * 0.125)
    area_y = int(height * 0.055)
    _paste_preview_logo_grid(
        image,
        left_logos,
        x=int(width * 0.035),
        y=area_y,
        area_width=area_width,
        area_height=area_height,
        horizontal='left',
    )
    _paste_preview_logo_grid(
        image,
        right_logos,
        x=width - int(width * 0.035) - area_width,
        y=area_y,
        area_width=area_width,
        area_height=area_height,
        horizontal='right',
    )


def _paste_preview_logo_grid(
    image: Image.Image,
    logos: list[Path],
    *,
    x: int,
    y: int,
    area_width: int,
    area_height: int,
    horizontal: str,
) -> None:
    if not logos:
        return
    columns, rows = _logo_grid_dimensions(len(logos))
    gap_x = int(image.size[0] * 0.006) if columns > 1 else 0
    gap_y = int(image.size[1] * 0.008) if rows > 1 else 0
    cell_width = max(16, (area_width - (gap_x * (columns - 1))) // columns)
    cell_height = max(16, (area_height - (gap_y * (rows - 1))) // rows)
    for index, logo_path in enumerate(logos):
        row = index // columns
        column = index % columns
        cell_x = x + (column * (cell_width + gap_x))
        cell_y = y + (row * (cell_height + gap_y))
        _paste_preview_image_fit_box(
            image,
            logo_path,
            cell_x,
            cell_y,
            cell_width,
            cell_height,
            horizontal=horizontal,
        )


def _paste_preview_image_fit_box(
    image: Image.Image,
    image_path: Path,
    x: int,
    y: int,
    box_width: int,
    box_height: int,
    *,
    horizontal: str = 'left',
    transparent_white: bool = False,
) -> None:
    try:
        logo = Image.open(image_path).convert('RGBA')
    except Exception:
        return
    if logo.width <= 0 or logo.height <= 0:
        return
    if transparent_white:
        logo = _transparent_white_pil_image(logo)
    scale = min(box_width / logo.width, box_height / logo.height)
    draw_width = max(1, int(logo.width * scale))
    draw_height = max(1, int(logo.height * scale))
    logo = logo.resize((draw_width, draw_height), Image.Resampling.LANCZOS)
    if horizontal == 'right':
        draw_x = x + box_width - draw_width
    elif horizontal == 'center':
        draw_x = x + ((box_width - draw_width) // 2)
    else:
        draw_x = x
    draw_y = y + ((box_height - draw_height) // 2)
    image.paste(logo, (draw_x, draw_y), logo)


def _should_draw_default_header_logo(payload: dict[str, Any]) -> bool:
    try:
        return certificate_template_use_default_logo(payload.get('corte_id'))
    except Exception:
        return True


def _transparent_white_image(image_path: Path) -> Image.Image:
    image = Image.open(image_path).convert('RGBA')
    return _transparent_white_pil_image(image)


def _transparent_white_pil_image(image: Image.Image) -> Image.Image:
    converted = image.convert('RGBA')
    pixels = []
    for red, green, blue, alpha in converted.getdata():
        if red > 245 and green > 245 and blue > 245:
            pixels.append((red, green, blue, 0))
        else:
            pixels.append((red, green, blue, alpha))
    converted.putdata(pixels)
    return converted


def _certificate_qr_image(payload: dict[str, Any], *, box_size: int) -> Image.Image:
    try:
        import qrcode
    except ImportError as exc:
        raise InscriptionCertificateError(
            'No está instalada la dependencia qrcode para generar el código QR del certificado.'
        ) from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box_size,
        border=4,
    )
    qr.add_data(_certificate_qr_payload(payload))
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color='black', back_color='white')
    if hasattr(qr_image, 'get_image'):
        qr_image = qr_image.get_image()
    return qr_image.convert('RGB')


def _certificate_qr_payload(payload: dict[str, Any]) -> str:
    certificate_code = _certificate_code(payload) or 'VISTA-PREVIA'
    verification_code = _certificate_verification_code(payload)
    if (
        not verification_code
        or certificate_code.upper() == 'VISTA-PREVIA'
        or verification_code.upper() == 'VISTA-PREVIA'
    ):
        return (
            'INTEC CERTIFICADO - VISTA PREVIA SIN VALIDEZ OFICIAL\n'
            f'Número: {certificate_code}'
        )
    query = urlencode(
        {
            'numero': certificate_code,
            'verificacion': verification_code,
        }
    )
    base_url = _certificate_verification_base_url()
    verification_url = f'{base_url}{CERTIFICATE_VERIFICATION_PATH}?{query}'
    if _is_local_verification_base_url(base_url):
        return _certificate_qr_summary(payload, verification_url)
    return verification_url


def _certificate_qr_summary(payload: dict[str, Any], verification_url: str) -> str:
    student = _trim_to_max(payload.get('nombre'), 90)
    course = _trim_to_max(_certificate_course_display_name(payload), 110)
    certificate_code = _certificate_code(payload)
    verification_code = _certificate_verification_code(payload)
    identity = _masked_identity(payload.get('cedula'))
    issued_at = _clean_text(_first_non_empty(payload.get('fecha_inscripcion'), _today_label()))
    lines = [
        'INTEC - VERIFICACION DE CERTIFICADO',
        f'Numero: {certificate_code}',
        f'Codigo: {verification_code}',
        f'Estudiante: {student or "No registrado"}',
        f'Cedula: {identity or "No registrada"}',
        f'Curso: {course or "No registrado"}',
        f'Emision: {issued_at}',
        f'Validar: {verification_url}',
    ]
    return '\n'.join(lines)


def _is_local_verification_base_url(base_url: str) -> bool:
    normalized = _clean_text(base_url).lower()
    return (
        '://127.0.0.1' in normalized
        or '://localhost' in normalized
        or '://0.0.0.0' in normalized
    )


def _certificate_verification_base_url() -> str:
    configured_url = _clean_text(
        _first_non_empty(
            os.getenv('CERTIFICATE_VERIFICATION_BASE_URL'),
            os.getenv('PUBLIC_BASE_URL'),
            os.getenv('APP_BASE_URL'),
        )
    )
    if configured_url:
        return configured_url.rstrip('/')
    return 'https://intec.edu.ec'


def _draw_preview_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    center_x: float,
    y: float,
    max_width: float,
    font: ImageFont.ImageFont,
    fill: str,
    max_lines: int | None = 3,
) -> None:
    clean_text = _clean_text(text)
    if not clean_text:
        return
    lines = _wrap_preview_text(draw, clean_text, font, max_width, max_lines=max_lines)
    line_height = _preview_line_height(font)
    start_y = y - (((len(lines) - 1) * line_height) / 2)
    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        draw.text((center_x - (line_width / 2), start_y + (index * line_height)), line, font=font, fill=fill)


def _wrap_preview_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: float,
    *,
    max_lines: int | None = 3,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        candidate_width = draw.textbbox((0, 0), candidate, font=font)[2]
        if not current or candidate_width <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    if max_lines is None:
        return lines or ['']
    return lines[:max(1, max_lines)] or ['']


def _preview_font(weight: str, size: int) -> ImageFont.ImageFont:
    font_name = 'arialbd.ttf' if weight == 'bold' else 'arial.ttf'
    candidates = [
        Path('C:/Windows/Fonts') / font_name,
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if weight == 'bold' else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _preview_line_height(font: ImageFont.ImageFont) -> int:
    bbox = font.getbbox('Ag')
    return max(1, int((bbox[3] - bbox[1]) * 1.18))


def _preview_course_font_size(course_name: str) -> int:
    length = len(_clean_text(course_name))
    if length > 120:
        return 24
    if length > 95:
        return 27
    if length > 55:
        return 31
    return 39


def _draw_centered_wrapped_text(
    pdf,
    text: str,
    *,
    center_x: float,
    y: float,
    max_width: float,
    font_name: str,
    font_size: float,
    leading: float,
    color,
    max_lines: int | None = 3,
) -> None:
    clean_text = _clean_text(text)
    if not clean_text:
        return
    lines = _wrap_canvas_text(pdf, clean_text, font_name, font_size, max_width, max_lines=max_lines)
    start_y = y + ((len(lines) - 1) * leading / 2)
    pdf.setFillColor(color)
    pdf.setFont(font_name, font_size)
    for index, line in enumerate(lines):
        pdf.drawCentredString(center_x, start_y - (index * leading), line)


def _wrap_canvas_text(
    pdf,
    text: str,
    font_name: str,
    font_size: float,
    max_width: float,
    *,
    max_lines: int | None = 3,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        if not current or pdf.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    if max_lines is None:
        return lines or ['']
    return lines[:max(1, max_lines)] or ['']


def _certificate_course_font_size(course_name: str) -> float:
    length = len(_clean_text(course_name))
    if length > 120:
        return 12.8
    if length > 95:
        return 13.8
    if length > 55:
        return 15
    return 20


def _certificate_course_leading(course_name: str) -> float:
    return _certificate_course_font_size(course_name) + 2.8


def _certificate_course_display_name(payload: dict[str, Any]) -> str:
    candidates = [
        payload.get('nombre_materia'),
        payload.get('materia'),
        payload.get('curso_educontinua'),
        _strip_certificate_cut_prefix(payload.get('nombre_corte')),
    ]
    cleaned = [_clean_text(candidate) for candidate in candidates if _clean_text(candidate)]
    if not cleaned:
        return 'Curso de educación continua'
    return max(cleaned, key=len)


def _certificate_cut_display_name(payload: dict[str, Any], course_name: str) -> str:
    raw_cut_name = _clean_text(_first_non_empty(payload.get('nombre_corte'), payload.get('codigo_periodo')))
    if not raw_cut_name:
        return ''
    stripped_course = _strip_certificate_cut_prefix(raw_cut_name)
    course_key = _clean_text(course_name).lower()
    stripped_key = stripped_course.lower()
    if '-' in raw_cut_name and course_key and (stripped_key == course_key or stripped_key in course_key or course_key in stripped_key):
        return raw_cut_name.split('-', 1)[0].strip()
    return raw_cut_name


def _strip_certificate_cut_prefix(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ''
    parts = text.split('-', 1)
    if len(parts) == 2 and 'corte' in parts[0].lower():
        return parts[1].strip()
    return text


def _approval_certificate_meta(payload: dict[str, Any]) -> str:
    parts = []
    grade = _clean_text(payload.get('nota_final'))
    attendance = _clean_text(payload.get('porcentaje_asistencia'))
    if grade:
        parts.append(f'Nota final: {grade}')
    if attendance:
        parts.append(f'Asistencia: {attendance}')
    return ' · '.join(parts)


def _student_identity_label(payload: dict[str, Any]) -> str:
    cedula = _clean_text(payload.get('cedula'))
    if not cedula:
        return ''
    return f'Estudiante con número de cédula de identidad: {cedula}'


def _certificate_background_path(payload: dict[str, Any]) -> Path | None:
    try:
        return certificate_template_background_path(payload.get('corte_id'))
    except Exception:
        return None


def _build_pdf_story(payload: dict[str, Any]) -> list[Any]:
    styles = _pdf_styles()
    story: list[Any] = []
    header_flowables = _certificate_header_flowables()
    if header_flowables:
        story.extend([*header_flowables, Spacer(1, 0.07 * inch)])

    certificate_code = _certificate_code(payload)
    if certificate_code:
        story.extend([Paragraph(_safe_html(certificate_code), styles['CertificateCode']), Spacer(1, 0.04 * inch)])

    story.extend(
        [
            Paragraph(_certificate_title(payload), styles['CertificateTitle']),
            Spacer(1, 0.14 * inch),
            Paragraph(_certificate_statement(payload), styles['BodyJustified']),
            Spacer(1, 0.12 * inch),
        ]
    )

    story.extend(
        _section_table(
            'DATOS DEL ESTUDIANTE',
            [
                ('Nombre completo', payload.get('nombre')),
                ('Cédula de ciudadanía/identidad/pasaporte', payload.get('cedula')),
                ('Correo electrónico', payload.get('email')),
                ('Teléfono', payload.get('telefono')),
                ('Ciudad / localidad', payload.get('localidad')),
                ('Dirección', payload.get('direccion')),
            ],
            styles,
        )
    )
    story.extend(
        _section_table(
            'DATOS ACADÉMICOS' if _is_approval_certificate(payload) else 'DATOS DE LA INSCRIPCIÓN',
            _academic_certificate_rows(payload)
            if _is_approval_certificate(payload)
            else [
                ('Curso inscrito', payload.get('nombre_materia')),
                ('Modalidad', payload.get('modalidad')),
                ('Fecha de inicio del curso', payload.get('fecha_inicio')),
                ('Fecha de registro', payload.get('fecha_inscripcion')),
                ('Estado', 'Inscrito'),
            ],
            styles,
        )
    )

    story.append(Spacer(1, 0.08 * inch))
    if _is_approval_certificate(payload):
        story.append(
            Paragraph(
                'Este certificado se emite para avalar la aprobación del participante conforme a los datos '
                f"académicos disponibles al momento de su generación. Fecha de emisión: "
                f"{_safe_html(_fallback(payload.get('fecha_inscripcion')))}.",
                styles['BodyJustified'],
            )
        )
    else:
        story.append(
            Paragraph(
                'Este certificado se emite para avalar el estado de inscripción del participante '
                f"conforme a los datos disponibles al momento de su generación. Fecha de emisión: "
                f"{_safe_html(_fallback(payload.get('fecha_inscripcion')))}.",
                styles['BodyJustified'],
            )
        )
    story.extend(_signature_block(styles))
    story.extend(_certificate_qr_block(payload, styles))
    return story


def _section_table(title: str, rows: list[tuple[str, Any]], styles: dict[str, ParagraphStyle]) -> list[Any]:
    data = [
        [
            Paragraph(_safe_html(label), styles['TableLabel']),
            Paragraph(_safe_html(_fallback(value)), styles['TableValue']),
        ]
        for label, value in rows
    ]
    table = Table(data, colWidths=[2.1 * inch, 4.95 * inch], hAlign='LEFT')
    table.setStyle(
        TableStyle(
            [
                ('GRID', (0, 0), (-1, -1), 0.45, colors.HexColor('#C7C6C6')),
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F2F7F8')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]
        )
    )
    return [
        Paragraph(_safe_html(title), styles['SectionTitle']),
        Spacer(1, 0.05 * inch),
        table,
        Spacer(1, 0.09 * inch),
    ]


def _certificate_qr_block(payload: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    qr_image = _certificate_qr_image(payload, box_size=7)
    image_buffer = BytesIO()
    qr_image.save(image_buffer, format='PNG')
    image_buffer.seek(0)
    qr_flowable = ReportLabImage(image_buffer, width=0.82 * inch, height=0.82 * inch)
    qr_flowable.hAlign = 'RIGHT'
    table = Table([['', qr_flowable]], colWidths=[5.95 * inch, 0.95 * inch], hAlign='LEFT')
    table.setStyle(
        TableStyle(
            [
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]
        )
    )
    return [Spacer(1, 0.06 * inch), table]


def _signature_block(styles: dict[str, ParagraphStyle]) -> list[Any]:
    signature = _signature_flowable()
    if not signature:
        return []

    table = Table(
        [[signature]],
        colWidths=[3.15 * inch],
        rowHeights=[0.92 * inch],
        hAlign='CENTER',
    )
    table.setStyle(
        TableStyle(
            [
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [Spacer(1, 0.1 * inch), table]


def _certificate_statement(payload: dict[str, Any]) -> str:
    institution = _safe_html(_fallback(_first_non_empty(payload.get('institution_name'), CERTIFICATE_INSTITUTION_NAME)))
    student = _safe_html(_fallback(payload.get('nombre')))
    cedula = _safe_html(_fallback(payload.get('cedula')))
    course = _safe_html(_fallback(payload.get('nombre_materia')))
    start_date = _safe_html(_fallback(payload.get('fecha_inicio')))
    if _is_approval_certificate(payload):
        grade = _safe_html(_fallback(payload.get('nota_final')))
        attendance = _safe_html(_fallback(payload.get('porcentaje_asistencia')))
        period = _safe_html(_fallback(_first_non_empty(payload.get('nombre_corte'), payload.get('codigo_periodo'))))
        return (
            f'Por medio del presente, el <b>{institution}</b> certifica que el/la señor/a/ita&nbsp;'
            f'<b>{student}</b>, portador de la cédula de ciudadanía/identidad/pasaporte: <b>{cedula}</b>, '
            f'aprobó el curso <b>{course}</b>, correspondiente a <b>{period}</b>, con nota final '
            f'<b>{grade}</b> y asistencia registrada de <b>{attendance}</b>.'
        )
    return (
        f'Por medio del presente, el <b>{institution}</b> certifica que el/la señor/a/ita&nbsp;'
        f'<b>{student}</b>, portador de la cédula de ciudadanía/identidad/pasaporte: <b>{cedula}</b>, se encuentra legalmente '
        f'inscrito/a en el curso <b>{course}</b>, que inicia el <b>{start_date}</b>, '
        'de acuerdo con la información registrada en el sistema '
        'institucional académico.'
    )


def _academic_certificate_rows(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        ('Curso aprobado', payload.get('nombre_materia')),
        ('Corte o período', _first_non_empty(payload.get('nombre_corte'), payload.get('codigo_periodo'))),
        ('Modalidad', payload.get('modalidad')),
        ('Fecha de inicio del curso', payload.get('fecha_inicio')),
        ('Nota final', payload.get('nota_final')),
        ('Asistencia', payload.get('porcentaje_asistencia')),
        ('Estado', 'Aprobado'),
    ]


def _certificate_title(payload: dict[str, Any]) -> str:
    if _is_approval_certificate(payload):
        return 'CERTIFICADO DE APROBACIÓN'
    return 'CERTIFICADO DE INSCRIPCIÓN'


def _is_approval_certificate(payload: dict[str, Any]) -> bool:
    certificate_type = _clean_text(_first_non_empty(payload.get('tipo_certificado'), payload.get('certificate_type')))
    normalized = certificate_type.upper().replace('Ó', 'O')
    return normalized in {'APROBACION', 'APROBADO', 'CERTIFICADO_APROBACION'}


def _pdf_styles() -> dict[str, ParagraphStyle]:
    base_styles = getSampleStyleSheet()
    return {
        'CertificateTitle': ParagraphStyle(
            'CertificateTitle',
            parent=base_styles['Title'],
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
            fontSize=15,
            leading=18,
            textColor=colors.HexColor('#931913'),
            spaceAfter=6,
        ),
        'Institution': ParagraphStyle(
            'Institution',
            parent=base_styles['Normal'],
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
            fontSize=9,
            leading=11,
            textColor=colors.HexColor('#777777'),
        ),
        'CertificateCode': ParagraphStyle(
            'CertificateCode',
            parent=base_styles['Normal'],
            alignment=TA_RIGHT,
            fontName='Helvetica-Bold',
            fontSize=9.2,
            leading=11,
            textColor=colors.HexColor('#333333'),
        ),
        'QrLabel': ParagraphStyle(
            'QrLabel',
            parent=base_styles['Normal'],
            alignment=TA_RIGHT,
            fontName='Helvetica',
            fontSize=7.4,
            leading=9,
            textColor=colors.HexColor('#555555'),
        ),
        'BodyJustified': ParagraphStyle(
            'BodyJustified',
            parent=base_styles['BodyText'],
            alignment=TA_JUSTIFY,
            fontName='Helvetica',
            fontSize=8.8,
            leading=12.2,
            textColor=colors.HexColor('#333333'),
        ),
        'SignatureInstitution': ParagraphStyle(
            'SignatureInstitution',
            parent=base_styles['Normal'],
            alignment=TA_CENTER,
            fontName='Helvetica',
            fontSize=8.2,
            leading=10,
            textColor=colors.HexColor('#111111'),
        ),
        'SectionTitle': ParagraphStyle(
            'SectionTitle',
            parent=base_styles['Heading3'],
            alignment=TA_LEFT,
            fontName='Helvetica-Bold',
            fontSize=9.2,
            leading=11,
            textColor=colors.HexColor('#931913'),
            spaceBefore=2,
            spaceAfter=0,
        ),
        'TableLabel': ParagraphStyle(
            'TableLabel',
            parent=base_styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=7.9,
            leading=9.5,
            textColor=colors.HexColor('#931913'),
        ),
        'TableValue': ParagraphStyle(
            'TableValue',
            parent=base_styles['Normal'],
            fontName='Helvetica',
            fontSize=7.9,
            leading=9.5,
            textColor=colors.HexColor('#333333'),
        ),
        'SignatureLabel': ParagraphStyle(
            'SignatureLabel',
            parent=base_styles['Normal'],
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
            fontSize=8.4,
            leading=10,
            textColor=colors.HexColor('#333333'),
        ),
    }


def _draw_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(colors.HexColor('#777777'))
    canvas.drawRightString(
        document.pagesize[0] - document.rightMargin,
        0.38 * inch,
        f'Pagina {canvas.getPageNumber()}',
    )
    canvas.restoreState()


def _certificate_header_flowables() -> list[Any]:
    flowables: list[Any] = []
    try:
        use_default_logo = certificate_template_use_default_logo()
    except Exception:
        use_default_logo = True

    if use_default_logo:
        logo = _logo_flowable()
        if logo:
            flowables.append(logo)

    complement_logos = _complement_logo_flowables()
    if complement_logos:
        if flowables:
            flowables.append(Spacer(1, 0.03 * inch))
        flowables.append(_complement_logo_table(complement_logos))

    return flowables


def _logo_flowable() -> SvgLogoFlowable | None:
    logo_path = _logo_svg_path()
    if not logo_path:
        return None
    try:
        return SvgLogoFlowable(logo_path, width=1.85 * inch)
    except Exception:
        return None


def _complement_logo_flowables() -> list[ReportLabImage]:
    try:
        paths = certificate_template_complement_logo_paths()
    except Exception:
        return []

    logos: list[ReportLabImage] = []
    for logo_path in paths:
        try:
            logo = ReportLabImage(
                str(logo_path),
                width=1.08 * inch,
                height=0.42 * inch,
                kind='proportional',
            )
        except Exception:
            continue
        logo.hAlign = 'CENTER'
        logos.append(logo)
    return logos


def _complement_logo_table(logos: list[ReportLabImage]) -> Table:
    columns = min(4, max(1, len(logos)))
    rows: list[list[Any]] = []
    for index in range(0, len(logos), columns):
        row = list(logos[index:index + columns])
        while len(row) < columns:
            row.append('')
        rows.append(row)

    table = Table(
        rows,
        colWidths=[1.28 * inch] * columns,
        hAlign='CENTER',
    )
    table.setStyle(
        TableStyle(
            [
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _signature_flowable() -> ReportLabImage | None:
    signature_path = _signature_image_path()
    if not signature_path:
        return None
    try:
        signature = ReportLabImage(
            str(signature_path),
            width=2.3 * inch,
            height=0.9 * inch,
            kind='proportional',
        )
    except Exception:
        return None
    signature.hAlign = 'CENTER'
    return signature


def _build_email_logo_attachment() -> dict[str, Any] | None:
    logo_path = _email_logo_path()
    if not logo_path:
        return None
    try:
        content = logo_path.read_bytes()
    except OSError:
        return None

    return {
        '@odata.type': '#microsoft.graph.fileAttachment',
        'name': EMAIL_LOGO_FILE_NAME,
        'contentType': 'image/png',
        'contentBytes': b64encode(content).decode('ascii'),
        'isInline': True,
        'contentId': EMAIL_LOGO_CONTENT_ID,
    }


def _email_logo_path() -> Path | None:
    candidates = [
        settings.PROJECT_ROOT / 'frontend' / 'dist' / EMAIL_LOGO_FILE_NAME,
        settings.PROJECT_ROOT / 'frontend' / 'public' / EMAIL_LOGO_FILE_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _logo_svg_path() -> Path | None:
    candidates = [
        settings.PROJECT_ROOT / 'frontend' / 'dist' / LOGO_FILE_NAME,
        settings.PROJECT_ROOT / 'frontend' / 'public' / LOGO_FILE_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _signature_image_path() -> Path | None:
    candidates = [
        settings.PROJECT_ROOT / 'frontend' / 'public' / SIGNATURE_FILE_NAME,
        settings.PROJECT_ROOT / 'frontend' / 'dist' / SIGNATURE_FILE_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _parse_svg_paths(svg_path: Path) -> tuple[float, float, list[dict[str, Any]]]:
    root = ET.fromstring(svg_path.read_text(encoding='utf-8'))
    view_box = _clean_text(root.attrib.get('viewBox'))
    view_numbers = [float(value) for value in re.findall(r'-?\d+(?:\.\d+)?', view_box)]
    if len(view_numbers) != 4:
        raise InscriptionCertificateError('El logo SVG no tiene viewBox válido.')
    view_x, view_y, view_width, view_height = view_numbers
    style_fills = _svg_style_fills(root)
    parsed_paths: list[dict[str, Any]] = []

    for element in root.iter():
        if not element.tag.endswith('path'):
            continue
        d_value = _clean_text(element.attrib.get('d'))
        if not d_value:
            continue
        fill = _svg_path_fill(element, style_fills)
        commands, bounds = _parse_svg_path_d(d_value)
        if not commands or not _bounds_intersect_viewbox(bounds, view_x, view_y, view_width, view_height):
            continue
        parsed_paths.append({'commands': commands, 'fill': fill})

    if not parsed_paths:
        raise InscriptionCertificateError('El logo SVG no contiene trazos renderizables.')
    return view_width, view_height, parsed_paths


def _svg_style_fills(root: ET.Element) -> dict[str, colors.Color]:
    fills: dict[str, colors.Color] = {}
    for element in root.iter():
        if not element.tag.endswith('style') or not element.text:
            continue
        for match in re.finditer(r'\.([\w-]+)\s*\{[^}]*fill\s*:\s*(#[0-9A-Fa-f]{3,6})', element.text):
            fills[match.group(1)] = colors.HexColor(match.group(2))
    return fills


def _svg_path_fill(element: ET.Element, style_fills: dict[str, colors.Color]) -> colors.Color:
    fill = _clean_text(element.attrib.get('fill'))
    if fill.startswith('#'):
        return colors.HexColor(fill)
    class_name = _clean_text(element.attrib.get('class'))
    if class_name and class_name in style_fills:
        return style_fills[class_name]
    style = _clean_text(element.attrib.get('style'))
    style_match = re.search(r'fill\s*:\s*(#[0-9A-Fa-f]{3,6})', style)
    if style_match:
        return colors.HexColor(style_match.group(1))
    return colors.HexColor('#777777')


def _parse_svg_path_d(d_value: str) -> tuple[list[tuple], tuple[float, float, float, float]]:
    tokens = re.findall(r'[CcHhLlMmSsVvZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?', d_value)
    commands: list[tuple] = []
    bounds: list[tuple[float, float]] = []
    index = 0
    active_command = ''
    current_x = current_y = 0.0
    start_x = start_y = 0.0
    last_control: tuple[float, float] | None = None
    last_curve_command = False

    def is_command(token: str) -> bool:
        return bool(re.fullmatch(r'[CcHhLlMmSsVvZz]', token))

    def has_numbers(count: int) -> bool:
        return index + count <= len(tokens) and all(not is_command(tokens[index + offset]) for offset in range(count))

    def number() -> float:
        nonlocal index
        value = float(tokens[index])
        index += 1
        return value

    def add_bound(*points: tuple[float, float]) -> None:
        bounds.extend(points)

    while index < len(tokens):
        if is_command(tokens[index]):
            active_command = tokens[index]
            index += 1

        if not active_command:
            break

        command = active_command
        absolute = command.isupper()
        command_upper = command.upper()

        if command_upper == 'Z':
            commands.append(('Z',))
            current_x, current_y = start_x, start_y
            last_control = None
            last_curve_command = False
            active_command = ''
            continue

        if command_upper == 'M':
            first_pair = True
            while has_numbers(2):
                x = number()
                y = number()
                if not absolute:
                    x += current_x
                    y += current_y
                if first_pair:
                    commands.append(('M', x, y))
                    start_x, start_y = x, y
                    first_pair = False
                else:
                    commands.append(('L', x, y))
                current_x, current_y = x, y
                add_bound((x, y))
            active_command = 'L' if absolute else 'l'
            last_control = None
            last_curve_command = False
            continue

        if command_upper == 'L':
            while has_numbers(2):
                x = number()
                y = number()
                if not absolute:
                    x += current_x
                    y += current_y
                commands.append(('L', x, y))
                current_x, current_y = x, y
                add_bound((x, y))
            last_control = None
            last_curve_command = False
            continue

        if command_upper == 'H':
            while has_numbers(1):
                x = number()
                if not absolute:
                    x += current_x
                commands.append(('L', x, current_y))
                current_x = x
                add_bound((x, current_y))
            last_control = None
            last_curve_command = False
            continue

        if command_upper == 'V':
            while has_numbers(1):
                y = number()
                if not absolute:
                    y += current_y
                commands.append(('L', current_x, y))
                current_y = y
                add_bound((current_x, y))
            last_control = None
            last_curve_command = False
            continue

        if command_upper == 'C':
            while has_numbers(6):
                x1, y1, x2, y2, x, y = [number() for _ in range(6)]
                if not absolute:
                    x1 += current_x
                    y1 += current_y
                    x2 += current_x
                    y2 += current_y
                    x += current_x
                    y += current_y
                commands.append(('C', x1, y1, x2, y2, x, y))
                current_x, current_y = x, y
                last_control = (x2, y2)
                last_curve_command = True
                add_bound((x1, y1), (x2, y2), (x, y))
            continue

        if command_upper == 'S':
            while has_numbers(4):
                if last_curve_command and last_control:
                    x1 = (2 * current_x) - last_control[0]
                    y1 = (2 * current_y) - last_control[1]
                else:
                    x1, y1 = current_x, current_y
                x2, y2, x, y = [number() for _ in range(4)]
                if not absolute:
                    x2 += current_x
                    y2 += current_y
                    x += current_x
                    y += current_y
                commands.append(('C', x1, y1, x2, y2, x, y))
                current_x, current_y = x, y
                last_control = (x2, y2)
                last_curve_command = True
                add_bound((x1, y1), (x2, y2), (x, y))
            continue

        break

    if not bounds:
        return commands, (0.0, 0.0, 0.0, 0.0)
    xs = [point[0] for point in bounds]
    ys = [point[1] for point in bounds]
    return commands, (min(xs), min(ys), max(xs), max(ys))


def _bounds_intersect_viewbox(
    bounds: tuple[float, float, float, float],
    view_x: float,
    view_y: float,
    view_width: float,
    view_height: float,
) -> bool:
    min_x, min_y, max_x, max_y = bounds
    return not (
        max_x < view_x
        or min_x > view_x + view_width
        or max_y < view_y
        or min_y > view_y + view_height
    )


def _validate_certificate_payload(payload: dict[str, Any]) -> None:
    required_fields = {
        'nombre': 'Debes enviar el nombre del estudiante para generar el certificado.',
        'cedula': 'Debes enviar la cédula del estudiante para generar el certificado.',
        'email': 'Debes enviar el correo del estudiante para generar el certificado.',
        'matricula': 'Debes enviar la matrícula o código de inscripción para generar el certificado.',
        'nombre_materia': 'Debes enviar el curso inscrito para generar el certificado.',
    }
    for field, message in required_fields.items():
        if not _clean_text(payload.get(field)):
            raise InscriptionCertificateError(message)


def _ensure_certificate_code(payload: dict[str, Any]) -> dict[str, Any]:
    certificate_payload = dict(payload)
    certificate_code = _certificate_code(certificate_payload)
    if not certificate_code:
        certificate_code = _next_certificate_code()

    certificate_payload['codigo_certificado'] = certificate_code
    certificate_payload['certificate_code'] = certificate_code
    return certificate_payload


def _ensure_certificate_verification_code(payload: dict[str, Any]) -> dict[str, Any]:
    certificate_payload = dict(payload)
    verification_code = _certificate_verification_code(certificate_payload)
    if not verification_code:
        verification_code = str(uuid4())
    certificate_payload['codigo_verificacion'] = verification_code
    certificate_payload['verification_code'] = verification_code
    return certificate_payload


def _certificate_code(payload: dict[str, Any]) -> str:
    return _clean_text(_first_non_empty(payload.get('codigo_certificado'), payload.get('certificate_code')))


def _certificate_verification_code(payload: dict[str, Any]) -> str:
    return _clean_text(_first_non_empty(payload.get('codigo_verificacion'), payload.get('verification_code')))


def _next_certificate_code() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT NEXT VALUE FOR dbo.SeqCertificadosGenerados")
            row = cursor.fetchone()
    except Exception as exc:
        raise InscriptionCertificateError(
            'No fue posible reservar el código del certificado en la base de datos.'
        ) from exc

    sequence = _safe_int(row[0] if row else None, default=0)
    if sequence <= 0:
        raise InscriptionCertificateError('La secuencia de certificados devolvió un valor inválido.')
    return _format_certificate_code(sequence)


def _register_certificate_generation(payload: dict[str, Any], stored_relative_path: str) -> dict[str, str]:
    certificate_code = _certificate_code(payload)
    if not certificate_code:
        raise InscriptionCertificateError('No fue posible registrar el certificado sin número de certificado.')

    verification_code = _certificate_verification_code(payload) or str(uuid4())
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dbo.CERTIFICADOS_GENERADOS (
                    TipoCertificado,
                    TipoOrigen,
                    NumeroCertificado,
                    CodigoEstud,
                    CedulaEst,
                    ApellidosNombre,
                    Cod_AnioBasica,
                    CodigoPeriodo,
                    CodigoMateria,
                    Num_Matricula,
                    CodCurso,
                    UsuarioGenero,
                    RutaArchivo,
                    CodigoVerificacion,
                    Observacion
                )
                OUTPUT INSERTED.CertificadoId
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    _certificate_registry_type(payload),
                    _certificate_origin(payload),
                    certificate_code,
                    _numeric_or_none(payload.get('codigo_estud')),
                    _trim_to_max(payload.get('cedula'), 50) or None,
                    _trim_to_max(payload.get('nombre'), 150) or None,
                    _numeric_or_none(payload.get('cod_anio_basica')),
                    _numeric_or_none(payload.get('codigo_periodo')),
                    _numeric_or_none(payload.get('codigo_materia')),
                    _numeric_or_none(payload.get('numero_matricula')),
                    _numeric_or_none(payload.get('cod_curso')),
                    'SISTEMA',
                    _trim_to_max(stored_relative_path, 500) or None,
                    _trim_to_max(verification_code, 100),
                    _trim_to_max(_certificate_registry_observation(payload), 500) or None,
                ],
            )
            row = cursor.fetchone()
    except Exception as exc:
        raise InscriptionCertificateError(
            'No fue posible registrar el historial del certificado generado.'
        ) from exc

    return {
        'certificado_id': str(row[0]) if row and row[0] is not None else '',
        'codigo_certificado': certificate_code,
        'certificate_code': certificate_code,
        'codigo_verificacion': verification_code,
    }


def _certificate_origin(payload: dict[str, Any]) -> str:
    source = _clean_text(payload.get('source'))
    if source in {'matricula_masiva', 'matricula_academica', 'inscripcion', 'dashboard_estudiante', 'admin_corte'}:
        return 'MATRICULA'
    return 'OTRO'


def _certificate_registry_type(payload: dict[str, Any]) -> str:
    if _is_approval_certificate(payload):
        return 'APROBACION'
    return 'INSCRIPCION'


def _certificate_registry_observation(payload: dict[str, Any]) -> str:
    cut_label = _clean_text(payload.get('nombre_corte'))
    course_label = _clean_text(payload.get('nombre_materia'))
    if cut_label and course_label:
        return f'{cut_label} - {course_label}'
    default_label = 'Certificado de aprobación generado por el sistema.' if _is_approval_certificate(payload) else 'Certificado de inscripción generado por el sistema.'
    return cut_label or course_label or default_label


def _format_certificate_code(sequence: int) -> str:
    return f'{CERTIFICATE_CODE_PREFIX}-{sequence:0{CERTIFICATE_CODE_PADDING}d}'


def _certificate_storage_dir() -> Path:
    custom_path = os.getenv('INSCRIPTION_CERTIFICATE_STORAGE_DIR', '').strip()
    if custom_path:
        return Path(custom_path)
    return settings.BASE_DIR / CERTIFICATE_STORAGE_DIR_NAME


def _source_label(value: Any) -> str:
    source = _clean_text(value)
    if source == 'matricula_masiva':
        return 'Carga masiva desde Excel'
    if source == 'matricula_academica':
        return 'Matrícula académica por selección'
    return 'Formulario público de inscripción'


def _default_internal_observations(source: str, payment_link: str | None) -> str:
    if source == 'matricula_masiva':
        return 'Matrícula generada desde carga Excel sin cargo de pago.'
    if source == 'matricula_academica':
        return 'Matrícula generada por selección académica sin cargo de pago.'
    if payment_link:
        return 'Enlace de pago generado para completar la inscripción.'
    return 'Registro de inscripción generado desde formulario público.'


def _today_label() -> str:
    today = timezone.localdate()
    if not isinstance(today, date):
        today = date.today()
    return today.strftime('%d/%m/%Y')


def _format_money(value: Any) -> str:
    if value in (None, ''):
        return '$ 0.00'
    try:
        return f'$ {Decimal(str(value)):.2f}'
    except (InvalidOperation, ValueError, TypeError):
        text = _clean_text(value)
        return f'$ {text}' if text else '$ 0.00'


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


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if _clean_text(value):
            return value
    return ''


def _fallback(value: Any) -> str:
    return _clean_text(value) or 'No registrado'


def _clean_text(value: Any) -> str:
    if value is None:
        return ''
    return re.sub(r'\s+', ' ', str(value).replace('\r', ' ').replace('\n', ' ')).strip()


def _trim_to_max(value: Any, max_length: int) -> str:
    return _clean_text(value)[:max_length]


def _safe_html(value: Any) -> str:
    return escape(_clean_text(value), quote=False)


def _slug_part(value: Any) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')[:60]


def _last_four_digits(value: Any) -> str:
    digits = re.sub(r'\D+', '', _clean_text(value))
    return digits[-4:] if digits else ''


def _masked_identity(value: Any) -> str:
    digits = re.sub(r'\D+', '', _clean_text(value))
    if not digits:
        return ''
    if len(digits) <= 4:
        return digits
    return f'***{digits[-4:]}'


def _safe_filename(value: Any) -> str:
    filename = Path(_clean_text(value)).name
    filename = re.sub(r'[^A-Za-z0-9_.-]+', '_', filename)
    if not filename.lower().endswith('.pdf'):
        filename = f'{filename}.pdf'
    return filename or 'certificado_inscripcion.pdf'
