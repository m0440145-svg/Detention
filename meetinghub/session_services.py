import hashlib,json,secrets,math,uuid
from pathlib import Path
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.models import Notification
from core.services import validate_upload
from .models import *
from . import services


def locked(user,record):
    row=MeetingRecord.objects.select_for_update().select_related('committee','meeting').get(pk=record.pk)
    services.require_manage(user,row)
    if row.archived or row.status=='cancelled':raise ValidationError('الاجتماع مؤرشف أو ملغى.')
    return row

def live_roster(record):return record.rosters.filter(generation=record.roster_generation).first()

def require_version(record,version):
    if version!=record.version:raise ValidationError('تغير الاجتماع؛ أعد تحميل الصفحة قبل إعادة المحاولة.')

def bump(record):record.version+=1;record.save(update_fields=['version','agenda_version','roster_generation','updated_at'])

def may_read_item(user,record,item):
    current=record.agenda_items.filter(pk=item['id']).first() if item.get('id') else None
    restricted=item['kind']=='secret' or (current and current.kind=='secret')
    allowed=services.can_manage(user,record.committee) or Member.objects.filter(user=user,pk__in=[item['presenter'],item['owner']]).exists()
    if current and current.kind=='secret':allowed=services.can_manage(user,record.committee) or Member.objects.filter(user=user,pk__in=[current.presenter_id,current.owner_id]).exists()
    return not restricted or allowed

def revision_items(user,record,revision):
    if not services.records_for(user,True).filter(pk=record.pk).exists():raise PermissionDenied()
    return [item for item in revision.items if may_read_item(user,record,item)]

def draft_items(user,record):
    rows=record.agenda_items.filter(removed=False).select_related('presenter__user','owner__user','parent','previous_decision').prefetch_related('files')
    if services.can_manage(user,record.committee):return rows
    return rows.filter(Q(kind__in=['briefing','discussion','decision','vote','minutes','information'])|Q(presenter__user=user)|Q(owner__user=user))

def agenda_payload(record):
    return [{'id':i.pk,'parent':i.parent_id,'title':i.title,'kind':i.kind,'duration':i.duration,'presenter':i.presenter_id,'presenter_name':str(i.presenter),'owner':i.owner_id,'owner_name':str(i.owner),'position':i.position,'link':i.link,'previous_decision':i.previous_decision_id,'notes':i.notes,'files':[{'id':f.pk,'name':f.name,'sha256':f.sha256,'size':f.size} for f in i.files.all()]} for i in record.agenda_items.filter(removed=False).select_related('presenter__user','owner__user').prefetch_related('files')]

def publish_locked(user,record,reason):
    payload=agenda_payload(record)
    if not payload:raise ValidationError('أضف بندًا واحدًا على الأقل قبل النشر.')
    record.agenda_version+=1
    revision=AgendaRevision.objects.create(meeting=record,version=record.agenda_version,items=payload,digest=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),reason=reason,created_by=user)
    return revision

@transaction.atomic
def save_item(user,record,data,reason,version,instance=None):
    record=locked(user,record);require_version(record,version);reason=services.reason_required(reason)
    item=record.agenda_items.filter(pk=instance.pk,removed=False).first() if instance else AgendaItem(meeting=record,position=record.agenda_items.count())
    if item is None:raise ValidationError('البند غير موجود في المسودة الحالية.')
    for key in ['title','kind','duration','presenter','owner','parent','link','previous_decision','notes']:
        if key in data:setattr(item,key,data[key])
    if item.parent_id:
        parent=item.parent;seen={item.pk} if item.pk else set()
        while parent:
            if parent.pk in seen or parent.meeting_id!=record.pk or parent.removed:raise ValidationError('البند الأب يجب أن يكون في الاجتماع نفسه؛ يمنع الدوران.')
            seen.add(parent.pk);parent=parent.parent
    for member in [item.presenter,item.owner]:
        if not record.invitees.filter(pk=member.pk).exists() or not member.active:raise ValidationError('المقدم والمسؤول يجب أن يكونا مدعوين نشطين.')
    if item.link and not item.link.startswith('https://'):raise ValidationError('الرابط يجب أن يستخدم HTTPS.')
    if item.previous_decision_id:
        from core.views import decisions_for
        if not decisions_for(user).filter(pk=item.previous_decision_id).exists():raise PermissionDenied('القرار السابق خارج صلاحياتك.')
    item.full_clean();item.save()
    if record.agenda_version:publish_locked(user,record,reason)
    bump(record);services.event(f'meeting:{record.pk}',user,'تعديل جدول الأعمال' if instance else 'إضافة بند',reason,{'item':item.pk,'agenda_version':record.agenda_version,'version':record.version})
    return item

