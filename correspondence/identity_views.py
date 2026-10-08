import base64,os,time
from django.shortcuts import render,redirect
from django.contrib.auth import login
from django.core.exceptions import PermissionDenied
from django.utils.http import url_has_allowed_host_and_scheme
from authlib.integrations.django_client import OAuth
from .security import seal,unseal,verify_totp
from .models import MFADevice,ExternalIdentity
from core.models import User
from core import services

def enrollment(request):
    user=request.user if request.user.is_authenticated else None
    if not user:
        if request.session.get('mfa_enroll_until',0)<time.time():return redirect('/login/')
        user=User.objects.filter(pk=request.session.get('mfa_enroll_user'),is_active=True).first()
    if not user:return redirect('/login/')
    import pyotp
    device,created=MFADevice.objects.get_or_create(user=user,defaults={'secret':base64.b64encode(seal(pyotp.random_base32().encode())).decode()})
    error=''
    if request.method=='POST':
        if verify_totp(user,request.POST.get('code',''),activate=True):
            login(request,user,backend='core.auth.EmailPhoneBackend');request.session['mfa_verified']=True
            request.session.pop('mfa_enroll_user',None);request.session.pop('mfa_enroll_until',None)
            services.audit(None,user,'تأكيد المصادقة المتعددة');return redirect('/')
        error='رمز غير صالح أو مستعمل أو تم تقييد المحاولات.'
    uri='' if device.confirmed else pyotp.TOTP(unseal(base64.b64decode(device.secret)).decode()).provisioning_uri(user.email,issuer_name='الإحسان')
    return render(request,'correspondence/mfa.html',{'uri':uri,'error':error})

def oidc_client():
    issuer=os.getenv('OIDC_DISCOVERY_URL','')
    if not issuer.startswith('https://'):raise PermissionDenied('SSO غير مفعل.')
    oauth=OAuth()
    return oauth.register('identity',client_id=os.environ['OIDC_CLIENT_ID'],client_secret=os.environ['OIDC_CLIENT_SECRET'],server_metadata_url=issuer,client_kwargs={'scope':'openid email profile','code_challenge_method':'S256'})

def oidc_start(request):
    return oidc_client().authorize_redirect(request,os.environ['OIDC_CALLBACK_URL'],prompt='login',acr_values=os.getenv('OIDC_ACR_VALUES',''))

def oidc_callback(request):
    token=oidc_client().authorize_access_token(request)
    info=token.get('userinfo')
    if not info or not info.get('sub'):raise PermissionDenied('تعذر التحقق من الهوية.')
    identity=ExternalIdentity.objects.filter(source=os.getenv('OIDC_IDENTITY_SOURCE','oidc'),external_id=info['sub'],user__is_active=True).select_related('user').first()
    if not identity:raise PermissionDenied('الهوية غير مرتبطة بموظف نشط؛ لا ينشأ حساب تلقائي.')
    login(request,identity.user,backend='core.auth.EmailPhoneBackend')
    # OIDC authentication does not silently bypass the local MFA policy.
    request.session['mfa_verified']=False
    services.audit(None,identity.user,'دخول SSO موثق')
    return redirect('/security/enroll/')

from django.views.decorators.http import require_POST
@require_POST
def language(request):
    value=request.POST.get('language')
    if value not in ['ar','en']:raise PermissionDenied('Unsupported language')
    request.session['ui_language']=value
    target=request.headers.get('Referer','/')
    if not url_has_allowed_host_and_scheme(target,{request.get_host()},require_https=request.is_secure()):target='/'
    return redirect(target)
