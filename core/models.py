import uuid
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

class Role(models.TextChoices):
    ADMIN='admin','مسؤول النظام'
    EXECUTIVE='executive','المدير التنفيذي'
    ASSISTANT='assistant','مساعد المدير التنفيذي'
    HEAD='head','رئيس الوحدة'
    EMPLOYEE='employee','موظف'
    VIEWER='viewer','عرض فقط'
class User(AbstractUser):
    REQUIRED_FIELDS = ['email','employee_number']
    email=models.EmailField(unique=True)
    phone=models.CharField(max_length=20, unique=True, null=True, blank=True)
    role=models.CharField(max_length=20,choices=Role.choices,default=Role.EMPLOYEE)
    employee_number=models.CharField(max_length=30,unique=True)
    job_title=models.CharField(max_length=150,blank=True)
    manager=models.ForeignKey('self',null=True,blank=True,on_delete=models.SET_NULL)
    units=models.ManyToManyField('Unit',related_name='employees',blank=True)
    def save(self,*args,**kwargs):
        if self.is_superuser: self.role=Role.ADMIN
        if self.phone=='': self.phone=None
        return super().save(*args,**kwargs)
    def __str__(self): return self.get_full_name() or self.username
class Unit(models.Model):
    name=models.CharField(max_length=150,unique=True)
    description=models.TextField(blank=True)
    head=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='led_units')
    active=models.BooleanField(default=True)
    created_at=models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name
class Meeting(models.Model):
    number=models.CharField(max_length=60,unique=True)
    name=models.CharField(max_length=200)
    committee=models.CharField(max_length=200)
    date=models.DateField()
    def __str__(self): return self.name
class Decision(models.Model):
    number=models.CharField(max_length=60,unique=True)
    meeting=models.ForeignKey(Meeting,on_delete=models.PROTECT,related_name='decisions')
    text=models.TextField()
    date=models.DateField()
    followup_owner=models.ForeignKey(User,on_delete=models.PROTECT)
    @property
    def progress(self):
        values=list(self.tasks.values_list('progress',flat=True))
        return round(sum(values)/len(values)) if values else 0
    @property
    def approved_progress(self):
        total=self.tasks.count()
        return round(self.tasks.filter(status=Status.CLOSED).count()/total*100) if total else 0
    def __str__(self): return self.number
class Status(models.TextChoices):
    NEW='new','جديدة'
    ASSIGNED='assigned','تم الإسناد'
    ACTIVE='active','قيد التنفيذ'
    WAITING='waiting','بانتظار رد'
    EXTERNAL='external','بانتظار جهة خارجية'
    BLOCKED='blocked','متعثرة'
    OVERDUE='overdue','متأخرة'
    COMPLETED='completed','مكتملة'
    APPROVAL='approval','بانتظار الاعتماد'
    RETURNED='returned','معادة للتنفيذ'
    PAUSED='paused','موقوفة'
    CANCELLED='cancelled','ملغاة'
    CLOSED='closed','مغلقة'
class Priority(models.TextChoices):
    LOW='low','منخفضة'
    NORMAL='normal','عادية'
    IMPORTANT='important','مهمة'
    URGENT='urgent','عاجلة'
    CRITICAL='critical','حرجة'
class Source(models.TextChoices):
    DECISION='decision','قرار اجتماع'
    EXECUTIVE='executive','تكليف المدير التنفيذي'
    PLAN='plan','خطة تشغيلية'
    INBOUND='inbound','مراسلة واردة'
    OUTBOUND='outbound','مراسلة صادرة'
    INTERNAL='internal','إجراء داخلي'
    PROJECT='project','مشروع'
    UNIT='unit','طلب وحدة'
    INDEPENDENT='independent','مهمة مستقلة'
    OTHER='other','أخرى'
