import hashlib,hmac,json,uuid
from decimal import Decimal,InvalidOperation
from datetime import datetime,timedelta
from pathlib import Path
from urllib.parse import urlsplit
from django.conf import settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.models import Notification,User,Role,Task,Status
from .models import *
from . import services,workflow_services as workflow

def require_committee(user,committee):
    if not services.committees_for(user).filter(pk=committee.pk).exists() or user.role=='admin':raise PermissionDenied()
def manage(user,committee):
    require_committee(user,committee)
    if not services.can_manage(user,committee):raise PermissionDenied('الإجراء لرئيس اللجنة وأمينها أو الإدارة المخولة.')
def entries_for(user,archived=False):
    qs=GovernanceEntry.objects.filter(committee__in=services.committees_for(user)).exclude(committee__isnull=True)
    if user.role=='admin':return qs.none()
    if not archived:qs=qs.filter(archived=False)
    visible=services.records_for(user,True).values('pk')
    qs=qs.filter(Q(meeting__isnull=True)|Q(meeting_id__in=visible))
    if user.role not in ['executive','assistant']:
        managed=[c.pk for c in services.committees_for(user) if services.can_manage(user,c)]
        qs=qs.filter(Q(secret=False)|Q(owner=user)|Q(member__user=user)|Q(committee_id__in=managed))
    return qs.select_related('committee','meeting__meeting','member__user','owner','document')

def member(user,committee):
    require_committee(user,committee);row=services.active_members().filter(user=user,committees=committee).first()
    if not row or row.role=='observer':raise PermissionDenied('لا عضوية مخولة بالإجراء؛ المراقب للقراءة فقط.')
    return row

def document_allowed(user,doc):
    from .platform_services import permit_download
    if DestructionRequest.objects.filter(document=doc,executed_at__isnull=False).exists() or (doc.meeting and not permit_download(doc.meeting)):return False
    try:require_committee(user,doc.committee)
    except PermissionDenied:return False
    if doc.meeting and not services.records_for(user,True).filter(pk=doc.meeting_id).exists():return False
    if services.can_manage(user,doc.committee) or doc.owner_id==user.pk:return True
    if Candidate.objects.filter(document=doc,member__user=user).exists():return True
    return not doc.secret

@transaction.atomic
def upload_document(user,committee,upload,meeting=None,secret=True):
    require_committee(user,committee)
    if Member.objects.filter(user=user,role='observer',committees=committee).exists() and not services.can_manage(user,committee):raise PermissionDenied('المراقب للقراءة فقط.')
    if meeting and (meeting.committee_id!=committee.pk or not services.records_for(user,True).filter(pk=meeting.pk).exists()):raise PermissionDenied()
    from core.services import validate_upload
    validate_upload(upload);content=upload.read();upload.seek(0);name=Path(upload.name).name[:250];upload.name=uuid.uuid4().hex+Path(name).suffix.lower()
    row=Document(committee=committee,meeting=meeting,owner=user,file=upload,name=name,size=upload.size,sha256=hashlib.sha256(content).hexdigest(),secret=secret)
    try:row.save();services.event(f'committee:{committee.pk}',user,'رفع وثيقة حوكمة','رفع وفحص وثيقة محمية',{'document':row.pk,'sha256':row.sha256})
    except Exception:
        if row.file.name:row.file.storage.delete(row.file.name)
        raise
    return row

def stamp(value):
    try:result=datetime.fromisoformat(value)
    except (ValueError,TypeError):raise ValidationError('وقت غير صالح.')
    return timezone.make_aware(result) if timezone.is_naive(result) else result

