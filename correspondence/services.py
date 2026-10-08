import hashlib,json,re,uuid,csv,io
from datetime import timedelta,datetime,time
from pathlib import Path
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Q,F
from django.utils import timezone
from core.models import User,Role,Source,Priority,Status,Task
from core import services as task_services
from core.policy import GLOBAL_ROLES,tasks_for
from .models import *

TERMINAL={MailStatus.DONE,MailStatus.ARCHIVED,MailStatus.CANCELLED}
OUTGOING=set(Kind.values)-{Kind.IN}


def visible(user):
    qs=Correspondence.objects.select_related('owner','unit','party','classification','classification__retention','created_by','signer','route','template','related','decision').prefetch_related('authorized','cc')
    if not user.is_authenticated or not user.is_active or user.role==Role.ADMIN: return qs.none()
    direct=Q(owner=user)|Q(created_by=user)|Q(authorized=user)|Q(cc=user)|Q(referrals__recipient=user)
    scope=direct|Q(secrecy=Secrecy.PUBLIC)
    if user.role in GLOBAL_ROLES: scope=Q()
    elif user.role in [Role.HEAD,Role.VIEWER]: scope|=Q(unit__in=user.units.all())
    allowed=Q(secrecy__in=[Secrecy.PUBLIC,Secrecy.INTERNAL])|direct
    return qs.filter(scope).filter(allowed).distinct()


def has_access(user,field):
    access=MailAccess.objects.filter(user=user).first()
    return bool(access and getattr(access,field))


def require_visible(user,mail):
    if not visible(user).filter(pk=mail.pk).exists(): raise PermissionDenied('المراسلة غير متاحة لهذا الحساب.')


def can_manage(user,mail):
    return user.is_active and user.role not in [Role.ADMIN,Role.VIEWER,Role.BOARD] and (user.role in GLOBAL_ROLES or (user.role==Role.HEAD and mail.unit.head_id==user.pk) or has_access(user,'register'))


def require_manage(user,mail):
    require_visible(user,mail)
    if not can_manage(user,mail): raise PermissionDenied('هذا الإجراء يتطلب صلاحية إدارة المراسلة.')


def require_edit(user,mail):
    require_visible(user,mail)
    if user.role in [Role.ADMIN,Role.VIEWER,Role.BOARD] or (mail.created_by_id!=user.pk and mail.owner_id!=user.pk and not can_manage(user,mail)): raise PermissionDenied('لا تملك صلاحية تحرير المراسلة.')


def reason_required(reason):
    reason=(reason or '').strip()
    if not reason: raise ValidationError('سبب الإجراء إلزامي.')
    return reason


def event_payload(mail_id,actor_id,action,reason,ip,old,new,previous_hash,created_at):
    return json.dumps({'mail':mail_id,'actor':actor_id,'action':action,'reason':reason,'ip':ip,'old':old,'new':new,'previous_hash':previous_hash,'at':created_at.isoformat()},ensure_ascii=False,sort_keys=True,separators=(',',':'))


@transaction.atomic
def event(mail,user,action,reason,old=None,new=None,ip=''):
    Correspondence.objects.select_for_update().get(pk=mail.pk)
    previous=mail.events.order_by('-pk').first(); previous_hash=previous.digest if previous else ''
    at=timezone.now(); old=old or {}; new=new or {}; actor_id=user.pk if user else None
    digest=hashlib.sha256(event_payload(mail.pk,actor_id,action,reason,ip,old,new,previous_hash,at).encode()).hexdigest()
    row=MailEvent.objects.create(mail=mail,actor=user,action=action,reason=reason,ip=ip,old=old,new=new,previous_hash=previous_hash,digest=digest,created_at=at)
    from .integrations import emit_event
    mapping={'تسجيل مراسلة':'registered','إحالة مراسلة':'referred','إجراء: done':'closed','إجراء: cancel':'closed','تنبيه SLA':'late'}
    if action in mapping: emit_event(mail,mapping[action],row.pk)
    return row


def verify_chain(mail):
    previous=''
    for e in mail.events.order_by('pk'):
        digest=hashlib.sha256(event_payload(e.mail_id,e.actor_id,e.action,e.reason,e.ip,e.old,e.new,e.previous_hash,e.created_at).encode()).hexdigest()
        if e.previous_hash!=previous or e.digest!=digest: return False
        previous=e.digest
    return True


def snapshot(mail):
    fields=['rich_text','summary','keywords','project_reference','program_reference','personal_data','reference','kind','subject','body','external_number','original_date','party_id','classification_id','template_id','unit_id','owner_id','secrecy','priority','status','channel','source_platform','no_attachments','signer_id','route_id','approval_roles','approval_index','related_id','decision_id','due_at','paused_at','signed_at','signature_method','signed_by_id']
    return {k:(getattr(mail,k).isoformat() if hasattr(getattr(mail,k),'isoformat') else getattr(mail,k)) for k in fields}


