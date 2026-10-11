import base64,json
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from correspondence.security import seal,unseal
from .models import *
from . import services,governance_services as gov,workflow_services as workflow

@transaction.atomic
def create_election(user,committee,title,nominations_start,nominations_end,ballot_end,seats,reference,allow_proxy=False,proxy_limit=0,parent=None):
    gov.manage(user,committee);Committee.objects.select_for_update().get(pk=committee.pk)
    if not nominations_start<nominations_end<ballot_end or ballot_end<=timezone.now() or seats<1 or not reference.strip() or not title.strip():raise ValidationError('مواعيد الانتخابات والمقاعد والمرجع مطلوبة ومرتبة.')
    if (allow_proxy and proxy_limit<1) or (not allow_proxy and proxy_limit):raise ValidationError('حدد حد الوكالات وفق اللائحة عند السماح بها.')
    if parent and (parent.committee_id!=committee.pk or parent.status!='tie'):raise ValidationError('جولة الحسم تحتاج انتخابات سابقة متعادلة في اللجنة.')
    row=Election(committee=committee,title=title,nominations_start=nominations_start,nominations_end=nominations_end,ballot_end=ballot_end,seats=seats,reference=reference,allow_proxy=allow_proxy,proxy_limit=proxy_limit,parent=parent);row.full_clean();row.save()
    services.event(f'committee:{committee.pk}',user,'دورة انتخابات','قواعد مصدرها لائحة الجهة',{'election':row.pk,'parent':row.parent_id});return row

@transaction.atomic
def nominate(user,election,statement,document):
    election=Election.objects.select_for_update().get(pk=election.pk);person=gov.member(user,election.committee)
    if election.status!='nomination' or not election.nominations_start<=timezone.now()<election.nominations_end:raise ValidationError('نافذة الترشح مغلقة.')
    if person.role=='observer':raise PermissionDenied('المراقب غير مؤهل للترشح.')
    if document.committee_id!=election.committee_id or document.owner_id!=user.pk:raise PermissionDenied('ملف المرشح غير مخول.')
    if election.parent_id and person.pk not in election.parent.result.tied:raise ValidationError('جولة الحسم للمرشحين المتعادلين فقط.')
    if election.candidates.filter(member=person).exists():raise ValidationError('ترشح مكرر.')
    row=Candidate.objects.create(election=election,member=person,statement=statement,document=document)
    services.event(f'committee:{election.committee_id}',user,'طلب ترشح','ترشح ضمن النافذة',{'candidate':row.pk});return row

@transaction.atomic
def review_candidate(user,candidate,approved,reason):
    election=Election.objects.select_for_update().get(pk=candidate.election_id);gov.manage(user,election.committee);reason=services.reason_required(reason)
    if election.status!='nomination':raise ValidationError('القائمة مثبتة؛ لا تعدل أهلية المرشح.')
    if candidate.member.user_id==user.pk:raise PermissionDenied('لا اعتماد ذاتي للترشح.')
    candidate.approved=approved;candidate.reviewed_by=user;candidate.review_reason=reason;candidate.save();services.event(f'committee:{election.committee_id}',user,'مراجعة أهلية مرشح',reason,{'candidate':candidate.pk,'approved':approved});return candidate

@transaction.atomic
def freeze_election(user,election,reason,version):
    election=Election.objects.select_for_update().get(pk=election.pk);gov.manage(user,election.committee);reason=services.reason_required(reason)
    if election.version!=version or election.status!='nomination' or not election.nominations_end<=timezone.now()<election.ballot_end:raise ValidationError('لا تثبيت قبل انتهاء الترشيح أو بعد انتهاء التصويت أو بتعارض إصدار.')
    voters=list(services.active_members().filter(committees=election.committee).exclude(role='observer').order_by('pk').values_list('pk',flat=True))
    candidates=list(election.candidates.filter(approved=True,member__in=services.active_members()).order_by('member_id').values('member_id','member__user__first_name','member__user__last_name','member__user__username'))
    if not voters or len(candidates)<election.seats:raise ValidationError('عدد المرشحين المعتمدين أو الناخبين غير كافٍ.')
    roll=VoterRoll.objects.create(election=election,candidates=candidates,voters=voters,digest=workflow.digest({'candidates':candidates,'voters':voters,'reference':election.reference,'seats':election.seats}))
    election.aggregate_encrypted=base64.b64encode(seal(json.dumps({str(c['member_id']):0 for c in candidates}).encode())).decode();election.status='ballot';election.version+=1;election.save()
    services.event(f'committee:{election.committee_id}',user,'تثبيت قائمة انتخابات',reason,{'election':election.pk,'digest':roll.digest,'voters':len(voters),'candidates':len(candidates)});return roll

