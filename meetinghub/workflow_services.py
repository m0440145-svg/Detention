import base64,hashlib,json
from decimal import Decimal
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.db.models import Q
from core.models import Notification
from correspondence.security import seal,unseal
from .models import *
from . import services,session_services as session

def digest(data):return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def emit(row,user,action,reason,data):services.event(f'meeting:{row.pk}',user,action,reason,data)
def readable(user,meeting):
    if not services.records_for(user,True).filter(pk=meeting.pk).exists():raise PermissionDenied()
def member_for(user,meeting):
    readable(user,meeting);member=services.active_members().filter(user=user,pk__in=meeting.invitees.values('pk')).first()
    if not member or member.role=='observer':raise PermissionDenied('الإجراء لعضو مدعو نشط؛ المراقب للقراءة فقط.')
    return member

def visible_votes(user,meeting):
    readable(user,meeting)
    return [v for v in meeting.votes.select_related('item') if session.may_read_item(user,meeting,{'id':v.item_id,'kind':v.item.kind,'presenter':v.item.presenter_id,'owner':v.item.owner_id})]

def own_present(roster,member):return Attendance.objects.filter(eligibility__roster=roster,eligibility__member=member,status__in=['present','remote']).exists()
def electorate(meeting,roster):
    eligible=set(roster.people.filter(invited=True,voting_eligible=True,member__in=services.active_members()).values_list('pk',flat=True))
    present=set(Attendance.objects.filter(eligibility__roster=roster,status__in=['present','remote']).values_list('eligibility_id',flat=True))&eligible
    for proxy in roster.proxies.filter(voting_allowed=True,revoked_at__isnull=True,valid_until__gt=timezone.now()):
        if proxy.delegate_id in present and proxy.principal_id in eligible:present.add(proxy.principal_id)
    return sorted(present)

def totals(vote):
    if not vote.aggregate_encrypted:return {'yes':0,'no':0,'abstain':0}
    return json.loads(unseal(base64.b64decode(vote.aggregate_encrypted)))
def store_totals(vote,counts):vote.aggregate_encrypted=base64.b64encode(seal(json.dumps(counts).encode())).decode()

@transaction.atomic
def create_vote(user,meeting,item,question,secret,denominator,threshold,reference,closes_at,purpose='decision'):
    meeting=session.locked(user,meeting)
    if item.meeting_id!=meeting.pk or item.removed or (purpose=='decision' and item.kind not in ['decision','vote']):raise ValidationError('التصويت الرسمي يرتبط ببند قرار أو تصويت في الاجتماع.')
    if purpose not in ['decision','poll'] or denominator not in ['eligible','cast']:raise ValidationError('نوع أو قاعدة تصويت غير صالحة.')
    if not question.strip() or not reference.strip() or not timezone.now()<closes_at<=meeting.ends_at:raise ValidationError('السؤال ومرجع قاعدة التمرير وموعد الإغلاق ضمن الاجتماع مطلوبة.')
    row=Vote(meeting=meeting,item=item,question=question,secret=secret,denominator=denominator,threshold=threshold,reference=reference,closes_at=closes_at,purpose=purpose,created_by=user);row.full_clean();row.save()
    emit(meeting,user,'إنشاء تصويت','إعداد سؤال وقاعدة موثقة',{'vote':row.pk,'secret':secret});return row

