import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .course_cuts import CourseCutError
from .course_mail import CourseMailError, list_mail_students, preview_mail, send_mail_preview
from .security import require_admin_session

logger = logging.getLogger(__name__)


def _respond(action):
    try:
        return JsonResponse({'ok': True, 'result': action()})
    except (CourseMailError, CourseCutError) as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('Course mail request failed')
        return JsonResponse({'ok': False, 'message': 'No se pudo procesar la solicitud de correo.'}, status=500)


def _payload(request):
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        raise CourseMailError('Envía un JSON válido.')
    if not isinstance(data, dict):
        raise CourseMailError('Envía un objeto JSON válido.')
    return data


@require_GET
@require_admin_session
def students_view(request):
    return _respond(lambda: list_mail_students(request.GET.get('corte_id'),
                                              request.GET.get('kind'), request.dashboard_user))


@csrf_exempt
@require_POST
@require_admin_session
def preview_view(request):
    return _respond(lambda: preview_mail(_payload(request), request.dashboard_user))


@csrf_exempt
@require_POST
@require_admin_session
def send_view(request):
    return _respond(lambda: send_mail_preview(_payload(request).get('token'), request.dashboard_user))
