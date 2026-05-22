from __future__ import annotations

import json
import os
import re
import secrets
from base64 import b64encode, urlsafe_b64decode
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.db import IntegrityError, connection, transaction

from .inscription_catalogs import (
    calculate_inscription_amount,
    get_pensum_status_column,
    is_catalog_value_active,
)
from .microsoft365 import (
    Microsoft365Error,
    Microsoft365ValidationError,
    build_intec_account_identity,
    create_microsoft365_user,
)


class PaymentGatewayError(Exception):
    pass


class ProviderHttpError(PaymentGatewayError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f'Error del proveedor ({status_code}): {detail}')


DEFAULT_PAYMENT_RECEIPT_EMAIL = 'DeptCobranzas@intec.edu.ec'
GRAPH_MAIL_SEND_ROLE = 'Mail.Send'


def create_payment_link_and_notify(payload: dict[str, Any]) -> dict[str, Any]:
    email = str(payload.get('email') or '').strip()
    nombre = str(payload.get('nombre') or '').strip()
    cedula = str(payload.get('cedula') or '').strip()
    matricula = str(payload.get('matricula') or '').strip()
    monto = payload.get('monto')
    descripcion = str(payload.get('descripcion') or 'Pago de inscripcion').strip()
    data_treatment_accepted = bool(payload.get('data_treatment_accepted'))
    cod_anio_basica = str(payload.get('cod_anio_basica') or '').strip()
    codigo_materia = str(payload.get('codigo_materia') or '').strip()
    codigo_periodo = str(payload.get('codigo_periodo') or '').strip()
    estado_periodo = str(payload.get('estado_periodo') or '').strip().lower()

    if not data_treatment_accepted:
        raise PaymentGatewayError(
            'No es posible completar la inscripcion sin aceptar el tratamiento de datos personales.'
        )

    if not email:
        raise PaymentGatewayError('Debes enviar el correo del estudiante para generar y enviar el pago.')

    if not cedula:
        raise PaymentGatewayError('Debes registrar el numero de cedula para completar la inscripcion.')

    if not re.fullmatch(r'\d{6,20}', cedula):
        raise PaymentGatewayError('La cedula debe contener solo numeros (entre 6 y 20 digitos).')

    if not cod_anio_basica:
        raise PaymentGatewayError('Debes seleccionar la carrera (Cod_AnioBasica) para registrar el curso.')

    if not codigo_materia:
        raise PaymentGatewayError('Debes seleccionar el curso a seguir antes de continuar.')

    if not codigo_periodo:
        raise PaymentGatewayError('Debes seleccionar el periodo para continuar con la inscripcion.')

    if estado_periodo and estado_periodo != 'activo':
        raise PaymentGatewayError(
            'El periodo seleccionado esta inactivo. Debes elegir un periodo con estado Activo.'
        )

    if not matricula:
        matricula = generate_unique_numcodigo()

    monto_calculado = _resolve_monto_from_pensum(cod_anio_basica, codigo_materia)
    if monto_calculado is not None:
        monto = f'{monto_calculado:.2f}'
    elif monto in (None, '', 0, '0'):
        raise PaymentGatewayError(
            'No fue posible calcular el monto del curso con Horas x ValorHoraVirtual en PENSUM.'
        )

    if _cabecera_has_numcodigo(matricula):
        raise PaymentGatewayError(
            'El numero de matricula generado ya existe en CABECERA_MATRICULA. '
            'Solicita un nuevo numero para continuar.'
        )

    _ensure_inscription_registry_table()

    descripcion = _compose_course_payment_description(payload, descripcion)

    inscription_id = _register_inscription_request(
        cedula=cedula,
        email=email,
        nombre=nombre,
        matricula=matricula,
        monto=monto,
        descripcion=descripcion,
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
        estado_periodo=estado_periodo,
        payment_link='PENDIENTE',
        provider_response={'status': 'pendiente', 'source': 'form-submit'},
    )

    official_record = _upsert_official_inscription_records(
        cedula=cedula,
        nombre=nombre,
        email=email,
        telefono=_first_non_empty(payload.get('telefono'), payload.get('provider_payload', {}).get('telefono') if isinstance(payload.get('provider_payload'), dict) else None),
        direccion=_first_non_empty(payload.get('direccion'), payload.get('provider_payload', {}).get('direccion') if isinstance(payload.get('provider_payload'), dict) else None),
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
        matricula=matricula,
        monto=monto,
        descripcion=descripcion,
        payment_link='PENDIENTE',
    )
    official_sync_result: dict[str, Any] = {'ok': True, 'message': 'Sincronizacion oficial completada.'}
    microsoft365_result: dict[str, Any] = {'ok': False, 'message': 'No ejecutado.'}
    welcome_email_result: dict[str, Any] = {'sent': False, 'message': 'No ejecutado.'}
    try:
        intec_account = build_intec_account_identity(nombre=nombre, cedula=cedula)
    except Microsoft365ValidationError as exc:
        raise PaymentGatewayError(str(exc)) from exc

    try:
        microsoft365_user = create_microsoft365_user(
            {
                'nombre_completo': nombre,
                'cedula': cedula,
            }
        )
        microsoft365_result = {
            'ok': True,
            'message': 'Usuario Microsoft 365 creado y licenciado correctamente.',
            'user': microsoft365_user,
        }
        resolved_intec_email = str(microsoft365_user.get('correo') or intec_account['correo']).strip()
        if resolved_intec_email:
            intec_account['correo'] = resolved_intec_email
            if official_record:
                _update_official_intec_credentials(
                    codigo_estud=official_record['codigo_estud'],
                    correo_intec=resolved_intec_email,
                    password_temporal=intec_account['password_temporal'],
                )
        try:
            welcome_email_result = _send_intec_welcome_email(
                recipient_email=email,
                recipient_name=nombre,
                intec_email=intec_account['correo'],
                password=intec_account['password_temporal'],
                course_name=_resolve_welcome_course_name(payload),
            )
            if welcome_email_result.get('sent') and official_record:
                _mark_correos_estud_intec_sent(official_record['codigo_estud'])
        except PaymentGatewayError as exc:
            welcome_email_result = {
                'sent': False,
                'message': f'Usuario Microsoft 365 creado, pero no fue posible enviar bienvenida: {str(exc)}',
            }
        microsoft365_result['welcome_email'] = welcome_email_result
    except Microsoft365Error as exc:
        microsoft365_result = {
            'ok': False,
            'message': str(exc),
        }
        _update_inscription_request_result(
            inscription_id=inscription_id,
            payment_link='ERROR_MICROSOFT365',
            provider_response={
                'status': 'error',
                'source': 'microsoft365',
                'message': str(exc),
                'official_sync': official_sync_result,
            },
        )
        raise PaymentGatewayError(f'No fue posible crear el usuario Microsoft 365: {str(exc)}') from exc

    provider_payload = _build_alldigital_payload(
        raw_payload=payload,
        email=email,
        nombre=nombre,
        cedula=cedula,
        matricula=matricula,
        monto=monto,
        descripcion=descripcion,
    )

    try:
        provider_response = _call_payment_provider(provider_payload)
    except Exception as exc:
        _update_inscription_request_result(
            inscription_id=inscription_id,
            payment_link='ERROR',
            provider_response={'status': 'error', 'message': str(exc)},
        )
        raise

    payment_link = _extract_payment_link(provider_response)
    if not payment_link:
        _update_inscription_request_result(
            inscription_id=inscription_id,
            payment_link='SIN_LINK',
            provider_response=provider_response,
        )
        raise PaymentGatewayError(
            'La pasarela no devolvio una direccion de pago utilizable. '
            'Revisa la respuesta del proveedor.'
        )

    receipt_email = _resolve_payment_receipt_email()

    try:
        email_result = _send_payment_link_email(
            recipient_email=email,
            recipient_name=nombre,
            payment_link=payment_link,
            matricula=matricula,
            monto=monto,
            receipt_email=receipt_email,
        )
    except PaymentGatewayError as exc:
        email_result = {
            'sent': False,
            'message': (
                'Se genero el enlace de pago, pero no fue posible enviar el correo: '
                f'{str(exc)}'
            ),
        }

    if official_record:
        try:
            _update_official_links_after_payment(
                codigo_estud=official_record['codigo_estud'],
                cod_anio_basica=cod_anio_basica,
                codigo_periodo=codigo_periodo,
                payment_link=payment_link,
            )
        except Exception as exc:
            official_sync_result = {
                'ok': False,
                'message': f'Enlace generado, pero no se actualizo referencia oficial: {str(exc)}',
            }

    _update_inscription_request_result(
        inscription_id=inscription_id,
        payment_link=payment_link,
        provider_response={
            'provider': provider_response,
            'email_result': email_result,
            'welcome_email_result': welcome_email_result,
            'official_sync': official_sync_result,
            'microsoft365': microsoft365_result,
            'receipt_email': receipt_email,
            'status': 'completado',
        },
    )

    return {
        'matricula': matricula,
        'monto': monto,
        'payment_link': payment_link,
        'receipt_email': receipt_email,
        'provider_response': provider_response,
        'email_result': email_result,
        'welcome_email_result': welcome_email_result,
        'official_sync': official_sync_result,
        'microsoft365': microsoft365_result,
    }