@transaction.atomic
def vote_transition(user,vote,action,reason,version):
    meeting=session.locked(user,vote.meeting);vote=Vote.objects.select_for_update().get(pk=vote.pk);session.require_version(vote,version);reason=services.reason_required(reason)
    if action=='open':
        if vote.status!='draft' or vote.closes_at<=timezone.now():raise ValidationError('الصندوق ليس مسودة سارية.')
        if not session.quorum(meeting) or not session.quorum(meeting)['actual_quorum']:raise ValidationError('لا يفتح التصويت دون النصاب الفعلي.')
        roster=session.live_roster(meeting);vote.roster=roster;vote.electorate=electorate(meeting,roster)
        if not vote.electorate:raise ValidationError('لا توجد أهلية تصويت حاضرة.')
        vote.status='open';vote.opened_at=timezone.now();store_totals(vote,{'yes':0,'no':0,'abstain':0})
        for p in roster.people.filter(pk__in=vote.electorate):Notification.objects.create(recipient=p.member.user,text=f'تصويت مفتوح: /meetings/{meeting.pk}/votes/')
    elif action=='close':
        if vote.status!='open':raise ValidationError('التصويت غير مفتوح.')
        if not session.quorum(meeting)['actual_quorum']:raise ValidationError('فقد النصاب؛ لا تعتمد النتيجة حتى عودة النصاب أو إلغاء التصويت.')
        counts=totals(vote);count=sum(counts.values());base=len(vote.electorate) if vote.denominator=='eligible' else count
        outcome='tie' if counts['yes']==counts['no'] else ('passed' if base and Decimal(counts['yes'])*100/base>=vote.threshold and counts['yes']>counts['no'] else 'failed')
        payload={'vote':vote.pk,'counts':counts,'base':base,'threshold':str(vote.threshold),'reference':vote.reference,'electorate':len(vote.electorate),'outcome':outcome}
        VoteResult.objects.create(vote=vote,totals=counts,electorate_count=len(vote.electorate),denominator_count=base,outcome=outcome,digest=digest(payload),closed_by=user)
        vote.status='closed';vote.closed_at=timezone.now()
    elif action=='cancel':
        if vote.status not in ['draft','open']:raise ValidationError('التصويت المختوم لا يلغى أو يعدل.')
        vote.status='cancelled';vote.closed_at=timezone.now()
    else:raise ValidationError('إجراء غير صالح.')
    vote.version+=1;vote.save();emit(meeting,user,'انتقال صندوق تصويت',reason,{'vote':vote.pk,'action':action,'version':vote.version});return vote

@transaction.atomic
def cast(user,vote,choice,eligibility_id=None):
    meeting=MeetingRecord.objects.select_for_update().get(pk=vote.meeting_id);member=member_for(user,meeting);vote=Vote.objects.select_for_update().get(pk=vote.pk)
    if vote not in visible_votes(user,meeting):raise PermissionDenied()
    if vote.status!='open' or timezone.now()>=vote.closes_at or not session.quorum(meeting)['actual_quorum']:raise ValidationError('الصندوق مغلق أو انتهت المهلة أو فقد النصاب.')
    if choice not in ['yes','no','abstain']:raise ValidationError('خيار غير صالح.')
    p=vote.roster.people.filter(pk=eligibility_id).first() if eligibility_id else vote.roster.people.filter(member=member).first()
    if not p or p.pk not in vote.electorate or not p.member.active or not own_present(vote.roster,member):raise PermissionDenied('لا أهلية تصويت حاضرة لهذا العضو.')
    if p.member_id!=member.pk and not vote.roster.proxies.filter(principal=p,delegate__member=member,voting_allowed=True,revoked_at__isnull=True,valid_until__gt=timezone.now()).exists():raise PermissionDenied('لا وكالة تصويت سارية.')
    if Disclosure.objects.filter(meeting=meeting,item=vote.item,member=p.member,recused=True,approved=True).exists():raise PermissionDenied('التنحي المعتمد يمنع التصويت على هذا البند.')
    if vote.participation.filter(eligibility=p).exists():raise ValidationError('سجلت المشاركة مسبقًا؛ لا تصويت ثانٍ أو تعديل.')
    proof=Participation.objects.create(vote=vote,eligibility=p,represented_by=member)
    if not vote.secret:PublicBallot.objects.create(participation=proof,choice=choice)
    counts=totals(vote);counts[choice]+=1;store_totals(vote,counts);vote.save(update_fields=['aggregate_encrypted'])
    emit(meeting,user,'إثبات مشاركة في التصويت','مشاركة موثقة؛ الاختيار السري غير مسجل في التدقيق',{'vote':vote.pk,'eligibility':p.pk,'secret':vote.secret})
    return proof

