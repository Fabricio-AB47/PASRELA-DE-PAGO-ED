import json
import logging
from html import escape

from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .bulk_enrollment import (
    BulkEnrollmentError,
    TEMPLATE_FILE_NAME,
    build_bulk_enrollment_template,
    excel_upload_from_json,
    list_academic_enrollment_students,
    process_bulk_enrollment_excel,
    process_selected_student_enrollment,
)
from .admin_certificates import (
    AdminCertificateError,
    download_admin_certificate,
    generate_admin_certificates,
    list_admin_certificate_students,
)
from .certificate_template import (
    CertificateTemplateError,
    build_certificate_template_preview,
    get_certificate_template_config,
    save_certificate_template_config,
)
from .course_cuts import (
    CourseCutError,
    close_course_cut,
    create_course_cut,
    list_course_cut_schedule,
    list_attendance_students,
    list_enrolled_students,
    list_grade_transfer_students,
    list_course_cut_students,
    list_course_cuts,
    save_attendance_records,
    save_course_cut_module,
    save_course_cut_schedule,
    save_grade_transfer,
    sync_course_cut_students,
    sync_course_cut_teams,
    update_course_cut,
)
from .continuing_education import complement_status
from .inscription_catalogs import (
    AcademicCatalogError,
    fetch_admin_academic_catalogs,
    fetch_inscription_catalogs,
    update_carrera_status,
    update_pensum_status,
    upsert_pensum_entry,
)
from .inscription_certificate import (
    CERTIFICATE_CONTENT_TYPE,
    InscriptionCertificateError,
    build_certificate_payload,
    create_stored_certificate_record,
    load_signed_certificate_payload,
    load_or_create_stored_certificate,
    send_certificate_email,
    verify_certificate_record,
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
    create_financial_card_payment,
    create_payment_link_and_notify,
    generate_all_digital_payment_receipt_document,
    generate_unique_numcodigo,
    get_payment_student_profile,
    get_registered_user_payment_detail,
    list_registered_user_payments,
    list_financial_payment_operations,
    reconcile_pending_all_digital_payments,
    search_payment_links_for_operations,
    correct_continuing_education_discount,
    register_continuing_education_payment,
    register_continuing_education_discount,
    upload_continuing_education_invoice,
)
from .notifications import list_notifications, mark_notifications_read, notification_storage_status
from .security import (
    clear_request_rate_limit,
    create_session_token,
    enforce_request_rate_limit,
    require_admin_session,
    require_dashboard_session,
    require_student_session,
    require_teacher_session,
)
from .services import (
    AuthError,
    InactiveUserError,
    InvalidScopeError,
    RoleSelectionRequired,
    authenticate_user,
)
from .student_registration import (
    RegisteredUserExistsError,
    USER_REGISTERED_MESSAGE,
    ensure_user_is_not_registered,
)
from .student_records import StudentLookupError, lookup_student_inscription
from .student_updates import (
    StudentUpdateError,
    get_student_migration_credentials,
    list_students_for_update,
    update_enrolled_student,
)
from .student_dashboard import (
    StudentDashboardError,
    build_student_certificate,
    get_student_grades_dashboard,
    get_student_schedule_dashboard,
    preview_student_certificate,
    send_student_certificate,
)
from .teacher_dashboard import (
    TeacherDashboardError,
    get_teacher_attendance_dashboard,
    get_teacher_attendance_roster,
    get_teacher_course_dashboard,
    get_teacher_grades_dashboard,
    get_teacher_schedule_dashboard,
    save_teacher_schedule,
    save_teacher_attendance,
)
from .teacher_enrollment import (
    TeacherEnrollmentError,
    create_teacher_entry_and_send_credentials,
    enroll_existing_teacher,
    inspect_teacher_identity_by_cedula,
    list_teacher_candidates,
)

logger = logging.getLogger(__name__)


@require_GET
def health_view(_request):
    try:
        notifications = notification_storage_status()
    except Exception:
        notifications = {'available': False}
    try:
        continuing_education = complement_status()
    except Exception:
        continuing_education = {'available': False}
    return JsonResponse(
        {
            'ok': True,
            'service': 'dashboard-auth',
            'continuing_education': {'available': bool(continuing_education.get('available'))},
            'notifications': {'available': bool(notifications.get('available'))},
        }
    )


@require_GET
@require_dashboard_session
@never_cache
def notifications_view(request):
    try:
        result = list_notifications(request.dashboard_user, limit=request.GET.get('limit') or 30)
    except Exception:
        logger.exception('Unexpected error loading dashboard notifications.')
        return JsonResponse({'ok': False, 'message': 'No fue posible cargar las notificaciones.'}, status=500)
    return JsonResponse({'ok': True, 'result': result})


