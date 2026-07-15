import os
import base64
import hashlib
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

ADMIN_URL = os.environ.get("ADMIN_URL", "admin").strip().strip("/")

if not ADMIN_URL:
    ADMIN_URL = "admin"

BASE_DIR = Path(__file__).resolve().parent.parent


def get_bool_env(name, default=False):
    # Env приходит строкой из shell/Docker, поэтому все boolean-настройки
    # читаем через один helper с понятными значениями true/false.
    value = os.environ.get(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_int_env(name, default):
    value = os.environ.get(name)

    if value is None or value == "":
        return default

    return int(value)


def get_float_env(name, default):
    value = os.environ.get(name)

    if value is None or value == "":
        return default

    return float(value)


def get_list_env(name, default=None):
    # Списки вроде ALLOWED_HOSTS удобно передавать одной comma-separated строкой.
    value = os.environ.get(name)

    if value is None:
        return list(default or [])

    return [item.strip() for item in value.split(",") if item.strip()]


DEBUG = get_bool_env("DJANGO_DEBUG", True)

if not DEBUG and ADMIN_URL == "admin":
    raise ImproperlyConfigured("ADMIN_URL must be changed in production.")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")

if not SECRET_KEY:
    # Локальный fallback нужен только для удобного dev-запуска.
    # В production без явного секрета приложение не стартует.
    if DEBUG:
        # Local DEBUG-only fallback; production raises without DJANGO_SECRET_KEY.
        SECRET_KEY = (
            "local-development-secret-key-change-me-before-production-np-pechatniki"
        )  # nosec B105
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required in production.")

DEFAULT_DEV_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]

ALLOWED_HOSTS = get_list_env(
    "DJANGO_ALLOWED_HOSTS",
    DEFAULT_DEV_HOSTS if DEBUG else [],
)

CSRF_TRUSTED_ORIGINS = get_list_env("DJANGO_CSRF_TRUSTED_ORIGINS")

if not DEBUG:
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS is required in production.")

    if any("*" in host for host in ALLOWED_HOSTS):
        raise ImproperlyConfigured(
            "Wildcard hosts are not allowed in production DJANGO_ALLOWED_HOSTS."
        )

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "problems.apps.ProblemsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.middleware.AdminCssMiddleware",
    "config.middleware.DynamicResponseCacheMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

SQLITE_PATH = os.environ.get("DJANGO_SQLITE_PATH")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        # Путь можно переопределить env-переменной, чтобы в будущем вынести
        # SQLite-файл в persistent volume без изменения кода.
        "NAME": Path(SQLITE_PATH) if SQLITE_PATH else BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            # WAL даёт публичным SELECT не блокироваться на коротких
            # записях голосов и sessions. FULL сохраняет надёжную
            # синхронизацию вместо опасного ускорения через OFF.
            "timeout": get_float_env("DJANGO_SQLITE_TIMEOUT", 20),
            "init_command": (
                "PRAGMA journal_mode=WAL; "
                "PRAGMA synchronous=FULL"
            ),
        },
    }
}

# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "ru-ru"

TIME_ZONE = "Europe/Moscow"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

# Публичный URL намеренно не совпадает ни с STATIC_ROOT,
# ни с каталогом, который Nginx видит внутри своего контейнера.
STATIC_URL = "/assets/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # В production collectstatic пишет хешированные имена ресурсов и
        # переписывает CSS url(...), чтобы публичные URL были логическими
        # адресами собранной статики, а не путями исходного проекта.
        "BACKEND": (
            "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
            if not DEBUG
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        ),
    },
}

MEDIA_URL = "/media/"
# MEDIA_ROOT должен быть абсолютным и предсказуемым для nginx/Docker volume.
MEDIA_ROOT = BASE_DIR / "media"


# Security settings for reverse-proxy production deployments.
# В продакшене Django будет работать за reverse proxy, поэтому HTTPS-статус
# берём из X-Forwarded-Proto. Доверять этому заголовку можно только от прокси.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = get_bool_env("DJANGO_USE_X_FORWARDED_HOST", False)

