from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
from .models import Committee,MeetingRecord,Member,AgendaItem,ImmutableSnapshot

class Document(models.Model):
    def __str__(self):return self.name
    committee=models.ForeignKey(Committee,on_delete=models.PROTECT)
    meeting=models.ForeignKey(MeetingRecord,null=True,blank=True,on_delete=models.PROTECT)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    file=models.FileField(upload_to='governance-files/%Y/%m/')
    name=models.CharField(max_length=250)
    sha256=models.CharField(max_length=64)
    size=models.PositiveIntegerField()
    secret=models.BooleanField(default=True)
    created_at=models.DateTimeField(auto_now_add=True)

class GovernanceEntry(models.Model):
    decision=models.ForeignKey('core.Decision',null=True,blank=True,on_delete=models.PROTECT,related_name='governance_recommendations')
    task=models.ForeignKey('core.Task',null=True,blank=True,on_delete=models.PROTECT,related_name='governance_recommendations')
    feedback_source=models.OneToOneField('meetinghub.Feedback',null=True,blank=True,on_delete=models.PROTECT)
    @property
    def display_fields(self):
        labels={'regulator':'الجهة الرقابية','article':'المادة','action':'الإجراء','frequency':'الدورية','gap':'الفجوة','remediation':'المعالجة','official_url':'رابط المصدر الرسمي','last_verified':'تاريخ التحقق','legal_status':'حالة التحقق','period':'الفترة','source':'المصدر','amount':'المبلغ','category':'التصنيف','priority':'الأولوية','expected_impact':'الأثر المتوقع','measured_impact':'الأثر المقاس','review_date':'تاريخ المراجعة','resource':'المورد','quantity':'الكمية','starts':'البداية','ends':'النهاية','requirements':'المتطلبات','task':'المهمة','hours':'الساعات','number':'رقم الغرفة','severity':'الحدة'}
        return [(labels.get(k,'بيان إضافي'),{'attendance':'حضور فعلي','task':'مهمة معتمدة','normal':'عادية','high':'عالية','critical':'حرجة'}.get(str(v),v)) for k,v in self.data.items()]
    def get_status_display(self):return {'draft':'مسودة','review':'قيد المراجعة','approved':'معتمد','complete':'مكتمل','ended':'منتهٍ','cancelled':'ملغى'}.get(self.status,self.status)
    def __str__(self):return self.title
    KINDS=[('compliance','التزامات الامتثال'),('finance','المؤشرات المالية'),('recommendation','التوصيات والأثر'),('service','خدمات وتجهيزات الاجتماع'),('volunteer','الساعات التطوعية'),('crisis','غرفة الطوارئ')]
    kind=models.CharField(max_length=20,choices=KINDS,db_index=True)
    committee=models.ForeignKey(Committee,on_delete=models.PROTECT)
    meeting=models.ForeignKey(MeetingRecord,null=True,blank=True,on_delete=models.PROTECT)
    member=models.ForeignKey(Member,null=True,blank=True,on_delete=models.PROTECT)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    title=models.CharField(max_length=250)
    data=models.JSONField(default=dict)
    due_date=models.DateField(null=True,blank=True,db_index=True)
    status=models.CharField(max_length=20,default='draft')
    document=models.ForeignKey(Document,null=True,blank=True,on_delete=models.PROTECT)
    secret=models.BooleanField(default=True)
    archived=models.BooleanField(default=False)
    version=models.PositiveIntegerField(default=1)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    @property
    def deadline_label(self):
        from django.utils import timezone
        if not self.due_date or self.status in ['complete','approved','ended','cancelled']:return ''
        days=(self.due_date-timezone.localdate()).days
        return f'متأخر {-days} يوم' if days<0 else ('مستحق اليوم' if days==0 else (f'قريب خلال {days} يوم' if days<=30 else f'متبقي {days} يوم'))

