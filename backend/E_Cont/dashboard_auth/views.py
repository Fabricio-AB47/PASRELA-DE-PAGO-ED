import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .inscription_catalogs import fetch_inscription_catalogs
from .payments import (
    PaymentGatewayError,
    admin_cancel_payment,
    admin_get_payment_info,
    create_payment_link_and_notify,
    generate_unique_numcodigo,
)
from .services import AuthError, InactiveUserError, InvalidScopeError, authenticate_user
from .student_records import StudentLookupError, lookup_student_inscription

logger = logging.getLogger(__name__)


@require_GET
def health_view(_request):
    return JsonResponse({'ok': True, 'service': 'dashboard-auth'})


@csrf_exempt
@require_POST
def login_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    identifier = str(payload.get('identifier', '')).strip()
    password = str(payload.get('password', ''))
    scope = str(payload.get('scope', 'auto')).strip().lower()

    if not identifier or not password:
        return JsonResponse(
            {'ok': False, 'message': 'Debes completar el usuario y la contrasena.'},
            status=400,
        )

    try:
        user = authenticate_user(identifier, password, scope)
    except InvalidScopeError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except InactiveUserError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=403)
    except AuthError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=401)
    except Exception:
        logger.exception('Unexpected error while authenticating dashboard user.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno validando el acceso. Intenta de nuevo.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Acceso concedido.',
            'user': user.to_dict(),
        }
    )


@csrf_exempt
@require_POST
def student_lookup_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    email = str(payload.get('email', '')).strip()
    matricula = str(payload.get('matricula', '')).strip()

    try:
        record = lookup_student_inscription(email, matricula)
    except StudentLookupError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=404)
    except Exception:
        logger.exception('Unexpected error while looking up student inscription.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno consultando la inscripcion del estudiante.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Registro de inscripcion localizado.',
            'record': record,
        }
    )


@csrf_exempt
@require_POST
def inscription_payment_link_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    try:
        result = create_payment_link_and_notify(payload)
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while creating payment link from inscription flow.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno creando el enlace de pago.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Enlace de pago generado.',
            'matricula': result.get('matricula'),
            'payment_link': result.get('payment_link'),
            'receipt_email': result.get('receipt_email'),
            'email_result': result.get('email_result'),
            'provider_response': result.get('provider_response'),
            'official_sync': result.get('official_sync'),
        }
    )


@require_GET
def inscription_generate_matricula_view(_request):
    try:
        matricula = generate_unique_numcodigo()
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while generating unique matricula.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno generando la matricula unica.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Matricula unica generada.',
            'matricula': matricula,
        }
    )


@require_GET
def inscription_catalogs_view(_request):
    try:
        catalogs = fetch_inscription_catalogs()
    except Exception:
        logger.exception('Unexpected error while loading inscription catalogs.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno cargando carreras, cursos y periodos.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Catalogos de inscripcion cargados.',
            'catalogs': catalogs,
        }
    )


@csrf_exempt
@require_POST
def admin_payment_info_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    try:
        result = admin_get_payment_info(payload)
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while requesting payment info from admin dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno consultando la transaccion.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Consulta completada.', 'result': result})


@csrf_exempt
@require_POST
def admin_payment_cancel_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    try:
        result = admin_cancel_payment(payload)
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while canceling payment from admin dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno anulando la transaccion.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Anulacion ejecutada.', 'result': result})
