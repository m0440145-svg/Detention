import base64,hashlib,hmac,json,math
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.models import Meeting,Role
from core.policy import GLOBAL_ROLES
from correspondence.security import seal
from .models import *


def administrative(user):return user.is_active and user.role==Role.ADMIN

def require_admin(user):
    if not administrative(user):raise PermissionDenied('إدارة الأعضاء وأدوارهم واللجان لمسؤول النظام فقط.')

def active_members(on=None):
    on=on or timezone.localdate()
    return Member.objects.select_related('user').filter(archived=False,user__is_active=True,starts__lte=on).exclude(user__role=Role.ADMIN).filter(Q(ends__isnull=True)|Q(ends__gte=on))

def committees_for(user):
    qs=Committee.objects.select_related('chair__user','secretary__user').prefetch_related('members__user')
    if not user.is_active:return qs.none()
    if administrative(user) or user.role in GLOBAL_ROLES:return qs
    return qs.filter(members__in=active_members().filter(user=user)).distinct()

def can_manage(user,committee):
    if not user.is_active or user.role==Role.ADMIN:return False
    if user.role in GLOBAL_ROLES:return True
    return active_members().filter(user=user).filter(Q(pk=committee.chair_id,role=MemberRole.CHAIR)|Q(pk=committee.secretary_id,role=MemberRole.SECRETARY)).exists()

def records_for(user,include_archived=False):
    qs=MeetingRecord.objects.select_related('meeting','committee','committee__chair__user','committee__secretary__user','created_by').prefetch_related('invitees__user')
    if not user.is_active or user.role==Role.ADMIN:return qs.none()
    member=active_members().filter(user=user)
    direct=Q(invitees__in=member)|Q(committee__chair__in=member)|Q(committee__secretary__in=member)
    scope=direct|Q(committee__members__in=member)
    if user.role in GLOBAL_ROLES:scope=Q()
    qs=qs.filter(scope).filter(Q(secrecy='internal')|direct|Q(created_by=user)).distinct()
    return qs if include_archived else qs.filter(archived=False)

def require_manage(user,record):
    if not records_for(user,True).filter(pk=record.pk).exists() or not can_manage(user,record.committee):raise PermissionDenied('لا تملك إدارة هذا الاجتماع.')

def reason_required(reason):
    reason=(reason or '').strip()
    if not reason:raise ValidationError('سبب الإجراء مطلوب.')
    return reason

def digest_payload(scope,actor,action,reason,details,at,previous):
    return json.dumps({'scope':scope,'actor':actor,'action':action,'reason':reason,'details':details,'at':at.isoformat(),'previous':previous},ensure_ascii=False,sort_keys=True,separators=(',',':'))

@transaction.atomic
def event(scope,user,action,reason,details):
    stream,_=AuditStream.objects.get_or_create(scope=scope)
    stream=AuditStream.objects.select_for_update().get(pk=stream.pk)
    previous=stream.events.order_by('-pk').first();previous=previous.digest if previous else ''
    at=timezone.now();reason=reason_required(reason)
    digest=hashlib.sha256(digest_payload(scope,user.pk if user else None,action,reason,details,at,previous).encode()).hexdigest()
    return Event.objects.create(stream=stream,actor=user,action=action,reason=reason,details=details,created_at=at,previous_hash=previous,digest=digest)

def verify_chain(scope):
    previous=''
    for row in Event.objects.filter(stream__scope=scope).order_by('pk'):
        digest=hashlib.sha256(digest_payload(scope,row.actor_id,row.action,row.reason,row.details,row.created_at,previous).encode()).hexdigest()
        if row.previous_hash!=previous or row.digest!=digest:return False
        previous=row.digest
    return True

def member_snapshot(member):
    return {'user':member.user_id,'role':member.role,'starts':str(member.starts),'ends':str(member.ends) if member.ends else None,'archived':member.archived,'organization':member.organization,'capacity':member.capacity,'identity_recorded':bool(member.identity_hash)}