@csrf_exempt
@require_POST
@require_dashboard_session
@never_cache
def notifications_read_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8')) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)
    notification_ids = payload.get('notification_ids')
    if notification_ids is not None and not isinstance(notification_ids, list):
        return JsonResponse({'ok': False, 'message': 'notification_ids debe ser una lista.'}, status=400)
    try:
        updated = mark_notifications_read(request.dashboard_user, notification_ids)
    except Exception:
        logger.exception('Unexpected error marking dashboard notifications as read.')
        return JsonResponse({'ok': False, 'message': 'No fue posible actualizar las notificaciones.'}, status=500)
    return JsonResponse({'ok': True, 'updated': updated})


@csrf_exempt
@require_POST
def login_view(request):
    content_type = str(request.META.get('CONTENT_TYPE') or '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        return JsonResponse({'ok': False, 'message': 'El contenido debe enviarse como application/json.'}, status=415)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    identifier = str(payload.get('identifier', '')).strip()
    password = str(payload.get('password', ''))
    scope = str(payload.get('scope', 'auto')).strip().lower()

    if not identifier or not password:
        return JsonResponse(
            {'ok': False, 'message': 'Debes completar el usuario y la contraseña.'},
            status=400,
        )

    rate_response = enforce_request_rate_limit(
        request,
        scope='dashboard-login',
        identifier=identifier,
        limit=5,
        window_seconds=900,
    )
    if rate_response:
        return rate_response

    try:
        user = authenticate_user(identifier, password, scope)
    except RoleSelectionRequired as exc:
        clear_request_rate_limit(request, scope='dashboard-login', identifier=identifier)
        return JsonResponse(
            {
                'ok': False,
                'selection_required': True,
                'message': str(exc),
                'roles': exc.roles,
            },
            status=409,
        )
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
                'message': 'Ocurrió un error interno validando el acceso. Intenta de nuevo.',
            },
            status=500,
        )

    user_payload = user.to_dict()
    clear_request_rate_limit(request, scope='dashboard-login', identifier=identifier)
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
    content_type = str(request.META.get('CONTENT_TYPE') or '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        return JsonResponse({'ok': False, 'message': 'El contenido debe enviarse como application/json.'}, status=415)
    rate_response = enforce_request_rate_limit(
        request,
        scope='student-inscription-lookup',
        limit=20,
        window_seconds=600,
    )
    if rate_response:
        return rate_response
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
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
                'message': 'Ocurrió un error interno consultando la inscripción del estudiante.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Registro de inscripción localizado.',
            'record': record,
        }
    )


@csrf_exempt
@require_POST
def inscription_payment_link_view(request):
    content_type = str(request.META.get('CONTENT_TYPE') or '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        return JsonResponse({'ok': False, 'message': 'El contenido debe enviarse como application/json.'}, status=415)
    rate_response = enforce_request_rate_limit(
        request,
        scope='public-payment-link',
        limit=5,
        window_seconds=900,
    )
    if rate_response:
        return rate_response
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        ensure_user_is_not_registered(
            payload.get('cedula'),
            cod_anio_basica=payload.get('cod_anio_basica'),
            codigo_materia=payload.get('codigo_materia'),
            codigo_periodo=payload.get('codigo_periodo'),
        )
    except RegisteredUserExistsError:
        return JsonResponse(
            {
                'ok': False,
                'message': USER_REGISTERED_MESSAGE,
                'registered_user': {'exists': True, 'message': USER_REGISTERED_MESSAGE},
            },
            status=409,
        )
    except Exception:
        logger.exception('Unexpected error while validating registered user from inscription flow.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno validando si el usuario ya está registrado.',
            },
            status=500,
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
                'message': 'Ocurrió un error interno creando el enlace de pago.',
            },
            status=500,
        )

    try:
        certificate_payload = build_certificate_payload(payload, result, source='inscripcion')
        certificate_record = create_stored_certificate_record(certificate_payload)
    except InscriptionCertificateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while storing inscription certificate.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno generando el certificado de inscripción.',
            },
            status=500,
        )
    try:
        certificate_email_result = send_certificate_email(
            recipient_email=str(payload.get('email') or '').strip(),
            recipient_name=str(payload.get('nombre') or '').strip(),
            certificate_record=certificate_record,
        )
    except Exception as exc:
        certificate_email_result = {
            'sent': False,
            'message': f'Certificado generado, pero no fue posible enviarlo por correo: {str(exc)}',
            'filename': certificate_record.get('filename'),
        }

    return JsonResponse(
        {
            'ok': True,
            'message': 'Enlace de pago generado.',
            'matricula': result.get('matricula'),
            'payment_link': result.get('payment_link'),
            'receipt_email': result.get('receipt_email'),
            'email_result': result.get('email_result'),
            'payment_status': 'GENERADA',
            'official_sync': {'ok': bool((result.get('official_sync') or {}).get('ok'))},
            'microsoft365': {'ok': bool((result.get('microsoft365') or {}).get('ok'))},
            'certificate': certificate_record,
            'certificate_email_result': certificate_email_result,
        }
    )


