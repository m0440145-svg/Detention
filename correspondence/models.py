import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator,MaxValueValidator
from django.db import models
from django.utils import timezone
from core.models import Priority

class Kind(models.TextChoices):
    IN='IN','وارد خارجي'
    OUT='OUT','صادر خارجي'
    INT='INT','مذكرة داخلية'
    CIR='CIR','تعميم'
    DEC='DEC','قرار إداري'
    RPL='RPL','رد على وارد'

class MailStatus(models.TextChoices):
    DRAFT='draft','مسودة'
    REGISTERED='registered','مسجّل'
    REFERRED='referred','محال'
    ACTIVE='active','قيد المعالجة'
    SUSPENDED='suspended','معلّق بمبرر'
    WAITING='waiting','بانتظار رد جهة خارجية'
    REVIEW='review','تدقيق'
    APPROVAL='approval','بانتظار اعتماد'
    SIGNING='signing','بانتظار توقيع'
    ISSUED='issued','مُصدَر برقم'
    SENT='sent','مُرسَل'
    RECEIVED='received','مستلم'
    DONE='done','منجز'
    ARCHIVED='archived','مؤرشف'
    RETURNED='returned','مُعاد للتصحيح'
    CANCELLED='cancelled','ملغى'

class Secrecy(models.TextChoices):
    PUBLIC='public','عام'
    INTERNAL='internal','داخلي'
    RESTRICTED='restricted','مقيّد'
    SECRET='secret','سري'

class Party(models.Model):
    name=models.CharField(max_length=200,unique=True)
    email=models.EmailField(blank=True)
    supervisory=models.BooleanField(default=False)
    active=models.BooleanField(default=True)
    def __str__(self): return self.name

class RetentionPolicy(models.Model):
    name=models.CharField(max_length=150,unique=True)
    years=models.PositiveSmallIntegerField(null=True,blank=True,validators=[MinValueValidator(1)])
    legal_reference=models.TextField(blank=True)
    approved=models.BooleanField(default=False)
    def __str__(self): return self.name+(' — معتمدة' if self.approved else ' — بانتظار اعتماد')

class Classification(models.Model):
    name=models.CharField(max_length=150,unique=True)
    parent=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT)
    route_unit=models.ForeignKey('core.Unit',null=True,blank=True,on_delete=models.PROTECT)
    retention=models.ForeignKey(RetentionPolicy,on_delete=models.PROTECT)
    def __str__(self): return self.name

class MailTemplate(models.Model):
    name=models.CharField(max_length=150)
    version=models.PositiveIntegerField(default=1)
    kind=models.CharField(max_length=4,choices=Kind.choices)
    body=models.TextField(help_text='متغيرات معتمدة بصيغة {{الموضوع}}؛ لا يقبل كودًا أو HTML.')
    active=models.BooleanField(default=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['name','version'],name='mail_template_version')]
    def __str__(self): return f'{self.name} — إصدار {self.version}'

class ApprovalRoute(models.Model):
    name=models.CharField(max_length=150)
    kind=models.CharField(max_length=4,choices=Kind.choices)
    levels=models.JSONField(default=list,help_text='قائمة مرتبة للأدوار: head, assistant, executive')
    authority_reference=models.CharField(max_length=250,help_text='مرجع مصفوفة الصلاحيات المعتمدة')
    active=models.BooleanField(default=True)
    def clean(self):
        if not isinstance(self.levels,list) or not self.levels or any(x not in ['head','assistant','executive'] for x in self.levels): raise ValidationError('حدد مسارًا مرتبًا للأدوار المخولة، دون مسؤول النظام.')
        if not self.authority_reference.strip(): raise ValidationError('مرجع مصفوفة الصلاحيات المعتمدة إلزامي.')
    def __str__(self): return self.name

