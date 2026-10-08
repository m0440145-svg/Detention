"""Phase-two persistent agenda, invitations, attendance and reminder records."""
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator,MaxValueValidator
from .models import MeetingRecord,Member

class AgendaItem(models.Model):
    meeting=models.ForeignKey(MeetingRecord,on_delete=models.PROTECT,related_name='agenda_items')
    parent=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='children')
    title=models.CharField(max_length=250)
    kind=models.CharField(max_length=20,choices=[('briefing','إحاطة'),('discussion','نقاش'),('decision','قرار'),('vote','تصويت'),('minutes','اعتماد محضر'),('secret','سري'),('information','معلومات')])
    duration=models.PositiveSmallIntegerField(validators=[MinValueValidator(1),MaxValueValidator(1440)])
    presenter=models.ForeignKey(Member,on_delete=models.PROTECT,related_name='+')
    owner=models.ForeignKey(Member,on_delete=models.PROTECT,related_name='+')
    link=models.URLField(blank=True)
    previous_decision=models.ForeignKey('core.Decision',null=True,blank=True,on_delete=models.PROTECT)
    notes=models.TextField(blank=True)
    position=models.PositiveIntegerField(default=0)
    removed=models.BooleanField(default=False)
    class Meta:ordering=['position','pk']

class ImmutableSnapshot(models.Model):
    class Meta:abstract=True
    def save(self,*args,**kwargs):
        if self.pk:raise ValidationError('النسخة المنشورة ثابتة؛ أنشئ إصدارًا جديدًا.')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):raise ValidationError('السجل لا يقبل الحذف.')

class AgendaRevision(ImmutableSnapshot):
    meeting=models.ForeignKey(MeetingRecord,on_delete=models.PROTECT,related_name='agenda_revisions')
    version=models.PositiveIntegerField()
    items=models.JSONField()
    digest=models.CharField(max_length=64)
    reason=models.TextField()
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['meeting','version'],name='meeting_agenda_version')]

class MeetingFile(models.Model):
    meeting=models.ForeignKey(MeetingRecord,on_delete=models.PROTECT,related_name='files')
    purpose=models.CharField(max_length=10,choices=[('agenda','جدول الأعمال'),('proxy','وثيقة توكيل')],default='agenda')
    protected=models.BooleanField(default=False)
    item=models.ForeignKey(AgendaItem,null=True,blank=True,on_delete=models.PROTECT,related_name='files')
    file=models.FileField(upload_to='meeting-files/%Y/%m/')
    name=models.CharField(max_length=250)
    size=models.PositiveIntegerField()
    sha256=models.CharField(max_length=64)
    uploaded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)

class ReadAcknowledgment(ImmutableSnapshot):
    revision=models.ForeignKey(AgendaRevision,on_delete=models.PROTECT,related_name='acknowledgments')
    member=models.ForeignKey(Member,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['revision','member'],name='meeting_agenda_read_once')]

class Roster(ImmutableSnapshot):
    meeting=models.ForeignKey(MeetingRecord,on_delete=models.PROTECT,related_name='rosters')
    generation=models.PositiveIntegerField()
    quorum_percent=models.DecimalField(max_digits=5,decimal_places=2)
    quorum_reference=models.CharField(max_length=250)
    eligible_count=models.PositiveIntegerField()
    required=models.PositiveIntegerField()
    digest=models.CharField(max_length=64)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['meeting','generation'],name='meeting_roster_generation')]

class Eligibility(ImmutableSnapshot):
    roster=models.ForeignKey(Roster,on_delete=models.PROTECT,related_name='people')
    member=models.ForeignKey(Member,on_delete=models.PROTECT)
    name=models.CharField(max_length=200)
    role=models.CharField(max_length=20)
    invited=models.BooleanField()
    quorum_eligible=models.BooleanField()
    voting_eligible=models.BooleanField()
    class Meta:constraints=[models.UniqueConstraint(fields=['roster','member'],name='meeting_roster_member')]

class Invitation(models.Model):
    eligibility=models.OneToOneField(Eligibility,on_delete=models.PROTECT,related_name='invitation')
    token_hash=models.CharField(max_length=64,unique=True)
    expires_at=models.DateTimeField()
    revoked_at=models.DateTimeField(null=True,blank=True)
    response=models.CharField(max_length=15,choices=[('pending','لم يرد'),('confirm','أكد الحضور'),('decline','اعتذر'),('proxy','طلب حضور بالتوكيل')],default='pending')
    responded_at=models.DateTimeField(null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)

class RSVPAttempt(models.Model):
    key=models.CharField(max_length=64,db_index=True)
    created_at=models.DateTimeField(auto_now_add=True,db_index=True)

class Attendance(models.Model):
    eligibility=models.OneToOneField(Eligibility,on_delete=models.PROTECT,related_name='attendance')
    status=models.CharField(max_length=20,choices=[('present','حاضر'),('remote','حاضر عن بعد'),('left','غادر'),('disconnected','فقد الاتصال'),('absent','متغيب')],default='absent')
    first_entered_at=models.DateTimeField(null=True,blank=True)
    entered_at=models.DateTimeField(null=True,blank=True)
    exited_at=models.DateTimeField(null=True,blank=True)
    verification=models.CharField(max_length=200)
    marked_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    updated_at=models.DateTimeField(auto_now=True)

class Proxy(models.Model):
    roster=models.ForeignKey(Roster,on_delete=models.PROTECT,related_name='proxies')
    principal=models.ForeignKey(Eligibility,on_delete=models.PROTECT,related_name='given_proxies')
    delegate=models.ForeignKey(Eligibility,on_delete=models.PROTECT,related_name='received_proxies')
    document=models.ForeignKey(MeetingFile,on_delete=models.PROTECT,related_name='proxy_records')
    valid_until=models.DateTimeField()
    attendance_allowed=models.BooleanField(default=False)
    voting_allowed=models.BooleanField(default=False)
    revoked_at=models.DateTimeField(null=True,blank=True)
    approved_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:constraints=[models.UniqueConstraint(fields=['roster','principal'],condition=models.Q(revoked_at__isnull=True),name='meeting_one_live_proxy'),models.CheckConstraint(condition=~models.Q(principal=models.F('delegate')),name='meeting_proxy_distinct')]

class Reminder(models.Model):
    invitation=models.ForeignKey(Invitation,on_delete=models.PROTECT,related_name='reminders')
    kind=models.CharField(max_length=20,choices=[('midpoint','منتصف مهلة التأكيد'),('final','قبل نهاية المهلة'),('meeting','قبل الاجتماع')])
    due_at=models.DateTimeField(db_index=True)
    delivered_at=models.DateTimeField(null=True,blank=True)
    cancelled_at=models.DateTimeField(null=True,blank=True)
    notification=models.OneToOneField('core.Notification',null=True,blank=True,on_delete=models.PROTECT)
    class Meta:constraints=[models.UniqueConstraint(fields=['invitation','kind'],name='meeting_reminder_dedup')]