def create_mass_matriculation_and_credentials(payload: dict[str, Any]) -> dict[str, Any]:
    email = str(payload.get('email') or '').strip()
    nombre = str(payload.get('nombre') or '').strip()
    cedula = str(payload.get('cedula') or '').strip()
    matricula = str(payload.get('matricula') or '').strip()
    monto = '0.00'
    descripcion = str(payload.get('descripcion') or 'Matricula masiva').strip()
    cod_anio_basica = str(payload.get('cod_anio_basica') or '').strip()
    codigo_materia = str(payload.get('codigo_materia') or '').strip()
    codigo_periodo = str(payload.get('codigo_periodo') or '').strip()
    estado_periodo = str(payload.get('estado_periodo') or '').strip().lower()

    if not email:
        raise PaymentGatewayError('Debes enviar el correo del estudiante para completar la matricula masiva.')

    if not cedula:
        raise PaymentGatewayError('Debes registrar el numero de cedula para completar la matricula masiva.')

    if not re.fullmatch(r'\d{6,20}', cedula):
        raise PaymentGatewayError('La cedula debe contener solo numeros (entre 6 y 20 digitos).')

    if not cod_anio_basica:
        raise PaymentGatewayError('Debes seleccionar la carrera (Cod_AnioBasica) para registrar el curso.')

    if not codigo_materia:
        raise PaymentGatewayError('Debes seleccionar el curso antes de continuar.')

    if not codigo_periodo:
        raise PaymentGatewayError('Debes seleccionar el periodo para continuar con la matricula masiva.')

    if estado_periodo and estado_periodo != 'activo':
        raise PaymentGatewayError('El periodo seleccionado esta inactivo. Debes elegir un periodo con estado Activo.')

    if not matricula:
        matricula = generate_unique_numcodigo()

    if _cabecera_has_numcodigo(matricula):
        raise PaymentGatewayError(
            'El numero de matricula generado ya existe en CABECERA_MATRICULA. '
            'Solicita un nuevo numero para continuar.'
        )

    descripcion = _compose_mass_matriculation_description(payload, descripcion)
    try:
        intec_account = build_intec_account_identity(nombre=nombre, cedula=cedula)
    except Microsoft365ValidationError as exc:
        raise PaymentGatewayError(str(exc)) from exc

    official_record = _upsert_official_inscription_records(
        cedula=cedula,
        nombre=nombre,
        email=email,
        telefono=_first_non_empty(
            payload.get('telefono'),
            payload.get('provider_payload', {}).get('telefono') if isinstance(payload.get('provider_payload'), dict) else None,
        ),
        direccion=_first_non_empty(
            payload.get('direccion'),
            payload.get('provider_payload', {}).get('direccion') if isinstance(payload.get('provider_payload'), dict) else None,
        ),
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
        matricula=matricula,
        monto=monto,
        descripcion=descripcion,
        payment_link='',
        create_payment_record=False,
    )

    microsoft365_result: dict[str, Any] = {'ok': False, 'message': 'No ejecutado.'}
    welcome_email_result: dict[str, Any] = {'sent': False, 'message': 'No ejecutado.'}

    try:
        microsoft365_user = create_microsoft365_user(
            {
                'nombre_completo': nombre,
                'cedula': cedula,
            }
        )
        microsoft365_result = {
            'ok': True,
            'message': 'Usuario Microsoft 365 creado y licenciado correctamente.',
            'user': microsoft365_user,
        }
        resolved_intec_email = str(microsoft365_user.get('correo') or intec_account['correo']).strip()
        if resolved_intec_email:
            intec_account['correo'] = resolved_intec_email
            _update_official_intec_credentials(
                codigo_estud=official_record['codigo_estud'],
                correo_intec=resolved_intec_email,
                password_temporal=intec_account['password_temporal'],
            )
    except Microsoft365Error as exc:
        microsoft365_result = {
            'ok': False,
            'message': str(exc),
        }
        raise PaymentGatewayError(f'No fue posible crear el usuario Microsoft 365: {str(exc)}') from exc

    try:
        welcome_email_result = _send_intec_welcome_email(
            recipient_email=email,
            recipient_name=nombre,
            intec_email=intec_account['correo'],
            password=intec_account['password_temporal'],
            course_name=_resolve_welcome_course_name(payload),
        )
        if welcome_email_result.get('sent'):
            _mark_correos_estud_intec_sent(official_record['codigo_estud'])
    except PaymentGatewayError as exc:
        welcome_email_result = {
            'sent': False,
            'message': f'Usuario Microsoft 365 creado, pero no fue posible enviar bienvenida: {str(exc)}',
        }
    microsoft365_result['welcome_email'] = welcome_email_result

    provider_response = {
        'status': 'completado',
        'source': 'matricula-masiva-sin-cargo',
        'official_record': official_record,
        'microsoft365': microsoft365_result,
        'welcome_email_result': welcome_email_result,
    }

    return {
        'matricula': matricula,
        'monto': monto,
        'payment_link': '',
        'receipt_email': '',
        'provider_response': provider_response,
        'email_result': {'sent': False, 'message': 'No aplica para matricula masiva.'},
        'welcome_email_result': welcome_email_result,
        'official_sync': {
            'ok': True,
            'message': 'Matricula oficial registrada.',
            'record': official_record,
        },
        'microsoft365': microsoft365_result,
    }


def generate_unique_numcodigo(length: int = 5, max_attempts: int = 200) -> str:
    for _ in range(max_attempts):
        number = secrets.randbelow(10 ** length)
        candidate = f'{number:0{length}d}'
        if not _cabecera_has_numcodigo(candidate):
            return candidate
    raise PaymentGatewayError(
        'No fue posible generar un numero de matricula unico. Intenta nuevamente.'
    )


def admin_get_payment_info(payload: dict[str, Any]) -> dict[str, Any]:
    provider_payload = payload.get('provider_payload')
    if isinstance(provider_payload, dict):
        return _call_payment_provider(provider_payload)

    transaccion_id = str(payload.get('transaccion_id') or '').strip()
    plataforma_id = str(payload.get('plataforma_id') or '').strip()
    cliente = str(payload.get('cliente') or '').strip()

    if not any([transaccion_id, plataforma_id, cliente]):
        raise PaymentGatewayError(
            'Debes indicar al menos un criterio: transaccion_id, plataforma_id o cliente.'
        )

    query_payload: dict[str, Any] = {
        'accion': 'consultar',
        'transaccion_id': transaccion_id,
        'plataforma_id': plataforma_id,
        'cliente': cliente,
    }
    return _call_payment_provider(query_payload)


def admin_cancel_payment(payload: dict[str, Any]) -> dict[str, Any]:
    provider_payload = payload.get('provider_payload')
    if isinstance(provider_payload, dict):
        return _call_payment_provider(provider_payload)

    transaccion_id = str(payload.get('transaccion_id') or '').strip()
    plataforma_id = str(payload.get('plataforma_id') or '').strip()
    motivo = str(payload.get('motivo') or 'Anulacion solicitada desde dashboard').strip()

    if not transaccion_id and not plataforma_id:
        raise PaymentGatewayError('Debes enviar transaccion_id o plataforma_id para anular.')

    cancel_payload: dict[str, Any] = {
        'accion': 'anular',
        'transaccion_id': transaccion_id,
        'plataforma_id': plataforma_id,
        'motivo': motivo,
    }
    return _call_payment_provider(cancel_payload)


