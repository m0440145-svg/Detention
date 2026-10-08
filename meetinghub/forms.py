from decimal import Decimal
from django import forms
from core.models import User,Meeting,Role
from core.policy import GLOBAL_ROLES
from .models import *
from . import services

class MemberForm(forms.ModelForm):
    national_id=forms.CharField(label='الهوية الوطنية (اختياري؛ تخزن مشفرة ولا تظهر بعد الحفظ)',required=False,max_length=10,widget=forms.PasswordInput(render_value=False,attrs={'autocomplete':'off','inputmode':'numeric'}))
    reason=forms.CharField(label='سبب إنشاء العضوية أو تعديلها',widget=forms.Textarea(attrs={'rows':2}))
    class Meta:
        model=Member
        fields=['user','role','organization','capacity','starts','ends']
        labels={'user':'حساب المستخدم','role':'دور الاجتماعات','organization':'الجهة','capacity':'الصفة','starts':'بداية العضوية','ends':'نهاية العضوية'}
        widgets={k:forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d') for k in ['starts','ends']}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['user'].queryset=User.objects.filter(is_active=True).exclude(role=Role.ADMIN)
        if self.instance.pk:self.fields['user'].disabled=True

class CommitteeForm(forms.ModelForm):
    reason=forms.CharField(label='سبب إنشاء اللجنة أو تعديلها',widget=forms.Textarea(attrs={'rows':2}))
    version=forms.IntegerField(required=False,widget=forms.HiddenInput())
    class Meta:
        model=Committee
        fields=['name','kind','mandate','chair','secretary','members','starts','ends']
        labels={'name':'اسم اللجنة / المجلس','kind':'النوع','mandate':'الاختصاص','chair':'الرئيس','secretary':'أمين السر','members':'الأعضاء','starts':'تاريخ البداية','ends':'تاريخ النهاية'}
        widgets={k:forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d') for k in ['starts','ends']}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        active=services.active_members()
        self.fields['members'].queryset=active
        self.fields['chair'].queryset=active.filter(role='chair');self.fields['secretary'].queryset=active.filter(role='secretary')
        if self.instance.pk:self.fields['version'].initial=self.instance.version;self.fields['version'].required=True

class MeetingForm(forms.Form):
    legacy=forms.ModelChoiceField(label='توسعة اجتماع سابق بدل إنشاء سجل جديد (اختياري)',queryset=Meeting.objects.none(),required=False)
    number=forms.CharField(label='رقم الاجتماع',max_length=60)
    name=forms.CharField(label='عنوان الاجتماع',max_length=200)
    committee=forms.ModelChoiceField(label='اللجنة / المجلس',queryset=Committee.objects.none())
    kind=forms.ChoiceField(label='نوع الاجتماع',choices=MeetingRecord._meta.get_field('kind').choices)
    mode=forms.ChoiceField(label='نمط الانعقاد',choices=MeetingRecord._meta.get_field('mode').choices)
    starts_at=forms.DateTimeField(label='بداية الاجتماع — توقيت الرياض',widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
    ends_at=forms.DateTimeField(label='نهاية الاجتماع — توقيت الرياض',widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
    venue=forms.CharField(label='المكان',required=False,max_length=250)
    connection_url=forms.URLField(label='رابط الاتصال HTTPS',required=False)
    rsvp_deadline=forms.DateTimeField(label='مهلة تأكيد الحضور — توقيت الرياض',widget=forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M'))
    quorum_percent=forms.DecimalField(label='نسبة النصاب المعتمدة % — إلزامية',min_value=Decimal('0.01'),max_value=100,max_digits=5,decimal_places=2)
    quorum_reference=forms.CharField(label='مرجع النسبة من النظام الأساسي / اللائحة المعتمدة',max_length=250)
    allow_proxy=forms.BooleanField(label='السماح بالتوكيل',required=False)
    proxy_limit=forms.IntegerField(label='الحد المسموح للتوكيلات (صفر عند تعطيلها)',min_value=0,max_value=65535,initial=0)
    secrecy=forms.ChoiceField(label='مستوى السرية',choices=MeetingRecord._meta.get_field('secrecy').choices)
    invitees=forms.ModelMultipleChoiceField(label='المدعوون — من أعضاء اللجنة فقط',queryset=Member.objects.none())
    reason=forms.CharField(label='سبب الإنشاء أو التعديل',widget=forms.Textarea(attrs={'rows':2}))
    version=forms.IntegerField(required=False,widget=forms.HiddenInput())
    def __init__(self,*args,user,instance=None,**kwargs):
        if instance:
            initial={key:getattr(instance,key) for key in services.MEETING_FIELDS};initial.update(number=instance.meeting.number,name=instance.meeting.name,invitees=instance.invitees.all(),version=instance.version);kwargs['initial']=initial
        super().__init__(*args,**kwargs)
        committees=services.committees_for(user).filter(archived=False)
        self.fields['committee'].queryset=Committee.objects.filter(pk__in=[c.pk for c in committees if services.can_manage(user,c)])
        self.fields['invitees'].queryset=Member.objects.select_related('user').filter(committees__in=self.fields['committee'].queryset,archived=False,user__is_active=True).distinct()
        self.fields['legacy'].queryset=Meeting.objects.filter(governance__isnull=True) if user.role in GLOBAL_ROLES else Meeting.objects.none()
        if instance:self.fields['legacy'].disabled=True;self.fields['version'].required=True

class ActionForm(forms.Form):
    action=forms.ChoiceField(label='الإجراء',choices=[('schedule','جدولة الاجتماع'),('cancel','إلغاء الاجتماع'),('archive','أرشفة / حذف منطقي'),('restore','استرجاع إلى مسودة')])
    reason=forms.CharField(label='سبب الإجراء')
    version=forms.IntegerField(widget=forms.HiddenInput())
