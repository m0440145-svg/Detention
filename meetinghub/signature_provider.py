"""Fail-closed Parq adapter. Contract must be confirmed with the provider before activation."""
import os,json,hmac,hashlib,uuid
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone
import requests
from .models import *
from . import services,workflow_services as workflow,platform_services as platform

def config():
    values={k:os.getenv('PARQ_'+k,'') for k in ['API_URL','API_KEY','HMAC_KEY','TEST_URL']}
    if any(not values[k] for k in ['API_URL','API_KEY','HMAC_KEY']):raise ValidationError('التوقيع الرسمي معطل؛ إعداد API وAPI Key وHMAC واتفاقية المزود والاختبار مطلوبة.')
    for key in ['API_URL','TEST_URL']:
        if values[key]:
            u=urlsplit(values[key])
            if u.scheme!='https' or u.hostname!='ehsan.parq.sa' or u.username or u.password or u.port not in [None,443]:raise ValidationError('مسار التوقيع يقتصر على HTTPS للمزود ehsan.parq.sa.')
    return values

def headers(conf,body,key):
    stamp=str(int(timezone.now().timestamp()));sig=hmac.new(conf['HMAC_KEY'].encode(),stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    return {'Content-Type':'application/json','Authorization':'Bearer '+conf['API_KEY'],'X-Ehsan-Timestamp':stamp,'X-Ehsan-Signature':sig,'Idempotency-Key':str(key)}

def valid_link(value):
    u=urlsplit(value or '')
    if u.scheme!='https' or u.hostname!='ehsan.parq.sa' or u.username or u.password or u.port not in [None,443]:raise ValidationError('رابط نتيجة غير موثوق.')
    return value

def test_connection(user):
    services.require_admin(user);conf=config()
    if not conf['TEST_URL']:raise ValidationError('حدد PARQ_TEST_URL وفق عقد المزود؛ لا نخمن مسار اختبار.')
    nonce=str(uuid.uuid4());body=json.dumps({'nonce':nonce,'operation':'connection_test'},separators=(',',':')).encode()
    try:
        r=requests.post(conf['TEST_URL'],data=body,headers=headers(conf,body,nonce),timeout=(5,20),allow_redirects=False);r.raise_for_status();d=r.json()
        if not isinstance(d,dict) or d.get('nonce')!=nonce or d.get('authenticated') is not True:raise ValidationError('المزود لم يؤكد اختبار الاتصال الموقع.')
    except (requests.RequestException,ValueError,ValidationError):
        platform.record_provider_test(user,'parq',False,'فشل اختبار المصادقة؛ لا أسرار محفوظة');raise ValidationError('فشل اختبار الاتصال بالتوقيع؛ راجع الإعداد وعقد المزود.')
    return platform.record_provider_test(user,'parq',True,'استجابة مصادقة فعلية مع nonce مطابق',os.getenv('PARQ_ENVIRONMENT','sandbox'))

@transaction.atomic
def prepare_signature(user,revision):
    meeting=MeetingRecord.objects.select_for_update().get(pk=revision.minutes.meeting_id);services.require_manage(user,meeting)
    conf=config()
    if not platform.provider_ready('parq','production'):raise ValidationError('يلزم اختبار اتصال فعلي ناجح قبل التوقيع الرسمي.')
    if os.getenv('PARQ_ENVIRONMENT','sandbox')!='production':raise ValidationError('اختبار sandbox لا يفعّل التوقيع الرسمي للإنتاج.')
    minutes=Minutes.objects.select_for_update().get(pk=revision.minutes_id)
    if revision.version!=minutes.current_version or minutes.status not in ['review','signed','final']:raise ValidationError('أرسل إصدارًا ثابتًا معروضًا للاعتماد أو نهائيًا.')
    people=services.active_members().filter(pk__in=revision.signatories)
    if people.count()!=len(revision.signatories):raise ValidationError('موقع مطلوب غير نشط.')
    req,created=SignatureRequest.objects.get_or_create(revision=revision)
    return req,conf,meeting,people

def start_signature(user,revision):
    req,conf,meeting,people=prepare_signature(user,revision)
    if req.status=='complete' or req.provider_id:return req
    payload={'meeting_id':meeting.pk,'minute_id':revision.pk,'version':revision.version,'content':revision.snapshot,'content_hash':revision.digest,'signatories':[{'membership_id':p.pk,'name':str(p)} for p in people],'request_nonce':str(req.nonce)}
    body=json.dumps(payload,ensure_ascii=False,separators=(',',':'),default=str).encode()
    try:
        response=requests.post(conf['API_URL'],data=body,headers=headers(conf,body,req.nonce),timeout=(5,20),allow_redirects=False);response.raise_for_status();data=response.json()
        if not isinstance(data,dict) or not isinstance(data.get('request_id'),str) or not data['request_id'] or len(data['request_id'])>200:raise ValidationError('استجابة توقيع غير صالحة.')
    except (requests.RequestException,ValueError,ValidationError):
        platform.runtime_failure('parq','تعذر تأكيد طلب التوقيع لدى المزود؛ إعادة التحقق مطلوبة')
        raise ValidationError('تعذر تأكيد طلب التوقيع. إعادة الطلب تستخدم المفتاح نفسه؛ راجع المزود قبل أي طلب مختلف.')
    req.provider_id=data['request_id'];req.status='pending';req.save();workflow.emit(meeting,user,'طلب توقيع رسمي','استجابة مزود فعلية؛ لا شهادة داخلية',{'request':req.pk,'revision':revision.pk,'digest':revision.digest});return req

@transaction.atomic
def callback(body,timestamp,signature):
    conf=config()
    if len(body)>131072:raise ValidationError('رد مزود يتجاوز الحد.')
    try:stamp=int(timestamp)
    except (ValueError,TypeError):raise PermissionDenied('توقيت توقيع غير صالح.')
    if abs(timezone.now().timestamp()-stamp)>300:raise PermissionDenied('رد خارج مهلة التحقق.')
    expected=hmac.new(conf['HMAC_KEY'].encode(),str(stamp).encode()+b'.'+body,hashlib.sha256).hexdigest()
    if not isinstance(signature,str) or not hmac.compare_digest(expected,signature):raise PermissionDenied('توقيع webhook غير صالح.')
    try:data=json.loads(body)
    except ValueError:raise ValidationError('رد JSON غير صالح.')
    if not isinstance(data,dict) or not isinstance(data.get('event_id'),str) or not 1<=len(data['event_id'])<=200:raise ValidationError('معرف حدث مزود مطلوب.')
    try:nonce=uuid.UUID(data.get('request_nonce',''))
    except (ValueError,TypeError):raise ValidationError('معرف الطلب غير صالح.')
    req=SignatureRequest.objects.filter(nonce=nonce).select_related('revision__minutes__meeting').first()
    if not req or data.get('request_id')!=req.provider_id or data.get('content_hash')!=req.revision.digest:raise ValidationError('طلب أو بصمة غير مطابقة.')
    meeting=MeetingRecord.objects.select_for_update().get(pk=req.revision.minutes.meeting_id);req=SignatureRequest.objects.select_for_update().get(pk=req.pk)
    content_hash=hashlib.sha256(body).hexdigest();old=ProviderReceipt.objects.filter(event_id=data['event_id']).first()
    if old:
        if old.request_id!=req.pk or old.digest!=content_hash:raise PermissionDenied('إعادة حدث بمحتوى مختلف.')
        return req
    if req.status=='complete':raise ValidationError('الطلب مكتمل؛ لا تخفيض أو تغيير بعد اكتماله.')
    if data.get('status') not in ['pending','complete','failed']:raise ValidationError('حالة مزود غير صالحة.')
    if data['status']=='complete':
        signers=data.get('signed_memberships')
        if not isinstance(signers,list) or any(not isinstance(n,int) for n in signers) or set(signers)!=set(req.revision.signatories):raise ValidationError('نقص أو اختلاف الموقعين المطلوبين.')
        req.summary_url=valid_link(data.get('summary_url'));req.verification_url=valid_link(data.get('verification_url'))
        if not isinstance(data.get('audit'),list) or len(json.dumps(data['audit']))>50000:raise ValidationError('سجل مزود مطلوب ضمن الحد.')
        # Store a safe provider summary, never arbitrary payload or raw identity/credentials.
        for person in Member.objects.filter(pk__in=req.revision.signatories):MinuteSignature.objects.get_or_create(revision=req.revision,member=person,defaults={'capacity':person.get_role_display(),'method':'parq_verified_hmac_callback','content_digest':req.revision.digest})
        minutes=Minutes.objects.select_for_update().get(pk=req.revision.minutes_id)
        if minutes.current_version==req.revision.version and minutes.status in ['review','signed']:minutes.status='signed';minutes.version+=1;minutes.save()
        req.provider_audit=[{'event':str(e.get('event',''))[:100],'at':str(e.get('at',''))[:50]} for e in data['audit'] if isinstance(e,dict)]
    req.status=data['status'];req.save();ProviderReceipt.objects.create(request=req,event_id=data['event_id'],digest=content_hash)
    workflow.emit(meeting,None,'رد توقيع رسمي موثق','HMAC وتوقيت وبصمة وحدث غير مكرر',{'request':req.pk,'status':req.status,'revision':req.revision_id});return req
