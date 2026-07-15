from __future__ import annotations

import os
from hashlib import sha256
from functools import wraps
from typing import Any, Callable

from django.core.cache import cache
from django.core import signing
from django.http import JsonResponse


SESSION_TOKEN_SALT = 'dashboard-auth-session'
DEFAULT_SESSION_MAX_AGE_SECONDS = 2 * 60 * 60
MAX_ADMIN_JSON_BODY_BYTES = int(os.getenv('MAX_ADMIN_JSON_BODY_BYTES') or str(8 * 1024 * 1024))
FINANCIAL_ALLOWED_ADMIN_REQUESTS = {
    ('GET', '/admin/course-cuts/'),
    ('GET', '/admin/enrolled-students/'),
    ('GET', '/admin/payments/'),
    ('POST', '/admin/payments/reconcile/'),
    ('POST', '/admin/payments/register/'),
    ('POST', '/admin/payments/discount/'),
    ('POST', '/admin/payments/receipt/'),
    ('POST', '/admin/payment-info/'),
    ('GET', '/admin/payment-operations/'),
    ('GET', '/admin/payment-operations/links/'),
    ('POST', '/admin/payment-operations/generate/'),
    ('POST', '/admin/payment-cancel/'),
}
ACADEMIC_ALLOWED_ADMIN_REQUESTS = {
    ('GET', '/admin/academic-catalogs/'),
    ('GET', '/admin/academic-enrollment/students/'),
    ('POST', '/admin/academic-enrollment/selected/'),
    ('GET', '/admin/teachers/'),
    ('POST', '/admin/teacher-entry/'),
    ('POST', '/admin/teacher-enrollment/'),
    ('GET', '/admin/course-cuts/'),
    ('POST', '/admin/course-cuts/create/'),
    ('POST', '/admin/course-cuts/close/'),
    ('GET', '/admin/course-cuts/students/'),
    ('POST', '/admin/course-cuts/students/sync/'),
    ('GET', '/admin/course-cuts/schedule/'),
    ('POST', '/admin/course-cuts/schedule/save/'),
    ('POST', '/admin/course-cuts/teams/sync/'),
    ('GET', '/admin/enrolled-students/'),
    ('GET', '/admin/grade-transfer/'),
    ('POST', '/admin/grade-transfer/save/'),
    ('GET', '/admin/attendance/'),
    ('POST', '/admin/attendance/save/'),
    ('POST', '/admin/carrera-status/'),
    ('POST', '/admin/pensum/'),
    ('POST', '/admin/pensum-status/'),
    ('GET', '/admin/bulk-enrollment/template/'),
    ('POST', '/admin/bulk-enrollment/'),
    ('GET', '/admin/certificate-template/'),
    ('GET', '/admin/certificate-template/preview/'),
    ('GET', '/admin/certificates/students/'),
    ('POST', '/admin/certificates/generate/'),
    ('GET', '/admin/certificates/download/'),
}
SECRETARY_ALLOWED_ADMIN_REQUESTS = {
    request_key
    for request_key in ACADEMIC_ALLOWED_ADMIN_REQUESTS
    if request_key[1] not in {
        '/admin/carrera-status/',
        '/admin/pensum/',
        '/admin/pensum-status/',
        '/admin/teacher-entry/',
    }
}
EXECUTIVE_READ_ADMIN_REQUESTS = {
    request_key
    for request_key in (FINANCIAL_ALLOWED_ADMIN_REQUESTS | ACADEMIC_ALLOWED_ADMIN_REQUESTS)
    if request_key[0] == 'GET'
}
STUDENT_SUPPORT_READ_REQUESTS = {
    ('GET', '/admin/course-cuts/'),
    ('GET', '/admin/course-cuts/students/'),
    ('GET', '/admin/enrolled-students/'),
}
ADMISSIONS_ALLOWED_ADMIN_REQUESTS = STUDENT_SUPPORT_READ_REQUESTS | {
    ('GET', '/admin/academic-catalogs/'),
    ('GET', '/admin/academic-enrollment/students/'),
}
ROLE_ALLOWED_ADMIN_REQUESTS = {
    'FINANCIERO': FINANCIAL_ALLOWED_ADMIN_REQUESTS,
    'ACADEMICO': ACADEMIC_ALLOWED_ADMIN_REQUESTS,
    'ACADÉMICO': ACADEMIC_ALLOWED_ADMIN_REQUESTS,
    'SECRETARIA': SECRETARY_ALLOWED_ADMIN_REQUESTS,
    'SECRETARÍA': SECRETARY_ALLOWED_ADMIN_REQUESTS,
    'RECTOR': EXECUTIVE_READ_ADMIN_REQUESTS,
    'VICERRECTOR': EXECUTIVE_READ_ADMIN_REQUESTS,
    'BIENESTAR': STUDENT_SUPPORT_READ_REQUESTS,
    'ADMISIONES': ADMISSIONS_ALLOWED_ADMIN_REQUESTS,
}
FULL_ADMIN_ROLES = {'ADMINISTRADOR'}