class Disclosure(models.Model):
    meeting=models.ForeignKey(MeetingRecord,on_delete=models.PROTECT,related_name='disclosures')
    item=models.ForeignKey(AgendaItem,on_delete=models.PROTECT)
    member=models.ForeignKey(Member,on_delete=models.PROTECT)
    body=models.TextField()
    kind=models.CharField(max_length=100)
    recused=models.BooleanField(default=False)
    approved=models.BooleanField(default=False)
    document=models.ForeignKey(Document,null=True,blank=True,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['meeting','item','member'],name='one_item_disclosure')]

class Idea(models.Model):
    committee=models.ForeignKey(Committee,on_delete=models.PROTECT)
    body=models.TextField()
    status=models.CharField(max_length=12,default='submitted')
    recommendation=models.OneToOneField(GovernanceEntry,null=True,blank=True,on_delete=models.PROTECT)
    # Deliberately no owner, submission time or identity-reference field.

class IdeaVote(ImmutableSnapshot):
    idea=models.ForeignKey(Idea,on_delete=models.PROTECT,related_name='votes')
    voter_hash=models.CharField(max_length=64)
    class Meta:constraints=[models.UniqueConstraint(fields=['idea','voter_hash'],name='one_idea_vote')]

class Election(models.Model):
    def __str__(self):return self.title
    committee=models.ForeignKey(Committee,on_delete=models.PROTECT)
    title=models.CharField(max_length=250)
    nominations_start=models.DateTimeField()
    nominations_end=models.DateTimeField()
    ballot_end=models.DateTimeField()
    seats=models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    reference=models.CharField(max_length=300)
    allow_proxy=models.BooleanField(default=False)
    proxy_limit=models.PositiveSmallIntegerField(default=0)
    status=models.CharField(max_length=15,default='nomination')
    aggregate_encrypted=models.TextField(blank=True,editable=False)
    version=models.PositiveIntegerField(default=1)
    parent=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT)

class Candidate(models.Model):
    election=models.ForeignKey(Election,on_delete=models.PROTECT,related_name='candidates')
    member=models.ForeignKey(Member,on_delete=models.PROTECT)
    statement=models.TextField()
    document=models.ForeignKey(Document,on_delete=models.PROTECT)
    approved=models.BooleanField(default=False)
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT)
    review_reason=models.TextField(blank=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['election','member'],name='election_candidate_unique')]

class VoterRoll(ImmutableSnapshot):
    election=models.OneToOneField(Election,on_delete=models.PROTECT,related_name='roll')
    candidates=models.JSONField()
    voters=models.JSONField()
    digest=models.CharField(max_length=64)
    created_at=models.DateTimeField(auto_now_add=True)

class ElectionProxy(models.Model):
    election=models.ForeignKey(Election,on_delete=models.PROTECT)
    principal=models.ForeignKey(Member,on_delete=models.PROTECT,related_name='+')
    delegate=models.ForeignKey(Member,on_delete=models.PROTECT,related_name='+')
    document=models.ForeignKey(Document,on_delete=models.PROTECT)
    valid_until=models.DateTimeField()
    class Meta:constraints=[models.UniqueConstraint(fields=['election','principal'],name='election_proxy_unique')]

class ElectionParticipation(ImmutableSnapshot):
    election=models.ForeignKey(Election,on_delete=models.PROTECT,related_name='participation')
    member=models.ForeignKey(Member,on_delete=models.PROTECT)
    represented_by=models.ForeignKey(Member,on_delete=models.PROTECT,related_name='+')
    class Meta:constraints=[models.UniqueConstraint(fields=['election','member'],name='election_one_ballot')]

class ElectionResult(ImmutableSnapshot):
    election=models.OneToOneField(Election,on_delete=models.PROTECT,related_name='result')
    totals=models.JSONField()
    winners=models.JSONField()
    reserves=models.JSONField()
    tied=models.JSONField()
    digest=models.CharField(max_length=64)
    created_at=models.DateTimeField(auto_now_add=True)

