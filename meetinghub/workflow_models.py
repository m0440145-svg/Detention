from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator,MaxValueValidator
from .models import MeetingRecord,Member,AgendaItem,Eligibility,MeetingFile,ImmutableSnapshot,Committee

class Vote(models.Model):
    def __str__(self):return self.question
    meeting=models.ForeignKey(MeetingRecord,on_delete=models.PROTECT,related_name='votes')
    item=models.ForeignKey(AgendaItem,on_delete=models.PROTECT,related_name='votes')
    question=models.CharField(max_length=500)
    secret=models.BooleanField(default=False)
    purpose=models.CharField(max_length=12,choices=[('decision','قرار'),('poll','استطلاع')],default='decision')
    denominator=models.CharField(max_length=12,choices=[('eligible','المؤهلون المثبتون'),('cast','المشاركون بالتصويت')])
    threshold=models.DecimalField(max_digits=5,decimal_places=2,validators=[MinValueValidator(0.01),MaxValueValidator(100)])
    reference=models.CharField(max_length=300)
    closes_at=models.DateTimeField()
    opened_at=models.DateTimeField(null=True,blank=True)
    closed_at=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=12,choices=[('draft','مسودة'),('open','مفتوح'),('closed','مغلق'),('cancelled','ملغى')],default='draft')
    roster=models.ForeignKey('meetinghub.Roster',null=True,blank=True,on_delete=models.PROTECT)
    # Secret ballots only update this encrypted aggregate. No ballot row identifies a choice.
    aggregate_encrypted=models.TextField(blank=True,editable=False)
    electorate=models.JSONField(default=list,blank=True)
    version=models.PositiveIntegerField(default=1)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)

class Participation(ImmutableSnapshot):
    vote=models.ForeignKey(Vote,on_delete=models.PROTECT,related_name='participation')
    eligibility=models.ForeignKey(Eligibility,on_delete=models.PROTECT)
    represented_by=models.ForeignKey(Member,on_delete=models.PROTECT)
    class Meta:constraints=[models.UniqueConstraint(fields=['vote','eligibility'],name='one_vote_per_eligibility')]

class PublicBallot(ImmutableSnapshot):
    participation=models.OneToOneField(Participation,on_delete=models.PROTECT,related_name='public_ballot')
    choice=models.CharField(max_length=8,choices=[('yes','موافق'),('no','معارض'),('abstain','ممتنع')])
    created_at=models.DateTimeField(auto_now_add=True)

class VoteResult(ImmutableSnapshot):
    vote=models.OneToOneField(Vote,on_delete=models.PROTECT,related_name='result')
    totals=models.JSONField()
    electorate_count=models.PositiveIntegerField()
    denominator_count=models.PositiveIntegerField()
    outcome=models.CharField(max_length=12)
    digest=models.CharField(max_length=64)
    closed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)

class Minutes(models.Model):
    meeting=models.OneToOneField(MeetingRecord,on_delete=models.PROTECT,related_name='minutes')
    current_version=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=12,choices=[('draft','مسودة'),('review','معروض للاعتماد'),('signed','موقع داخليًا'),('final','نهائي')],default='draft')
    version=models.PositiveIntegerField(default=1)

class MinuteRevision(ImmutableSnapshot):
    minutes=models.ForeignKey(Minutes,on_delete=models.PROTECT,related_name='revisions')
    version=models.PositiveIntegerField()
    body=models.TextField()
    snapshot=models.JSONField()
    signatories=models.JSONField()
    secret=models.BooleanField(default=False)
    digest=models.CharField(max_length=64)
    reason=models.TextField()
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['minutes','version'],name='minute_revision_unique')]

class MinuteSignature(ImmutableSnapshot):
    revision=models.ForeignKey(MinuteRevision,on_delete=models.PROTECT,related_name='signatures')
    member=models.ForeignKey(Member,on_delete=models.PROTECT)
    capacity=models.CharField(max_length=150)
    method=models.CharField(max_length=100)
    content_digest=models.CharField(max_length=64)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['revision','member'],name='minute_signature_once')]

class MinuteSeal(ImmutableSnapshot):
    revision=models.OneToOneField(MinuteRevision,on_delete=models.PROTECT,related_name='seal')
    digest=models.CharField(max_length=64)
    finalized_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)