@transaction.atomic
def publish_agenda(user,record,reason,version):
    record=locked(user,record);require_version(record,version);reason=services.reason_required(reason)
    revision=publish_locked(user,record,reason);bump(record)
    services.event(f'meeting:{record.pk}',user,'نشر جدول أعمال',reason,{'revision':revision.version,'digest':revision.digest})
    return revision

@transaction.atomic
def reorder(user,record,ids,reason,version):
    record=locked(user,record);require_version(record,version);reason=services.reason_required(reason)
    existing=set(record.agenda_items.filter(removed=False).values_list('pk',flat=True))
    if len(ids)!=len(set(ids)) or set(ids)!=existing:raise ValidationError('أرسل جميع البنود مرة واحدة، دون تكرار أو بند من اجتماع آخر.')
    rows={i.pk:i for i in record.agenda_items.filter(removed=False)}
    for position,pk in enumerate(ids):
        item=rows[pk]
        if item.parent_id and ids.index(item.parent_id)>position:raise ValidationError('البند الأب يجب أن يسبق بنوده الفرعية.')
        item.position=position;item.save(update_fields=['position'])
    if record.agenda_version:publish_locked(user,record,reason)
    bump(record);services.event(f'meeting:{record.pk}',user,'إعادة ترتيب جدول أعمال',reason,{'order':ids,'agenda_version':record.agenda_version})

@transaction.atomic
def remove_item(user,record,item,reason,version):
    record=locked(user,record);require_version(record,version);reason=services.reason_required(reason)
    item=record.agenda_items.filter(pk=item.pk,removed=False).first()
    if item is None:raise ValidationError('البند غير موجود في المسودة الحالية.')
    if item.children.filter(removed=False).exists():raise ValidationError('أزل أو انقل البنود الفرعية أولًا.')
    item.removed=True;item.save(update_fields=['removed'])
    if record.agenda_version:
        if not record.agenda_items.filter(removed=False).exists():raise ValidationError('الجدول المنشور يحتاج بندًا واحدًا على الأقل.')
        publish_locked(user,record,reason)
    bump(record);services.event(f'meeting:{record.pk}',user,'إزالة من المسودة',reason,{'item':item.pk,'agenda_version':record.agenda_version})

@transaction.atomic
def upload_file(user,record,upload,reason,version,item=None,purpose='agenda'):
    record=locked(user,record);require_version(record,version);reason=services.reason_required(reason)
    if item and (item.meeting_id!=record.pk or item.removed):raise ValidationError('البند خارج الاجتماع.')
    if purpose not in ['agenda','proxy']:raise ValidationError('غرض الملف غير صالح.')
    if purpose=='proxy' and item:raise ValidationError('وثيقة التوكيل مستقلة عن بنود جدول الأعمال.')
    validate_upload(upload);content=upload.read();upload.seek(0);name=Path(upload.name).name[:250];upload.name=uuid.uuid4().hex+Path(name).suffix.lower()
    row=MeetingFile(meeting=record,item=item,purpose=purpose,protected=purpose=='proxy' or (item and item.kind=='secret') or False,name=name,size=upload.size,sha256=hashlib.sha256(content).hexdigest(),uploaded_by=user)
    try:
        row.file=upload;row.save()
        if item and record.agenda_version:publish_locked(user,record,reason)
        bump(record);services.event(f'meeting:{record.pk}',user,'رفع مرفق اجتماع',reason,{'file':row.pk,'sha256':row.sha256,'purpose':purpose,'agenda_version':record.agenda_version})
    except Exception:
        if row.file.name:row.file.storage.delete(row.file.name)
        raise
    return row