def minute_visible(user,revision):
    meeting=revision.minutes.meeting
    if not services.records_for(user,True).filter(pk=meeting.pk).exists():return False
    if services.can_manage(user,meeting.committee):return True
    for item in revision.snapshot.get('agenda',[]):
        if not session.may_read_item(user,meeting,item):return False
    return not revision.secret or bool(revision.signatories and Member.objects.filter(user=user,pk__in=revision.signatories).exists())

def notes_for(user,meeting):
    readable(user,meeting)
    allowed=[n.pk for n in meeting.notes.filter(Q(owner=user)|Q(shared=True)).select_related('item') if not n.item or session.may_read_item(user,meeting,{'id':n.item_id,'kind':n.item.kind,'presenter':n.item.presenter_id,'owner':n.item.owner_id})]
    return meeting.notes.filter(pk__in=allowed)

def minute_payload(meeting):
    roster=session.live_roster(meeting)
    return {'meeting':meeting.meeting.name,'number':meeting.meeting.number,'committee':meeting.committee.name,'starts_at':meeting.starts_at.isoformat(),'agenda_version':meeting.agenda_version,'agenda':session.agenda_payload(meeting),'roster':roster.digest if roster else None,'attendance':list(Attendance.objects.filter(eligibility__roster=roster).values('eligibility__name','status','first_entered_at','entered_at','exited_at','verification')) if roster else [],'votes':[{'id':r.vote_id,'question':r.vote.question,'totals':r.totals,'outcome':r.outcome,'digest':r.digest} for r in VoteResult.objects.filter(vote__meeting=meeting)],'decisions':list(meeting.meeting.decisions.values('id','number','text','status','due_date'))}

@transaction.atomic
def save_minutes(user,meeting,body,signatories,reason,version=0,correction=False):
    meeting=MeetingRecord.objects.select_for_update().get(pk=meeting.pk);services.require_manage(user,meeting);reason=services.reason_required(reason)
    if meeting.archived or meeting.status=='cancelled':raise ValidationError('الاجتماع مؤرشف أو ملغى.')
    if not meeting.agenda_version:raise ValidationError('انشر جدول الأعمال قبل إنشاء المحضر.')
    minutes,_=Minutes.objects.get_or_create(meeting=meeting)
    minutes=Minutes.objects.select_for_update().get(pk=minutes.pk)
    if minutes.current_version and version!=minutes.version:raise ValidationError('تعارض إصدار المحضر.')
    if minutes.status in ['review','signed','final'] and not correction:raise ValidationError('أنشئ إصدار تصحيح صريحًا؛ النسخة المعروضة أو الموقعة ثابتة.')
    people=services.active_members().filter(pk__in=signatories).filter(pk__in=meeting.invitees.values('pk'))
    if not signatories or people.count()!=len(set(signatories)):raise ValidationError('حدد الموقعين المطلوبين من المدعوين النشطين.')
    secret=meeting.secrecy=='secret' or meeting.agenda_items.filter(kind='secret',removed=False).exists()
    if secret:
        for p in people:
            if len(session.revision_items(p.user,meeting,meeting.agenda_revisions.get(version=meeting.agenda_version)))!=len(session.agenda_payload(meeting)):raise ValidationError('أحد الموقعين لا يملك قراءة جميع البنود السرية.')
    payload=json.loads(json.dumps(minute_payload(meeting),default=str));payload['body']=body
    minutes.current_version+=1
    revision=MinuteRevision.objects.create(minutes=minutes,version=minutes.current_version,body=body,snapshot=payload,signatories=sorted(set(signatories)),secret=secret,digest=digest({'content':payload,'signatories':sorted(set(signatories))}),reason=reason,created_by=user)
    minutes.status='draft';minutes.version+=1;minutes.save();emit(meeting,user,'إصدار محضر',reason,{'revision':revision.pk,'digest':revision.digest,'correction':correction});return revision