def work_hours_between(start,end,rules=None):
    if end<=start: return 0.0
    rules=rules or MailSettings.current(); start=timezone.localtime(start); end=timezone.localtime(end)
    holidays=set(Holiday.objects.filter(date__gte=start.date(),date__lte=end.date()).values_list('date',flat=True))
    day=start.date(); total=0
    while day<=end.date():
        if day.weekday() not in rules.weekend and day not in holidays:
            a=timezone.make_aware(datetime.combine(day,time(rules.work_start)),timezone.get_current_timezone())
            b=a+timedelta(hours=rules.work_end-rules.work_start)
            total+=max(0,(min(end,b)-max(start,a)).total_seconds()/3600)
        day+=timedelta(days=1)
    return total


def add_work_hours(start,hours,rules=None):
    rules=rules or MailSettings.current(); current=timezone.localtime(start); remaining=float(hours)
    if remaining<0 or remaining>365*24: raise ValidationError('مدة عمل غير صالحة.')
    holidays=set(Holiday.objects.values_list('date',flat=True))
    for _ in range(4000):
        day=current.date(); a=timezone.make_aware(datetime.combine(day,time(rules.work_start)),timezone.get_current_timezone()); b=a+timedelta(hours=rules.work_end-rules.work_start)
        if day.weekday() not in rules.weekend and day not in holidays:
            current=max(current,a); capacity=max(0,(b-current).total_seconds()/3600)
            if remaining<=capacity: return current+timedelta(hours=remaining)
            remaining-=capacity
        current=timezone.make_aware(datetime.combine(day+timedelta(days=1),time(rules.work_start)),timezone.get_current_timezone())
    raise ValidationError('تعذر احتساب مهلة العمل؛ راجع التقويم.')


@transaction.atomic
def allocate_number(kind,at=None):
    from hijridate import Gregorian
    day=timezone.localtime(at or timezone.now()).date(); year=Gregorian(day.year,day.month,day.day).to_hijri().year
    Sequence.objects.get_or_create(kind=kind,year=year)
    sequence=Sequence.objects.select_for_update().get(kind=kind,year=year)
    sequence.last+=1; sequence.save(update_fields=['last'])
    return f'{kind}-{year}-{sequence.last:06d}'


def validate_people(mail,user):
    if not mail.owner.is_active or mail.owner.role in [Role.ADMIN,Role.VIEWER,Role.BOARD] or not mail.owner.units.filter(pk=mail.unit_id).exists(): raise ValidationError('المسؤول حساب تنفيذي نشط في الوحدة القائدة.')
    if user.role not in GLOBAL_ROLES and not user.units.filter(pk=mail.unit_id).exists(): raise PermissionDenied('الإسناد خارج وحداتك غير مسموح.')
    if mail.signer and (not mail.signer.is_active or mail.signer.role not in [Role.HEAD,Role.ASSISTANT,Role.EXECUTIVE]): raise ValidationError('الموقّع يجب أن يكون مخولًا بالاعتماد.')
    if mail.kind==Kind.IN and (not mail.external_number.strip() or not mail.original_date): raise ValidationError('رقم وتاريخ الخطاب الأصلي إلزاميان للوارد.')
    if mail.template_id and mail.template.kind!=mail.kind: raise ValidationError('القالب لا يطابق نوع المراسلة.')
    if mail.kind==Kind.RPL and (not mail.related_id or mail.related.kind!=Kind.IN): raise ValidationError('الرد يجب أن يرتبط بوارد.')
    if mail.related_id and not visible(user).filter(pk=mail.related_id).exists(): raise PermissionDenied('لا تملك صلاحية المراسلة المرتبطة.')
    if mail.decision_id:
        from core.views import decisions_for
        if not decisions_for(user).filter(pk=mail.decision_id).exists(): raise PermissionDenied('القرار المرتبط غير متاح.')
    if mail.secrecy==Secrecy.SECRET and user.role!=Role.EXECUTIVE: raise PermissionDenied('تصنيف المراسلة السرية وتحديد قائمتها من صلاحية المدير التنفيذي.')
    if not mail.party.active or not mail.unit.active: raise ValidationError('الجهة والوحدة يجب أن تكونا نشطتين.')