@transaction.atomic
def save_member(user,data,instance=None,reason='',national_id=''):
    require_admin(user);reason=reason_required(reason)
    row=Member.objects.select_for_update().get(pk=instance.pk) if instance else Member()
    before=member_snapshot(row) if instance else {}
    if instance and data.get('user',row.user).pk!=row.user_id:raise ValidationError('حساب العضو ثابت؛ عدّل البيانات في دليل المستخدمين.')
    for key in ['user','role','organization','capacity','starts','ends']:
        if key in data:setattr(row,key,data[key])
    if national_id:
        if len(national_id)!=10 or not national_id.isascii() or not national_id.isdigit():raise ValidationError('الهوية الوطنية يجب أن تتكون من 10 أرقام 0-9.')
        row.identity_hash=hmac.new(settings.SECRET_KEY.encode(),national_id.encode(),hashlib.sha256).hexdigest()
        row.identity_encrypted=base64.b64encode(seal(national_id.encode())).decode()
    row.full_clean();row.save()
    event(f'member:{row.pk}',user,'تحديث العضوية' if instance else 'إنشاء عضوية',reason,{'before':before,'after':member_snapshot(row)})
    return row

@transaction.atomic
def archive_member(user,member,archived,reason):
    require_admin(user);reason=reason_required(reason);member=Member.objects.select_for_update().get(pk=member.pk)
    if archived and Committee.objects.filter(archived=False).filter(Q(chair=member)|Q(secretary=member)).exists():raise ValidationError('غيّر مسؤولية اللجان النشطة قبل أرشفة الرئيس أو أمين السر.')
    old=member.archived;member.archived=archived;member.full_clean();member.save()
    event(f'member:{member.pk}',user,'أرشفة عضو' if archived else 'استرجاع عضو',reason,{'before':old,'after':archived})
    return member

def committee_snapshot(row):
    return {'name':row.name,'chair':row.chair_id,'secretary':row.secretary_id,'kind':row.kind,'mandate':row.mandate,'starts':str(row.starts),'ends':str(row.ends) if row.ends else None,'members':list(row.members.values_list('pk',flat=True)),'version':row.version,'archived':row.archived}

@transaction.atomic
def save_committee(user,data,members,instance=None,reason='',version=None):
    require_admin(user);reason=reason_required(reason)
    row=Committee.objects.select_for_update().get(pk=instance.pk) if instance else Committee()
    if instance and version!=row.version:raise ValidationError('تغيرت بيانات اللجنة؛ أعد تحميل الصفحة قبل الحفظ.')
    before=committee_snapshot(row) if instance else {}
    for key in ['name','kind','mandate','chair','secretary','starts','ends']:
        if key in data:setattr(row,key,data[key])
    members=list(members)
    if any(not member.active for member in members):raise ValidationError('تضم اللجنة أعضاء نشطين فقط.')
    if row.chair_id not in [m.pk for m in members] or row.secretary_id not in [m.pk for m in members]:raise ValidationError('الرئيس وأمين السر يجب أن يكونا ضمن أعضاء اللجنة.')
    row.full_clean()
    if instance:row.version+=1
    row.save();row.members.set(members)
    event(f'committee:{row.pk}',user,'تحديث لجنة' if instance else 'إنشاء لجنة',reason,{'before':before,'after':committee_snapshot(row)})
    return row

@transaction.atomic
def archive_committee(user,committee,archived,reason):
    require_admin(user);reason=reason_required(reason);committee=Committee.objects.select_for_update().get(pk=committee.pk)
    if archived and committee.meetings.filter(archived=False).exclude(status='cancelled').exists():raise ValidationError('أرشف الاجتماعات المفتوحة أو ألغها أولًا.')
    committee.archived=archived;committee.version+=1;committee.save()
    event(f'committee:{committee.pk}',user,'أرشفة لجنة' if archived else 'استرجاع لجنة',reason,{'archived':archived})
    return committee