class PlatformRevision(ImmutableSnapshot):
    version=models.PositiveIntegerField(unique=True)
    content=models.JSONField()
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    reason=models.TextField()
    created_at=models.DateTimeField(auto_now_add=True)

class PlatformSettings(models.Model):
    # Singleton with draft and published pointers. Publishing is explicit.
    draft=models.ForeignKey(PlatformRevision,null=True,on_delete=models.PROTECT,related_name='+')
    published=models.ForeignKey(PlatformRevision,null=True,on_delete=models.PROTECT,related_name='+')
    version=models.PositiveIntegerField(default=1)

class FeatureState(models.Model):
    def get_state_display(self):return {'works':'يعمل — بحسب بيئة الاختبار','ready_to_connect':'جاهز للربط','disabled':'معطل','error':'خطأ'}.get(self.state,self.state)
    key=models.CharField(max_length=60,unique=True)
    name=models.CharField(max_length=200)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT)
    state=models.CharField(max_length=20,default='ready_to_connect')
    requirements=models.TextField()
    tested_at=models.DateTimeField(null=True)
    environment=models.CharField(max_length=20,blank=True)
    evidence=models.TextField(blank=True)
    message=models.TextField()

class ActivationRequest(models.Model):
    def get_status_display(self):return {'new':'جديد','review':'قيد المراجعة','waiting':'بانتظار البيانات','approved':'مقبول إداريًا','rejected':'مرفوض','complete':'اكتمل الطلب الإداري'}.get(self.status,self.status)
    feature=models.ForeignKey(FeatureState,on_delete=models.PROTECT)
    requester=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    technical_owner=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT,related_name='+')
    reason=models.TextField()
    missing=models.TextField()
    status=models.CharField(max_length=20,default='new')
    notes=models.TextField(blank=True)
    version=models.PositiveIntegerField(default=1)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['feature'],condition=models.Q(status__in=['new','review','waiting','approved']),name='one_open_activation')]

class ClassificationPolicy(models.Model):
    level=models.CharField(max_length=20,unique=True)
    days=models.PositiveIntegerField(null=True,blank=True)
    reference=models.TextField()
    download_allowed=models.BooleanField(default=True)
    sharing_allowed=models.BooleanField(default=False)
    version=models.PositiveIntegerField(default=1)

class RecordPolicy(models.Model):
    meeting=models.OneToOneField(MeetingRecord,on_delete=models.PROTECT,related_name='record_policy')
    policy=models.ForeignKey(ClassificationPolicy,on_delete=models.PROTECT)
    legal_hold=models.BooleanField(default=False)
    hold_reason=models.TextField(blank=True)
    expires_at=models.DateTimeField(null=True,blank=True)
    # Audit/snapshot retention is conservative: no irreversible deletion endpoint.

class TourCompletion(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    version=models.CharField(max_length=20)
    completed_at=models.DateTimeField(auto_now=True)

class DestructionRequest(models.Model):
    document=models.OneToOneField(Document,on_delete=models.PROTECT)
    reason=models.TextField()
    requester=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    approved_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT,related_name='+')
    executed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT,related_name='+')
    executed_at=models.DateTimeField(null=True)

class OTPChallenge(models.Model):
    import uuid
    nonce=models.UUIDField(default=uuid.uuid4,unique=True)
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    code_hash=models.CharField(max_length=64)
    expires_at=models.DateTimeField()
    attempts=models.PositiveSmallIntegerField(default=0)
    used_at=models.DateTimeField(null=True)
    delivered=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)

class Resource(models.Model):
    committee=models.ForeignKey(Committee,on_delete=models.PROTECT)
    name=models.CharField(max_length=150)
    capacity=models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    active=models.BooleanField(default=True)
    version=models.PositiveIntegerField(default=1)
    def __str__(self):return f'{self.committee}: {self.name} ({self.capacity})'
    class Meta:constraints=[models.UniqueConstraint(fields=['committee','name'],name='committee_resource_unique')]
