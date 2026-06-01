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

from django.conf import settings
from django.core import signing
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Flowable, Image as ReportLabImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


class InscriptionCertificateError(Exception):
    pass


CERTIFICATE_CONTENT_TYPE = 'application/pdf'
CERTIFICATE_STORAGE_DIR_NAME = 'certificados_inscripcion'
DEFAULT_INSTITUTION_NAME = 'Instituto Superior Tecnológico INTEC'
CERTIFICATE_SIGNING_SALT = 'dashboard_auth.inscription_certificate'
CERTIFICATE_VERSION = '2026-07-20-logo-signature-v5'
DEFAULT_COURSE_START_DATE = '20 de julio de 2026'
LOGO_FILE_NAME = 'Intec-Logowithslogangray.svg'
SIGNATURE_FILE_NAME = 'firma veronica.jpeg'


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
        '0.00' if source == 'matricula_masiva' else None,
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
        or source == 'matricula_masiva'
    )

    return {
        'source': source,
        'institution_name': _first_non_empty(payload.get('institution_name'), DEFAULT_INSTITUTION_NAME),
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
        'fecha_inicio': _first_non_empty(payload.get('fecha_inicio'), DEFAULT_COURSE_START_DATE),
        'nombre': _clean_text(_first_non_empty(payload.get('nombre'), provider_payload.get('nombre'))),
        'cedula': _clean_text(_first_non_empty(payload.get('cedula'), provider_payload.get('cedula'))),
        'email': _clean_text(_first_non_empty(payload.get('email'), provider_payload.get('email'))),
        'telefono': _clean_text(_first_non_empty(payload.get('telefono'), provider_payload.get('telefono'))),
        'localidad': _clean_text(_first_non_empty(payload.get('localidad'), provider_payload.get('localidad'))),
        'direccion': _clean_text(_first_non_empty(payload.get('direccion'), provider_payload.get('direccion'))),
        'ocupacion': _clean_text(_first_non_empty(payload.get('ocupacion'), provider_payload.get('ocupacion'))),
        'empresa': _clean_text(_first_non_empty(payload.get('empresa'), provider_payload.get('empresa'))),
        'codigo_periodo': _clean_text(
            _first_non_empty(payload.get('codigo_periodo'), provider_payload.get('codigo_periodo'))
        ),
        'estado_periodo': _clean_text(
            _first_non_empty(payload.get('estado_periodo'), provider_payload.get('estado_periodo'))
        ),
        'monto': _format_money(monto),
        'modalidad': _clean_text(_first_non_empty(payload.get('modalidad'), provider_payload.get('modalidad'), 'Virtual')),
        'payment_link': _clean_text(payment_link),
        'data_treatment_accepted': data_treatment_accepted,
        'inscripcion_aprobada': 'Sí',
        'responsable_recepcion': 'Sistema de inscripciones INTEC',
        'observaciones_internas': _default_internal_observations(source, payment_link),
    }