def _call_payment_provider(payload: dict[str, Any]) -> dict[str, Any]:
    api_url = (os.getenv('PAYMENTS_API_URL') or '').strip()
    if not api_url:
        raise PaymentGatewayError('No se encontro PAYMENTS_API_URL en las variables de entorno.')

    base_headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': (os.getenv('PAYMENTS_USER_AGENT') or 'INTEC-Inscripcion-Service/1.0').strip(),
    }
    origin = (os.getenv('PAYMENTS_ORIGIN') or '').strip()
    referer = (os.getenv('PAYMENTS_REFERER') or '').strip()
    if origin:
        base_headers['Origin'] = origin
    if referer:
        base_headers['Referer'] = referer

    token = _get_payments_token()
    prepared_payload = dict(payload)
    token_body_key = (os.getenv('PAYMENTS_TOKEN_IN_BODY_KEY') or '').strip()
    if token and token_body_key and token_body_key not in prepared_payload:
        prepared_payload[token_body_key] = token

    auth_headers_variants = _build_provider_auth_headers(base_headers, token)
    last_error: ProviderHttpError | None = None

    for headers in auth_headers_variants:
        try:
            return _post_json(api_url, prepared_payload, headers)
        except ProviderHttpError as exc:
            last_error = exc
            if exc.status_code not in {401, 403}:
                raise

    if last_error:
        raise last_error

    raise PaymentGatewayError('No fue posible completar la solicitud al proveedor de pagos.')


def _get_payments_token() -> str | None:
    token = (os.getenv('token_pay') or os.getenv('TOKEN_PAY') or '').strip()
    return token or None


def _build_alldigital_payload(
    raw_payload: dict[str, Any],
    email: str,
    nombre: str,
    cedula: str,
    matricula: str,
    monto: Any,
    descripcion: str,
) -> dict[str, Any]:
    provider_payload = raw_payload.get('provider_payload')
    provider_payload = provider_payload if isinstance(provider_payload, dict) else {}

    first_name, last_name = _split_full_name(nombre)
    nombres = _first_non_empty(provider_payload.get('nombres'), raw_payload.get('nombres'), first_name)
    apellidos = _first_non_empty(provider_payload.get('apellidos'), raw_payload.get('apellidos'), last_name)

    telefono_celular = _first_non_empty(
        provider_payload.get('telefono_celular'),
        provider_payload.get('telefono'),
        raw_payload.get('telefono_celular'),
        raw_payload.get('telefono'),
    )
    direccion = _first_non_empty(
        provider_payload.get('direccion'),
        raw_payload.get('direccion'),
    )

    ambiente_raw = _first_non_empty(
        provider_payload.get('ambiente'),
        raw_payload.get('ambiente'),
        os.getenv('PAYMENTS_AMBIENTE'),
        'Produccion',
    )
    ambiente = _normalize_alldigital_ambiente(ambiente_raw)
    tipo_recurrente = _first_non_empty(
        provider_payload.get('tipoRecurrente'),
        raw_payload.get('tipoRecurrente'),
        os.getenv('PAYMENTS_TIPO_RECURRENTE'),
        'No',
    )

    external_name = _first_non_empty(
        provider_payload.get('datosExterno', {}).get('nombre') if isinstance(provider_payload.get('datosExterno'), dict) else None,
        provider_payload.get('external_nombre'),
        raw_payload.get('external_nombre'),
        os.getenv('PAYMENTS_EXTERNAL_NAME'),
        'intec-inscripcion',
    )
    external_id = _first_non_empty(
        provider_payload.get('datosExterno', {}).get('id') if isinstance(provider_payload.get('datosExterno'), dict) else None,
        provider_payload.get('external_id'),
        raw_payload.get('external_id'),
        f'{matricula}-{secrets.token_hex(3)}',
    )
    external_tipo = _first_non_empty(
        provider_payload.get('datosExterno', {}).get('tipo') if isinstance(provider_payload.get('datosExterno'), dict) else None,
        provider_payload.get('external_tipo'),
        raw_payload.get('external_tipo'),
        os.getenv('PAYMENTS_EXTERNAL_TIPO'),
        'Redirecciona',
    )
    external_url = _first_non_empty(
        provider_payload.get('datosExterno', {}).get('url') if isinstance(provider_payload.get('datosExterno'), dict) else None,
        provider_payload.get('external_url'),
        raw_payload.get('external_url'),
        os.getenv('PAYMENTS_EXTERNAL_URL'),
        'https://intec.edu.do',
    )

    missing_fields: list[str] = []
    if not cedula:
        missing_fields.append('datosCliente.identificacion')
    if not nombres:
        missing_fields.append('datosCliente.nombres')
    if not apellidos:
        missing_fields.append('datosCliente.apellidos')
    if not email:
        missing_fields.append('datosCliente.correo')
    if not telefono_celular:
        missing_fields.append('datosCliente.telefono_celular')
    if not direccion:
        missing_fields.append('datosCliente.direccion')
    if not ambiente:
        missing_fields.append('ambiente')
    if not tipo_recurrente:
        missing_fields.append('tipoRecurrente')
    if not external_name:
        missing_fields.append('datosExterno.nombre')
    if not external_id:
        missing_fields.append('datosExterno.id')
    if not external_tipo:
        missing_fields.append('datosExterno.tipo')

    if missing_fields:
        raise PaymentGatewayError(
            'Faltan campos obligatorios para la pasarela de pago: ' + ', '.join(missing_fields)
        )

    normalized_monto = str(monto).strip() if monto is not None else ''

    return {
        'ambiente': ambiente,
        'datosCliente': {
            'identificacion': cedula,
            'nombres': nombres,
            'apellidos': apellidos,
            'correo': email,
            'telefono_celular': telefono_celular,
            'direccion': direccion,
        },
        'descripcion': descripcion,
        'monto': normalized_monto,
        'tipoRecurrente': tipo_recurrente,
        'datosExterno': {
            'nombre': external_name,
            'id': external_id,
            'tipo': external_tipo,
            'url': external_url,
        },
    }


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _compose_course_payment_description(raw_payload: dict[str, Any], fallback: str) -> str:
    provider_payload = raw_payload.get('provider_payload')
    provider_payload = provider_payload if isinstance(provider_payload, dict) else {}

    course_name = _first_non_empty(
        provider_payload.get('nombre_materia'),
        raw_payload.get('nombre_materia'),
        provider_payload.get('curso_nombre'),
        raw_payload.get('curso_nombre'),
    )
    course_name = _remove_numbers_and_trim(course_name)

    if course_name:
        return f'Pago de inscripcion del curso {course_name}'

    clean_fallback = str(fallback or '').strip()
    return clean_fallback or 'Pago de inscripcion'


def _compose_mass_matriculation_description(raw_payload: dict[str, Any], fallback: str) -> str:
    provider_payload = raw_payload.get('provider_payload')
    provider_payload = provider_payload if isinstance(provider_payload, dict) else {}

    course_name = _first_non_empty(
        provider_payload.get('nombre_materia'),
        raw_payload.get('nombre_materia'),
        provider_payload.get('curso_nombre'),
        raw_payload.get('curso_nombre'),
    )
    course_name = _remove_numbers_and_trim(course_name)

    if course_name:
        return f'Matricula masiva del curso {course_name}'

    clean_fallback = str(fallback or '').strip()
    return clean_fallback or 'Matricula masiva'


def _resolve_welcome_course_name(raw_payload: dict[str, Any]) -> str:
    provider_payload = raw_payload.get('provider_payload')
    provider_payload = provider_payload if isinstance(provider_payload, dict) else {}

    course_name = _first_non_empty(
        provider_payload.get('nombre_materia'),
        raw_payload.get('nombre_materia'),
        provider_payload.get('curso_nombre'),
        raw_payload.get('curso_nombre'),
        raw_payload.get('descripcion'),
    )
    course_name = _remove_numbers_and_trim(course_name)
    return course_name or 'el curso seleccionado'


