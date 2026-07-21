"""Django settings for Campus ANPR, including live-frame CORS support."""

from pathlib import Path

from corsheaders.defaults import default_headers
from decouple import config


BASE_DIR = Path(__file__).resolve().parent.parent


# Keep these development defaults compatible with the existing project.
# Production deployments should provide them through environment variables.
SECRET_KEY = config(
    "DJANGO_SECRET_KEY",
    default="django-insecure-#!$0jf%!ejoi#2+3zyk4lz%y6wv%r1fczrob==!wt2(^^uu1m6",
)

DEBUG = config("DJANGO_DEBUG", default=True, cast=bool)

ALLOWED_HOSTS = config(
    "DJANGO_ALLOWED_HOSTS",
    default="localhost,127.0.0.1",
    cast=lambda value: [
        host.strip()
        for host in value.split(",")
        if host.strip()
    ],
)


INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_filters",
    "accounts",
    "vehicles",
    "dashboard",
    "records",
    "access_management",
    "notifications",
    "reports",
    "anpr",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
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
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST"),
        "PORT": config("DB_PORT"),
        "OPTIONS": {
            "charset": "utf8mb4",
        },
    }
}


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


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]

# The authenticated live-frame poller uses HTTP ETags so unchanged frames
# return 304 without transferring the JPEG again. ``If-None-Match`` is not
# part of django-cors-headers' default request-header allow-list, therefore it
# must be added explicitly for the Vite frontend's cross-origin requests.
CORS_ALLOW_HEADERS = (
    *default_headers,
    "if-none-match",
)

CORS_EXPOSE_HEADERS = [
    "ETag",
    "X-ANPR-Frame-Sequence",
    "X-ANPR-Published-At",
    "X-ANPR-FPS",
    "X-ANPR-Vehicle-Count",
    "X-ANPR-Tracked-Count",
]

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_PAGINATION_CLASS": (
        "rest_framework.pagination.PageNumberPagination"
    ),
    "PAGE_SIZE": 10,
}


# Shared transport used by Django, the CCTV worker process, and WebSockets.
REDIS_URL = config(
    "REDIS_URL",
    default="redis://127.0.0.1:6379/0",
)

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
            "prefix": "campus_anpr",
            "capacity": 1000,
            "expiry": 10,
            "group_expiry": 60,
        },
    }
}

# The newest frame replaces the previous one; live video must never backlog.
ANPR_LIVE_FRAME_TTL_SECONDS = config(
    "ANPR_LIVE_FRAME_TTL_SECONDS",
    default=5,
    cast=int,
)
ANPR_LIVE_STATUS_TTL_SECONDS = config(
    "ANPR_LIVE_STATUS_TTL_SECONDS",
    default=15,
    cast=int,
)
ANPR_LIVE_EVENT_HISTORY_SIZE = config(
    "ANPR_LIVE_EVENT_HISTORY_SIZE",
    default=100,
    cast=int,
)
ANPR_LIVE_FRAME_JPEG_QUALITY = config(
    "ANPR_LIVE_FRAME_JPEG_QUALITY",
    default=80,
    cast=int,
)


# Continuous vehicle tracking. The repository-owned BoT-SORT configuration
# enables ReID and avoids silently falling back to Ultralytics ByteTrack.
ANPR_VEHICLE_MODEL_PATH = Path(
    config(
        "ANPR_VEHICLE_MODEL_PATH",
        default=str(BASE_DIR / "anpr" / "models" / "yolov8n.pt"),
    )
)
ANPR_CCTV_TRACKER_CONFIG = config(
    "ANPR_CCTV_TRACKER_CONFIG",
    default=str(BASE_DIR / "anpr" / "trackers" / "botsort_reid.yaml"),
)
ANPR_VEHICLE_CONFIDENCE = config(
    "ANPR_VEHICLE_CONFIDENCE",
    default=0.35,
    cast=float,
)
ANPR_VEHICLE_IOU = config(
    "ANPR_VEHICLE_IOU",
    default=0.50,
    cast=float,
)
ANPR_VEHICLE_IMAGE_SIZE = config(
    "ANPR_VEHICLE_IMAGE_SIZE",
    default=640,
    cast=int,
)
ANPR_YOLO_DEVICE = config(
    "ANPR_YOLO_DEVICE",
    default=None,
)


ANPR_YOLO_MODEL_PATH = BASE_DIR / "anpr" / "models" / "best.pt"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
