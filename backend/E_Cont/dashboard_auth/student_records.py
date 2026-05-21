from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import connection


class StudentLookupError(Exception):
    pass


def lookup_student_inscription(email: str, matricula: str) -> dict[str, Any]:
    clean_email = _clean_text(email)
    clean_matricula = _clean_text(matricula)

    if not clean_email or not clean_matricula:
        raise StudentLookupError('Debes completar el correo y la matricula para consultar la inscripcion.')

    student = _find_registered_student(clean_email, clean_matricula)
    if student is None:
        mismatch_message = _build_mismatch_message(clean_email, clean_matricula)
        raise StudentLookupError(mismatch_message)

    enrollment = _fetch_enrollment_header(clean_matricula)
    payments = _fetch_payment_rows(clean_matricula, enrollment.get('codigo_periodo') if enrollment else None)

    return {
        'student': {
            'matricula': clean_matricula,
            'nombre': student.get('nombre') or 'Estudiante',
            'correo': student.get('correo') or clean_email,
            'cedula': student.get('cedula') or 'No disponible',
            'telefono': student.get('telefono') or 'No disponible',
            'periodo': student.get('periodo') or (enrollment.get('codigo_periodo') if enrollment else 'No disponible'),
            'fuente': student.get('source') or 'Registro institucional',
        },
        'inscription': {
            'estado': enrollment.get('estado') if enrollment else 'Sin matricula registrada',
            'tipo_matricula': enrollment.get('control_detalle') if enrollment else 'No disponible',
            'numero_matricula': enrollment.get('num_matricula') if enrollment else 'No disponible',
            'fecha_pago': enrollment.get('fecha_pago') if enrollment else 'No disponible',
            'valor_total': _format_decimal(enrollment.get('valor')) if enrollment else '0.00',
            'valor_inscripcion': _format_decimal(enrollment.get('inscrip_valor')) if enrollment else '0.00',
            'valor_matricula': _format_decimal(enrollment.get('matri_valor')) if enrollment else '0.00',
            'cuota_1': _format_decimal(enrollment.get('cuota_1')) if enrollment else '0.00',
            'descuento': _format_decimal(_sum_decimals(
                enrollment.get('descuento'),
                enrollment.get('descuento_pronto_pago'),
                enrollment.get('descuento_referidos'),
                enrollment.get('beca'),
            )) if enrollment else '0.00',
            'documentos': _build_documents(student, enrollment),
        },
        'payments_summary': {
            'registros': len(payments),
            'total_programado': _format_decimal(_sum_decimals(*[payment.get('valor') for payment in payments])),
            'total_reportado': _format_decimal(_sum_decimals(*[payment.get('valor_registrado') for payment in payments])),
            'ultimo_registro': payments[0]['fecha_pago'] if payments else 'No disponible',
        },
        'payments': [
            {
                'cuota': payment['num'],
                'periodo': payment['codperiodo'],
                'concepto': payment['detalle'],
                'fecha_pago': payment['fecha_pago'],
                'valor': _format_decimal(payment.get('valor')),
                'banco': payment['banco'],
                'numero_deposito': payment['no_deposito'],
                'fecha_deposito': payment['fecha_deposito'],
                'valor_registrado': _format_decimal(payment.get('valor_registrado')),
                'referencia': payment['referencia'],
                'soporte': payment['url_deposito'],
            }
            for payment in payments
        ],
    }