@csrf_exempt
@require_POST
@never_cache
def inscription_certificate_view(request):
    content_type = str(request.META.get('CONTENT_TYPE') or '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        return JsonResponse({'ok': False, 'message': 'El contenido debe enviarse como application/json.'}, status=415)
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        certificate_payload = _certificate_payload_from_request(payload)
        content, filename = load_or_create_stored_certificate(certificate_payload)
    except InscriptionCertificateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while generating inscription certificate.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno generando el certificado de inscripción.',
            },
            status=500,
        )

    response = HttpResponse(content, content_type=CERTIFICATE_CONTENT_TYPE)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _certificate_payload_from_request(payload: dict) -> dict:
    certificate = payload.get('certificate') if isinstance(payload, dict) else None
    token = ''
    if isinstance(certificate, dict):
        token = str(certificate.get('token') or '')
    if not token and isinstance(payload, dict):
        token = str(payload.get('certificate_token') or '')
    if not token:
        raise InscriptionCertificateError(
            'Debes enviar un token de certificado generado por el sistema.'
        )
    return load_signed_certificate_payload(token)


@require_GET
@never_cache
def certificate_verify_view(request):
    try:
        result = verify_certificate_record(
            request.GET.get('numero') or request.GET.get('code') or request.GET.get('certificado'),
            request.GET.get('verificacion') or request.GET.get('verification') or request.GET.get('v'),
        )
    except InscriptionCertificateError as exc:
        return JsonResponse({'ok': False, 'valid': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while verifying certificate.')
        return JsonResponse(
            {
                'ok': False,
                'valid': False,
                'message': 'Ocurrió un error interno verificando el certificado.',
            },
            status=500,
        )

    status = 200 if result.get('valid') else 404
    response_payload = {
        'ok': bool(result.get('valid')),
        **result,
    }
    wants_json = (
        request.GET.get('format') == 'json'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if wants_json:
        return JsonResponse(response_payload, status=status)
    return HttpResponse(_certificate_verification_html(response_payload), status=status)


def _certificate_verification_html(payload: dict) -> str:
    certificate = payload.get('certificate') if isinstance(payload.get('certificate'), dict) else {}
    valid = bool(payload.get('valid'))
    status_label = 'Certificado válido' if valid else 'Certificado no válido'
    status_color = '#0f7a3a' if valid else '#9B0E0E'
    message = escape(str(payload.get('message') or ''))

    def value(key: str, fallback: str = 'No registrado') -> str:
        text = str(certificate.get(key) or '').strip()
        return escape(text or fallback)

    rows = [
        ('Número de certificado', value('numero_certificado')),
        ('Tipo', value('tipo_certificado')),
        ('Estado', value('estado')),
        ('Estudiante', value('estudiante')),
        ('Cédula', value('cedula_mascara')),
        ('Código estudiante', value('codigo_estud')),
        ('Fecha de emisión', value('fecha_generacion')),
        ('Detalle', value('observacion')),
    ]
    rows_html = ''.join(
        f'<tr><th>{label}</th><td>{field_value}</td></tr>'
        for label, field_value in rows
    )
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Verificación de certificado INTEC</title>
  <style>
    body {{
      margin: 0;
      background: #eef6f8;
      color: #1f2933;
      font-family: Arial, sans-serif;
    }}
    main {{
      max-width: 760px;
      margin: 32px auto;
      padding: 0 16px;
    }}
    section {{
      background: #fff;
      border-radius: 14px;
      padding: 26px;
      box-shadow: 0 14px 38px rgba(15, 23, 42, 0.12);
    }}
    .eyebrow {{
      color: #7a858d;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 8px 0 12px;
      color: #9B0E0E;
      font-size: 30px;
    }}
    .status {{
      display: inline-block;
      margin: 8px 0 18px;
      padding: 10px 14px;
      border-radius: 999px;
      background: {status_color};
      color: #fff;
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
    }}
    th, td {{
      border-top: 1px solid #d8e1e4;
      padding: 12px 8px;
      text-align: left;
      vertical-align: top;
      font-size: 15px;
    }}
    th {{
      width: 34%;
      color: #6b7280;
      font-weight: 700;
    }}
    p {{
      margin: 0;
      color: #4b5563;
      line-height: 1.5;
    }}
  </style>
</head>
<body>
  <main>
    <section>
      <div class="eyebrow">INTEC</div>
      <h1>Verificación de certificado</h1>
      <div class="status">{status_label}</div>
      <p>{message}</p>
      <table>{rows_html}</table>
    </section>
  </main>
</body>
</html>"""


@require_GET
@never_cache
def inscription_generate_matricula_view(_request):
    rate_response = enforce_request_rate_limit(
        _request,
        scope='public-matricula-number',
        limit=10,
        window_seconds=600,
    )
    if rate_response:
        return rate_response
    try:
        matricula = generate_unique_numcodigo()
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while generating unique matricula.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno generando la matrícula única.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Matrícula única generada.',
            'matricula': matricula,
        }
    )


@require_GET
@never_cache
def inscription_catalogs_view(_request):
    try:
        catalogs = fetch_inscription_catalogs()
    except Exception:
        logger.exception('Unexpected error while loading inscription catalogs.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando carreras, cursos y períodos.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Catálogos de inscripción cargados.',
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
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
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
                'message': 'Ocurrió un error interno creando el usuario Microsoft 365.',
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
                'message': 'Ocurrió un error interno consultando la licencia Microsoft 365.',
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
@never_cache
def admin_certificate_template_view(request):
    try:
        config = get_certificate_template_config(request.GET.get('corte_id') or request.GET.get('CorteId'))
    except Exception:
        logger.exception('Unexpected error while loading certificate template config.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando la plantilla de certificado.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Plantilla de certificado cargada.',
            'config': config,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
@never_cache
def admin_certificate_template_save_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        config = save_certificate_template_config(payload, user_login=_dashboard_user_login(request))
    except CertificateTemplateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while saving certificate template config.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno guardando la plantilla de certificado.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Plantilla de certificado guardada.',
            'config': config,
        }
    )


@require_GET
@require_admin_session
@never_cache
def admin_certificate_template_preview_view(request):
    try:
        content, filename = build_certificate_template_preview(request.GET.get('corte_id') or request.GET.get('CorteId'))
    except CertificateTemplateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except InscriptionCertificateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while generating certificate template preview.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno generando la previsualización del certificado.',
            },
            status=500,
        )

    response = HttpResponse(content, content_type=CERTIFICATE_CONTENT_TYPE)
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@require_GET
@require_admin_session
@never_cache
def admin_certificate_students_view(request):
    try:
        result = list_admin_certificate_students(
            request.GET.get('corte_id') or request.GET.get('CorteId'),
            search=request.GET.get('q', ''),
            limit=request.GET.get('limit', 300),
        )
    except (AdminCertificateError, CourseCutError) as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading certificate students.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando estudiantes para certificado.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Estudiantes para certificado cargados.',
            'result': result,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
@never_cache
def admin_certificate_generate_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = generate_admin_certificates(payload, user_login=_dashboard_user_login(request))
    except (AdminCertificateError, CourseCutError, InscriptionCertificateError) as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while generating admin certificates.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno generando certificados de la corte.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Proceso de certificados ejecutado.',
            'result': result,
        }
    )


@require_GET
@require_admin_session
@never_cache
def admin_certificate_download_view(request):
    try:
        content, filename = download_admin_certificate(
            request.GET.get('corte_id') or request.GET.get('CorteId'),
            request.GET.get('corte_estudiante_id') or request.GET.get('CorteEstudianteId'),
        )
    except AdminCertificateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while downloading admin certificate.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno descargando el certificado.',
            },
            status=500,
        )

    response = HttpResponse(content, content_type=CERTIFICATE_CONTENT_TYPE)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_GET
@require_admin_session
@never_cache
def admin_academic_catalogs_view(_request):
    try:
        catalogs = fetch_admin_academic_catalogs()
    except Exception:
        logger.exception('Unexpected error while loading admin academic catalogs.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando carreras y pensum.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Catálogos académicos cargados.',
            'catalogs': catalogs,
        }
    )


@require_GET
@require_admin_session
@never_cache
def admin_course_cuts_view(_request):
    try:
        cuts = list_course_cuts()
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading course cuts.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando las cohortes.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Cohortes cargadas.', 'cuts': cuts})


@csrf_exempt
@require_POST
@require_admin_session
def admin_course_cut_create_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = create_course_cut(payload, user_login=_dashboard_user_login(request))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while creating course cut.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno creando la cohorte.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Cohorte creada.', 'cut': result}, status=201)


@csrf_exempt
@require_POST
@require_admin_session
def admin_course_cut_update_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = update_course_cut(payload, user_login=_dashboard_user_login(request))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while updating course cut.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno actualizando la cohorte.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Cohorte actualizada.', 'cut': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_course_cut_close_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = close_course_cut(payload, user_login=_dashboard_user_login(request))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while closing course cut.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cerrando la cohorte.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Cohorte cerrada.', 'cut': result})


@require_GET
@require_admin_session
@never_cache
def admin_course_cut_students_view(request):
    try:
        result = list_course_cut_students(request.GET.get('corte_id') or request.GET.get('CorteId'))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading course cut students.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando estudiantes de la corte.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Estudiantes de corte cargados.',
            'result': result,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_course_cut_students_sync_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = sync_course_cut_students(payload, user_login=_dashboard_user_login(request))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while syncing course cut students.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno matriculando estudiantes en educación continua.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Sincronización de estudiantes procesada.',
            'result': result,
        }
    )


@require_GET
@require_admin_session
@never_cache
def admin_course_cut_schedule_view(request):
    try:
        result = list_course_cut_schedule(request.GET.get('corte_id') or request.GET.get('CorteId'))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading course cut schedule.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando horario y Teams.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Horario y Teams cargados.',
            'result': result,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_course_cut_schedule_save_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = save_course_cut_schedule(payload, user_login=_dashboard_user_login(request))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while saving course cut schedule.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno guardando el horario.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Horario guardado y sesiones generadas.',
            'result': result,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_course_cut_module_save_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)
    try:
        result = save_course_cut_module(payload, user_login=_dashboard_user_login(request))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while saving course module.')
        return JsonResponse(
            {'ok': False, 'message': 'Ocurrió un error interno guardando el módulo y sus docentes.'},
            status=500,
        )
    return JsonResponse({'ok': True, 'message': 'Módulo y docentes guardados.', 'result': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_course_cut_teams_sync_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = sync_course_cut_teams(payload, user_login=_dashboard_user_login(request))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while syncing course cut Teams.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno matriculando por Teams.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Proceso de Teams encolado.',
            'result': result,
        }
    )


@require_GET
@require_admin_session
@never_cache
def admin_enrolled_students_view(request):
    try:
        result = list_enrolled_students(
            request.GET.get('corte_id') or request.GET.get('CorteId'),
            search=request.GET.get('q', ''),
            limit=request.GET.get('limit', 300),
        )
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading enrolled students.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando estudiantes matriculados.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Estudiantes matriculados cargados.',
            'result': result,
        }
    )


@require_GET
@require_admin_session
@never_cache
def admin_student_updates_view(request):
    try:
        result = list_students_for_update(
            request.GET.get('corte_id') or request.GET.get('CorteId'),
            search=request.GET.get('q') or request.GET.get('search') or '',
            limit=request.GET.get('limit') or 300,
        )
    except StudentUpdateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading students for update.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando estudiantes para actualización.',
            },
            status=500,
        )
    role = request.dashboard_user.get('role') if isinstance(request.dashboard_user.get('role'), dict) else {}
    result['can_view_credentials'] = str(role.get('name') or '').strip().upper() == 'ADMINISTRADOR'
    return JsonResponse(
        {
            'ok': True,
            'message': 'Estudiantes disponibles para actualización.',
            'result': result,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
@never_cache
def admin_student_credentials_view(request):
    try:
        payload = json.loads(request.body or b'{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'message': 'Envía un JSON válido.'}, status=400)

    try:
        credentials = get_student_migration_credentials(
            payload.get('corte_id') or payload.get('CorteId'),
            payload.get('codigo_estud') or payload.get('CodigoEstud'),
        )
    except StudentUpdateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading student migration credentials.')
        return JsonResponse(
            {'ok': False, 'message': 'No fue posible cargar las credenciales de migración.'},
            status=500,
        )

    logger.warning(
        'Credenciales de migración consultadas por %s para CodigoEstud=%s.',
        _dashboard_user_login(request),
        credentials.get('codigo_estud'),
    )
    response = JsonResponse({'ok': True, 'credentials': credentials})
    response['Cache-Control'] = 'no-store, no-cache, max-age=0, must-revalidate, private'
    response['Pragma'] = 'no-cache'
    return response


@csrf_exempt
@require_POST
@require_admin_session
def admin_student_update_save_view(request):
    try:
        payload = json.loads(request.body or b'{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'message': 'Envía un JSON válido.'}, status=400)

    try:
        result = update_enrolled_student(
            payload,
            user_login=_dashboard_user_login(request),
        )
    except StudentUpdateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while updating enrolled student.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno actualizando la información del estudiante.',
            },
            status=500,
        )
    return JsonResponse({'ok': True, **result})


@require_GET
@require_admin_session
@never_cache
def admin_grade_transfer_view(request):
    try:
        result = list_grade_transfer_students(
            request.GET.get('corte_id') or request.GET.get('CorteId'),
            search=request.GET.get('q', ''),
            limit=request.GET.get('limit', 300),
        )
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading grade transfer students.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando el pase de notas.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Pase de notas cargado.',
            'result': result,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_grade_transfer_save_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = save_grade_transfer(payload, user_login=_dashboard_user_login(request))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while saving grade transfer.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno procesando el pase de notas.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Pase de notas procesado.',
            'result': result,
        }
    )


@require_GET
@require_admin_session
@never_cache
def admin_attendance_view(request):
    try:
        result = list_attendance_students(
            request.GET.get('corte_id') or request.GET.get('CorteId'),
            attendance_date=request.GET.get('fecha') or request.GET.get('date'),
            hour=request.GET.get('hora') or request.GET.get('time'),
        )
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading administrative attendance.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando asistencia.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Asistencia cargada.',
            'result': result,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_attendance_save_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = save_attendance_records(payload, user_login=_dashboard_user_login(request))
    except CourseCutError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while saving administrative attendance.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno guardando asistencia.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Asistencia guardada.',
            'result': result,
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
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
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
                'message': 'Ocurrió un error interno actualizando el estado de la carrera.',
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
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
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
                'message': 'Ocurrió un error interno guardando el pensum.',
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
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
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
                'message': 'Ocurrió un error interno actualizando el estado de la materia.',
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
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
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
                'message': 'Ocurrió un error interno procesando la matrícula masiva.',
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


@require_GET
@require_admin_session
@never_cache
def admin_academic_enrollment_students_view(request):
    try:
        students = list_academic_enrollment_students(
            search=request.GET.get('q', ''),
            limit=request.GET.get('limit', 200),
        )
    except Exception:
        logger.exception('Unexpected error while loading academic enrollment students.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando estudiantes para matrícula académica.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Estudiantes cargados.',
            'students': students,
        }
    )


@require_GET
@require_teacher_session
@never_cache
def teacher_course_dashboard_view(request):
    try:
        dashboard = get_teacher_course_dashboard(request.dashboard_user)
    except TeacherDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=404)
    except Exception:
        logger.exception('Unexpected error while loading teacher course dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando la información docente.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Información docente cargada.',
            'dashboard': dashboard,
        }
    )


@require_GET
@require_teacher_session
@never_cache
def teacher_attendance_dashboard_view(request):
    try:
        dashboard = get_teacher_attendance_dashboard(request.dashboard_user)
    except TeacherDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=404)
    except Exception:
        logger.exception('Unexpected error while loading teacher attendance dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando la asistencia docente.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Asistencia docente cargada.',
            'dashboard': dashboard,
        }
    )


@require_GET
@require_teacher_session
@never_cache
def teacher_schedule_dashboard_view(request):
    try:
        dashboard = get_teacher_schedule_dashboard(request.dashboard_user)
    except TeacherDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=404)
    except Exception:
        logger.exception('Unexpected error while loading teacher schedule dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando el horario docente.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Horario docente cargado.',
            'dashboard': dashboard,
        }
    )


@csrf_exempt
@require_POST
@require_teacher_session
def teacher_schedule_save_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = save_teacher_schedule(request.dashboard_user, payload)
    except TeacherDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while saving teacher schedule.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno guardando el horario docente.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Horario docente guardado.',
            'result': result,
        }
    )


@require_GET
@require_teacher_session
@never_cache
def teacher_attendance_roster_view(request):
    try:
        result = get_teacher_attendance_roster(request.dashboard_user, request.GET)
    except TeacherDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading teacher attendance roster.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando estudiantes para asistencia.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Estudiantes de asistencia cargados.',
            'result': result,
        }
    )


@csrf_exempt
@require_POST
@require_teacher_session
def teacher_attendance_save_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = save_teacher_attendance(request.dashboard_user, payload)
    except TeacherDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while saving teacher attendance.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno guardando la asistencia.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Asistencia guardada.',
            'result': result,
        }
    )


@require_GET
@require_teacher_session
@never_cache
def teacher_grades_dashboard_view(request):
    try:
        dashboard = get_teacher_grades_dashboard(request.dashboard_user)
    except TeacherDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=404)
    except Exception:
        logger.exception('Unexpected error while loading teacher grades dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando las calificaciones docentes.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Calificaciones docentes cargadas.',
            'dashboard': dashboard,
        }
    )


@require_GET
@require_student_session
@never_cache
def student_schedule_dashboard_view(request):
    try:
        dashboard = get_student_schedule_dashboard(request.dashboard_user)
    except StudentDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=404)
    except Exception:
        logger.exception('Unexpected error while loading student schedule dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando el horario estudiantil.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Horario estudiantil cargado.',
            'dashboard': dashboard,
        }
    )


@require_GET
@require_student_session
@never_cache
def student_grades_dashboard_view(request):
    try:
        dashboard = get_student_grades_dashboard(request.dashboard_user)
    except StudentDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=404)
    except Exception:
        logger.exception('Unexpected error while loading student grades dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando las calificaciones estudiantiles.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Calificaciones estudiantiles cargadas.',
            'dashboard': dashboard,
        }
    )


@require_GET
@require_student_session
@never_cache
def student_certificate_download_view(request):
    estudiante_corte_id = request.GET.get('estudiante_corte_id') or request.GET.get('EstudianteCorteId')
    try:
        content, filename = build_student_certificate(request.dashboard_user, estudiante_corte_id)
    except StudentDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except InscriptionCertificateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while generating student certificate.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno generando el certificado estudiantil.',
            },
            status=500,
        )

    response = HttpResponse(content, content_type=CERTIFICATE_CONTENT_TYPE)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_GET
@require_student_session
@never_cache
def student_certificate_preview_view(request):
    estudiante_corte_id = request.GET.get('estudiante_corte_id') or request.GET.get('EstudianteCorteId')
    try:
        content, filename = preview_student_certificate(request.dashboard_user, estudiante_corte_id)
    except StudentDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except InscriptionCertificateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while previewing student certificate.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno generando la vista previa del certificado.',
            },
            status=500,
        )

    response = HttpResponse(content, content_type=CERTIFICATE_CONTENT_TYPE)
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@csrf_exempt
@require_POST
@require_student_session
@never_cache
def student_certificate_send_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    estudiante_corte_id = payload.get('estudiante_corte_id') or payload.get('EstudianteCorteId')
    try:
        result = send_student_certificate(request.dashboard_user, estudiante_corte_id)
    except StudentDashboardError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except InscriptionCertificateError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while sending student certificate.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno enviando el certificado estudiantil.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': result.get('message') or 'Certificado enviado correctamente.',
            'result': result,
        }
    )


@require_GET
@require_admin_session
@never_cache
def admin_teacher_candidates_view(request):
    try:
        teachers = list_teacher_candidates(
            search=request.GET.get('q', ''),
            limit=request.GET.get('limit', 100),
        )
    except TeacherEnrollmentError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while loading teacher candidates.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno cargando docentes.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Docentes cargados.',
            'teachers': teachers,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_academic_enrollment_selected_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = process_selected_student_enrollment(payload)
    except BulkEnrollmentError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while processing selected academic enrollment.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno procesando la matrícula académica seleccionada.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Matrícula académica procesada.',
            'result': result,
        }
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_teacher_identity_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)

    try:
        result = inspect_teacher_identity_by_cedula(payload.get('cedula'), nombre=payload.get('nombre'))
    except TeacherEnrollmentError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while validating teacher identity.')
        return JsonResponse(
            {'ok': False, 'message': 'Ocurrió un error interno validando la cédula.'},
            status=500,
        )
    return JsonResponse({'ok': True, 'message': result['message'], 'result': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_teacher_entry_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = create_teacher_entry_and_send_credentials(payload, user_login=_dashboard_user_login(request))
    except TeacherEnrollmentError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while processing teacher entry.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno procesando el ingreso docente.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Ingreso docente procesado.',
            'result': result,
        },
        status=201,
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_teacher_enrollment_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = enroll_existing_teacher(payload, user_login=_dashboard_user_login(request))
    except TeacherEnrollmentError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while processing teacher enrollment.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno procesando la matrícula docente.',
            },
            status=500,
        )

    return JsonResponse(
        {
            'ok': True,
            'message': 'Matrícula docente procesada.',
            'result': result,
        },
        status=201,
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_payment_info_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = admin_get_payment_info(payload, user_login=_dashboard_user_login(request))
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while requesting payment info from admin dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno consultando la transacción.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Consulta completada.', 'result': result})


@require_GET
@require_admin_session
@never_cache
def admin_payment_operations_view(request):
    try:
        result = list_financial_payment_operations(request.GET.get('cedula', ''))
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error loading financial payment operations.')
        return JsonResponse({'ok': False, 'message': 'No fue posible cargar las operaciones del estudiante.'}, status=500)
    return JsonResponse({'ok': True, 'message': 'Operaciones cargadas.', 'result': result})


@require_GET
@require_admin_session
@never_cache
def admin_payment_operations_links_view(request):
    try:
        result = search_payment_links_for_operations(request.GET.get('q', ''))
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error searching AllDigital payment links.')
        return JsonResponse({'ok': False, 'message': 'No fue posible consultar los enlaces de pago.'}, status=500)
    return JsonResponse({'ok': True, 'message': 'Enlaces cargados.', 'result': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_payment_operations_generate_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)
    try:
        result = create_financial_card_payment(payload, user_login=_dashboard_user_login(request))
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error generating a financial card payment link.')
        return JsonResponse({'ok': False, 'message': 'No fue posible generar el enlace con tarjeta.'}, status=500)
    return JsonResponse({'ok': True, 'message': 'Enlace de pago generado.', 'result': result}, status=201)


@require_GET
@require_admin_session
@never_cache
def admin_registered_payments_view(request):
    try:
        student_codigo = request.GET.get('student_codigo', '').strip()
        student_cedula = request.GET.get('student_cedula', '').strip()
        codigo_estud = request.GET.get('codigo_estud', '').strip()
        if student_codigo or student_cedula:
            result = get_payment_student_profile(student_codigo, cedula=student_cedula)
        elif codigo_estud:
            result = get_registered_user_payment_detail(
                codigo_estud,
                cuenta_id=request.GET.get('cuenta_id', '').strip(),
            )
        else:
            result = list_registered_user_payments(
                search=request.GET.get('q', ''),
                payment_status=request.GET.get('payment_status', 'all'),
                page=request.GET.get('page', 1),
                page_size=request.GET.get('page_size', 25),
            )
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while listing registered user payments.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno consultando los pagos registrados.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Pagos registrados cargados.', 'result': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_payments_reconcile_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8')) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)
    try:
        result = reconcile_pending_all_digital_payments(
            limit=payload.get('limit') or 50,
            force=True,
            user_login=_dashboard_user_login(request),
        )
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error reconciling AllDigital payments.')
        return JsonResponse({'ok': False, 'message': 'No fue posible validar los pagos con AllDigital.'}, status=500)
    return JsonResponse({'ok': True, 'message': 'Validación de pagos completada.', 'result': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_payment_register_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)
    try:
        result = register_continuing_education_payment(
            payload,
            user_login=_dashboard_user_login(request),
        )
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error registering continuing education payment.')
        return JsonResponse(
            {'ok': False, 'message': 'No fue posible registrar el pago en Educación Continua.'},
            status=500,
        )
    return JsonResponse({'ok': True, 'message': 'Pago registrado en INTECEDUCONTINUA.', 'result': result}, status=201)


@csrf_exempt
@require_POST
@require_admin_session
def admin_payment_invoice_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)
    try:
        result = upload_continuing_education_invoice(
            payload,
            user_login=_dashboard_user_login(request),
        )
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error uploading continuing education invoice.')
        return JsonResponse({'ok': False, 'message': 'No fue posible guardar la factura.'}, status=500)
    return JsonResponse(
        {'ok': True, 'message': 'Factura guardada en INTECEDUCONTINUA.', 'result': result},
        status=201,
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_payment_discount_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)
    try:
        result = register_continuing_education_discount(payload, user_login=_dashboard_user_login(request))
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error registering continuing education discount.')
        return JsonResponse({'ok': False, 'message': 'No fue posible registrar el descuento.'}, status=500)
    benefit_name = 'Beca' if result.get('discount_type') in {'BECA', 'BECA_INTEC'} else 'Descuento'
    return JsonResponse(
        {'ok': True, 'message': f'{benefit_name} registrada en INTECEDUCONTINUA.', 'result': result},
        status=201,
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_payment_discount_correction_view(request):
    role = (getattr(request, 'dashboard_user', {}) or {}).get('role') or {}
    if str(role.get('name') or '').strip().upper() != 'ADMINISTRADOR':
        return JsonResponse(
            {'ok': False, 'message': 'Solo el administrador puede corregir descuentos o becas.'},
            status=403,
        )
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)
    try:
        result = correct_continuing_education_discount(
            payload,
            user_login=_dashboard_user_login(request),
        )
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error correcting continuing education discount.')
        return JsonResponse({'ok': False, 'message': 'No fue posible corregir el descuento o beca.'}, status=500)
    return JsonResponse(
        {'ok': True, 'message': 'Descuento o beca corregido en INTECEDUCONTINUA.', 'result': result},
        status=201,
    )


@csrf_exempt
@require_POST
@require_admin_session
def admin_payment_receipt_generate_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'}, status=400)
    try:
        result = generate_all_digital_payment_receipt_document(
            payload.get('inscription_payment_id') or payload.get('solicitud_id')
        )
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error generating All Digital payment receipt.')
        return JsonResponse({'ok': False, 'message': 'No fue posible generar el documento de pago.'}, status=500)
    return JsonResponse({'ok': True, 'message': 'Documento generado y guardado en OneDrive.', 'result': result})


@csrf_exempt
@require_POST
@require_admin_session
def admin_payment_cancel_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse(
            {'ok': False, 'message': 'El cuerpo de la solicitud no es JSON válido.'},
            status=400,
        )

    try:
        result = admin_cancel_payment(payload, user_login=_dashboard_user_login(request))
    except PaymentGatewayError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error while canceling payment from admin dashboard.')
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ocurrió un error interno anulando la transacción.',
            },
            status=500,
        )

    return JsonResponse({'ok': True, 'message': 'Anulacion ejecutada.', 'result': result})


def _dashboard_user_login(request) -> str:
    user = getattr(request, 'dashboard_user', {}) or {}
    return str(user.get('login') or user.get('email') or 'SISTEMA').strip()
