"""Meeting governance extends core.Meeting; decisions retain their existing FK."""
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator,MaxValueValidator
from django.db import models
from django.utils import timezone
from core.models import Meeting

class MemberRole(models.TextChoices):
    CHAIR='chair','رئيس المجلس'
    SECRETARY='secretary','أمين السر'
    MEMBER='member','عضو'
    OBSERVER='observer','مراقب'

class Member(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='meeting_member')
    role=models.CharField(max_length=20,choices=MemberRole.choices,default=MemberRole.MEMBER)
    organization=models.CharField(max_length=200,blank=True)
    capacity=models.CharField(max_length=150,blank=True)
    starts=models.DateField()
    ends=models.DateField(null=True,blank=True)
    archived=models.BooleanField(default=False,db_index=True)
    identity_hash=models.CharField(max_length=64,unique=True,null=True,blank=True,editable=False)
    identity_encrypted=models.TextField(blank=True,editable=False)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(ends__isnull=True)|models.Q(ends__gte=models.F('starts')),name='meeting_member_period'),models.CheckConstraint(condition=models.Q(role__in=MemberRole.values),name='meeting_member_role')]
    def clean(self):
        if self.ends and self.starts and self.ends<self.starts:raise ValidationError('نهاية العضوية يجب ألا تسبق بدايتها.')
        if self.user_id and self.user.role=='admin':raise ValidationError('مسؤول النظام يدير العضويات ولا يمنح عضوية تشغيلية لنفسه.')
    @property
    def active(self):
        today=timezone.localdate()
        return not self.archived and self.user.is_active and self.user.role!='admin' and self.starts<=today and (not self.ends or self.ends>=today)
    def __str__(self):return str(self.user)

class Committee(models.Model):
    name=models.CharField(max_length=200,unique=True)
    kind=models.CharField(max_length=20,choices=[('board','مجلس الإدارة'),('assembly','الجمعية العمومية'),('committee','لجنة')])
    mandate=models.TextField()
    chair=models.ForeignKey(Member,on_delete=models.PROTECT,related_name='chaired_committees')
    secretary=models.ForeignKey(Member,on_delete=models.PROTECT,related_name='secretary_committees')
    members=models.ManyToManyField(Member,related_name='committees')
    starts=models.DateField()
    ends=models.DateField(null=True,blank=True)
    archived=models.BooleanField(default=False,db_index=True)
    version=models.PositiveIntegerField(default=1)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(ends__isnull=True)|models.Q(ends__gte=models.F('starts')),name='meeting_committee_period')]
    def clean(self):
        if self.ends and self.starts and self.ends<self.starts:raise ValidationError('نهاية اللجنة تسبق بدايتها.')
        if self.chair_id and (self.chair.role!=MemberRole.CHAIR or not self.chair.active):raise ValidationError('اختر رئيسًا بعضوية نشطة ودور رئيس المجلس.')
        if self.secretary_id and (self.secretary.role!=MemberRole.SECRETARY or not self.secretary.active):raise ValidationError('اختر أمين سر بعضوية نشطة.')
        if self.chair_id and self.chair_id==self.secretary_id:raise ValidationError('الرئيس وأمين السر شخصان مستقلان.')
    @property
    def active(self):
        today=timezone.localdate()
        return not self.archived and self.starts<=today and (not self.ends or self.ends>=today)
    def __str__(self):return self.name

