import os,re,json,hashlib,hmac,secrets
from datetime import timedelta
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from core.models import User,Notification
from .models import *
from . import services,governance_services as gov

FEATURES={
 'parq':('التوقيع الرسمي','عقد ehsan.parq.sa، مسارات API وwebhook، API Key وHMAC، معرفات الموقعين، اختبار sandbox واتفاقية معالجة البيانات'),
 'nafath':('الدخول عبر نفاذ','اعتماد الجهة وحساب مزود الهوية وإعداد OIDC/OAuth وcallback واختبار دخول'),
 'email':('رمز الدخول عبر البريد','SMTP فعلي وبياناته في البيئة، عنوان مرسل معتمد واختبار تسليم'),
 'sms':('الرسائل النصية','حساب SMS وعقد وإعداد API في البيئة واختبار معرف رسالة'),
 'whatsapp':('WhatsApp Business','حساب Business وقوالب معتمدة ورموز ومخطط بيانات واختبار'),
 'video':('Zoom / Teams','OAuth ونطاقات وحساب مؤسسي واختبار تحكم؛ الروابط اليدوية تعمل مستقلة'),
 'hr':('الموارد البشرية','API ومخطط موظفين ومطابقة وصلاحيات واختبار'),
 'finance':('المزامنة المالية','API ومخطط مالي ومطابقة ومصدر موثق واختبار'),
 'volunteer':('المنصة الوطنية للتطوع','API واعتماد رسمي وحقول ساعات واختبار'),
 'storage':('التخزين الخارجي الدائم','مخزن كائنات خاص ومفاتيح بيئة واختبار رفع وتنزيل وإقامة بيانات مناسبة'),
 'ai':('التفريغ والتلخيص الذكي','مزود AI وموافقة خصوصية ومراجعة بشرية واختبار'),
 'semantic':('البحث الدلالي','خدمة فهرسة وتضمين واختبار صلاحيات وحذف الفهرس'),
 'erp':('ERP / DMS','API أو SSO مع مخطط وربط وسجل مطابقة واختبار'),
}

def features():
    for key,(name,requirements) in FEATURES.items():FeatureState.objects.get_or_create(key=key,defaults={'name':name,'requirements':requirements,'message':'بانتظار متطلبات الربط والاختبار'})
    return FeatureState.objects.order_by('pk')

@transaction.atomic
def request_activation(user,key,reason):
    if not user.is_active:raise PermissionDenied()
    features();feature=FeatureState.objects.select_for_update().get(key=key);reason=services.reason_required(reason)
    if ActivationRequest.objects.filter(feature=feature,status__in=['new','review','waiting','approved']).exists():raise ValidationError('يوجد طلب مفتوح لهذه الميزة.')
    row=ActivationRequest.objects.create(feature=feature,requester=user,reason=reason,missing=feature.requirements);services.event('platform:activation',user,'طلب تفعيل',reason,{'request':row.pk,'feature':key});return row

@transaction.atomic
def activation_transition(user,row,status,notes,version,technical_owner=None):
    services.require_admin(user);row=ActivationRequest.objects.select_for_update().get(pk=row.pk);reason=services.reason_required(notes)
    transitions={'new':['review','rejected'],'review':['waiting','approved','rejected'],'waiting':['review','rejected'],'approved':['complete','waiting'],'rejected':[],'complete':[]}
    if row.version!=version or status not in transitions[row.status]:raise ValidationError('انتقال غير صالح أو تعارض طلب.')
    row.status=status;row.notes=notes;row.technical_owner=technical_owner;row.version+=1;row.save();services.event('platform:activation',user,'انتقال طلب',reason,{'request':row.pk,'status':status});return row

@transaction.atomic
def set_feature(user,feature,state,evidence,environment):
    services.require_admin(user);feature=FeatureState.objects.select_for_update().get(pk=feature.pk)
    if state not in ['works','ready_to_connect','disabled','error']:raise ValidationError('حالة غير صالحة.')
    if state=='works':raise ValidationError('حالة يعمل تسجل آليًا من اختبار المزود؛ لا تعتمد بقرار إداري.')
    feature.state=state;feature.message=services.reason_required(evidence);feature.evidence=evidence;feature.environment=environment;feature.save();services.event('platform:features',user,'تحديث حالة وظيفة',evidence,{'feature':feature.key,'state':state});return feature

def provider_ready(key,environment=None):
    rows=FeatureState.objects.filter(key=key,state='works',tested_at__gte=timezone.now()-timedelta(days=30))
    if environment:rows=rows.filter(environment=environment)
    return rows.exists()

@transaction.atomic
def runtime_failure(key,message):
    feature=FeatureState.objects.select_for_update().filter(key=key).first()
    if feature:
        feature.state='error';feature.message=message;feature.save(update_fields=['state','message'])
        services.event('platform:features',None,'فشل اتصال أثناء التشغيل',message,{'feature':key,'previous_test':str(feature.tested_at)})

