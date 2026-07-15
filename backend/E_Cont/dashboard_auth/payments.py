from __future__ import annotations

import json
import logging
import os
import re
import secrets
from base64 import b64decode, b64encode, urlsafe_b64decode
from decimal import Decimal, InvalidOperation
from datetime import datetime
from html import escape
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.core.cache import cache
from django.db import IntegrityError, connection, transaction

from .inscription_catalogs import (
    calculate_inscription_amount,
    get_pensum_status_column,
    is_catalog_value_active,
)
from .course_cuts import CourseCutError, assign_matricula_to_open_cut, ensure_open_cut_for_enrollment
from .continuing_education import (
    connection_for_query,
    complement_database_name,
    configure_cut_in_complement,
    ensure_student_course_charge,
    is_complement_available,
    sync_student_enrollment_to_complement,
)
from .microsoft365 import (
    Microsoft365Error,
    Microsoft365ValidationError,
    build_intec_account_identity,
    create_microsoft365_user,
    upload_continuing_education_voucher,
)
from .payment_receipt import build_all_digital_payment_receipt
from .notifications import create_notification_safely


logger = logging.getLogger(__name__)


class PaymentGatewayError(Exception):
    pass


class ProviderHttpError(PaymentGatewayError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f'El proveedor de pagos rechazó la solicitud ({status_code}).')


DEFAULT_PAYMENT_RECEIPT_EMAIL = 'DeptCobranzas@intec.edu.ec'
GRAPH_MAIL_CC_ENV_KEYS = (
    'MS_MAIL_CC_RECIPIENTS',
    'MICROSOFT_MAIL_CC_RECIPIENTS',
    'GRAPH_MAIL_CC_RECIPIENTS',
)
GRAPH_MAIL_SEND_ROLE = 'Mail.Send'
EXCEL_ENROLLMENT_NET_AMOUNT = Decimal('400.00')
PAID_PROVIDER_STATUSES = {'PAGADA', 'PAGADO', 'APROBADA', 'APROBADO', 'COMPLETADA', 'COMPLETADO'}
TERMINAL_PROVIDER_STATUSES = PAID_PROVIDER_STATUSES | {
    'ANULADA', 'ANULADO', 'ELIMINADA', 'ELIMINADO', 'ERROR', 'RECHAZADA', 'RECHAZADO',
}


def create_payment_link_and_notify(payload: dict[str, Any]) -> dict[str, Any]:
    email = str(payload.get('email') or '').strip()
    nombre = str(payload.get('nombre') or '').strip()
    cedula = str(payload.get('cedula') or '').strip()
    matricula = str(payload.get('matricula') or '').strip()
    monto = payload.get('monto')
    descripcion = str(payload.get('descripcion') or 'Pago de inscripción').strip()
    data_treatment_accepted = bool(payload.get('data_treatment_accepted'))
    cod_anio_basica = str(payload.get('cod_anio_basica') or '').strip()
    codigo_materia = str(payload.get('codigo_materia') or '').strip()
    codigo_periodo = str(payload.get('codigo_periodo') or '').strip()
    estado_periodo = str(payload.get('estado_periodo') or '').strip().lower()

    if not data_treatment_accepted:
        raise PaymentGatewayError(
            'No es posible completar la inscripción sin aceptar el tratamiento de datos personales.'
        )

    if not email:
        raise PaymentGatewayError('Debes enviar el correo del estudiante para generar y enviar el pago.')

    if not cedula:
        raise PaymentGatewayError('Debes registrar el número de cédula para completar la inscripción.')

    if not re.fullmatch(r'\d{6,20}', cedula):
        raise PaymentGatewayError('La cédula debe contener solo números (entre 6 y 20 dígitos).')

    if not cod_anio_basica:
        raise PaymentGatewayError('Debes seleccionar la carrera (Cod_AnioBasica) para registrar el curso.')

    if not codigo_materia:
        raise PaymentGatewayError('Debes seleccionar el curso a seguir antes de continuar.')

    if not codigo_periodo:
        raise PaymentGatewayError('Debes seleccionar el período para continuar con la inscripción.')

    if estado_periodo and estado_periodo != 'activo':
        raise PaymentGatewayError(
            'El período seleccionado está inactivo. Debes elegir un período con estado Activo.'
        )

    _ensure_open_cut_for_request(
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
    )

    if not matricula:
        matricula = generate_unique_numcodigo()

    monto_calculado = _resolve_monto_from_pensum(cod_anio_basica, codigo_materia)
    if monto_calculado is not None:
        monto = f'{monto_calculado:.2f}'
    elif monto in (None, '', 0, '0'):
        raise PaymentGatewayError(
            'No fue posible validar el curso activo en PENSUM para asignar el monto fijo.'
        )

    if _cabecera_has_numcodigo(matricula):
        raise PaymentGatewayError(
            'El número de matrícula generado ya existe en CABECERA_MATRICULA. '
            'Solicita un nuevo número para continuar.'
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
        localidad=_first_non_empty(payload.get('localidad'), payload.get('provider_payload', {}).get('localidad') if isinstance(payload.get('provider_payload'), dict) else None),
        tipo_postulante=_first_non_empty(payload.get('tipo_postulante'), payload.get('provider_payload', {}).get('tipo_postulante') if isinstance(payload.get('provider_payload'), dict) else None),
        carrera_ocupacion=_first_non_empty(payload.get('carrera_ocupacion'), payload.get('provider_payload', {}).get('carrera_ocupacion') if isinstance(payload.get('provider_payload'), dict) else None, payload.get('ocupacion')),
        actividad_profesional=_first_non_empty(payload.get('actividad_profesional'), payload.get('provider_payload', {}).get('actividad_profesional') if isinstance(payload.get('provider_payload'), dict) else None, payload.get('empresa')),
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
        matricula=matricula,
        monto=monto,
        descripcion=descripcion,
        payment_link='PENDIENTE',
        continuing_education_charge=Decimal('500.00'),
        enrollment_origin='LINK_PAGO',
    )
    matricula = _payment_email_matricula_label(official_record, matricula)
    _update_inscription_request_matricula(inscription_id, matricula)
    official_sync_result: dict[str, Any] = {
        'ok': True,
        'message': 'Sincronización oficial completada.',
        'record': official_record,
    }
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
            'La pasarela no devolvió una dirección de pago utilizable. '
            'Revisa la respuesta del proveedor.'
        )

    receipt_email = _resolve_payment_receipt_email()

    try:
        email_result = _send_payment_link_email(
            recipient_email=email,
            recipient_name=nombre,
            payment_link=payment_link,
            matricula=_payment_email_matricula_label(official_record, matricula),
            monto=monto,
            receipt_email=receipt_email,
        )
    except PaymentGatewayError as exc:
        email_result = {
            'sent': False,
            'message': (
                'Se generó el enlace de pago, pero no fue posible enviar el correo: '
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
                'message': f'Enlace generado, pero no se actualizó referencia oficial: {str(exc)}',
                'record': official_record,
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

    course_name = _clean_text(official_record.get('materia_corte') if official_record else '') or descripcion
    notification_data = {
        'inscription_id': inscription_id,
        'codigo_estud': _clean_text(official_record.get('codigo_estud') if official_record else ''),
        'matricula': matricula,
        'course_name': course_name,
        'payment_link': payment_link,
    }
    create_notification_safely(
        event_key=f'payment-link-created:{inscription_id}:financial',
        notification_type='PAYMENT_LINK_CREATED',
        title='Nuevo enlace de pago generado',
        message=f'{nombre} generó un enlace para {course_name} por USD {monto}.',
        recipient_category='staff',
        recipient_role='FINANCIERO',
        route='#payments',
        data=notification_data,
    )
    create_notification_safely(
        event_key=f'payment-link-created:{inscription_id}:student',
        notification_type='PAYMENT_LINK_CREATED',
        title='Tu enlace de pago está listo',
        message=f'Se generó el enlace de pago para {course_name}.',
        recipient_category='student',
        recipient_login=_clean_text(intec_account.get('correo')) or email,
        route='#dashboard',
        data=notification_data,
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
    descripcion = str(payload.get('descripcion') or 'Matrícula masiva').strip()
    cod_anio_basica = str(payload.get('cod_anio_basica') or '').strip()
    codigo_materia = str(payload.get('codigo_materia') or '').strip()
    codigo_periodo = str(payload.get('codigo_periodo') or '').strip()
    estado_periodo = str(payload.get('estado_periodo') or '').strip().lower()
    provider_payload = payload.get('provider_payload') if isinstance(payload.get('provider_payload'), dict) else {}
    enrollment_source = _clean_text(provider_payload.get('tipo')).lower()
    is_excel_enrollment = enrollment_source == 'matricula_masiva_sin_cargo'

    if not email:
        raise PaymentGatewayError('Debes enviar el correo del estudiante para completar la matrícula masiva.')

    if not cedula:
        raise PaymentGatewayError('Debes registrar el número de cédula para completar la matrícula masiva.')

    if not re.fullmatch(r'\d{6,20}', cedula):
        raise PaymentGatewayError('La cédula debe contener solo números (entre 6 y 20 dígitos).')

    if not cod_anio_basica:
        raise PaymentGatewayError('Debes seleccionar la carrera (Cod_AnioBasica) para registrar el curso.')

    if not codigo_materia:
        raise PaymentGatewayError('Debes seleccionar el curso antes de continuar.')

    if not codigo_periodo:
        raise PaymentGatewayError('Debes seleccionar el período para continuar con la matrícula masiva.')

    if estado_periodo and estado_periodo != 'activo':
        raise PaymentGatewayError('El período seleccionado está inactivo. Debes elegir un período con estado Activo.')

    _ensure_open_cut_for_request(
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
    )

    if not matricula:
        matricula = generate_unique_numcodigo()

    if _cabecera_has_numcodigo(matricula):
        raise PaymentGatewayError(
            'El número de matrícula generado ya existe en CABECERA_MATRICULA. '
            'Solicita un nuevo número para continuar.'
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
        localidad=_first_non_empty(
            payload.get('localidad'),
            payload.get('provider_payload', {}).get('localidad') if isinstance(payload.get('provider_payload'), dict) else None,
        ),
        tipo_postulante=_first_non_empty(
            payload.get('tipo_postulante'),
            payload.get('provider_payload', {}).get('tipo_postulante') if isinstance(payload.get('provider_payload'), dict) else None,
        ),
        carrera_ocupacion=_first_non_empty(
            payload.get('carrera_ocupacion'),
            payload.get('provider_payload', {}).get('carrera_ocupacion') if isinstance(payload.get('provider_payload'), dict) else None,
            payload.get('ocupacion'),
        ),
        actividad_profesional=_first_non_empty(
            payload.get('actividad_profesional'),
            payload.get('provider_payload', {}).get('actividad_profesional') if isinstance(payload.get('provider_payload'), dict) else None,
            payload.get('empresa'),
        ),
        cod_anio_basica=cod_anio_basica,
        codigo_materia=codigo_materia,
        codigo_periodo=codigo_periodo,
        matricula=matricula,
        monto=monto,
        descripcion=descripcion,
        payment_link='',
        create_payment_record=False,
        continuing_education_charge=EXCEL_ENROLLMENT_NET_AMOUNT if is_excel_enrollment else Decimal('500.00'),
        enrollment_origin='EXCEL' if is_excel_enrollment else 'BOTON_PAGOS',
    )
    matricula = _payment_email_matricula_label(official_record, matricula)

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
        'email_result': {'sent': False, 'message': 'No aplica para matrícula masiva.'},
        'welcome_email_result': welcome_email_result,
        'official_sync': {
            'ok': True,
            'message': 'Matrícula oficial registrada.',
            'record': official_record,
        },
        'microsoft365': microsoft365_result,
    }


def generate_unique_numcodigo(length: int = 5, max_attempts: int = 200) -> str:
    min_value = 10 ** (length - 1) if length > 1 else 0
    value_range = (10 ** length) - min_value
    for _ in range(max_attempts):
        number = min_value + secrets.randbelow(value_range)
        candidate = str(number)
        if not _cabecera_has_numcodigo(candidate):
            return candidate
    raise PaymentGatewayError(
        'No fue posible generar un número de matrícula único. Intenta nuevamente.'
    )


def reconcile_pending_all_digital_payments(
    *,
    limit: Any = 25,
    force: bool = False,
    user_login: str = 'CONCILIACION_AUTOMATICA',
) -> dict[str, Any]:
    """Consulta AllDigital y aplica una sola vez los pagos confirmados."""
    safe_limit = max(1, min(_safe_int(limit, default=25), 100))
    interval = max(15, _safe_int(os.getenv('PAYMENTS_AUTO_RECONCILE_SECONDS'), default=60))
    last_result_key = 'payments:auto-reconcile:last-result'
    lock_key = 'payments:auto-reconcile:lock'
    if not force:
        cached = cache.get(last_result_key)
        if isinstance(cached, dict):
            return {**cached, 'cached': True}
    if not cache.add(lock_key, True, timeout=max(interval, 60)):
        return {'ok': True, 'running': True, 'processed': 0, 'paid': 0, 'errors': 0}

    result: dict[str, Any] = {
        'ok': True,
        'running': False,
        'processed': 0,
        'paid': 0,
        'updated': 0,
        'errors': 0,
        'checked_at': datetime.now().isoformat(timespec='seconds'),
    }
    try:
        candidates = _pending_all_digital_candidates(safe_limit)
        for candidate in candidates:
            transaction_id = _clean_text(candidate.get('transaction_id'))
            if not transaction_id:
                continue
            result['processed'] += 1
            try:
                provider_response = _get_payment_provider_transaction(transaction_id)
                _validate_reconciled_provider_payment(candidate, provider_response)
                provider_status = _extract_provider_status(provider_response)
                if candidate.get('origin') == 'FINANCIERO':
                    _sync_financial_card_payment_status(
                        transaction_id,
                        provider_response,
                        user_login=user_login,
                    )
                else:
                    _update_inscription_provider_status(
                        candidate.get('request_id'),
                        provider_response,
                    )
                result['updated'] += 1
                if provider_status in PAID_PROVIDER_STATUSES:
                    result['paid'] += 1
            except Exception:
                result['errors'] += 1
                logger.exception(
                    'Automatic AllDigital reconciliation failed for %s transaction %s.',
                    candidate.get('origin'),
                    transaction_id,
                )
        if result['paid']:
            try:
                confirmed_links = _list_generated_payment_links('', 'with_payments')
                result['confirmed_links_processed'] = len(confirmed_links)
            except Exception:
                result['errors'] += 1
                logger.exception('Confirmed AllDigital payments could not be synchronized completely.')
        result['ok'] = result['errors'] == 0
        cache.set(last_result_key, result, timeout=interval)
        return result
    finally:
        cache.delete(lock_key)


def _pending_all_digital_candidates(limit: int) -> list[dict[str, str]]:
    _ensure_financial_card_payment_table()
    inscription_rows = _fetch_payment_rows(
        f"""
        SELECT TOP ({limit})
            CONVERT(varchar(50), I.Id) AS request_id,
            JSON_VALUE(I.ProviderResponse, '$.provider.data.id') AS transaction_id,
            I.Cedula AS cedula,
            TRY_CONVERT(decimal(18,2), I.Monto) AS expected_amount,
            UPPER(LTRIM(RTRIM(ISNULL(JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre'), 'GENERADA')))) AS provider_status,
            'INSCRIPCION' AS origin
        FROM dbo.INSCRIPCION_SOLICITUD_PAGO AS I
        WHERE NULLIF(JSON_VALUE(I.ProviderResponse, '$.provider.data.id'), '') IS NOT NULL
          AND UPPER(LTRIM(RTRIM(ISNULL(JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre'), 'GENERADA'))))
              NOT IN ('PAGADA','PAGADO','APROBADA','APROBADO','COMPLETADA','COMPLETADO','ANULADA','ANULADO','ELIMINADA','ELIMINADO','ERROR','RECHAZADA','RECHAZADO')
        ORDER BY I.CreadoEn ASC, I.Id ASC
        """,
        [],
    )
    remaining = max(0, limit - len(inscription_rows))
    financial_rows: list[dict[str, Any]] = []
    if remaining:
        financial_rows = _fetch_payment_rows(
            f"""
            SELECT TOP ({remaining})
                CONVERT(varchar(50), Id) AS request_id,
                CONVERT(varchar(50), TransaccionId) AS transaction_id,
                Cedula AS cedula,
                Monto AS expected_amount,
                UPPER(LTRIM(RTRIM(Estado))) AS provider_status,
                'FINANCIERO' AS origin
            FROM dbo.FIN_SOLICITUD_PAGO_TARJETA
            WHERE TransaccionId IS NOT NULL
              AND AplicadoEn IS NULL
              AND UPPER(LTRIM(RTRIM(Estado)))
                  NOT IN ('ANULADA','ANULADO','ELIMINADA','ELIMINADO','ERROR','RECHAZADA','RECHAZADO')
            ORDER BY CreadoEn ASC, Id ASC
            """,
            [],
        )
    return [
        {key: _clean_text(value) for key, value in row.items()}
        for row in [*inscription_rows, *financial_rows]
    ]


def _validate_reconciled_provider_payment(
    candidate: dict[str, Any],
    provider_response: dict[str, Any],
) -> None:
    data = provider_response.get('data') if isinstance(provider_response.get('data'), dict) else {}
    provider_id = _clean_text(data.get('id')) or _extract_provider_transaction_id(provider_response)
    expected_id = _clean_text(candidate.get('transaction_id'))
    if not provider_id or provider_id != expected_id:
        raise PaymentGatewayError('AllDigital devolvió una transacción distinta a la solicitada.')

    expected_amount = _to_decimal(candidate.get('expected_amount'))
    provider_amount = _to_decimal(data.get('monto'))
    if expected_amount <= 0 or provider_amount != expected_amount:
        raise PaymentGatewayError('El monto informado por AllDigital no coincide con la solicitud registrada.')

    client = data.get('cliente') if isinstance(data.get('cliente'), dict) else {}
    expected_identity = re.sub(r'\D+', '', _clean_text(candidate.get('cedula')))
    provider_identity = re.sub(r'\D+', '', _clean_text(client.get('identificacion')))
    if expected_identity and provider_identity != expected_identity:
        raise PaymentGatewayError('La identificación informada por AllDigital no corresponde al estudiante.')

    currency = data.get('moneda') if isinstance(data.get('moneda'), dict) else {}
    currency_code = _clean_text(currency.get('codigo')).upper()
    if currency_code and currency_code not in {'USD', '840'}:
        raise PaymentGatewayError('La moneda informada por AllDigital no es USD.')


def _update_inscription_provider_status(request_id: Any, provider_response: dict[str, Any]) -> None:
    clean_request_id = _clean_text(request_id)
    if not clean_request_id.isdigit():
        return
    serialized = json.dumps(provider_response, ensure_ascii=False, default=str)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.INSCRIPCION_SOLICITUD_PAGO
            SET ProviderResponse = JSON_MODIFY(
                CASE WHEN ISJSON(ProviderResponse) = 1 THEN ProviderResponse ELSE '{}' END,
                '$.provider',
                JSON_QUERY(%s)
            )
            WHERE Id = %s
            """,
            [serialized, clean_request_id],
        )


def admin_get_payment_info(payload: dict[str, Any], *, user_login: str = 'SISTEMA') -> dict[str, Any]:
    provider_payload = payload.get('provider_payload')
    if isinstance(provider_payload, dict):
        transaction_id = _first_non_empty(provider_payload.get('transacción_id'), provider_payload.get('transaccion_id'))
        result = (
            _get_payment_provider_transaction(transaction_id)
            if transaction_id
            else _call_payment_provider(provider_payload)
        )
        _sync_financial_card_payment_status(transaction_id, result, user_login=user_login)
        return result

    transacción_id = str(payload.get('transacción_id') or payload.get('transaccion_id') or '').strip()
    plataforma_id = str(payload.get('plataforma_id') or '').strip()
    cliente = str(payload.get('cliente') or '').strip()

    if not any([transacción_id, plataforma_id, cliente]):
        raise PaymentGatewayError(
            'Debes indicar al menos un criterio: transacción_id, plataforma_id o cliente.'
        )

    if not transacción_id:
        raise PaymentGatewayError('La consulta automática requiere el ID de transacción de AllDigital.')
    result = _get_payment_provider_transaction(transacción_id)
    _sync_financial_card_payment_status(transacción_id, result, user_login=user_login)
    return result


def list_financial_payment_operations(cedula: Any) -> dict[str, Any]:
    clean_identity = _clean_text(cedula)
    if not re.fullmatch(r'\d{6,20}', clean_identity):
        raise PaymentGatewayError('Ingresa una cédula válida de 6 a 20 dígitos.')

    payment_data = list_registered_user_payments(
        search=clean_identity,
        payment_status='all',
        page=1,
        page_size=100,
    )
    enrollments = [
        row for row in payment_data.get('users', [])
        if _clean_text(row.get('cedula')) == clean_identity
    ]
    if not enrollments:
        raise PaymentGatewayError('No se encontraron matrículas activas para esta cédula.')

    profile = get_payment_student_profile(cedula=clean_identity)
    original_links = [
        {**row, 'origin': 'INSCRIPCION'}
        for row in payment_data.get('payment_links', [])
        if _clean_text(row.get('cedula')) == clean_identity
    ]
    _ensure_financial_card_payment_table()
    financial_links = _fetch_payment_rows(
        """
        SELECT
            CONVERT(varchar(50), Id) AS request_id,
            LTRIM(RTRIM(Cedula)) AS cedula,
            LTRIM(RTRIM(NombreEstudiante)) AS nombre,
            LTRIM(RTRIM(Email)) AS email,
            CONVERT(varchar(50), CodigoEstud) AS codigo_estud,
            CONVERT(varchar(50), CorteEstudianteId) AS estudiante_corte_id,
            CONVERT(varchar(50), CorteId) AS corte_id,
            LTRIM(RTRIM(Curso)) AS course_name,
            LTRIM(RTRIM(Corte)) AS cut_name,
            CONVERT(varchar(50), TransaccionId) AS provider_transaction_id,
            LTRIM(RTRIM(PaymentLink)) AS payment_link,
            LTRIM(RTRIM(Estado)) AS provider_status,
            LTRIM(RTRIM(TipoPago)) AS payment_type,
            Monto AS amount,
            CONVERT(varchar(19), CreadoEn, 120) AS created_at,
            CONVERT(varchar(19), ActualizadoEn, 120) AS updated_at,
            LTRIM(RTRIM(CreadoPor)) AS created_by,
            'FINANCIERO' AS origin
        FROM dbo.FIN_SOLICITUD_PAGO_TARJETA
        WHERE LTRIM(RTRIM(Cedula)) = %s
          AND UPPER(LTRIM(RTRIM(Estado))) NOT IN ('ANULADA', 'ANULADO', 'ELIMINADA', 'ELIMINADO', 'ERROR')
        ORDER BY CreadoEn DESC, Id DESC
        """,
        [clean_identity],
    )
    for row in financial_links:
        row['amount'] = str(row.get('amount') or '0.00')
        row['is_paid'] = _clean_text(row.get('provider_status')).lower() in {
            'pagada', 'pagado', 'aprobada', 'aprobado', 'completada', 'completado'
        }

    transactions = [*financial_links, *original_links]
    for enrollment in enrollments:
        outstanding = sum(
            (
                _to_decimal(item.get('amount'))
                for item in transactions
                if not item.get('is_paid')
                and _clean_text(item.get('corte_id')) == _clean_text(enrollment.get('corte_id'))
            ),
            Decimal('0.00'),
        )
        pending = _to_decimal(enrollment.get('pending_balance'))
        enrollment['active_link_value'] = str(outstanding)
        enrollment['available_to_generate'] = str(max(Decimal('0.00'), pending - outstanding))

    return {
        'student': profile.get('student', {}),
        'enrollments': enrollments,
        'transactions': transactions,
        'source_database': payment_data.get('source_database'),
    }


def search_payment_links_for_operations(search: Any = '') -> dict[str, Any]:
    clean_search = _clean_text(search)[:120]
    payment_data = list_registered_user_payments(
        search=clean_search,
        payment_status='all',
        page=1,
        page_size=100,
    )
    original_links = [
        {**row, 'origin': 'INSCRIPCION', 'payment_type': 'INSCRIPCIÓN'}
        for row in payment_data.get('payment_links', [])
    ]
    _ensure_financial_card_payment_table()
    where_sql = "UPPER(LTRIM(RTRIM(Estado))) NOT IN ('ANULADA', 'ANULADO', 'ELIMINADA', 'ELIMINADO', 'ERROR')"
    params: list[Any] = []
    if clean_search:
        where_sql += """
          AND (
              Cedula LIKE %s OR NombreEstudiante LIKE %s OR Email LIKE %s
              OR Curso LIKE %s OR CONVERT(varchar(50), TransaccionId) LIKE %s
          )
        """
        params.extend([f'%{clean_search}%'] * 5)
    financial_links = _fetch_payment_rows(
        f"""
        SELECT TOP (500)
            CONVERT(varchar(50), Id) AS request_id,
            LTRIM(RTRIM(Cedula)) AS cedula,
            LTRIM(RTRIM(NombreEstudiante)) AS nombre,
            LTRIM(RTRIM(Email)) AS email,
            CONVERT(varchar(50), CodigoEstud) AS codigo_estud,
            CONVERT(varchar(50), CorteEstudianteId) AS estudiante_corte_id,
            CONVERT(varchar(50), CorteId) AS corte_id,
            LTRIM(RTRIM(Curso)) AS course_name,
            LTRIM(RTRIM(Corte)) AS cut_name,
            CONVERT(varchar(50), TransaccionId) AS provider_transaction_id,
            LTRIM(RTRIM(PaymentLink)) AS payment_link,
            LTRIM(RTRIM(Estado)) AS provider_status,
            LTRIM(RTRIM(TipoPago)) AS payment_type,
            Monto AS amount,
            CONVERT(varchar(19), CreadoEn, 120) AS created_at,
            CONVERT(varchar(19), ActualizadoEn, 120) AS updated_at,
            'FINANCIERO' AS origin
        FROM dbo.FIN_SOLICITUD_PAGO_TARJETA
        WHERE {where_sql}
        ORDER BY CreadoEn DESC, Id DESC
        """,
        params,
    )
    for row in financial_links:
        status = _clean_text(row.get('provider_status'))
        row['amount'] = str(row.get('amount') or '0.00')
        row['is_paid'] = status.lower() in {
            'pagada', 'pagado', 'aprobada', 'aprobado', 'completada', 'completado'
        }
        row['display_status'] = 'PAGO CONFIRMADO' if row['is_paid'] else (status or 'GENERADA')

    transactions = sorted(
        [*financial_links, *original_links],
        key=lambda item: _clean_text(item.get('created_at')),
        reverse=True,
    )
    paid = [item for item in transactions if item.get('is_paid')]
    pending = [item for item in transactions if not item.get('is_paid')]
    return {
        'transactions': transactions,
        'summary': {
            'total': len(transactions),
            'paid': len(paid),
            'pending': len(pending),
            'generated_value': str(sum((_to_decimal(item.get('amount')) for item in transactions), Decimal('0.00'))),
            'paid_value': str(sum((_to_decimal(item.get('amount')) for item in paid), Decimal('0.00'))),
        },
        'filters': {'search': clean_search},
    }


def create_financial_card_payment(payload: dict[str, Any], *, user_login: str) -> dict[str, Any]:
    clean_identity = _clean_text(payload.get('cedula'))
    enrollment_id = _clean_text(payload.get('estudiante_corte_id'))
    amount = _to_decimal(payload.get('monto'))
    if not re.fullmatch(r'\d{6,20}', clean_identity):
        raise PaymentGatewayError('La cédula del estudiante no es válida.')
    if not enrollment_id.isdigit():
        raise PaymentGatewayError('Selecciona una matrícula válida.')
    if amount <= 0:
        raise PaymentGatewayError('El abono debe ser mayor a cero.')

    operations = list_financial_payment_operations(clean_identity)
    enrollment = next(
        (row for row in operations['enrollments'] if _clean_text(row.get('estudiante_corte_id')) == enrollment_id),
        None,
    )
    if not enrollment:
        raise PaymentGatewayError('La matrícula no corresponde al estudiante indicado.')
    pending_balance = _to_decimal(enrollment.get('pending_balance'))
    available_balance = _to_decimal(enrollment.get('available_to_generate'))
    if pending_balance <= 0:
        raise PaymentGatewayError('La matrícula ya no tiene saldo pendiente.')
    if available_balance <= 0:
        raise PaymentGatewayError(
            'El saldo ya está cubierto por uno o más enlaces activos. Anula el enlace anterior antes de reemplazarlo.'
        )
    if amount > available_balance:
        raise PaymentGatewayError(f'El abono no puede superar el valor disponible de ${available_balance:.2f}.')

    student = operations.get('student', {})
    email = _first_non_empty(student.get('correo_personal'), student.get('correo_intec'), enrollment.get('email'))
    phone = _first_non_empty(student.get('movil'), student.get('telefono'))
    address = _clean_text(student.get('direccion'))
    if not email or not phone or not address:
        raise PaymentGatewayError(
            'Antes de generar el enlace, completa correo, teléfono y dirección en DATOS_ESTUD.'
        )

    _ensure_financial_card_payment_table()
    payment_type = 'TOTAL' if amount == pending_balance else 'PARCIAL'
    description = _trim_to_max(
        f'{"Pago total" if payment_type == "TOTAL" else "Abono parcial"} - {enrollment.get("course_name")}',
        250,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO dbo.FIN_SOLICITUD_PAGO_TARJETA (
                Cedula, NombreEstudiante, Email, CodigoEstud, CorteEstudianteId, CorteId,
                Curso, Corte, Monto, TipoPago, Estado, CreadoPor
            )
            OUTPUT INSERTED.Id
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PROCESANDO', %s)
            """,
            [
                clean_identity, enrollment.get('nombre'), email, enrollment.get('codigo_estud'),
                enrollment_id, enrollment.get('corte_id'), enrollment.get('course_name'),
                enrollment.get('cut_name'), amount, payment_type, _trim_to_max(user_login or 'SISTEMA', 100),
            ],
        )
        request_id = str(cursor.fetchone()[0])

    provider_payload = _build_alldigital_payload(
        raw_payload={
            'telefono': phone,
            'direccion': address,
            'external_id': f'FIN-{request_id}',
        },
        email=email,
        nombre=_clean_text(enrollment.get('nombre')),
        cedula=clean_identity,
        matricula=f"EC-{enrollment.get('codigo_estud')}-{enrollment.get('corte_id')}",
        monto=f'{amount:.2f}',
        descripcion=description,
    )
    try:
        provider_response = _call_payment_provider(provider_payload)
        payment_link = _extract_payment_link(provider_response)
        if not payment_link:
            raise PaymentGatewayError('AllDigital no devolvió un enlace de pago utilizable.')
        transaction_id = _extract_provider_transaction_id(provider_response)
        provider_status = _extract_provider_status(provider_response) or 'GENERADA'
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE dbo.FIN_SOLICITUD_PAGO_TARJETA
                SET PaymentLink = %s, TransaccionId = %s, Estado = %s,
                    ProviderResponse = %s, ActualizadoEn = SYSDATETIME()
                WHERE Id = %s
                """,
                [payment_link, transaction_id or None, provider_status, json.dumps(provider_response, ensure_ascii=False), request_id],
            )
    except Exception as exc:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE dbo.FIN_SOLICITUD_PAGO_TARJETA
                SET Estado = 'ERROR', ProviderResponse = %s, ActualizadoEn = SYSDATETIME()
                WHERE Id = %s
                """,
                [json.dumps({'error': str(exc)}, ensure_ascii=False), request_id],
            )
        raise

    email_result: dict[str, Any]
    try:
        email_result = _send_payment_link_email(
            recipient_email=email,
            recipient_name=_clean_text(enrollment.get('nombre')),
            payment_link=payment_link,
            matricula=f"EC-{enrollment.get('codigo_estud')}-{enrollment.get('corte_id')}",
            monto=f'{amount:.2f}',
            receipt_email=_resolve_payment_receipt_email(),
        )
    except PaymentGatewayError as exc:
        email_result = {'sent': False, 'message': str(exc)}

    notification_data = {
        'request_id': request_id,
        'cedula': clean_identity,
        'codigo_estud': enrollment.get('codigo_estud'),
        'corte_id': enrollment.get('corte_id'),
        'payment_type': payment_type,
        'amount': str(amount),
        'payment_link': payment_link,
    }
    create_notification_safely(
        event_key=f'financial-card-link:{request_id}:student',
        notification_type='PAYMENT_LINK_CREATED',
        title='Nuevo enlace de pago con tarjeta',
        message=f'Se generó un {"pago total" if payment_type == "TOTAL" else "abono parcial"} por USD {amount:.2f}.',
        recipient_category='student',
        recipient_login=_first_non_empty(student.get('correo_intec'), student.get('usuario_login'), email),
        route='#dashboard',
        data=notification_data,
    )
    create_notification_safely(
        event_key=f'financial-card-link:{request_id}:financial',
        notification_type='PAYMENT_LINK_CREATED',
        title='Enlace de tarjeta generado por financiero',
        message=f'{enrollment.get("nombre")}: {payment_type.lower()} de USD {amount:.2f}.',
        recipient_category='staff',
        recipient_role='FINANCIERO',
        route='#payment-operations',
        data=notification_data,
    )
    return {
        **notification_data,
        'transaction_id': transaction_id,
        'provider_status': provider_status,
        'email_result': email_result,
    }