@transaction.atomic
def create_mail(user,data,authorized=(),cc=(),upload=None,ip=''):
    if user.role in [Role.ADMIN,Role.VIEWER,Role.BOARD]: raise PermissionDenied('لا تملك صلاحية تسجيل مراسلة.')
    if data.get('kind')==Kind.IN and user.role not in GLOBAL_ROLES|{Role.HEAD} and not has_access(user,'register'): raise PermissionDenied('تسجيل الوارد يتطلب صلاحية الاتصالات الإدارية.')
    safe={k:v for k,v in data.items() if k in EDIT_FIELDS}; mail=Correspondence(**safe,created_by=user)
    if mail.rich_text:
        from .documents import clean_richtext
        mail.body=clean_richtext(mail.body)
    validate_people(mail,user); mail.full_clean(exclude=['reference'])
    rules=MailSettings.current(); now=timezone.now(); mail.created_at=now
    mail.sla_hours=float(rules.sla_days[mail.priority])*(rules.work_end-rules.work_start)
    mail.due_at=add_work_hours(now,mail.sla_hours,rules)
    if mail.external_deadline: mail.due_at=min(mail.due_at,mail.external_deadline)
    mail.referral_due_at=add_work_hours(now,float(rules.referral_hours[mail.priority]),rules)
    if mail.kind==Kind.IN:
        if Correspondence.objects.filter(kind=Kind.IN,party=mail.party,external_number=mail.external_number).exclude(status=MailStatus.CANCELLED).exists(): raise ValidationError('وارد مكرر لنفس الجهة ورقم الخطاب؛ راجع السجل الموجود.')
        mail.status=MailStatus.REGISTERED; mail.reference=allocate_number(mail.kind,now)
    mail.save()
    people=list(authorized); carbon=list(cc)
    if any(not p.is_active or p.role==Role.ADMIN for p in people+carbon): raise ValidationError('القائمة تضم حسابًا غير مخول أو غير نشط.')
    mail.authorized.set(people); mail.cc.set(carbon)
    rebuild_search(mail)
    event(mail,user,'تسجيل مراسلة','تسجيل جديد',new=snapshot(mail),ip=ip)
    if upload: attach(user,mail,upload,'مرفق التسجيل',ip)
    route_unit=mail.classification.route_unit
    if mail.kind==Kind.IN and mail.party.supervisory:
        targets=list(User.objects.filter(role=Role.EXECUTIVE,is_active=True))
        targets+=list(User.objects.filter(mail_access__compliance=True,is_active=True).exclude(role=Role.ADMIN))
    elif mail.kind==Kind.IN and route_unit and route_unit.head_id: targets=[route_unit.head]
    else: targets=[]
    for target in targets:
        if mail.secrecy in [Secrecy.RESTRICTED,Secrecy.SECRET] and not mail.authorized.filter(pk=target.pk).exists(): continue
        Referral.objects.create(mail=mail,recipient=target,instruction='study',note='توجيه تلقائي حسب الجهة/التصنيف',created_by=user)
        alert(mail,target,'routing','مراسلة واردة بانتظار إجراء',f'route:{mail.pk}:{target.pk}')
    if targets and mail.referrals.exists(): mail.status=MailStatus.REFERRED; mail.first_referred_at=now; mail.save(update_fields=['status','first_referred_at'])
    return mail


EDIT_FIELDS=['rich_text','summary','keywords','project_reference','program_reference','personal_data','kind','subject','body','external_number','original_date','party','classification','template','unit','owner','secrecy','priority','channel','source_platform','no_attachments','signer','route','related','decision','external_deadline']
@transaction.atomic
def edit_mail(user,mail,data,reason,ip=''):
    mail=Correspondence.objects.select_for_update().get(pk=mail.pk); require_edit(user,mail); reason=reason_required(reason)
    if mail.status not in [MailStatus.DRAFT,MailStatus.RETURNED,MailStatus.REGISTERED]: raise ValidationError('التعديل متاح للمسودة أو المعادة أو الوارد قبل الإحالة؛ النسخة المعتمدة لا تُعدّل.')
    old=snapshot(mail)
    for k,v in data.items():
        if k not in EDIT_FIELDS or k=='kind': raise ValidationError('الحقل لا يقبل التعديل المباشر.')
        setattr(mail,k,v)
    if mail.rich_text:
        from .documents import clean_richtext
        mail.body=clean_richtext(mail.body)
    validate_people(mail,user); mail.full_clean(); mail.updated_at=timezone.now(); mail.save()
    rebuild_search(mail)
    event(mail,user,'تعديل مراسلة',reason,old,snapshot(mail),ip)
    return mail