class MailSettings(models.Model):
    sla_days=models.JSONField(default=dict,help_text='normal/important/urgent/critical: أيام عمل')
    referral_hours=models.JSONField(default=dict)
    weekend=models.JSONField(default=list,help_text='أرقام أيام الأسبوع: الاثنين 0؛ الجمعة 4 والسبت 5')
    work_start=models.PositiveSmallIntegerField(default=9,validators=[MaxValueValidator(23)])
    work_end=models.PositiveSmallIntegerField(default=17,validators=[MinValueValidator(1),MaxValueValidator(24)])
    reminder_percent=models.PositiveSmallIntegerField(default=80,validators=[MinValueValidator(1),MaxValueValidator(100)])
    manager_percent=models.PositiveSmallIntegerField(default=100,validators=[MinValueValidator(100),MaxValueValidator(300)])
    executive_percent=models.PositiveSmallIntegerField(default=150,validators=[MinValueValidator(100),MaxValueValidator(500)])
    source_system=models.CharField(max_length=100,default='Snso — الربط غير مفعّل')
    def clean(self):
        if self.work_start>=self.work_end: raise ValidationError('وقت نهاية العمل يجب أن يلي بدايته.')
        if not isinstance(self.weekend,list) or len(set(self.weekend))>=7 or any(not isinstance(x,int) or x not in range(7) for x in self.weekend): raise ValidationError('تقويم أيام العمل غير صالح.')
        for matrix in [self.sla_days,self.referral_hours]:
            if not isinstance(matrix,dict) or set(matrix)!=set(['normal','important','urgent','critical']) or any(not isinstance(x,(int,float)) or isinstance(x,bool) or not 0<x<=365 for x in matrix.values()): raise ValidationError('مصفوفة SLA يجب أن تشمل الأولويات الأربع بقيم موجبة حتى 365.')
        if not self.reminder_percent<=self.manager_percent<=self.executive_percent: raise ValidationError('رتّب نسب التذكير والتصعيد تصاعديًا.')
    brand_name=models.CharField(max_length=200,default='جمعية الإحسان للخدمات الاجتماعية')
    license_number=models.CharField(max_length=30,default='1504')
    national_address=models.CharField(max_length=250,blank=True)
    footer=models.TextField(default='جمعية الإحسان للخدمات الاجتماعية')
    signature_policy=models.JSONField(default=dict,blank=True)
    retention_committee=models.ManyToManyField(settings.AUTH_USER_MODEL,blank=True,related_name='+')
    retention_quorum=models.PositiveSmallIntegerField(default=2)
    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1,defaults={'sla_days':{'normal':10,'important':5,'urgent':3,'critical':1},'referral_hours':{'normal':16,'important':8,'urgent':8,'critical':2},'weekend':[4,5]})[0]

class Holiday(models.Model):
    date=models.DateField(unique=True)
    name=models.CharField(max_length=150)
    def __str__(self): return f'{self.date} — {self.name}'

class MailAccess(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='mail_access')
    register=models.BooleanField(default=False)
    templates=models.BooleanField(default=False)
    compliance=models.BooleanField(default=False)
    def __str__(self): return str(self.user)

class Sequence(models.Model):
    kind=models.CharField(max_length=4,choices=Kind.choices)
    year=models.PositiveSmallIntegerField()
    last=models.PositiveIntegerField(default=0)
    class Meta: constraints=[models.UniqueConstraint(fields=['kind','year'],name='mail_sequence_year_kind')]