def file_allowed(user,file):
    if not services.records_for(user,True).filter(pk=file.meeting_id).exists():return False
    record=file.meeting
    if services.can_manage(user,record.committee):return True
    if file.purpose=='proxy':return file.proxy_records.filter(Q(principal__member__user=user)|Q(delegate__member__user=user),revoked_at__isnull=True).exists()
    if not record.agenda_version:return False
    if file.item_id and not any(any(f['id']==file.pk for f in i['files']) for rev in record.agenda_revisions.all() for i in revision_items(user,record,rev)):return False
    if file.item_id:return may_read_item(user,record,{'kind':'secret' if file.protected else file.item.kind,'presenter':file.item.presenter_id,'owner':file.item.owner_id})
    return not file.protected

@transaction.atomic
def acknowledge(user,record,revision):
    record=MeetingRecord.objects.select_for_update().get(pk=record.pk)
    if not services.records_for(user).filter(pk=record.pk).exists():raise PermissionDenied()
    if revision.meeting_id!=record.pk or revision.version!=record.agenda_version:raise ValidationError('الاطلاع يسجل على الإصدار المنشور الحالي فقط.')
    member=services.active_members().filter(user=user,pk__in=record.invitees.values('pk')).first()
    if not member:raise PermissionDenied('تأكيد الاطلاع للمدعو نفسه فقط.')
    row,created=ReadAcknowledgment.objects.get_or_create(revision=revision,member=member)
    if created:services.event(f'meeting:{record.pk}',user,'تأكيد اطلاع','اطلاع على إصدار جدول الأعمال؛ لا يعد اعتمادًا أو توقيعًا',{'revision':revision.version,'member':member.pk})
    return row