@transaction.atomic
def attach(user,mail,upload,reason,ip=''):
    mail=Correspondence.objects.select_for_update().get(pk=mail.pk); require_edit(user,mail); reason=reason_required(reason)
    if mail.status in TERMINAL|{MailStatus.ISSUED,MailStatus.SENT,MailStatus.RECEIVED}: raise ValidationError('النسخة النهائية لا تقبل تغيير المرفقات.')
    task_services.validate_upload(upload)
    digest=hashlib.sha256()
    for chunk in upload.chunks(): digest.update(chunk)
    upload.seek(0); name=Path(upload.name).name; upload.name=uuid.uuid4().hex+Path(name).suffix.lower()
    a=MailAttachment.objects.create(mail=mail,file=upload,name=name,sha256=digest.hexdigest(),size=upload.size,uploaded_by=user)
    event(mail,user,'رفع مرفق',reason,new={'attachment':a.pk,'name':name,'sha256':a.sha256,'size':a.size},ip=ip)
    return a


@transaction.atomic
def refer(user,mail,recipients,instruction,note='',ip='',mode='parallel'):
    mail=Correspondence.objects.select_for_update().get(pk=mail.pk); require_manage(user,mail)
    if mail.kind!=Kind.IN or mail.status not in [MailStatus.REGISTERED,MailStatus.REFERRED,MailStatus.ACTIVE,MailStatus.RETURNED]: raise ValidationError('الإحالة متاحة للوارد في مرحلة المعالجة.')
    if instruction not in dict(Referral._meta.get_field('instruction').choices): raise ValidationError('التأشيرة إلزامية.')
    recipients=list(recipients)
    if not recipients: raise ValidationError('اختر محالًا إليه واحدًا على الأقل.')
    if any(not p.is_active or p.role in [Role.ADMIN,Role.VIEWER,Role.BOARD] for p in recipients): raise ValidationError('اختر حسابات تنفيذية نشطة.')
    if mail.secrecy in [Secrecy.RESTRICTED,Secrecy.SECRET] and any(not visible(p).filter(pk=mail.pk).exists() for p in recipients): raise PermissionDenied('المحال إليه خارج قائمة التصريح؛ يجب أن يصرح له المدير أولًا.')
    if mode not in ['parallel','sequential']: raise ValidationError('نوع الإحالة غير صالح.')
    batch=uuid.uuid4(); result=[]
    for position,recipient in enumerate(recipients):
        if mail.referrals.filter(recipient=recipient,completed_at__isnull=True).exists(): continue
        row=Referral.objects.create(mail=mail,recipient=recipient,instruction=instruction,note=note,created_by=user,mode=mode,batch=batch,position=position); result.append(row)
        if mode=='parallel' or position==0: alert(mail,recipient,'referral','مراسلة محالة إليك',f'referral:{row.pk}')
    if not mail.first_referred_at:mail.first_referred_at=timezone.now()
    old=mail.status; mail.status=MailStatus.REFERRED; mail.updated_at=timezone.now(); mail.save(update_fields=['status','updated_at','first_referred_at'])
    event(mail,user,'إحالة مراسلة',note or dict(Referral._meta.get_field('instruction').choices)[instruction],{'status':old},{'status':mail.status,'recipients':[p.pk for p in recipients],'instruction':instruction,'mode':mode},ip)
    return result


@transaction.atomic
def complete_referral(user,referral,reason,ip=''):
    mail=Correspondence.objects.select_for_update().get(pk=referral.mail_id); require_visible(user,mail); reason=reason_required(reason)
    referral=Referral.objects.select_for_update().get(pk=referral.pk)
    if user.pk!=referral.recipient_id and not can_manage(user,mail): raise PermissionDenied('لا تملك إتمام هذه الإحالة.')
    if mail.status in TERMINAL or referral.completed_at: raise ValidationError('الإحالة مغلقة أو المراسلة نهائية.')
    if referral.mode=='sequential' and mail.referrals.filter(batch=referral.batch,position__lt=referral.position,completed_at__isnull=True).exists(): raise ValidationError('أكمل المرحلة السابقة قبل هذه الإحالة المتسلسلة.')
    if referral.task_id and referral.task.status!=Status.CLOSED: raise ValidationError('المهمة المرتبطة يجب أن تعتمد وتغلق أولًا.')
    referral.completed_at=timezone.now(); referral.save(update_fields=['completed_at'])
    if referral.mode=='sequential':
        next_row=mail.referrals.filter(batch=referral.batch,position__gt=referral.position,completed_at__isnull=True).order_by('position').first()
        if next_row: alert(mail,next_row.recipient,'referral','حان إجراء مرحلتك في الإحالة المتسلسلة',f'referral:{next_row.pk}')
    event(mail,user,'إتمام إحالة',reason,new={'referral':referral.pk},ip=ip)
    return referral