def _remove_numbers_and_trim(value: str) -> str:
    text = str(value or '')
    text = re.sub(r'\d+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' -')
    return text


def _normalize_alldigital_ambiente(value: str) -> str:
    clean = str(value or '').strip()
    if not clean:
        return ''

    aliases = {
        'pruebas': 'Pruebas',
        'prueba': 'Pruebas',
        'test': 'Pruebas',
        'testing': 'Pruebas',
        'sandbox': 'Pruebas',
        'dev': 'Pruebas',
        'produccion': 'Produccion',
        'producción': 'Produccion',
        'prod': 'Produccion',
        'live': 'Produccion',
    }
    return aliases.get(clean.lower(), clean)


def _split_full_name(full_name: str) -> tuple[str, str]:
    clean_name = str(full_name or '').strip()
    if not clean_name:
        return '', ''

    parts = [chunk for chunk in clean_name.split() if chunk]
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], parts[0]

    return ' '.join(parts[:-1]), parts[-1]


def _build_provider_auth_headers(base_headers: dict[str, str], token: str | None) -> list[dict[str, str]]:
    auth_mode = (os.getenv('PAYMENTS_AUTH_MODE') or 'auto').strip().lower()

    if not token or auth_mode == 'none':
        return [dict(base_headers)]

    if auth_mode != 'auto':
        return [_headers_with_auth_mode(base_headers, token, auth_mode)]

    ordered_modes = ['both', 'x-api-key', 'bearer', 'token-header']
    return [_headers_with_auth_mode(base_headers, token, mode) for mode in ordered_modes]


def _headers_with_auth_mode(base_headers: dict[str, str], token: str, mode: str) -> dict[str, str]:
    headers = dict(base_headers)
    if mode == 'bearer':
        headers['Authorization'] = f'Bearer {token}'
        return headers
    if mode == 'x-api-key':
        headers['x-api-key'] = token
        return headers
    if mode == 'token-header':
        headers['token'] = token
        return headers

    headers['Authorization'] = f'Bearer {token}'
    headers['x-api-key'] = token
    return headers


def _cabecera_has_numcodigo(value: str) -> bool:
    clean_value = str(value or '').strip()
    if not clean_value:
        return False

    with connection.cursor() as cursor:
        column_name = _resolve_numcodigo_column(cursor)
        cursor.execute(
            f"""
            SELECT TOP (1) 1
            FROM dbo.CABECERA_MATRICULA
            WHERE LTRIM(RTRIM(CAST([{column_name}] AS varchar(20)))) = %s
            """,
            [clean_value],
        )
        return cursor.fetchone() is not None


def _ensure_inscription_registry_table() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            IF OBJECT_ID('dbo.INSCRIPCION_SOLICITUD_PAGO', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.INSCRIPCION_SOLICITUD_PAGO (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    Cedula NVARCHAR(20) NOT NULL,
                    Email NVARCHAR(255) NOT NULL,
                    NombreEstudiante NVARCHAR(200) NOT NULL,
                    Matricula NVARCHAR(20) NOT NULL,
                    Monto NVARCHAR(30) NULL,
                    Descripcion NVARCHAR(300) NULL,
                    CodAnioBasica NVARCHAR(20) NOT NULL,
                    CodigoMateria NVARCHAR(50) NOT NULL,
                    CodigoPeriodo NVARCHAR(20) NOT NULL,
                    EstadoPeriodo NVARCHAR(30) NULL,
                    PaymentLink NVARCHAR(600) NOT NULL,
                    ProviderResponse NVARCHAR(MAX) NULL,
                    CreadoEn DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
            END
            """
        )
        cursor.execute(
            """
            IF NOT EXISTS (
                SELECT 1
                FROM sys.indexes
                WHERE name = 'UX_INSCRIPCION_SOLICITUD_PAGO_CEDULA_CURSO_PERIODO'
                  AND object_id = OBJECT_ID('dbo.INSCRIPCION_SOLICITUD_PAGO')
            )
            BEGIN
                CREATE UNIQUE INDEX UX_INSCRIPCION_SOLICITUD_PAGO_CEDULA_CURSO_PERIODO
                ON dbo.INSCRIPCION_SOLICITUD_PAGO (Cedula, CodigoMateria, CodigoPeriodo);
            END
            """
        )


def _inscription_request_exists(cedula: str, codigo_materia: str, codigo_periodo: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TOP (1) 1
            FROM dbo.INSCRIPCION_SOLICITUD_PAGO
            WHERE LTRIM(RTRIM(Cedula)) = %s
              AND LTRIM(RTRIM(CodigoMateria)) = %s
              AND LTRIM(RTRIM(CodigoPeriodo)) = %s
            """,
            [cedula, codigo_materia, codigo_periodo],
        )
        return cursor.fetchone() is not None


def _register_inscription_request(
    cedula: str,
    email: str,
    nombre: str,
    matricula: str,
    monto: Any,
    descripcion: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    estado_periodo: str,
    payment_link: str,
    provider_response: Any,
) -> int:
    serialized_provider_response = _serialize_json(provider_response)
    monto_label = str(monto).strip() if monto is not None else None

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dbo.INSCRIPCION_SOLICITUD_PAGO (
                    Cedula,
                    Email,
                    NombreEstudiante,
                    Matricula,
                    Monto,
                    Descripcion,
                    CodAnioBasica,
                    CodigoMateria,
                    CodigoPeriodo,
                    EstadoPeriodo,
                    PaymentLink,
                    ProviderResponse
                )
                OUTPUT INSERTED.Id
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    cedula,
                    email,
                    nombre,
                    matricula,
                    monto_label,
                    descripcion,
                    cod_anio_basica,
                    codigo_materia,
                    codigo_periodo,
                    estado_periodo,
                    payment_link,
                    serialized_provider_response,
                ],
            )
            row = cursor.fetchone()
            if row and row[0]:
                return int(row[0])
            raise PaymentGatewayError('No fue posible confirmar el registro de inscripcion en la base de datos.')
    except IntegrityError:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT TOP (1) Id
                FROM dbo.INSCRIPCION_SOLICITUD_PAGO
                WHERE LTRIM(RTRIM(Cedula)) = %s
                  AND LTRIM(RTRIM(CodigoMateria)) = %s
                  AND LTRIM(RTRIM(CodigoPeriodo)) = %s
                ORDER BY Id DESC
                """,
                [cedula, codigo_materia, codigo_periodo],
            )
            row = cursor.fetchone()
            if row and row[0]:
                return int(row[0])
        raise PaymentGatewayError('No fue posible reutilizar la solicitud de inscripcion existente.')


def _update_inscription_request_result(
    inscription_id: int,
    payment_link: str,
    provider_response: Any,
) -> None:
    serialized_provider_response = _serialize_json(provider_response)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.INSCRIPCION_SOLICITUD_PAGO
            SET PaymentLink = %s,
                ProviderResponse = %s
            WHERE Id = %s
            """,
            [payment_link, serialized_provider_response, inscription_id],
        )


