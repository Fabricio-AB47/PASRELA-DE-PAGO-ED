from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from base64 import urlsafe_b64decode
from hashlib import sha256
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.core.cache import cache
from django.db import connection
from django.utils import timezone


DEFAULT_GRAPH_SCOPE = 'https://graph.microsoft.com/.default'
DEFAULT_GRAPH_BASE_URL = 'https://graph.microsoft.com/v1.0'
DEFAULT_DOMAIN = 'intec.edu.ec'
DEFAULT_USAGE_LOCATION = 'EC'
DEFAULT_STUDENT_LICENSE_KEYWORD = 'STUDENT'
DEFAULT_STUDENT_LICENSE_DISPLAY_NAME = 'Office 365 A1 para estudiantes'
DEFAULT_STUDENT_LICENSE_SKU_PART_NUMBER = 'STANDARDWOFFPACK_STUDENT'
DEFAULT_TEACHER_LICENSE_KEYWORD = 'FACULTY'
DEFAULT_TEACHER_LICENSE_DISPLAY_NAME = 'Office 365 A1 para profesores'
DEFAULT_TEACHER_LICENSE_SKU_PART_NUMBER = 'STANDARDWOFFPACK_FACULTY'
EXCLUDED_STAFF_LICENSE_KEYWORDS = ('FACULTY', 'TEACHER', 'PROFESSOR', 'PROFESOR')
TEACHER_LICENSE_KEYWORDS = ('FACULTY', 'TEACHER', 'PROFESSOR', 'PROFESOR')
READ_LICENSE_ROLES = {'Directory.Read.All', 'LicenseAssignment.Read.All', 'LicenseAssignment.ReadWrite.All'}
CREATE_USER_ROLES = {'User.ReadWrite.All', 'Directory.ReadWrite.All'}
ASSIGN_LICENSE_ROLES = {'LicenseAssignment.ReadWrite.All'}
VERIFY_USER_ROLES = {'User.Read.All', 'User.ReadWrite.All', 'Directory.Read.All', 'Directory.ReadWrite.All'}
GRAPH_RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)
DEFAULT_ALIAS_MAX_ATTEMPTS = 100


class Microsoft365Error(Exception):
    status_code = 400


class Microsoft365ValidationError(Microsoft365Error):
    status_code = 400


class Microsoft365ConfigError(Microsoft365Error):
    status_code = 500


class Microsoft365GraphError(Microsoft365Error):
    status_code = 502

    def __init__(self, message: str, graph_status_code: int | None = None):
        super().__init__(message)
        self.graph_status_code = graph_status_code


def upload_continuing_education_voucher(
    content: bytes,
    *,
    course_name: str,
    cut_name: str,
    student_name: str,
    student_code: str,
    file_name: str,
) -> dict[str, str]:
    """Store a payment receipt in the configured institutional OneDrive."""
    if not content:
        raise Microsoft365ValidationError('El comprobante está vacío.')
    config = _graph_config()
    token = _get_access_token(config)
    owner = _env_first(
        'ONEDRIVE_USER_ID',
        'MS_SENDER_USER_ID',
        'MS_SENDER_EMAIL',
        'MICROSOFT_SENDER_USER_ID',
    )
    if not owner:
        raise Microsoft365ConfigError(
            'Configura ONEDRIVE_USER_ID con el correo propietario de la carpeta EDUCACION_CONTINUA.'
        )

    root_folder = _safe_onedrive_name(os.getenv('ONEDRIVE_EDUCATION_ROOT_FOLDER') or 'EDUCACION_CONTINUA')
    student_folder = _safe_onedrive_name(f'{student_code} - {student_name}')
    folders = [
        root_folder,
        _safe_onedrive_name(course_name or 'CURSO_SIN_NOMBRE'),
        _safe_onedrive_name(cut_name or 'CORTE_SIN_NOMBRE'),
        student_folder,
    ]
    _ensure_onedrive_folder_path(config, token, owner, folders)

    safe_file_name = _safe_onedrive_name(file_name or f'comprobante_{student_code}', max_length=140)
    encoded_owner = quote(owner, safe='')
    encoded_path = '/'.join(quote(part, safe='') for part in [*folders, safe_file_name])
    endpoint = f"{config['base_url']}/users/{encoded_owner}/drive/root:/{encoded_path}:/content"
    item = _graph_binary_request('PUT', endpoint, token, content, operation='cargar comprobante en OneDrive')
    return {
        'item_id': str(item.get('id') or ''),
        'file_name': str(item.get('name') or safe_file_name),
        'relative_path': '/'.join([*folders, safe_file_name]),
        'web_url': str(item.get('webUrl') or ''),
    }


