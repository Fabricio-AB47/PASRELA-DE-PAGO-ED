from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


class ListExportError(Exception):
    pass


EXPORT_DEFINITIONS = {
    'students': {
        'label': 'ESTUDIANTES',
        'filename': 'listado-estudiantes',
        'columns': (
            ('nombre', 'NOMBRES Y APELLIDOS'),
            ('correo_personal', 'CORREO PERSONAL'),
            ('correo_intec', 'CORREO INSTITUCIONAL'),
            ('telefono', 'TELÉFONO'),
            ('movil', 'MÓVIL'),
        ),
    },
    'teachers': {
        'label': 'DOCENTES',
        'filename': 'listado-docentes',
        'columns': (
            ('nombre', 'NOMBRES Y APELLIDOS'),
            ('correo_personal', 'CORREO PERSONAL'),
            ('correo_intec', 'CORREO INSTITUCIONAL'),
            ('telefono', 'TELÉFONO'),
            ('movil', 'MÓVIL'),
        ),
    },
}


def build_people_list_export(payload: dict[str, Any]) -> tuple[bytes, str, str]:
    export_kind = _clean(payload.get('kind')).lower()
    export_format = _clean(payload.get('format')).lower()
    definition = EXPORT_DEFINITIONS.get(export_kind)
    if not definition:
        raise ListExportError('El tipo de listado solicitado no es válido.')
    if export_format not in {'xls', 'pdf'}:
        raise ListExportError('Selecciona un formato de descarga válido: XLS o PDF.')

    raw_rows = payload.get('rows')
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ListExportError('No existen registros para generar el documento.')
    if len(raw_rows) > 1000:
        raise ListExportError('El documento admite un máximo de 1000 registros.')

    columns = definition['columns']
    rows = [_normalized_row(row, columns) for row in raw_rows if isinstance(row, dict)]
    if not rows:
        raise ListExportError('No existen registros válidos para generar el documento.')

    requested_title = _clean(payload.get('title'))
    title = (requested_title or f"LISTADO DE {definition['label']}")[:180]
    filename = definition['filename']
    if export_format == 'xls':
        return _build_spreadsheet_xml(title, columns, rows), 'application/vnd.ms-excel', f'{filename}.xls'
    return _build_pdf(title, columns, rows), 'application/pdf', f'{filename}.pdf'


def _normalized_row(row: dict[str, Any], columns: tuple[tuple[str, str], ...]) -> list[str]:
    values = []
    for key, _label in columns:
        value = _clean(row.get(key))
        if key == 'nombre':
            value = value.upper()
        values.append(value)
    return values


def _build_spreadsheet_xml(
    title: str,
    columns: tuple[tuple[str, str], ...],
    rows: list[list[str]],
) -> bytes:
    def cell(value: str, style: str = 'Body') -> str:
        safe_value = xml_escape(value, {'"': '&quot;', "'": '&apos;'})
        return f'<Cell ss:StyleID="{style}"><Data ss:Type="String">{safe_value}</Data></Cell>'

    header_cells = ''.join(cell(label, 'Header') for _key, label in columns)
    body_rows = ''.join(
        f"<Row>{''.join(cell(value) for value in row)}</Row>"
        for row in rows
    )
    document = f'''<?xml version="1.0" encoding="UTF-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Styles>
  <Style ss:ID="Default" ss:Name="Normal"><Alignment ss:Vertical="Center"/><Font ss:FontName="Arial" ss:Size="10"/></Style>
  <Style ss:ID="Title"><Alignment ss:Horizontal="Center"/><Font ss:FontName="Arial" ss:Size="14" ss:Bold="1" ss:Color="#A71914"/></Style>
  <Style ss:ID="Header"><Alignment ss:Horizontal="Center" ss:Vertical="Center" ss:WrapText="1"/><Font ss:FontName="Arial" ss:Size="10" ss:Bold="1" ss:Color="#FFFFFF"/><Interior ss:Color="#A71914" ss:Pattern="Solid"/></Style>
  <Style ss:ID="Body"><Alignment ss:Vertical="Center" ss:WrapText="1"/><Font ss:FontName="Arial" ss:Size="10"/><Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1" ss:Color="#D9D9D9"/></Borders></Style>
 </Styles>
 <Worksheet ss:Name="Listado">
  <Table>
   <Column ss:Width="190"/><Column ss:Width="170"/><Column ss:Width="170"/><Column ss:Width="100"/><Column ss:Width="100"/>
   <Row ss:Height="28"><Cell ss:MergeAcross="4" ss:StyleID="Title"><Data ss:Type="String">{xml_escape(title)}</Data></Cell></Row>
   <Row ss:Height="8"/>
   <Row ss:Height="28">{header_cells}</Row>
   {body_rows}
  </Table>
  <WorksheetOptions xmlns="urn:schemas-microsoft-com:office:excel"><FreezePanes/><FrozenNoSplit/><SplitHorizontal>3</SplitHorizontal><TopRowBottomPane>3</TopRowBottomPane><ActivePane>2</ActivePane></WorksheetOptions>
 </Worksheet>
</Workbook>'''
    return document.encode('utf-8')


def _build_pdf(
    title: str,
    columns: tuple[tuple[str, str], ...],
    rows: list[list[str]],
) -> bytes:
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=title,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ExportTitle',
        parent=styles['Title'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=18,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#A71914'),
        spaceAfter=4 * mm,
    )
    header_style = ParagraphStyle(
        'ExportHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9,
        textColor=colors.white,
        alignment=TA_CENTER,
    )
    body_style = ParagraphStyle(
        'ExportBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9,
        textColor=colors.HexColor('#4F4F4F'),
    )
    table_data = [
        [Paragraph(xml_escape(label), header_style) for _key, label in columns],
        *[
            [Paragraph(xml_escape(value or '-'), body_style) for value in row]
            for row in rows
        ],
    ]
    table = Table(
        table_data,
        colWidths=[68 * mm, 58 * mm, 58 * mm, 40 * mm, 40 * mm],
        repeatRows=1,
        hAlign='CENTER',
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#A71914')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#D3D9DB')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F3F8F9')]),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    document.build([Paragraph(xml_escape(title), title_style), Spacer(1, 2 * mm), table])
    return output.getvalue()


def _clean(value: Any) -> str:
    printable_value = ''.join(
        character if ord(character) >= 32 else ' '
        for character in str(value or '')
    )
    return ' '.join(printable_value.split())[:500]