def _serialize_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _upsert_official_inscription_records(
    cedula: str,
    nombre: str,
    email: str,
    telefono: str,
    direccion: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    matricula: str,
    monto: Any,
    descripcion: str,
    payment_link: str,
    create_payment_record: bool = True,
) -> dict[str, str]:
    try:
        intec_account = build_intec_account_identity(nombre=nombre, cedula=cedula)
    except Microsoft365ValidationError as exc:
        raise PaymentGatewayError(str(exc)) from exc

    pensum_template = _get_pensum_subject_template(cod_anio_basica, codigo_materia)

    with transaction.atomic():
        codigo_estud = _resolve_or_create_datos_estud(
            cedula=cedula,
            nombre=nombre,
            email=email,
            telefono=telefono,
            direccion=direccion,
            correo_intec=intec_account['correo'],
        )

        _upsert_correos_estud_intec(
            codigo_estud=codigo_estud,
            nombre=nombre,
            email=email,
            correo_intec=intec_account['correo'],
            password_temporal=intec_account['password_temporal'],
            codigo_periodo=codigo_periodo,
            descripcion=descripcion,
        )

        existing_materia = _get_existing_official_materia(
            codigo_estud=codigo_estud,
            cod_anio_basica=cod_anio_basica,
            codigo_materia=codigo_materia,
            codigo_periodo=codigo_periodo,
        )
        if existing_materia:
            return {
                'codigo_estud': str(codigo_estud),
                'num_matricula': str(_safe_int(existing_materia.get('Num_Matricula'), default=0)),
                'num_reg_pago': str(_safe_int(existing_materia.get('Num'), default=0)),
                'already_enrolled': '1',
                'payment_record_created': '0',
                'codigo_materia': str(codigo_materia),
                'materia': str(pensum_template.get('materia') or ''),
            }

        cabecera_template = _get_cabecera_template(cod_anio_basica, codigo_periodo)
        if not create_payment_record:
            cabecera_template = {
                **cabecera_template,
                'InscripValor': Decimal('0'),
                'MatriValor': Decimal('0'),
            }
        materia_template = _get_carreraxestud_template(
            cod_anio_basica,
            codigo_materia,
            codigo_periodo,
            pensum_template=pensum_template,
        )

        num_matricula = _next_num_matricula(codigo_estud, cod_anio_basica, codigo_periodo)
        num_reg_pago = _next_num_registro_pago(codigo_estud, codigo_periodo) if create_payment_record else 0
        valor_decimal = _to_decimal(monto) if create_payment_record else Decimal('0')

        _insert_cabecera_matricula(
            codigo_estud=codigo_estud,
            cod_anio_basica=cod_anio_basica,
            codigo_periodo=codigo_periodo,
            num_matricula=num_matricula,
            valor=valor_decimal,
            payment_link=payment_link,
            template=cabecera_template,
        )

        _insert_carreraxestud(
            codigo_estud=codigo_estud,
            cod_anio_basica=cod_anio_basica,
            codigo_materia=codigo_materia,
            codigo_periodo=codigo_periodo,
            num_matricula=num_matricula,
            template=materia_template,
        )

        if create_payment_record:
            _insert_registropagos(
                codigo_estud=codigo_estud,
                cod_anio_basica=cod_anio_basica,
                codigo_periodo=codigo_periodo,
                num=num_reg_pago,
                valor=valor_decimal,
                detalle=descripcion,
            )

        return {
            'codigo_estud': str(codigo_estud),
            'num_matricula': str(num_matricula),
            'num_reg_pago': str(num_reg_pago) if create_payment_record else '',
            'payment_record_created': '1' if create_payment_record else '0',
            'codigo_materia': str(codigo_materia),
            'materia': str(pensum_template.get('materia') or ''),
        }


def _update_official_links_after_payment(
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_periodo: str,
    payment_link: str,
) -> None:
    with connection.cursor() as cursor:
        try:
            cursor.execute("EXEC sys.sp_set_session_context @key = N'SYNC_ESTADO_TRIGGER', @value = 1")
            cursor.execute(
                """
                UPDATE dbo.CABECERA_MATRICULA
                SET linkUrl = %s
                WHERE CAST(codigo_estud AS varchar(50)) = %s
                  AND CAST(cod_anio_Basica AS varchar(20)) = %s
                  AND CAST(codigo_periodo AS varchar(20)) = %s
                """,
                [payment_link, str(codigo_estud), str(cod_anio_basica), str(codigo_periodo)],
            )
        finally:
            cursor.execute("EXEC sys.sp_set_session_context @key = N'SYNC_ESTADO_TRIGGER', @value = NULL")
        cursor.execute(
            """
            UPDATE dbo.REGISTROPAGOS
            SET Referencia = %s
            WHERE CAST(Codestu AS varchar(50)) = %s
              AND CAST(cod_anio_Basica AS varchar(20)) = %s
              AND CAST(codperiodo AS varchar(20)) = %s
            """,
            [payment_link, str(codigo_estud), str(cod_anio_basica), str(codigo_periodo)],
        )


def _resolve_or_create_datos_estud(
    cedula: str,
    nombre: str,
    email: str,
    telefono: str,
    direccion: str,
    correo_intec: str,
) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TOP (1) CAST(codigo_estud AS varchar(50))
            FROM dbo.DATOS_ESTUD
            WHERE LTRIM(RTRIM(Cedula_Est)) = %s
            """,
            [cedula],
        )
        row = cursor.fetchone()
        if row and row[0]:
            codigo_estud = str(row[0]).strip()
            cursor.execute(
                """
                UPDATE dbo.DATOS_ESTUD
                SET Apellidos_nombre = %s,
                    correo = %s,
                    correointec = %s,
                    telefono = %s,
                    movil = %s,
                    calle_principal = %s,
                    Estado = 'D'
                WHERE CAST(codigo_estud AS varchar(50)) = %s
                """,
                [nombre, email, correo_intec, telefono or '', telefono or '', direccion or '', codigo_estud],
            )
            return codigo_estud

        cursor.execute("SELECT ISNULL(MAX(CAST(codigo_estud AS decimal(18,0))), 0) + 1 FROM dbo.DATOS_ESTUD")
        codigo_estud = str(_safe_int(cursor.fetchone()[0], default=1))

        cursor.execute(
            """
            INSERT INTO dbo.DATOS_ESTUD (
                codigo_estud,
                Cedula_Est,
                Apellidos_nombre,
                correo,
                correointec,
                telefono,
                movil,
                calle_principal,
                Cedula,
                Fotos,
                Tipodoc,
                NumMigracion,
                Estado
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, 'D')
            """,
            [
                codigo_estud,
                cedula,
                nombre,
                email,
                correo_intec,
                telefono or '',
                telefono or '',
                direccion or '',
                _safe_int(cedula, default=0),
            ],
        )
        return codigo_estud


def _upsert_correos_estud_intec(
    codigo_estud: str,
    nombre: str,
    email: str,
    correo_intec: str,
    password_temporal: str,
    codigo_periodo: str,
    descripcion: str,
) -> None:
    nombre_corto = _trim_to_max(nombre, 100)
    email_corto = _trim_to_max(email, 100)
    correo_intec_corto = _trim_to_max(correo_intec, 100)
    tipo_curso = _trim_to_max('E', 1)
    password = _trim_to_max(password_temporal, 30)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TOP (1) 1
            FROM dbo.CorreosEstudIntec
            WHERE CAST(codestud AS varchar(50)) = %s
            """,
            [codigo_estud],
        )
        exists = cursor.fetchone() is not None

        if exists:
            cursor.execute(
                """
                UPDATE dbo.CorreosEstudIntec
                SET Nombres = %s,
                    CorreoPersonal = %s,
                    CorreoIntec = %s,
                    Password = %s,
                    Periodo = %s,
                    Estado = ISNULL(Estado, 'A'),
                    Descripcion = %s
                WHERE CAST(codestud AS varchar(50)) = %s
                """,
                [
                    nombre_corto,
                    email_corto,
                    correo_intec_corto,
                    password,
                    _safe_int(codigo_periodo, default=0),
                    descripcion,
                    codigo_estud,
                ],
            )
            return

        cursor.execute(
            """
            INSERT INTO dbo.CorreosEstudIntec (
                codestud,
                Nombres,
                CorreoPersonal,
                CorreoIntec,
                Password,
                fecha,
                Periodo,
                CorreoEnviado,
                Estado,
                Descripcion,
                TipoCursoMigra
            )
            VALUES (%s, %s, %s, %s, %s, GETDATE(), %s, 0, %s, %s, %s)
            """,
            [
                codigo_estud,
                nombre_corto,
                email_corto,
                correo_intec_corto,
                password,
                _safe_int(codigo_periodo, default=0),
                'A',
                descripcion,
                tipo_curso,
            ],
        )


def _mark_correos_estud_intec_sent(codigo_estud: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.CorreosEstudIntec
            SET CorreoEnviado = 1
            WHERE CAST(codestud AS varchar(50)) = %s
            """,
            [str(codigo_estud)],
        )


def _update_official_intec_credentials(
    codigo_estud: str,
    correo_intec: str,
    password_temporal: str,
) -> None:
    correo_intec_corto = _trim_to_max(correo_intec, 100)
    password = _trim_to_max(password_temporal, 30)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.DATOS_ESTUD
            SET correointec = %s
            WHERE CAST(codigo_estud AS varchar(50)) = %s
            """,
            [correo_intec_corto, str(codigo_estud)],
        )
        cursor.execute(
            """
            UPDATE dbo.CorreosEstudIntec
            SET CorreoIntec = %s,
                Password = %s
            WHERE CAST(codestud AS varchar(50)) = %s
            """,
            [correo_intec_corto, password, str(codigo_estud)],
        )