def create_session_token(user: dict[str, Any]) -> str:
    payload = {
        'category': str(user.get('category') or ''),
        'login': str(user.get('login') or ''),
        'email': str(user.get('email') or ''),
        'role': user.get('role') or {},
    }
    return signing.dumps(payload, salt=SESSION_TOKEN_SALT, compress=True)


def require_dashboard_session(view_func: Callable) -> Callable:
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        token = _extract_session_token(request)
        if not token:
            return _auth_response('Debes iniciar sesión para consultar notificaciones.', 401)
        try:
            session_user = signing.loads(
                token,
                salt=SESSION_TOKEN_SALT,
                max_age=_session_max_age_seconds(),
            )
        except signing.SignatureExpired:
            return _auth_response('La sesión expiró. Inicia sesión nuevamente.', 401)
        except signing.BadSignature:
            return _auth_response('Sesión inválida. Inicia sesión nuevamente.', 401)
        if str(session_user.get('category') or '').lower() not in {'staff', 'teacher', 'student'}:
            return _auth_response('No tienes permisos para consultar notificaciones.', 403)
        request_validation_error = _validate_admin_request_shape(request)
        if request_validation_error:
            return request_validation_error
        request.dashboard_user = session_user
        return view_func(request, *args, **kwargs)

    return wrapped


def require_admin_session(view_func: Callable) -> Callable:
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        token = _extract_session_token(request)
        if not token:
            return _auth_response('Debes iniciar sesión para acceder al dashboard administrativo.', 401)

        try:
            session_user = signing.loads(
                token,
                salt=SESSION_TOKEN_SALT,
                max_age=_session_max_age_seconds(),
            )
        except signing.SignatureExpired:
            return _auth_response('La sesión expiró. Inicia sesión nuevamente.', 401)
        except signing.BadSignature:
            return _auth_response('Sesión inválida. Inicia sesión nuevamente.', 401)

        if str(session_user.get('category') or '').lower() != 'staff':
            return _auth_response('No tienes permisos para ejecutar esta accion.', 403)

        role_name = _staff_role_name(session_user)
        if not _staff_request_is_allowed(request, role_name):
            return _auth_response(
                'Tu rol no tiene permisos para ejecutar esta operación administrativa.',
                403,
            )

        request_validation_error = _validate_admin_request_shape(request)
        if request_validation_error:
            return request_validation_error

        request.dashboard_user = session_user
        return view_func(request, *args, **kwargs)

    return wrapped


def _staff_role_name(session_user: dict[str, Any]) -> str:
    role = session_user.get('role') if isinstance(session_user.get('role'), dict) else {}
    return str(role.get('name') or '').strip().upper()


def _financial_request_is_allowed(request) -> bool:
    return _request_matches_allowlist(request, FINANCIAL_ALLOWED_ADMIN_REQUESTS)


def _staff_request_is_allowed(request, role_name: str) -> bool:
    if role_name in FULL_ADMIN_ROLES:
        return True
    allowed_requests = ROLE_ALLOWED_ADMIN_REQUESTS.get(role_name, set())
    return _request_matches_allowlist(request, allowed_requests)


def _request_matches_allowlist(request, allowed_requests: set[tuple[str, str]]) -> bool:
    request_path = str(request.path or '').rstrip('/') + '/'
    request_key = (str(request.method or '').upper(), request_path)
    return any(
        request_key[0] == allowed_method and request_key[1].endswith(allowed_path)
        for allowed_method, allowed_path in allowed_requests
    )


