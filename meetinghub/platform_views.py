import os,uuid,hashlib
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.mail import send_mail
from django.conf import settings
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from .models import *
from . import services,platform_services as ops
from .workflow_forms import PlatformForm
from .workflow_views import checked,failure

@login_required
@never_cache
def integrations(request):
    error='';features=ops.features();admin=services.administrative(request.user)
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='request':ops.request_activation(request.user,request.POST.get('feature'),request.POST.get('reason',''))
            elif action=='transition':ops.activation_transition(request.user,get_object_or_404(ActivationRequest,pk=request.POST.get('request')),request.POST.get('status'),request.POST.get('notes',''),int(request.POST.get('version','0')))
            elif action=='state':ops.set_feature(request.user,get_object_or_404(FeatureState,key=request.POST.get('feature')),request.POST.get('state'),request.POST.get('reason',''),request.POST.get('environment',''))
            elif action=='test_parq':
                from .signature_provider import test_connection
                test_connection(request.user)
            elif action=='test_email':
                services.require_admin(request.user)
                if settings.EMAIL_BACKEND!='django.core.mail.backends.smtp.EmailBackend' or not os.getenv('EMAIL_HOST') or not request.user.email:raise ValidationError('يلزم SMTP في البيئة وبريد مسؤول صحيح؛ لا اختبار console أو محاكاة.')
                sent=send_mail('اختبار بريد منصة الاجتماعات','اختبار تسليم فعلي؛ لا يحتوي أسرارًا أو بيانات أعضاء.',settings.DEFAULT_FROM_EMAIL,[request.user.email],fail_silently=False)
                ops.record_provider_test(request.user,'email',sent==1,'استجابة SMTP على رسالة اختبار إلى بريد المسؤول','production')
            else:raise ValidationError('إجراء غير صالح.')
            messages.success(request,'حفظ الإجراء؛ اكتمال الطلب الإداري لا يفعّل التكامل.');return redirect(request.path)
        except (ValidationError,PermissionDenied,ValueError) as exc:error=failure(exc)
        except Exception:
            if request.POST.get('action')=='test_email' and admin:ops.record_provider_test(request.user,'email',False,'تعذر اختبار SMTP؛ راجع الإعداد دون عرض تفاصيل حساسة')
            error='تعذر اختبار المزود؛ راجع إعداداته في البيئة.'
    requests=ActivationRequest.objects.select_related('feature','requester')
    if not admin:requests=requests.filter(requester=request.user)
    return render(request,'meetinghub/integrations.html',{'features':features,'requests':requests,'admin':admin,'error':error})

@login_required
@never_cache
def settings_page(request):
    services.require_admin(request.user);singleton,_=PlatformSettings.objects.get_or_create(pk=1);error='';initial={**(singleton.draft.content if singleton.draft else {}),'version':singleton.version}
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='save':d=checked(PlatformForm(request.POST));reason=d.pop('reason');version=d.pop('version');ops.save_platform(request.user,d,reason,version)
            elif action=='publish':ops.publish_platform(request.user,int(request.POST.get('revision','0')),request.POST.get('reason',''),int(request.POST.get('version','0')))
            elif action=='resource':ops.save_resource(request.user,get_object_or_404(Committee,pk=request.POST.get('committee')),request.POST.get('name',''),int(request.POST.get('capacity','0')),request.POST.get('active')=='on',request.POST.get('reason',''),int(request.POST.get('version','0')))
            elif action=='policy':ops.save_classification(request.user,request.POST.get('level'),int(request.POST['days']) if request.POST.get('days') else None,request.POST.get('reference',''),request.POST.get('download_allowed')=='on',request.POST.get('sharing_allowed')=='on',int(request.POST.get('version','0')))
            else:raise ValidationError('إجراء غير صالح.')
            messages.success(request,'حفظت الإعدادات وأعيدت قراءتها من قاعدة البيانات.');return redirect(request.path)
        except (ValidationError,PermissionDenied,ValueError) as exc:error=failure(exc)
    return render(request,'meetinghub/platform_settings.html',{'form':PlatformForm(request.POST if error and request.POST.get('action')=='save' else None,initial=initial),'settings_row':singleton,'revisions':PlatformRevision.objects.order_by('-version'),'policies':ClassificationPolicy.objects.all(),'resources':Resource.objects.select_related('committee'),'committees':Committee.objects.filter(archived=False),'error':error,'preview':singleton.draft.content if singleton.draft else None})