def _official_materia_exists(
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TOP (1) 1
            FROM dbo.CARRERAXESTUD
            WHERE CAST(codigo_estud AS varchar(50)) = %s
              AND CAST(cod_anio_Basica AS varchar(20)) = %s
              AND CAST(codigo_materia AS varchar(20)) = %s
              AND CAST(codigo_periodo AS varchar(20)) = %s
            """,
            [codigo_estud, str(cod_anio_basica), str(codigo_materia), str(codigo_periodo)],
        )
        return cursor.fetchone() is not None


def _get_existing_official_materia(
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
) -> dict[str, Any] | None:
    query = """
        SELECT TOP (1)
            cx.Num_Matricula,
            rp.Num
        FROM dbo.CARRERAXESTUD cx
        LEFT JOIN dbo.REGISTROPAGOS rp
          ON CAST(rp.Codestu AS varchar(50)) = CAST(cx.codigo_estud AS varchar(50))
         AND CAST(rp.cod_anio_Basica AS varchar(20)) = CAST(cx.cod_anio_Basica AS varchar(20))
         AND CAST(rp.codperiodo AS varchar(20)) = CAST(cx.codigo_periodo AS varchar(20))
        WHERE CAST(cx.codigo_estud AS varchar(50)) = %s
          AND CAST(cx.cod_anio_Basica AS varchar(20)) = %s
          AND CAST(cx.codigo_materia AS varchar(20)) = %s
          AND CAST(cx.codigo_periodo AS varchar(20)) = %s
        ORDER BY cx.Fecha_Matricula DESC
    """
    return _fetch_one_row(
        query,
        [str(codigo_estud), str(cod_anio_basica), str(codigo_materia), str(codigo_periodo)],
    )


def _get_cabecera_template(cod_anio_basica: str, codigo_periodo: str) -> dict[str, Any]:
    query = """
        SELECT TOP (1)
            ControlMatricula,
            codhorario,
            codmodalidad,
            coddias,
            codjornada,
            ISNULL(InscripValor, 0) AS InscripValor,
            ISNULL(MatriValor, 0) AS MatriValor,
            ISNULL(codestadoMat, 1) AS codestadoMat,
            ISNULL(Jornada, '') AS Jornada
        FROM dbo.CABECERA_MATRICULA
        WHERE CAST(cod_anio_Basica AS varchar(20)) = %s
          AND CAST(codigo_periodo AS varchar(20)) = %s
        ORDER BY fecha_pago DESC
    """
    row = _fetch_one_row(query, [str(cod_anio_basica), str(codigo_periodo)])
    if row:
        return row
    return {
        'ControlMatricula': 1,
        'codhorario': 1,
        'codmodalidad': 1,
        'coddias': 1,
        'codjornada': 1,
        'InscripValor': 0,
        'MatriValor': 0,
        'codestadoMat': 1,
        'Jornada': '',
    }


def _get_pensum_subject_template(cod_anio_basica: str, codigo_materia: str) -> dict[str, Any]:
    status_column = get_pensum_status_column()
    if status_column:
        status_select = f"RTRIM(ISNULL([{status_column}], 'A')) AS estado_materia"
    else:
        status_select = "CAST('A' AS varchar(20)) AS estado_materia"

    query = f"""
        SELECT TOP (1)
            RTRIM(ISNULL(Nomb_Materia, '')) AS materia,
            CAST(ISNULL(Creditos, 0) AS decimal(18, 2)) AS creditos,
            CAST(ISNULL(Orden, 1) AS decimal(18, 0)) AS orden,
            CAST(ISNULL(Semestre, 1) AS decimal(18, 0)) AS semestre,
            CAST(ISNULL(NumMalla, 0) AS decimal(18, 0)) AS num_malla,
            RTRIM(ISNULL(CAST(cod_materia AS varchar(50)), '')) AS cod_materia,
            RTRIM(ISNULL(CAST(tipomateria AS varchar(20)), '')) AS tipo_materia,
            {status_select}
        FROM dbo.PENSUM
        WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
          AND LTRIM(RTRIM(CAST(codigo_materia AS varchar(50)))) = %s
        ORDER BY Orden ASC
    """
    row = _fetch_one_row(query, [str(cod_anio_basica), str(codigo_materia)])
    if not row:
        raise PaymentGatewayError(
            'No se encontro en PENSUM la materia seleccionada para la carrera indicada.'
        )
    if not is_catalog_value_active(row.get('estado_materia'), default=True):
        raise PaymentGatewayError(
            'La materia seleccionada esta inactiva en PENSUM y no puede matricularse.'
        )

    credits = _to_decimal(row.get('creditos'))
    if credits <= 0:
        credits = Decimal('1')

    return {
        'materia': str(row.get('materia') or '').strip(),
        'Num_Creditos': credits,
        'num': _safe_int(row.get('orden'), default=1),
        'Semestre': _safe_int(row.get('semestre'), default=1),
        'NumMalla': _safe_int(row.get('num_malla'), default=0),
        'cod_materia': str(row.get('cod_materia') or '').strip(),
        'tipo_materia': str(row.get('tipo_materia') or '').strip(),
    }


def _get_carreraxestud_template(
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    pensum_template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query = """
        SELECT TOP (1)
            paralelo,
            NumGrupo,
            Num_Creditos,
            num,
            TipoMatricula,
            ControlMatricula,
            NumCertificado,
            gcer,
            NumMatricuMod
        FROM dbo.CARRERAXESTUD
        WHERE CAST(cod_anio_Basica AS varchar(20)) = %s
          AND CAST(codigo_materia AS varchar(20)) = %s
          AND CAST(codigo_periodo AS varchar(20)) = %s
        ORDER BY Fecha_Matricula DESC
    """
    row = _fetch_one_row(query, [str(cod_anio_basica), str(codigo_materia), str(codigo_periodo)])
    if row:
        if pensum_template and _to_decimal(row.get('Num_Creditos')) <= 0:
            row['Num_Creditos'] = pensum_template.get('Num_Creditos')
        return row
    template = {
        'paralelo': 'A',
        'NumGrupo': 1,
        'Num_Creditos': Decimal('1'),
        'num': 1,
        'TipoMatricula': 'N',
        'ControlMatricula': 1,
        'NumCertificado': 0,
        'gcer': 0,
        'NumMatricuMod': 0,
    }
    if pensum_template:
        template.update(
            {
                'Num_Creditos': pensum_template.get('Num_Creditos') or Decimal('1'),
                'num': pensum_template.get('num') or 1,
            }
        )
    return template


def _next_num_matricula(codigo_estud: str, cod_anio_basica: str, codigo_periodo: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ISNULL(MAX(CAST(Num_Matricula AS decimal(18,0))), 0) + 1
            FROM dbo.CABECERA_MATRICULA
            WHERE CAST(codigo_estud AS varchar(50)) = %s
              AND CAST(cod_anio_Basica AS varchar(20)) = %s
              AND CAST(codigo_periodo AS varchar(20)) = %s
            """,
            [codigo_estud, str(cod_anio_basica), str(codigo_periodo)],
        )
        return _safe_int(cursor.fetchone()[0], default=1)


def _next_numcodigo_cabecera() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT ISNULL(MAX(CAST(numcodigo AS decimal(18,0))), 0) + 1 FROM dbo.CABECERA_MATRICULA")
        return _safe_int(cursor.fetchone()[0], default=1)


