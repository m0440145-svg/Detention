"""Versioned integration contracts and durable, idempotent delivery."""
import hashlib,hmac,ipaddress,json,os,socket,urllib.parse
from datetime import timedelta
import requests
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.contrib.sessions.models import Session
from core.models import User,Unit,Role
from core import services as core_services
from .models import *
from . import services

MAX_PAYLOAD=2*1024*1024

def validate_endpoint(endpoint):
    url=urllib.parse.urlsplit(endpoint)
    hosts=set(filter(None,os.getenv('INTEGRATION_ALLOWED_HOSTS','').split(',')))
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.hostname not in hosts:raise ValidationError('عنوان الربط يجب أن يكون HTTPS ضمن قائمة المضيفين المسموحة.')
    for item in socket.getaddrinfo(url.hostname,url.port or 443,type=socket.SOCK_STREAM):
        address=ipaddress.ip_address(item[4][0])
        if not address.is_global:raise ValidationError('عنوان الربط ليس عنوانًا عامًا مسموحًا.')
    return endpoint

def secret(config):
    value=os.getenv(config.secret_env,'')
    if len(value)<32:raise ValidationError('مفتاح الربط غير مهيأ أو قصير.')
    return value

def verify_callback(config,body,timestamp,signature):
    if not config.active:raise PermissionDenied('الربط معطل.')
    try: stamp=int(timestamp)
    except (TypeError,ValueError):raise PermissionDenied('طابع زمني غير صالح.')
    if abs(timezone.now().timestamp()-stamp)>300:raise PermissionDenied('انتهت صلاحية الطلب.')
    expected=hmac.new(secret(config).encode(),str(stamp).encode()+b'.'+body,hashlib.sha256).hexdigest()
    if not isinstance(signature,str) or not hmac.compare_digest(expected,signature):raise PermissionDenied('توقيع الربط غير صالح.')

@transaction.atomic
def sync_hr(source,event_id,payload):
    """No implicit deletions: an explicit HR termination disables access immediately."""
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    previous=SyncReceipt.objects.filter(source=source,event_id=event_id).first()
    if previous:
        if previous.digest!=digest:raise ValidationError('معرف الحدث أعيد بمحتوى مختلف.')
        return previous.summary
    if not event_id or not isinstance(payload,dict):raise ValidationError('حدث موارد بشرية غير صالح.')
    units=payload.get('units',[]);employees=payload.get('employees',[])
    if len(units)>500 or len(employees)>10000:raise ValidationError('دفعة أكبر من الحد المسموح.')
    for row in units:
        uid=str(row['id']); name=row['name'].strip()
        if not name or len(uid)>100:raise ValidationError('بيانات وحدة غير صالحة.')
        link=ExternalUnit.objects.filter(source=source,external_id=uid).first()
        if link: unit=link.unit;unit.name=name;unit.active=bool(row.get('active',True));unit.save()
        else:
            unit=Unit.objects.filter(name=name).first() or Unit.objects.create(name=name)
            ExternalUnit.objects.create(source=source,external_id=uid,unit=unit)
    count=disabled=0
    for row in employees:
        uid=str(row['id']); employee=str(row['employee_number']);email=row.get('email','').strip().lower()
        if not uid or len(uid)>100 or not employee or len(employee)>30:raise ValidationError('رقم موظف غير صالح.')
        link=ExternalIdentity.objects.filter(source=source,external_id=uid).select_related('user').first()
        if link:user=link.user
        else:
            user=User.objects.filter(employee_number=employee).first()
            if user and ExternalIdentity.objects.filter(user=user).exists():raise ValidationError('الموظف مربوط بهوية أخرى.')
            if not user:
                user=User(username='hr-'+hashlib.sha256((source+uid).encode()).hexdigest()[:24],employee_number=employee,email=email,role=Role.EMPLOYEE)
                user.set_unusable_password();user.save()
            ExternalIdentity.objects.create(source=source,external_id=uid,user=user)
        user.first_name=row.get('first_name','')[:150];user.last_name=row.get('last_name','')[:150];user.email=email
        user.employee_number=employee;user.job_title=row.get('job_title','')[:150]
        user.is_active=bool(row.get('active',True)) and not row.get('terminated',False)
        # HR cannot silently grant executive/admin privileges.
        user.full_clean(exclude=['password','phone','manager']);user.save()
        assigned=list(ExternalUnit.objects.filter(source=source,external_id__in=[str(x) for x in row.get('units',[])]).values_list('unit_id',flat=True))
        if len(set(assigned))!=len(set(map(str,row.get('units',[])))):raise ValidationError('وحدة موظف غير معروفة.')
        user.units.set(assigned);ExternalIdentity.objects.filter(user=user).update(synced_at=timezone.now())
        if not user.is_active:
            from oauth2_provider.models import AccessToken,RefreshToken
            AccessToken.objects.filter(user=user).delete();RefreshToken.objects.filter(user=user).delete();disabled+=1
        count+=1
    for row in units:
        link=ExternalUnit.objects.get(source=source,external_id=str(row['id']))
        if row.get('head_id'):
            head=ExternalIdentity.objects.get(source=source,external_id=str(row['head_id'])).user
            if not head.is_active or not head.units.filter(pk=link.unit_id).exists():raise ValidationError('رئيس الوحدة غير صالح.')
            link.unit.head=head;link.unit.save(update_fields=['head'])
    for row in employees:
        if row.get('manager_id'):
            person=ExternalIdentity.objects.get(source=source,external_id=str(row['id'])).user
            person.manager=ExternalIdentity.objects.get(source=source,external_id=str(row['manager_id'])).user
            if person.manager_id==person.pk:raise ValidationError('مدير الموظف لا يكون نفسه.')
            person.save(update_fields=['manager'])
    summary={'employees':count,'disabled':disabled,'units':len(units)}
    SyncReceipt.objects.create(source=source,event_id=event_id,digest=digest,summary=summary)
    core_services.audit(None,None,'مزامنة موارد بشرية',new={'source':source,'event':event_id,**summary})
    return summary