class MeetingRecord(models.Model):
    meeting=models.OneToOneField(Meeting,on_delete=models.PROTECT,related_name='governance')
    committee=models.ForeignKey(Committee,on_delete=models.PROTECT,related_name='meetings')
    kind=models.CharField(max_length=20,choices=[('ordinary','عادي'),('extraordinary','غير عادي'),('emergency','طارئ')])
    mode=models.CharField(max_length=20,choices=[('onsite','حضوري'),('remote','عن بعد'),('hybrid','هجين')])
    starts_at=models.DateTimeField()
    ends_at=models.DateTimeField()
    venue=models.CharField(max_length=250,blank=True)
    connection_url=models.URLField(blank=True)
    rsvp_deadline=models.DateTimeField()
    quorum_percent=models.DecimalField(max_digits=5,decimal_places=2,validators=[MinValueValidator(Decimal("0.01")),MaxValueValidator(100)])
    quorum_reference=models.CharField(max_length=250)
    allow_proxy=models.BooleanField(default=False)
    proxy_limit=models.PositiveSmallIntegerField(default=0)
    secrecy=models.CharField(max_length=20,choices=[('internal','داخلي'),('secret','سري')],default='internal')
    invitees=models.ManyToManyField(Member,related_name='meetings')
    status=models.CharField(max_length=20,choices=[('draft','مسودة'),('scheduled','مجدول'),('cancelled','ملغى')],default='draft',db_index=True)
    archived=models.BooleanField(default=False,db_index=True)
    version=models.PositiveIntegerField(default=1)
    agenda_version=models.PositiveIntegerField(default=0)
    roster_generation=models.PositiveIntegerField(default=0)
    final_reminder_hours=models.PositiveSmallIntegerField(default=6,validators=[MinValueValidator(1),MaxValueValidator(168)])
    meeting_reminder_hours=models.PositiveSmallIntegerField(default=24,validators=[MinValueValidator(1),MaxValueValidator(168)])
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['starts_at','pk']
        constraints=[models.CheckConstraint(condition=models.Q(ends_at__gt=models.F('starts_at')),name='meeting_time_order'),models.CheckConstraint(condition=models.Q(rsvp_deadline__lte=models.F('starts_at')),name='meeting_rsvp_order'),models.CheckConstraint(condition=models.Q(quorum_percent__gt=0)&models.Q(quorum_percent__lte=100),name='meeting_quorum_range'),models.CheckConstraint(condition=models.Q(status__in=['draft','scheduled','cancelled']),name='meeting_hub_status')]
    def clean(self):
        errors={}
        if self.starts_at and self.ends_at and self.ends_at<=self.starts_at:errors['ends_at']='نهاية الاجتماع يجب أن تتبع بدايته.'
        if self.starts_at and self.rsvp_deadline and self.rsvp_deadline>self.starts_at:errors['rsvp_deadline']='مهلة التأكيد لا تتجاوز بداية الاجتماع.'
        if self.mode in ['onsite','hybrid'] and not self.venue.strip():errors['venue']='حدد مكان الانعقاد.'
        if self.mode in ['remote','hybrid'] and not self.connection_url:errors['connection_url']='حدد رابط الاتصال.'
        if self.connection_url and not self.connection_url.startswith('https://'):errors['connection_url']='رابط الاتصال يجب أن يستخدم HTTPS.'
        if not self.quorum_reference.strip():errors['quorum_reference']='اكتب مرجع نسبة النصاب من النظام الأساسي للجهة.'
        if self.allow_proxy and not self.proxy_limit:errors['proxy_limit']='حدد الحد المسموح للتوكيلات.'
        if not self.allow_proxy and self.proxy_limit:errors['proxy_limit']='حد التوكيل يكون صفرًا عند عدم السماح به.'
        if self.committee_id and not self.committee.active:errors['committee']='اللجنة غير نشطة.'
        if errors:raise ValidationError(errors)
    def __str__(self):return str(self.meeting)

class AuditStream(models.Model):
    scope=models.CharField(max_length=80,unique=True)

class Event(models.Model):
    stream=models.ForeignKey(AuditStream,on_delete=models.PROTECT,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    action=models.CharField(max_length=150)
    reason=models.TextField()
    details=models.JSONField(default=dict)
    created_at=models.DateTimeField()
    previous_hash=models.CharField(max_length=64,blank=True)
    digest=models.CharField(max_length=64)
    class Meta:ordering=['pk']
    def save(self,*args,**kwargs):
        if self.pk:raise ValidationError('سجل الاجتماعات لا يقبل التعديل.')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):raise ValidationError('سجل الاجتماعات لا يقبل الحذف.')

from .session_models import *
