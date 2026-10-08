import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DEBUG', '0') == '1'
SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG and 'test' not in __import__('sys').argv:
        raise ImproperlyConfigured('Set SECRET_KEY before starting the application')
    SECRET_KEY = 'development-only-never-use-in-production'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
INSTALLED_APPS = ['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','rest_framework','core','correspondence']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[], 'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','core.context.navigation']}}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE':'django.db.backends.sqlite3','NAME':BASE_DIR/'db.sqlite3'}}
if os.getenv('POSTGRES_HOST'):
    DATABASES['default'] = {'ENGINE':'django.db.backends.postgresql','NAME':os.getenv('POSTGRES_DB','detention'),'USER':os.getenv('POSTGRES_USER','detention'),'PASSWORD':os.environ['POSTGRES_PASSWORD'],'HOST':os.environ['POSTGRES_HOST'],'PORT':os.getenv('POSTGRES_PORT','5432')}
if not DEBUG and 'test' not in __import__('sys').argv and not os.getenv('POSTGRES_HOST'):
    raise ImproperlyConfigured('PostgreSQL configuration is required in production')
AUTH_USER_MODEL = 'core.User'
AUTHENTICATION_BACKENDS = ['core.auth.EmailPhoneBackend']
AUTH_PASSWORD_VALIDATORS = [{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':12}},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE = 'ar'
FORMAT_MODULE_PATH = 'config.formats'
USE_THOUSAND_SEPARATOR = False
TIME_ZONE = 'Asia/Riyadh'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR/'staticfiles'
MEDIA_ROOT = BASE_DIR/'media'
MEDIA_URL = '/protected-media/'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 28800
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
CSRF_TRUSTED_ORIGINS = [v for v in os.getenv('CSRF_TRUSTED_ORIGINS','').split(',') if v]
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES':['rest_framework.authentication.SessionAuthentication'],'DEFAULT_PERMISSION_CLASSES':['rest_framework.permissions.IsAuthenticated'],'DEFAULT_PAGINATION_CLASS':'rest_framework.pagination.PageNumberPagination','PAGE_SIZE':30,'DEFAULT_THROTTLE_CLASSES':['rest_framework.throttling.UserRateThrottle'],'DEFAULT_THROTTLE_RATES':{'user':'300/hour'}}
LOGGING = {'version':1,'disable_existing_loggers':False,'handlers':{'console':{'class':'logging.StreamHandler'}},'root':{'handlers':['console'],'level':'INFO'}}
REST_FRAMEWORK['EXCEPTION_HANDLER'] = 'core.api.exceptions'
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
if os.getenv('TRUST_PROXY') == '1':
    # Enable only behind the bundled proxy; never expose the app port publicly.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