def create_stored_certificate_record(payload: dict[str, Any]) -> dict[str, str]:
    certificate_payload = _normalize_certificate_payload(payload)
    content, filename = build_inscription_certificate(certificate_payload)
    stored_filename = _safe_filename(filename)
    storage_path = _certificate_storage_dir() / stored_filename
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(content)

    signed_payload = {
        **certificate_payload,
        'stored_filename': stored_filename,
        'stored_relative_path': f'{CERTIFICATE_STORAGE_DIR_NAME}/{stored_filename}',
        'stored_at': timezone.now().isoformat(),
        'certificate_version': CERTIFICATE_VERSION,
    }
    return {
        'filename': stored_filename,
        'token': sign_certificate_payload(signed_payload),
        'stored_path': signed_payload['stored_relative_path'],
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
    logo_attachment = _build_email_logo_attachment()
    logo_html = ''
    if logo_attachment:
        logo_html = """
            <tr>
              <td align="center" style="padding:24px 28px 8px 28px;background:#ffffff;">
                <img src="cid:intec-logo" width="230" alt="INTEC" style="display:block;width:230px;max-width:78%;height:auto;border:0;" />
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
                <h2 style="margin:0;font-size:22px;font-weight:700;">Certificado de inscripción</h2>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 28px;color:#111827;">
                <p style="margin:0 0 12px 0;font-size:16px;">Hola {safe_recipient_label},</p>
                <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#374151;">
                  Adjuntamos tu certificado de inscripción. El documento avala que ya te encuentras inscrito/a
                  en el curso <strong>{safe_course}</strong>, que inicia el <strong>{safe_start_date}</strong>.
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
            'subject': 'Certificado de inscripción INTEC',
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

    from .payments import _send_graph_mail

    _send_graph_mail(mail_payload)
    return {
        'sent': True,
        'message': f'Certificado de inscripción enviado correctamente a {recipient_email}.',
        'filename': certificate_filename,
    }


def build_inscription_certificate(payload: dict[str, Any]) -> tuple[bytes, str]:
    certificate_payload = _normalize_certificate_payload(payload)
    _validate_certificate_payload(certificate_payload)

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.64 * inch,
        leftMargin=0.64 * inch,
        topMargin=0.48 * inch,
        bottomMargin=0.48 * inch,
        title='Certificado de Inscripción',
        author=certificate_payload.get('institution_name') or DEFAULT_INSTITUTION_NAME,
    )
    story = _build_pdf_story(certificate_payload)
    document.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return output.getvalue(), build_certificate_filename(certificate_payload)


def load_or_create_stored_certificate(payload: dict[str, Any]) -> tuple[bytes, str]:
    certificate_payload = _normalize_certificate_payload(payload)
    _validate_certificate_payload(certificate_payload)

    filename = _safe_filename(certificate_payload.get('stored_filename') or build_certificate_filename(certificate_payload))
    storage_path = _certificate_storage_dir() / filename
    if storage_path.exists() and certificate_payload.get('certificate_version') == CERTIFICATE_VERSION:
        return storage_path.read_bytes(), filename

    content, generated_filename = build_inscription_certificate(certificate_payload)
    filename = _safe_filename(generated_filename)
    storage_path = _certificate_storage_dir() / filename
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(content)
    return content, filename


def build_certificate_filename(payload: dict[str, Any]) -> str:
    matricula = _slug_part(payload.get('matricula')) or 'sin-codigo'
    cedula = _slug_part(payload.get('cedula')) or 'sin-cedula'
    return f'certificado_inscripcion_{matricula}_{cedula}.pdf'


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
        raise InscriptionCertificateError('El certificado solicitado no contiene datos validos.')
    return payload


def _normalize_certificate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    certificate_payload = build_certificate_payload(payload) if not payload.get('nombre') else dict(payload)
    certificate_payload.setdefault('institution_name', DEFAULT_INSTITUTION_NAME)
    certificate_payload.setdefault('fecha_inicio', DEFAULT_COURSE_START_DATE)
    certificate_payload.setdefault('responsable_recepcion', 'Sistema de inscripciones INTEC')
    certificate_payload.setdefault('observaciones_internas', _default_internal_observations(
        _clean_text(certificate_payload.get('source')) or 'inscripcion',
        _clean_text(certificate_payload.get('payment_link')),
    ))
    certificate_payload.setdefault('monto', '$ 0.00')
    certificate_payload.setdefault('modalidad', 'Virtual')
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


def _build_pdf_story(payload: dict[str, Any]) -> list[Any]:
    styles = _pdf_styles()
    story: list[Any] = []
    logo = _logo_flowable()
    if logo:
        story.extend([logo, Spacer(1, 0.07 * inch)])

    story.extend(
        [
            Paragraph('CERTIFICADO DE INSCRIPCIÓN', styles['CertificateTitle']),
            Paragraph(_safe_html(payload.get('institution_name') or DEFAULT_INSTITUTION_NAME), styles['Institution']),
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
                ('Cédula / identificación', payload.get('cedula')),
                ('Correo electronico', payload.get('email')),
                ('Telefono', payload.get('telefono')),
                ('Ciudad / localidad', payload.get('localidad')),
                ('Dirección', payload.get('direccion')),
                ('Ocupación', payload.get('ocupacion')),
                ('Empresa / institución', payload.get('empresa')),
            ],
            styles,
        )
    )
    story.extend(
        _section_table(
            'DATOS DE LA INSCRIPCIÓN',
            [
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
    story.append(
        Paragraph(
            'Este certificado se emite para avalar el estado de inscripción del participante '
            f"conforme a los datos disponibles al momento de su generación. Fecha de emisión: "
            f"{_safe_html(_fallback(payload.get('fecha_inscripcion')))}.",
            styles['BodyJustified'],
        )
    )
    story.extend(_signature_block(styles))
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


def _signature_block(_styles: dict[str, ParagraphStyle]) -> list[Any]:
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
    institution = _safe_html(_fallback(payload.get('institution_name')))
    student = _safe_html(_fallback(payload.get('nombre')))
    cedula = _safe_html(_fallback(payload.get('cedula')))
    course = _safe_html(_fallback(payload.get('nombre_materia')))
    start_date = _safe_html(_fallback(payload.get('fecha_inicio')))
    return (
        f'Por medio del presente documento, <b>{institution}</b> certifica que '
        f'<b>{student}</b>, con identificación No. <b>{cedula}</b>, se encuentra '
        f'ya inscrito/a en el curso <b>{course}</b>, que inicia el <b>{start_date}</b>, '
        'de acuerdo con la información registrada en el sistema '
        'institucional de inscripciones.'
    )


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
        'BodyJustified': ParagraphStyle(
            'BodyJustified',
            parent=base_styles['BodyText'],
            alignment=TA_JUSTIFY,
            fontName='Helvetica',
            fontSize=8.8,
            leading=12.2,
            textColor=colors.HexColor('#333333'),
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


def _logo_flowable() -> SvgLogoFlowable | None:
    logo_path = _logo_svg_path()
    if not logo_path:
        return None
    try:
        return SvgLogoFlowable(logo_path, width=1.85 * inch)
    except Exception:
        return None


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
    logo_path = _logo_svg_path()
    if not logo_path:
        return None
    try:
        content = logo_path.read_bytes()
    except OSError:
        return None

    return {
        '@odata.type': '#microsoft.graph.fileAttachment',
        'name': LOGO_FILE_NAME,
        'contentType': 'image/svg+xml',
        'contentBytes': b64encode(content).decode('ascii'),
        'isInline': True,
        'contentId': 'intec-logo',
    }


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
        raise InscriptionCertificateError('El logo SVG no tiene viewBox valido.')
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


def _certificate_storage_dir() -> Path:
    custom_path = os.getenv('INSCRIPTION_CERTIFICATE_STORAGE_DIR', '').strip()
    if custom_path:
        return Path(custom_path)
    return settings.BASE_DIR / CERTIFICATE_STORAGE_DIR_NAME


def _source_label(value: Any) -> str:
    source = _clean_text(value)
    if source == 'matricula_masiva':
        return 'Carga masiva desde Excel'
    return 'Formulario público de inscripción'


def _default_internal_observations(source: str, payment_link: str | None) -> str:
    if source == 'matricula_masiva':
        return 'Matrícula generada desde carga Excel sin cargo de pago.'
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


def _safe_html(value: Any) -> str:
    return escape(_clean_text(value), quote=False)


def _slug_part(value: Any) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')[:60]


def _safe_filename(value: Any) -> str:
    filename = Path(_clean_text(value)).name
    filename = re.sub(r'[^A-Za-z0-9_.-]+', '_', filename)
    if not filename.lower().endswith('.pdf'):
        filename = f'{filename}.pdf'
    return filename or 'certificado_inscripcion.pdf'