@transaction.atomic
def issue_invitations(user,record,reason,version):
    record=locked(user,record);require_version(record,version);reason=services.reason_required(reason)
    now=timezone.now()
    if record.status!='scheduled' or record.rsvp_deadline<=now:raise ValidationError('جدول الاجتماع وحدد مهلة مستقبلية قبل إنشاء الدعوات.')
    if record.starts_at<=now or Attendance.objects.filter(eligibility__roster__meeting=record).exists():raise ValidationError('لا تستبدل الأهلية بعد بدء الحضور.')
    if not record.agenda_version:raise ValidationError('انشر جدول الأعمال قبل إصدار الدعوات.')
    day=timezone.localtime(record.starts_at).date();people=list(services.active_members(day).filter(committees=record.committee).distinct());invited=set(record.invitees.values_list('pk',flat=True))
    if not invited or not invited.issubset({p.pk for p in people}):raise ValidationError('راجع أهلية جميع المدعوين.')
    count=sum(p.role!='observer' for p in people)
    if not count:raise ValidationError('لا توجد أهلية للنصاب.')
    old=live_roster(record)
    if old:
        Invitation.objects.filter(eligibility__roster=old,revoked_at__isnull=True).update(revoked_at=now)
        Reminder.objects.filter(invitation__eligibility__roster=old,delivered_at__isnull=True,cancelled_at__isnull=True).update(cancelled_at=now)
    last=record.rosters.order_by("-generation").first()
    record.roster_generation=(last.generation if last else 0)+1
    payload=[{'member':p.pk,'name':str(p),'role':p.role,'invited':p.pk in invited,'eligible':p.role!='observer'} for p in people]
    digest=hashlib.sha256(json.dumps({'people':payload,'quorum':str(record.quorum_percent),'reference':record.quorum_reference},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    roster=Roster.objects.create(meeting=record,generation=record.roster_generation,quorum_percent=record.quorum_percent,quorum_reference=record.quorum_reference,eligible_count=count,required=math.ceil(Decimal(count)*record.quorum_percent/100),digest=digest,created_by=user)
    links=[]
    for person in payload:
        eligibility=Eligibility.objects.create(roster=roster,member_id=person['member'],name=person['name'],role=person['role'],invited=person['invited'],quorum_eligible=person['eligible'],voting_eligible=person['eligible'])
        if not eligibility.invited:continue
        raw=secrets.token_urlsafe(32)
        invitation=Invitation.objects.create(eligibility=eligibility,token_hash=hashlib.sha256(raw.encode()).hexdigest(),expires_at=record.rsvp_deadline)
        links.append({'id':invitation.pk,'member':eligibility.name,'token':raw})
        times={'midpoint':now+(record.rsvp_deadline-now)/2,'final':record.rsvp_deadline-timedelta(hours=record.final_reminder_hours),'meeting':record.starts_at-timedelta(hours=record.meeting_reminder_hours)}
        for kind,due in times.items():
            if now<due<(record.starts_at if kind=='meeting' else record.rsvp_deadline):Reminder.objects.create(invitation=invitation,kind=kind,due_at=due)
        Notification.objects.create(recipient=eligibility.member.user,task=None,text=f'دعوة اجتماع داخلية: /meetings/{record.pk}/ — أكد حضورك من مساحة الاجتماع. لم ترسل رسالة خارجية.')
    bump(record);services.event(f'meeting:{record.pk}',user,'إصدار دعوات وأهلية مجمدة',reason,{'generation':roster.generation,'digest':digest,'eligible':count,'required':roster.required,'invitations':len(links),'delivery':'internal only'})
    return links

def validate_invitation(invitation):
    eligibility=invitation.eligibility;record=eligibility.roster.meeting;now=timezone.now()
    if invitation.revoked_at or invitation.expires_at<=now or record.archived or record.status!='scheduled' or eligibility.roster.generation!=record.roster_generation or not eligibility.member.active:raise ValidationError('رابط الدعوة منتهي أو ملغى أو العضوية غير نشطة.')
    return record

def find_invitation(token):
    if not isinstance(token,str) or len(token)!=43:raise ValidationError('رابط غير صالح.')
    invitation=Invitation.objects.select_related('eligibility__member__user','eligibility__roster__meeting').filter(token_hash=hashlib.sha256(token.encode()).hexdigest()).first()
    if not invitation:raise ValidationError('رابط غير صالح.')
    validate_invitation(invitation);return invitation

@transaction.atomic
def respond(invitation,answer,user=None):
    record=MeetingRecord.objects.select_for_update().get(pk=invitation.eligibility.roster.meeting_id)
    invitation=Invitation.objects.select_for_update().select_related('eligibility__member__user','eligibility__roster__meeting').get(pk=invitation.pk)
    validate_invitation(invitation)
    if user and invitation.eligibility.member.user_id!=user.pk:raise PermissionDenied('الرد يخص صاحب الدعوة فقط.')
    if answer not in ['confirm','decline','proxy']:raise ValidationError('اختر تأكيدًا أو اعتذارًا أو طلب توكيل.')
    if answer=='proxy' and not record.allow_proxy:raise ValidationError('الاجتماع لا يسمح بالتوكيل.')
    before=invitation.response
    if answer!=before:
        invitation.response=answer;invitation.responded_at=timezone.now();invitation.save(update_fields=['response','responded_at'])
        services.event(f'meeting:{record.pk}',user,'رد دعوة', 'تحديث تأكيد الحضور المسبق؛ ليس حضورًا فعليًا',{'invitation':invitation.pk,'member':invitation.eligibility.member_id,'before':before,'after':answer})
    return invitation

@transaction.atomic
def revoke_invitation(user,record,invitation,reason):
    record=locked(user,record);reason=services.reason_required(reason)
    if invitation.eligibility.roster.meeting_id!=record.pk:raise ValidationError('الدعوة خارج الاجتماع.')
    invitation.revoked_at=timezone.now();invitation.save(update_fields=['revoked_at'])
    services.event(f'meeting:{record.pk}',user,'إبطال دعوة',reason,{'invitation':invitation.pk})

@transaction.atomic
def mark_attendance(user,record,eligibility,status,verification,reason):
    record=locked(user,record);reason=services.reason_required(reason)
    now=timezone.now()
    if record.status!='scheduled' or eligibility.roster.meeting_id!=record.pk or eligibility.roster.generation!=record.roster_generation or not eligibility.invited or not eligibility.member.active:raise ValidationError('الحضور يحتاج مدعوًا نشطًا في سجل الأهلية الحالي واجتماعًا مجدولًا.')
    if status not in dict(Attendance._meta.get_field('status').choices):raise ValidationError('حالة حضور غير صالحة.')
    if now<record.starts_at-timedelta(minutes=30):raise ValidationError('تسجيل الحضور متاح قبل الاجتماع بنصف ساعة وأثناءه.')
    if status in ['present','remote'] and now>record.ends_at:raise ValidationError('انتهى موعد الاجتماع؛ لا يسجل دخول جديد.')
    if not verification.strip():raise ValidationError('طريقة التحقق من الحضور مطلوبة.')
    row=Attendance.objects.filter(eligibility=eligibility).first()
    before=row.status if row else 'unrecorded';row=row or Attendance(eligibility=eligibility)
    if status in ['left','disconnected'] and before not in ['present','remote']:raise ValidationError('الخروج أو فقد الاتصال يحتاج حضورًا فعليًا سابقًا.')
    if status in ['present','remote'] and before not in ['present','remote']:
        row.entered_at=now;row.first_entered_at=row.first_entered_at or now;row.exited_at=None
    elif status in ['left','disconnected','absent']:row.exited_at=now if row.first_entered_at else None
    row.status=status;row.verification=verification.strip();row.marked_by=user;row.full_clean();row.save()
    services.event(f'meeting:{record.pk}',user,'تحديث حضور فعلي',reason,{'eligibility':eligibility.pk,'before':before,'after':status,'verification':verification,'entered_at':row.entered_at.isoformat() if row.entered_at else None,'exited_at':row.exited_at.isoformat() if row.exited_at else None})
    return row

@transaction.atomic
def approve_proxy(user,record,principal,delegate,document,valid_until,attendance_allowed,voting_allowed,reason):
    record=locked(user,record);reason=services.reason_required(reason);roster=live_roster(record);now=timezone.now()
    if not roster or not record.allow_proxy or not record.proxy_limit:raise ValidationError('لا يسمح الاجتماع بالتوكيل أو لم تصدر أهلية بعد.')
    if not attendance_allowed and not voting_allowed:raise ValidationError('حدد نطاق التوكيل وفق لائحة الجهة.')
    for p in [principal,delegate]:
        if p.roster_id!=roster.pk or not p.invited or not p.member.active:raise ValidationError('طرفا التوكيل يجب أن يكونا مدعوين نشطين في الأهلية الحالية.')
    if principal.pk==delegate.pk or not principal.quorum_eligible or not delegate.quorum_eligible:raise ValidationError('لا توكيل للنفس أو من/إلى مراقب.')
    if voting_allowed and (not principal.voting_eligible or not delegate.voting_eligible):raise ValidationError('أهلية التصويت لا تسمح بهذا التوكيل.')
    if valid_until<=now or valid_until>record.ends_at:raise ValidationError('مدة التوكيل يجب أن تكون سارية وألا تتجاوز نهاية الاجتماع.')
    if document.meeting_id!=record.pk or document.purpose!='proxy':raise ValidationError('أرفق وثيقة توكيل مرفوعة لهذا الاجتماع.')
    live=roster.proxies.filter(revoked_at__isnull=True,valid_until__gt=now)
    if live.filter(principal=principal).exists():raise ValidationError('يوجد توكيل قائم للموكل.')
    if live.filter(delegate=delegate).count()>=record.proxy_limit:raise ValidationError('تجاوز الوكيل الحد المسموح للتوكيلات.')
    if live.filter(Q(delegate=principal)|Q(principal=delegate)).exists():raise ValidationError('لا يسمح بتسلسل التوكيلات.')
    # Expired records are retained but stop blocking the unique active proxy.
    roster.proxies.filter(principal=principal,revoked_at__isnull=True,valid_until__lte=now).update(revoked_at=now)
    row=Proxy.objects.create(roster=roster,principal=principal,delegate=delegate,document=document,valid_until=valid_until,attendance_allowed=attendance_allowed,voting_allowed=voting_allowed,approved_by=user)
    services.event(f'meeting:{record.pk}',user,'اعتماد توكيل',reason,{'proxy':row.pk,'principal':principal.pk,'delegate':delegate.pk,'document':document.pk,'attendance_allowed':attendance_allowed,'voting_allowed':voting_allowed,'valid_until':valid_until.isoformat()})
    return row

@transaction.atomic
def revoke_proxy(user,record,proxy,reason):
    record=locked(user,record);reason=services.reason_required(reason)
    if proxy.roster.meeting_id!=record.pk:raise ValidationError('التوكيل خارج الاجتماع.')
    proxy.revoked_at=timezone.now();proxy.save(update_fields=['revoked_at'])
    services.event(f'meeting:{record.pk}',user,'إلغاء توكيل',reason,{'proxy':proxy.pk})


def quorum(record):
    roster=live_roster(record)
    if not roster:return None
    active=set(roster.people.filter(member_id__in=services.active_members().values('pk')).values_list('pk',flat=True))
    eligible=set(roster.people.filter(quorum_eligible=True).values_list('pk',flat=True))
    actual=set(Attendance.objects.filter(eligibility__roster=roster,status__in=['present','remote'],eligibility_id__in=active).values_list('eligibility_id',flat=True))&eligible
    confirmed=set(Invitation.objects.filter(eligibility__roster=roster,response='confirm',revoked_at__isnull=True,eligibility_id__in=active).values_list('eligibility_id',flat=True))&eligible
    for proxy in roster.proxies.filter(revoked_at__isnull=True,valid_until__gt=timezone.now(),attendance_allowed=True):
        if proxy.principal_id in active and proxy.delegate_id in active:
            if proxy.delegate_id in actual:actual.add(proxy.principal_id)
            if proxy.delegate_id in confirmed:confirmed.add(proxy.principal_id)
    return {'eligible':roster.eligible_count,'invited_eligible':roster.people.filter(invited=True,quorum_eligible=True).count(),'percent':str(roster.quorum_percent),'required':roster.required,'confirmed':len(confirmed),'actual_present':len(actual),'missing_confirmed':max(0,roster.required-len(confirmed)),'missing_actual':max(0,roster.required-len(actual)),'confirmed_quorum':len(confirmed)>=roster.required,'actual_quorum':len(actual)>=roster.required and record.status=='scheduled' and not record.archived and record.starts_at-timedelta(minutes=30)<=timezone.now()<=record.ends_at,'status':'تأكيد مسبق وحضور فعلي منفصلان؛ النصاب محسوب من سجل أهلية مجمد.','roster_generation':roster.generation,'roster_digest':roster.digest}

@transaction.atomic
def run_reminders(now=None):
    now=now or timezone.now();count=0
    RSVPAttempt.objects.filter(created_at__lt=now-timedelta(days=1)).delete()
    for pk in Reminder.objects.filter(due_at__lte=now,delivered_at__isnull=True,cancelled_at__isnull=True).values_list('pk',flat=True):
        meeting_id=Reminder.objects.values_list('invitation__eligibility__roster__meeting_id',flat=True).get(pk=pk)
        MeetingRecord.objects.select_for_update().get(pk=meeting_id)
        row=Reminder.objects.select_for_update().select_related('invitation__eligibility__roster__meeting','invitation__eligibility__member__user').get(pk=pk)
        if row.delivered_at or row.cancelled_at:continue
        inv=row.invitation;record=inv.eligibility.roster.meeting
        valid=not inv.revoked_at and inv.eligibility.member.active and not record.archived and record.status=='scheduled' and inv.eligibility.roster.generation==record.roster_generation
        if row.kind=='meeting':valid=valid and now<record.starts_at and inv.response!='decline'
        else:valid=valid and now<inv.expires_at and inv.response=='pending'
        if not valid:row.cancelled_at=now;row.save(update_fields=['cancelled_at']);continue
        row.notification=Notification.objects.create(recipient=inv.eligibility.member.user,task=None,text=f'تذكير اجتماع داخلي ({row.get_kind_display()}): /meetings/{record.pk}/ — لا يمثل رسالة بريد أو SMS.')
        row.delivered_at=now;row.save(update_fields=['notification','delivered_at']);count+=1
        services.event(f'meeting:{record.pk}',None,'تذكير داخلي آلي','موعد تذكير مستحق',{'reminder':row.pk,'notification':row.notification_id})
    return count

@transaction.atomic
def reset_invitations(user,record,reason,version):
    record=locked(user,record);require_version(record,version);reason=services.reason_required(reason)
    if record.starts_at<=timezone.now() or Attendance.objects.filter(eligibility__roster__meeting=record).exists():raise ValidationError('لا يغير سجل الأهلية بعد بدء الحضور أو الاجتماع.')
    now=timezone.now()
    Invitation.objects.filter(eligibility__roster__meeting=record,revoked_at__isnull=True).update(revoked_at=now)
    Reminder.objects.filter(invitation__eligibility__roster__meeting=record,delivered_at__isnull=True,cancelled_at__isnull=True).update(cancelled_at=now)
    record.roster_generation=0;bump(record)
    services.event(f'meeting:{record.pk}',user,'إبطال جميع الدعوات والأهلية',reason,{'version':record.version})
