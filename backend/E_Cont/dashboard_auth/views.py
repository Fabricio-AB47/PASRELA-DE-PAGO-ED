import json
import logging

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .bulk_enrollment import (
    BulkEnrollmentError,
    TEMPLATE_FILE_NAME,
    build_bulk_enrollment_template,
    excel_upload_from_json,
    process_bulk_enrollment_excel,
)
from .inscription_catalogs import (
    AcademicCatalogError,
    fetch_admin_academic_catalogs,
    fetch_inscription_catalogs,
    update_carrera_status,
    update_pensum_status,
    upsert_pensum_entry,
)
from .microsoft365 import (
    Microsoft365Error,
    create_microsoft365_user,
    get_student_license_summary,
)
from .payments import (
    PaymentGatewayError,
    admin_cancel_payment,
    admin_get_payment_info,
    create_payment_link_and_notify,
    generate_unique_numcodigo,
)
from .security import create_session_token, require_admin_session
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

    user_payload = user.to_dict()
    return JsonResponse(
        {
            'ok': True,
            'message': 'Acceso concedido.',
            'user': user_payload,
            'session_token': create_session_token(user_payload),
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
            'microsoft365': result.get('microsoft365'),
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
@require_admin_session
def microsoft365_create_user_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    try:
        result = create_microsoft365_user(payload)
    except Microsoft365Error as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=exc.status_code)
    except Exception:
        logger.exception('Unexpected error while creating Microsoft 365 user.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno creando el usuario Microsoft 365.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Usuario Microsoft 365 creado y licenciado correctamente.',
            'result': result,
        },
        status=201,
    )


@require_GET
@require_admin_session
def microsoft365_student_license_view(_request):
    try:
        result = get_student_license_summary()
    except Microsoft365Error as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=exc.status_code)
    except Exception:
        logger.exception('Unexpected error while loading Microsoft 365 student license.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno consultando la licencia Microsoft 365.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Licencia Microsoft 365 para estudiantes localizada.',
            'license': result,
        }
    )


@require_GET
@require_admin_session
def admin_academic_catalogs_view(_request):
    try:
        catalogs = fetch_admin_academic_catalogs()
    except Exception:
        logger.exception('Unexpected error while loading admin academic catalogs.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno cargando carreras y pensum.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Catalogos academicos cargados.',
            'catalogs': catalogs,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_carrera_status_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    try:
        result = update_carrera_status(payload)
    except AcademicCatalogError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while updating carrera status.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno actualizando el estado de la carrera.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Estado de carrera actualizado.', 'result': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_pensum_entry_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    try:
        result = upsert_pensum_entry(payload)
    except AcademicCatalogError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while saving pensum entry.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno guardando el pensum.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Pensum guardado.', 'result': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_pensum_status_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    try:
        result = update_pensum_status(payload)
    except AcademicCatalogError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while updating pensum status.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno actualizando el estado de la materia.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Estado de materia actualizado.', 'result': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_bulk_enrollment_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON valido.'},
            status=400,
        )

    defaults = {
        'carrera_num': payload.get('carrera_num', ''),
        'cod_anio_basica': payload.get('cod_anio_basica', ''),
        'codigo_materia': payload.get('codigo_materia', ''),
        'codigo_periodo': payload.get('codigo_periodo', ''),
        'estado_periodo': payload.get('estado_periodo', ''),
        'nombre_materia': payload.get('nombre_materia', ''),
    }

    try:
        uploaded_file = excel_upload_from_json(payload)
        result = process_bulk_enrollment_excel(uploaded_file, defaults)
    except BulkEnrollmentError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while processing bulk enrollment upload.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrio un error interno procesando la matricula masiva.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Carga masiva procesada.',
            'result': result,
        }
    )


@require_GET
@require_admin_session
def admin_bulk_enrollment_template_view(_request):
    content = build_bulk_enrollment_template()
    response = HttpResponse(
        content,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{TEMPLATE_FILE_NAME}"'
    return response


@csrf_exempt
@require_POST
@require_admin_session
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
@require_admin_session
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