def validate_entry(row):
    d=row.data
    if not isinstance(d,dict):raise ValidationError('حقول سجل غير صالحة.')
    needed={'compliance':['regulator','article','action','frequency','gap','remediation'],'finance':['period','source','amount','category'],'recommendation':['source','priority','expected_impact','review_date'],'service':['resource','quantity','starts','ends','requirements'],'volunteer':['starts','ends','source'],'crisis':['number','severity','starts','ends']}
    if row.kind not in needed or any(not str(d.get(k,'')).strip() for k in needed[row.kind]):raise ValidationError('أكمل الحقول المطلوبة لهذا السجل.')
    if row.meeting_id and row.meeting.committee_id!=row.committee_id:raise ValidationError('الاجتماع لا يتبع اللجنة.')
    if row.document_id and row.document.committee_id!=row.committee_id:raise ValidationError('الوثيقة خارج اللجنة.')
    if row.member_id and not row.committee.members.filter(pk=row.member_id).exists():raise ValidationError('العضو خارج اللجنة.')
    if not row.owner.is_active or row.owner.role in [Role.ADMIN,Role.VIEWER]:raise ValidationError('اختر مسؤولًا نشطًا للعمل.')
    if row.kind=='compliance':
        u=urlsplit(d.get('official_url',''))
        if d.get('official_url') and (u.scheme!='https' or not u.hostname or not u.hostname.endswith('.gov.sa') or u.path in ['', '/']):raise ValidationError('أدخل رابط نص رسمي مباشر على نطاق حكومي سعودي، أو اتركه بلا تأكيد قانوني.')
        if d.get('last_verified'):__import__('datetime').date.fromisoformat(d['last_verified'])
        if not row.due_date:raise ValidationError('استحقاق الالتزام مطلوب.')
        d['legal_status']='مرجع أدخله المسؤول؛ لا اعتماد قانوني تلقائي' if u.hostname and d.get('last_verified') else 'يتطلب تحققًا قانونيًا'
    if row.kind=='finance':
        try:amount=Decimal(str(d['amount']))
        except InvalidOperation:raise ValidationError('قيمة مالية غير صالحة.')
        if not amount.is_finite() or abs(amount)>Decimal('999999999999'):raise ValidationError('قيمة مالية خارج النطاق.')
        d['amount']=str(amount)
    if row.kind in ['service','volunteer','crisis']:
        start,end=stamp(d['starts']),stamp(d['ends'])
        if end<=start:raise ValidationError('النهاية يجب أن تتبع البداية.')
        if row.kind=='service':
            if not row.meeting_id or row.meeting.status=='cancelled':raise ValidationError('الخدمة تحتاج اجتماعًا غير ملغى.')
            if int(d['quantity'])<1:raise ValidationError('الكمية أكبر من صفر.')
            resource=Resource.objects.filter(committee=row.committee,name=d['resource'],active=True).first()
            if not resource:raise ValidationError('اختر موردًا نشطًا معرفًا لدى اللجنة.')
            events=[]
            for other in GovernanceEntry.objects.filter(committee=row.committee,kind='service',archived=False).exclude(status='cancelled').exclude(pk=row.pk):
                if other.data.get('resource')==d['resource'] and stamp(other.data['starts'])<end and stamp(other.data['ends'])>start:events.extend([(max(start,stamp(other.data['starts'])),int(other.data['quantity'])),(min(end,stamp(other.data['ends'])),-int(other.data['quantity']))])
            booked=peak=0
            for at,change in sorted(events):booked+=change;peak=max(peak,booked)
            if peak+int(d['quantity'])>resource.capacity:raise ValidationError('تعارض حجز المورد أو تجاوز سعته.')
        if row.kind=='volunteer':
            if not row.member_id:raise ValidationError('عضو الساعات مطلوب.')
            if end>timezone.now():raise ValidationError('لا تعتمد ساعات مستقبلية.')
            if d['source']=='attendance':
                if not row.meeting_id or not AttendanceSpan.objects.filter(eligibility__member=row.member,eligibility__roster__meeting=row.meeting,starts=start,ends=end).exists():raise ValidationError('الساعات تحتاج جلسة حضور فعلية مغلقة مطابقة.')
            elif d['source']=='task':
                task=Task.objects.filter(pk=d.get('task'),owner=row.member.user,status=Status.CLOSED).first()
                if not task:raise ValidationError('ساعات المهمة تحتاج مهمة معتمدة للمكلف.')
            else:raise ValidationError('اختر حضورًا فعليًا أو مهمة معتمدة.')
            for other in GovernanceEntry.objects.filter(kind='volunteer',member=row.member,archived=False).exclude(status='cancelled').exclude(pk=row.pk):
                if stamp(other.data['starts'])<end and stamp(other.data['ends'])>start:raise ValidationError('ساعات متداخلة أو مكررة.')
            d['hours']=str(round(Decimal(str((end-start).total_seconds()))/3600,2))
        if row.kind=='crisis' and (not row.meeting_id or row.meeting.kind!='emergency' or len(row.title.strip())<5):raise ValidationError('الغرفة تحتاج عنوانًا واضحًا واجتماعًا طارئًا.')
    if row.kind=='recommendation' and d.get('measured_impact') and not row.document_id:raise ValidationError('الأثر المقاس يحتاج دليلًا.')

