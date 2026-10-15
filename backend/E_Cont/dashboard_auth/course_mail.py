"""Independent course notices. Preview and sending never generate certificates."""
import logging
import re
from html import escape
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .academic_rules import is_passing_grade
from .admin_certificates import _certificate_payment_accounts
from .course_cuts import list_enrolled_students
from .payments import _build_intec_logo_attachment, _send_graph_mail

logger = logging.getLogger(__name__)
SALT = 'course-mail-preview-v1'
TYPES = {
    'approval': 'Aprobación y gestión del certificado',
    'failed': 'Resultado académico: reprobación',
    'balance': 'Valores pendientes',
}
ACADEMIC_ROLES = {'ADMINISTRADOR', 'ACADEMICO', 'ACADÉMICO', 'SECRETARIA', 'SECRETARÍA'}
PROTECTED_FIELDS = {
    'approval': ['nombre', 'curso', 'nota', 'estado_pagos'],
    'failed': ['nombre', 'nota', 'curso'],
    'balance': ['nombre', 'saldo'],
}


def validate_protected_fields(subject, body, kind):
    if (re.findall(r'\{([^{}]+)\}', subject) != []
            or re.findall(r'\{([^{}]+)\}', body) != PROTECTED_FIELDS[kind]):
        raise CourseMailError('Los campos automáticos están protegidos: no puedes modificarlos, eliminarlos ni sustituir sus valores.')


class CourseMailError(ValueError):
    pass


def allowed_types(user):
    role = str((user.get('role') or {}).get('name') or '').strip().upper()
    if role in ACADEMIC_ROLES:
        return list(TYPES)
    return ['balance'] if role == 'FINANCIERO' else []