class Correspondence(models.Model):
    uid=models.UUIDField(default=uuid.uuid4,unique=True,editable=False)
    reference=models.CharField(max_length=40,null=True,blank=True,unique=True,editable=False)
    kind=models.CharField(max_length=4,choices=Kind.choices)
    subject=models.CharField(max_length=250)
    body=models.TextField(blank=True)
    search_text=models.TextField(blank=True,editable=False)
    external_number=models.CharField(max_length=100,blank=True)
    original_date=models.DateField(null=True,blank=True)
    party=models.ForeignKey(Party,on_delete=models.PROTECT)
    classification=models.ForeignKey(Classification,on_delete=models.PROTECT)
    template=models.ForeignKey(MailTemplate,null=True,blank=True,on_delete=models.PROTECT)
    unit=models.ForeignKey('core.Unit',on_delete=models.PROTECT)
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='owned_mail')
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='created_mail')
    cc=models.ManyToManyField(settings.AUTH_USER_MODEL,blank=True,related_name='cc_mail')
    authorized=models.ManyToManyField(settings.AUTH_USER_MODEL,blank=True,related_name='authorized_mail')
    secrecy=models.CharField(max_length=20,choices=Secrecy.choices,default=Secrecy.INTERNAL)
    priority=models.CharField(max_length=20,choices=[(p,Priority(p).label) for p in ['normal','important','urgent','critical']],default=Priority.NORMAL)
    status=models.CharField(max_length=20,choices=MailStatus.choices,default=MailStatus.DRAFT,db_index=True)
    channel=models.CharField(max_length=20,choices=[('manual','يدوي'),('email','بريد مؤسسي'),('portal','بوابة'),('api','API'),('platform','منصة حكومية')],default='manual')
    source_platform=models.CharField(max_length=150,blank=True)
    no_attachments=models.BooleanField(default=False)
    signer=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='signable_mail')
    route=models.ForeignKey(ApprovalRoute,null=True,blank=True,on_delete=models.PROTECT)
    approval_roles=models.JSONField(default=list,blank=True)
    approval_index=models.PositiveIntegerField(default=0)
    related=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='replies')
    decision=models.ForeignKey('core.Decision',null=True,blank=True,on_delete=models.PROTECT,related_name='correspondence')
    created_at=models.DateTimeField(default=timezone.now)
    updated_at=models.DateTimeField(default=timezone.now)
    due_at=models.DateTimeField(null=True,blank=True,db_index=True)
    referral_due_at=models.DateTimeField(null=True,blank=True)
    external_deadline=models.DateTimeField(null=True,blank=True)
    sla_hours=models.FloatField(default=80)
    paused_hours=models.FloatField(default=0)
    paused_at=models.DateTimeField(null=True,blank=True)
    signature_method=models.CharField(max_length=20,blank=True)
    signature_evidence=models.TextField(blank=True)
    signed_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='signed_mail')
    signed_at=models.DateTimeField(null=True,blank=True)
    sent_evidence=models.TextField(blank=True)
    archived_at=models.DateTimeField(null=True,blank=True)
    retain_until=models.DateField(null=True,blank=True)
    rich_text=models.BooleanField(default=False)
    summary=models.TextField(blank=True)
    keywords=models.JSONField(default=list,blank=True)
    project_reference=models.CharField(max_length=100,blank=True)
    program_reference=models.CharField(max_length=100,blank=True)
    personal_data=models.BooleanField(default=False)
    first_referred_at=models.DateTimeField(null=True,blank=True)
    completed_at=models.DateTimeField(null=True,blank=True)
    legacy_reference=models.CharField(max_length=100,blank=True)
    class Meta:
        ordering=['-created_at','-pk']
        constraints=[models.UniqueConstraint(fields=['party','external_number'],condition=models.Q(kind=Kind.IN)&~models.Q(status=MailStatus.CANCELLED),name='mail_incoming_external_unique'),models.CheckConstraint(condition=models.Q(status__in=MailStatus.values),name='mail_status_valid'),models.CheckConstraint(condition=models.Q(kind__in=Kind.values),name='mail_kind_valid'),models.CheckConstraint(condition=models.Q(status__in=['issued','sent','received'],reference__isnull=False,signed_at__isnull=False)|~models.Q(status__in=['issued','sent','received']),name='issued_mail_signed_numbered')]
    @property
    def code(self): return self.reference or 'DRAFT-'+str(self.uid).split('-')[0].upper()
    @property
    def hijri_date(self):
        from hijridate import Gregorian
        d=timezone.localtime(self.created_at).date()
        return str(Gregorian(d.year,d.month,d.day).to_hijri())
    @property
    def is_overdue(self): return self.status not in ['done','archived','cancelled'] and not self.paused_at and bool(self.due_at and self.due_at<timezone.now())
    @property
    def sla_percent(self):
        from .services import work_hours_between
        total=max(0.01,work_hours_between(self.created_at,self.due_at)-self.paused_hours) if self.due_at else 1
        elapsed=max(0,work_hours_between(self.created_at,self.paused_at or timezone.now())-self.paused_hours)
        return round(elapsed/total*100,1)
    @property
    def sla_color(self):
        if self.status in ['done','archived','cancelled']:return 'gray'
        return 'red' if self.sla_percent>100 else 'amber' if self.sla_percent>=70 else 'green'
    def __str__(self): return self.code+' — '+self.subject

