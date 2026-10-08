"""Operational records for integrations, privacy, retention and verified identity."""
import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError

class Integration(models.Model):
    key=models.CharField(max_length=40,unique=True)
    kind=models.CharField(max_length=30,choices=[(x,x) for x in ['hr','email','sms','dms','signature','webhook']])
    endpoint=models.URLField()
    secret_env=models.CharField(max_length=80,help_text='اسم متغير البيئة؛ لا تحفظ المفتاح هنا')
    active=models.BooleanField(default=False)
    events=models.JSONField(default=list,blank=True)
    options=models.JSONField(default=dict,blank=True)
    last_sync=models.DateTimeField(null=True,blank=True)
    def __str__(self): return self.key

class Delivery(models.Model):
    uid=models.UUIDField(default=uuid.uuid4,unique=True)
    integration=models.ForeignKey(Integration,on_delete=models.PROTECT)
    event=models.CharField(max_length=60)
    payload=models.JSONField(default=dict)
    dedup=models.CharField(max_length=200,unique=True)
    state=models.CharField(max_length=20,default='pending')
    attempts=models.PositiveIntegerField(default=0)
    next_attempt=models.DateTimeField(default=timezone.now)
    lease_until=models.DateTimeField(null=True,blank=True)
    error=models.CharField(max_length=200,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    delivered_at=models.DateTimeField(null=True,blank=True)

class ExternalIdentity(models.Model):
    source=models.CharField(max_length=80)
    external_id=models.CharField(max_length=100)
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    synced_at=models.DateTimeField(default=timezone.now)
    class Meta: constraints=[models.UniqueConstraint(fields=['source','external_id'],name='hr_source_id_unique')]

class ExternalUnit(models.Model):
    source=models.CharField(max_length=80)
    external_id=models.CharField(max_length=100)
    unit=models.OneToOneField('core.Unit',on_delete=models.PROTECT)
    class Meta: constraints=[models.UniqueConstraint(fields=['source','external_id'],name='hr_unit_source_unique')]

class SyncReceipt(models.Model):
    source=models.CharField(max_length=80)
    event_id=models.CharField(max_length=100)
    digest=models.CharField(max_length=64)
    summary=models.JSONField(default=dict)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['source','event_id'],name='hr_event_unique')]

class NotificationPreference(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    email=models.BooleanField(default=True)
    critical_sms=models.BooleanField(default=False)
    daily_digest=models.BooleanField(default=True)
    last_digest=models.DateField(null=True,blank=True)

class MFADevice(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    secret=models.TextField()
    confirmed=models.BooleanField(default=False)
    last_step=models.BigIntegerField(default=-1)
    failed_attempts=models.PositiveIntegerField(default=0)
    locked_until=models.DateTimeField(null=True,blank=True)

class ExportGrant(models.Model):
    mail=models.ForeignKey('Correspondence',on_delete=models.PROTECT)
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    granted_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    expires=models.DateTimeField()
    download=models.BooleanField(default=False)
    printing=models.BooleanField(default=False)
    reason=models.TextField()

class ProcessingActivity(models.Model):
    name=models.CharField(max_length=200)
    purpose=models.TextField()
    legal_basis=models.TextField()
    categories=models.JSONField(default=list)
    recipients=models.TextField(blank=True)
    retention_reference=models.TextField()
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    updated_at=models.DateTimeField(auto_now=True)

class SubjectRequest(models.Model):
    uid=models.UUIDField(default=uuid.uuid4,unique=True)
    requester=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    kind=models.CharField(max_length=20,choices=[('access','اطلاع'),('correct','تصحيح'),('destroy','إتلاف'),('withdraw','سحب موافقة')])
    description=models.TextField()
    state=models.CharField(max_length=20,default='submitted')
    due_at=models.DateTimeField()
    response=models.TextField(blank=True)
    handled_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)

class RetentionCase(models.Model):
    uid=models.UUIDField(default=uuid.uuid4,unique=True)
    mail=models.ForeignKey('Correspondence',on_delete=models.PROTECT)
    action=models.CharField(max_length=20,choices=[('destroy','إتلاف المرفقات'),('permanent','حفظ دائم')])
    state=models.CharField(max_length=20,default='proposed')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    reason=models.TextField()
    minutes_reference=models.CharField(max_length=250,blank=True)
    proof=models.JSONField(default=dict)
    completed_at=models.DateTimeField(null=True,blank=True)

class RetentionApproval(models.Model):
    case=models.ForeignKey(RetentionCase,on_delete=models.PROTECT,related_name='approvals')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    reason=models.TextField()
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['case','actor'],name='retention_approval_unique')]

class LegalHold(models.Model):
    mail=models.ForeignKey('Correspondence',on_delete=models.PROTECT)
    reason=models.TextField()
    active=models.BooleanField(default=True)
    imposed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)

class SignatureRequest(models.Model):
    uid=models.UUIDField(default=uuid.uuid4,unique=True)
    mail=models.ForeignKey('Correspondence',on_delete=models.PROTECT,related_name='signature_requests')
    signer=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    reserved_reference=models.CharField(max_length=100,blank=True)
    pdf_hash=models.CharField(max_length=64,blank=True)
    content_hash=models.CharField(max_length=64)
    method=models.CharField(max_length=20)
    state=models.CharField(max_length=20,default='pending')
    expires=models.DateTimeField()
    signed_pdf=models.FileField(upload_to='signed/%Y/%m/',blank=True)
    evidence=models.JSONField(default=dict)
    completed_at=models.DateTimeField(null=True,blank=True)

class MigrationBatch(models.Model):
    uid=models.UUIDField(default=uuid.uuid4,unique=True)
    source=models.CharField(max_length=80)
    digest=models.CharField(max_length=64,unique=True)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    summary=models.JSONField(default=dict)
    committed=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)

class ReportSchedule(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    report=models.CharField(max_length=30)
    frequency=models.CharField(max_length=10,choices=[('daily','يومي'),('weekly','أسبوعي'),('monthly','شهري')])
    active=models.BooleanField(default=True)
    next_run=models.DateTimeField(default=timezone.now)
    last_run=models.DateTimeField(null=True,blank=True)

class APICallLog(models.Model):
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT)
    method=models.CharField(max_length=10)
    path=models.CharField(max_length=200)
    status=models.PositiveIntegerField()
    duration_ms=models.PositiveIntegerField()
    created_at=models.DateTimeField(auto_now_add=True)
