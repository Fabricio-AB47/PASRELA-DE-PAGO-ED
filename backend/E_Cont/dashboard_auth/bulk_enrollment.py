from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .inscription_certificate import (
    build_certificate_payload,
    create_stored_certificate_record,
    send_certificate_email,
)
from .payments import PaymentGatewayError, create_mass_matriculation_and_credentials
from .student_registration import (
    RegisteredUserExistsError,
    USER_REGISTERED_MESSAGE,
    ensure_user_is_not_registered,
)


class BulkEnrollmentError(Exception):
    pass


@dataclass
class InMemoryExcelUpload:
    name: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)

    def seek(self, _position: int) -> None:
        return None

    def read(self) -> bytes:
        return self.content


TEMPLATE_FILE_NAME = 'plantilla_matricula_masiva.xlsx'
TEMPLATE_HEADERS = [
    'Nombres',
    'Apellidos',
    'Cédula',
    'Correo',
    'Número de celular',
    'Ocupación',
    'Empresa',
    'Localidad',
    'Dirección',
]
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_BULK_ROWS = 500
DEFAULT_EMPTY_LOCATION = 'No registrado'


HEADER_ALIASES = {
    'nombres': 'nombres',
    'nombre': 'nombres',
    'nombre completo': 'nombre_completo',
    'apellidos': 'apellidos',
    'apellido': 'apellidos',
    'cedula': 'cedula',
    'cédula': 'cedula',
    'identificacion': 'cedula',
    'identificación': 'cedula',
    'correo': 'email',
    'email': 'email',
    'correo electronico': 'email',
    'correo electrónico': 'email',
    'numero de celular': 'telefono',
    'número de celular': 'telefono',
    'celular': 'telefono',
    'telefono': 'telefono',
    'teléfono': 'telefono',
    'ocupacion': 'ocupacion',
    'ocupación': 'ocupacion',
    'empresa': 'empresa',
    'localidad': 'localidad',
    'direccion': 'direccion',
    'dirección': 'direccion',
}