def _safe_onedrive_name(value: str, *, max_length: int = 90) -> str:
    clean = re.sub(r'[\\/:*?"<>|#%]', '-', str(value or '').strip())
    clean = re.sub(r'\s+', ' ', clean).strip(' .')
    return (clean or 'SIN_NOMBRE')[:max_length].rstrip(' .')


def _ensure_onedrive_folder_path(
    config: dict[str, str], token: str, owner: str, folders: list[str],
) -> None:
    encoded_owner = quote(owner, safe='')
    parent = _graph_request(
        'GET', f"{config['base_url']}/users/{encoded_owner}/drive/root", token,
        operation='consultar raíz de OneDrive',
    )
    accumulated: list[str] = []
    for folder_name in folders:
        accumulated.append(folder_name)
        encoded_path = '/'.join(quote(part, safe='') for part in accumulated)
        endpoint = f"{config['base_url']}/users/{encoded_owner}/drive/root:/{encoded_path}"
        try:
            parent = _graph_request('GET', endpoint, token, operation=f'consultar carpeta {folder_name}')
        except Microsoft365GraphError as exc:
            if exc.graph_status_code != 404:
                raise
            parent_id = quote(str(parent.get('id') or ''), safe='')
            try:
                parent = _graph_request(
                    'POST',
                    f"{config['base_url']}/users/{encoded_owner}/drive/items/{parent_id}/children",
                    token,
                    body={
                        'name': folder_name,
                        'folder': {},
                        '@microsoft.graph.conflictBehavior': 'fail',
                    },
                    operation=f'crear carpeta {folder_name}',
                )
            except Microsoft365GraphError as create_exc:
                if create_exc.graph_status_code != 409:
                    raise
                parent = _graph_request('GET', endpoint, token, operation=f'consultar carpeta {folder_name}')


def _graph_binary_request(
    method: str, url: str, token: str, content: bytes, *, operation: str,
) -> dict[str, Any]:
    request = Request(
        url,
        data=content,
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
            'Content-Type': 'application/octet-stream',
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read().decode('utf-8')
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='ignore')
        raise Microsoft365GraphError(
            f'Microsoft Graph rechazó la operación "{operation}" ({exc.code}): '
            f'{_graph_error_message(detail) or exc.reason}',
            graph_status_code=exc.code,
        ) from exc
    except URLError as exc:
        raise Microsoft365GraphError(f'No fue posible conectar con Microsoft Graph: {exc.reason}') from exc
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise Microsoft365GraphError('Microsoft Graph devolvió una respuesta inválida al cargar el archivo.') from exc


def create_microsoft365_user(payload: dict[str, Any]) -> dict[str, Any]:
    return _create_microsoft365_user_with_license(payload, license_kind='student')


def create_microsoft365_teacher_user(payload: dict[str, Any]) -> dict[str, Any]:
    return _create_microsoft365_user_with_license(payload, license_kind='teacher')