@transaction.atomic
def save_entry(user,kind,committee,title,owner,data,reason,meeting=None,member=None,due_date=None,document=None,secret=True,instance=None,version=0):
    Committee.objects.select_for_update().get(pk=committee.pk);reason=services.reason_required(reason)
    self_volunteer=kind=='volunteer' and member and member.user_id==user.pk
    if self_volunteer:
        require_committee(user,committee)
        if member.role=='observer':raise PermissionDenied('المراقب للقراءة فقط.')
    else:manage(user,committee)
    if meeting:workflow.readable(user,meeting)
    row=GovernanceEntry.objects.select_for_update().get(pk=instance.pk) if instance else GovernanceEntry(kind=kind,committee=committee,created_by=user)
    if instance and (not entries_for(user).filter(pk=row.pk).exists() or version!=row.version or row.status in ['approved','complete','ended','cancelled']):raise ValidationError('السجل معتمد أو يوجد تعارض إصدار أو خارج النطاق.')
    if instance and (row.kind!=kind or row.committee_id!=committee.pk):raise ValidationError('نوع السجل واللجنة ثابتان.')
    for k,v in dict(title=title,owner=owner,data=data,meeting=meeting,member=member,due_date=due_date,document=document,secret=secret).items():setattr(row,k,v)
    validate_entry(row);row.full_clean();row.version+=1;row.save()
    services.event(f'committee:{committee.pk}',user,'حفظ سجل '+kind,reason,{'entry':row.pk,'version':row.version});return row

@transaction.atomic
def entry_transition(user,row,action,reason,version):
    Committee.objects.select_for_update().get(pk=row.committee_id);row=GovernanceEntry.objects.select_for_update().get(pk=row.pk);reason=services.reason_required(reason)
    if not entries_for(user,True).filter(pk=row.pk).exists():raise PermissionDenied()
    if row.version!=version:raise ValidationError('تعارض إصدار.')
    if action=='submit':
        if row.created_by_id!=user.pk and not services.can_manage(user,row.committee):raise PermissionDenied()
        if row.status!='draft':raise ValidationError('السجل ليس مسودة.')
        row.status='review'
    elif action in ['approve','reject','cancel','archive','restore','complete','end']:
        if not (action=='complete' and row.kind=='service' and row.owner_id==user.pk):manage(user,row.committee)
        if action=='approve':
            if row.status!='review':raise ValidationError('الاعتماد بعد تقديم السجل للمراجعة.')
            if row.kind=='volunteer' and (row.member.user_id==user.pk or row.created_by_id==user.pk):raise PermissionDenied('لا اعتماد ذاتي للساعات.')
            if row.kind=='finance' and not row.document_id:raise ValidationError('اعتماد مالي يحتاج دليلًا.')
            row.status='approved'
        elif action=='reject':
            if row.status!='review':raise ValidationError('الرفض لسجل قيد المراجعة.')
            row.status='draft'
        elif action=='complete':
            if row.status!='approved' or row.kind not in ['service','recommendation','compliance']:raise ValidationError('الإكمال بعد الاعتماد.')
            if row.kind=='service' and row.owner_id!=user.pk:raise PermissionDenied('إكمال الخدمة لمسؤولها فقط.')
            if not row.document_id:raise ValidationError('الإكمال يحتاج شاهدًا.')
            row.status='complete'
        elif action=='end':
            if row.kind!='crisis' or row.status not in ['draft','review','approved']:raise ValidationError('الغرفة غير نشطة.')
            row.status='ended'
        elif action=='cancel':row.status='cancelled'
        elif action=='archive':row.archived=True
        elif action=='restore':row.archived=False
    else:raise ValidationError('إجراء غير صالح.')
    row.version+=1;row.save();services.event(f'committee:{row.committee_id}',user,'انتقال سجل',reason,{'entry':row.pk,'action':action,'status':row.status,'version':row.version});return row