@transaction.atomic
def minute_transition(user,revision,action,reason,password='',capacity='',version=None):
    meeting=MeetingRecord.objects.select_for_update().get(pk=revision.minutes.meeting_id);readable(user,meeting);minutes=Minutes.objects.select_for_update().get(pk=revision.minutes_id);reason=services.reason_required(reason)
    if revision.version!=minutes.current_version:raise ValidationError('الإجراء يخص الإصدار الحالي فقط.')
    if version!=minutes.version:raise ValidationError('تعارض إصدار المحضر.')
    if action=='review':
        services.require_manage(user,meeting)
        if minutes.status!='draft' or meeting.votes.filter(status='open').exists():raise ValidationError('أغلق التصويت وأكمل المسودة قبل عرض المحضر.')
        if not revision.body.strip():raise ValidationError('حرر مناقشات المحضر قبل عرضه.')
        minutes.status='review'
        for p in Member.objects.filter(pk__in=revision.signatories):Notification.objects.create(recipient=p.user,text=f'محضر بانتظار الاطلاع والتوقيع الداخلي غير الرسمي: /meetings/{meeting.pk}/minutes/')
    elif action=='sign':
        member=member_for(user,meeting)
        if not minute_visible(user,revision) or member.pk not in revision.signatories or minutes.status not in ['review','signed']:raise PermissionDenied('لا توقيع مخول على هذه النسخة.')
        if not user.check_password(password):raise ValidationError('أعد التحقق بكلمة مرور حسابك الحالية؛ لا توقيع دون تحقق.')
        if not capacity.strip():raise ValidationError('صفة الموقع مطلوبة.')
        if revision.signatures.filter(member=member).exists():raise ValidationError('وقعت النسخة مسبقًا.')
        MinuteSignature.objects.create(revision=revision,member=member,capacity=capacity,method='password_reauthentication_internal_unofficial',content_digest=revision.digest)
        if revision.signatures.count()==len(revision.signatories):minutes.status='signed'
    elif action=='final':
        services.require_manage(user,meeting)
        signed=set(revision.signatures.filter(content_digest=revision.digest).values_list('member_id',flat=True))
        if minutes.status!='signed' or signed!=set(revision.signatories):raise ValidationError('نقص توقيع مطلوب أو اختلاف بصمة المحتوى.')
        MinuteSeal.objects.create(revision=revision,digest=revision.digest,finalized_by=user);minutes.status='final'
        meeting.closed_at=timezone.now();meeting.save(update_fields=['closed_at'])
        for person in Attendance.objects.filter(eligibility__roster__meeting=meeting,eligibility__roster__generation=meeting.roster_generation,status__in=['present','remote']):
            end=min(meeting.closed_at,meeting.ends_at)
            if person.entered_at and end>person.entered_at:AttendanceSpan.objects.get_or_create(eligibility=person.eligibility,starts=person.entered_at,defaults={'ends':end})
            person.status='left';person.exited_at=end;person.verification='إنهاء الجلسة باعتماد المحضر';person.marked_by=user;person.save()
    else:raise ValidationError('إجراء محضر غير صالح.')
    minutes.version+=1;minutes.save();emit(meeting,user,'انتقال محضر',reason,{'revision':revision.pk,'action':action,'digest':revision.digest});return minutes