MEETING_FIELDS=['committee','kind','mode','starts_at','ends_at','venue','connection_url','rsvp_deadline','quorum_percent','quorum_reference','allow_proxy','proxy_limit','secrecy','final_reminder_hours','meeting_reminder_hours']
def snapshot(row):
    return {'number':row.meeting.number,'name':row.meeting.name,'committee':row.committee_id,'starts_at':row.starts_at.isoformat(),'ends_at':row.ends_at.isoformat(),'rsvp_deadline':row.rsvp_deadline.isoformat(),'kind':row.kind,'mode':row.mode,'venue':row.venue,'connection_url':row.connection_url,'quorum_percent':str(row.quorum_percent),'quorum_reference':row.quorum_reference,'allow_proxy':row.allow_proxy,'proxy_limit':row.proxy_limit,'secrecy':row.secrecy,'status':row.status,'archived':row.archived,'version':row.version,'invitees':list(row.invitees.values_list('pk',flat=True))}

@transaction.atomic
def save_meeting(user,data,invitees,instance=None,reason='',version=None,legacy=None):
    reason=reason_required(reason)
    if instance:
        row=MeetingRecord.objects.select_for_update().select_related('meeting','committee').get(pk=instance.pk);require_manage(user,row)
        if row.archived or row.status=='cancelled':raise ValidationError('استرجع الاجتماع المؤرشف أو الملغى قبل تعديله.')
        if version!=row.version:raise ValidationError('تغير الاجتماع بواسطة مستخدم آخر؛ أعد تحميله قبل الحفظ.')
    else:
        row=MeetingRecord(created_by=user)
    committee=data.get('committee',row.committee if instance else None)
    if not committee or not can_manage(user,committee):raise PermissionDenied('إنشاء الاجتماع وتعديله لرئيس اللجنة وأمينها أو الإدارة التنفيذية.')
    if not committee.active:raise ValidationError('اللجنة غير نشطة.')
    before=snapshot(row) if instance else {}
    if instance and row.roster_generation:
        frozen=['committee','starts_at','ends_at','rsvp_deadline','quorum_percent','quorum_reference','allow_proxy','proxy_limit','secrecy','final_reminder_hours','meeting_reminder_hours']
        if any(k in data and data[k]!=getattr(row,k) for k in frozen) or set(m.pk for m in invitees)!=set(row.invitees.values_list('pk',flat=True)):
            raise ValidationError('أبطل الدعوات وسجل الأهلية قبل تغيير الموعد أو النصاب أو المدعوين؛ ثم أعد إصدارها.')
    for key in MEETING_FIELDS:
        if key in data:setattr(row,key,data[key])
    day=timezone.localtime(row.starts_at).date()
    if committee.starts>day or (committee.ends and committee.ends<day):raise ValidationError('موعد الاجتماع خارج فترة اللجنة.')
    people=list(invitees)
    eligible=set(active_members(day).filter(committees=committee).values_list('pk',flat=True))
    if not people or any(m.pk not in eligible for m in people):raise ValidationError('اختر مدعوين بعضوية سارية في اللجنة يوم الاجتماع.')
    if not any(m.role!=MemberRole.OBSERVER for m in people):raise ValidationError('يجب أن تتضمن الدعوة عضوًا مؤهلًا للنصاب.')
    if not instance and row.starts_at<=timezone.now():raise ValidationError('اختر موعدًا قادمًا للاجتماع الجديد.')
    row.full_clean(exclude=['meeting'])
    if instance:
        meeting=Meeting.objects.select_for_update().get(pk=row.meeting_id)
    elif legacy:
        if user.role not in GLOBAL_ROLES:raise PermissionDenied('توسعة الاجتماع السابق للإدارة التنفيذية فقط.')
        meeting=Meeting.objects.select_for_update().get(pk=legacy.pk)
        if MeetingRecord.objects.filter(meeting=meeting).exists():raise ValidationError('هذا الاجتماع موسع بالفعل.')
    else:meeting=Meeting()
    if row.secrecy=='secret' and meeting.pk and meeting.decisions.exists():raise ValidationError('ربط القرارات باجتماع سري يحتاج امتداد السرية في المرحلة الرابعة؛ احتفظ بسرية السجل الحالية.')
    meeting.number=data.get('number',meeting.number);meeting.name=data.get('name',meeting.name);meeting.committee=committee.name;meeting.date=day
    meeting.full_clean();meeting.save();row.meeting=meeting
    if instance:row.version+=1
    row.full_clean();row.save();row.invitees.set(people)
    event(f'meeting:{row.pk}',user,'تحديث اجتماع' if instance else 'إنشاء اجتماع',reason,{'before':before,'after':snapshot(row),'extended_existing':bool(legacy)})
    return row