@transaction.atomic
def convert_to_task(user,referral,data,ip=''):
    mail=Correspondence.objects.select_for_update().get(pk=referral.mail_id); require_manage(user,mail)
    referral=Referral.objects.select_for_update().get(pk=referral.pk)
    if referral.task_id: return referral.task
    if referral.completed_at or mail.status in TERMINAL: raise ValidationError('الإحالة النهائية لا تتحول إلى مهمة.')
    if referral.mode=='sequential' and mail.referrals.filter(batch=referral.batch,position__lt=referral.position,completed_at__isnull=True).exists(): raise ValidationError('المرحلة السابقة لم تنجز بعد.')
    owner=referral.recipient
    unit=next((u for u in owner.units.filter(active=True) if user.role in GLOBAL_ROLES or u.head_id==user.pk),None)
    if not unit: raise ValidationError('لا توجد وحدة قائدة مخولة للمحال إليه.')
    due=data.get('due_date') or timezone.localtime(mail.due_at).date()
    if due<timezone.localdate(): raise ValidationError('حدد موعدًا جديدًا صالحًا للمهمة المتأخرة.')
    restricted=mail.secrecy in [Secrecy.RESTRICTED,Secrecy.SECRET]
    task=task_services.create_task(user,{'title':('معالجة إحالة مراسلة محمية' if restricted else mail.subject),'description':('المحتوى متاح للمصرح لهم فقط في وحدة الاتصالات الإدارية.' if restricted else f'مراسلة {mail.code}\n'+mail.body),'unit':unit,'owner':owner,'start_date':timezone.localdate(),'due_date':due,'priority':mail.priority,'confidentiality':'secret' if mail.secrecy==Secrecy.SECRET else 'restricted' if mail.secrecy==Secrecy.RESTRICTED else 'normal','source':Source.INBOUND if mail.kind==Kind.IN else Source.OUTBOUND,'expected_result':'معالجة الإحالة وتوثيق الرد','success_indicator':'اعتماد المخرج وإغلاق المهمة'},[])
    referral.task=task; referral.save(update_fields=['task'])
    event(mail,user,'تحويل إحالة إلى مهمة','إنشاء تكليف مرتبط',new={'referral':referral.pk,'task':task.pk,'due_date':str(due)},ip=ip)
    return task


def actor_is(user,role,mail):
    principals=User.objects.filter(role=role,is_active=True)
    if role==Role.HEAD: principals=principals.filter(pk=mail.unit.head_id)
    if principals.filter(pk=user.pk).exists(): return True
    return user.is_active and user.role in [Role.HEAD,Role.ASSISTANT,Role.EXECUTIVE] and Delegation.objects.filter(principal__in=principals,delegate=user,starts__lte=timezone.now(),ends__gte=timezone.now()).exists()