def _create_microsoft365_user_with_license(
    payload: dict[str, Any],
    *,
    license_kind: str,
) -> dict[str, Any]:
    profile: dict[str, str] | None = None
    selected_license: dict[str, Any] | None = None
    license_label = _license_display_name(license_kind)

    try:
        profile = build_intec_account_identity(
            nombre=_first_non_empty(
                payload.get('nombre_completo'),
                payload.get('displayName'),
                payload.get('nombre'),
            ),
            cedula=payload.get('cedula'),
            correo=_first_non_empty(
                payload.get('correo'),
                payload.get('correointec'),
                payload.get('userPrincipalName'),
            ),
            nombres=payload.get('nombres'),
            apellidos=payload.get('apellidos'),
        )
        config = _graph_config()
        token = _get_access_token(config)
        _validate_graph_token_roles(token, config=config)
        subscribed_skus = _fetch_subscribed_skus(config, token)
        selected_license = _resolve_license(subscribed_skus, license_kind=license_kind)
        _validate_requested_sku(payload, selected_license['skuId'], license_label)
        profile = _resolve_available_user_profile(config, token, profile)

        user_payload = {
            'accountEnabled': True,
            'displayName': profile['display_name'],
            'givenName': profile['nombres'],
            'surname': profile['apellidos'],
            'mailNickname': profile['alias'],
            'userPrincipalName': profile['correo'],
            'usageLocation': config['usage_location'],
            'passwordProfile': _build_password_profile(profile),
        }

        created_user, created_new_user = _create_or_prepare_active_user(
            config=config,
            token=token,
            profile=profile,
            user_payload=user_payload,
        )
        user_identifier = _graph_user_identifier(created_user, profile['correo'])
        current_user = _fetch_user_for_license(config, token, user_identifier)
        if not _user_has_license(current_user, selected_license['skuId']):
            _assign_microsoft365_license(
                config,
                token,
                user_identifier,
                selected_license['skuId'],
                license_label,
            )
        verified_user = _fetch_user_for_license(config, token, user_identifier)

        _record_microsoft365_audit(
            correo=profile['correo'],
            accion=f'crear_usuario_microsoft365_{license_kind}',
            estado='ok',
            sku_id=selected_license['skuId'],
            mensaje_error='',
        )

        return {
            'correo': profile['correo'],
            'displayName': profile['display_name'],
            'givenName': profile['nombres'],
            'surname': profile['apellidos'],
            'mailNickname': profile['alias'],
            'usageLocation': config['usage_location'],
            'license': _license_summary(selected_license),
            'licenseKind': license_kind,
            'accountCreated': created_new_user,
            'emailCollisionResolved': bool(profile.get('alias_suffix')),
            'baseCorreo': profile.get('correo_base') or profile['correo'],
            'createdUser': _safe_user_payload(created_user),
            'verifiedUser': _safe_user_payload(verified_user),
        }
    except Microsoft365Error as exc:
        _record_microsoft365_audit(
            correo=(profile or {}).get('correo') or str(payload.get('correo') or payload.get('email') or '').strip(),
            accion=f'crear_usuario_microsoft365_{license_kind}',
            estado='error',
            sku_id=(selected_license or {}).get('skuId') or '',
            mensaje_error=str(exc),
        )
        raise


def get_student_license_summary() -> dict[str, Any]:
    config = _graph_config()
    token = _get_access_token(config)
    _validate_graph_token_roles(token, require_create=False, config=config)
    subscribed_skus = _fetch_subscribed_skus(config, token)
    student_license = _resolve_student_license(subscribed_skus)
    summary = _license_summary(student_license)
    summary['mensaje'] = 'Esta es la licencia que se usara para estudiantes.'
    return summary


def get_teacher_license_summary() -> dict[str, Any]:
    config = _graph_config()
    token = _get_access_token(config)
    _validate_graph_token_roles(token, require_create=False, config=config)
    subscribed_skus = _fetch_subscribed_skus(config, token)
    teacher_license = _resolve_teacher_license(subscribed_skus)
    summary = _license_summary(teacher_license)
    summary['mensaje'] = 'Esta es la licencia que se usara para profesores.'
    return summary


def build_intec_account_identity(
    nombre: Any,
    cedula: Any,
    correo: Any = None,
    nombres: Any = None,
    apellidos: Any = None,
    domain: str | None = None,
) -> dict[str, str]:
    configured_domain = _clean_domain(domain or os.getenv('MICROSOFT_DEFAULT_DOMAIN') or DEFAULT_DOMAIN)
    name_parts = _resolve_name_parts(nombre=nombre, nombres=nombres, apellidos=apellidos)
    cedula_digits = re.sub(r'\D+', '', str(cedula or ''))
    if len(cedula_digits) < 4:
        raise Microsoft365ValidationError('La cédula debe tener al menos 4 dígitos para generar la contraseña temporal.')

    alias = _email_alias(name_parts['primer_nombre'], name_parts['primer_apellido'])
    generated_email = f'{alias}@{configured_domain}'
    requested_email = str(correo or generated_email).strip().lower()
    _validate_domain_email(requested_email, configured_domain)

    first_initial = _normalize_ascii(name_parts['primer_nombre'])[:1].upper()
    password_surname = _password_component(name_parts['primer_apellido'])
    password = f'{first_initial}{password_surname}{cedula_digits[-4:]}@{timezone.localdate().year}'

    return {
        'display_name': name_parts['display_name'],
        'nombres': name_parts['nombres'],
        'apellidos': name_parts['apellidos'],
        'primer_nombre': name_parts['primer_nombre'],
        'primer_apellido': name_parts['primer_apellido'],
        'alias': alias,
        'correo': requested_email,
        'password_temporal': password,
    }