def enforce_request_rate_limit(
    request,
    *,
    scope: str,
    identifier: str = '',
    limit: int = 20,
    window_seconds: int = 300,
) -> JsonResponse | None:
    """Fixed-window limiter for authentication and unauthenticated public operations."""
    safe_limit = max(1, int(limit))
    safe_window = max(30, int(window_seconds))
    source = f'{scope}|{_client_ip(request)}|{str(identifier).strip().lower()}'
    cache_key = f'security:rate:{sha256(source.encode("utf-8")).hexdigest()}'
    if cache.add(cache_key, 1, timeout=safe_window):
        return None
    try:
        attempts = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=safe_window)
        attempts = 1
    if attempts <= safe_limit:
        return None
    response = _auth_response(
        'Se alcanzó el límite temporal de solicitudes. Intenta nuevamente más tarde.',
        429,
    )
    response['Retry-After'] = str(safe_window)
    return response


def clear_request_rate_limit(request, *, scope: str, identifier: str = '') -> None:
    source = f'{scope}|{_client_ip(request)}|{str(identifier).strip().lower()}'
    cache.delete(f'security:rate:{sha256(source.encode("utf-8")).hexdigest()}')


def _client_ip(request) -> str:
    remote_addr = str(request.META.get('REMOTE_ADDR') or '').strip()
    forwarded = str(request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',', 1)[0].strip()
    if remote_addr in {'127.0.0.1', '::1'} and forwarded:
        return forwarded[:64]
    return remote_addr[:64] or 'unknown'


def require_teacher_session(view_func: Callable) -> Callable:
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        token = _extract_session_token(request)
        if not token:
            return _auth_response('Debes iniciar sesión para acceder al dashboard docente.', 401)

        try:
            session_user = signing.loads(
                token,
                salt=SESSION_TOKEN_SALT,
                max_age=_session_max_age_seconds(),
            )
        except signing.SignatureExpired:
            return _auth_response('La sesión expiró. Inicia sesión nuevamente.', 401)
        except signing.BadSignature:
            return _auth_response('Sesión inválida. Inicia sesión nuevamente.', 401)

        if str(session_user.get('category') or '').lower() != 'teacher':
            return _auth_response('No tienes permisos para consultar información docente.', 403)

        request_validation_error = _validate_admin_request_shape(request)
        if request_validation_error:
            return request_validation_error
        request.dashboard_user = session_user
        return view_func(request, *args, **kwargs)

    return wrapped


def require_student_session(view_func: Callable) -> Callable:
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        token = _extract_session_token(request)
        if not token:
            return _auth_response('Debes iniciar sesión para acceder al dashboard estudiantil.', 401)

        try:
            session_user = signing.loads(
                token,
                salt=SESSION_TOKEN_SALT,
                max_age=_session_max_age_seconds(),
            )
        except signing.SignatureExpired:
            return _auth_response('La sesión expiró. Inicia sesión nuevamente.', 401)
        except signing.BadSignature:
            return _auth_response('Sesión inválida. Inicia sesión nuevamente.', 401)

        if str(session_user.get('category') or '').lower() != 'student':
            return _auth_response('No tienes permisos para consultar información estudiantil.', 403)

        request_validation_error = _validate_admin_request_shape(request)
        if request_validation_error:
            return request_validation_error
        request.dashboard_user = session_user
        return view_func(request, *args, **kwargs)

    return wrapped


def _extract_session_token(request) -> str:
    authorization = request.headers.get('Authorization', '').strip()
    if authorization.lower().startswith('bearer '):
        return authorization[7:].strip()
    return request.headers.get('X-Dashboard-Session', '').strip()


def _validate_admin_request_shape(request) -> JsonResponse | None:
    if request.method not in {'POST', 'PUT', 'PATCH'}:
        return None

    content_type = str(request.META.get('CONTENT_TYPE') or '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        return JsonResponse(
            {'ok': False, 'message': 'El contenido debe enviarse como application/json.'},
            status=415,
        )

    content_length = str(request.META.get('CONTENT_LENGTH') or '').strip()
    if content_length:
        try:
            if int(content_length) > MAX_ADMIN_JSON_BODY_BYTES:
                return JsonResponse(
                    {'ok': False, 'message': 'La solicitud supera el tamaño permitido.'},
                    status=413,
                )
        except ValueError:
            return JsonResponse({'ok': False, 'message': 'Solicitud inválida.'}, status=400)

    return None


def _session_max_age_seconds() -> int:
    value = os.getenv('DASHBOARD_SESSION_MAX_AGE_SECONDS', '').strip()
    if not value:
        return DEFAULT_SESSION_MAX_AGE_SECONDS
    try:
        return max(300, int(value))
    except ValueError:
        return DEFAULT_SESSION_MAX_AGE_SECONDS


def _auth_response(message: str, status: int) -> JsonResponse:
    return JsonResponse({'ok': False, 'message': message}, status=status)