class Task(models.Model):
    number=models.UUIDField(default=uuid.uuid4,unique=True,editable=False)
    title=models.CharField(max_length=250)
    description=models.TextField()
    source=models.CharField(max_length=20,choices=Source.choices,default=Source.INDEPENDENT)
    task_type=models.CharField(max_length=100,default='تشغيلية')
    project=models.CharField(max_length=150,blank=True)
    unit=models.ForeignKey(Unit,on_delete=models.PROTECT,related_name='tasks')
    owner=models.ForeignKey(User,on_delete=models.PROTECT,related_name='owned_tasks')
    assignment_mode=models.CharField(max_length=20,choices=[('manual','موظف أو موظفون محددون'),('unit','جميع موظفي الوحدة القائدة'),('units','موظفو الوحدة القائدة والوحدات المشاركة')],default='manual')
    participants=models.ManyToManyField(User,blank=True,related_name='participating_tasks')
    participating_units=models.ManyToManyField(Unit,blank=True,related_name='shared_tasks')
    start_date=models.DateField()
    due_date=models.DateField(db_index=True)
    priority=models.CharField(max_length=20,choices=Priority.choices,default=Priority.NORMAL,db_index=True)
    confidentiality=models.CharField(max_length=20,choices=[('normal','عادية'),('restricted','مقيدة'),('secret','سرية')],default='normal')
    status=models.CharField(max_length=20,choices=Status.choices,default=Status.ASSIGNED,db_index=True)
    progress=models.PositiveSmallIntegerField(default=0,validators=[MinValueValidator(0),MaxValueValidator(100)])
    auto_progress=models.BooleanField(default=False)
    expected_result=models.TextField()
    success_indicator=models.CharField(max_length=250)
    notes=models.TextField(blank=True)
    decision=models.ForeignKey(Decision,null=True,blank=True,on_delete=models.PROTECT,related_name='tasks')
    created_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='created_tasks')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(default=timezone.now,db_index=True)
    submitted_at=models.DateTimeField(null=True,blank=True)
    closed_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        ordering=['due_date','id']
        constraints=[models.CheckConstraint(condition=models.Q(progress__lte=100),name='task_progress_max'),models.CheckConstraint(condition=models.Q(due_date__gte=models.F('start_date')),name='task_dates_order')]
        indexes=[models.Index(fields=['unit','status']),models.Index(fields=['owner','status'])]
    @property
    def code(self): return f'TSK-{self.pk:05d}' if self.pk else 'جديدة'
    @property
    def days_remaining(self): return (self.due_date-timezone.localdate()).days
    @property
    def is_overdue(self): return self.due_date<timezone.localdate() and self.status not in [Status.CLOSED,Status.CANCELLED,Status.APPROVAL,Status.COMPLETED]
    def clean(self):
        if self.due_date and self.start_date and self.due_date<self.start_date: raise ValidationError('موعد الاستحقاق يجب أن يلي تاريخ البدء.')
        if self.owner_id and self.unit_id and not self.owner.units.filter(pk=self.unit_id).exists(): raise ValidationError('المسؤول الرئيسي يجب أن ينتمي للوحدة القائدة.')
    def get_status_display(self): return Catalog.objects.filter(kind='status',key=self.status).values_list('label',flat=True).first() or Status(self.status).label
    def get_priority_display(self): return Catalog.objects.filter(kind='priority',key=self.priority).values_list('label',flat=True).first() or Priority(self.priority).label
    def get_task_type_display(self): return Catalog.objects.filter(kind='task_type',key=self.task_type).values_list('label',flat=True).first() or self.task_type
    def __str__(self): return self.title
class Subtask(models.Model):
    task=models.ForeignKey(Task,on_delete=models.PROTECT,related_name='subtasks')
    title=models.CharField(max_length=250)
    owner=models.ForeignKey(User,on_delete=models.PROTECT)
    due_date=models.DateField()
    progress=models.PositiveSmallIntegerField(default=0,validators=[MaxValueValidator(100)])
    comment=models.TextField(blank=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=models.Q(progress__lte=100),name='subtask_progress_max')]
class Update(models.Model):
    task=models.ForeignKey(Task,on_delete=models.PROTECT,related_name='updates')
    user=models.ForeignKey(User,on_delete=models.PROTECT)
    progress=models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    comment=models.TextField()
    accomplished=models.TextField()
    remaining=models.TextField()
    created_at=models.DateTimeField(auto_now_add=True)