# В DEBUG эти настройки выключены, чтобы локальный HTTP-запуск не ломался.
# При DJANGO_DEBUG=false defaults становятся production-friendly.
SECURE_SSL_REDIRECT = get_bool_env("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = get_bool_env("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = get_bool_env("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = get_int_env("DJANGO_SESSION_COOKIE_AGE", 60 * 60 * 24 * 365)

SECURE_HSTS_SECONDS = get_int_env(
    "DJANGO_SECURE_HSTS_SECONDS",
    0 if DEBUG else 2_592_000,
)
# HSTS включаем только вне DEBUG: на локальном домене и до проверки HTTPS
# эта настройка может надолго закешировать неверное поведение в браузере.
SECURE_HSTS_INCLUDE_SUBDOMAINS = get_bool_env(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
    not DEBUG and SECURE_HSTS_SECONDS > 0,
)
SECURE_HSTS_PRELOAD = get_bool_env(
    "DJANGO_SECURE_HSTS_PRELOAD",
    not DEBUG and SECURE_HSTS_SECONDS > 0,
)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"


# Upload and anti-spam limits.
# Лимиты лежат в settings, чтобы формы, тесты и будущий nginx limit
# можно было держать синхронными без магических чисел в коде.
PROBLEM_PHOTO_MAX_FILES = get_int_env("PROBLEM_PHOTO_MAX_FILES", 5)
PROBLEM_PHOTO_MAX_SIZE = get_int_env("PROBLEM_PHOTO_MAX_SIZE", 10 * 1024 * 1024)
PROBLEM_IMAGE_ALLOWED_FORMATS = get_list_env(
    "PROBLEM_IMAGE_ALLOWED_FORMATS",
    ["JPEG", "PNG", "WEBP"],
)
PROBLEM_IMAGE_MAX_WIDTH = get_int_env("PROBLEM_IMAGE_MAX_WIDTH", 1920)
PROBLEM_IMAGE_MAX_HEIGHT = get_int_env("PROBLEM_IMAGE_MAX_HEIGHT", 1920)
PROBLEM_IMAGE_MAX_PIXELS = get_int_env("PROBLEM_IMAGE_MAX_PIXELS", 16_000_000)
PROBLEM_IMAGE_JPEG_QUALITY = get_int_env("PROBLEM_IMAGE_JPEG_QUALITY", 82)
PROBLEM_IMAGE_WEBP_QUALITY = get_int_env("PROBLEM_IMAGE_WEBP_QUALITY", 82)
PROBLEM_IMAGE_PNG_COMPRESS_LEVEL = get_int_env("PROBLEM_IMAGE_PNG_COMPRESS_LEVEL", 9)
PROBLEM_IMAGE_MIN_SAVING_BYTES = get_int_env("PROBLEM_IMAGE_MIN_SAVING_BYTES", 4096)
PROBLEM_EVIDENCE_MAX_FILES = get_int_env("PROBLEM_EVIDENCE_MAX_FILES", 10)
PROBLEM_EVIDENCE_MAX_SIZE = get_int_env(
    "PROBLEM_EVIDENCE_MAX_SIZE",
    20 * 1024 * 1024,
)
PROBLEM_FORM_MIN_SUBMIT_SECONDS = get_float_env(
    "PROBLEM_FORM_MIN_SUBMIT_SECONDS",
    3,
)
PROBLEM_FORM_TOKEN_MAX_AGE_SECONDS = get_int_env(
    "PROBLEM_FORM_TOKEN_MAX_AGE_SECONDS",
    24 * 60 * 60,
)
PROBLEM_VOTE_MIN_INTERVAL_SECONDS = get_float_env(
    "PROBLEM_VOTE_MIN_INTERVAL_SECONDS",
    1,
)
PROBLEM_LIST_PAGE_SIZE = get_int_env("PROBLEM_LIST_PAGE_SIZE", 12)

# Голос хранится не по IP/User-Agent, а по случайному cookie-токену браузера.
# В базе лежит только HMAC этого токена: утечка SQLite не раскрывает cookie,
# а удаление cookie пользователем честно создаст новый браузерный идентификатор.
PROBLEM_VOTER_COOKIE_NAME = os.environ.get(
    "PROBLEM_VOTER_COOKIE_NAME",
    "np_problem_voter",
)
PROBLEM_VOTER_COOKIE_AGE = get_int_env(
    "PROBLEM_VOTER_COOKIE_AGE",
    60 * 60 * 24 * 400,
)
PROBLEM_VOTER_COOKIE_SECURE = get_bool_env(
    "PROBLEM_VOTER_COOKIE_SECURE",
    not DEBUG,
)
PROBLEM_VOTER_COOKIE_SAMESITE = os.environ.get(
    "PROBLEM_VOTER_COOKIE_SAMESITE",
    "Lax",
)
PROBLEM_VOTER_HMAC_KEY = os.environ.get("PROBLEM_VOTER_HMAC_KEY")

if not PROBLEM_VOTER_HMAC_KEY:
    if DEBUG:
        PROBLEM_VOTER_HMAC_KEY = base64.urlsafe_b64encode(
            hashlib.sha256(
                f"local-voter-hmac:{SECRET_KEY}".encode("utf-8")
            ).digest()
        ).decode("ascii")
    else:
        raise ImproperlyConfigured("PROBLEM_VOTER_HMAC_KEY is required in production.")

# В production файлы отдаёт Nginx только после проверки Django через
# X-Accel-Redirect. В DEBUG можно отключить accel и читать файл Django-ответом,
# чтобы локальный runserver не терял изображения.
PROTECTED_MEDIA_URL = "/protected-media/"
PROTECTED_MEDIA_USE_X_ACCEL = get_bool_env("PROTECTED_MEDIA_USE_X_ACCEL", not DEBUG)
PUBLIC_PHOTO_TOKEN_MAX_AGE = get_int_env(
    "PUBLIC_PHOTO_TOKEN_MAX_AGE",
    30 * 60,
)

FILE_UPLOAD_MAX_MEMORY_SIZE = get_int_env(
    "DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE",
    2_500_000,
)
DATA_UPLOAD_MAX_NUMBER_FILES = get_int_env(
    "DJANGO_DATA_UPLOAD_MAX_NUMBER_FILES",
    PROBLEM_PHOTO_MAX_FILES + PROBLEM_EVIDENCE_MAX_FILES + 10,
)


LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO")

FIELD_ENCRYPTION_KEYS = get_list_env("FIELD_ENCRYPTION_KEYS")

if DEBUG:
    local_field_key = base64.urlsafe_b64encode(
        hashlib.sha256(
            f"local-field-encryption:{SECRET_KEY}".encode("utf-8")
        ).digest()
    ).decode("ascii")
    local_field_key_definition = f"local-dev:{local_field_key}"

    if not FIELD_ENCRYPTION_KEYS:
        FIELD_ENCRYPTION_KEYS = [local_field_key_definition]
    elif not any(key.startswith("local-dev:") for key in FIELD_ENCRYPTION_KEYS):
        FIELD_ENCRYPTION_KEYS.append(local_field_key_definition)
elif not FIELD_ENCRYPTION_KEYS:
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS is required in production.")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "sensitive_values": {
            "()": "config.logging_filters.SensitiveValueFilter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["sensitive_values"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
