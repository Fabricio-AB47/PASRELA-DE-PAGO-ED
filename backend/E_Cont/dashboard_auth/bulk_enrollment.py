from __future__ import annotations

import base64
from io import BytesIO
import os
import re
import unicodedata
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .payments import PaymentGatewayError, create_mass_matriculation_and_credentials


class BulkEnrollmentError(Exception):
    pass


class InMemoryExcelUpload(BytesIO):
    def __init__(self, content: bytes, name: str):
        super().__init__(content)
        self.name = name
        self.size = len(content)


MAX_UPLOAD_BYTES = int(os.getenv('BULK_ENROLLMENT_MAX_UPLOAD_BYTES') or str(5 * 1024 * 1024))
MAX_BULK_ROWS = int(os.getenv('BULK_ENROLLMENT_MAX_ROWS') or '100')
DEFAULT_EMPTY_LOCATION = 'No registrada'
TEMPLATE_FILE_NAME = 'plantilla_matricula_masiva.xlsx'
TEMPLATE_HEADERS = (
    ('Nombres', 'Obligatorio. Primer y segundo nombre. Ejemplo: Juan Roman'),
    ('Apellidos', 'Obligatorio. Primer y segundo apellido. Ejemplo: Recalde Romo'),
    ('Cedula', 'Obligatorio. Solo numeros. Se conserva como texto para ceros iniciales.'),
    ('Correo', 'Obligatorio. Correo personal del estudiante.'),
    ('Numero de celular', 'Obligatorio. Telefono de contacto.'),
    ('Ocupacion', 'Opcional.'),
    ('Empresa', 'Opcional.'),
    ('Localidad', 'Opcional. Si queda vacio el sistema carga No registrada.'),
    ('Direccion', 'Opcional. Si queda vacio el sistema carga No registrada.'),
)

HEADER_ALIASES = {
    'nombre_completo': (
        'nombre completo',
        'nombres completos',
        'nombres y apellidos',
        'nombre y apellido',
        'estudiante',
    ),
    'nombres': ('nombres', 'nombre'),
    'apellidos': ('apellidos', 'apellido'),
    'cedula': ('cedula', 'cedula de ciudadania', 'identificacion', 'documento'),
    'email': ('correo', 'correo electronico', 'email', 'e-mail'),
    'telefono': ('numero de celular', 'celular', 'telefono', 'numero telefono', 'numero de telefono'),
    'localidad': ('localidad', 'ciudad'),
    'direccion': ('direccion', 'domicilio'),
    'ocupacion': ('ocupacion', 'profesion'),
    'empresa': ('empresa', 'institucion'),
}


def excel_upload_from_json(payload: dict[str, Any]) -> InMemoryExcelUpload:
    file_payload = payload.get('excel') or payload.get('file') or {}
    if not isinstance(file_payload, dict):
        raise BulkEnrollmentError('Debes enviar el archivo Excel dentro del cuerpo JSON.')

    file_name = _clean_text(
        file_payload.get('name') or file_payload.get('filename') or TEMPLATE_FILE_NAME
    )
    content_base64 = _clean_text(
        file_payload.get('content_base64') or file_payload.get('base64') or file_payload.get('content')
    )
    if not content_base64:
        raise BulkEnrollmentError('Debes enviar el contenido del Excel codificado en Base64.')

    if ',' in content_base64 and content_base64.lower().startswith('data:'):
        content_base64 = content_base64.split(',', 1)[1]

    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise BulkEnrollmentError('El contenido Base64 del Excel es invalido.') from exc

    return InMemoryExcelUpload(content, file_name)