class Referral(models.Model):
    mail=models.ForeignKey(Correspondence,on_delete=models.PROTECT,related_name='referrals')
    recipient=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    mode=models.CharField(max_length=12,choices=[('parallel','متوازية'),('sequential','متسلسلة')],default='parallel')
    batch=models.UUIDField(default=uuid.uuid4)
    position=models.PositiveIntegerField(default=0)
    instruction=models.CharField(max_length=20,choices=[('view','للاطلاع'),('reply','للإفادة'),('study','للدراسة والرفع'),('execute','للتنفيذ'),('file','للحفظ')])
    note=models.TextField(blank=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)
    completed_at=models.DateTimeField(null=True,blank=True)
    task=models.OneToOneField('core.Task',null=True,blank=True,on_delete=models.PROTECT,related_name='mail_referral')

class Delegation(models.Model):
    principal=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='mail_delegations')
    delegate=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='mail_delegated_to')
    starts=models.DateTimeField()
    ends=models.DateTimeField()
    reason=models.TextField()
    def clean(self):
        if self.starts>=self.ends or self.principal_id==self.delegate_id: raise ValidationError('تفويض غير صالح.')
        if self.principal.role not in ['head','assistant','executive'] or self.delegate.role not in ['head','assistant','executive']: raise ValidationError('التفويض مقتصر على الأدوار المخولة بالاعتماد.')

class MailAttachment(models.Model):
    mail=models.ForeignKey(Correspondence,on_delete=models.PROTECT,related_name='attachments')
    file=models.FileField(upload_to='correspondence/%Y/%m/')
    name=models.CharField(max_length=250)
    sha256=models.CharField(max_length=64)
    size=models.PositiveIntegerField()
    uploaded_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)

class MailEvent(models.Model):
    mail=models.ForeignKey(Correspondence,on_delete=models.PROTECT,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.PROTECT)
    action=models.CharField(max_length=150)
    reason=models.TextField()
    ip=models.CharField(max_length=45,blank=True)
    old=models.JSONField(default=dict)
    new=models.JSONField(default=dict)
    previous_hash=models.CharField(max_length=64,blank=True)
    digest=models.CharField(max_length=64)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta: ordering=['pk']
    def save(self,*args,**kwargs):
        if self.pk: raise ValidationError('سجل التدقيق لا يقبل التعديل.')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs): raise ValidationError('سجل التدقيق لا يقبل الحذف.')

class MailAlert(models.Model):
    mail=models.ForeignKey(Correspondence,on_delete=models.PROTECT,related_name='alerts')
    recipient=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    stage=models.CharField(max_length=40)
    text=models.CharField(max_length=250)
    key=models.CharField(max_length=180,unique=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-pk']

class OCRJob(models.Model):
    attachment=models.OneToOneField(MailAttachment,on_delete=models.PROTECT,related_name='ocr_job')
    requested_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    state=models.CharField(max_length=20,choices=[('pending','بانتظار الاستخراج'),('done','تم الاستخراج — يحتاج مراجعة'),('failed','تعذر الاستخراج')],default='pending')
    text=models.TextField(blank=True)
    page_confidence=models.JSONField(default=list,blank=True)
    proposals=models.JSONField(default=dict,blank=True)
    confidence=models.FloatField(null=True,blank=True)
    error=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    completed_at=models.DateTimeField(null=True,blank=True)

class SavedSearch(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    name=models.CharField(max_length=120)
    filters=models.JSONField(default=dict)
    class Meta: constraints=[models.UniqueConstraint(fields=['user','name'],name='mail_saved_search_name')]

from .governance_models import (Integration, Delivery, ExternalIdentity, ExternalUnit, SyncReceipt, NotificationPreference, MFADevice, ExportGrant, ProcessingActivity, SubjectRequest, RetentionCase, RetentionApproval, LegalHold, SignatureRequest, MigrationBatch, ReportSchedule, APICallLog)