@transaction.atomic
def transition(user,mail,action,reason,ip='',evidence=''):
    mail=Correspondence.objects.select_for_update().get(pk=mail.pk); require_visible(user,mail); reason=reason_required(reason)
    if user.role in [Role.ADMIN,Role.VIEWER,Role.BOARD]: raise PermissionDenied('الدور للقراءة أو الإعدادات فقط.')
    old=snapshot(mail); current=mail.status
    if action=='submit-incoming-approval':
        require_manage(user,mail)
        if mail.kind!=Kind.IN or current not in ['active','referred']:raise ValidationError('اعتماد الوارد متاح خلال المعالجة.')
        if not mail.route or mail.route.kind!=Kind.IN or not mail.route.active:raise ValidationError('اختر مسار اعتماد وارد معتمدًا.')
        mail.route.full_clean();mail.approval_roles=list(mail.route.levels);mail.approval_index=0;mail.status=MailStatus.APPROVAL
    elif action=='suspend':
        require_manage(user,mail)
        if mail.kind!=Kind.IN or current not in ['registered','referred','active']:raise ValidationError('لا يمكن تعليق الوارد.')
        mail.status=MailStatus.SUSPENDED
    elif action=='unsuspend':
        require_manage(user,mail)
        if current!=MailStatus.SUSPENDED:raise ValidationError('السجل غير معلق.')
        mail.status=MailStatus.ACTIVE
    elif action=='done-override':
        require_manage(user,mail)
        if user.role!=Role.EXECUTIVE or mail.kind!=Kind.IN or current not in ['registered','referred','active','suspended']:raise PermissionDenied('الاستثناء التنفيذي للوارد المفتوح فقط.')
        branches=list(mail.referrals.filter(completed_at__isnull=True).values_list('pk',flat=True))
        mail.referrals.filter(pk__in=branches).update(completed_at=timezone.now())
        event(mail,user,'استثناء إغلاق فروع الإحالة',reason,new={'referrals':branches,'linked_tasks_unchanged':True},ip=ip)
        mail.status=MailStatus.DONE;mail.completed_at=timezone.now()
    elif action=='submit':
        require_edit(user,mail)
        if mail.kind not in OUTGOING or current not in [MailStatus.DRAFT,MailStatus.RETURNED]: raise ValidationError('لا يمكن تقديم هذه المراسلة.')
        if not mail.signer_id or not mail.route_id or not mail.route.active or mail.route.kind!=mail.kind: raise ValidationError('اختر الموقّع ومسار اعتماد نشطًا مطابقًا لنوع المراسلة.')
        mail.route.full_clean()
        if not mail.no_attachments and not mail.attachments.exists(): raise ValidationError('أرفق المستندات أو أكد أن الصادر بدون مرفقات.')
        if not mail.body.strip() and not mail.template_id: raise ValidationError('نص المراسلة أو قالبها إلزامي قبل تقديمها.')
        mail.approval_roles=list(mail.route.levels); mail.approval_index=0; mail.status=MailStatus.REVIEW
    elif action=='review':
        require_manage(user,mail)
        if current!=MailStatus.REVIEW: raise ValidationError('المراسلة ليست في التدقيق.')
        mail.status=MailStatus.APPROVAL
    elif action=='approve':
        if current!=MailStatus.APPROVAL or mail.approval_index>=len(mail.approval_roles): raise ValidationError('لا يوجد اعتماد معلق.')
        if user.pk==mail.created_by_id or not actor_is(user,mail.approval_roles[mail.approval_index],mail): raise PermissionDenied('الاعتماد للمستوى الحالي، دون اعتماد المستخدم لمسودته.')
        mail.approval_index+=1
        if mail.approval_index==len(mail.approval_roles): mail.status=MailStatus.ACTIVE if mail.kind==Kind.IN else MailStatus.SIGNING
    elif action=='reject':
        if current not in [MailStatus.REVIEW,MailStatus.APPROVAL,MailStatus.SIGNING]: raise ValidationError('لا يمكن إعادة هذه المراسلة.')
        if current==MailStatus.APPROVAL:
            if mail.approval_index>=len(mail.approval_roles) or not actor_is(user,mail.approval_roles[mail.approval_index],mail): raise PermissionDenied('الرفض للمستوى الحالي.')
        else: require_manage(user,mail)
        mail.status=MailStatus.RETURNED; mail.approval_index=0; mail.signed_at=None; mail.signature_method=''; mail.signed_by=None
    elif action=='sign':
        if current!=MailStatus.SIGNING or mail.kind not in [Kind.INT,Kind.CIR]: raise ValidationError('التوقيع الداخلي متاح للمذكرات والتعاميم فقط؛ الخطابات الخارجية والقرارات تستخدم مسار التوقيع الموثق من صفحة التوقيع والاستبقاء.')
        policy=MailSettings.current().signature_policy.get(mail.kind,['internal_attestation'])
        methods=policy.get('methods',[]) if isinstance(policy,dict) else policy
        if 'internal_attestation' not in methods:raise ValidationError('السياسة تتطلب مستوى توقيع أعلى.')
        delegate=Delegation.objects.filter(principal_id=mail.signer_id,delegate=user,starts__lte=timezone.now(),ends__gte=timezone.now()).exists()
        if user.pk!=mail.signer_id and not delegate: raise PermissionDenied('التوقيع للموقّع المحدد أو النائب ضمن فترة التفويض.')
        if not evidence.strip(): raise ValidationError('أرفق مرجع دليل التوقيع الداخلي؛ هذا الإجراء ليس شهادة رقمية.')
        mail.signed_at=timezone.now(); mail.signed_by=user; mail.signature_method='internal_attestation'; mail.signature_evidence=evidence
        mail.reference=allocate_number(mail.kind); mail.status=MailStatus.ISSUED
    elif action=='send':
        require_manage(user,mail)
        if current!=MailStatus.ISSUED or not evidence.strip(): raise ValidationError('الإرسال يتطلب صادرًا معتمدًا ومرجع دليل إرسال يدوي.')
        mail.sent_evidence=evidence; mail.status=MailStatus.SENT
    elif action=='receive':
        require_manage(user,mail)
        if current!=MailStatus.SENT or not evidence.strip(): raise ValidationError('إثبات الاستلام يتطلب دليلًا لصادر مرسل.')
        mail.status=MailStatus.RECEIVED
    elif action=='start':
        require_edit(user,mail)
        if mail.kind!=Kind.IN or current not in [MailStatus.REGISTERED,MailStatus.REFERRED,MailStatus.RETURNED]: raise ValidationError('لا يمكن بدء المعالجة.')
        mail.status=MailStatus.ACTIVE
    elif action=='pause':
        require_manage(user,mail)
        if mail.kind!=Kind.IN or current not in [MailStatus.REFERRED,MailStatus.ACTIVE]: raise ValidationError('إيقاف SLA متاح أثناء معالجة الوارد لانتظار جهة خارجية.')
        mail.status=MailStatus.WAITING; mail.paused_at=timezone.now()
    elif action=='resume':
        require_manage(user,mail)
        if current!=MailStatus.WAITING or not mail.paused_at: raise ValidationError('لا يوجد انتظار خارجي موقوف.')
        hours=work_hours_between(mail.paused_at,timezone.now()); mail.paused_hours+=hours; mail.due_at=add_work_hours(mail.due_at,hours); mail.paused_at=None; mail.status=MailStatus.ACTIVE
    elif action=='done':
        require_manage(user,mail)
        if mail.kind!=Kind.IN or current not in [MailStatus.REFERRED,MailStatus.ACTIVE,MailStatus.REGISTERED]: raise ValidationError('لا يمكن إنجاز هذه المراسلة.')
        if mail.referrals.filter(completed_at__isnull=True).exclude(instruction='view').exists(): raise ValidationError('أكمل كل فروع الإحالة قبل إغلاق الوارد.')
        mail.status=MailStatus.DONE
    elif action=='archive':
        require_manage(user,mail)
        if current not in [MailStatus.DONE,MailStatus.SENT,MailStatus.RECEIVED,MailStatus.CANCELLED]: raise ValidationError('الأرشفة بعد الإنجاز أو الإرسال أو الإلغاء.')
        policy=mail.classification.retention
        if not policy.approved or not policy.legal_reference.strip(): raise ValidationError('فئة الاستبقاء تحتاج مرجعًا واعتمادًا قبل الأرشفة.')
        mail.archived_at=timezone.now(); mail.status=MailStatus.ARCHIVED
        if policy.years:
            day=timezone.localdate()
            try: mail.retain_until=day.replace(year=day.year+policy.years)
            except ValueError: mail.retain_until=day.replace(year=day.year+policy.years,day=28)
    elif action=='cancel':
        require_manage(user,mail)
        if current in [MailStatus.ARCHIVED,MailStatus.CANCELLED]: raise ValidationError('المراسلة نهائية.')
        mail.status=MailStatus.CANCELLED; mail.paused_at=None
    else: raise ValidationError('إجراء غير مدعوم.')
    if action=='done':mail.completed_at=timezone.now()
    mail.updated_at=timezone.now(); mail.full_clean(); mail.save()
    rebuild_search(mail)
    event(mail,user,'إجراء: '+action,reason,old,snapshot(mail),ip)
    return mail