def record_provider_test(user,key,success,evidence,environment='sandbox'):
    services.require_admin(user);features();f=FeatureState.objects.get(key=key)
    f.state='works' if success else 'error';f.tested_at=timezone.now();f.evidence=evidence;f.environment=environment;f.message='اختبار اتصال ناجح؛ راجع نطاق البيئة' if success else 'فشل اختبار الاتصال';f.save();services.event('platform:features',user,'اختبار اتصال مزود',evidence,{'feature':key,'success':success,'environment':environment});return f

@transaction.atomic
def save_platform(user,content,reason,version):
    services.require_admin(user);reason=services.reason_required(reason);row,_=PlatformSettings.objects.get_or_create(pk=1);row=PlatformSettings.objects.select_for_update().get(pk=1)
    if row.version!=version:raise ValidationError('تعارض إعدادات.')
    needed=['name','about','vision','mission','objectives','contact']
    if any(not str(content.get(k,'')).strip() for k in needed):raise ValidationError('اسم المنصة ومحتواها العام وبيانات التواصل مطلوبة.')
    for key,default in [('primary','#cf6028'),('secondary','#f18927'),('neutral','#afa5a2'),('background','#f9f4ee'),('support','#91877d'),('text','#575650')]:
        value=content.get(key,default);value='#'+value.lstrip('#')
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}',value):raise ValidationError('لون غير صالح.')
        content[key]=value.lower()
    if content.get('logo_url') and not content['logo_url'].startswith('https://'):raise ValidationError('رابط الشعار يجب أن يستخدم HTTPS.')
    last=PlatformRevision.objects.order_by('-version').first();revision=PlatformRevision.objects.create(version=(last.version if last else 0)+1,content=content,created_by=user,reason=reason)
    row.draft=revision;row.version+=1;row.save();services.event('platform:settings',user,'مسودة إعدادات',reason,{'revision':revision.pk,'version':row.version});return row

@transaction.atomic
def publish_platform(user,revision_id,reason,version):
    services.require_admin(user);row=PlatformSettings.objects.select_for_update().get(pk=1);reason=services.reason_required(reason)
    if row.version!=version:raise ValidationError('تعارض إعدادات.')
    revision=PlatformRevision.objects.get(pk=revision_id);row.published=revision;row.version+=1;row.save();services.event('platform:settings',user,'نشر أو استرجاع إعدادات',reason,{'revision':revision.pk});return row

@transaction.atomic
def save_classification(user,level,days,reference,download_allowed,sharing_allowed,version=0):
    services.require_admin(user);reference=services.reason_required(reference)
    if level not in ['public','internal','secret','highly_secret'] or (days is not None and days<1):raise ValidationError('تصنيف أو مدة غير صالحة.')
    row=ClassificationPolicy.objects.filter(level=level).first()
    if row:
        row=ClassificationPolicy.objects.select_for_update().get(pk=row.pk)
        if version!=row.version:raise ValidationError('تعارض السياسة.')
    else:row=ClassificationPolicy(level=level)
    row.days=days;row.reference=reference;row.download_allowed=download_allowed;row.sharing_allowed=sharing_allowed;row.version+=1;row.full_clean();row.save();services.event('platform:classification',user,'سياسة تصنيف واحتفاظ',reference,{'policy':row.pk,'version':row.version});return row

@transaction.atomic
def bind_policy(user,meeting,policy,legal_hold,reason):
    MeetingRecord.objects.select_for_update().get(pk=meeting.pk);services.require_manage(user,meeting);reason=services.reason_required(reason)
    if policy.level in ['secret','highly_secret'] and meeting.secrecy!='secret':raise ValidationError('الاجتماع يجب أن يكون سريًا قبل ربط سياسة سرية.')
    row,_=RecordPolicy.objects.get_or_create(meeting=meeting,defaults={'policy':policy});row.policy=policy;row.legal_hold=legal_hold;row.hold_reason=reason if legal_hold else '';row.expires_at=meeting.created_at+timedelta(days=policy.days) if policy.days else None;row.save();services.event(f'meeting:{meeting.pk}',user,'ربط سياسة أو حجز نظامي',reason,{'policy':policy.pk,'hold':legal_hold});return row

def permit_download(meeting):
    policy=RecordPolicy.objects.filter(meeting=meeting).select_related('policy').first()
    return not policy or policy.policy.download_allowed

@transaction.atomic
def destruction(user,document,reason):
    gov.manage(user,document.committee);Document.objects.select_for_update().get(pk=document.pk);reason=services.reason_required(reason)
    policy=RecordPolicy.objects.filter(meeting=document.meeting).first() if document.meeting_id else None
    if not policy or policy.legal_hold or not policy.expires_at or policy.expires_at>timezone.now():raise ValidationError('الإتلاف يحتاج سياسة منتهية دون حجز نظامي.')
    if not document.file.name or DestructionRequest.objects.filter(document=document).exists():raise ValidationError('طلب إتلاف سابق أو ملف غير متاح.')
    row=DestructionRequest.objects.create(document=document,requester=user,reason=reason);services.event(f'committee:{document.committee_id}',user,'طلب إتلاف ملف',reason,{'request':row.pk,'sha256':document.sha256});return row

