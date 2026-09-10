"""
Базовые настройки АИС «БЗ ГЭТ».

Стек выбран как рекомендация исполнителя (см. раздел «Технологический стек»
плана работ по ТЗ-БЗ-ГЭТ-2026-V2.2) и требует утверждения Заказчиком —
см. открытый вопрос №1 плана.
"""
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "insecure-dev-key")
DEBUG = os.environ.get("DEBUG", "0") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "drf_spectacular",
    "apps.core",
    "apps.iam",
    "apps.documents",
    "apps.templates_bank",
    "apps.audit",
    "apps.search_ocr",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "bz_get"),
        "USER": os.environ.get("POSTGRES_USER", "bz_get"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "bz_get"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "iam.User"

# Argon2id — ТЗ 4.7 требует именно этот алгоритм (RFC 9106) вместо
# дефолтного PBKDF2. PBKDF2 оставлен вторым для чтения старых хэшей при миграции.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# Парольная политика (решение Заказчика, усиление): минимум 14 символов,
# история 10 паролей, проверка по чёрному списку. CommonPasswordValidator
# (встроенный словарь Django из ~20000 самых частых скомпрометированных
# паролей) принят как реализация требования «проверка по чёрному списку» —
# Заказчик не присылал отдельный корпоративный словарь (в отличие,
# например, от тезауруса Smart Search, где файл был прислан явно), поэтому
# собственный список не придуман самостоятельно, использован стандартный
# инструмент Django для этой же цели.
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 14},
    },
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "apps.iam.validators.SpecialCharacterValidator"},
    {"NAME": "apps.iam.validators.PasswordHistoryValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Web GUI (Django Templates + HTMX/Alpine) — вход/выход по имени маршрута,
# не по URL напрямую. Домашняя страница — Smart Search (apps/search_ocr,
# ТЗ 4.4.1); раньше (пока поиска не было) временно вела на /styleguide/.
LOGIN_URL = "iam:login"
LOGIN_REDIRECT_URL = "search_ocr:search"
LOGOUT_REDIRECT_URL = "iam:login"

# Сессии — частичная реализация ТЗ 4.7 (полная política блокировок и
# параллельных сессий запланирована на Этап 3). Таймаут неактивности
# дифференцирован решением Заказчика: 30 минут по умолчанию (личное
# рабочее место), 15 минут — если пользователь отметил вход как терминал
# общего доступа (LoginForm.shared_terminal, apps/iam/views.py явно
# вызывает request.session.set_expiry() при завершении входа — это
# значение здесь работает как дефолт ДО первого такого вызова и как база
# для любой сессии, где set_expiry() не был вызван явно). Различие
# специфично для Web GUI (браузерная сессия сотрудника на конкретном
# устройстве) — в External API/JWT-контуре понятия «рабочее место» и
# «терминал общего доступа» не применимы, там свой отдельный таймаут —
# ACCESS_TOKEN_LIFETIME (SIMPLE_JWT ниже), не связан с этой настройкой.
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

# Два независимых контура (решение Заказчика, пересматривает более
# раннее «всё через DRF»):
# - Web GUI (внутренний, для сотрудников) — Django Templates + HTMX +
#   Alpine.js, серверный рендеринг, сессия + CSRF (apps/*/views.py,
#   apps/*/forms.py). DRF в этом контуре не участвует вообще.
# - External API (интеграции) — DRF + drf-spectacular (OpenAPI),
#   JWT-аутентификация (apps/*/api.py, apps/*/serializers.py). Отдельный
#   от Web механизм входа, но та же бизнес-логика (apps/iam/services.py) —
#   HTTP-слой обоих контуров не должен её дублировать.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "АИС «БЗ ГЭТ» — External API",
    "DESCRIPTION": (
        "REST API для внешних интеграций (не для внутреннего Web GUI — "
        "тот работает на серверном рендеринге без этого API). "
        "Аутентификация — JWT (заголовок Authorization: Bearer <access>)."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Честная граница: logout в Web-контуре не отзывает уже выданные JWT —
# access живёт своим TTL, refresh можно использовать до истечения, пока
# не подключён blacklist (rest_framework_simplejwt.token_blacklist,
# требует отдельного INSTALLED_APPS + миграции) — не сделано в этой
# партии, короткий ACCESS_TOKEN_LIFETIME ниже снижает, а не убирает окно.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
    # Оригиналы НРД — неизменяемый бакет с Object Locking (WORM).
    "originals": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    # Редактируемые копии и бланки — заменяемый бакет, см. apps/core/storage.py.
    "working": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
}