@transaction.atomic
def extend_due(user,mail,new_due,reason,ip=''):
    mail=Correspondence.objects.select_for_update().get(pk=mail.pk); require_manage(user,mail); reason=reason_required(reason)
    if mail.status in TERMINAL or new_due<=mail.due_at: raise ValidationError('التمديد يجب أن يلي الموعد الحالي لمراسلة مفتوحة.')
    old=snapshot(mail); mail.due_at=new_due; mail.save(update_fields=['due_at'])
    event(mail,user,'تمديد الاستحقاق',reason,old,snapshot(mail),ip)
    return mail


def rendered_body(mail):
    if not mail.template: return mail.body
    context={'الموضوع':mail.subject,'الجهة_المرسل_إليها':mail.party.name,'اسم_المخاطَب':mail.party.name,'المسمى':'','رقم_الوارد_المرتبط':mail.related.code if mail.related_id else '', 'اسم_الموقّع':str(mail.signer) if mail.signer else '', 'مسمى_الموقّع':mail.signer.job_title if mail.signer else ''}
    return re.sub(r'\{\{\s*([^{}]+?)\s*\}\}',lambda m:context.get(m.group(1),'[متغير غير معروف]'),mail.template.body)+'\n\n'+mail.body


def alert(mail,target,stage,text,key):
    if not target.is_active or not visible(target).filter(pk=mail.pk).exists(): return
    MailAlert.objects.get_or_create(key=key,defaults={'mail':mail,'recipient':target,'stage':stage,'text':text})


