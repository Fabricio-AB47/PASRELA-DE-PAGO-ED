import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue

        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_bool_env(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def get_list_env(key: str) -> list[str]:
    value = os.getenv(key, '')
    return [item.strip() for item in value.split(',') if item.strip()]


def get_required_env(key: str) -> str:
    value = os.getenv(key, '').strip()
    if not value:
        raise ImproperlyConfigured(f'Missing required environment variable: {key}')
    return value


def get_sql_server_database_config() -> dict:
    engine_name = os.getenv('DB_ENGINE', '').strip().lower()
    valid_engines = {'mssql', 'sqlserver', 'sql_server', 'sql-server'}
    if engine_name not in valid_engines:
        raise ImproperlyConfigured(
            "This project only supports SQL Server. Set DB_ENGINE=mssql in .env."
        )

    config = {
        'ENGINE': 'mssql',
        'NAME': get_required_env('DB_NAME'),
        'USER': get_required_env('DB_USER'),
        'PASSWORD': get_required_env('DB_PASSWORD'),
        'HOST': get_required_env('DB_HOST'),
        'PORT': get_required_env('DB_PORT'),
        'OPTIONS': {
            'driver': os.getenv('DB_ODBC_DRIVER', 'ODBC Driver 18 for SQL Server').strip(),
            'host_is_server': True,
        },
    }

    extra_params = os.getenv('DB_ODBC_EXTRA_PARAMS', '').strip()
    if extra_params:
        config['OPTIONS']['extra_params'] = extra_params

    return config


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent.parent
load_env_file(PROJECT_ROOT / '.env')


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = get_bool_env('DEBUG', True)

ALLOWED_HOSTS = get_list_env('DJANGO_ALLOWED_HOSTS')
CSRF_TRUSTED_ORIGINS = get_list_env('DJANGO_CSRF_TRUSTED_ORIGINS')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = get_bool_env('DJANGO_SECURE_SSL_REDIRECT', False)
SESSION_COOKIE_SECURE = get_bool_env('DJANGO_SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = get_bool_env('DJANGO_CSRF_COOKIE_SECURE', not DEBUG)


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'E_Cont.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'E_Cont.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': get_sql_server_database_config()
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