@transaction.atomic
def meeting_action(user,record,action,reason,version):
    row=MeetingRecord.objects.select_for_update().get(pk=record.pk);require_manage(user,row);reason=reason_required(reason)
    if version!=row.version:raise ValidationError('تغير الاجتماع؛ أعد تحميل الصفحة.')
    before=snapshot(row)
    if action=='schedule':
        if row.archived or row.status!='draft':raise ValidationError('الجدولة متاحة للمسودة فقط.')
        row.full_clean()
        if row.starts_at<=timezone.now():raise ValidationError('موعد الاجتماع انتهى.')
        people=list(row.invitees.all());eligible=set(active_members(timezone.localtime(row.starts_at).date()).filter(committees=row.committee).values_list('pk',flat=True))
        if not people or any(p.pk not in eligible for p in people):raise ValidationError('راجع قائمة المدعوين وفترات عضويتهم.')
        if not any(p.role!=MemberRole.OBSERVER for p in people):raise ValidationError('لا يوجد مدعو مؤهل للنصاب.')
        row.status='scheduled'
    elif action=='cancel':
        if row.archived or row.status=='cancelled':raise ValidationError('الاجتماع مؤرشف أو ملغى.')
        row.status='cancelled'
    elif action=='archive':
        if row.meeting.decisions.exists():raise ValidationError('اجتماع مرتبط بقرارات؛ لا يقبل الحذف المنطقي في هذه المرحلة.')
        row.archived=True
    elif action=='restore':
        if not row.archived and row.status!='cancelled':raise ValidationError('الاجتماع ليس مؤرشفًا أو ملغى.')
        row.archived=False;row.status='draft'
    else:raise ValidationError('إجراء غير متاح في المرحلة الأولى.')
    if action in ['cancel','archive','restore']:
        Invitation.objects.filter(eligibility__roster__meeting=row,revoked_at__isnull=True).update(revoked_at=timezone.now())
        Reminder.objects.filter(invitation__eligibility__roster__meeting=row,delivered_at__isnull=True,cancelled_at__isnull=True).update(cancelled_at=timezone.now())
    row.version+=1;row.save()
    event(f'meeting:{row.pk}',user,'إجراء اجتماع: '+action,reason,{'before':before,'after':snapshot(row)})
    return row

def quorum(record):
    from .session_services import quorum as frozen_quorum
    result=frozen_quorum(record)
    if result:return result
    day=timezone.localtime(record.starts_at).date()
    people=active_members(day).filter(committees=record.committee).exclude(role=MemberRole.OBSERVER).distinct()
    count=people.count()
    invited=people.filter(pk__in=record.invitees.values('pk')).count()
    return {'eligible':count,'invited_eligible':invited,'percent':str(record.quorum_percent),'required':math.ceil(Decimal(count)*record.quorum_percent/Decimal(100)),'confirmed':None,'actual_present':None,'status':'لم يبدأ تسجيل الحضور؛ العدد المطلوب محسوب من كامل أعضاء اللجنة المؤهلين، ولا ينخفض بتقليل المدعوين.'}