def build_bulk_enrollment_template() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Carga'
    instructions = workbook.create_sheet('Instrucciones')
    example = workbook.create_sheet('Ejemplo')

    header_fill = PatternFill('solid', fgColor='9B0E0E')
    header_font = Font(color='FFFFFF', bold=True)
    border = Border(bottom=Side(style='thin', color='D9D9D9'))

    for column_index, header in enumerate(TEMPLATE_HEADERS, start=1):
        cell = sheet.cell(row=1, column=column_index, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
        cell.border = border
        sheet.column_dimensions[cell.column_letter].width = max(len(header) + 8, 20)

    for column in ('C', 'E'):
        sheet[f'{column}2'].number_format = '@'
        sheet.column_dimensions[column].width = 22

    instructions['A1'] = 'Plantilla válida para matrícula masiva'
    instructions['A1'].font = Font(bold=True, size=14, color='9B0E0E')
    instructions['A3'] = 'Columnas obligatorias: Nombres, Apellidos, Cédula, Correo, Número de celular.'
    instructions['A4'] = 'Columnas opcionales: Ocupación, Empresa, Localidad, Dirección.'
    instructions['A5'] = 'No cambies los nombres de las columnas.'
    instructions.column_dimensions['A'].width = 90

    for column_index, header in enumerate(TEMPLATE_HEADERS, start=1):
        cell = example.cell(row=1, column=column_index, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
        cell.border = border
        example.column_dimensions[cell.column_letter].width = max(len(header) + 8, 22)

    example.append(
        [
            'Juan Carlos',
            'Recalde Romo',
            '0012345678',
            'juan.recalde@example.com',
            '0999999999',
            'Analista',
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


def excel_upload_from_json(payload: dict[str, Any]) -> InMemoryExcelUpload:
    excel_payload = payload.get('excel') if isinstance(payload, dict) else None
    if not isinstance(excel_payload, dict):
        raise BulkEnrollmentError('Debes enviar el archivo Excel en el campo excel.')

    file_name = str(excel_payload.get('name') or '').strip()
    content_base64 = str(excel_payload.get('content_base64') or '').strip()
    if not file_name:
        raise BulkEnrollmentError('El archivo Excel debe incluir nombre.')
    if not file_name.lower().endswith('.xlsx'):
        raise BulkEnrollmentError('El archivo debe estar en formato .xlsx.')
    if not content_base64:
        raise BulkEnrollmentError('El archivo Excel esta vacio.')

    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise BulkEnrollmentError('El contenido del Excel no es base64 valido.') from exc

    upload = InMemoryExcelUpload(name=file_name, content=content)
    if upload.size > MAX_UPLOAD_BYTES:
        raise BulkEnrollmentError('El archivo excede el tamano maximo permitido para carga masiva.')
    return upload


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
            try:
                ensure_user_is_not_registered(
                    payload['cedula'],
                    cod_anio_basica=payload['cod_anio_basica'],
                    codigo_materia=payload['codigo_materia'],
                    codigo_periodo=payload['codigo_periodo'],
                )
            except RegisteredUserExistsError:
                results.append(
                    {
                        'ok': False,
                        'fila': row['fila'],
                        'nombre': payload['nombre'],
                        'cedula': payload['cedula'],
                        'email': payload['email'],
                        'registered': True,
                        'message': USER_REGISTERED_MESSAGE,
                    }
                )
                continue
            result = create_mass_matriculation_and_credentials(payload)
            welcome_email_result = result.get('welcome_email_result', {})
            welcome_email_sent = bool(welcome_email_result.get('sent'))
            official_record = result.get('official_sync', {}).get('record') or {}
            certificate_payload = build_certificate_payload(payload, result, source='matricula_masiva')
            certificate_record = create_stored_certificate_record(certificate_payload)
            try:
                certificate_email_result = send_certificate_email(
                    recipient_email=payload['email'],
                    recipient_name=payload['nombre'],
                    certificate_record=certificate_record,
                )
            except Exception as exc:
                certificate_email_result = {
                    'sent': False,
                    'message': f'Certificado generado, pero no fue posible enviarlo por correo: {str(exc)}',
                    'filename': certificate_record.get('filename'),
                }
            results.append(
                {
                    'ok': True,
                    'fila': row['fila'],
                    'nombre': payload['nombre'],
                    'cedula': payload['cedula'],
                    'email': payload['email'],
                    'matricula': result.get('matricula'),
                    'codigo_materia': official_record.get('codigo_materia') or payload['codigo_materia'],
                    'materia': official_record.get('materia') or payload.get('nombre_materia'),
                    'welcome_email_sent': welcome_email_sent,
                    'welcome_email_message': str(welcome_email_result.get('message') or ''),
                    'microsoft365_ok': bool(result.get('microsoft365', {}).get('ok')),
                    'certificate': certificate_record,
                    'certificate_email_sent': bool(certificate_email_result.get('sent')),
                    'certificate_email_message': str(certificate_email_result.get('message') or ''),
                    'message': (
                        'Procesado correctamente.'
                        if welcome_email_sent
                        else str(welcome_email_result.get('message') or 'Procesado, pero la bienvenida quedó pendiente.')
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
        'cod_anio_basica': 'Debes seleccionar la carrera para la matrícula masiva.',
        'codigo_materia': 'Debes seleccionar el curso para la matrícula masiva.',
        'codigo_periodo': 'Debes seleccionar el período para la matrícula masiva.',
    }
    cleaned = {key: str(value or '').strip() for key, value in defaults.items()}
    for field, message in required.items():
        if not cleaned.get(field):
            raise BulkEnrollmentError(message)

    estado_periodo = cleaned.get('estado_periodo', '').lower()
    if estado_periodo and estado_periodo != 'activo':
        raise BulkEnrollmentError('El período seleccionado está inactivo.')
    return cleaned


def _read_excel_rows(uploaded_file: Any) -> list[dict[str, str]]:
    try:
        raw_content = uploaded_file.read()
        workbook = load_workbook(filename=BytesIO(raw_content), data_only=True)
    except Exception as exc:
        raise BulkEnrollmentError('No fue posible leer el archivo Excel. Verifica el formato.') from exc

    try:
        worksheet = workbook['Carga'] if 'Carga' in workbook.sheetnames else workbook.active
        header_map = _header_map(worksheet)
        rows: list[dict[str, str]] = []

        for row_index in range(2, worksheet.max_row + 1):
            row_data: dict[str, str] = {'fila': row_index}
            has_value = False
            for column_index, field_name in header_map.items():
                value = worksheet.cell(row=row_index, column=column_index).value
                text = _clean_text(value)
                if text:
                    has_value = True
                row_data[field_name] = text
            if has_value:
                rows.append(row_data)
        return rows
    finally:
        workbook.close()


def _header_map(worksheet: Any) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for column_index in range(1, worksheet.max_column + 1):
        header = _normalize_header(worksheet.cell(row=1, column=column_index).value)
        field_name = HEADER_ALIASES.get(header)
        if field_name:
            mapping[column_index] = field_name

    required = {'cedula', 'email', 'telefono'}
    if not ({'nombre_completo'} <= set(mapping.values()) or {'nombres', 'apellidos'} <= set(mapping.values())):
        raise BulkEnrollmentError('El Excel debe incluir Nombre completo o Nombres y Apellidos.')
    missing = required - set(mapping.values())
    if missing:
        raise BulkEnrollmentError('Faltan columnas obligatorias: ' + ', '.join(sorted(missing)))
    return mapping


def _payload_from_row(row: dict[str, str], defaults: dict[str, str]) -> dict[str, Any]:
    nombre = _row_full_name(row)
    cedula = re.sub(r'\D+', '', _clean_text(row.get('cedula')))
    email = _clean_text(row.get('email')).lower()
    telefono = _clean_text(row.get('telefono'))
    if not nombre:
        raise PaymentGatewayError('Falta nombre del estudiante.')
    if not cedula:
        raise PaymentGatewayError('Falta cédula del estudiante.')
    if not email:
        raise PaymentGatewayError('Falta correo del estudiante.')
    if not telefono:
        raise PaymentGatewayError('Falta numero de celular del estudiante.')

    direccion = _clean_text(row.get('direccion')) or DEFAULT_EMPTY_LOCATION
    localidad = _clean_text(row.get('localidad')) or DEFAULT_EMPTY_LOCATION
    course_name = _clean_text(defaults.get('nombre_materia'))
    descripcion = f'Matrícula masiva del curso {course_name}' if course_name else 'Matrícula masiva'

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
    nombre_completo = _clean_text(row.get('nombre_completo'))
    if nombre_completo:
        return nombre_completo
    nombres = _clean_text(row.get('nombres'))
    apellidos = _clean_text(row.get('apellidos'))
    return _clean_text(f'{nombres} {apellidos}')


def _normalize_header(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())


def _clean_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())