class SignatureRequest(models.Model):
    revision=models.OneToOneField(MinuteRevision,on_delete=models.PROTECT,related_name='official_request')
    import uuid
    nonce=models.UUIDField(default=uuid.uuid4,unique=True,editable=False)
    provider_id=models.CharField(max_length=200,blank=True)
    status=models.CharField(max_length=12,default='pending')
    summary_url=models.URLField(blank=True)
    verification_url=models.URLField(blank=True)
    provider_audit=models.JSONField(default=list,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)

class ProviderReceipt(ImmutableSnapshot):
    request=models.ForeignKey(SignatureRequest,on_delete=models.PROTECT,related_name='receipts')
    event_id=models.CharField(max_length=200,unique=True)
    digest=models.CharField(max_length=64)
    created_at=models.DateTimeField(auto_now_add=True)

class LiveState(models.Model):
    meeting=models.OneToOneField(MeetingRecord,on_delete=models.PROTECT,related_name='live_state')
    item=models.ForeignKey(AgendaItem,null=True,blank=True,on_delete=models.PROTECT)
    state=models.CharField(max_length=10,default='paused')
    elapsed_seconds=models.PositiveIntegerField(default=0)
    started_at=models.DateTimeField(null=True,blank=True)
    version=models.PositiveIntegerField(default=1)

class Speaker(models.Model):
    meeting=models.ForeignKey(MeetingRecord,on_delete=models.PROTECT,related_name='speakers')
    member=models.ForeignKey(Member,on_delete=models.PROTECT)
    status=models.CharField(max_length=10,default='waiting')
    created_at=models.DateTimeField(auto_now_add=True)

class Note(models.Model):
    meeting=models.ForeignKey(MeetingRecord,on_delete=models.PROTECT,related_name='notes')
    item=models.ForeignKey(AgendaItem,null=True,blank=True,on_delete=models.PROTECT)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    shared=models.BooleanField(default=False)
    body=models.TextField(blank=True)
    strokes=models.JSONField(default=list,blank=True)
    page=models.PositiveIntegerField(default=1)
    version=models.PositiveIntegerField(default=1)
    updated_at=models.DateTimeField(auto_now=True)

class ExecutionRecord(models.Model):
    def __str__(self):return self.decision.number
    decision=models.OneToOneField('core.Decision',on_delete=models.PROTECT,related_name='execution')
    revision=models.ForeignKey(MinuteRevision,on_delete=models.PROTECT)
    item=models.ForeignKey(AgendaItem,on_delete=models.PROTECT)
    supervisor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    team=models.ManyToManyField(settings.AUTH_USER_MODEL,related_name='execution_team')
    priority=models.CharField(max_length=20,default='normal')
    sla_hours=models.PositiveIntegerField(validators=[MinValueValidator(1)])
    escalation_hours=models.PositiveIntegerField(validators=[MinValueValidator(1)])
    output=models.TextField()
    deferred=models.BooleanField(default=False)
    status_reason=models.TextField(blank=True)
    version=models.PositiveIntegerField(default=1)

class ExecutionStage(models.Model):
    execution=models.ForeignKey(ExecutionRecord,on_delete=models.PROTECT,related_name='stages')
    title=models.CharField(max_length=250)
    due_date=models.DateField()
    progress=models.PositiveSmallIntegerField(default=0,validators=[MaxValueValidator(100)])
    evidence=models.ForeignKey('core.Attachment',null=True,blank=True,on_delete=models.PROTECT)
    link=models.URLField(blank=True)
    reason=models.TextField()

class DecisionAlert(ImmutableSnapshot):
    decision=models.ForeignKey('core.Decision',on_delete=models.PROTECT,related_name='meeting_alerts')
    key=models.CharField(max_length=200,unique=True)
    kind=models.CharField(max_length=20)
    created_at=models.DateTimeField(auto_now_add=True)

class Feedback(ImmutableSnapshot):
    meeting=models.ForeignKey(MeetingRecord,on_delete=models.PROTECT)
    member=models.ForeignKey(Member,on_delete=models.PROTECT)
    rating=models.PositiveSmallIntegerField(validators=[MinValueValidator(1),MaxValueValidator(5)])
    body=models.TextField()
    anonymous=models.BooleanField(default=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['meeting','member'],name='meeting_feedback_once')]