def _find_registered_student(email: str, matricula: str) -> dict[str, Any] | None:
    preinscription_query = """
        SELECT TOP (1)
            CAST(Codestu AS varchar(50)) AS matricula,
            Apellidos_nombre AS nombre,
            correo,
            Cedula,
            telefono,
            CAST(codperiodo AS varchar(50)) AS periodo,
            'PREINSCRIPCION' AS source,
            urlcedula,
            urltitulo,
            urldeposito,
            urlconvenio
        FROM dbo.PREINSCRIPCION
        WHERE LTRIM(RTRIM(CAST(Codestu AS varchar(50)))) = %s
          AND LOWER(LTRIM(RTRIM(ISNULL(correo, '')))) = LOWER(%s)
        ORDER BY codperiodo DESC, Fecha_Ingreso DESC
    """
    row = _fetch_one(preinscription_query, [matricula, email])
    if row is not None:
        return {
            'matricula': _clean_text(row.get('matricula')),
            'nombre': _clean_text(row.get('nombre')),
            'correo': _clean_text(row.get('correo')),
            'cedula': _clean_text(row.get('Cedula')),
            'telefono': _clean_text(row.get('telefono')),
            'periodo': _clean_text(row.get('periodo')),
            'source': _clean_text(row.get('source')),
            'urlcedula': _clean_text(row.get('urlcedula')),
            'urltitulo': _clean_text(row.get('urltitulo')),
            'urldeposito': _clean_text(row.get('urldeposito')),
            'urlconvenio': _clean_text(row.get('urlconvenio')),
        }

    student_query = """
        SELECT TOP (1)
            CAST(codestud AS varchar(50)) AS matricula,
            Nombres AS nombre,
            CorreoPersonal,
            CorreoIntec,
            CAST(Periodo AS varchar(50)) AS periodo,
            'CORREOSESTUDINTEC' AS source
        FROM dbo.CorreosEstudIntec
        WHERE LTRIM(RTRIM(CAST(codestud AS varchar(50)))) = %s
          AND (
              LOWER(LTRIM(RTRIM(ISNULL(CorreoPersonal, '')))) = LOWER(%s)
              OR LOWER(LTRIM(RTRIM(ISNULL(CorreoIntec, '')))) = LOWER(%s)
          )
        ORDER BY Periodo DESC
    """
    row = _fetch_one(student_query, [matricula, email, email])
    if row is None:
        return None

    return {
        'matricula': _clean_text(row.get('matricula')),
        'nombre': _clean_text(row.get('nombre')),
        'correo': _clean_text(row.get('CorreoIntec')) or _clean_text(row.get('CorreoPersonal')),
        'cedula': None,
        'telefono': None,
        'periodo': _clean_text(row.get('periodo')),
        'source': _clean_text(row.get('source')),
        'urlcedula': None,
        'urltitulo': None,
        'urldeposito': None,
        'urlconvenio': None,
    }


def _build_mismatch_message(email: str, matricula: str) -> str:
    matricula_query = """
        SELECT TOP (1) correo
        FROM dbo.PREINSCRIPCION
        WHERE LTRIM(RTRIM(CAST(Codestu AS varchar(50)))) = %s
        ORDER BY codperiodo DESC, Fecha_Ingreso DESC
    """
    row = _fetch_one(matricula_query, [matricula])
    if row is not None:
        return (
            'La matricula fue localizada, pero el correo no coincide con el registro de preinscripcion '
            'del estudiante.'
        )

    student_query = """
        SELECT TOP (1) codestud
        FROM dbo.CorreosEstudIntec
        WHERE LTRIM(RTRIM(CAST(codestud AS varchar(50)))) = %s
    """
    row = _fetch_one(student_query, [matricula])
    if row is not None:
        return (
            'La matricula existe en el sistema academico, pero el correo enviado no coincide con el '
            'correo registrado del estudiante.'
        )

    email_query = """
        SELECT TOP (1) Codestu
        FROM dbo.PREINSCRIPCION
        WHERE LOWER(LTRIM(RTRIM(ISNULL(correo, '')))) = LOWER(%s)
        ORDER BY codperiodo DESC, Fecha_Ingreso DESC
    """
    row = _fetch_one(email_query, [email])
    if row is not None:
        return 'El correo fue localizado, pero la matricula no corresponde al registro del estudiante.'

    return 'No encontramos una inscripcion con ese correo y matricula.'


def _fetch_enrollment_header(matricula: str) -> dict[str, Any] | None:
    query = """
        SELECT TOP (1)
            CAST(codigo_periodo AS varchar(50)) AS codigo_periodo,
            CAST(Num_Matricula AS varchar(50)) AS num_matricula,
            CONVERT(varchar(10), fecha_pago, 23) AS fecha_pago,
            valor,
            InscripValor AS inscrip_valor,
            MatriValor AS matri_valor,
            Cuota1 AS cuota_1,
            Descuento AS descuento,
            Descuentoprontopago AS descuento_pronto_pago,
            Descuentoreferidos AS descuento_referidos,
            Beca AS beca,
            RTRIM(ISNULL(ESTADOMATRICULA.Estado, '')) AS estado,
            RTRIM(ISNULL(CONTROLMATRICULA.detalle, '')) AS control_detalle,
            CABECERA_MATRICULA.urlcedula,
            CABECERA_MATRICULA.urltitulo,
            CABECERA_MATRICULA.urldeposito,
            CABECERA_MATRICULA.urlconvenio
        FROM dbo.CABECERA_MATRICULA
        LEFT JOIN dbo.ESTADOMATRICULA
          ON ESTADOMATRICULA.numestado = CABECERA_MATRICULA.codestadoMat
        LEFT JOIN dbo.CONTROLMATRICULA
          ON CONTROLMATRICULA.num = CABECERA_MATRICULA.ControlMatricula
        WHERE LTRIM(RTRIM(CAST(codigo_estud AS varchar(50)))) = %s
        ORDER BY codigo_periodo DESC, Num_Matricula DESC
    """
    row = _fetch_one(query, [matricula])
    if row is None:
        return None

    return {
        'codigo_periodo': _clean_text(row.get('codigo_periodo')),
        'num_matricula': _clean_text(row.get('num_matricula')) or 'No disponible',
        'fecha_pago': _clean_text(row.get('fecha_pago')) or 'No disponible',
        'valor': row.get('valor'),
        'inscrip_valor': row.get('inscrip_valor'),
        'matri_valor': row.get('matri_valor'),
        'cuota_1': row.get('cuota_1'),
        'descuento': row.get('descuento'),
        'descuento_pronto_pago': row.get('descuento_pronto_pago'),
        'descuento_referidos': row.get('descuento_referidos'),
        'beca': row.get('beca'),
        'estado': _clean_text(row.get('estado')) or 'Sin estado',
        'control_detalle': _clean_text(row.get('control_detalle')) or 'No disponible',
        'urlcedula': _clean_text(row.get('urlcedula')),
        'urltitulo': _clean_text(row.get('urltitulo')),
        'urldeposito': _clean_text(row.get('urldeposito')),
        'urlconvenio': _clean_text(row.get('urlconvenio')),
    }