def _resolve_name_parts(nombre: Any, nombres: Any = None, apellidos: Any = None) -> dict[str, str]:
    clean_nombres = _clean_spaces(nombres)
    clean_apellidos = _clean_spaces(apellidos)

    if clean_nombres and clean_apellidos:
        primer_nombre = clean_nombres.split()[0]
        primer_apellido = clean_apellidos.split()[0]
        display_name = f'{clean_nombres} {clean_apellidos}'
        return {
            'display_name': display_name,
            'nombres': clean_nombres,
            'apellidos': clean_apellidos,
            'primer_nombre': primer_nombre,
            'primer_apellido': primer_apellido,
        }

    clean_nombre = _clean_spaces(nombre)
    tokens = clean_nombre.split()
    if len(tokens) < 2:
        raise Microsoft365ValidationError(
            'Debes enviar nombre completo con al menos nombre y primer apellido.'
        )

    primer_nombre = tokens[0]
    if len(tokens) >= 4:
        clean_nombres = ' '.join(tokens[:2])
        clean_apellidos = ' '.join(tokens[2:])
        primer_apellido = tokens[2]
    else:
        clean_nombres = primer_nombre
        clean_apellidos = ' '.join(tokens[1:])
        primer_apellido = tokens[1]

    return {
        'display_name': clean_nombre,
        'nombres': clean_nombres,
        'apellidos': clean_apellidos,
        'primer_nombre': primer_nombre,
        'primer_apellido': primer_apellido,
    }


def _graph_config() -> dict[str, str]:
    tenant_id, tenant_key = _env_first_named('MICROSOFT_TENANT_ID', 'TENANT_ID', 'MS_TENANT_ID')
    client_id, client_key = _env_first_named('MICROSOFT_CLIENT_ID', 'CLIENT_ID', 'MS_CLIENT_ID')
    client_secret, _secret_key = _env_first_named(
        'MICROSOFT_CLIENT_SECRET',
        'CLIENT_SECRET',
        'MS_CLIENT_SECRET',
    )
    if not tenant_id or not client_id or not client_secret:
        raise Microsoft365ConfigError(
            'Faltan credenciales Microsoft Graph en variables de entorno. '
            'Configura MICROSOFT_TENANT_ID/MICROSOFT_CLIENT_ID/MICROSOFT_CLIENT_SECRET '
            'o reutiliza TENANT_ID/CLIENT_ID/CLIENT_SECRET. MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET '
            'queda solo como respaldo.'
        )

    return {
        'tenant_id': tenant_id,
        'client_id': client_id,
        'client_secret': client_secret,
        'tenant_source': tenant_key,
        'client_id_source': client_key,
        'scope': str(os.getenv('MICROSOFT_GRAPH_SCOPE') or DEFAULT_GRAPH_SCOPE).strip(),
        'base_url': str(os.getenv('MICROSOFT_GRAPH_BASE_URL') or DEFAULT_GRAPH_BASE_URL).strip().rstrip('/'),
        'domain': _clean_domain(os.getenv('MICROSOFT_DEFAULT_DOMAIN') or DEFAULT_DOMAIN),
        'usage_location': str(os.getenv('MICROSOFT_DEFAULT_USAGE_LOCATION') or DEFAULT_USAGE_LOCATION).strip().upper(),
        'student_license_sku_id': str(os.getenv('MICROSOFT_STUDENT_LICENSE_SKU_ID') or '').strip(),
        'student_license_keyword': str(
            os.getenv('MICROSOFT_STUDENT_LICENSE_KEYWORD') or DEFAULT_STUDENT_LICENSE_KEYWORD
        ).strip().upper(),
    }