def _next_num_registro_pago(codigo_estud: str, codigo_periodo: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ISNULL(MAX(CAST(Num AS decimal(18,0))), 0) + 1
            FROM dbo.REGISTROPAGOS
            WHERE CAST(Codestu AS varchar(50)) = %s
              AND CAST(codperiodo AS varchar(20)) = %s
            """,
            [codigo_estud, str(codigo_periodo)],
        )
        return _safe_int(cursor.fetchone()[0], default=1)


def _insert_cabecera_matricula(
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_periodo: str,
    num_matricula: int,
    valor: Decimal,
    payment_link: str,
    template: dict[str, Any],
) -> None:
    with connection.cursor() as cursor:
        try:
            # Bypass broken trigger scope on CABECERA_MATRICULA while keeping insert transactional.
            cursor.execute("EXEC sys.sp_set_session_context @key = N'SYNC_ESTADO_TRIGGER', @value = 1")
            cursor.execute(
                """
                INSERT INTO dbo.CABECERA_MATRICULA (
                    codigo_estud,
                    cod_anio_Basica,
                    codigo_periodo,
                    Num_Matricula,
                    fecha_pago,
                    valor,
                    InscripValor,
                    MatriValor,
                    ControlMatricula,
                    codhorario,
                    codmodalidad,
                    coddias,
                    codjornada,
                    codestadoMat,
                    Jornada,
                    linkUrl
                )
                VALUES (%s, %s, %s, %s, GETDATE(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    codigo_estud,
                    _safe_int(cod_anio_basica, default=0),
                    _safe_int(codigo_periodo, default=0),
                    num_matricula,
                    valor,
                    _to_decimal(template.get('InscripValor')),
                    _to_decimal(template.get('MatriValor')),
                    _safe_int(template.get('ControlMatricula'), default=1),
                    _safe_int(template.get('codhorario'), default=1),
                    _safe_int(template.get('codmodalidad'), default=1),
                    _safe_int(template.get('coddias'), default=1),
                    _safe_int(template.get('codjornada'), default=1),
                    _safe_int(template.get('codestadoMat'), default=1),
                    str(template.get('Jornada') or ''),
                    payment_link,
                ],
            )
        finally:
            cursor.execute("EXEC sys.sp_set_session_context @key = N'SYNC_ESTADO_TRIGGER', @value = NULL")


def _insert_carreraxestud(
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    num_matricula: int,
    template: dict[str, Any],
) -> None:
    num_creditos = _to_decimal(template.get('Num_Creditos'))
    if num_creditos <= 0:
        num_creditos = Decimal('1')

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO dbo.CARRERAXESTUD (
                codigo_estud,
                cod_anio_Basica,
                codigo_materia,
                codigo_periodo,
                Num_Matricula,
                paralelo,
                NumGrupo,
                Num_Creditos,
                TipoMatricula,
                ControlMatricula,
                NumCertificado,
                gcer,
                NumMatricuMod,
                TipoCursoMigra,
                Fecha_Matricula
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'E', GETDATE())
            """,
            [
                codigo_estud,
                _safe_int(cod_anio_basica, default=0),
                _safe_int(codigo_materia, default=0),
                _safe_int(codigo_periodo, default=0),
                num_matricula,
                str(template.get('paralelo') or 'A'),
                _safe_int(template.get('NumGrupo'), default=1),
                num_creditos,
                str(template.get('TipoMatricula') or 'N'),
                _safe_int(template.get('ControlMatricula'), default=1),
                _safe_int(template.get('NumCertificado'), default=0),
                _safe_int(template.get('gcer'), default=0),
                _safe_int(template.get('NumMatricuMod'), default=0),
            ],
        )


def _insert_registropagos(
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_periodo: str,
    num: int,
    valor: Decimal,
    detalle: str,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO dbo.REGISTROPAGOS (
                Codestu,
                Num,
                codperiodo,
                cod_anio_Basica,
                fechapago,
                Detalle,
                Valor,
                usuarioreg,
                FechaRegistro,
                ValorRegistrado,
                correoenviado
            )
            VALUES (%s, %s, %s, %s, GETDATE(), %s, %s, 'SISTEMA', GETDATE(), %s, 0)
            """,
            [
                codigo_estud,
                num,
                _safe_int(codigo_periodo, default=0),
                _safe_int(cod_anio_basica, default=0),
                detalle[:150],
                valor,
                valor,
            ],
        )


def _fetch_one_row(query: str, params: list[Any]) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row is None:
            return None
        columns = [col[0] for col in cursor.description]
        return {columns[idx]: row[idx] for idx in range(len(columns))}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _trim_to_max(value: Any, max_length: int) -> str:
    text = str(value or '').strip()
    if max_length <= 0:
        return ''
    return text[:max_length]


def _resolve_numcodigo_column(cursor: Any) -> str:
    cursor.execute(
        """
        SELECT TOP (1) COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'CABECERA_MATRICULA'
          AND LOWER(COLUMN_NAME) = 'numcodigo'
        """
    )
    row = cursor.fetchone()
    if row and row[0]:
        return str(row[0])
    return 'numcodigo'


def _resolve_monto_from_pensum(cod_anio_basica: str, codigo_materia: str) -> Decimal | None:
    status_column = get_pensum_status_column()
    if status_column:
        status_select = f"RTRIM(ISNULL([{status_column}], 'A')) AS estado_materia"
    else:
        status_select = "CAST('A' AS varchar(20)) AS estado_materia"

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT TOP (1)
                CAST(ISNULL(Horas, 0) AS decimal(18, 0)) AS horas,
                CAST(ISNULL(ValorHoraVirtual, 0) AS decimal(18, 5)) AS valor_hora_virtual,
                {status_select}
            FROM dbo.PENSUM
            WHERE LTRIM(RTRIM(CAST(Cod_AnioBasica AS varchar(20)))) = %s
              AND LTRIM(RTRIM(CAST(codigo_materia AS varchar(50)))) = %s
            ORDER BY Orden ASC
            """,
            [cod_anio_basica, codigo_materia],
        )
        rows = cursor.fetchall()

    if not rows:
        return None

    row = next((item for item in rows if is_catalog_value_active(item[2], default=True)), None)
    if row is None:
        return None

    horas = _to_decimal(row[0])
    valor_hora_virtual = _to_decimal(row[1])
    return calculate_inscription_amount(horas, valor_hora_virtual)


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or '0'))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def _send_payment_link_email(
    recipient_email: str,
    recipient_name: str,
    payment_link: str,
    matricula: str,
    monto: Any,
    receipt_email: str,
) -> dict[str, Any]:
    recipient_label = recipient_name or recipient_email
    safe_recipient_label = escape(recipient_label)
    safe_matricula = escape(matricula or 'No disponible')
    safe_monto = escape(str(monto if monto not in (None, '') else 'No disponible'))
    safe_payment_link = escape(payment_link)
    safe_button_href = escape(payment_link if payment_link else '#', quote=True)
    safe_receipt_email = escape(receipt_email)
    safe_receipt_mailto = escape(f'mailto:{receipt_email}', quote=True)
    logo_attachment = _build_intec_logo_attachment()
    logo_html = ''
    if logo_attachment:
        logo_html = """
            <tr>
              <td align="center" style="padding:24px 28px 8px 28px;background:#ffffff;">
                <img src="cid:intec-logo" width="230" alt="INTEC" style="display:block;width:230px;max-width:78%;height:auto;border:0;" />
              </td>
            </tr>
""".rstrip()
    receipt_message = ''
    if receipt_email:
        receipt_message = f"""
                <p style="margin:0 0 18px 0;font-size:14px;line-height:1.6;color:#374151;">
                  Luego de realizar el pago, envia el comprobante al correo
                  <a href="{safe_receipt_mailto}" style="color:#9B0E0E;text-decoration:underline;">{safe_receipt_email}</a>
                  indicando tu nombre completo y matricula.
                </p>
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
                <h2 style="margin:0;font-size:22px;font-weight:700;">Pago de inscripcion</h2>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 28px;color:#111827;">
                <p style="margin:0 0 12px 0;font-size:16px;">Hola {safe_recipient_label},</p>
                <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#374151;">Hemos generado tu enlace para completar el pago de inscripcion.</p>

                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 18px 0;border:1px solid #e5e7eb;border-radius:10px;">
                  <tr>
                    <td style="padding:14px 16px;font-size:14px;color:#111827;"><strong>Matricula:</strong> {safe_matricula}</td>
                  </tr>
                  <tr>
                    <td style="padding:14px 16px;border-top:1px solid #e5e7eb;font-size:16px;color:#111827;"><strong>Valor a cancelar:</strong> RD$ {safe_monto}</td>
                  </tr>
                </table>

                <p style="margin:0 0 18px 0;">
                  <a href="{safe_button_href}" style="display:inline-block;background:#9B0E0E;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:8px;font-weight:600;">Pagar ahora</a>
                </p>

{receipt_message}

                <p style="margin:0 0 8px 0;font-size:13px;color:#6b7280;">Si el boton no funciona, copia y pega este enlace en tu navegador:</p>
                <p style="margin:0;font-size:13px;word-break:break-all;color:#9B0E0E;">{safe_payment_link}</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()

    mail_payload = {
        'message': {
            'subject': 'Enlace de pago de inscripcion',
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
        },
        'saveToSentItems': True,
    }
    if logo_attachment:
        mail_payload['message']['attachments'] = [logo_attachment]

    _send_graph_mail(mail_payload)

    return {
        'sent': True,
        'message': f'Correo enviado correctamente a {recipient_email}.',
        'receipt_email': receipt_email,
    }