@transaction.atomic
def approve_destruction(user,row,reason):
    row=DestructionRequest.objects.select_for_update().select_related('document__committee','document__meeting').get(pk=row.pk);gov.manage(user,row.document.committee);reason=services.reason_required(reason)
    policy=RecordPolicy.objects.filter(meeting=row.document.meeting).first()
    if not policy or policy.legal_hold or not policy.expires_at or policy.expires_at>timezone.now():raise ValidationError('إتلاف مرفوض لسياسة أو حجز.')
    if user.pk==row.requester_id or user.pk==row.document.owner_id or row.executed_at:raise PermissionDenied('الإتلاف يحتاج موافقات مستقلة؛ لا موافقة الرافع أو تكرار التنفيذ.')
    if row.approved_by_id is None:row.approved_by=user
    else:
        if user.pk==row.approved_by_id:raise PermissionDenied('الموافقة الثانية من شخص مستقل.')
        row.executed_by=user;row.executed_at=timezone.now()
        path=row.document.file.name;storage=row.document.file.storage
        # Keep immutable hashes and reference metadata. Physical removal happens after commit.
        transaction.on_commit(lambda:storage.delete(path))
    row.save();services.event(f'committee:{row.document.committee_id}',user,'موافقة إتلاف ملف',reason,{'request':row.pk,'executed':bool(row.executed_at)});return row

def otp_configured():
    return provider_ready('email') and settings.EMAIL_BACKEND=='django.core.mail.backends.smtp.EmailBackend' and bool(getattr(settings,'EMAIL_HOST',''))

@transaction.atomic
def request_otp(identifier):
    if not otp_configured():raise ValidationError('الدخول برمز البريد ينتظر تهيئة SMTP واختبار التسليم؛ استخدم الدخول الحالي.')
    person=User.objects.filter(email__iexact=identifier,is_active=True).first()
    if not person:return None
    if OTPChallenge.objects.filter(user=person,created_at__gte=timezone.now()-timedelta(minutes=15)).count()>=3:return None
    code=''.join(secrets.choice('0123456789') for _ in range(8));row=OTPChallenge(user=person,expires_at=timezone.now()+timedelta(minutes=5));row.code_hash=hmac.new(settings.SECRET_KEY.encode(),(str(row.nonce)+':'+code).encode(),hashlib.sha256).hexdigest();row.save()
    if send_mail('رمز دخول منصة الإحسان',f'رمز الدخول: {code}\nصالح 5 دقائق. لا تشاركه.',settings.DEFAULT_FROM_EMAIL,[person.email],fail_silently=False)!=1:raise ValidationError('لم يؤكد مزود البريد تسليم الرسالة.')
    row.delivered=True;row.save(update_fields=['delivered']);return row

@transaction.atomic
def verify_otp(nonce,code):
    row=OTPChallenge.objects.select_for_update().filter(nonce=nonce).first()
    if not row or row.used_at or row.expires_at<=timezone.now() or row.attempts>=5 or not row.delivered or not row.user.is_active:raise ValidationError('رمز غير صالح أو منتهٍ.')
    row.attempts+=1;row.save(update_fields=['attempts']);expected=hmac.new(settings.SECRET_KEY.encode(),(str(row.nonce)+':'+code).encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,row.code_hash):return None
    row.used_at=timezone.now();row.save(update_fields=['used_at']);return row.user

@transaction.atomic
def save_resource(user,committee,name,capacity,active,reason,version=0):
    services.require_admin(user);Committee.objects.select_for_update().get(pk=committee.pk);reason=services.reason_required(reason)
    row=Resource.objects.filter(committee=committee,name=name).first()
    if row:
        row=Resource.objects.select_for_update().get(pk=row.pk)
        if row.version!=version:raise ValidationError('تعارض إصدار المورد.')
    else:row=Resource(committee=committee,name=name)
    if not 1<=capacity<=10000:raise ValidationError('سعة غير صالحة.')
    if row.pk and (capacity<row.capacity or not active):
        from .governance_services import stamp
        bookings=GovernanceEntry.objects.filter(committee=committee,kind='service',archived=False).exclude(status__in=['cancelled','complete'])
        for booking in bookings:
            if booking.data.get('resource')==name and stamp(booking.data['ends'])>timezone.now():raise ValidationError('لا تقلل السعة أو تعطل المورد قبل إكمال حجوزاته السارية.')
    row.capacity=capacity;row.active=active;row.version+=1;row.full_clean();row.save();services.event('platform:resources',user,'تعريف مورد خدمة',reason,{'resource':row.pk,'capacity':capacity,'version':row.version});return row