@transaction.atomic
def enqueue(config,event,payload,dedup):
    if not config.active:return None
    if len(json.dumps(payload).encode())>MAX_PAYLOAD:raise ValidationError('حزمة الربط كبيرة.')
    row,_=Delivery.objects.get_or_create(dedup=dedup,defaults={'integration':config,'event':event,'payload':payload})
    return row

def emit_event(mail,event,version):
    # Webhooks contain no content, party or title; protected records never emit an identifier.
    if mail.secrecy in ['restricted','secret']:return
    for config in Integration.objects.filter(active=True,kind='webhook'):
        if event in config.events:enqueue(config,event,{'version':1,'uid':str(mail.uid),'reference':mail.reference,'event':event},f'webhook:{config.pk}:{version}:{event}')

@transaction.atomic
def claim_delivery():
    now=timezone.now()
    row=Delivery.objects.select_for_update().filter(state__in=['pending','retry','sending'],next_attempt__lte=now).filter(models.Q(lease_until__isnull=True)|models.Q(lease_until__lt=now)).order_by('pk').first()
    if row:
        row.state='sending';row.attempts+=1;row.lease_until=now+timedelta(minutes=2);row.save()
    return row

from django.db import models
def deliver_pending(limit=20):
    count=0
    for _ in range(limit):
        row=claim_delivery()
        if not row:break
        try:
            if not row.integration.active:raise ValidationError('Integration disabled')
            endpoint=validate_endpoint(row.integration.endpoint)
            body=json.dumps({'version':1,'id':str(row.uid),'event':row.event,'data':row.payload},ensure_ascii=False,separators=(',',':')).encode()
            stamp=str(int(timezone.now().timestamp())); signature=hmac.new(secret(row.integration).encode(),stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
            if row.integration.kind=='email' and row.integration.options.get('adapter')=='microsoft-graph':
                send_graph(row.integration,row.payload)
                response=type('Response',(),{'status_code':202})()
            else:response=requests.post(endpoint,data=body,headers={'Content-Type':'application/json','X-Ehsan-Timestamp':stamp,'X-Ehsan-Signature':signature,'Idempotency-Key':str(row.uid)},timeout=(5,20),allow_redirects=False)
            if not 200<=response.status_code<300:raise ValidationError('HTTP '+str(response.status_code))
            row.state='delivered';row.delivered_at=timezone.now();row.error='';count+=1
        except (ValidationError,requests.RequestException,OSError) as exc:
            row.state='failed' if row.attempts>=8 else 'retry';row.error=type(exc).__name__ # no credentials, content or response bodies in logs
            row.next_attempt=timezone.now()+timedelta(seconds=min(3600,30*2**row.attempts))
        row.lease_until=None;row.save(update_fields=['state','delivered_at','error','next_attempt','lease_until'])
    return count

def schedule_notifications():
    configs=list(Integration.objects.filter(active=True,kind__in=['email','sms']))
    for alert in MailAlert.objects.filter(created_at__gte=timezone.now()-timedelta(days=1)).select_related('recipient','mail'):
        user=alert.recipient
        if not user.is_active:continue
        pref,_=NotificationPreference.objects.get_or_create(user=user)
        for config in configs:
            if config.kind=='email' and pref.email and not pref.daily_digest:
                enqueue(config,'notification.email',{'to':user.email,'text':'لديك إشعار جديد في منصة الإحسان.','url':'/communications/'},f'notify:{alert.pk}:{config.pk}')
            elif config.kind=='sms' and pref.critical_sms and user.phone and alert.mail.priority=='critical':
                enqueue(config,'notification.sms',{'to':user.phone,'text':'مراسلة عاجلة جداً تحتاج إجراء في منصة الإحسان.'},f'notify:{alert.pk}:{config.pk}')
    today=timezone.localdate()
    for pref in NotificationPreference.objects.filter(email=True,daily_digest=True,user__is_active=True).exclude(last_digest=today).select_related('user'):
        count=MailAlert.objects.filter(recipient=pref.user,created_at__date=today).count()
        for config in configs:
            if config.kind=='email' and count:enqueue(config,'notification.digest',{'to':pref.user.email,'text':f'لديك {count} إشعارات. راجع منصة الإحسان.','url':'/communications/'},f'digest:{pref.user_id}:{config.pk}:{today}')
        if count:pref.last_digest=today;pref.save(update_fields=['last_digest'])

def poll_hr():
    for config in Integration.objects.filter(kind='hr',active=True):
        if config.last_sync and config.last_sync>timezone.now()-timedelta(hours=24):continue
        try:
            endpoint=validate_endpoint(config.endpoint)
            response=requests.get(endpoint,headers={'Authorization':'Bearer '+secret(config)},timeout=(5,20),allow_redirects=False)
            response.raise_for_status()
            if len(response.content)>MAX_PAYLOAD:raise ValidationError('HR payload too large')
            data=response.json();sync_hr(config.key,data['event_id'],data['data'])
            config.last_sync=timezone.now();config.save(update_fields=['last_sync'])
        except (ValidationError,requests.RequestException,OSError,ValueError,KeyError):
            # A failed poll never disables employees based on a missing snapshot.
            continue


def send_graph(config,payload):
    import re,base64
    options=config.options;tenant=options.get('tenant','');client=options.get('client_id','');mailbox=options.get('mailbox','')
    if not re.fullmatch(r'[0-9a-fA-F-]{36}',tenant) or not client or not mailbox:raise ValidationError('Graph configuration incomplete')
    token=requests.post('https://login.microsoftonline.com/'+tenant+'/oauth2/v2.0/token',data={'client_id':client,'client_secret':secret(config),'scope':'https://graph.microsoft.com/.default','grant_type':'client_credentials'},timeout=(5,20),allow_redirects=False)
    token.raise_for_status();access=token.json()['access_token']
    text=payload.get('text','تقرير منصة الإحسان')
    message={'subject':'منصة الإحسان — إشعار','body':{'contentType':'Text','content':text},'toRecipients':[{'emailAddress':{'address':payload['to']}}]}
    if payload.get('headers'):
        from .reports import xlsx_bytes
        data=xlsx_bytes(payload['headers'],payload['rows'])
        message['attachments']=[{'@odata.type':'#microsoft.graph.fileAttachment','name':'report.xlsx','contentType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','contentBytes':base64.b64encode(data).decode()}]
    response=requests.post('https://graph.microsoft.com/v1.0/users/'+urllib.parse.quote(mailbox,safe='')+'/sendMail',headers={'Authorization':'Bearer '+access},json={'message':message,'saveToSentItems':True},timeout=(5,20),allow_redirects=False)
    response.raise_for_status()