def _fetch_payment_rows(matricula: str, periodo: str | None) -> list[dict[str, Any]]:
    if periodo:
        query = """
            SELECT TOP (12)
                CAST(Num AS varchar(50)) AS num,
                CAST(codperiodo AS varchar(50)) AS codperiodo,
                CONVERT(varchar(10), fechapago, 23) AS fecha_pago,
                Detalle,
                Valor AS valor,
                Banco,
                NoDeposito,
                CONVERT(varchar(10), FechaDeposito, 23) AS fecha_deposito,
                ValorRegistrado AS valor_registrado,
                Referencia,
                urldeposito
            FROM dbo.REGISTROPAGOS
            WHERE LTRIM(RTRIM(CAST(Codestu AS varchar(50)))) = %s
              AND LTRIM(RTRIM(CAST(codperiodo AS varchar(50)))) = %s
            ORDER BY Num DESC
        """
        rows = _fetch_all(query, [matricula, periodo])
        if rows:
            return [_serialize_payment_row(row) for row in rows]

    query = """
        SELECT TOP (12)
            CAST(Num AS varchar(50)) AS num,
            CAST(codperiodo AS varchar(50)) AS codperiodo,
            CONVERT(varchar(10), fechapago, 23) AS fecha_pago,
            Detalle,
            Valor AS valor,
            Banco,
            NoDeposito,
            CONVERT(varchar(10), FechaDeposito, 23) AS fecha_deposito,
            ValorRegistrado AS valor_registrado,
            Referencia,
            urldeposito
        FROM dbo.REGISTROPAGOS
        WHERE LTRIM(RTRIM(CAST(Codestu AS varchar(50)))) = %s
        ORDER BY codperiodo DESC, Num DESC
    """
    return [_serialize_payment_row(row) for row in _fetch_all(query, [matricula])]


def _serialize_payment_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'num': _clean_text(row.get('num')) or 'No disponible',
        'codperiodo': _clean_text(row.get('codperiodo')) or 'No disponible',
        'fecha_pago': _clean_text(row.get('fecha_pago')) or 'No disponible',
        'detalle': _clean_text(row.get('Detalle')) or 'Sin detalle',
        'valor': row.get('valor'),
        'banco': _clean_text(row.get('Banco')) or 'No reportado',
        'no_deposito': _clean_text(row.get('NoDeposito')) or 'No reportado',
        'fecha_deposito': _clean_text(row.get('fecha_deposito')) or 'No reportado',
        'valor_registrado': row.get('valor_registrado'),
        'referencia': _clean_text(row.get('Referencia')) or 'No reportada',
        'url_deposito': _clean_text(row.get('urldeposito')) or 'No disponible',
    }


def _build_documents(student: dict[str, Any], enrollment: dict[str, Any] | None) -> list[dict[str, str]]:
    documents = []
    for label, key in [
        ('Cedula', 'urlcedula'),
        ('Titulo', 'urltitulo'),
        ('Deposito', 'urldeposito'),
        ('Convenio', 'urlconvenio'),
    ]:
        value = student.get(key) or (enrollment.get(key) if enrollment else None)
        if value:
            documents.append({'label': label, 'file': value})
    return documents


def _fetch_one(query: str, params: list[Any]) -> dict[str, Any] | None:
    rows = _fetch_all(query, params)
    return rows[0] if rows else None


def _fetch_all(query: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace('\r', '').replace('\n', '').strip()
    return text or None


def _format_decimal(value: Any) -> str:
    if value is None:
        return '0.00'
    try:
        return f'{Decimal(value):.2f}'
    except (InvalidOperation, ValueError, TypeError):
        return '0.00'


def _sum_decimals(*values: Any) -> Decimal:
    total = Decimal('0')
    for value in values:
        if value is None:
            continue
        try:
            total += Decimal(value)
        except (InvalidOperation, ValueError, TypeError):
            continue
    return total
