"""
WSGI config for E_Cont project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'E_Cont.settings')

django_application = get_wsgi_application()


def application(environ, start_response):
    host = environ.get('HTTP_HOST', '')
    hostname, separator, port = host.partition(':')

    if hostname.lower() == 'pasarela_bk.intec.edu.ec':
        environ['HTTP_HOST'] = '127.0.0.1' + (separator + port if separator else '')
        environ['SERVER_NAME'] = '127.0.0.1'

    return django_application(environ, start_response)