def admin_cancel_payment(payload: dict[str, Any], *, user_login: str = 'SISTEMA') -> dict[str, Any]:
    provider_payload = payload.get('provider_payload')
    if isinstance(provider_payload, dict):
        provider_transaction_id = _first_non_empty(
            provider_payload.get('transacción_id'), provider_payload.get('transaccion_id')
        )
        result = _delete_payment_provider_transaction(
            provider_transaction_id,
            _clean_text(provider_payload.get('motivo')),
        )
        _mark_payment_request_cancelled(provider_transaction_id)
        return result

    transacción_id = str(payload.get('transacción_id') or payload.get('transaccion_id') or '').strip()
    plataforma_id = str(payload.get('plataforma_id') or '').strip()
    motivo = str(payload.get('motivo') or 'Anulacion solicitada desde dashboard').strip()

    if not transacción_id and not plataforma_id:
        raise PaymentGatewayError('Debes enviar transacción_id o plataforma_id para anular.')

    if not transacción_id:
        raise PaymentGatewayError('La anulación requiere el ID de transacción de AllDigital.')
    result = _delete_payment_provider_transaction(transacción_id, motivo)
    _mark_payment_request_cancelled(transacción_id)
    create_notification_safely(
        event_key=f'payment-cancelled:{transacción_id or plataforma_id}',
        notification_type='PAYMENT_CANCELLED',
        title='Transacción anulada',
        message=f'La transacción {transacción_id or plataforma_id} fue anulada por {user_login}.',
        recipient_category='staff',
        recipient_role='FINANCIERO',
        route='#payment-operations',
        data={'transaction_id': transacción_id, 'platform_id': plataforma_id, 'reason': motivo},
    )
    return result


def _mark_payment_request_cancelled(transaction_id: Any) -> None:
    clean_transaction_id = _clean_text(transaction_id)
    if not clean_transaction_id:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.INSCRIPCION_SOLICITUD_PAGO
            SET ProviderResponse = CASE
                WHEN ISJSON(ProviderResponse) = 1
                THEN JSON_MODIFY(
                    JSON_MODIFY(ProviderResponse, '$.provider.data.estado.nombre', 'Anulada'),
                    '$.status',
                    'anulada'
                )
                ELSE ProviderResponse
            END
            WHERE JSON_VALUE(ProviderResponse, '$.provider.data.id') = %s
               OR PaymentLink LIKE %s
            """,
            [clean_transaction_id, f'%/{clean_transaction_id}'],
        )
    _ensure_financial_card_payment_table()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.FIN_SOLICITUD_PAGO_TARJETA
            SET Estado = 'ANULADA', ActualizadoEn = SYSDATETIME()
            WHERE CONVERT(varchar(50), TransaccionId) = %s
            """,
            [clean_transaction_id],
        )