def _amount(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else Decimal('0')
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def list_mail_students(corte_id, kind, user):
    if kind not in allowed_types(user):
        raise CourseMailError('No tienes permisos para este tipo de correo.')
    result = list_enrolled_students(corte_id, limit=1000)
    accounts = _certificate_payment_accounts(corte_id) if kind != 'failed' else {}
    students = []
    for source in result['students']:
        student = dict(source)
        account = accounts.get((str(student['codigo_estud']), str(student['corte_id'])), {})
        student.update(account)
        emails = []
        for field in ('correo_intec', 'correo_personal'):
            address = str(student.get(field) or '').strip()
            try:
                validate_email(address)
            except ValidationError:
                continue
            if address.casefold() not in {item.casefold() for item in emails}:
                emails.append(address)
        email = emails[0] if emails else ''
        reason = '' if emails else 'Sin correo válido registrado'
        grade = student.get('nota_final')
        if kind == 'approval' and not is_passing_grade(grade):
            reason = 'Sin nota aprobatoria (7 a 10)'
        if kind == 'failed' and (grade is None or grade == '' or not 0 <= _amount(grade) < 7):
            reason = 'Sin nota reprobatoria (0 a menos de 7)'
        if kind == 'balance' and _amount(account.get('saldo_pendiente')) <= 0:
            reason = 'Sin valores pendientes registrados'
        student.update(email=email, emails=emails, eligible=not reason, reason=reason)
        students.append(student)
    return {'cut': result['cut'], 'students': students, 'types': allowed_types(user)}


def build_message(student, kind, extra=''):
    greeting = f"Estimado/a {student['nombre']}:"
    if kind == 'approval':
        course = student.get('nombre_curso') or student.get('nombre_corte') or 'programa de Educación Continua'
        text = (f"Nos complace informarle que ha aprobado satisfactoriamente {course}, "
                f"con una calificación final de {student['nota_final']}/10.\n\n"
                'Le felicitamos por su esfuerzo, dedicación y compromiso durante su formación.\n\n'
                'Su certificado de aprobación se encuentra en proceso de gestión. Una vez emitido, '
                'será enviado al correo electrónico que tiene registrado en nuestra institución.')
        if _amount(student.get('saldo_pendiente')) > 0:
            text += f"\n\nActualmente registra un saldo pendiente de USD {_amount(student['saldo_pendiente']):.2f}."
    elif kind == 'failed':
        course = student.get('nombre_curso') or student.get('nombre_corte') or 'programa de Educación Continua'
        text = (f"Le informamos que su calificación final registrada es {student['nota_final']}/10.\n\n"
                'De acuerdo con la evaluación académica, no alcanzó el requisito mínimo de aprobación. '
                f'Por este motivo, su resultado en {course} es reprobado y no es posible emitir el certificado de aprobación.\n\n'
                'Si desea solicitar una revisión de su resultado, resolver dudas o conocer las opciones disponibles '
                'para continuar con su formación, comuníquese con Secretaría Académica. '
                'Nuestro equipo le brindará la orientación necesaria sobre los pasos a seguir.')
    else:
        text = ('Al revisar su estado de cuenta, hemos identificado valores pendientes de pago.\n\n'
                f"Saldo pendiente: USD {_amount(student.get('saldo_pendiente')):.2f}.\n\n"
                'Le solicitamos comunicarse con el área financiera de INTEC para consultar el detalle de los valores '
                'pendientes, verificar su estado de cuenta y recibir orientación para regularizar su situación.\n\n'
                'Si ya realizó el pago o envió su comprobante anteriormente, por favor reenvíelo a '
                'DeptCobranzas@intec.edu.ec e incluya su nombre completo y número de cédula. '
                'Esta información permitirá verificar el pago y actualizar su estado de cuenta.\n\n'
                'Agradecemos su atención y colaboración.')
    body = f'{greeting}\n\n{text}'
    if extra:
        body += f'\n\n{extra}'
    body += '\n\nAtentamente,\nEducación Continua · INTEC'
    return {'student_id': str(student['corte_estudiante_id']), 'name': student['nombre'],
            'email': student['email'], 'emails': student['emails'],
            'subject': f'{TYPES[kind]} · INTEC', 'body': body,
            'html': build_notice_html(TYPES[kind], body)}


def build_notice_html(title, body):
    """Match the existing credentials/certificate email layout; escape all text."""
    paragraphs = ''.join(
        '<p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;color:#374151;">'
        + escape(paragraph).replace('\n', '<br />') + '</p>'
        for paragraph in body.split('\n\n')
    )
    logo = ''
    if _build_intec_logo_attachment():
        logo = '''<tr><td align="center" style="padding:24px 28px 8px 28px;background:#ffffff;">
          <img src="cid:intec-logo.png" width="230" alt="INTEC"
          style="display:block;width:230px;max-width:78%;height:auto;border:0;" />
        </td></tr>'''
    return f'''<!doctype html><html lang="es">
      <body style="margin:0;padding:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
          style="background:#f3f4f6;padding:24px 0;">
          <tr><td align="center">
            <table role="presentation" width="620" cellspacing="0" cellpadding="0"
              style="width:100%;max-width:620px;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 8px 26px rgba(15,23,42,0.12);">
              {logo}
              <tr><td style="background:#9B0E0E;padding:20px 28px;color:#ffffff;">
                <h2 style="margin:0;font-size:22px;font-weight:700;">{escape(title)}</h2>
              </td></tr>
              <tr><td style="padding:26px 28px;color:#111827;">{paragraphs}</td></tr>
            </table>
          </td></tr>
        </table>
      </body></html>'''


def preview_mail(payload, user):
    kind = payload.get('kind')
    ids = payload.get('student_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 25:
        raise CourseMailError('Selecciona entre 1 y 25 estudiantes por envío.')
    if any(not isinstance(value, (str, int)) or isinstance(value, bool) for value in ids):
        raise CourseMailError('La selección de estudiantes no es válida.')
    ids = list(dict.fromkeys(str(value) for value in ids))
    extra = payload.get('extra', '')
    if not isinstance(extra, str) or len(extra) > 3000:
        raise CourseMailError('El mensaje adicional admite hasta 3000 caracteres.')
    result = list_mail_students(payload.get('corte_id'), kind, user)
    by_id = {str(s['corte_estudiante_id']): s for s in result['students']}
    edits = payload.get('edits', {})
    if not isinstance(edits, dict) or any(key not in ids for key in edits):
        raise CourseMailError('Las ediciones deben corresponder a los estudiantes seleccionados.')
    messages = []
    originals = []
    template = payload.get('template')
    if template is not None:
        if not isinstance(template, dict):
            raise CourseMailError('La plantilla no es válida.')
        subject, body = template.get('subject'), template.get('body')
        if (not isinstance(subject, str) or not subject.strip() or len(subject) > 250
                or '\r' in subject or '\n' in subject):
            raise CourseMailError('El asunto de la plantilla debe contener entre 1 y 250 caracteres, sin saltos de línea.')
        if not isinstance(body, str) or not body.strip() or len(body) > 10000:
            raise CourseMailError('El texto de la plantilla debe contener entre 1 y 10000 caracteres.')
        validate_protected_fields(subject, body, kind)
    for student_id in ids:
        student = by_id.get(student_id)
        if not student or not student['eligible']:
            raise CourseMailError('La selección contiene estudiantes no habilitados. Actualiza el listado.')
        message = build_message(student, kind, extra.strip())
        originals.append(dict(message))
        effective_template = edits.get(student_id, template)
        if effective_template is not None:
            if not isinstance(effective_template, dict):
                raise CourseMailError('La edición del correo no es válida.')
            subject, body = effective_template.get('subject'), effective_template.get('body')
            if (not isinstance(subject, str) or not subject.strip() or len(subject) > 250
                    or '\r' in subject or '\n' in subject):
                raise CourseMailError('El asunto debe contener entre 1 y 250 caracteres, sin saltos de línea.')
            if not isinstance(body, str) or not body.strip() or len(body) > 10000:
                raise CourseMailError('El texto debe contener entre 1 y 10000 caracteres.')
            validate_protected_fields(subject, body, kind)
            values = {
                'nombre': str(student.get('nombre') or ''),
                'curso': str(student.get('nombre_curso') or student.get('nombre_corte') or 'Educación continua'),
                'nota': str(student.get('nota_final') if student.get('nota_final') is not None else 'Sin nota'),
                'saldo': f"{_amount(student.get('saldo_pendiente')):.2f}",
                'cedula': str(student.get('cedula') or ''),
                'correo': student['email'],
                'estado_pagos': (f"Actualmente registra un saldo pendiente de USD {_amount(student.get('saldo_pendiente')):.2f}."
                                 if _amount(student.get('saldo_pendiente')) > 0 else ''),
            }

            def replace_field(match):
                field = match.group(1)
                if field not in values:
                    raise CourseMailError(f'Campo de plantilla desconocido: {{{field}}}.')
                return values[field]

            subject = re.sub(r'\{([^{}]+)\}', replace_field, effective_template['subject'])
            body = re.sub(r'\{([^{}]+)\}', replace_field, effective_template['body'])
            if extra.strip():
                body += '\n\n' + extra.strip()
            if len(subject) > 250 or '\r' in subject or '\n' in subject or len(body) > 12000:
                raise CourseMailError('El mensaje personalizado supera los límites permitidos.')
            message.update(subject=subject.strip(), body=body,
                           html=build_notice_html(TYPES[kind], body),
                           editable_subject=effective_template['subject'],
                           editable_body=effective_template['body'], field_values=values)
        messages.append(message)
    snapshot = {'kind': kind, 'corte_id': payload.get('corte_id'), 'student_ids': ids,
                'extra': extra.strip(), 'template': template, 'edits': edits, 'originals': originals,
                'messages': messages, 'login': user.get('login'), 'nonce': uuid4().hex}
    logo = _build_intec_logo_attachment()
    return {'messages': messages, 'token': signing.dumps(snapshot, salt=SALT, compress=True),
            'logo_url': f"data:image/png;base64,{logo['contentBytes']}" if logo else ''}


def send_mail_preview(token, user):
    if not isinstance(token, str) or not token:
        raise CourseMailError('Genera y revisa una vista previa antes de enviar.')
    try:
        preview = signing.loads(token, salt=SALT, max_age=900)
    except (signing.BadSignature, TypeError, ValueError):
        raise CourseMailError('La vista previa expiró o es inválida. Genera una nueva.')
    if preview['login'] != user.get('login') or preview['kind'] not in allowed_types(user):
        raise CourseMailError('No tienes permisos para enviar esta vista previa.')
    current = preview_mail(preview, user)
    current_snapshot = signing.loads(current['token'], salt=SALT)
    if (current['messages'] != preview['messages']
            or current_snapshot['originals'] != preview.get('originals')):
        raise CourseMailError('Los datos cambiaron. Actualiza el listado y revisa una nueva vista previa.')
    key = f"course-mail:{preview['nonce']}"
    if not cache.add(key, True, timeout=900):
        raise CourseMailError('Esta vista previa ya fue procesada. Revisa el resultado antes de reenviar.')
    results = []
    for message in preview['messages']:
        try:
            mail_message = {
                'subject': message['subject'],
                'body': {'contentType': 'HTML', 'content': message['html']},
                'toRecipients': [{'emailAddress': {'address': address}} for address in message['emails']],
            }
            logo = _build_intec_logo_attachment()
            if logo:
                mail_message['attachments'] = [logo]
            _send_graph_mail({'message': mail_message, 'saveToSentItems': True, '_skip_default_cc': True})
            results.append({**message, 'ok': True, 'status': 'Aceptado por el servicio de correo'})
            logger.info('Course mail accepted: actor=%s kind=%s cut=%s student=%s',
                        user.get('login'), preview['kind'], preview['corte_id'], message['student_id'])
        except Exception:
            logger.exception('Course mail failed: actor=%s kind=%s student=%s',
                             user.get('login'), preview['kind'], message['student_id'])
            results.append({**message, 'ok': False,
                            'status': 'No se pudo confirmar el envío. Verifica Enviados antes de reintentar.'})
    return {'results': results, 'accepted': sum(r['ok'] for r in results),
            'failed': sum(not r['ok'] for r in results)}