def _send_intec_welcome_email(
    recipient_email: str,
    recipient_name: str,
    intec_email: str,
    password: str,
    course_name: str,
) -> dict[str, Any]:
    recipient_label = recipient_name or recipient_email
    safe_recipient_label = escape(recipient_label)
    safe_intec_email = escape(intec_email)
    safe_password = escape(password)
    safe_course_name = escape(course_name or 'el curso seleccionado')
    logo_attachment = _build_intec_logo_attachment()
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
                <h2 style="margin:0;font-size:22px;font-weight:700;">Credenciales INTEC</h2>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 28px;color:#111827;">
                <p style="margin:0 0 12px 0;font-size:16px;">Hola {safe_recipient_label},</p>
                <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#374151;">Te damos la bienvenida al Instituto Superior Tecnologico de Tecnicas Empresariales y del Conocimiento INTEC. Has sido matriculado en el curso <strong>{safe_course_name}</strong>, en el cual continuaras con tu preparacion profesional para lograr tu exito.</p>
                <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#374151;">Tu cuenta institucional ha sido creada correctamente.</p>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 18px 0;border:1px solid #e5e7eb;border-radius:10px;">
                  <tr>
                    <td style="padding:14px 16px;font-size:14px;color:#111827;"><strong>Usuario:</strong> {safe_intec_email}</td>
                  </tr>
                  <tr>
                    <td style="padding:14px 16px;border-top:1px solid #e5e7eb;font-size:14px;color:#111827;"><strong>Contraseña:</strong> {safe_password}</td>
                  </tr>
                </table>
                <p style="margin:0;font-size:13px;line-height:1.6;color:#6b7280;">Conserva estas credenciales en un lugar seguro y no las compartas con terceros.</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()

    mail_payload = {
        'message': {
            'subject': 'Credenciales de acceso INTEC',
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
        },
        'saveToSentItems': True,
    }
    if logo_attachment:
        mail_payload['message']['attachments'] = [logo_attachment]

    _send_graph_mail(mail_payload)
    return {
        'sent': True,
        'message': f'Credenciales INTEC enviadas correctamente a {recipient_email}.',
    }


def _build_intec_logo_attachment() -> dict[str, Any] | None:
    logo_path = (
        Path(__file__).resolve().parents[3]
        / 'frontend'
        / 'public'
        / 'Intec-Logowithslogangray.svg'
    )
    try:
        content = logo_path.read_bytes()
    except OSError:
        return None

    return {
        '@odata.type': '#microsoft.graph.fileAttachment',
        'name': 'Intec-Logowithslogangray.svg',
        'contentType': 'image/svg+xml',
        'contentBytes': b64encode(content).decode('ascii'),
        'isInline': True,
        'contentId': 'intec-logo',
    }


def _send_graph_mail(mail_payload: dict[str, Any]) -> None:
    tenant_id = (os.getenv('MS_TENANT_ID') or '').strip()
    client_id = (os.getenv('MS_CLIENT_ID') or '').strip()
    client_secret = (os.getenv('MS_CLIENT_SECRET') or '').strip()
    sender_identity = _resolve_graph_sender_identity()

    if not tenant_id or not client_id or not client_secret:
        raise PaymentGatewayError(
            'No se encontraron credenciales MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET para envio de correo.'
        )

    access_token = _get_graph_access_token(tenant_id, client_id, client_secret)
    _validate_graph_mail_send_permission(access_token)

    endpoint = f'https://graph.microsoft.com/v1.0/users/{quote(sender_identity, safe="")}/sendMail'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }

    try:
        _post_json(endpoint, mail_payload, headers, expect_json=False)
    except ProviderHttpError as exc:
        if exc.status_code == 404 and 'ErrorInvalidUser' in exc.detail:
            raise PaymentGatewayError(
                'Microsoft Graph no reconoce el remitente configurado. '
                'Configura un usuario valido en MS_SENDER_USER_ID (Object ID/UPN) '
                'o MS_SENDER_EMAIL y asegure permisos Mail.Send para la aplicacion.'
            ) from exc
        if exc.status_code == 403 and 'ErrorAccessDenied' in exc.detail:
            raise PaymentGatewayError(
                'Microsoft Graph denego el envio de correo. La aplicacion debe tener '
                'el permiso Application Mail.Send con Grant admin consent, y el remitente '
                f'{sender_identity} debe ser un buzon valido con permiso para enviar.'
            ) from exc
        raise


def _resolve_payment_receipt_email() -> str:
    return DEFAULT_PAYMENT_RECEIPT_EMAIL


def _resolve_graph_sender_identity() -> str:
    sender_user_id = (os.getenv('MS_SENDER_USER_ID') or '').strip()
    if sender_user_id:
        return sender_user_id

    sender_email = (os.getenv('MS_SENDER_EMAIL') or '').strip()
    if sender_email:
        return sender_email

    raise PaymentGatewayError(
        'No hay remitente configurado para Microsoft Graph. Define MS_SENDER_USER_ID '
        '(Object ID/UPN) o MS_SENDER_EMAIL en el archivo .env.'
    )


def _get_graph_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    token_url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'
    form_body = (
        f'client_id={quote(client_id, safe="")}&'
        f'client_secret={quote(client_secret, safe="")}&'
        'scope=https%3A%2F%2Fgraph.microsoft.com%2F.default&'
        'grant_type=client_credentials'
    ).encode('utf-8')

    request = Request(
        token_url,
        data=form_body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )

    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode('utf-8')
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='ignore')
        raise PaymentGatewayError(
            f'No fue posible autenticar con Microsoft Graph ({exc.code}): {detail or exc.reason}'
        ) from exc
    except URLError as exc:
        raise PaymentGatewayError(f'No fue posible conectar con Microsoft Graph: {exc.reason}') from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PaymentGatewayError('Graph devolvio una respuesta invalida al solicitar token.') from exc

    token = str(payload.get('access_token') or '').strip()
    if not token:
        raise PaymentGatewayError('Graph no devolvio access_token para envio de correo.')
    return token


def _validate_graph_mail_send_permission(access_token: str) -> None:
    roles = _graph_token_roles(access_token)
    if GRAPH_MAIL_SEND_ROLE not in roles:
        raise PaymentGatewayError(
            'Microsoft Graph no puede enviar el correo porque el token no contiene '
            'Mail.Send como permiso de aplicacion. En Azure Portal agrega Microsoft Graph '
            '> Application permissions > Mail.Send y ejecuta Grant admin consent.'
        )


def _graph_token_roles(access_token: str) -> set[str]:
    parts = str(access_token or '').split('.')
    if len(parts) < 2:
        return set()

    payload_part = parts[1]
    padded_payload = payload_part + '=' * (-len(payload_part) % 4)
    try:
        decoded = urlsafe_b64decode(padded_payload.encode('utf-8')).decode('utf-8')
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return set()

    roles = payload.get('roles')
    if isinstance(roles, list):
        return {str(role) for role in roles if str(role).strip()}

    scope = str(payload.get('scp') or '')
    return {item for item in scope.split() if item}


def _extract_payment_link(provider_response: Any) -> str | None:
    if isinstance(provider_response, dict):
        direct_keys = [
            'payment_link',
            'link',
            'url',
            'url_pago',
            'urlPago',
            'checkout_url',
            'urlCheckout',
            'url_redireccion',
            'linkPago',
        ]
        for key in direct_keys:
            value = provider_response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        nested_keys = ['data', 'result', 'payload', 'transaccion']
        for nested in nested_keys:
            nested_value = provider_response.get(nested)
            found = _extract_payment_link(nested_value)
            if found:
                return found

    if isinstance(provider_response, list):
        for item in provider_response:
            found = _extract_payment_link(item)
            if found:
                return found

    return None


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], expect_json: bool = True) -> dict[str, Any]:
    body = json.dumps(payload).encode('utf-8')
    request = Request(url, data=body, headers=headers, method='POST')

    try:
        with urlopen(request, timeout=35) as response:
            raw = response.read().decode('utf-8')
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='ignore')
        raise ProviderHttpError(exc.code, detail or str(exc.reason)) from exc
    except URLError as exc:
        raise PaymentGatewayError(f'No fue posible conectar con el proveedor: {exc.reason}') from exc

    if not raw:
        return {}

    if not expect_json:
        return {'ok': True}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PaymentGatewayError('El proveedor devolvio una respuesta no JSON.') from exc

    if isinstance(parsed, dict):
        return parsed
    return {'data': parsed}