class Comment(models.Model):
    task=models.ForeignKey(Task,on_delete=models.PROTECT,related_name='comments')
    user=models.ForeignKey(User,on_delete=models.PROTECT)
    body=models.TextField()
    parent=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='replies')
    mentions=models.ManyToManyField(User,blank=True,related_name='mentioned_comments')
    mentioned_units=models.ManyToManyField(Unit,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
class Attachment(models.Model):
    task=models.ForeignKey(Task,null=True,blank=True,on_delete=models.PROTECT,related_name='attachments')
    decision=models.ForeignKey(Decision,null=True,blank=True,on_delete=models.PROTECT,related_name='attachments')
    subtask=models.ForeignKey(Subtask,null=True,blank=True,on_delete=models.PROTECT,related_name='attachments')
    comment=models.ForeignKey(Comment,null=True,blank=True,on_delete=models.PROTECT,related_name='attachments')
    uploaded_by=models.ForeignKey(User,on_delete=models.PROTECT)
    file=models.FileField(upload_to='uploads/%Y/%m/')
    original_name=models.CharField(max_length=250)
    created_at=models.DateTimeField(auto_now_add=True)
class Obstacle(models.Model):
    TYPES=[(v,v) for v in ['انتظار اعتماد','نقص معلومات','نقص موارد','نقص موظفين','اعتماد مالي','اعتماد إداري','جهة خارجية','مشكلة تقنية','ضغط أعمال','تأخر مورد','تأخر وحدة أخرى','أخرى']]
    task=models.ForeignKey(Task,on_delete=models.PROTECT,related_name='obstacles')
    reported_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='reported_obstacles')
    kind=models.CharField(max_length=100,choices=TYPES)
    description=models.TextField()
    caused_by=models.CharField(max_length=200)
    needs_decision=models.BooleanField(default=False)
    requested_action=models.TextField()
    intervention_owner=models.ForeignKey(User,on_delete=models.PROTECT,related_name='assigned_obstacles')
    expected_resolution=models.DateField()
    impact=models.CharField(max_length=20,choices=[('low','منخفض'),('medium','متوسط'),('high','مرتفع')])
    created_at=models.DateTimeField(auto_now_add=True)
    resolved_at=models.DateTimeField(null=True,blank=True)
    resolution=models.TextField(blank=True)
class Audit(models.Model):
    task=models.ForeignKey(Task,null=True,on_delete=models.PROTECT,related_name='activities')
    actor=models.ForeignKey(User,null=True,on_delete=models.PROTECT)
    action=models.CharField(max_length=200)
    old=models.JSONField(default=dict)
    new=models.JSONField(default=dict)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at','-id']
    def save(self,*args,**kwargs):
        if self.pk: raise ValidationError('سجل النشاط غير قابل للتعديل.')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs): raise ValidationError('سجل النشاط غير قابل للحذف.')
class Notification(models.Model):
    recipient=models.ForeignKey(User,on_delete=models.PROTECT,related_name='notifications')
    task=models.ForeignKey(Task,null=True,on_delete=models.PROTECT)
    text=models.CharField(max_length=300)
    key=models.CharField(max_length=200,null=True,blank=True,unique=True)
    read=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']
class Escalation(models.Model):
    task=models.ForeignKey(Task,on_delete=models.PROTECT,related_name='escalations')
    recipient=models.ForeignKey(User,on_delete=models.PROTECT)
    reason=models.TextField()
    level=models.PositiveSmallIntegerField()
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['task','recipient','level'],name='unique_escalation')]
class RuleSettings(models.Model):
    followup_elapsed=models.PositiveSmallIntegerField(default=30,validators=[MaxValueValidator(100)])
    risk_elapsed=models.PositiveSmallIntegerField(default=70,validators=[MaxValueValidator(100)])
    risk_progress=models.PositiveSmallIntegerField(default=30,validators=[MaxValueValidator(100)])
    stale_days=models.PositiveSmallIntegerField(default=5)
    remind_days=models.PositiveSmallIntegerField(default=3)
    head_after=models.PositiveSmallIntegerField(default=1)
    assistant_after=models.PositiveSmallIntegerField(default=3)
    executive_after=models.PositiveSmallIntegerField(default=7)
    critical_multiplier=models.FloatField(default=0.5,validators=[MinValueValidator(0.1),MaxValueValidator(1)])
    @classmethod
    def current(cls): return cls.objects.get_or_create(pk=1)[0]
class LoginAttempt(models.Model):
    identifier=models.CharField(max_length=254)
    ip=models.GenericIPAddressField()
    succeeded=models.BooleanField()
    created_at=models.DateTimeField(auto_now_add=True,db_index=True)

class Catalog(models.Model):
    kind=models.CharField(max_length=20,choices=[('status','حالات المهام'),('priority','الأولويات'),('task_type','أنواع المهام')])
    key=models.CharField(max_length=100)
    label=models.CharField(max_length=100)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['kind','key'],name='unique_catalog_entry')]