def _get_access_token(config: dict[str, str]) -> str:
    cache_identity = sha256(
        f"{config['tenant_id']}|{config['client_id']}|{config['scope']}".encode('utf-8')
    ).hexdigest()
    cache_key = f'microsoft-graph:directory-token:{cache_identity}'
    cached_token = cache.get(cache_key)
    if cached_token:
        return str(cached_token)

    token_url = f"https://login.microsoftonline.com/{quote(config['tenant_id'], safe='')}/oauth2/v2.0/token"
    form_body = urlencode(
        {
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'scope': config['scope'],
            'grant_type': 'client_credentials',
        }
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
        raise Microsoft365GraphError(
            f'No fue posible autenticar con Microsoft Graph ({exc.code}): {_graph_error_message(detail) or exc.reason}'
        ) from exc
    except URLError as exc:
        raise Microsoft365GraphError(f'No fue posible conectar con Microsoft Graph: {exc.reason}') from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Microsoft365GraphError('Graph devolvió una respuesta inválida al solicitar token.') from exc

    token = str(payload.get('access_token') or '').strip()
    if not token:
        raise Microsoft365GraphError('Graph no devolvió access_token.')
    try:
        expires_in = int(payload.get('expires_in') or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    cache.set(cache_key, token, timeout=max(60, expires_in - 300))
    return token


def _validate_graph_token_roles(
    token: str,
    require_create: bool = True,
    config: dict[str, str] | None = None,
) -> None:
    roles = _token_roles(token)
    missing_groups: list[str] = []

    if not roles.intersection(READ_LICENSE_ROLES):
        missing_groups.append('Directory.Read.All o LicenseAssignment.Read.All')
    if require_create and not roles.intersection(CREATE_USER_ROLES):
        missing_groups.append('User.ReadWrite.All o Directory.ReadWrite.All')
    if require_create and not roles.intersection(ASSIGN_LICENSE_ROLES):
        missing_groups.append('LicenseAssignment.ReadWrite.All')
    if require_create and not roles.intersection(VERIFY_USER_ROLES):
        missing_groups.append('User.Read.All, User.ReadWrite.All o Directory.Read.All')

    if missing_groups:
        diagnostic = _token_permission_diagnostic(roles, config)
        raise Microsoft365ConfigError(
            'El token Microsoft Graph no contiene permisos de aplicacion suficientes. '
            'En Azure Portal revisa que los permisos esten en tipo Application, no Delegated, '
            'y ejecuta Grant admin consent. Faltan: '
            f'{", ".join(missing_groups)}. {diagnostic}'
        )


def _token_roles(token: str) -> set[str]:
    parts = str(token or '').split('.')
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


def _token_permission_diagnostic(roles: set[str], config: dict[str, str] | None) -> str:
    if config:
        source = config.get('client_id_source') or 'desconocida'
        suffix = str(config.get('client_id') or '')[-8:] or 'desconocido'
        prefix = f'Diagnostico seguro: variable={source}, client_id_suffix={suffix}'
    else:
        prefix = 'Diagnostico seguro: client_id_suffix=desconocido'

    if roles:
        return f"{prefix}, roles_token={', '.join(sorted(roles))}."
    return f'{prefix}, roles_token=sin roles de aplicacion.'


def _fetch_subscribed_skus(config: dict[str, str], token: str) -> list[dict[str, Any]]:
    payload = _graph_request(
        'GET',
        f"{config['base_url']}/subscribedSkus",
        token,
        operation='consultar licencias Microsoft 365 en /subscribedSkus',
    )
    values = payload.get('value') if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise Microsoft365GraphError('Graph no devolvió la lista de subscribedSkus.')
    return values


def _resolve_student_license(subscribed_skus: list[dict[str, Any]]) -> dict[str, Any]:
    return _resolve_license(subscribed_skus, license_kind='student')


def _resolve_teacher_license(subscribed_skus: list[dict[str, Any]]) -> dict[str, Any]:
    return _resolve_license(subscribed_skus, license_kind='teacher')


def _resolve_license(subscribed_skus: list[dict[str, Any]], *, license_kind: str) -> dict[str, Any]:
    env_prefix = _license_env_prefix(license_kind)
    display_name = _license_display_name(license_kind)
    configured_sku_id = str(os.getenv(f'{env_prefix}_SKU_ID') or '').strip()
    if configured_sku_id:
        selected = next(
            (
                sku
                for sku in subscribed_skus
                if str(sku.get('skuId') or '').strip().lower() == configured_sku_id.lower()
            ),
            None,
        )
        if not selected:
            raise Microsoft365ConfigError(
                f'{env_prefix}_SKU_ID no existe en subscribedSkus.'
            )
        if license_kind == 'student' and _is_staff_license(selected):
            raise Microsoft365ConfigError(
                f'{env_prefix}_SKU_ID corresponde a una licencia de profesores y no se puede usar para estudiantes.'
            )
        if license_kind == 'teacher' and not _is_teacher_license(selected):
            raise Microsoft365ConfigError(
                f'{env_prefix}_SKU_ID no parece corresponder a Office 365 A1 para profesores.'
            )
        return selected

    keyword = str(os.getenv(f'{env_prefix}_KEYWORD') or _default_license_keyword(license_kind)).strip().upper()
    matches = [
        sku
        for sku in subscribed_skus
        if keyword in str(sku.get('skuPartNumber') or '').upper()
        and (license_kind != 'student' or not _is_staff_license(sku))
        and (license_kind != 'teacher' or _is_teacher_license(sku))
    ]

    if not matches:
        raise Microsoft365ConfigError(f'No se encontró la licencia {display_name}')

    preferred_part_number = str(
        os.getenv(f'{env_prefix}_SKU_PART_NUMBER') or _default_license_part_number(license_kind)
    ).strip().upper()
    if preferred_part_number:
        exact_matches = [
            sku
            for sku in matches
            if str(sku.get('skuPartNumber') or '').strip().upper() == preferred_part_number
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

    if len(matches) > 1:
        raise Microsoft365ConfigError(
            f'Se encontraron varias licencias para {display_name}. Configura {env_prefix}_SKU_ID '
            f'o {env_prefix}_SKU_PART_NUMBER manualmente.'
        )

    return matches[0]


def _validate_requested_sku(payload: dict[str, Any], expected_sku_id: str, license_label: str) -> None:
    requested_sku = _first_non_empty(
        payload.get('skuId'),
        payload.get('licenseSkuId'),
        payload.get('assignedLicenseSkuId'),
    )
    if requested_sku and str(requested_sku).strip().lower() != str(expected_sku_id).strip().lower():
        raise Microsoft365ValidationError(
            f'No se permite asignar una licencia distinta a {license_label}.'
        )


def _create_or_prepare_active_user(
    config: dict[str, str],
    token: str,
    profile: dict[str, str],
    user_payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    try:
        created_user = _graph_request(
            'POST',
            f"{config['base_url']}/users",
            token,
            body=user_payload,
            operation='crear usuario Microsoft 365 en /users',
        )
        return created_user, True
    except Microsoft365GraphError as exc:
        if _is_existing_user_error(exc):
            raise Microsoft365GraphError(
                'El correo institucional ya existe en Microsoft 365. '
                'Vuelve a intentar para generar una variante numerada disponible.',
                graph_status_code=exc.graph_status_code,
            ) from exc
        raise


def _resolve_available_user_profile(
    config: dict[str, str],
    token: str,
    profile: dict[str, str],
) -> dict[str, str]:
    base_alias = _email_local_part(profile.get('correo')) or profile['alias']
    domain = _email_domain(profile.get('correo')) or config['domain']
    max_attempts = _alias_max_attempts()

    for index in range(max_attempts):
        suffix = '' if index == 0 else str(index)
        candidate_alias = f'{base_alias}{suffix}'
        candidate_email = f'{candidate_alias}@{domain}'.lower()
        if not _graph_user_exists(config, token, candidate_email):
            resolved = dict(profile)
            resolved['alias'] = candidate_alias
            resolved['correo'] = candidate_email
            resolved['correo_base'] = str(profile.get('correo') or candidate_email).lower()
            resolved['alias_suffix'] = suffix
            return resolved

    raise Microsoft365ValidationError(
        'No fue posible generar un correo INTEC disponible. '
        f'Se probaron {max_attempts} variantes para {base_alias}@{domain}.'
    )


def _graph_user_exists(config: dict[str, str], token: str, user_identifier: str) -> bool:
    try:
        _graph_request(
            'GET',
            f"{config['base_url']}/users/{quote(user_identifier, safe='')}?$select=id,userPrincipalName",
            token,
            operation='verificar correo Microsoft 365 existente',
        )
        return True
    except Microsoft365GraphError as exc:
        if exc.graph_status_code == 404:
            return False
        raise


def _build_password_profile(profile: dict[str, str]) -> dict[str, Any]:
    return {
        'forceChangePasswordNextSignIn': False,
        'password': profile['password_temporal'],
    }


def _is_existing_user_error(exc: Microsoft365GraphError) -> bool:
    message = str(exc).lower()
    return any(
        fragment in message
        for fragment in (
            'already exists',
            'objectconflict',
            'same value for property userprincipalname',
            'same value for property mailnickname',
            'another object with the same value',
            'ya existe',
        )
    )


def _graph_user_identifier(user_payload: dict[str, Any], fallback_upn: str) -> str:
    user_id = str(user_payload.get('id') or '').strip() if isinstance(user_payload, dict) else ''
    return user_id or str(fallback_upn or '').strip()


def _fetch_user_for_license(config: dict[str, str], token: str, user_identifier: str) -> dict[str, Any]:
    return _graph_request_with_retry(
        'GET',
        (
            f"{config['base_url']}/users/{quote(user_identifier, safe='')}"
            '?$select=id,displayName,givenName,surname,mailNickname,'
            'userPrincipalName,assignedLicenses,usageLocation,accountEnabled'
        ),
        token,
        operation='consultar usuario Microsoft 365 creado',
        retry_statuses={404},
    )


def _user_has_license(user_payload: dict[str, Any], sku_id: str) -> bool:
    assigned = user_payload.get('assignedLicenses') if isinstance(user_payload, dict) else None
    if not isinstance(assigned, list):
        return False

    expected = str(sku_id or '').strip().lower()
    return any(str(row.get('skuId') or '').strip().lower() == expected for row in assigned if isinstance(row, dict))


def _assign_microsoft365_license(
    config: dict[str, str],
    token: str,
    user_identifier: str,
    sku_id: str,
    license_label: str,
) -> None:
    _graph_request_with_retry(
        'POST',
        f"{config['base_url']}/users/{quote(user_identifier, safe='')}/assignLicense",
        token,
        body={
            'addLicenses': [
                {
                    'skuId': sku_id,
                }
            ],
            'removeLicenses': [],
        },
        operation=f'asignar licencia {license_label}',
        retry_statuses={404},
    )


def _graph_request_with_retry(
    method: str,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
    operation: str = 'ejecutar solicitud Microsoft Graph',
    retry_statuses: set[int] | None = None,
) -> dict[str, Any]:
    retry_statuses = retry_statuses or set()
    delays = [0.0, *GRAPH_RETRY_DELAYS_SECONDS]
    last_error: Microsoft365GraphError | None = None

    for attempt_index, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            return _graph_request(
                method,
                url,
                token,
                body=body,
                operation=operation,
            )
        except Microsoft365GraphError as exc:
            last_error = exc
            if exc.graph_status_code not in retry_statuses or attempt_index == len(delays) - 1:
                raise

    if last_error:
        raise last_error
    raise Microsoft365GraphError(f'No fue posible completar la operacion "{operation}".')


def _graph_request(
    method: str,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
    operation: str = 'ejecutar solicitud Microsoft Graph',
) -> dict[str, Any]:
    data = None
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
    }
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode('utf-8')
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='ignore')
        raise Microsoft365GraphError(
            f'Microsoft Graph rechazo la operacion "{operation}" ({exc.code}): '
            f'{_graph_error_message(detail) or exc.reason}',
            graph_status_code=exc.code,
        ) from exc
    except URLError as exc:
        raise Microsoft365GraphError(f'No fue posible conectar con Microsoft Graph: {exc.reason}') from exc

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Microsoft365GraphError('Microsoft Graph devolvió una respuesta JSON inválida.') from exc


def _license_summary(license_row: dict[str, Any]) -> dict[str, Any]:
    prepaid_units = license_row.get('prepaidUnits') if isinstance(license_row, dict) else {}
    consumed_units = license_row.get('consumedUnits')
    enabled_units = None
    available_units = None
    if isinstance(prepaid_units, dict):
        enabled_units = prepaid_units.get('enabled')
    if isinstance(enabled_units, int) and isinstance(consumed_units, int):
        available_units = max(0, enabled_units - consumed_units)

    return {
        'skuId': str(license_row.get('skuId') or ''),
        'skuPartNumber': str(license_row.get('skuPartNumber') or ''),
        'consumedUnits': consumed_units,
        'prepaidUnits': prepaid_units,
        'licenciasDisponibles': available_units,
    }


def _safe_user_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    allowed = {
        'id',
        'displayName',
        'userPrincipalName',
        'assignedLicenses',
        'usageLocation',
        'givenName',
        'surname',
        'mailNickname',
        'accountEnabled',
    }
    return {key: value for key, value in payload.items() if key in allowed}


def _record_microsoft365_audit(
    correo: str,
    accion: str,
    estado: str,
    sku_id: str,
    mensaje_error: str,
) -> None:
    try:
        _ensure_microsoft365_audit_table()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO dbo.Microsoft365Audit (
                    correo,
                    accion,
                    estado,
                    skuIdAsignado,
                    mensaje_error
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    str(correo or '')[:320],
                    str(accion or '')[:100],
                    str(estado or '')[:50],
                    str(sku_id or '')[:100] or None,
                    str(mensaje_error or '')[:1000] or None,
                ],
            )
    except Exception:
        return


def _ensure_microsoft365_audit_table() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            IF OBJECT_ID('dbo.Microsoft365Audit', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.Microsoft365Audit (
                    Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                    correo NVARCHAR(320) NOT NULL,
                    fecha DATETIME2(0) NOT NULL CONSTRAINT DF_Microsoft365Audit_fecha DEFAULT SYSUTCDATETIME(),
                    accion NVARCHAR(100) NOT NULL,
                    estado NVARCHAR(50) NOT NULL,
                    skuIdAsignado NVARCHAR(100) NULL,
                    mensaje_error NVARCHAR(1000) NULL
                )
            END
            """
        )


def _is_staff_license(sku: dict[str, Any]) -> bool:
    part_number = str(sku.get('skuPartNumber') or '').upper()
    return any(keyword in part_number for keyword in EXCLUDED_STAFF_LICENSE_KEYWORDS)


def _is_teacher_license(sku: dict[str, Any]) -> bool:
    part_number = str(sku.get('skuPartNumber') or '').upper()
    return any(keyword in part_number for keyword in TEACHER_LICENSE_KEYWORDS)


def _license_env_prefix(license_kind: str) -> str:
    if license_kind == 'teacher':
        return 'MICROSOFT_TEACHER_LICENSE'
    return 'MICROSOFT_STUDENT_LICENSE'


def _license_display_name(license_kind: str) -> str:
    if license_kind == 'teacher':
        return str(
            os.getenv('MICROSOFT_TEACHER_LICENSE_DISPLAY_NAME') or DEFAULT_TEACHER_LICENSE_DISPLAY_NAME
        ).strip()
    return str(
        os.getenv('MICROSOFT_STUDENT_LICENSE_DISPLAY_NAME') or DEFAULT_STUDENT_LICENSE_DISPLAY_NAME
    ).strip()


def _default_license_keyword(license_kind: str) -> str:
    if license_kind == 'teacher':
        return DEFAULT_TEACHER_LICENSE_KEYWORD
    return DEFAULT_STUDENT_LICENSE_KEYWORD


def _default_license_part_number(license_kind: str) -> str:
    if license_kind == 'teacher':
        return DEFAULT_TEACHER_LICENSE_SKU_PART_NUMBER
    return DEFAULT_STUDENT_LICENSE_SKU_PART_NUMBER


def _validate_domain_email(email: str, domain: str) -> None:
    if not email or '@' not in email:
        raise Microsoft365ValidationError('El correo institucional es inválido.')
    if not email.lower().endswith(f'@{domain.lower()}'):
        raise Microsoft365ValidationError(f'El correo debe pertenecer al dominio @{domain}.')


def _email_alias(first_name: str, first_surname: str) -> str:
    first = _email_component(first_name)
    surname = _email_component(first_surname)
    if not first or not surname:
        raise Microsoft365ValidationError('No fue posible generar el alias del correo institucional.')
    return f'{first}.{surname}'


def _email_component(value: str) -> str:
    normalized = _normalize_ascii(value).lower()
    return re.sub(r'[^a-z0-9]', '', normalized)


def _email_local_part(value: Any) -> str:
    text = str(value or '').strip().lower()
    if '@' not in text:
        return ''
    local_part = text.split('@', 1)[0].strip()
    return re.sub(r'[^a-z0-9._-]', '', _normalize_ascii(local_part).lower())


def _email_domain(value: Any) -> str:
    text = str(value or '').strip().lower()
    if '@' not in text:
        return ''
    return _clean_domain(text.rsplit('@', 1)[1])


def _password_component(value: str) -> str:
    normalized = _normalize_ascii(value)
    cleaned = re.sub(r'[^A-Za-z0-9]', '', normalized)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else ''


def _normalize_ascii(value: Any) -> str:
    normalized = unicodedata.normalize('NFD', str(value or '').strip())
    return ''.join(ch for ch in normalized if unicodedata.category(ch) != 'Mn')


def _clean_spaces(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())


def _clean_domain(value: Any) -> str:
    domain = str(value or '').strip().lower().lstrip('@')
    return domain or DEFAULT_DOMAIN


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _env_first(*keys: str) -> str:
    return _env_first_named(*keys)[0]


def _env_first_named(*keys: str) -> tuple[str, str]:
    for key in keys:
        value = str(os.getenv(key) or '').strip()
        if value:
            return value, key
    return '', ''


def _alias_max_attempts() -> int:
    value = str(os.getenv('MICROSOFT_ALIAS_MAX_ATTEMPTS') or '').strip()
    if not value:
        return DEFAULT_ALIAS_MAX_ATTEMPTS
    try:
        return max(2, int(value))
    except ValueError:
        return DEFAULT_ALIAS_MAX_ATTEMPTS


def _graph_error_message(raw: str) -> str:
    if not raw:
        return ''
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:1000]
    error = payload.get('error') if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get('message') or error.get('code') or '')[:1000]
    return str(payload)[:1000]