@transaction.atomic
def disclose(user,meeting,item,body,kind,recused,document=None):
    meeting=MeetingRecord.objects.select_for_update().get(pk=meeting.pk);person=workflow.member_for(user,meeting)
    if item.meeting_id!=meeting.pk or item.removed or meeting.closed_at:raise ValidationError('البند خارج الاجتماع أو مغلق.')
    if not body.strip() or not kind.strip():raise ValidationError('نوع ووصف الإفصاح مطلوبان.')
    if document and (document.committee_id!=meeting.committee_id or document.owner_id!=user.pk):raise PermissionDenied()
    if Disclosure.objects.filter(meeting=meeting,item=item,member=person).exists():raise ValidationError('إفصاح مكرر.')
    row=Disclosure.objects.create(meeting=meeting,item=item,member=person,body=body,kind=kind,recused=recused,document=document)
    workflow.emit(meeting,user,'إفصاح تعارض مصالح','إفصاح العضو؛ القاعدة تنتظر اعتماد المخول',{'disclosure':row.pk});return row

@transaction.atomic
def approve_disclosure(user,row,reason):
    meeting=MeetingRecord.objects.select_for_update().get(pk=row.meeting_id);services.require_manage(user,meeting);reason=services.reason_required(reason)
    if row.member.user_id==user.pk:raise PermissionDenied('اعتماد إفصاحك لمخول آخر.')
    if meeting.closed_at or row.approved:raise ValidationError('إفصاح معتمد أو اجتماع مغلق.')
    if Participation.objects.filter(vote__item=row.item,eligibility__member=row.member).exists():raise ValidationError('صوت العضو بالفعل؛ وثق معالجة رسمية قبل اعتماد تنحي متأخر.')
    row.approved=True;row.save();workflow.emit(meeting,user,'اعتماد قاعدة التنحي',reason,{'disclosure':row.pk,'recused':row.recused});return row

@transaction.atomic
def submit_idea(user,committee,body):
    member(user,committee)
    if not 10<=len(body.strip())<=5000:raise ValidationError('الفكرة من 10 إلى 5000 حرف دون بيانات تعريفية.')
    # No identity or timestamp audit couples anonymous content to its author.
    return Idea.objects.create(committee=committee,body=body)

@transaction.atomic
def vote_idea(user,idea):
    p=member(user,idea.committee);Idea.objects.select_for_update().get(pk=idea.pk)
    if idea.status!='submitted':raise ValidationError('الفكرة انتهت من التصويت.')
    key=hmac.new(settings.SECRET_KEY.encode(),f'idea:{idea.pk}:member:{p.pk}'.encode(),hashlib.sha256).hexdigest()
    if IdeaVote.objects.filter(idea=idea,voter_hash=key).exists():raise ValidationError('تصويت مكرر.')
    return IdeaVote.objects.create(idea=idea,voter_hash=key)