def build_bulk_enrollment_template() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = 'Carga'
    worksheet.sheet_view.showGridLines = False

    header_fill = PatternFill('solid', fgColor='9B0E0E')
    header_font = Font(color='FFFFFF', bold=True)
    required_fill = PatternFill('solid', fgColor='FCECEC')
    thin = Side(style='thin', color='D9DEE3')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    widths = [24, 26, 18, 34, 22, 22, 24, 22, 34]

    for column_index, (header, note) in enumerate(TEMPLATE_HEADERS, start=1):
        cell = worksheet.cell(row=1, column=column_index, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.comment = Comment(note, 'INTEC')
        worksheet.column_dimensions[get_column_letter(column_index)].width = widths[column_index - 1]

    for row in range(2, 202):
        for column in range(1, len(TEMPLATE_HEADERS) + 1):
            cell = worksheet.cell(row=row, column=column)
            cell.border = border
            cell.alignment = Alignment(vertical='center')
            if column <= 5:
                cell.fill = required_fill
        worksheet[f'C{row}'].number_format = '@'
        worksheet[f'E{row}'].number_format = '@'

    worksheet.freeze_panes = 'A2'
    worksheet.auto_filter.ref = 'A1:I201'
    worksheet.row_dimensions[1].height = 32

    instructions = workbook.create_sheet('Instrucciones')
    instructions.sheet_view.showGridLines = False
    instructions['A1'] = 'Plantilla valida para Matricula masiva'
    instructions['A1'].font = Font(bold=True, size=14, color='9B0E0E')
    instructions['A3'] = 'Columnas obligatorias'
    instructions['A3'].font = Font(bold=True)
    instructions['A4'] = 'Nombres, Apellidos, Cedula, Correo, Numero de celular.'
    instructions['A6'] = 'Columnas opcionales'
    instructions['A6'].font = Font(bold=True)
    instructions['A7'] = 'Ocupacion, Empresa, Localidad, Direccion.'
    instructions['A9'] = 'Reglas importantes'
    instructions['A9'].font = Font(bold=True)
    instructions['A10'] = '1. No cambies los encabezados de la hoja Carga.'
    instructions['A11'] = '2. Llena desde la fila 2. No agregues titulos antes de la fila 1.'
    instructions['A12'] = '3. La carrera, curso y periodo se seleccionan en el dashboard, no en el Excel.'
    instructions['A13'] = '4. Cedula y Numero de celular estan como texto para conservar ceros iniciales.'
    instructions['A14'] = '5. El archivo debe guardarse como .xlsx.'
    instructions.column_dimensions['A'].width = 115

    example = workbook.create_sheet('Ejemplo')
    example.sheet_view.showGridLines = False
    for column_index, (header, _note) in enumerate(TEMPLATE_HEADERS, start=1):
        cell = example.cell(row=1, column=column_index, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        example.column_dimensions[get_column_letter(column_index)].width = widths[column_index - 1]
    example.append(
        [
            'Juan Roman',
            'Recalde Romo',
            '00123456789',
            'juan.roman@correo.com',
            '0999999999',
            'Asistente administrativo',
            'Empresa ABC',
            'Quito',
            'Av. Principal 123',
        ]
    )
    for row in example.iter_rows(min_row=2, max_row=2, min_col=1, max_col=len(TEMPLATE_HEADERS)):
        for cell in row:
            cell.border = border
    example['C2'].number_format = '@'
    example['E2'].number_format = '@'

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def process_bulk_enrollment_excel(uploaded_file: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    if not uploaded_file:
        raise BulkEnrollmentError('Debes seleccionar un archivo Excel para procesar.')

    file_name = str(getattr(uploaded_file, 'name', '') or '').strip()
    if not file_name.lower().endswith('.xlsx'):
        raise BulkEnrollmentError('El archivo debe estar en formato .xlsx.')

    file_size = int(getattr(uploaded_file, 'size', 0) or 0)
    if file_size > MAX_UPLOAD_BYTES:
        raise BulkEnrollmentError('El archivo excede el tamano maximo permitido para carga masiva.')

    clean_defaults = _clean_defaults(defaults)
    rows = _read_excel_rows(uploaded_file)
    if not rows:
        raise BulkEnrollmentError('El archivo no contiene filas para procesar.')
    if len(rows) > MAX_BULK_ROWS:
        raise BulkEnrollmentError(f'La carga masiva permite hasta {MAX_BULK_ROWS} filas por archivo.')

    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = _payload_from_row(row, clean_defaults)
            result = create_mass_matriculation_and_credentials(payload)
            welcome_email_result = result.get('welcome_email_result', {})
            welcome_email_sent = bool(welcome_email_result.get('sent'))
            results.append(
                {
                    'ok': True,
                    'fila': row['fila'],
                    'nombre': payload['nombre'],
                    'cedula': payload['cedula'],
                    'email': payload['email'],
                    'matricula': result.get('matricula'),
                    'payment_link': result.get('payment_link'),
                    'email_sent': bool(result.get('email_result', {}).get('sent')),
                    'welcome_email_sent': welcome_email_sent,
                    'welcome_email_message': str(welcome_email_result.get('message') or ''),
                    'microsoft365_ok': bool(result.get('microsoft365', {}).get('ok')),
                    'message': (
                        'Procesado correctamente.'
                        if welcome_email_sent
                        else str(welcome_email_result.get('message') or 'Procesado, pero la bienvenida quedo pendiente.')
                    ),
                }
            )
        except Exception as exc:
            results.append(
                {
                    'ok': False,
                    'fila': row.get('fila'),
                    'nombre': _row_full_name(row),
                    'cedula': row.get('cedula', ''),
                    'email': row.get('email', ''),
                    'message': str(exc),
                }
            )

    successful = sum(1 for item in results if item['ok'])
    failed = len(results) - successful
    return {
        'total': len(results),
        'exitosos': successful,
        'fallidos': failed,
        'results': results,
    }


def _clean_defaults(defaults: dict[str, Any]) -> dict[str, str]:
    required = {
        'cod_anio_basica': 'Debes seleccionar la carrera para la matricula masiva.',
        'codigo_materia': 'Debes seleccionar el curso para la matricula masiva.',
        'codigo_periodo': 'Debes seleccionar el periodo para la matricula masiva.',
    }
    clean = {key: _clean_text(value) for key, value in defaults.items()}
    for key, message in required.items():
        if not clean.get(key):
            raise BulkEnrollmentError(message)

    return clean


def _read_excel_rows(uploaded_file: Any) -> list[dict[str, str]]:
    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError):
        pass

    try:
        workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
    except Exception as exc:
        raise BulkEnrollmentError('No fue posible leer el archivo Excel. Verifica que sea un .xlsx valido.') from exc

    worksheet = workbook['Carga'] if 'Carga' in workbook.sheetnames else workbook.active
    header_map: dict[int, str] | None = None
    rows: list[dict[str, str]] = []

    for excel_index, raw_row in enumerate(worksheet.iter_rows(values_only=True), start=1):
        values = [_cell_to_text(value) for value in raw_row]
        if not any(values):
            continue

        if header_map is None:
            header_map = _build_header_map(values)
            _validate_headers(header_map)
            continue

        mapped = {
            field_name: values[column_index] if column_index < len(values) else ''
            for column_index, field_name in header_map.items()
        }
        if not any(value for value in mapped.values()):
            continue
        mapped['fila'] = str(excel_index)
        rows.append(mapped)

    workbook.close()
    return rows


def _build_header_map(headers: list[str]) -> dict[int, str]:
    alias_map = {
        _normalize_header(alias): field_name
        for field_name, aliases in HEADER_ALIASES.items()
        for alias in aliases
    }
    header_map: dict[int, str] = {}
    for index, header in enumerate(headers):
        normalized = _normalize_header(header)
        field_name = alias_map.get(normalized)
        if field_name and field_name not in header_map.values():
            header_map[index] = field_name
    return header_map


def _validate_headers(header_map: dict[int, str]) -> None:
    found = set(header_map.values())
    has_name = 'nombre_completo' in found or {'nombres', 'apellidos'}.issubset(found)
    missing = []
    if not has_name:
        missing.append('Nombre completo o Nombres y Apellidos')
    for field_name, label in (
        ('cedula', 'Cedula'),
        ('email', 'Correo'),
        ('telefono', 'Numero de celular'),
    ):
        if field_name not in found:
            missing.append(label)

    if missing:
        raise BulkEnrollmentError('Faltan columnas obligatorias en el Excel: ' + ', '.join(missing) + '.')


def _payload_from_row(row: dict[str, str], defaults: dict[str, str]) -> dict[str, Any]:
    nombre = _row_full_name(row)
    cedula = re.sub(r'\D+', '', _clean_text(row.get('cedula')))
    email = _clean_text(row.get('email')).lower()
    telefono = _clean_text(row.get('telefono'))
    if not nombre:
        raise PaymentGatewayError('Falta nombre del estudiante.')
    if not cedula:
        raise PaymentGatewayError('Falta cedula del estudiante.')
    if not email:
        raise PaymentGatewayError('Falta correo del estudiante.')
    if not telefono:
        raise PaymentGatewayError('Falta numero de celular del estudiante.')

    direccion = _clean_text(row.get('direccion')) or DEFAULT_EMPTY_LOCATION
    localidad = _clean_text(row.get('localidad')) or DEFAULT_EMPTY_LOCATION
    course_name = _clean_text(defaults.get('nombre_materia'))
    descripcion = f'Matricula masiva del curso {course_name}' if course_name else 'Matricula masiva'

    return {
        'nombre': nombre,
        'cedula': cedula,
        'email': email,
        'telefono': telefono,
        'localidad': localidad,
        'direccion': direccion,
        'ocupacion': _clean_text(row.get('ocupacion')),
        'empresa': _clean_text(row.get('empresa')),
        'descripcion': descripcion,
        'nombre_materia': course_name,
        'carrera_num': defaults.get('carrera_num', ''),
        'cod_anio_basica': defaults['cod_anio_basica'],
        'codigo_materia': defaults['codigo_materia'],
        'codigo_periodo': defaults['codigo_periodo'],
        'estado_periodo': defaults.get('estado_periodo', ''),
        'data_treatment_accepted': True,
        'provider_payload': {
            'tipo': 'matricula_masiva_sin_cargo',
            'nombre': nombre,
            'cedula': cedula,
            'email': email,
            'telefono': telefono,
            'localidad': localidad,
            'direccion': direccion,
            'ocupacion': _clean_text(row.get('ocupacion')),
            'empresa': _clean_text(row.get('empresa')),
            'descripcion': descripcion,
            'nombre_materia': course_name,
            'carrera_num': defaults.get('carrera_num', ''),
            'cod_anio_basica': defaults['cod_anio_basica'],
            'codigo_materia': defaults['codigo_materia'],
            'codigo_periodo': defaults['codigo_periodo'],
            'estado_periodo': defaults.get('estado_periodo', ''),
        },
    }


def _row_full_name(row: dict[str, str]) -> str:
    full_name = _clean_text(row.get('nombre_completo'))
    if full_name:
        return full_name
    return _clean_text(f"{_clean_text(row.get('nombres'))} {_clean_text(row.get('apellidos'))}")


def _cell_to_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value).strip()
    return _clean_text(value)


def _clean_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())


def _normalize_header(value: Any) -> str:
    normalized = unicodedata.normalize('NFD', str(value or '').strip().lower())
    without_accents = ''.join(ch for ch in normalized if unicodedata.category(ch) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', without_accents).strip()
