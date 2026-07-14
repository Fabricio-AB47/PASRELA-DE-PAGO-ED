from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


INTEC_RED = colors.HexColor('#A71916')
SOFT_BLUE = colors.HexColor('#E8F2F5')


def build_all_digital_payment_receipt(payment: dict[str, Any]) -> bytes:
    """Build a non-tax PDF receipt for a payment confirmed through All Digital."""
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=22 * mm,
        leftMargin=22 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Comprobante All Digital {payment.get('provider_transaction_id') or ''}",
        author='INTEC Educación Continua',
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ReceiptTitle', parent=styles['Title'], textColor=INTEC_RED, fontSize=18,
        leading=22, alignment=TA_CENTER, spaceAfter=5 * mm,
    )
    status_style = ParagraphStyle(
        'PaidStatus', parent=styles['Heading2'], textColor=colors.HexColor('#14784A'),
        alignment=TA_CENTER, fontSize=13, spaceAfter=7 * mm,
    )
    note_style = ParagraphStyle(
        'ReceiptNote', parent=styles['BodyText'], fontSize=8.5, leading=12,
        textColor=colors.HexColor('#666666'), alignment=TA_CENTER,
    )
    label_style = ParagraphStyle(
        'ReceiptLabel', parent=styles['BodyText'], textColor=INTEC_RED,
        fontName='Helvetica-Bold', fontSize=9.5, leading=12,
        splitLongWords=True,
    )
    value_style = ParagraphStyle(
        'ReceiptValue', parent=styles['BodyText'], textColor=colors.black,
        fontName='Helvetica', fontSize=9.5, leading=13,
        splitLongWords=True,
    )

    story = []
    logo_path = Path(settings.PROJECT_ROOT) / 'frontend' / 'public' / 'Intec-Logowithslogangray.png'
    if logo_path.exists():
        logo = Image(str(logo_path), width=57 * mm, height=20 * mm)
        logo.hAlign = 'CENTER'
        story.extend([logo, Spacer(1, 4 * mm)])
    story.append(Paragraph('COMPROBANTE DE PAGO ELECTRÓNICO', title_style))
    story.append(Paragraph('PAGO CONFIRMADO', status_style))

    registered_value = _number(payment.get('registered_value'))
    paid_value = registered_value if registered_value > 0 else _number(payment.get('amount'))
    raw_rows = [
        ('Comprobante', f"AD-{_text(payment.get('provider_transaction_id') or payment.get('inscription_payment_id'))}"),
        ('Estudiante', _text(payment.get('nombre'))),
        ('Identificación', _text(payment.get('cedula'))),
        ('Código estudiantil', _text(payment.get('codigo_estud'))),
        ('Matrícula', _text(payment.get('matricula'))),
        ('Curso', _text(payment.get('course_name') or payment.get('description'))),
        ('Corte', _text(payment.get('cut_name'))),
        ('ID de transacción', _text(payment.get('provider_transaction_id'))),
        ('Registro de pago', _text(payment.get('payment_record_number'))),
        ('Fecha confirmada', _text(payment.get('paid_at'))),
        ('Valor pagado', f'USD {paid_value:,.2f}'),
    ]
    rows = [
        [Paragraph(escape(label), label_style), Paragraph(escape(value), value_style)]
        for label, value in raw_rows
    ]
    label_width = 44 * mm
    table = Table(
        rows,
        colWidths=[label_width, document.width - label_width],
        hAlign='CENTER',
        splitByRow=True,
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), SOFT_BLUE),
        ('TEXTCOLOR', (0, 0), (0, -1), INTEC_RED),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#C9D4D8')),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.extend([
        table,
        Spacer(1, 9 * mm),
        Paragraph(
            'Documento generado automáticamente al verificar el pago en los registros de All Digital. '
            'Es un comprobante de control administrativo y no reemplaza una factura tributaria.',
            note_style,
        ),
        Spacer(1, 3 * mm),
        Paragraph(f"Generado: {timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')}", note_style),
    ])
    document.build(story)
    return output.getvalue()


def _text(value: Any) -> str:
    return str(value or '-').strip() or '-'


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