def _ensure_financial_card_payment_table() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            IF OBJECT_ID('dbo.FIN_SOLICITUD_PAGO_TARJETA', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.FIN_SOLICITUD_PAGO_TARJETA (
                    Id bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
                    Cedula varchar(20) NOT NULL,
                    NombreEstudiante nvarchar(200) NOT NULL,
                    Email nvarchar(254) NOT NULL,
                    CodigoEstud int NOT NULL,
                    CorteEstudianteId bigint NOT NULL,
                    CorteId int NOT NULL,
                    Curso nvarchar(250) NULL,
                    Corte nvarchar(150) NULL,
                    Monto decimal(18,2) NOT NULL,
                    TipoPago varchar(20) NOT NULL,
                    PaymentLink nvarchar(1000) NULL,
                    TransaccionId varchar(100) NULL,
                    Estado varchar(40) NOT NULL,
                    ProviderResponse nvarchar(max) NULL,
                    AplicadoEn datetime2 NULL,
                    AplicacionResultado nvarchar(max) NULL,
                    CreadoPor nvarchar(100) NOT NULL,
                    CreadoEn datetime2 NOT NULL CONSTRAINT DF_FIN_SOL_PAGO_CREADO DEFAULT SYSDATETIME(),
                    ActualizadoEn datetime2 NOT NULL CONSTRAINT DF_FIN_SOL_PAGO_ACT DEFAULT SYSDATETIME()
                );
                CREATE INDEX IX_FIN_SOL_PAGO_CEDULA ON dbo.FIN_SOLICITUD_PAGO_TARJETA (Cedula, CreadoEn DESC);
                CREATE INDEX IX_FIN_SOL_PAGO_TRANSACCION ON dbo.FIN_SOLICITUD_PAGO_TARJETA (TransaccionId);
            END
            IF COL_LENGTH('dbo.FIN_SOLICITUD_PAGO_TARJETA', 'AplicadoEn') IS NULL
                ALTER TABLE dbo.FIN_SOLICITUD_PAGO_TARJETA ADD AplicadoEn datetime2 NULL;
            IF COL_LENGTH('dbo.FIN_SOLICITUD_PAGO_TARJETA', 'AplicacionResultado') IS NULL
                ALTER TABLE dbo.FIN_SOLICITUD_PAGO_TARJETA ADD AplicacionResultado nvarchar(max) NULL;
            """
        )


def _sync_financial_card_payment_status(transaction_id: Any, provider_response: dict[str, Any], *, user_login: str) -> None:
    clean_transaction_id = _clean_text(transaction_id) or _extract_provider_transaction_id(provider_response)
    if not clean_transaction_id:
        return
    status = _extract_provider_status(provider_response)
    if not status:
        return
    _ensure_financial_card_payment_table()
    paid_statuses = {'PAGADA', 'PAGADO', 'APROBADA', 'APROBADO', 'COMPLETADA', 'COMPLETADO'}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.FIN_SOLICITUD_PAGO_TARJETA
            SET Estado = CASE WHEN Estado = 'APLICANDO' AND AplicadoEn IS NULL THEN Estado ELSE %s END,
                ProviderResponse = %s, ActualizadoEn = SYSDATETIME()
            WHERE CONVERT(varchar(50), TransaccionId) = %s
            """,
            [status, json.dumps(provider_response, ensure_ascii=False), clean_transaction_id],
        )
    if status not in paid_statuses:
        return

    rows = _fetch_payment_rows(
        """
        SELECT TOP (1)
            CONVERT(varchar(50), Id) AS request_id,
            CONVERT(varchar(50), CodigoEstud) AS codigo_estud,
            CONVERT(varchar(50), CorteEstudianteId) AS estudiante_corte_id,
            CONVERT(varchar(50), CorteId) AS corte_id,
            Monto AS amount,
            LTRIM(RTRIM(NombreEstudiante)) AS student_name,
            LTRIM(RTRIM(Email)) AS email,
            AplicadoEn AS applied_at
        FROM dbo.FIN_SOLICITUD_PAGO_TARJETA
        WHERE CONVERT(varchar(50), TransaccionId) = %s
        """,
        [clean_transaction_id],
    )
    if not rows or rows[0].get('applied_at'):
        return
    payment = rows[0]
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE dbo.FIN_SOLICITUD_PAGO_TARJETA
                    SET Estado = 'APLICANDO', ActualizadoEn = SYSDATETIME()
                    OUTPUT INSERTED.Id
                    WHERE Id = %s AND AplicadoEn IS NULL AND Estado <> 'APLICANDO'
                    """,
                    [payment['request_id']],
                )
                if not cursor.fetchone():
                    return
            application = register_continuing_education_payment(
                {
                    'codigo_estud': payment['codigo_estud'],
                    'corte_id': payment['corte_id'],
                    'estudiante_corte_id': payment['estudiante_corte_id'],
                    'valor': payment['amount'],
                    'forma_pago': 'TARJETA',
                    'banco': 'ALLDIGITAL',
                    'numero_deposito': clean_transaction_id,
                    'numero_comprobante': clean_transaction_id,
                    'fecha_deposito': datetime.now().date().isoformat(),
                    'observacion': 'Pago con tarjeta confirmado por AllDigital.',
                },
                user_login=user_login,
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE dbo.FIN_SOLICITUD_PAGO_TARJETA
                    SET Estado = %s, AplicadoEn = SYSDATETIME(), AplicacionResultado = %s,
                        ActualizadoEn = SYSDATETIME()
                    WHERE Id = %s
                    """,
                    [status, json.dumps(application, ensure_ascii=False, default=str), payment['request_id']],
                )
    except Exception as exc:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE dbo.FIN_SOLICITUD_PAGO_TARJETA
                SET Estado = %s, AplicacionResultado = %s, ActualizadoEn = SYSDATETIME()
                WHERE Id = %s AND AplicadoEn IS NULL
                """,
                [status, json.dumps({'error': str(exc)}, ensure_ascii=False), payment['request_id']],
            )
        raise PaymentGatewayError(
            f'AllDigital confirmó el pago, pero no fue posible aplicarlo en Educación Continua: {exc}'
        ) from exc

    notification_data = {
        'request_id': payment['request_id'],
        'transaction_id': clean_transaction_id,
        'codigo_estud': payment['codigo_estud'],
        'corte_id': payment['corte_id'],
        'amount': str(payment['amount']),
    }
    create_notification_safely(
        event_key=f'financial-card-paid:{payment["request_id"]}:student',
        notification_type='PAYMENT_CONFIRMED',
        title='Pago con tarjeta confirmado',
        message=f'AllDigital confirmó tu pago de USD {_to_decimal(payment["amount"]):.2f}.',
        recipient_category='student',
        recipient_login=_clean_text(payment.get('email')),
        route='#dashboard',
        data=notification_data,
    )
    create_notification_safely(
        event_key=f'financial-card-paid:{payment["request_id"]}:financial',
        notification_type='PAYMENT_CONFIRMED',
        title='Pago con tarjeta aplicado',
        message=f'{payment["student_name"]}: USD {_to_decimal(payment["amount"]):.2f} aplicado en Educación Continua.',
        recipient_category='staff',
        recipient_role='FINANCIERO',
        route='#payments',
        data=notification_data,
    )


def list_registered_user_payments(
    *,
    search: Any = '',
    payment_status: Any = 'all',
    page: Any = 1,
    page_size: Any = 25,
) -> dict[str, Any]:
    _ensure_continuing_education_payments_available()
    payment_validation = reconcile_pending_all_digital_payments()
    account_sync = _sync_missing_continuing_education_payment_accounts()
    account_sync['excel_charge_adjustments'] = _sync_excel_course_charge_adjustments()
    complement_enrollments = _continuing_education_object('edu', 'VW_MatriculaEstudianteCompleta')
    complement_cuts = _continuing_education_object('edu', 'VW_CorteCursoDetalle')
    accounts_table = _continuing_education_object('fin', 'CuentaEstudiante')
    movements_table = _continuing_education_object('fin', 'MovimientoCuenta')
    invoices_table = _continuing_education_object('fin', 'FacturaMovimiento')
    clean_search = _clean_text(search)[:120]
    clean_status = _clean_text(payment_status).lower() or 'all'
    if clean_status not in {'all', 'with_payments', 'without_payments'}:
        raise PaymentGatewayError('El filtro de pagos no es válido.')

    current_page = max(1, _safe_int(page, default=1))
    per_page = min(100, max(10, _safe_int(page_size, default=25)))
    offset = (current_page - 1) * per_page

    where_parts: list[str] = []
    params: list[Any] = []
    if clean_search:
        pattern = f'%{clean_search}%'
        where_parts.append(
            """
            (
                CONVERT(varchar(50), E.CodigoEstud) LIKE %s
                OR LTRIM(RTRIM(E.CedulaEst)) LIKE %s
                OR E.ApellidosNombre LIKE %s
                OR ISNULL(E.CorreoPersonal, '') LIKE %s
                OR ISNULL(E.CorreoIntec, '') LIKE %s
                OR ISNULL(CC.NombreCursoMateria, '') LIKE %s
                OR ISNULL(CC.NombreCorte, '') LIKE %s
            )
            """
        )
        params.extend([pattern] * 7)
    if clean_status == 'with_payments':
        where_parts.append('ISNULL(P.payment_count, 0) > 0')
    elif clean_status == 'without_payments':
        where_parts.append('ISNULL(P.payment_count, 0) = 0')

    where_sql = 'WHERE ' + ' AND '.join(where_parts) if where_parts else ''
    query = f"""
        ;WITH AccountSummary AS (
            SELECT
                CE.CorteEstudianteIdPrincipal,
                C.CuentaId,
                COUNT(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) <> 'DESCUENTO' THEN 1 END) AS payment_count,
                COUNT(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) <> 'DESCUENTO' AND F.FacturaMovimientoId IS NOT NULL AND F.EstadoFactura = 'SUBIDA' THEN 1 END) AS invoice_count,
                SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'DEBE' THEN M.Valor ELSE 0 END) AS total_value,
                SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) <> 'DESCUENTO' THEN M.Valor ELSE 0 END) AS registered_value,
                SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) = 'DESCUENTO' THEN M.Valor ELSE 0 END) AS discount_value,
                SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) = 'DESCUENTO' AND UPPER(LTRIM(RTRIM(ISNULL(M.Concepto, '')))) LIKE 'AJUSTE VALOR NETO CURSO - EXCEL%%' THEN M.Valor ELSE 0 END) AS excel_net_adjustment,
                MAX(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' THEN M.FechaMovimiento END) AS last_payment_date
            FROM {complement_enrollments} AS CE
            LEFT JOIN {accounts_table} AS C ON C.EstudianteCorteId = CE.EstudianteCorteId
            LEFT JOIN {movements_table} AS M ON M.CuentaId = C.CuentaId
            LEFT JOIN {invoices_table} AS F ON F.MovimientoId = M.MovimientoId
            GROUP BY CE.CorteEstudianteIdPrincipal, C.CuentaId
        )
        SELECT
            CONVERT(varchar(50), E.CodigoEstud) AS codigo_estud,
            CONVERT(varchar(50), E.CorteEstudianteIdPrincipal) AS estudiante_corte_id,
            CONVERT(varchar(50), E.CorteId) AS corte_id,
            CONVERT(varchar(50), P.CuentaId) AS cuenta_id,
            LTRIM(RTRIM(E.ApellidosNombre)) AS nombre,
            LTRIM(RTRIM(E.CedulaEst)) AS cedula,
            COALESCE(NULLIF(LTRIM(RTRIM(E.CorreoIntec)), ''), NULLIF(LTRIM(RTRIM(E.CorreoPersonal)), '')) AS email,
            LTRIM(RTRIM(E.UsuarioLogin)) AS usuario_login,
            LTRIM(RTRIM(CC.NombreCursoMateria)) AS course_name,
            LTRIM(RTRIM(CC.NombreCorte)) AS cut_name,
            LTRIM(RTRIM(E.EstadoMatricula)) AS enrollment_status,
            CASE WHEN LOWER(LTRIM(RTRIM(ISNULL(E.Observacion, '')))) LIKE N'matrícula masiva%%' THEN 1 ELSE 0 END AS is_excel_enrollment,
            ISNULL(P.payment_count, 0) AS payment_count,
            ISNULL(P.invoice_count, 0) AS invoice_count,
            ISNULL(P.total_value, 0) AS total_value,
            ISNULL(P.registered_value, 0) AS registered_value,
            ISNULL(P.discount_value, 0) AS discount_value,
            ISNULL(P.excel_net_adjustment, 0) AS excel_net_adjustment,
            ISNULL(P.total_value, 0) - ISNULL(P.registered_value, 0) - ISNULL(P.discount_value, 0) AS pending_balance,
            CONVERT(varchar(10), P.last_payment_date, 23) AS last_payment_date,
            LTRIM(RTRIM(LP.Concepto)) AS last_payment_detail,
            COALESCE(NULLIF(LTRIM(RTRIM(LP.NumeroComprobante)), ''), NULLIF(LTRIM(RTRIM(LP.NumeroDeposito)), '')) AS last_payment_reference,
            COUNT(*) OVER() AS filtered_total
        FROM {complement_enrollments} AS E
        INNER JOIN {complement_cuts} AS CC ON CC.CorteId = E.CorteId
        LEFT JOIN AccountSummary AS P ON P.CorteEstudianteIdPrincipal = E.CorteEstudianteIdPrincipal
        OUTER APPLY (
            SELECT TOP (1) M.Concepto, M.NumeroComprobante, M.NumeroDeposito
            FROM {movements_table} AS M
            WHERE M.CuentaId = P.CuentaId
              AND M.EstadoMovimiento = 'ACTIVO'
              AND M.TipoMovimiento = 'HABER'
            ORDER BY M.FechaMovimiento DESC, M.MovimientoId DESC
        ) AS LP
        {where_sql}
        ORDER BY
            CASE WHEN ISNULL(P.payment_count, 0) > 0 THEN 0 ELSE 1 END,
            P.last_payment_date DESC,
            E.ApellidosNombre,
            E.CorteId DESC
        OFFSET %s ROWS FETCH NEXT %s ROWS ONLY
    """

    rows = _fetch_payment_rows(query, [*params, offset, per_page])
    filtered_total = int(rows[0].get('filtered_total') or 0) if rows else 0
    users = [_serialize_registered_user_payment(row) for row in rows]
    metrics = _registered_payment_metrics(complement_enrollments, accounts_table, movements_table, invoices_table)
    payment_links = _list_generated_payment_links(clean_search, clean_status)
    payment_link_metrics = _generated_payment_link_metrics()
    total_pages = max(1, (filtered_total + per_page - 1) // per_page)
    return {
        'users': users,
        'metrics': metrics,
        'payment_links': payment_links,
        'payment_link_metrics': payment_link_metrics,
        'pagination': {
            'page': current_page,
            'page_size': per_page,
            'total': filtered_total,
            'total_pages': total_pages,
        },
        'filters': {'search': clean_search, 'payment_status': clean_status},
        'source_database': complement_database_name(),
        'account_sync': account_sync,
        'payment_validation': payment_validation,
    }


def _sync_missing_continuing_education_payment_accounts() -> dict[str, Any]:
    complement_enrollments = _continuing_education_object('edu', 'VW_MatriculaEstudianteCompleta')
    primary_rows = _fetch_payment_rows(
        """
        SELECT TOP (300)
            CONVERT(varchar(50), E.CorteId) AS corte_id,
            CONVERT(varchar(50), E.CodigoEstud) AS codigo_estud,
            CONVERT(varchar(50), E.CorteEstudianteId) AS estudiante_corte_id
        FROM dbo.CORTE_CURSO_ESTUDIANTE AS E
        WHERE E.EstadoRegistro = 'A'
          AND UPPER(ISNULL(E.EstadoParticipacion, 'INSCRITO')) NOT IN ('ANULADO', 'RETIRADO')
        ORDER BY E.CorteId, E.CorteEstudianteId
        """,
        [],
    )
    if not primary_rows:
        return {'processed': 0, 'synced': 0, 'errors': 0}
    complement_rows = _fetch_payment_rows(
        f"""
        SELECT CONVERT(varchar(50), CorteEstudianteIdPrincipal) AS estudiante_corte_id
        FROM {complement_enrollments}
        WHERE CorteEstudianteIdPrincipal IS NOT NULL
        """,
        [],
    )
    existing_ids = {_clean_text(row.get('estudiante_corte_id')) for row in complement_rows}
    missing_rows = [
        row for row in primary_rows
        if _clean_text(row.get('estudiante_corte_id')) not in existing_ids
    ]
    if not missing_rows:
        return {'processed': 0, 'synced': 0, 'errors': 0}

    configured_cuts: set[str] = set()
    synced = 0
    errors = 0
    for row in missing_rows:
        corte_id = _clean_text(row.get('corte_id'))
        codigo_estud = _clean_text(row.get('codigo_estud'))
        try:
            if corte_id not in configured_cuts:
                configuration = configure_cut_in_complement(
                    corte_id,
                    usuario_registro='SISTEMA_PAGOS',
                )
                if not configuration.get('synced'):
                    raise PaymentGatewayError(
                        _clean_text(configuration.get('message')) or 'No se pudo configurar la corte.'
                    )
                configured_cuts.add(corte_id)
            result = sync_student_enrollment_to_complement(
                corte_id=corte_id,
                codigo_estud=codigo_estud,
                usuario_registro='SISTEMA_PAGOS',
                registrar_cargo_inicial=True,
            )
            if result.get('synced'):
                synced += 1
            else:
                errors += 1
        except Exception:
            errors += 1
    if synced:
        cache.delete(f'dashboard:continuing-education-payment-metrics:v5-invoices:{complement_database_name()}')
    return {'processed': len(missing_rows), 'synced': synced, 'errors': errors}


def _sync_excel_course_charge_adjustments() -> dict[str, Any]:
    rows = _fetch_payment_rows(
        """
        SELECT TOP (500)
            CONVERT(varchar(50), E.CorteId) AS corte_id,
            CONVERT(varchar(50), E.CodigoEstud) AS codigo_estud
        FROM dbo.CORTE_CURSO_ESTUDIANTE AS E
        WHERE E.EstadoRegistro = 'A'
          AND UPPER(ISNULL(E.EstadoParticipacion, 'INSCRITO')) NOT IN ('ANULADO', 'RETIRADO')
          AND LOWER(LTRIM(RTRIM(ISNULL(E.Observacion, '')))) LIKE N'matrícula masiva%'
        ORDER BY E.CorteEstudianteId
        """,
        [],
    )
    adjusted = 0
    unchanged = 0
    errors = 0
    for row in rows:
        try:
            result = ensure_student_course_charge(
                corte_id=row.get('corte_id'),
                codigo_estud=row.get('codigo_estud'),
                target_value=EXCEL_ENROLLMENT_NET_AMOUNT,
                origin='EXCEL',
                usuario_registro='SISTEMA_AJUSTE_EXCEL',
            )
            if result.get('adjusted'):
                adjusted += 1
            else:
                unchanged += 1
        except Exception:
            errors += 1
    if adjusted:
        cache.delete(f'dashboard:continuing-education-payment-metrics:v5-invoices:{complement_database_name()}')
    return {'processed': len(rows), 'adjusted': adjusted, 'unchanged': unchanged, 'errors': errors}


def get_registered_user_payment_detail(codigo_estud: Any, *, cuenta_id: Any = '') -> dict[str, Any]:
    _ensure_continuing_education_payments_available()
    complement_enrollments = _continuing_education_object('edu', 'VW_MatriculaEstudianteCompleta')
    complement_cuts = _continuing_education_object('edu', 'VW_CorteCursoDetalle')
    accounts_table = _continuing_education_object('fin', 'CuentaEstudiante')
    movements_table = _continuing_education_object('fin', 'MovimientoCuenta')
    invoices_table = _continuing_education_object('fin', 'FacturaMovimiento')
    clean_code = _clean_text(codigo_estud)
    if not clean_code or not clean_code.isdigit():
        raise PaymentGatewayError('Debes indicar un código de estudiante válido.')
    clean_account_id = _clean_text(cuenta_id)
    if clean_account_id and not clean_account_id.isdigit():
        raise PaymentGatewayError('La cuenta de educación continua no es válida.')

    account_filter = 'AND C.CuentaId = %s' if clean_account_id else ''
    detail_params: list[Any] = [clean_code]
    if clean_account_id:
        detail_params.append(clean_account_id)

    student_rows = _fetch_payment_rows(
        f"""
        SELECT TOP (1)
            CONVERT(varchar(50), E.CodigoEstud) AS codigo_estud,
            CONVERT(varchar(50), E.CorteEstudianteIdPrincipal) AS estudiante_corte_id,
            CONVERT(varchar(50), E.CorteId) AS corte_id,
            CONVERT(varchar(50), C.CuentaId) AS cuenta_id,
            LTRIM(RTRIM(E.ApellidosNombre)) AS nombre,
            LTRIM(RTRIM(E.CedulaEst)) AS cedula,
            COALESCE(NULLIF(LTRIM(RTRIM(E.CorreoIntec)), ''), NULLIF(LTRIM(RTRIM(E.CorreoPersonal)), '')) AS email,
            LTRIM(RTRIM(CC.NombreCursoMateria)) AS course_name,
            LTRIM(RTRIM(CC.NombreCorte)) AS cut_name,
            CASE WHEN LOWER(LTRIM(RTRIM(ISNULL(E.Observacion, '')))) LIKE N'matrícula masiva%%' THEN 1 ELSE 0 END AS is_excel_enrollment
        FROM {complement_enrollments} AS E
        INNER JOIN {complement_cuts} AS CC ON CC.CorteId = E.CorteId
        LEFT JOIN {accounts_table} AS C ON C.EstudianteCorteId = E.EstudianteCorteId
        WHERE E.CodigoEstud = %s
        {account_filter}
        ORDER BY E.CorteId DESC
        """,
        detail_params,
    )
    if not student_rows:
        raise PaymentGatewayError('El usuario no está matriculado en Educación Continua.')

    selected_account_id = clean_account_id or _clean_text(student_rows[0].get('cuenta_id'))
    if not selected_account_id:
        payment_rows = []
    else:
        payment_rows = _fetch_payment_rows(
            f"""
            SELECT TOP (100)
                CONVERT(varchar(50), M.MovimientoId) AS num,
                CONVERT(varchar(50), C.CorteId) AS codigo_periodo,
                CONVERT(varchar(10), M.FechaMovimiento, 23) AS fecha_pago,
                CONVERT(varchar(10), M.FechaDeposito, 23) AS fecha_deposito,
                LTRIM(RTRIM(M.Concepto)) AS detalle,
                CASE WHEN M.TipoMovimiento = 'DEBE' THEN M.Valor ELSE 0 END AS valor,
                CASE WHEN M.TipoMovimiento = 'HABER' THEN M.Valor ELSE 0 END AS valor_registrado,
                LTRIM(RTRIM(M.Banco)) AS banco,
                LTRIM(RTRIM(M.NumeroDeposito)) AS numero_deposito,
                LTRIM(RTRIM(M.NumeroComprobante)) AS referencia,
                LTRIM(RTRIM(M.UrlComprobante)) AS url_deposito,
                LTRIM(RTRIM(M.UsuarioRegistro)) AS usuario_registro,
                LTRIM(RTRIM(M.FormaPago)) AS forma_pago,
                LTRIM(RTRIM(M.TipoMovimiento)) AS tipo_movimiento,
                LTRIM(RTRIM(M.EstadoMovimiento)) AS estado_movimiento,
                CASE
                    WHEN M.TipoMovimiento <> 'HABER' OR UPPER(ISNULL(M.FormaPago, '')) = 'DESCUENTO' THEN 'NO_APLICA'
                    WHEN F.FacturaMovimientoId IS NULL OR F.EstadoFactura <> 'SUBIDA' THEN 'PENDIENTE'
                    ELSE 'SUBIDA'
                END AS estado_factura,
                LTRIM(RTRIM(F.NumeroFactura)) AS numero_factura,
                LTRIM(RTRIM(F.NombreArchivo)) AS nombre_factura,
                LTRIM(RTRIM(F.UrlDocumento)) AS url_factura,
                CONVERT(varchar(19), COALESCE(F.FechaModifica, F.FechaRegistro), 120) AS fecha_factura
            FROM {movements_table} AS M
            INNER JOIN {accounts_table} AS C ON C.CuentaId = M.CuentaId
            LEFT JOIN {invoices_table} AS F ON F.MovimientoId = M.MovimientoId
            WHERE M.CuentaId = %s
            ORDER BY M.FechaMovimiento DESC, M.MovimientoId DESC
            """,
            [selected_account_id],
        )
    active_rows = [
        row for row in payment_rows
        if _clean_text(row.get('estado_movimiento')).upper() == 'ACTIVO'
    ]
    total_value = sum(
        (_to_decimal(row.get('valor')) for row in active_rows),
        Decimal('0.00'),
    )
    registered_value = sum(
        (
            _to_decimal(row.get('valor_registrado'))
            for row in active_rows
            if _clean_text(row.get('forma_pago')).upper() != 'DESCUENTO'
        ),
        Decimal('0.00'),
    )
    discount_value = sum(
        (
            _to_decimal(row.get('valor_registrado'))
            for row in active_rows
            if _clean_text(row.get('forma_pago')).upper() == 'DESCUENTO'
        ),
        Decimal('0.00'),
    )
    excel_net_adjustment = sum(
        (
            _to_decimal(row.get('valor_registrado'))
            for row in active_rows
            if _is_excel_net_adjustment(row)
        ),
        Decimal('0.00'),
    )
    if _safe_int(student_rows[0].get('is_excel_enrollment'), default=0) == 1:
        total_value = EXCEL_ENROLLMENT_NET_AMOUNT
        discount_value = max(Decimal('0.00'), discount_value - excel_net_adjustment)
    total_value = _effective_total_value(total_value, registered_value)
    pending_balance = max(Decimal('0.00'), total_value - registered_value - discount_value)
    account_status = 'PAGADO' if total_value > 0 and pending_balance <= 0 else 'PENDIENTE'
    return {
        'student': {key: _clean_text(value) for key, value in student_rows[0].items()},
        'payments': [
            {
                **row,
                'valor': str(row.get('valor') or '0.00'),
                'valor_registrado': str(row.get('valor_registrado') or '0.00'),
                'estado_cuenta': account_status,
            }
            for row in payment_rows
        ],
        'summary': {
            'total_value': str(total_value),
            'registered_value': str(registered_value),
            'discount_value': str(discount_value),
            'pending_balance': str(pending_balance),
            'payment_status': account_status,
        },
        'source_database': complement_database_name(),
    }


def get_payment_student_profile(codigo_estud: Any = '', *, cedula: Any = '') -> dict[str, Any]:
    clean_code = _clean_text(codigo_estud)
    clean_identity = _clean_text(cedula)
    if clean_code and not clean_code.isdigit():
        raise PaymentGatewayError('El código del estudiante no es válido.')
    if clean_identity and not clean_identity.isdigit():
        raise PaymentGatewayError('La cédula del estudiante no es válida.')
    if not clean_code and not clean_identity:
        raise PaymentGatewayError('Debes indicar un estudiante válido.')
    student_filter = 'D.codigo_estud = %s' if clean_code else "LTRIM(RTRIM(CONVERT(varchar(50), D.Cedula_Est))) = %s"
    student_filter_value = clean_code or clean_identity
    rows = _fetch_payment_rows(
        f"""
        SELECT TOP (1)
            CONVERT(varchar(50), D.codigo_estud) AS codigo_estud,
            LTRIM(RTRIM(CONVERT(varchar(50), D.Cedula_Est))) AS cedula,
            LTRIM(RTRIM(D.Apellidos_nombre)) AS nombre,
            LTRIM(RTRIM(D.correo)) AS correo_personal,
            LTRIM(RTRIM(D.correointec)) AS correo_intec,
            LTRIM(RTRIM(D.telefono)) AS telefono,
            LTRIM(RTRIM(D.movil)) AS movil,
            CONVERT(varchar(10), D.Fecha_Nac, 23) AS fecha_nacimiento,
            LTRIM(RTRIM(D.Sexo)) AS sexo,
            LTRIM(RTRIM(D.Nacionalidad)) AS nacionalidad,
            LTRIM(RTRIM(D.EstadoCivil)) AS estado_civil,
            LTRIM(RTRIM(D.ciudad)) AS ciudad,
            LTRIM(RTRIM(D.Canton)) AS canton,
            LTRIM(RTRIM(D.sector)) AS sector,
            LTRIM(RTRIM(D.calle_principal)) AS direccion,
            LTRIM(RTRIM(D.Ocupacion)) AS ocupacion,
            LTRIM(RTRIM(D.empresa)) AS empresa,
            LTRIM(RTRIM(D.Lugar_Trabajo)) AS lugar_trabajo,
            LTRIM(RTRIM(D.DireccionTrabajo)) AS direccion_trabajo,
            LTRIM(RTRIM(D.Telefono_Trabajo)) AS telefono_trabajo,
            CONVERT(varchar(10), D.Fecha_Ingreso, 23) AS fecha_ingreso,
            LTRIM(RTRIM(D.tipodocumento)) AS tipo_documento,
            LTRIM(RTRIM(D.Estado)) AS estado
        FROM dbo.DATOS_ESTUD AS D
        WHERE {student_filter}
        """,
        [student_filter_value],
    )
    academic_student = rows[0] if rows else {}
    resolved_identity = _clean_text(academic_student.get('cedula')) or clean_identity
    request_rows = _fetch_payment_rows(
        """
        SELECT TOP (1)
            CONVERT(varchar(50), I.Id) AS solicitud_id,
            LTRIM(RTRIM(I.Cedula)) AS cedula,
            LTRIM(RTRIM(I.NombreEstudiante)) AS nombre,
            LTRIM(RTRIM(I.Email)) AS correo_personal,
            COALESCE(
                NULLIF(JSON_VALUE(I.ProviderResponse, '$.microsoft365.user.correo'), ''),
                NULLIF(JSON_VALUE(I.ProviderResponse, '$.microsoft365.user.user.correo'), '')
            ) AS correo_intec,
            LTRIM(RTRIM(I.Matricula)) AS matricula,
            LTRIM(RTRIM(I.Monto)) AS monto,
            LTRIM(RTRIM(I.Descripcion)) AS descripcion_pago,
            LTRIM(RTRIM(I.PaymentLink)) AS link_pago,
            JSON_VALUE(I.ProviderResponse, '$.provider.data.id') AS transaccion_id,
            CASE
                WHEN PR.Num IS NOT NULL
                  OR LOWER(LTRIM(RTRIM(ISNULL(JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre'), ''))))
                     IN ('pagada','pagado','aprobada','aprobado','completada','completado')
                THEN 'PAGO CONFIRMADO'
                ELSE 'GENERADA'
            END AS estado_pago,
            COALESCE(NULLIF(JSON_VALUE(I.ProviderResponse, '$.official_sync.record.codigo_estud'), ''), DS.codigo_estud) AS codigo_estud,
            JSON_VALUE(I.ProviderResponse, '$.official_sync.record.corte_id') AS corte_id,
            ISNULL(PR.ValorRegistrado, 0) AS valor_pagado_link,
            CONVERT(varchar(10), COALESCE(PR.FechaDeposito, PR.fechapago, PR.FechaRegistro), 23) AS fecha_pago_link,
            CONVERT(varchar(19), I.CreadoEn, 120) AS fecha_solicitud
        FROM dbo.INSCRIPCION_SOLICITUD_PAGO AS I
        OUTER APPLY (
            SELECT TOP (1) CONVERT(varchar(50), D.codigo_estud) AS codigo_estud
            FROM dbo.DATOS_ESTUD AS D
            WHERE LTRIM(RTRIM(CONVERT(varchar(50), D.Cedula_Est))) = LTRIM(RTRIM(I.Cedula))
            ORDER BY D.codigo_estud DESC
        ) AS DS
        OUTER APPLY (
            SELECT TOP (1) R.Num, R.ValorRegistrado, R.FechaDeposito, R.fechapago, R.FechaRegistro
            FROM dbo.REGISTROPAGOS AS R
            WHERE CONVERT(varchar(50), R.Codestu) = COALESCE(
                    NULLIF(JSON_VALUE(I.ProviderResponse, '$.official_sync.record.codigo_estud'), ''),
                    DS.codigo_estud
                  )
              AND CONVERT(varchar(50), R.codperiodo) = I.CodigoPeriodo
              AND (
                  NULLIF(LTRIM(RTRIM(R.Referencia)), '') = NULLIF(LTRIM(RTRIM(I.PaymentLink)), '')
                  OR NULLIF(LTRIM(RTRIM(R.NoDeposito)), '') = JSON_VALUE(I.ProviderResponse, '$.provider.data.id')
              )
            ORDER BY COALESCE(R.FechaDeposito, R.fechapago, R.FechaRegistro) DESC, R.Num DESC
        ) AS PR
        WHERE LTRIM(RTRIM(I.Cedula)) = %s
          AND (
              PR.Num IS NOT NULL
              OR LOWER(LTRIM(RTRIM(ISNULL(JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre'), ''))))
                 IN ('generada','generado','pagada','pagado','aprobada','aprobado','completada','completado')
          )
        ORDER BY I.CreadoEn DESC, I.Id DESC
        """,
        [resolved_identity],
    ) if resolved_identity else []
    payment_request = request_rows[0] if request_rows else {}
    if not academic_student and not payment_request:
        raise PaymentGatewayError('No se encontraron datos académicos ni solicitudes para el estudiante.')

    merged_student = dict(payment_request)
    for key, value in academic_student.items():
        if _clean_text(value):
            merged_student[key] = value
    resolved_code = _clean_text(merged_student.get('codigo_estud')) or clean_code
    if resolved_code and is_complement_available([('fin', 'VW_BalanceEstudiante', 'V')]):
        balance_view = _continuing_education_object('fin', 'VW_BalanceEstudiante')
        balance_rows = _fetch_payment_rows(
            f"""
            SELECT
                COUNT(*) AS cantidad_cuentas,
                ISNULL(SUM(TotalDebe), 0) AS total_facturado_educontinua,
                ISNULL(SUM(TotalHaber), 0) AS total_pagado_educontinua,
                ISNULL(SUM(SaldoPendiente), 0) AS saldo_pendiente_educontinua,
                CONVERT(varchar(19), MAX(UltimoPago), 120) AS ultimo_pago_educontinua
            FROM {balance_view}
            WHERE CONVERT(varchar(50), CodigoEstud) = %s
            """,
            [resolved_code],
        )
        balance = balance_rows[0] if balance_rows else {}
        if int(balance.get('cantidad_cuentas') or 0) > 0:
            pending_balance = _to_decimal(balance.get('saldo_pendiente_educontinua'))
            merged_student.update({
                'estado_financiero_educontinua': 'AL DÍA' if pending_balance <= 0 else 'PENDIENTE',
                'total_facturado_educontinua': balance.get('total_facturado_educontinua') or 0,
                'total_pagado_educontinua': balance.get('total_pagado_educontinua') or 0,
                'saldo_pendiente_educontinua': balance.get('saldo_pendiente_educontinua') or 0,
                'ultimo_pago_educontinua': balance.get('ultimo_pago_educontinua'),
            })
    enrollments = _fetch_payment_rows(
        """
        SELECT
            CONVERT(varchar(50), E.CorteEstudianteId) AS estudiante_corte_id,
            CONVERT(varchar(50), E.CorteId) AS corte_id,
            LTRIM(RTRIM(CC.NombreCorte)) AS corte,
            LTRIM(RTRIM(P.Nomb_Materia)) AS curso,
            LTRIM(RTRIM(E.EstadoParticipacion)) AS estado,
            CONVERT(varchar(10), E.FechaInicioEstudiante, 23) AS fecha_inicio
        FROM dbo.CORTE_CURSO_ESTUDIANTE AS E
        INNER JOIN dbo.CORTE_CURSO AS CC ON CC.CorteId = E.CorteId
        LEFT JOIN dbo.PENSUM AS P
          ON P.Cod_AnioBasica = E.Cod_AnioBasica AND P.codigo_materia = E.CodigoMateria
        WHERE E.CodigoEstud = %s
        ORDER BY E.FechaRegistro DESC, E.CorteEstudianteId DESC
        """,
        [resolved_code],
    ) if resolved_code else []
    return {
        'student': {key: _clean_text(value) for key, value in merged_student.items()},
        'enrollments': [{key: _clean_text(value) for key, value in row.items()} for row in enrollments],
        'has_academic_record': bool(academic_student),
        'has_payment_request': bool(payment_request),
    }


def register_continuing_education_payment(payload: dict[str, Any], *, user_login: str) -> dict[str, Any]:
    _ensure_continuing_education_payments_available()
    codigo_estud = _clean_text(payload.get('codigo_estud'))
    corte_id = _clean_text(payload.get('corte_id'))
    estudiante_corte_id = _clean_text(payload.get('estudiante_corte_id'))
    value = _to_decimal(payload.get('valor'))
    if not codigo_estud.isdigit() or not corte_id.isdigit() or not estudiante_corte_id.isdigit():
        raise PaymentGatewayError('La matrícula seleccionada no es válida.')
    if value <= 0:
        raise PaymentGatewayError('El valor del pago debe ser mayor a cero.')
    if value > Decimal('99999999.99'):
        raise PaymentGatewayError('El valor del pago supera el máximo permitido.')
    payment_method = _trim_to_max(payload.get('forma_pago') or 'VOUCHER', 50).upper()
    if payment_method == 'VOUCHER' and not _clean_text(payload.get('voucher_base64')):
        raise PaymentGatewayError('Debes adjuntar el voucher para registrar este pago.')

    enrollment_rows = _fetch_payment_rows(
        """
        SELECT TOP (1)
            E.CorteEstudianteId,
            E.CorteId,
            E.CodigoEstud,
            LTRIM(RTRIM(CC.NombreCorte)) AS NombreCorte,
            LTRIM(RTRIM(P.Nomb_Materia)) AS NombreCurso,
            LTRIM(RTRIM(D.Apellidos_nombre)) AS NombreEstudiante,
            COALESCE(NULLIF(LTRIM(RTRIM(D.correointec)), ''), NULLIF(LTRIM(RTRIM(D.correo)), '')) AS UsuarioLogin
        FROM dbo.CORTE_CURSO_ESTUDIANTE AS E
        INNER JOIN dbo.CORTE_CURSO AS CC ON CC.CorteId = E.CorteId
        LEFT JOIN dbo.PENSUM AS P
          ON P.Cod_AnioBasica = E.Cod_AnioBasica AND P.codigo_materia = E.CodigoMateria
        LEFT JOIN dbo.DATOS_ESTUD AS D ON D.codigo_estud = E.CodigoEstud
        WHERE E.CorteEstudianteId = %s AND E.CorteId = %s AND E.CodigoEstud = %s AND E.EstadoRegistro = 'A'
        """,
        [estudiante_corte_id, corte_id, codigo_estud],
    )
    if not enrollment_rows:
        raise PaymentGatewayError('El estudiante no tiene una matrícula activa en el corte seleccionado.')
    enrollment = enrollment_rows[0]

    configure_result = configure_cut_in_complement(corte_id, usuario_registro=user_login)
    if not configure_result.get('synced'):
        raise PaymentGatewayError(_clean_text(configure_result.get('message')) or 'No se pudo configurar el corte en Educación Continua.')
    sync_result = sync_student_enrollment_to_complement(
        corte_id=corte_id,
        codigo_estud=codigo_estud,
        usuario_registro=user_login,
        registrar_cargo_inicial=True,
    )
    if not sync_result.get('synced'):
        raise PaymentGatewayError(_clean_text(sync_result.get('message')) or 'No se pudo sincronizar la matrícula en Educación Continua.')

    complement_enrollments = _continuing_education_object('edu', 'CorteEstudiante')
    rows = _fetch_payment_rows(
        f"""
        SELECT TOP (1) EstudianteCorteId
        FROM {complement_enrollments}
        WHERE CorteEstudianteIdPrincipal = %s AND CorteId = %s AND CodigoEstud = %s
        """,
        [estudiante_corte_id, corte_id, codigo_estud],
    )
    if not rows:
        raise PaymentGatewayError('La matrícula no quedó disponible en INTECEDUCONTINUA.')
    complement_enrollment_id = _clean_text(rows[0].get('EstudianteCorteId'))

    voucher = _store_continuing_education_voucher(
        payload,
        codigo_estud=codigo_estud,
        course_name=_clean_text(enrollment.get('NombreCurso')),
        cut_name=_clean_text(enrollment.get('NombreCorte')),
        student_name=_clean_text(enrollment.get('NombreEstudiante')),
    )
    procedure = _continuing_education_object('fin', 'usp_RegistrarDepositoEstudiante')
    payment_rows = _fetch_payment_rows(
        f"""
        EXEC {procedure}
            @EstudianteCorteId = %s,
            @Valor = %s,
            @FormaPago = %s,
            @Banco = %s,
            @NumeroDeposito = %s,
            @FechaDeposito = %s,
            @NumeroComprobante = %s,
            @UrlComprobante = %s,
            @NombreArchivoComprobante = %s,
            @HashComprobante = %s,
            @UsuarioRegistro = %s,
            @Observacion = %s
        """,
        [
            complement_enrollment_id,
            value,
            payment_method,
            _trim_to_max(payload.get('banco'), 100),
            _trim_to_max(payload.get('numero_deposito'), 100),
            _clean_text(payload.get('fecha_deposito')) or None,
            _trim_to_max(payload.get('numero_comprobante'), 100),
            voucher['relative_path'],
            voucher['file_name'],
            voucher['sha256'],
            _trim_to_max(user_login or 'SISTEMA', 50),
            _trim_to_max(payload.get('observacion'), 500),
        ],
    )
    cache.delete(f'dashboard:continuing-education-payment-metrics:v5-invoices:{complement_database_name()}')
    payment_result = payment_rows[0] if payment_rows else {}
    payment_reference = _first_non_empty(
        payment_result.get('MovimientoId'),
        payment_result.get('movimiento_id'),
        payment_result.get('movimientoid'),
        payload.get('numero_comprobante'),
        payload.get('numero_deposito'),
        f'generated-{secrets.token_hex(6)}',
    )
    notification_data = {
        'codigo_estud': codigo_estud,
        'corte_id': corte_id,
        'estudiante_corte_id': estudiante_corte_id,
        'movement_id': payment_reference,
        'payment_method': payment_method,
        'value': str(value),
        'course_name': _clean_text(enrollment.get('NombreCurso')),
    }
    create_notification_safely(
        event_key=f'manual-payment:{codigo_estud}:{corte_id}:{payment_reference}:student',
        notification_type='PAYMENT_CONFIRMED',
        title='Pago registrado',
        message=f'Se registró un pago de USD {value:.2f} en tu cuenta de Educación Continua.',
        recipient_category='student',
        recipient_login=_clean_text(enrollment.get('UsuarioLogin')),
        route='#dashboard',
        data=notification_data,
    )
    create_notification_safely(
        event_key=f'manual-payment:{codigo_estud}:{corte_id}:{payment_reference}:financial',
        notification_type='PAYMENT_CONFIRMED',
        title='Pago manual registrado',
        message=f'{enrollment.get("NombreEstudiante")}: USD {value:.2f} mediante {payment_method}.',
        recipient_category='staff',
        recipient_role='FINANCIERO',
        route='#payments',
        data=notification_data,
    )
    return {
        'ok': True,
        'database': complement_database_name(),
        'payment': payment_rows[0] if payment_rows else {},
        'voucher': {
            'file_name': voucher['file_name'],
            'stored': bool(voucher['relative_path']),
            'location': voucher.get('folder_path', ''),
            'web_url': voucher.get('web_url', ''),
        },
    }


def register_continuing_education_discount(payload: dict[str, Any], *, user_login: str) -> dict[str, Any]:
    _ensure_continuing_education_payments_available()
    if not is_complement_available([('fin', 'usp_RegistrarMovimientoCuenta', 'P')]):
        raise PaymentGatewayError('El módulo financiero no permite registrar descuentos actualmente.')
    codigo_estud = _clean_text(payload.get('codigo_estud'))
    corte_id = _clean_text(payload.get('corte_id'))
    estudiante_corte_id = _clean_text(payload.get('estudiante_corte_id'))
    value = _to_decimal(payload.get('valor'))
    if not codigo_estud.isdigit() or not corte_id.isdigit() or not estudiante_corte_id.isdigit():
        raise PaymentGatewayError('La matrícula seleccionada no es válida.')
    if value <= 0:
        raise PaymentGatewayError('El descuento debe ser mayor a cero.')
    discount_type = _trim_to_max(payload.get('tipo_descuento') or 'OTRO', 50).upper()
    allowed_types = {'BECA', 'CONVENIO', 'PRONTO_PAGO', 'PROMOCIONAL', 'INSTITUCIONAL', 'OTRO'}
    if discount_type not in allowed_types:
        raise PaymentGatewayError('El tipo de descuento no es válido.')
    reason = _trim_to_max(payload.get('motivo'), 200)
    if not reason:
        raise PaymentGatewayError('Debes indicar el motivo del descuento.')

    enrollment_rows = _fetch_payment_rows(
        """
        SELECT TOP (1) E.CorteEstudianteId, E.CorteId, E.CodigoEstud,
            LTRIM(RTRIM(E.ApellidosNombre)) AS NombreEstudiante,
            COALESCE(NULLIF(LTRIM(RTRIM(D.correointec)), ''), NULLIF(LTRIM(RTRIM(D.correo)), '')) AS UsuarioLogin
        FROM dbo.CORTE_CURSO_ESTUDIANTE AS E
        LEFT JOIN dbo.DATOS_ESTUD AS D ON D.codigo_estud = E.CodigoEstud
        WHERE E.CorteEstudianteId = %s AND E.CorteId = %s AND E.CodigoEstud = %s AND E.EstadoRegistro = 'A'
        """,
        [estudiante_corte_id, corte_id, codigo_estud],
    )
    if not enrollment_rows:
        raise PaymentGatewayError('El estudiante no tiene una matrícula activa en la corte seleccionada.')
    enrollment = enrollment_rows[0]
    configure_result = configure_cut_in_complement(corte_id, usuario_registro=user_login)
    if not configure_result.get('synced'):
        raise PaymentGatewayError(_clean_text(configure_result.get('message')) or 'No se pudo configurar la corte.')
    sync_result = sync_student_enrollment_to_complement(
        corte_id=corte_id,
        codigo_estud=codigo_estud,
        usuario_registro=user_login,
        registrar_cargo_inicial=True,
    )
    if not sync_result.get('synced'):
        raise PaymentGatewayError(_clean_text(sync_result.get('message')) or 'No se pudo sincronizar la matrícula.')

    complement_enrollments = _continuing_education_object('edu', 'CorteEstudiante')
    accounts_table = _continuing_education_object('fin', 'CuentaEstudiante')
    movements_table = _continuing_education_object('fin', 'MovimientoCuenta')
    balance_rows = _fetch_payment_rows(
        f"""
        SELECT TOP (1)
            CE.EstudianteCorteId,
            C.CuentaId,
            ISNULL(SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'DEBE' THEN M.Valor ELSE 0 END), 0)
              - ISNULL(SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' THEN M.Valor ELSE 0 END), 0) AS SaldoPendiente
        FROM {complement_enrollments} AS CE
        INNER JOIN {accounts_table} AS C ON C.EstudianteCorteId = CE.EstudianteCorteId
        LEFT JOIN {movements_table} AS M ON M.CuentaId = C.CuentaId
        WHERE CE.CorteEstudianteIdPrincipal = %s
        GROUP BY CE.EstudianteCorteId, C.CuentaId
        """,
        [estudiante_corte_id],
    )
    if not balance_rows:
        raise PaymentGatewayError('No se encontró la cuenta financiera del estudiante.')
    account = balance_rows[0]
    pending_balance = _to_decimal(account.get('SaldoPendiente'))
    if pending_balance <= 0:
        raise PaymentGatewayError('La cuenta no tiene saldo pendiente para aplicar un descuento.')
    if value > pending_balance:
        raise PaymentGatewayError(f'El descuento no puede superar el saldo pendiente de ${pending_balance:.2f}.')

    procedure = _continuing_education_object('fin', 'usp_RegistrarMovimientoCuenta')
    movement_rows = _fetch_payment_rows(
        f"""
        EXEC {procedure}
            @CuentaId = %s,
            @TipoMovimiento = 'HABER',
            @Concepto = %s,
            @Valor = %s,
            @FormaPago = 'DESCUENTO',
            @UsuarioRegistro = %s,
            @Observacion = %s
        """,
        [
            account.get('CuentaId'),
            _trim_to_max(f'DESCUENTO {discount_type}: {reason}', 200),
            value,
            _trim_to_max(user_login or 'SISTEMA', 50),
            _trim_to_max(payload.get('observacion'), 500),
        ],
    )
    cache.delete(f'dashboard:continuing-education-payment-metrics:v5-invoices:{complement_database_name()}')
    create_notification_safely(
        event_key=f"discount:{account.get('CuentaId')}:{movement_rows[0].get('MovimientoId') if movement_rows else reason}",
        notification_type='DISCOUNT_APPLIED',
        title='Descuento aplicado',
        message=f'Se aplicó un descuento de ${value:.2f} a tu cuenta de Educación Continua.',
        recipient_category='student',
        recipient_login=_clean_text(enrollment.get('UsuarioLogin')),
        route='#dashboard',
        data={'codigo_estud': codigo_estud, 'corte_id': corte_id, 'discount_type': discount_type, 'value': str(value)},
    )
    return {
        'ok': True,
        'database': complement_database_name(),
        'discount': movement_rows[0] if movement_rows else {},
        'discount_type': discount_type,
        'value': str(value),
        'pending_balance': str(max(Decimal('0.00'), pending_balance - value)),
    }


def upload_continuing_education_invoice(payload: dict[str, Any], *, user_login: str) -> dict[str, Any]:
    _ensure_continuing_education_payments_available()
    if not is_complement_available([('fin', 'FacturaMovimiento', 'U')]):
        raise PaymentGatewayError('El control de facturación no está instalado en INTECEDUCONTINUA.')
    movement_id = _clean_text(payload.get('movimiento_id') or payload.get('movement_id'))
    if not movement_id.isdigit():
        raise PaymentGatewayError('Debes seleccionar un movimiento de pago válido.')

    movements_table = _continuing_education_object('fin', 'MovimientoCuenta')
    accounts_table = _continuing_education_object('fin', 'CuentaEstudiante')
    enrollments_view = _continuing_education_object('edu', 'VW_MatriculaEstudianteCompleta')
    cuts_view = _continuing_education_object('edu', 'VW_CorteCursoDetalle')
    invoices_table = _continuing_education_object('fin', 'FacturaMovimiento')
    rows = _fetch_payment_rows(
        f"""
        SELECT TOP (1)
            M.MovimientoId AS movimiento_id,
            CONVERT(varchar(50), C.CodigoEstud) AS codigo_estud,
            LTRIM(RTRIM(E.ApellidosNombre)) AS nombre_estudiante,
            LTRIM(RTRIM(CC.NombreCursoMateria)) AS nombre_curso,
            LTRIM(RTRIM(CC.NombreCorte)) AS nombre_corte
        FROM {movements_table} AS M
        INNER JOIN {accounts_table} AS C ON C.CuentaId = M.CuentaId
        INNER JOIN {enrollments_view} AS E ON E.EstudianteCorteId = C.EstudianteCorteId
        INNER JOIN {cuts_view} AS CC ON CC.CorteId = C.CorteId
        WHERE M.MovimientoId = %s
          AND M.EstadoMovimiento = 'ACTIVO'
          AND M.TipoMovimiento = 'HABER'
          AND UPPER(ISNULL(M.FormaPago, '')) <> 'DESCUENTO'
        """,
        [movement_id],
    )
    if not rows:
        raise PaymentGatewayError('El movimiento no corresponde a un pago activo que pueda facturarse.')
    payment = rows[0]
    invoice = _store_continuing_education_invoice(
        payload,
        codigo_estud=_clean_text(payment.get('codigo_estud')),
        course_name=_clean_text(payment.get('nombre_curso')),
        cut_name=_clean_text(payment.get('nombre_corte')),
        student_name=_clean_text(payment.get('nombre_estudiante')),
    )
    result_rows = _fetch_payment_rows(
        f"""
        MERGE {invoices_table} AS T
        USING (SELECT %s AS MovimientoId) AS S ON S.MovimientoId = T.MovimientoId
        WHEN MATCHED THEN UPDATE SET
            EstadoFactura = 'SUBIDA', NumeroFactura = NULLIF(%s, ''),
            UrlDocumento = %s, NombreArchivo = %s, HashDocumento = %s,
            UsuarioModifica = %s, FechaModifica = sysdatetime(), Observacion = NULLIF(%s, '')
        WHEN NOT MATCHED THEN INSERT (
            MovimientoId, EstadoFactura, NumeroFactura, UrlDocumento,
            NombreArchivo, HashDocumento, UsuarioRegistro, Observacion
        ) VALUES (%s, 'SUBIDA', NULLIF(%s, ''), %s, %s, %s, %s, NULLIF(%s, ''));
        SELECT TOP (1)
            CONVERT(varchar(50), FacturaMovimientoId) AS factura_id,
            CONVERT(varchar(50), MovimientoId) AS movimiento_id,
            EstadoFactura AS estado_factura, NumeroFactura AS numero_factura,
            UrlDocumento AS url_factura, NombreArchivo AS nombre_factura,
            CONVERT(varchar(19), COALESCE(FechaModifica, FechaRegistro), 120) AS fecha_factura
        FROM {invoices_table} WHERE MovimientoId = %s;
        """,
        [
            movement_id,
            _trim_to_max(payload.get('numero_factura'), 100),
            invoice['relative_path'], invoice['file_name'], invoice['sha256'],
            _trim_to_max(user_login or 'SISTEMA', 50), _trim_to_max(payload.get('observacion'), 500),
            movement_id, _trim_to_max(payload.get('numero_factura'), 100),
            invoice['relative_path'], invoice['file_name'], invoice['sha256'],
            _trim_to_max(user_login or 'SISTEMA', 50), _trim_to_max(payload.get('observacion'), 500),
            movement_id,
        ],
    )
    cache.delete(f'dashboard:continuing-education-payment-metrics:v5-invoices:{complement_database_name()}')
    return {
        'ok': True,
        'database': complement_database_name(),
        'invoice': result_rows[0] if result_rows else {},
        'storage': {
            'file_name': invoice['file_name'],
            'location': invoice.get('folder_path', ''),
            'web_url': invoice.get('web_url', ''),
        },
    }


def _store_continuing_education_voucher(
    payload: dict[str, Any],
    *,
    codigo_estud: str,
    course_name: str,
    cut_name: str,
    student_name: str,
) -> dict[str, str]:
    content = _clean_text(payload.get('voucher_base64'))
    original_name = Path(_clean_text(payload.get('voucher_name'))).name
    if not content:
        return {'relative_path': '', 'file_name': '', 'sha256': ''}
    if ',' in content:
        content = content.split(',', 1)[1]
    try:
        raw = b64decode(content, validate=True)
    except Exception as exc:
        raise PaymentGatewayError('El voucher adjunto no tiene un formato válido.') from exc
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise PaymentGatewayError('El voucher debe pesar entre 1 byte y 5 MB.')
    extension = Path(original_name).suffix.lower()
    if extension not in {'.pdf', '.png', '.jpg', '.jpeg'}:
        raise PaymentGatewayError('El voucher debe ser PDF, PNG, JPG o JPEG.')
    _validate_voucher_content(raw, extension)
    digest = sha256(raw).hexdigest()
    receipt_reference = _clean_text(payload.get('numero_comprobante')) or _clean_text(payload.get('numero_deposito'))
    receipt_date = _clean_text(payload.get('fecha_deposito')) or 'SIN_FECHA'
    reference_part = f'{receipt_reference}_' if receipt_reference else ''
    safe_name = f'{receipt_date}_COMPROBANTE_{reference_part}{digest[:8]}{extension}'
    try:
        uploaded = upload_continuing_education_voucher(
            raw,
            course_name=course_name,
            cut_name=cut_name,
            student_name=student_name,
            student_code=codigo_estud,
            file_name=safe_name,
        )
    except Microsoft365Error as exc:
        raise PaymentGatewayError(
            f'No fue posible guardar el comprobante en OneDrive; el pago no fue registrado. {exc}'
        ) from exc
    return {
        'relative_path': uploaded.get('web_url') or uploaded.get('relative_path') or '',
        'folder_path': (uploaded.get('relative_path') or '').rsplit('/', 1)[0],
        'web_url': uploaded.get('web_url') or '',
        'file_name': uploaded.get('file_name') or original_name or safe_name,
        'sha256': digest,
    }


def _store_continuing_education_invoice(
    payload: dict[str, Any],
    *,
    codigo_estud: str,
    course_name: str,
    cut_name: str,
    student_name: str,
) -> dict[str, str]:
    content = _clean_text(payload.get('invoice_base64') or payload.get('factura_base64'))
    original_name = Path(_clean_text(payload.get('invoice_name') or payload.get('factura_nombre'))).name
    if not content:
        raise PaymentGatewayError('Debes adjuntar el documento PDF de la factura.')
    if ',' in content:
        content = content.split(',', 1)[1]
    try:
        raw = b64decode(content, validate=True)
    except Exception as exc:
        raise PaymentGatewayError('El documento de factura no tiene un formato válido.') from exc
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise PaymentGatewayError('La factura debe pesar entre 1 byte y 5 MB.')
    extension = Path(original_name).suffix.lower()
    if extension != '.pdf':
        raise PaymentGatewayError('La factura debe ser un archivo PDF.')
    _validate_voucher_content(raw, extension)
    digest = sha256(raw).hexdigest()
    invoice_number = _clean_text(payload.get('numero_factura'))
    number_part = f'{invoice_number}_' if invoice_number else ''
    safe_name = f'FACTURA_{number_part}{digest[:8]}.pdf'
    try:
        uploaded = upload_continuing_education_voucher(
            raw,
            course_name=course_name,
            cut_name=cut_name,
            student_name=student_name,
            student_code=codigo_estud,
            file_name=safe_name,
            document_folder='FACTURAS',
        )
    except Microsoft365Error as exc:
        raise PaymentGatewayError(f'No fue posible guardar la factura en OneDrive. {exc}') from exc
    return {
        'relative_path': uploaded.get('web_url') or uploaded.get('relative_path') or '',
        'folder_path': (uploaded.get('relative_path') or '').rsplit('/', 1)[0],
        'web_url': uploaded.get('web_url') or '',
        'file_name': uploaded.get('file_name') or original_name or safe_name,
        'sha256': digest,
    }


def _validate_voucher_content(content: bytes, extension: str) -> None:
    signatures = {
        '.pdf': (b'%PDF-',),
        '.png': (b'\x89PNG\r\n\x1a\n',),
        '.jpg': (b'\xff\xd8\xff',),
        '.jpeg': (b'\xff\xd8\xff',),
    }
    if not any(content.startswith(signature) for signature in signatures.get(extension, ())):
        raise PaymentGatewayError(
            'El contenido del comprobante no coincide con la extensión seleccionada.'
        )


def _fetch_payment_rows(query: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection_for_query(query, params).cursor() as cursor:
        cursor.execute(query, params)
        while cursor.description is None:
            if not cursor.nextset():
                return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _list_generated_payment_links(search: str, payment_status: str) -> list[dict[str, Any]]:
    provider_status_expression = "LOWER(LTRIM(RTRIM(ISNULL(JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre'), ''))))"
    allowed_status_expression = (
        f"(PR.Num IS NOT NULL OR {provider_status_expression} IN "
        "('generada','generado','pagada','pagado','aprobada','aprobado','completada','completado'))"
    )
    where_parts: list[str] = [allowed_status_expression]
    params: list[Any] = []
    if search:
        pattern = f'%{search}%'
        where_parts.append(
            """
            (
                I.Cedula LIKE %s OR I.NombreEstudiante LIKE %s OR I.Email LIKE %s
                OR I.Matricula LIKE %s OR I.Descripcion LIKE %s OR I.PaymentLink LIKE %s
                OR JSON_VALUE(I.ProviderResponse, '$.provider.data.id') LIKE %s
            )
            """
        )
        params.extend([pattern] * 7)
    paid_expression = f"(PR.Num IS NOT NULL OR {provider_status_expression} IN ('pagada','pagado','aprobada','aprobado','completada','completado'))"
    if payment_status == 'with_payments':
        where_parts.append(paid_expression)
    elif payment_status == 'without_payments':
        where_parts.append(f"PR.Num IS NULL AND {provider_status_expression} IN ('generada','generado')")
    where_sql = 'WHERE ' + ' AND '.join(where_parts) if where_parts else ''

    rows = _fetch_payment_rows(
        f"""
        SELECT TOP (500)
            CONVERT(varchar(50), I.Id) AS inscription_payment_id,
            LTRIM(RTRIM(I.Cedula)) AS cedula,
            LTRIM(RTRIM(I.NombreEstudiante)) AS nombre,
            LTRIM(RTRIM(I.Email)) AS email,
            COALESCE(
                NULLIF(JSON_VALUE(I.ProviderResponse, '$.microsoft365.user.correo'), ''),
                NULLIF(JSON_VALUE(I.ProviderResponse, '$.microsoft365.user.user.correo'), ''),
                NULLIF(LTRIM(RTRIM(I.Email)), '')
            ) AS student_login,
            LTRIM(RTRIM(I.Matricula)) AS matricula,
            TRY_CONVERT(decimal(18,2), I.Monto) AS amount,
            LTRIM(RTRIM(I.Descripcion)) AS description,
            LTRIM(RTRIM(I.CodigoPeriodo)) AS codigo_periodo,
            LTRIM(RTRIM(I.CodigoMateria)) AS codigo_materia,
            LTRIM(RTRIM(I.PaymentLink)) AS payment_link,
            CONVERT(varchar(19), I.CreadoEn, 120) AS created_at,
            JSON_VALUE(I.ProviderResponse, '$.provider.data.id') AS provider_transaction_id,
            JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre') AS provider_status,
            COALESCE(NULLIF(JSON_VALUE(I.ProviderResponse, '$.official_sync.record.codigo_estud'), ''), DS.codigo_estud) AS codigo_estud,
            COALESCE(
                NULLIF(JSON_VALUE(I.ProviderResponse, '$.official_sync.record.materia_corte'), ''),
                NULLIF(JSON_VALUE(I.ProviderResponse, '$.official_sync.record.materia'), ''),
                NULLIF(LTRIM(RTRIM(I.Descripcion)), '')
            ) AS course_name,
            JSON_VALUE(I.ProviderResponse, '$.official_sync.record.corte_id') AS corte_id,
            COALESCE(
                NULLIF(JSON_VALUE(I.ProviderResponse, '$.official_sync.record.nombre_corte'), ''),
                CONCAT('Período ', LTRIM(RTRIM(I.CodigoPeriodo)))
            ) AS cut_name,
            JSON_VALUE(I.ProviderResponse, '$.payment_receipt.web_url') AS receipt_web_url,
            JSON_VALUE(I.ProviderResponse, '$.payment_receipt.item_id') AS receipt_item_id,
            JSON_VALUE(I.ProviderResponse, '$.payment_receipt.file_name') AS receipt_file_name,
            JSON_VALUE(I.ProviderResponse, '$.payment_receipt.sha256') AS receipt_sha256,
            CONVERT(varchar(50), PR.Num) AS payment_record_number,
            ISNULL(PR.ValorRegistrado, 0) AS registered_value,
            CONVERT(varchar(10), COALESCE(PR.FechaDeposito, PR.fechapago, PR.FechaRegistro), 23) AS paid_at,
            CASE WHEN {paid_expression} THEN 1 ELSE 0 END AS is_paid
        FROM dbo.INSCRIPCION_SOLICITUD_PAGO AS I
        OUTER APPLY (
            SELECT TOP (1) CONVERT(varchar(50), D.codigo_estud) AS codigo_estud
            FROM dbo.DATOS_ESTUD AS D
            WHERE LTRIM(RTRIM(CONVERT(varchar(50), D.Cedula_Est))) = LTRIM(RTRIM(I.Cedula))
            ORDER BY D.codigo_estud DESC
        ) AS DS
        OUTER APPLY (
            SELECT TOP (1) R.Num, R.ValorRegistrado, R.FechaDeposito, R.fechapago, R.FechaRegistro
            FROM dbo.REGISTROPAGOS AS R
            WHERE (
                NULLIF(JSON_VALUE(I.ProviderResponse, '$.official_sync.record.codigo_estud'), '') IS NOT NULL
                AND CONVERT(varchar(50), R.Codestu) = JSON_VALUE(I.ProviderResponse, '$.official_sync.record.codigo_estud')
            )
              AND CONVERT(varchar(50), R.codperiodo) = I.CodigoPeriodo
              AND (
                  NULLIF(LTRIM(RTRIM(R.Referencia)), '') = NULLIF(LTRIM(RTRIM(I.PaymentLink)), '')
                  OR NULLIF(LTRIM(RTRIM(R.NoDeposito)), '') = JSON_VALUE(I.ProviderResponse, '$.provider.data.id')
              )
            ORDER BY COALESCE(R.FechaDeposito, R.fechapago, R.FechaRegistro) DESC, R.Num DESC
        ) AS PR
        {where_sql}
        ORDER BY I.CreadoEn DESC, I.Id DESC
        """,
        params,
    )
    payments = [
        {
            **{key: _clean_text(value) for key, value in row.items()},
            'amount': str(row.get('amount') or '0.00'),
            'registered_value': str(row.get('registered_value') or '0.00'),
            'is_paid': bool(row.get('is_paid')),
            'display_status': 'PAGO CONFIRMADO' if row.get('is_paid') else (_clean_text(row.get('provider_status')) or 'GENERADO').upper(),
        }
        for row in rows
    ]
    for payment in payments:
        if payment['is_paid']:
            _ensure_all_digital_payment_receipt(payment)
    return payments


def generate_all_digital_payment_receipt_document(inscription_payment_id: Any) -> dict[str, Any]:
    clean_id = _clean_text(inscription_payment_id)
    if not clean_id.isdigit():
        raise PaymentGatewayError('La solicitud de pago no es válida.')
    payments = _list_generated_payment_links('', 'all')
    payment = next(
        (item for item in payments if _clean_text(item.get('inscription_payment_id')) == clean_id),
        None,
    )
    if not payment:
        raise PaymentGatewayError('No se encontró una solicitud activa para generar el documento.')
    if not payment.get('is_paid'):
        raise PaymentGatewayError('El documento solo puede generarse cuando el pago esté confirmado.')
    _ensure_all_digital_payment_receipt(payment, force=True)
    if payment.get('receipt_status') != 'GUARDADO' or not _clean_text(payment.get('receipt_web_url')):
        raise PaymentGatewayError(
            _clean_text(payment.get('receipt_sync_error')) or 'No fue posible guardar el documento en OneDrive.'
        )
    return {
        'inscription_payment_id': clean_id,
        'transaction_id': _clean_text(payment.get('provider_transaction_id')),
        'status': payment.get('receipt_status'),
        'file_name': _clean_text(payment.get('receipt_file_name')),
        'web_url': _clean_text(payment.get('receipt_web_url')),
    }


def _ensure_all_digital_payment_receipt(payment: dict[str, Any], *, force: bool = False) -> None:
    _notify_confirmed_link_payment(payment)
    if _clean_text(payment.get('receipt_web_url')) and not force:
        payment['receipt_status'] = 'GUARDADO'
        _sync_confirmed_link_payment_to_complement(payment)
        return
    transaction_id = _clean_text(payment.get('provider_transaction_id'))
    student_code = _clean_text(payment.get('codigo_estud'))
    request_id = _clean_text(payment.get('inscription_payment_id'))
    if not transaction_id or not student_code or not request_id:
        payment['receipt_status'] = 'PENDIENTE'
        payment['receipt_sync_error'] = 'Faltan datos oficiales para generar el comprobante.'
        return
    try:
        document = build_all_digital_payment_receipt(payment)
        digest = sha256(document).hexdigest()
        file_name = f'COMPROBANTE_ALLDIGITAL_{transaction_id}.pdf'
        uploaded = upload_continuing_education_voucher(
            document,
            course_name=_clean_text(payment.get('course_name')),
            cut_name=_clean_text(payment.get('cut_name')),
            student_name=_clean_text(payment.get('nombre')),
            student_code=student_code,
            file_name=file_name,
        )
        receipt = {
            'source': 'ALLDIGITAL',
            'status': 'PAGO CONFIRMADO',
            'transaction_id': transaction_id,
            'payment_record_number': _clean_text(payment.get('payment_record_number')),
            'item_id': _clean_text(uploaded.get('item_id')),
            'file_name': _clean_text(uploaded.get('file_name')) or file_name,
            'relative_path': _clean_text(uploaded.get('relative_path')),
            'web_url': _clean_text(uploaded.get('web_url')),
            'sha256': digest,
        }
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE dbo.INSCRIPCION_SOLICITUD_PAGO
                SET ProviderResponse = JSON_MODIFY(
                    ProviderResponse,
                    '$.payment_receipt',
                    JSON_QUERY(%s)
                )
                WHERE Id = %s AND ISJSON(ProviderResponse) = 1
                """,
                [json.dumps(receipt, ensure_ascii=False), request_id],
            )
        payment.update({
            'receipt_status': 'GUARDADO',
            'receipt_web_url': receipt['web_url'],
            'receipt_item_id': receipt['item_id'],
            'receipt_file_name': receipt['file_name'],
            'receipt_sha256': receipt['sha256'],
        })
        _sync_confirmed_link_payment_to_complement(payment)
    except Exception as exc:
        payment['receipt_status'] = 'PENDIENTE'
        payment['receipt_sync_error'] = str(exc)


def _sync_confirmed_link_payment_to_complement(payment: dict[str, Any]) -> None:
    transaction_id = _clean_text(payment.get('provider_transaction_id'))
    codigo_estud = _clean_text(payment.get('codigo_estud'))
    corte_id = _clean_text(payment.get('corte_id'))
    if not transaction_id or not codigo_estud or not corte_id:
        return
    try:
        configure_result = configure_cut_in_complement(corte_id, usuario_registro='ALLDIGITAL')
        if not configure_result.get('synced'):
            return
        enrollment_result = sync_student_enrollment_to_complement(
            corte_id=corte_id,
            codigo_estud=codigo_estud,
            usuario_registro='ALLDIGITAL',
            registrar_cargo_inicial=True,
        )
        if not enrollment_result.get('synced'):
            return
        complement_enrollments = _continuing_education_object('edu', 'CorteEstudiante')
        accounts_table = _continuing_education_object('fin', 'CuentaEstudiante')
        movements_table = _continuing_education_object('fin', 'MovimientoCuenta')
        account_rows = _fetch_payment_rows(
            f"""
            SELECT TOP (1) CE.EstudianteCorteId, C.CuentaId
            FROM {complement_enrollments} AS CE
            INNER JOIN {accounts_table} AS C ON C.EstudianteCorteId = CE.EstudianteCorteId
            WHERE CONVERT(varchar(50), CE.CorteId) = %s
              AND CONVERT(varchar(50), CE.CodigoEstud) = %s
            """,
            [corte_id, codigo_estud],
        )
        if not account_rows:
            return
        account = account_rows[0]
        existing_rows = _fetch_payment_rows(
            f"""
            SELECT TOP (1) MovimientoId
            FROM {movements_table}
            WHERE CuentaId = %s
              AND TipoMovimiento = 'HABER'
              AND EstadoMovimiento = 'ACTIVO'
              AND (
                  LTRIM(RTRIM(ISNULL(NumeroDeposito, ''))) = %s
                  OR (%s <> '' AND LTRIM(RTRIM(ISNULL(NumeroComprobante, ''))) = %s)
              )
            """,
            [
                account.get('CuentaId'), transaction_id,
                _clean_text(payment.get('payment_record_number')),
                _clean_text(payment.get('payment_record_number')),
            ],
        )
        if existing_rows:
            payment['complement_payment_status'] = 'SINCRONIZADO'
            return
        value = _to_decimal(payment.get('registered_value'))
        if value <= 0:
            value = _to_decimal(payment.get('amount'))
        if value <= 0:
            return
        procedure = _continuing_education_object('fin', 'usp_RegistrarDepositoEstudiante')
        _fetch_payment_rows(
            f"""
            EXEC {procedure}
                @EstudianteCorteId = %s,
                @Valor = %s,
                @FormaPago = 'ALLDIGITAL',
                @Banco = N'ALL DIGITAL',
                @NumeroDeposito = %s,
                @FechaDeposito = %s,
                @NumeroComprobante = %s,
                @UrlComprobante = %s,
                @NombreArchivoComprobante = %s,
                @HashComprobante = %s,
                @UsuarioRegistro = 'ALLDIGITAL',
                @Observacion = N'Pago confirmado por enlace All Digital.'
            """,
            [
                account.get('EstudianteCorteId'), value, transaction_id,
                _clean_text(payment.get('paid_at')) or None,
                _clean_text(payment.get('payment_record_number')),
                _clean_text(payment.get('receipt_web_url')),
                _clean_text(payment.get('receipt_file_name')),
                _clean_text(payment.get('receipt_sha256')),
            ],
        )
        payment['complement_payment_status'] = 'SINCRONIZADO'
        cache.delete(f'dashboard:continuing-education-payment-metrics:v5-invoices:{complement_database_name()}')
    except Exception as exc:
        payment['complement_payment_status'] = 'PENDIENTE'
        payment['complement_payment_error'] = str(exc)


def _notify_confirmed_link_payment(payment: dict[str, Any]) -> None:
    transaction_id = _clean_text(payment.get('provider_transaction_id'))
    if not transaction_id:
        return
    student_name = _clean_text(payment.get('nombre')) or 'Estudiante'
    course_name = _clean_text(payment.get('course_name')) or 'Educación Continua'
    value = _clean_text(payment.get('registered_value')) or _clean_text(payment.get('amount'))
    data = {
        'transaction_id': transaction_id,
        'codigo_estud': _clean_text(payment.get('codigo_estud')),
        'course_name': course_name,
        'value': value,
    }
    create_notification_safely(
        event_key=f'payment-confirmed:{transaction_id}:financial',
        notification_type='PAYMENT_CONFIRMED',
        title='Pago confirmado por All Digital',
        message=f'Se confirmó el pago de {student_name} para {course_name} por USD {value}.',
        recipient_category='staff',
        recipient_role='FINANCIERO',
        route='#payments',
        data=data,
    )
    create_notification_safely(
        event_key=f'payment-confirmed:{transaction_id}:student',
        notification_type='PAYMENT_CONFIRMED',
        title='Pago confirmado',
        message=f'Tu pago para {course_name} fue confirmado correctamente.',
        recipient_category='student',
        recipient_login=_clean_text(payment.get('student_login')) or _clean_text(payment.get('email')),
        route='#dashboard',
        data=data,
    )


def _generated_payment_link_metrics() -> dict[str, Any]:
    rows = _fetch_payment_rows(
        """
        SELECT
            COUNT(*) AS generated_links,
            SUM(CASE WHEN PR.Num IS NOT NULL OR LOWER(ISNULL(JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre'), '')) IN ('pagada','pagado','aprobada','aprobado','completada','completado') THEN 1 ELSE 0 END) AS paid_links,
            SUM(CASE WHEN PR.Num IS NULL AND LOWER(ISNULL(JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre'), '')) IN ('generada','generado') THEN 1 ELSE 0 END) AS generated_pending_links,
            SUM(ISNULL(TRY_CONVERT(decimal(18,2), I.Monto), 0)) AS generated_value,
            SUM(CASE WHEN PR.Num IS NOT NULL THEN ISNULL(PR.ValorRegistrado, 0)
                     WHEN LOWER(ISNULL(JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre'), '')) IN ('pagada','pagado','aprobada','aprobado','completada','completado')
                     THEN ISNULL(TRY_CONVERT(decimal(18,2), I.Monto), 0) ELSE 0 END) AS paid_value
        FROM dbo.INSCRIPCION_SOLICITUD_PAGO AS I
        OUTER APPLY (
            SELECT TOP (1) R.Num, R.ValorRegistrado
            FROM dbo.REGISTROPAGOS AS R
            WHERE CONVERT(varchar(50), R.Codestu) = JSON_VALUE(I.ProviderResponse, '$.official_sync.record.codigo_estud')
              AND CONVERT(varchar(50), R.codperiodo) = I.CodigoPeriodo
              AND (
                  NULLIF(LTRIM(RTRIM(R.Referencia)), '') = NULLIF(LTRIM(RTRIM(I.PaymentLink)), '')
                  OR NULLIF(LTRIM(RTRIM(R.NoDeposito)), '') = JSON_VALUE(I.ProviderResponse, '$.provider.data.id')
              )
            ORDER BY COALESCE(R.FechaDeposito, R.fechapago, R.FechaRegistro) DESC, R.Num DESC
        ) AS PR
        WHERE PR.Num IS NOT NULL
           OR LOWER(LTRIM(RTRIM(ISNULL(JSON_VALUE(I.ProviderResponse, '$.provider.data.estado.nombre'), ''))))
              IN ('generada','generado','pagada','pagado','aprobada','aprobado','completada','completado')
        """,
        [],
    )
    row = rows[0] if rows else {}
    return {
        'generated_links': int(row.get('generated_links') or 0),
        'paid_links': int(row.get('paid_links') or 0),
        'generated_pending_links': int(row.get('generated_pending_links') or 0),
        'generated_value': str(row.get('generated_value') or '0.00'),
        'paid_value': str(row.get('paid_value') or '0.00'),
    }


def _ensure_continuing_education_payments_available() -> None:
    required_objects = [
        ('edu', 'VW_MatriculaEstudianteCompleta', 'V'),
        ('fin', 'CuentaEstudiante', 'U'),
        ('fin', 'MovimientoCuenta', 'U'),
        ('fin', 'FacturaMovimiento', 'U'),
    ]
    if not is_complement_available(required_objects):
        raise PaymentGatewayError(
            'La base INTECEDUCONTINUA no está disponible o no contiene el módulo financiero requerido.'
        )


def _continuing_education_object(schema: str, object_name: str) -> str:
    return f'[{complement_database_name()}].[{schema}].[{object_name}]'


def _is_excel_net_adjustment(row: dict[str, Any]) -> bool:
    return (
        _clean_text(row.get('forma_pago')).upper() == 'DESCUENTO'
        and _clean_text(row.get('detalle')).upper().startswith('AJUSTE VALOR NETO CURSO - EXCEL')
    )


def _effective_total_value(total_value: Decimal, registered_value: Decimal) -> Decimal:
    """Use a confirmed payment as total when a legacy account has no initial charge."""
    if total_value <= 0 and registered_value > 0:
        return registered_value
    return total_value


def _serialize_registered_user_payment(row: dict[str, Any]) -> dict[str, Any]:
    total_value = _to_decimal(row.get('total_value'))
    registered_value = _to_decimal(row.get('registered_value'))
    discount_value = _to_decimal(row.get('discount_value'))
    is_excel_enrollment = _safe_int(row.get('is_excel_enrollment'), default=0) == 1
    if is_excel_enrollment:
        total_value = EXCEL_ENROLLMENT_NET_AMOUNT
        discount_value = max(
            Decimal('0.00'),
            discount_value - _to_decimal(row.get('excel_net_adjustment')),
        )
    total_value = _effective_total_value(total_value, registered_value)
    pending_balance = max(Decimal('0.00'), total_value - registered_value - discount_value)
    payment_complete = total_value > 0 and pending_balance <= 0
    payment_count = int(row.get('payment_count') or 0)
    invoice_count = int(row.get('invoice_count') or 0)
    return {
        'codigo_estud': _clean_text(row.get('codigo_estud')),
        'estudiante_corte_id': _clean_text(row.get('estudiante_corte_id')),
        'corte_id': _clean_text(row.get('corte_id')),
        'cuenta_id': _clean_text(row.get('cuenta_id')),
        'nombre': _clean_text(row.get('nombre')),
        'cedula': _clean_text(row.get('cedula')),
        'email': _clean_text(row.get('email')),
        'usuario_login': _clean_text(row.get('usuario_login')),
        'course_name': _clean_text(row.get('course_name')),
        'cut_name': _clean_text(row.get('cut_name')),
        'enrollment_status': _clean_text(row.get('enrollment_status')),
        'enrollment_origin': 'EXCEL' if is_excel_enrollment else '',
        'payment_count': payment_count,
        'invoice_count': invoice_count,
        'pending_invoice_count': max(0, payment_count - invoice_count),
        'invoice_status': (
            'SIN_PAGOS' if payment_count == 0
            else 'SUBIDA' if invoice_count >= payment_count
            else 'PENDIENTE'
        ),
        'total_value': str(total_value),
        'registered_value': str(registered_value),
        'discount_value': str(discount_value),
        'pending_balance': str(pending_balance),
        'payment_status': 'PAGADO' if payment_complete else 'PENDIENTE',
        'certificate_payment_ready': payment_complete,
        'last_payment_date': _clean_text(row.get('last_payment_date')),
        'last_payment_detail': _clean_text(row.get('last_payment_detail')),
        'last_payment_reference': _clean_text(row.get('last_payment_reference')),
        'inscription_payment_id': '',
        'provider_transaction_id': '',
        'provider_status': '',
    }


def _registered_payment_metrics(
    complement_enrollments: str,
    accounts_table: str,
    movements_table: str,
    invoices_table: str,
) -> dict[str, Any]:
    cache_key = f'dashboard:continuing-education-payment-metrics:v5-invoices:{complement_database_name()}'
    cached_metrics = cache.get(cache_key)
    if isinstance(cached_metrics, dict):
        return cached_metrics

    rows = _fetch_payment_rows(
        f"""
        ;WITH AccountSummary AS (
            SELECT
                CE.CorteEstudianteIdPrincipal,
                COUNT(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) <> 'DESCUENTO' THEN 1 END) AS payment_count,
                COUNT(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) <> 'DESCUENTO' AND F.FacturaMovimientoId IS NOT NULL AND F.EstadoFactura = 'SUBIDA' THEN 1 END) AS invoice_count,
                SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'DEBE' THEN M.Valor ELSE 0 END) AS total_value,
                SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) <> 'DESCUENTO' THEN M.Valor ELSE 0 END) AS registered_value,
                SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) = 'DESCUENTO' THEN M.Valor ELSE 0 END) AS discount_value,
                SUM(CASE WHEN M.EstadoMovimiento = 'ACTIVO' AND M.TipoMovimiento = 'HABER' AND UPPER(ISNULL(M.FormaPago, '')) = 'DESCUENTO' AND UPPER(LTRIM(RTRIM(ISNULL(M.Concepto, '')))) LIKE 'AJUSTE VALOR NETO CURSO - EXCEL%%' THEN M.Valor ELSE 0 END) AS excel_net_adjustment
            FROM {complement_enrollments} AS CE
            LEFT JOIN {accounts_table} AS C ON C.EstudianteCorteId = CE.EstudianteCorteId
            LEFT JOIN {movements_table} AS M ON M.CuentaId = C.CuentaId
            LEFT JOIN {invoices_table} AS F ON F.MovimientoId = M.MovimientoId
            GROUP BY CE.CorteEstudianteIdPrincipal
        )
        SELECT
            COUNT(*) AS registered_users,
            SUM(CASE WHEN ISNULL(P.payment_count, 0) > 0 THEN 1 ELSE 0 END) AS users_with_payments,
            SUM(ISNULL(P.payment_count, 0)) AS payment_records,
            SUM(ISNULL(P.invoice_count, 0)) AS uploaded_invoices,
            SUM(CASE WHEN LOWER(LTRIM(RTRIM(ISNULL(E.Observacion, '')))) LIKE N'matrícula masiva%%' THEN {EXCEL_ENROLLMENT_NET_AMOUNT} ELSE ISNULL(P.total_value, 0) END) AS total_value,
            SUM(ISNULL(P.registered_value, 0)) AS registered_value,
            SUM(CASE WHEN LOWER(LTRIM(RTRIM(ISNULL(E.Observacion, '')))) LIKE N'matrícula masiva%%' THEN ISNULL(P.discount_value, 0) - ISNULL(P.excel_net_adjustment, 0) ELSE ISNULL(P.discount_value, 0) END) AS discount_value
        FROM {complement_enrollments} AS E
        LEFT JOIN AccountSummary AS P ON P.CorteEstudianteIdPrincipal = E.CorteEstudianteIdPrincipal
        """,
        [],
    )
    row = rows[0] if rows else {}
    metrics = {
        'registered_users': int(row.get('registered_users') or 0),
        'users_with_payments': int(row.get('users_with_payments') or 0),
        'payment_records': int(row.get('payment_records') or 0),
        'uploaded_invoices': int(row.get('uploaded_invoices') or 0),
        'pending_invoices': max(
            0,
            int(row.get('payment_records') or 0) - int(row.get('uploaded_invoices') or 0),
        ),
        'total_value': str(row.get('total_value') or '0.00'),
        'registered_value': str(row.get('registered_value') or '0.00'),
        'discount_value': str(row.get('discount_value') or '0.00'),
    }
    cache_ttl = max(0, _safe_int(os.getenv('PAYMENTS_CACHE_TTL'), default=300))
    if cache_ttl:
        cache.set(cache_key, metrics, cache_ttl)
    return metrics


def _call_payment_provider(payload: dict[str, Any]) -> dict[str, Any]:
    api_url = (os.getenv('PAYMENTS_API_URL') or '').strip()
    if not api_url:
        raise PaymentGatewayError('No se encontró PAYMENTS_API_URL en las variables de entorno.')

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


def _get_payment_provider_transaction(transaction_id: Any) -> dict[str, Any]:
    return _request_payment_provider_transaction('GET', transaction_id)


def _delete_payment_provider_transaction(transaction_id: Any, reason: Any = '') -> dict[str, Any]:
    payload = {'motivo': _trim_to_max(reason, 250)} if _clean_text(reason) else None
    return _request_payment_provider_transaction('DELETE', transaction_id, payload=payload)


def _request_payment_provider_transaction(
    method: str,
    transaction_id: Any,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_transaction_id = _clean_text(transaction_id)
    if not clean_transaction_id or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', clean_transaction_id):
        raise PaymentGatewayError('El ID de transacción de AllDigital no es válido.')
    api_url = (os.getenv('PAYMENTS_API_URL') or '').strip().rstrip('/')
    if not api_url:
        raise PaymentGatewayError('No se encontró PAYMENTS_API_URL en las variables de entorno.')
    template = (os.getenv('PAYMENTS_TRANSACTION_URL_TEMPLATE') or '{base}/{transaction_id}').strip()
    transaction_url = template.format(
        base=api_url,
        transaction_id=quote(clean_transaction_id, safe=''),
    )
    if not transaction_url.lower().startswith('https://'):
        raise PaymentGatewayError('La consulta de AllDigital debe utilizar HTTPS.')

    base_headers = {
        'Accept': 'application/json',
        'User-Agent': (os.getenv('PAYMENTS_USER_AGENT') or 'INTEC-Inscripcion-Service/1.0').strip(),
    }
    body = None
    if payload is not None:
        body = json.dumps(payload).encode('utf-8')
        base_headers['Content-Type'] = 'application/json'
    last_error: ProviderHttpError | None = None
    for headers in _build_provider_auth_headers(base_headers, _get_payments_token()):
        try:
            return _request_json(transaction_url, method=method, headers=headers, body=body)
        except ProviderHttpError as exc:
            last_error = exc
            if exc.status_code not in {401, 403}:
                raise
    if last_error:
        raise last_error
    raise PaymentGatewayError('No fue posible consultar la transacción en AllDigital.')


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


def _clean_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())


def _env_first_named(*keys: str) -> tuple[str, str]:
    for key in keys:
        value = str(os.getenv(key) or '').strip()
        if value:
            return value, key
    return '', ''


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
        return f'Pago de inscripción del curso {course_name}'

    clean_fallback = str(fallback or '').strip()
    return clean_fallback or 'Pago de inscripción'


def _compose_mass_matriculation_description(raw_payload: dict[str, Any], fallback: str) -> str:
    provider_payload = raw_payload.get('provider_payload')
    provider_payload = provider_payload if isinstance(provider_payload, dict) else {}
    source_type = str(
        _first_non_empty(provider_payload.get('tipo'), raw_payload.get('tipo'))
    ).strip()
    label = 'Matrícula académica' if source_type == 'matricula_academica_sin_cargo' else 'Matrícula masiva'

    course_name = _first_non_empty(
        provider_payload.get('nombre_materia'),
        raw_payload.get('nombre_materia'),
        provider_payload.get('curso_nombre'),
        raw_payload.get('curso_nombre'),
    )
    course_name = _remove_numbers_and_trim(course_name)

    if course_name:
        return f'{label} del curso {course_name}'

    clean_fallback = str(fallback or '').strip()
    return clean_fallback or label


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
            raise PaymentGatewayError('No fue posible confirmar el registro de inscripción en la base de datos.')
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
        raise PaymentGatewayError('No fue posible reutilizar la solicitud de inscripción existente.')


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


def _update_inscription_request_matricula(inscription_id: int, matricula: str) -> None:
    clean_matricula = str(matricula or '').strip()
    if not inscription_id or not clean_matricula:
        return

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE dbo.INSCRIPCION_SOLICITUD_PAGO
            SET Matricula = %s
            WHERE Id = %s
            """,
            [clean_matricula, inscription_id],
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
    localidad: str,
    tipo_postulante: str,
    carrera_ocupacion: str,
    actividad_profesional: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    matricula: str,
    monto: Any,
    descripcion: str,
    payment_link: str,
    create_payment_record: bool = True,
    continuing_education_charge: Any = None,
    enrollment_origin: str = '',
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
            localidad=localidad,
            tipo_postulante=tipo_postulante,
            carrera_ocupacion=carrera_ocupacion,
            actividad_profesional=actividad_profesional,
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
            existing_numcodigo = str(existing_materia.get('numcodigo') or '').strip()
            existing_num_matricula = _safe_int(existing_materia.get('Num_Matricula'), default=0)
            cut_assignment = _assign_open_cut_for_matricula(
                codigo_estud=codigo_estud,
                cod_anio_basica=cod_anio_basica,
                codigo_materia=codigo_materia,
                codigo_periodo=codigo_periodo,
                num_matricula=existing_num_matricula,
                descripcion=descripcion,
                continuing_education_charge=continuing_education_charge,
                enrollment_origin=enrollment_origin,
            )
            return {
                'codigo_estud': str(codigo_estud),
                'matricula': existing_numcodigo,
                'numcodigo': existing_numcodigo,
                'num_matricula': str(existing_num_matricula),
                'num_reg_pago': str(_safe_int(existing_materia.get('Num'), default=0)),
                'already_enrolled': '1',
                'payment_record_created': '0',
                'cod_anio_basica': str(cod_anio_basica),
                'codigo_periodo': str(codigo_periodo),
                'codigo_materia': str(codigo_materia),
                'materia': str(pensum_template.get('materia') or ''),
                **cut_assignment,
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
            numcodigo=matricula,
            num_matricula=num_matricula,
            valor=valor_decimal,
            payment_link=payment_link,
            template=cabecera_template,
        )
        cabecera_record = _get_cabecera_matricula_record(
            codigo_estud=codigo_estud,
            cod_anio_basica=cod_anio_basica,
            codigo_periodo=codigo_periodo,
            num_matricula=num_matricula,
        )
        resolved_numcodigo = str((cabecera_record or {}).get('numcodigo') or matricula).strip()

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

        cut_assignment = _assign_open_cut_for_matricula(
            codigo_estud=codigo_estud,
            cod_anio_basica=cod_anio_basica,
            codigo_materia=codigo_materia,
            codigo_periodo=codigo_periodo,
            num_matricula=num_matricula,
            descripcion=descripcion,
            continuing_education_charge=continuing_education_charge,
            enrollment_origin=enrollment_origin,
        )

        return {
            'codigo_estud': str(codigo_estud),
            'matricula': resolved_numcodigo,
            'numcodigo': resolved_numcodigo,
            'num_matricula': str(num_matricula),
            'num_reg_pago': str(num_reg_pago) if create_payment_record else '',
            'payment_record_created': '1' if create_payment_record else '0',
            'cod_anio_basica': str(cod_anio_basica),
            'codigo_periodo': str(codigo_periodo),
            'codigo_materia': str(codigo_materia),
            'materia': str(pensum_template.get('materia') or ''),
            **cut_assignment,
        }


def _assign_open_cut_for_matricula(
    *,
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_materia: str,
    codigo_periodo: str,
    num_matricula: int,
    descripcion: str,
    continuing_education_charge: Any = None,
    enrollment_origin: str = '',
) -> dict[str, str]:
    try:
        assignment = assign_matricula_to_open_cut(
            codigo_estud=str(codigo_estud),
            cod_anio_basica=str(cod_anio_basica),
            codigo_materia=str(codigo_materia),
            codigo_periodo=str(codigo_periodo),
            num_matricula=_safe_int(num_matricula, default=0),
            usuario_registro='SISTEMA',
            observacion=descripcion,
            valor_total_curso=continuing_education_charge,
            origen_matricula=enrollment_origin,
        )
    except CourseCutError as exc:
        raise PaymentGatewayError(str(exc)) from exc

    return {
        'corte_id': str(assignment.get('corte_id') or ''),
        'tipo_oferta_corte': str(assignment.get('tipo_oferta') or ''),
        'numero_corte': str(assignment.get('numero_corte') or ''),
        'nombre_corte': str(assignment.get('nombre_corte') or ''),
        'fecha_inicio': str(assignment.get('fecha_inicio') or ''),
        'fecha_inicio_iso': str(assignment.get('fecha_inicio_iso') or ''),
        'codigo_materia_corte': str(assignment.get('codigo_materia') or ''),
        'materia_corte': str(assignment.get('materia_pensum') or assignment.get('materias_label') or ''),
    }


def _ensure_open_cut_for_request(*, cod_anio_basica: str, codigo_materia: str, codigo_periodo: str) -> None:
    try:
        ensure_open_cut_for_enrollment(
            cod_anio_basica=str(cod_anio_basica),
            codigo_materia=str(codigo_materia),
            codigo_periodo=str(codigo_periodo),
        )
    except CourseCutError as exc:
        raise PaymentGatewayError(str(exc)) from exc


def _update_official_links_after_payment(
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_periodo: str,
    payment_link: str,
) -> None:
    cabecera_link = _trim_to_max(payment_link, 100)
    payment_reference = _trim_to_max(payment_link, 80)
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
                [cabecera_link, str(codigo_estud), str(cod_anio_basica), str(codigo_periodo)],
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
            [payment_reference, str(codigo_estud), str(cod_anio_basica), str(codigo_periodo)],
        )


def _resolve_or_create_datos_estud(
    cedula: str,
    nombre: str,
    email: str,
    telefono: str,
    direccion: str,
    localidad: str,
    tipo_postulante: str,
    carrera_ocupacion: str,
    actividad_profesional: str,
    correo_intec: str,
) -> str:
    referencia = _datos_estud_reference(
        tipo_postulante=tipo_postulante,
        carrera_ocupacion=carrera_ocupacion,
        actividad_profesional=actividad_profesional,
    )
    nombre_corto = _trim_to_max(nombre, 70)
    email_corto = _trim_to_max(email, 80)
    correo_intec_corto = _trim_to_max(correo_intec, 100)
    telefono_corto = _trim_to_max(telefono, 30)
    movil_corto = _trim_to_max(telefono, 15)
    direccion_corta = _trim_to_max(direccion, 150)
    with connection.cursor() as cursor:
        lock_resource = 'PASARELA_CEDULA_' + re.sub(r'\D+', '', cedula)
        cursor.execute(
            """
            DECLARE @lock_result int;
            EXEC @lock_result = sys.sp_getapplock
                @Resource = %s,
                @LockMode = 'Exclusive',
                @LockOwner = 'Transaction',
                @LockTimeout = 10000;
            SELECT @lock_result;
            """,
            [lock_resource],
        )
        lock_row = cursor.fetchone()
        if not lock_row or int(lock_row[0]) < 0:
            raise PaymentGatewayError(
                'No fue posible reservar la cédula para el registro. Intenta nuevamente.'
            )

        cursor.execute(
            """
            SELECT TOP (1) CAST(codigo_estud AS varchar(50))
            FROM dbo.DATOS_ESTUD
            WHERE TRY_CONVERT(
                      decimal(20, 0),
                      REPLACE(REPLACE(REPLACE(REPLACE(
                          LTRIM(RTRIM(ISNULL(Cedula_Est, ''))), '-', ''
                      ), ' ', ''), '.', ''), ',', '')
                  ) = TRY_CONVERT(decimal(20, 0), %s)
               OR TRY_CONVERT(decimal(20, 0), Cedula) = TRY_CONVERT(decimal(20, 0), %s)
            """,
            [cedula, cedula],
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
                    ciudad = %s,
                    telefono = %s,
                    movil = %s,
                    calle_principal = %s,
                    Ocupacion = %s,
                    empresa = %s,
                    Lugar_Trabajo = %s,
                    AreaEstudio = %s,
                    EscogioProfesion = %s,
                    referencia = %s,
                    Estado = 'D'
                WHERE CAST(codigo_estud AS varchar(50)) = %s
                """,
                [
                    nombre_corto,
                    email_corto,
                    correo_intec_corto,
                    _trim_to_max(localidad, 70),
                    telefono_corto,
                    movil_corto,
                    direccion_corta,
                    _trim_to_max(carrera_ocupacion, 50),
                    _trim_to_max(actividad_profesional, 50),
                    _trim_to_max(actividad_profesional, 100),
                    _trim_to_max(carrera_ocupacion, 40),
                    _trim_to_max(_applicant_type_label(tipo_postulante), 200),
                    referencia,
                    codigo_estud,
                ],
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
                ciudad,
                correo,
                correointec,
                telefono,
                movil,
                calle_principal,
                Ocupacion,
                empresa,
                Lugar_Trabajo,
                AreaEstudio,
                EscogioProfesion,
                referencia,
                Cedula,
                Fotos,
                Tipodoc,
                NumMigracion,
                Estado
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, 'D')
            """,
            [
                codigo_estud,
                cedula,
                nombre_corto,
                _trim_to_max(localidad, 70),
                email_corto,
                correo_intec_corto,
                telefono_corto,
                movil_corto,
                direccion_corta,
                _trim_to_max(carrera_ocupacion, 50),
                _trim_to_max(actividad_profesional, 50),
                _trim_to_max(actividad_profesional, 100),
                _trim_to_max(carrera_ocupacion, 40),
                _trim_to_max(_applicant_type_label(tipo_postulante), 200),
                referencia,
                _safe_int(cedula, default=0),
            ],
        )
        return codigo_estud


def _applicant_type_label(value: Any) -> str:
    text = _clean_text(value)
    labels = {
        'profesional_independiente': 'Profesional independiente',
        'empresa_corporativo': 'Empresa / corporativo',
    }
    return labels.get(text, text)


def _datos_estud_reference(
    *,
    tipo_postulante: str,
    carrera_ocupacion: str,
    actividad_profesional: str,
) -> str:
    parts = [
        f'Tipo de postulante: {_applicant_type_label(tipo_postulante)}' if _clean_text(tipo_postulante) else '',
        f'Carrera u ocupación: {_clean_text(carrera_ocupacion)}' if _clean_text(carrera_ocupacion) else '',
        f'Actividad profesional o empresa: {_clean_text(actividad_profesional)}'
        if _clean_text(actividad_profesional)
        else '',
    ]
    return _trim_to_max('; '.join(part for part in parts if part), 500)


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
            cm.numcodigo,
            rp.Num
        FROM dbo.CARRERAXESTUD cx
        LEFT JOIN dbo.CABECERA_MATRICULA cm
          ON CAST(cm.codigo_estud AS varchar(50)) = CAST(cx.codigo_estud AS varchar(50))
         AND CAST(cm.cod_anio_Basica AS varchar(20)) = CAST(cx.cod_anio_Basica AS varchar(20))
         AND CAST(cm.codigo_periodo AS varchar(20)) = CAST(cx.codigo_periodo AS varchar(20))
         AND CAST(cm.Num_Matricula AS varchar(20)) = CAST(cx.Num_Matricula AS varchar(20))
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
            'No se encontró en PENSUM la materia seleccionada para la carrera indicada.'
        )
    if not is_catalog_value_active(row.get('estado_materia'), default=True):
        raise PaymentGatewayError(
            'La materia seleccionada está inactiva en PENSUM y no puede matricularse.'
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
    numcodigo: str,
    num_matricula: int,
    valor: Decimal,
    payment_link: str,
    template: dict[str, Any],
) -> None:
    cabecera_link = _trim_to_max(payment_link, 100)
    with connection.cursor() as cursor:
        numcodigo_column = _resolve_numcodigo_column(cursor)
        insert_numcodigo = not _cabecera_numcodigo_is_identity(cursor, numcodigo_column)
        numcodigo_column_sql = f'                    [{numcodigo_column}],\n' if insert_numcodigo else ''
        numcodigo_value_sql = ', %s' if insert_numcodigo else ''
        try:
            # Bypass broken trigger scope on CABECERA_MATRICULA while keeping insert transactional.
            cursor.execute("EXEC sys.sp_set_session_context @key = N'SYNC_ESTADO_TRIGGER', @value = 1")
            cursor.execute(
                f"""
                INSERT INTO dbo.CABECERA_MATRICULA (
                    codigo_estud,
                    cod_anio_Basica,
                    codigo_periodo,
                    Num_Matricula,
{numcodigo_column_sql}                    fecha_pago,
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
                VALUES (%s, %s, %s, %s{numcodigo_value_sql}, GETDATE(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    codigo_estud,
                    _safe_int(cod_anio_basica, default=0),
                    _safe_int(codigo_periodo, default=0),
                    num_matricula,
                    *([_safe_int(numcodigo, default=0)] if insert_numcodigo else []),
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
                    cabecera_link,
                ],
            )
        finally:
            cursor.execute("EXEC sys.sp_set_session_context @key = N'SYNC_ESTADO_TRIGGER', @value = NULL")


def _get_cabecera_matricula_record(
    codigo_estud: str,
    cod_anio_basica: str,
    codigo_periodo: str,
    num_matricula: int,
) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        numcodigo_column = _resolve_numcodigo_column(cursor)
        cursor.execute(
            f"""
            SELECT TOP (1)
                CAST([{numcodigo_column}] AS varchar(50)) AS numcodigo,
                Num_Matricula
            FROM dbo.CABECERA_MATRICULA
            WHERE CAST(codigo_estud AS varchar(50)) = %s
              AND CAST(cod_anio_Basica AS varchar(20)) = %s
              AND CAST(codigo_periodo AS varchar(20)) = %s
              AND CAST(Num_Matricula AS varchar(20)) = %s
            ORDER BY fecha_pago DESC
            """,
            [str(codigo_estud), str(cod_anio_basica), str(codigo_periodo), str(num_matricula)],
        )
        row = cursor.fetchone()
        if row is None:
            return None
        columns = [col[0] for col in cursor.description]
        return {columns[idx]: row[idx] for idx in range(len(columns))}


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


def _cabecera_numcodigo_is_identity(cursor: Any, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT CONVERT(int, c.is_identity)
        FROM sys.columns c
        WHERE c.object_id = OBJECT_ID('dbo.CABECERA_MATRICULA')
          AND c.name = %s
        """,
        [column_name],
    )
    row = cursor.fetchone()
    return bool(row and row[0])


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


def _payment_email_matricula_label(official_record: dict[str, Any] | None, fallback: Any) -> str:
    if isinstance(official_record, dict):
        for key in ('numcodigo', 'matricula'):
            value = str(official_record.get(key) or '').strip()
            if value and value != '0':
                return value
    return str(fallback or '').strip()


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
                <img src="cid:intec-logo.png" width="230" alt="INTEC" style="display:block;width:230px;max-width:78%;height:auto;border:0;" />
              </td>
            </tr>
""".rstrip()
    receipt_message = ''
    if receipt_email:
        receipt_message = f"""
                <p style="margin:0 0 18px 0;font-size:14px;line-height:1.6;color:#374151;">
                  Luego de realizar el pago, envía el comprobante al correo
                  <a href="{safe_receipt_mailto}" style="color:#9B0E0E;text-decoration:underline;">{safe_receipt_email}</a>
                  indicando tu nombre completo y matrícula.
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
                <h2 style="margin:0;font-size:22px;font-weight:700;">Pago de inscripción</h2>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 28px;color:#111827;">
                <p style="margin:0 0 12px 0;font-size:16px;">Hola {safe_recipient_label},</p>
                <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#374151;">Hemos generado tu enlace para completar el pago de inscripción.</p>

                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 18px 0;border:1px solid #e5e7eb;border-radius:10px;">
                  <tr>
                    <td style="padding:14px 16px;font-size:14px;color:#111827;"><strong>Matrícula:</strong> {safe_matricula}</td>
                  </tr>
                  <tr>
                    <td style="padding:14px 16px;border-top:1px solid #e5e7eb;font-size:16px;color:#111827;"><strong>Valor a cancelar:</strong> $ {safe_monto}</td>
                  </tr>
                </table>

                <p style="margin:0 0 18px 0;">
                  <a href="{safe_button_href}" style="display:inline-block;background:#9B0E0E;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:8px;font-weight:600;">Pagar ahora</a>
                </p>

{receipt_message}

                <p style="margin:0 0 8px 0;font-size:13px;color:#6b7280;">Si el botón no funciona, copia y pega este enlace en tu navegador:</p>
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
            'subject': 'Enlace de pago de inscripción',
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
                <h2 style="margin:0;font-size:22px;font-weight:700;">Credenciales INTEC</h2>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 28px;color:#111827;">
                <p style="margin:0 0 12px 0;font-size:16px;">Hola {safe_recipient_label},</p>
                <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#374151;">Te damos la bienvenida al Instituto Superior Tecnológico de Técnicas Empresariales y del Conocimiento INTEC. Has sido matriculado en el curso <strong>{safe_course_name}</strong>, en el cual continuarás con tu preparación profesional para lograr tu éxito.</p>
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
        / 'Intec-Logowithslogangray.png'
    )
    try:
        content = logo_path.read_bytes()
    except OSError:
        return None

    return {
        '@odata.type': '#microsoft.graph.fileAttachment',
        'name': 'Intec-Logowithslogangray.png',
        'contentType': 'image/png',
        'contentBytes': b64encode(content).decode('ascii'),
        'isInline': True,
        'contentId': 'intec-logo.png',
    }


def _send_graph_mail(mail_payload: dict[str, Any]) -> None:
    skip_default_cc = bool(mail_payload.pop('_skip_default_cc', False))
    tenant_id, tenant_source = _env_first_named('MS_TENANT_ID', 'MICROSOFT_TENANT_ID', 'TENANT_ID')
    client_id, client_source = _env_first_named('MS_CLIENT_ID', 'MICROSOFT_CLIENT_ID', 'CLIENT_ID')
    client_secret, _secret_source = _env_first_named(
        'MS_CLIENT_SECRET',
        'MICROSOFT_CLIENT_SECRET',
        'CLIENT_SECRET',
    )
    sender_identity = _resolve_graph_sender_identity()

    if not tenant_id or not client_id or not client_secret:
        raise PaymentGatewayError(
            'No se encontraron credenciales Microsoft Graph para envio de correo. '
            'Configura MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET o '
            'MICROSOFT_TENANT_ID/MICROSOFT_CLIENT_ID/MICROSOFT_CLIENT_SECRET.'
        )

    access_token = _get_graph_access_token(tenant_id, client_id, client_secret)
    _validate_graph_mail_send_permission(access_token)

    endpoint = f'https://graph.microsoft.com/v1.0/users/{quote(sender_identity, safe="")}/sendMail'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    if not skip_default_cc:
        _ensure_graph_mail_cc_recipients(mail_payload)

    try:
        _post_json(endpoint, mail_payload, headers, expect_json=False)
    except ProviderHttpError as exc:
        if exc.status_code == 404 and 'ErrorInvalidUser' in exc.detail:
            raise PaymentGatewayError(
                'Microsoft Graph no reconoce el remitente configurado. '
                'Configura un usuario válido en MS_SENDER_USER_ID (Object ID/UPN) '
                'o MS_SENDER_EMAIL y asegure permisos Mail.Send para la aplicacion.'
            ) from exc
        if exc.status_code == 403 and 'ErrorAccessDenied' in exc.detail:
            raise PaymentGatewayError(
                'Microsoft Graph denego el envio de correo. La aplicacion debe tener '
                'el permiso Application Mail.Send con Grant admin consent, y el remitente '
                f'{sender_identity} debe ser un buzón válido con permiso para enviar. '
                f'Diagnostico seguro: tenant_source={tenant_source}, client_source={client_source}.'
            ) from exc
        raise


def _ensure_graph_mail_cc_recipients(mail_payload: dict[str, Any]) -> None:
    message = mail_payload.get('message')
    if not isinstance(message, dict):
        return

    existing_addresses = set()
    for recipient_key in ('toRecipients', 'ccRecipients', 'bccRecipients'):
        for recipient in message.get(recipient_key) or []:
            address = _graph_mail_recipient_address(recipient).lower()
            if address:
                existing_addresses.add(address)

    cc_recipients = message.get('ccRecipients')
    if not isinstance(cc_recipients, list):
        cc_recipients = []

    has_new_copy = False
    for email in _resolve_graph_mail_cc_recipients():
        clean_email = _clean_text(email)
        if not clean_email:
            continue

        normalized_email = clean_email.lower()
        if normalized_email in existing_addresses:
            continue

        cc_recipients.append({'emailAddress': {'address': clean_email}})
        existing_addresses.add(normalized_email)
        has_new_copy = True

    if has_new_copy:
        message['ccRecipients'] = cc_recipients


def _resolve_graph_mail_cc_recipients() -> list[str]:
    raw_recipients = _first_non_empty(*(os.getenv(key) for key in GRAPH_MAIL_CC_ENV_KEYS))
    if not raw_recipients:
        return []
    return [
        email
        for email in (_clean_text(part) for part in re.split(r'[;,]', raw_recipients))
        if email
    ]


def _graph_mail_recipient_address(recipient: Any) -> str:
    if not isinstance(recipient, dict):
        return ''
    email_address = recipient.get('emailAddress')
    if not isinstance(email_address, dict):
        return ''
    return _clean_text(email_address.get('address'))


def _resolve_payment_receipt_email() -> str:
    return DEFAULT_PAYMENT_RECEIPT_EMAIL


def _resolve_graph_sender_identity() -> str:
    sender_user_id = _first_non_empty(
        os.getenv('MS_SENDER_USER_ID'),
        os.getenv('MICROSOFT_SENDER_USER_ID'),
        os.getenv('GRAPH_SENDER_USER_ID'),
    )
    if sender_user_id:
        return sender_user_id

    sender_email = _first_non_empty(
        os.getenv('MS_SENDER_EMAIL'),
        os.getenv('MICROSOFT_SENDER_EMAIL'),
        os.getenv('GRAPH_SENDER_EMAIL'),
    )
    if sender_email:
        return sender_email

    raise PaymentGatewayError(
        'No hay remitente configurado para Microsoft Graph. Define MS_SENDER_USER_ID '
        '(Object ID/UPN) o MS_SENDER_EMAIL en el archivo .env. Tambien se aceptan '
        'MICROSOFT_SENDER_USER_ID/MICROSOFT_SENDER_EMAIL.'
    )


def _get_graph_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    cache_identity = sha256(f'{tenant_id}|{client_id}'.encode('utf-8')).hexdigest()
    cache_key = f'microsoft-graph:mail-token:{cache_identity}'
    cached_token = cache.get(cache_key)
    if cached_token:
        return str(cached_token)

    token_url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'
    scope = _first_non_empty(
        os.getenv('MS_GRAPH_SCOPE'),
        os.getenv('MICROSOFT_GRAPH_SCOPE'),
        'https://graph.microsoft.com/.default',
    )
    form_body = (
        f'client_id={quote(client_id, safe="")}&'
        f'client_secret={quote(client_secret, safe="")}&'
        f'scope={quote(scope, safe="")}&'
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
        raise PaymentGatewayError('Graph devolvió una respuesta inválida al solicitar token.') from exc

    token = str(payload.get('access_token') or '').strip()
    if not token:
        raise PaymentGatewayError('Graph no devolvió access_token para envío de correo.')
    expires_in = _safe_int(payload.get('expires_in'), default=3600)
    cache.set(cache_key, token, timeout=max(60, expires_in - 300))
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

        nested_keys = ['data', 'result', 'payload', 'transacción']
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


def _extract_provider_transaction_id(provider_response: Any) -> str:
    if not isinstance(provider_response, dict):
        return ''
    for key in ('transacción_id', 'transaccion_id', 'transaction_id'):
        value = _clean_text(provider_response.get(key))
        if value:
            return value
    data = provider_response.get('data')
    if isinstance(data, dict):
        value = _clean_text(data.get('id'))
        if value:
            return value
    for key in ('result', 'payload', 'transacción'):
        value = _extract_provider_transaction_id(provider_response.get(key))
        if value:
            return value
    return ''


def _extract_provider_status(provider_response: Any) -> str:
    if not isinstance(provider_response, dict):
        return ''
    state = provider_response.get('estado')
    if isinstance(state, dict):
        value = _clean_text(state.get('nombre') or state.get('name'))
    else:
        value = _clean_text(state)
    if value:
        return value.upper()
    for key in ('data', 'result', 'payload', 'transacción'):
        value = _extract_provider_status(provider_response.get(key))
        if value:
            return value
    return _clean_text(provider_response.get('status')).upper()


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], expect_json: bool = True) -> dict[str, Any]:
    body = json.dumps(payload).encode('utf-8')
    return _request_json(url, method='POST', headers=headers, body=body, expect_json=expect_json)


def _request_json(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes | None = None,
    expect_json: bool = True,
) -> dict[str, Any]:
    request = Request(url, data=body, headers=headers, method=method)

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
        raise PaymentGatewayError('El proveedor devolvió una respuesta no JSON.') from exc

    if isinstance(parsed, dict):
        return parsed
    return {'data': parsed}
