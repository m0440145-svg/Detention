"""Fail-closed production controls; keys remain in environment, not database."""
import base64, hashlib, ipaddress, os, time
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.shortcuts import redirect
from django.db import transaction
from django.utils import timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .models import MFADevice, APICallLog

MAGIC=b'EHSAN-AES256GCM-1\x00'
def encryption_key():
    value=os.getenv('DATA_ENCRYPTION_KEY','')
    if not value:
        if settings.DEBUG: return hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        raise ValidationError('DATA_ENCRYPTION_KEY مطلوب في التشغيل الفعلي.')
    try: key=base64.b64decode(value,validate=True)
    except ValueError: raise ValidationError('مفتاح التشفير غير صالح.')
    if len(key)!=32: raise ValidationError('مفتاح التشفير يجب أن يكون 32 بايت.')
    return key

def seal(data):
    nonce=os.urandom(12)
    return MAGIC+nonce+AESGCM(encryption_key()).encrypt(nonce,data,MAGIC)

def unseal(data):
    if not data.startswith(MAGIC):
        if settings.DEBUG or os.getenv('ALLOW_LEGACY_PLAINTEXT')=='1':return data
        raise ValidationError('ملف قديم غير مشفر؛ نفذ ترحيل التشفير قبل التشغيل.')
    return AESGCM(encryption_key()).decrypt(data[len(MAGIC):len(MAGIC)+12],data[len(MAGIC)+12:],MAGIC)

class EncryptedStorage(FileSystemStorage):
    def _save(self,name,content):
        raw=b''.join(content.chunks())
        return super()._save(name,ContentFile(seal(raw)))
    def _open(self,name,mode='rb'):
        with super()._open(name,'rb') as stream: data=unseal(stream.read())
        return ContentFile(data,name=name)
    def size(self,name):
        with self._open(name) as f:return f.size

def scan_upload(upload):
    """ClamAV via local socket/CLI; production rejects unscanned uploads."""
    import shutil,subprocess,tempfile
    scanner=shutil.which('clamscan')
    if not scanner:
        if settings.DEBUG:return
        raise ValidationError('فحص مكافحة البرمجيات الضارة غير متاح؛ رُفض الرفع.')
    with tempfile.NamedTemporaryFile(prefix='scan-') as f:
        for chunk in upload.chunks(): f.write(chunk)
        f.flush(); result=subprocess.run([scanner,'--no-summary',f.name],capture_output=True,timeout=60)
    upload.seek(0)
    if result.returncode:raise ValidationError('رُفض الملف: إصابة أو تعذر الفحص.')

@transaction.atomic
def verify_totp(user,code,activate=False):
    import pyotp
    device=MFADevice.objects.select_for_update().filter(user=user).first()
    if not device or (not device.confirmed and not activate):return False
    now=timezone.now()
    if device.locked_until and device.locked_until>now:return False
    if not isinstance(code,str) or not code.isascii() or not code.isdigit() or len(code)!=6:return False
    secret=unseal(base64.b64decode(device.secret)).decode()
    totp=pyotp.TOTP(secret); step=int(now.timestamp())//30
    valid_step=next((n for n in [step-1,step,step+1] if n>device.last_step and pyotp.utils.strings_equal(totp.at(n*30),code)),None)
    if valid_step is None:
        device.failed_attempts+=1
        if device.failed_attempts>=5:device.locked_until=now+timedelta(minutes=15)
        device.save();return False
    device.last_step=valid_step;device.failed_attempts=0;device.locked_until=None
    if activate:device.confirmed=True
    device.save();return True

def mfa_required():return settings.MFA_REQUIRED

def check_login_factor(request,user,code):
    if not mfa_required():return True
    if not MFADevice.objects.filter(user=user,confirmed=True).exists():
        request.session['mfa_enroll_user']=user.pk
        request.session['mfa_enroll_until']=int(time.time())+300
        return False
    return verify_totp(user,code)

class SecurityMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        started=time.monotonic()
        request.ui_language=request.session.get("ui_language","ar")
        if request.user.is_authenticated:
            request.user.ui_language=request.ui_language
            if settings.MFA_REQUIRED and not request.session.get('mfa_verified') and not request.path.startswith(('/security/','/logout/','/api/auth/logout/')):
                # OAuth bearer requests are validated separately by the API authenticator.
                if not request.META.get('HTTP_AUTHORIZATION','').startswith('Bearer '):
                    if request.path.startswith('/api/'):return JsonResponse({'detail':'MFA required'},status=403)
                    return redirect('/security/enroll/')
            last=request.session.get('last_activity',int(time.time()))
            if time.time()-last>settings.SESSION_IDLE_SECONDS:
                from django.contrib.auth import logout
                logout(request);return redirect('/login/')
            request.session['last_activity']=int(time.time())
        if request.path.startswith('/admin/') and settings.ADMIN_IP_NETWORKS:
            try: allowed=any(ipaddress.ip_address(request.META.get('REMOTE_ADDR','')) in ipaddress.ip_network(x) for x in settings.ADMIN_IP_NETWORKS)
            except ValueError:allowed=False
            if not allowed:return JsonResponse({'detail':'Admin network denied'},status=403)
        response=self.get_response(request)
        if request.path.startswith('/api/'):
            APICallLog.objects.create(actor=request.user if request.user.is_authenticated else None,method=request.method,path=request.path[:200],status=response.status_code,duration_ms=int((time.monotonic()-started)*1000))
        return response

from oauth2_provider.contrib.rest_framework import OAuth2Authentication
class ScopedOAuthAuthentication(OAuth2Authentication):
    def authenticate_header(self,request):
        return 'Bearer' if request.META.get('HTTP_AUTHORIZATION','').startswith('Bearer ') else None
    def authenticate(self,request):
        result=super().authenticate(request)
        if result:
            user,token=result
            if not user or not user.is_active:raise PermissionDenied('Inactive API principal')
            scope='read' if request.method in ['GET','HEAD','OPTIONS'] else 'write'
            if scope not in token.scope.split():raise PermissionDenied('OAuth scope denied')
        return result

from rest_framework.throttling import BaseThrottle
class DatabaseUserThrottle(BaseThrottle):
    """Shared across web workers; completed calls are counted without content logging."""
    def allow_request(self,request,view):
        if not request.user.is_authenticated:return True
        since=timezone.now()-timedelta(hours=1)
        return APICallLog.objects.filter(actor=request.user,created_at__gte=since).count()<300
    def wait(self):return 60