@transaction.atomic
def approve_election_proxy(user,election,principal,delegate,document,valid_until,reason):
    election=Election.objects.select_for_update().get(pk=election.pk);gov.manage(user,election.committee);reason=services.reason_required(reason)
    if election.status!='ballot' or not election.allow_proxy or not election.proxy_limit:raise ValidationError('الانتخابات لا تسمح بالإنابة في هذه الحالة.')
    if principal.pk==delegate.pk or principal.pk not in election.roll.voters or delegate.pk not in election.roll.voters or not principal.active or not delegate.active:raise ValidationError('طرفا الإنابة مؤهلان ومختلفان.')
    if document.committee_id!=election.committee_id or not timezone.now()<valid_until<=election.ballot_end:raise ValidationError('وثيقة أو مدة إنابة غير صالحة.')
    live=ElectionProxy.objects.filter(election=election,valid_until__gt=timezone.now())
    if live.filter(delegate=delegate).count()>=election.proxy_limit or live.filter(Q(delegate=principal)|Q(principal=delegate)).exists():raise ValidationError('تجاوز الحد أو سلسلة إنابات.')
    if ElectionParticipation.objects.filter(election=election,member=principal).exists() or ElectionProxy.objects.filter(election=election,principal=principal).exists():raise ValidationError('مشاركة أو إنابة سابقة.')
    row=ElectionProxy.objects.create(election=election,principal=principal,delegate=delegate,document=document,valid_until=valid_until);services.event(f'committee:{election.committee_id}',user,'إنابة انتخابات',reason,{'proxy':row.pk});return row

@transaction.atomic
def cast_election(user,election,choices,represented=None):
    election=Election.objects.select_for_update().get(pk=election.pk);person=gov.member(user,election.committee)
    if election.status!='ballot' or timezone.now()>=election.ballot_end:raise ValidationError('الاقتراع غير مفتوح.')
    represented=represented or person
    if person.pk not in election.roll.voters or represented.pk not in election.roll.voters or not represented.active:raise PermissionDenied('لا أهلية انتخابية.')
    if represented.pk!=person.pk and not ElectionProxy.objects.filter(election=election,principal=represented,delegate=person,valid_until__gt=timezone.now()).exists():raise PermissionDenied('لا إنابة انتخابية سارية.')
    permitted={c['member_id'] for c in election.roll.candidates}
    if not isinstance(choices,list) or len(set(choices))!=len(choices) or not 1<=len(choices)<=election.seats or not set(choices)<=permitted:raise ValidationError('اختر مرشحين مثبتين دون تكرار وبحد المقاعد.')
    if election.participation.filter(member=represented).exists():raise ValidationError('اقتراع مكرر.')
    proof=ElectionParticipation.objects.create(election=election,member=represented,represented_by=person)
    counts=json.loads(unseal(base64.b64decode(election.aggregate_encrypted)))
    for key in choices:counts[str(key)]+=1
    election.aggregate_encrypted=base64.b64encode(seal(json.dumps(counts).encode())).decode();election.save(update_fields=['aggregate_encrypted'])
    services.event(f'committee:{election.committee_id}',user,'إثبات اقتراع انتخابي','الاختيارات السرية غير مرتبطة بإثبات المشاركة',{'election':election.pk,'member':represented.pk});return proof

@transaction.atomic
def close_election(user,election,reason,version):
    election=Election.objects.select_for_update().get(pk=election.pk);gov.manage(user,election.committee);reason=services.reason_required(reason)
    if election.status!='ballot' or election.version!=version or timezone.now()<election.ballot_end:raise ValidationError('الإغلاق بعد الموعد وبالإصدار الحالي فقط.')
    counts=json.loads(unseal(base64.b64decode(election.aggregate_encrypted)));ranked=sorted(((int(k),v) for k,v in counts.items()),key=lambda pair:(-pair[1],pair[0]))
    threshold=ranked[election.seats-1][1];tied=[pk for pk,v in ranked if v==threshold]
    above=[pk for pk,v in ranked if v>threshold];tie_needed=len(above)+len(tied)>election.seats
    winners=above if tie_needed else [pk for pk,v in ranked[:election.seats]]
    reserves=[pk for pk,v in ranked if pk not in winners and (not tie_needed or pk not in tied)]
    if not election.participation.exists():raise ValidationError('لا نتيجة دون مشاركة فعلية؛ مدّد بإصدار انتخابات جديد.')
    payload={'totals':counts,'winners':winners,'reserves':reserves,'tied':tied if tie_needed else []}
    result=ElectionResult.objects.create(election=election,**payload,digest=workflow.digest(payload));election.status='tie' if tie_needed else 'closed';election.version+=1;election.save();services.event(f'committee:{election.committee_id}',user,'محضر نتيجة انتخابات',reason,{'election':election.pk,'digest':result.digest,'tie':tie_needed});return result