@transaction.atomic
def accept_idea(user,idea,owner,due_date,expected_impact,reason):
    Committee.objects.select_for_update().get(pk=idea.committee_id);manage(user,idea.committee);idea=Idea.objects.select_for_update().get(pk=idea.pk)
    if idea.status!='submitted':raise ValidationError('الفكرة حُولت أو أغلقت مسبقًا.')
    entry=save_entry(user,'recommendation',idea.committee,'توصية من فكرة '+str(idea.pk),owner,{'source':'فكرة '+str(idea.pk),'description':idea.body,'priority':'normal','expected_impact':expected_impact,'review_date':str(due_date)},reason,due_date=due_date,secret=False)
    idea.status='accepted';idea.recommendation=entry;idea.save();return entry

@transaction.atomic
def feedback(user,meeting,rating,body,anonymous):
    person=workflow.member_for(user,meeting)
    if not meeting.closed_at:raise ValidationError('التقييم بعد إغلاق الاجتماع.')
    if Feedback.objects.filter(meeting=meeting,member=person).exists():raise ValidationError('تقييم مكرر.')
    row=Feedback(meeting=meeting,member=person,rating=rating,body=body,anonymous=anonymous);row.full_clean();row.save();return row

@transaction.atomic
def run_governance_alerts(now=None):
    now=now or timezone.now();count=0
    for entry in GovernanceEntry.objects.filter(kind__in=['compliance','recommendation'],archived=False,due_date__isnull=False).exclude(status__in=['complete','cancelled']):
        days=(entry.due_date-timezone.localdate(now)).days
        kind='overdue' if days<0 else 'today' if days==0 else 'soon' if days<=30 else None
        if kind:
            key=f'governance:{entry.pk}:{entry.due_date}:{kind}'
            notification,created=Notification.objects.get_or_create(key=key,defaults={'recipient':entry.owner,'text':f'استحقاق سجل حوكمة: {entry.title} /meetings/governance/{entry.kind}/'})
            count+=int(created)
    return count

@transaction.atomic
def feedback_recommendation(user,feedback,owner,due_date,reason):
    meeting=MeetingRecord.objects.select_for_update().get(pk=feedback.meeting_id);services.require_manage(user,meeting);reason=services.reason_required(reason)
    if GovernanceEntry.objects.filter(feedback_source=feedback).exists():raise ValidationError('حوّلت هذه الملاحظة مسبقًا.')
    row=save_entry(user,'recommendation',meeting.committee,'تحسين مستفاد من تقييم اجتماع',owner,{'source':'تغذية راجعة بعد اجتماع','priority':'normal','expected_impact':feedback.body,'review_date':due_date.isoformat()},reason,meeting=meeting,due_date=due_date)
    row.feedback_source=feedback;row.save(update_fields=['feedback_source']);return row

@transaction.atomic
def link_recommendation(user,row,decision,task,reason,version):
    row=GovernanceEntry.objects.select_for_update().get(pk=row.pk);manage(user,row.committee);reason=services.reason_required(reason)
    from core.services import require_decision_manage
    from core.views import decisions_for
    from core.policy import tasks_for
    require_decision_manage(user)
    if row.kind!='recommendation' or row.status!='approved' or row.version!=version or row.decision_id or row.task_id:raise ValidationError('الربط لتوصية معتمدة غير محولة وبإصدار حالي.')
    if not entries_for(user).filter(pk=row.pk).exists():raise PermissionDenied()
    if not decision and not task:raise ValidationError('اختر قرارًا أو مهمة تنفيذ من السجل الموحد.')
    if decision and not decisions_for(user).filter(pk=decision.pk).exists():raise PermissionDenied()
    if task and not tasks_for(user).filter(pk=task.pk).exists():raise PermissionDenied()
    if task and decision and task.decision_id!=decision.pk:raise ValidationError('المهمة لا تتبع القرار المختار.')
    if row.meeting_id and decision and decision.meeting_id!=row.meeting.meeting_id:raise ValidationError('القرار لا يتبع اجتماع التوصية.')
    row.decision=decision;row.task=task;row.version+=1;row.save();services.event(f'committee:{row.committee_id}',user,'تحويل توصية إلى متابعة تنفيذ',reason,{'entry':row.pk,'decision':row.decision_id,'task':row.task_id});return row
