from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable

from django.core import signing
from django.http import JsonResponse


SESSION_TOKEN_SALT = 'dashboard-auth-session'
DEFAULT_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
MAX_ADMIN_JSON_BODY_BYTES = int(os.getenv('MAX_ADMIN_JSON_BODY_BYTES') or str(8 * 1024 * 1024))


def create_session_token(user: dict[str, Any]) -> str:
    payload = {
        'category': str(user.get('category') or ''),
        'login': str(user.get('login') or ''),
        'email': str(user.get('email') or ''),
        'role': user.get('role') or {},
    }
    return signing.dumps(payload, salt=SESSION_TOKEN_SALT, compress=True)


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

        request_validation_error = _validate_admin_request_shape(request)
        if request_validation_error:
            return request_validation_error

        request.dashboard_user = session_user
        return view_func(request, *args, **kwargs)

    return wrapped


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