@never_cache
def public(request):
    singleton=PlatformSettings.objects.filter(pk=1).select_related('published').first()
    return render(request,'meetinghub/public.html',{'content':singleton.published.content if singleton and singleton.published else None})

@login_required
@never_cache
def privacy(request,pk):
    from .workflow_views import record_for
    from . import governance_services as gov
    record=record_for(request.user,pk);services.require_manage(request.user,record);error=''
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='bind':ops.bind_policy(request.user,record,get_object_or_404(ClassificationPolicy,pk=request.POST.get('policy')),request.POST.get('hold')=='on',request.POST.get('reason',''))
            elif action=='destroy':ops.destruction(request.user,get_object_or_404(Document,meeting=record,pk=request.POST.get('document')),request.POST.get('reason',''))
            elif action=='approve_destroy':ops.approve_destruction(request.user,get_object_or_404(DestructionRequest,pk=request.POST.get('request'),document__meeting=record),request.POST.get('reason',''))
            else:raise ValidationError('إجراء غير صالح.')
            return redirect(request.path)
        except (ValidationError,PermissionDenied,ValueError) as exc:error=failure(exc)
    return render(request,'meetinghub/privacy.html',{'record':record,'policies':ClassificationPolicy.objects.all(),'policy':RecordPolicy.objects.filter(meeting=record).first(),'documents':Document.objects.filter(meeting=record),'requests':DestructionRequest.objects.filter(document__meeting=record).select_related('document','requester','approved_by','executed_by'),'error':error})

@never_cache
def otp(request):
    error='';nonce=request.POST.get('nonce','');success=''
    if request.method=='POST':
        ip=hashlib.sha256(('otp:'+request.META.get('REMOTE_ADDR','')).encode()).hexdigest()
        if RSVPAttempt.objects.filter(key=ip,created_at__gte=timezone.now()-timedelta(minutes=15)).count()>=15:return render(request,'meetinghub/otp.html',{'error':'محاولات كثيرة؛ انتظر قليلًا.','available':ops.otp_configured()},status=429)
        RSVPAttempt.objects.create(key=ip)
        try:
            if request.POST.get('action')=='request':
                challenge=ops.request_otp(request.POST.get('email',''));nonce=str(challenge.nonce if challenge else uuid.uuid4());success='إذا كان الحساب مسجلًا، أُرسل رمز إلى بريده. صلاحية الرمز 5 دقائق.'
            elif request.POST.get('action')=='verify':
                user=ops.verify_otp(uuid.UUID(nonce),request.POST.get('code',''))
                if not user:raise ValidationError('رمز غير صالح.')
                login(request,user,backend='django.contrib.auth.backends.ModelBackend');return redirect('meeting-list')
            else:raise ValidationError('إجراء غير صالح.')
        except (ValidationError,ValueError) as exc:error=failure(exc)
        except Exception:error='تعذر إرسال رمز البريد؛ استخدم الدخول الحالي أو راجع مسؤول النظام.'
    return render(request,'meetinghub/otp.html',{'error':error,'success':success,'nonce':nonce,'available':ops.otp_configured()})

@login_required
def help_page(request):
    if request.method=='POST':TourCompletion.objects.update_or_create(user=request.user,defaults={'version':'0.3.0'});return redirect(request.path)
    return render(request,'meetinghub/help.html',{'manager':any(services.can_manage(request.user,c) for c in services.committees_for(request.user)),'admin':services.administrative(request.user),'tour':TourCompletion.objects.filter(user=request.user).first(),'reviewed':'2026-10-11','version':'0.3.0'})