@transaction.atomic
def live_action(user,meeting,action,reason,version,item=None):
    meeting=session.locked(user,meeting);reason=services.reason_required(reason);state,_=LiveState.objects.get_or_create(meeting=meeting);state=LiveState.objects.select_for_update().get(pk=state.pk)
    if state.version!=version:raise ValidationError('تعارض تحديث المؤقت.')
    if not meeting.starts_at<=timezone.now()<=meeting.ends_at:raise ValidationError('أدوات التشغيل متاحة أثناء الاجتماع فقط.')
    if item and (item.meeting_id!=meeting.pk or item.removed):raise ValidationError('بند خارج الاجتماع.')
    if state.state=='running':state.elapsed_seconds+=max(0,int((timezone.now()-state.started_at).total_seconds()))
    if action not in ['start','pause','reset']:raise ValidationError('إجراء غير صالح.')
    state.state='running' if action=='start' else 'paused';state.started_at=timezone.now() if action=='start' else None
    if action=='reset':state.elapsed_seconds=0
    state.item=item;state.version+=1;state.save();emit(meeting,user,'مؤقت الاجتماع',reason,{'state':state.state,'elapsed':state.elapsed_seconds,'item':state.item_id});return state

@transaction.atomic
def speaker_action(user,meeting,action,member_id=None,speaker_id=None):
    meeting=MeetingRecord.objects.select_for_update().get(pk=meeting.pk);readable(user,meeting)
    if meeting.closed_at or meeting.status!='scheduled':raise ValidationError('الاجتماع غير مفتوح.')
    if action=='join':
        member=member_for(user,meeting)
        if Speaker.objects.filter(meeting=meeting,member=member,status__in=['waiting','speaking']).exists():raise ValidationError('أنت في الطابور بالفعل.')
        row=Speaker.objects.create(meeting=meeting,member=member)
    else:
        services.require_manage(user,meeting);row=Speaker.objects.get(pk=speaker_id,meeting=meeting)
        if action not in ['speaking','done'] or row.status=='done':raise ValidationError('انتقال متحدث غير صالح.')
        if action=='speaking' and Speaker.objects.filter(meeting=meeting,status='speaking').exclude(pk=row.pk).exists():raise ValidationError('أنه المتحدث الحالي أولًا.')
        row.status=action;row.save()
    emit(meeting,user,'طابور المتحدثين','إدارة دور الحديث',{'speaker':row.pk,'status':row.status});return row

@transaction.atomic
def save_note(user,meeting,body,strokes,shared,item=None,instance=None,version=0,page=1):
    meeting=MeetingRecord.objects.select_for_update().get(pk=meeting.pk);member_for(user,meeting)
    if item and (item.meeting_id!=meeting.pk or not session.may_read_item(user,meeting,{'id':item.pk,'kind':item.kind,'presenter':item.presenter_id,'owner':item.owner_id})):raise PermissionDenied()
    if shared and hasattr(meeting,'record_policy') and not meeting.record_policy.policy.sharing_allowed:raise PermissionDenied('سياسة التصنيف تمنع المشاركة.')
    if shared and item and item.kind=='secret':raise ValidationError('الملاحظات على بند سري تبقى خاصة؛ لا تشارك تلقائيًا.')
    if not isinstance(strokes,list) or len(json.dumps(strokes))>200000 or len(body)>20000:raise ValidationError('حجم الملاحظة أو الرسم غير مسموح.')
    for stroke in strokes:
        if not isinstance(stroke,list) or len(stroke)>1000 or any(not isinstance(p,list) or len(p)!=2 or any(not isinstance(n,(float,int)) or not 0<=n<=1 for n in p) for p in stroke):raise ValidationError('رسم غير صالح.')
    row=Note.objects.select_for_update().get(pk=instance.pk,meeting=meeting,owner=user) if instance else Note(meeting=meeting,owner=user)
    if instance and version!=row.version:raise ValidationError('تعارض حفظ الملاحظة؛ احتفظ بنسختك وأعد التحميل.')
    row.body=body;row.strokes=strokes;row.shared=shared;row.item=item;row.page=page;row.version+=1;row.full_clean();row.save()
    # Private note content and drawing never enter a shared audit stream.
    if shared:emit(meeting,user,'حفظ ملاحظة مشتركة','حفظ صريح للملاحظة',{'note':row.pk,'version':row.version})
    return row