@transaction.atomic
def run_mail_rules(now=None):
    now=now or timezone.now(); rules=MailSettings.current(); count=0
    for mail in Correspondence.objects.select_for_update().exclude(status__in=TERMINAL).select_related('unit','owner'):
        if mail.paused_at or not mail.due_at: continue
        total=max(work_hours_between(mail.created_at,mail.due_at,rules)-mail.paused_hours,0.01)
        elapsed=max(0,work_hours_between(mail.created_at,now,rules)-mail.paused_hours); ratio=elapsed/total*100
        targets=[('reminder',rules.reminder_percent,[mail.owner]),('manager',rules.manager_percent,[mail.unit.head] if mail.unit.head else []),('executive',rules.executive_percent,list(User.objects.filter(role=Role.EXECUTIVE,is_active=True)))]
        for stage,threshold,recipients in targets:
            if ratio<threshold: continue
            for recipient in recipients:
                if not visible(recipient).filter(pk=mail.pk).exists(): continue
                key=f'sla:{mail.pk}:{mail.due_at.isoformat()}:{stage}:{recipient.pk}'
                a,created=MailAlert.objects.get_or_create(key=key,defaults={'mail':mail,'recipient':recipient,'stage':stage,'text':f'{mail.code}: '+('تذكير SLA' if stage=='reminder' else 'تصعيد SLA')})
                if created: event(mail,None,'تنبيه SLA','بلوغ نسبة المهلة',new={'recipient':recipient.pk,'stage':stage,'threshold':threshold,'elapsed_percent':round(ratio,1)}); count+=1
        if mail.kind==Kind.IN and not mail.referrals.exists() and now>=mail.referral_due_at:
            target=mail.unit.head or mail.owner
            before=mail.alerts.count(); alert(mail,target,'first_referral','تجاوز مهلة الإحالة الأولى',f'first:{mail.pk}:{target.pk}'); count+=mail.alerts.count()-before
    for row in Referral.objects.filter(completed_at__isnull=True,task__status=Status.CLOSED).select_related('mail'):
        row.completed_at=now; row.save(update_fields=['completed_at']); event(row.mail,None,'مزامنة المهمة المرتبطة','اعتماد وإغلاق المهمة في منصة المهام',new={'referral':row.pk,'task':row.task_id})
    return count


def normalize_arabic(text):
    text=re.sub('[\u064b-\u065f\u0670\u0640]','',str(text or ''))
    return text.translate(str.maketrans({'أ':'ا','إ':'ا','آ':'ا','ى':'ي','ة':'ه'})).casefold()


def rebuild_search(mail):
    texts=[mail.subject,mail.body,mail.party.name,mail.external_number,mail.code]
    texts+=list(OCRJob.objects.filter(attachment__mail=mail,state='done').values_list('text',flat=True))
    mail.search_text=normalize_arabic(' '.join(texts))
    mail.save(update_fields=['search_text'])


@transaction.atomic
def bulk_action(user,ids,action,reason,ip=''):
    reason=reason_required(reason)
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(x,int) or isinstance(x,bool) for x in ids): raise ValidationError('اختر من مراسلة إلى 100 مراسلة.')
    if action not in ['remind','archive']: raise ValidationError('الإجراء الجماعي غير مدعوم.')
    allowed=visible(user).filter(pk__in=set(ids)).values('pk')
    rows=list(Correspondence.objects.select_for_update().filter(pk__in=allowed))
    if len(rows)!=len(set(ids)):raise PermissionDenied('كل السجلات المختارة يجب أن تكون ضمن الصلاحية.')
    for mail in rows:require_manage(user,mail)
    for mail in rows:
        if action=='archive':transition(user,mail,'archive',reason,ip)
        else:
            if mail.status in TERMINAL:raise ValidationError('التذكير لمراسلة مفتوحة فقط.')
            key=f'bulk:{mail.pk}:{user.pk}:{timezone.localdate()}'
            alert(mail,mail.owner,'bulk','تذكير بمعالجة المراسلة',key)
            if not mail.events.filter(action='تذكير جماعي',created_at__date=timezone.localdate(),actor=user).exists():event(mail,user,'تذكير جماعي',reason,new={'recipient':mail.owner_id},ip=ip)
    return {'processed':len(rows)}


@transaction.atomic
def update_access(user,mail,authorized,cc,reason,ip=''):
    mail=Correspondence.objects.select_for_update().get(pk=mail.pk);require_visible(user,mail);reason=reason_required(reason)
    if user.role!=Role.EXECUTIVE:raise PermissionDenied('تغيير قائمة التصريح للمدير التنفيذي فقط.')
    people=list(authorized);carbon=list(cc)
    if any(not p.is_active or p.role==Role.ADMIN for p in people+carbon):raise ValidationError('القائمة تضم حسابًا غير مخول أو غير نشط.')
    old={'authorized':list(mail.authorized.values_list('pk',flat=True)),'cc':list(mail.cc.values_list('pk',flat=True))}
    mail.authorized.set(people);mail.cc.set(carbon)
    event(mail,user,'تغيير قائمة التصريح',reason,old,{'authorized':[p.pk for p in people],'cc':[p.pk for p in carbon]},ip)
