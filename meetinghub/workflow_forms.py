from django import forms
from django.utils import timezone
from django.db.models import Q
from core.models import User,Role,Attachment
from .models import *
from . import services,governance_services as gov,workflow_services as workflow

def users():return User.objects.filter(is_active=True).exclude(role__in=['admin','viewer'])
class ReasonForm(forms.Form):
    reason=forms.CharField(label='سبب الإجراء',widget=forms.Textarea(attrs={'rows':2}),max_length=3000)
    version=forms.IntegerField(widget=forms.HiddenInput,initial=1,min_value=0)
class VoteForm(forms.ModelForm):
    class Meta:
        model=Vote;fields=['item','question','secret','purpose','denominator','threshold','reference','closes_at']
        labels={'item':'البند','question':'سؤال التصويت','secret':'اقتراع سري','purpose':'نوع التصويت','denominator':'مقام النسبة','threshold':'النسبة المطلوبة','reference':'مرجع قاعدة التمرير','closes_at':'وقت الإغلاق — Asia/Riyadh'}
        widgets={'closes_at':forms.DateTimeInput(attrs={'type':'datetime-local'})}
    def __init__(self,*args,record,**kwargs):
        super().__init__(*args,**kwargs);self.fields['item'].queryset=record.agenda_items.filter(removed=False);self.fields['closes_at'].initial=timezone.localtime(record.ends_at).replace(tzinfo=None)
class CastForm(forms.Form):
    vote=forms.ModelChoiceField(queryset=Vote.objects.none(),label='الصندوق')
    choice=forms.ChoiceField(choices=[('yes','موافق'),('no','معارض'),('abstain','ممتنع')],label='الخيار')
    eligibility=forms.ModelChoiceField(queryset=Eligibility.objects.none(),required=False,label='صفة التصويت: نفسك أو موكلك')
    def __init__(self,*args,record,user,**kwargs):
        super().__init__(*args,**kwargs);self.fields['vote'].queryset=Vote.objects.filter(pk__in=[v.pk for v in workflow.visible_votes(user,record)],status='open')
        self.fields['eligibility'].queryset=Eligibility.objects.filter(roster__meeting=record,roster__generation=record.roster_generation).filter(Q(member__user=user)|Q(pk__in=Proxy.objects.filter(roster__meeting=record,delegate__member__user=user,voting_allowed=True,revoked_at__isnull=True,valid_until__gt=timezone.now()).values('principal_id')))
class MinutesForm(ReasonForm):
    body=forms.CharField(label='المناقشات ونص المحضر',widget=forms.Textarea(attrs={'rows':8}),max_length=20000)
    signatories=forms.ModelMultipleChoiceField(queryset=Member.objects.none(),label='الموقعون المطلوبون')
    correction=forms.BooleanField(required=False,label='إصدار تصحيح جديد مع حفظ الأصل')
    def __init__(self,*args,record,**kwargs):
        super().__init__(*args,**kwargs);self.fields['signatories'].queryset=record.invitees.filter(archived=False);self.fields['signatories'].initial=[record.committee.chair_id,record.committee.secretary_id]
        minute=Minutes.objects.filter(meeting=record).first();self.fields['version'].initial=minute.version if minute else 0
        if minute and minute.current_version:self.fields['body'].initial=minute.revisions.get(version=minute.current_version).body
class SignForm(ReasonForm):
    password=forms.CharField(label='كلمة مرور الحساب للتحقق',widget=forms.PasswordInput)
    capacity=forms.CharField(label='صفة الموقع',max_length=150)
class LiveForm(ReasonForm):
    item=forms.ModelChoiceField(queryset=AgendaItem.objects.none(),label='البند الحالي',required=False)
    command=forms.ChoiceField(label='المؤقت',choices=[('start','بدء / استئناف'),('pause','إيقاف مؤقت'),('reset','تصفير')])
    def __init__(self,*args,record,**kwargs):
        super().__init__(*args,**kwargs);self.fields['item'].queryset=record.agenda_items.filter(removed=False);s=LiveState.objects.filter(meeting=record).first();self.fields['version'].initial=s.version if s else 1
class NoteForm(forms.ModelForm):
    version=forms.IntegerField(widget=forms.HiddenInput,initial=0)
    note=forms.IntegerField(required=False,widget=forms.HiddenInput)
    class Meta:
        model=Note;fields=['item','body','shared','page','strokes'];labels={'item':'البند','body':'ملاحظتك','shared':'مشاركة مع المدعوين','page':'الصفحة'}
        widgets={'body':forms.Textarea(attrs={'rows':4}),'strokes':forms.HiddenInput()}
    def __init__(self,*args,record,user,**kwargs):
        super().__init__(*args,**kwargs);self.fields['item'].queryset=AgendaItem.objects.filter(pk__in=[i.pk for i in record.agenda_items.filter(removed=False) if workflow.session.may_read_item(user,record,{'id':i.pk,'kind':i.kind,'presenter':i.presenter_id,'owner':i.owner_id})]);self.fields['strokes'].initial=[];self.fields['strokes'].required=False
class DecisionFromMinutesForm(ReasonForm):
    item=forms.ModelChoiceField(queryset=AgendaItem.objects.none(),label='بند القرار')
    number=forms.CharField(label='رقم القرار',max_length=60)
    text=forms.CharField(label='نص القرار',widget=forms.Textarea)
    due_date=forms.DateField(label='موعد الاستحقاق',widget=forms.DateInput(attrs={'type':'date'}))
    followup_owner=forms.ModelChoiceField(queryset=users().exclude(role='board'),label='مسؤول المتابعة')
    supervisor=forms.ModelChoiceField(queryset=users().filter(role__in=['executive','assistant','head']),label='الجهة الإشرافية')
    team=forms.ModelMultipleChoiceField(queryset=users().exclude(role='board'),label='فريق التنفيذ',required=False)
    priority=forms.ChoiceField(choices=[('normal','عادية'),('important','مهمة'),('urgent','عاجلة'),('critical','حرجة')],label='الأولوية')
    sla_hours=forms.IntegerField(min_value=1,label='SLA بالساعات')
    escalation_hours=forms.IntegerField(min_value=1,label='التصعيد بعد الاستحقاق بالساعات')
    output=forms.CharField(label='المخرج القابل للقياس',widget=forms.Textarea)
    def __init__(self,*args,record,**kwargs):
        super().__init__(*args,**kwargs);self.fields['item'].queryset=record.agenda_items.filter(removed=False,kind__in=['decision','vote'])
class StageForm(ReasonForm):
    stage=forms.IntegerField(required=False,widget=forms.HiddenInput)
    execution=forms.ModelChoiceField(queryset=ExecutionRecord.objects.none(),label='القرار')
    title=forms.CharField(label='اسم المرحلة',max_length=250)
    due_date=forms.DateField(label='استحقاق المرحلة',widget=forms.DateInput(attrs={'type':'date'}))
    progress=forms.IntegerField(label='الإنجاز %',min_value=0,max_value=100)
    evidence=forms.ModelChoiceField(queryset=Attachment.objects.none(),required=False,label='شاهد مرفوع للقرار')
    link=forms.URLField(required=False,label='رابط شاهد HTTPS')
    def __init__(self,*args,record,user,**kwargs):
        super().__init__(*args,**kwargs);from core.views import decisions_for
        ds=decisions_for(user).filter(meeting=record.meeting);self.fields['execution'].queryset=ExecutionRecord.objects.filter(decision__in=ds);self.fields['evidence'].queryset=Attachment.objects.filter(decision__in=ds)
class DocumentForm(forms.Form):
    committee=forms.ModelChoiceField(queryset=Committee.objects.none(),label='اللجنة')
    meeting=forms.ModelChoiceField(queryset=MeetingRecord.objects.none(),required=False,label='الاجتماع')
    file=forms.FileField(label='الوثيقة — حتى 10 ميجابايت')
    secret=forms.BooleanField(required=False,initial=True,label='وثيقة مقيدة')
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs);self.fields['committee'].queryset=services.committees_for(user);self.fields['meeting'].queryset=services.records_for(user,True)
ENTRY_FIELDS={
 'compliance':{'regulator':'الجهة المنظمة','article':'المادة','action':'الإجراء المطلوب','frequency':'التكرار','gap':'تحليل الفجوة','remediation':'خطة المعالجة','official_url':'الرابط الرسمي المباشر (اختياري دون تأكيد)','last_verified':'تاريخ آخر تحقق YYYY-MM-DD (اختياري)'},
 'finance':{'period':'الفترة','source':'المصدر','amount':'القيمة المالية','category':'نوع المؤشر'},
 'recommendation':{'source':'المصدر','priority':'الأولوية','expected_impact':'الأثر المتوقع','review_date':'موعد المراجعة YYYY-MM-DD','measured_impact':'الأثر المقاس (اختياري)'},
 'service':{'resource':'المورد من مخزون اللجنة','quantity':'الكمية','starts':'بداية الحجز','ends':'نهاية الحجز','requirements':'المتطلبات'},
 'volunteer':{'starts':'البداية','ends':'النهاية','source':'المصدر: attendance أو task','task':'رقم المهمة المعتمدة (اختياري)'},
 'crisis':{'number':'رقم الغرفة','severity':'الخطورة','starts':'بداية الغرفة','ends':'نهاية الغرفة'},
}
class EntryForm(forms.ModelForm):
    reason=forms.CharField(label='سبب الحفظ',widget=forms.Textarea(attrs={'rows':2}))
    version=forms.IntegerField(widget=forms.HiddenInput,initial=0)
    class Meta:
        model=GovernanceEntry;fields=['committee','meeting','member','title','owner','due_date','document','secret'];labels={'committee':'اللجنة','meeting':'الاجتماع','member':'العضو عند الحاجة','title':'عنوان السجل','owner':'المسؤول','due_date':'تاريخ الاستحقاق','document':'الدليل','secret':'سجل مقيد'}
        widgets={'due_date':forms.DateInput(attrs={'type':'date'})}
    def __init__(self,*args,user,kind,**kwargs):
        super().__init__(*args,**kwargs);self.fields['committee'].queryset=services.committees_for(user);self.fields['meeting'].queryset=services.records_for(user,True);self.fields['member'].queryset=services.active_members().filter(committees__in=services.committees_for(user)).distinct();self.fields['owner'].queryset=users()
        self.fields['document'].queryset=Document.objects.filter(pk__in=[d.pk for d in Document.objects.filter(committee__in=services.committees_for(user)) if gov.document_allowed(user,d)])
        for key,label in ENTRY_FIELDS[kind].items():
            optional=key in ['official_url','last_verified','measured_impact','task'];field=forms.CharField(label=label,required=not optional,max_length=5000)
            if key in ['starts','ends']:field=forms.DateTimeField(label=label,widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
            if key in ['last_verified','review_date']:field=forms.DateField(label=label,required=not optional,widget=forms.DateInput(attrs={'type':'date'}))
            if key=='source' and kind=='volunteer':field=forms.ChoiceField(label='مصدر الساعات',choices=[('attendance','حضور اجتماع فعلي'),('task','مهمة معتمدة')])
            if key=='resource':field=forms.ChoiceField(label=label,choices=[(r.name,str(r)) for r in Resource.objects.filter(committee__in=services.committees_for(user),active=True)])
            if key=='quantity':field=forms.IntegerField(label=label,min_value=1)
            self.fields['data_'+key]=field
        if self.instance.pk:
            self.fields['version'].initial=self.instance.version
            for k,v in self.instance.data.items():
                if 'data_'+k in self.fields:self.fields['data_'+k].initial=v
class DisclosureForm(forms.Form):
    item=forms.ModelChoiceField(queryset=AgendaItem.objects.none(),label='البند')
    body=forms.CharField(label='وصف المصلحة والإجراء',widget=forms.Textarea)
    kind=forms.CharField(label='نوع المصلحة')
    recused=forms.BooleanField(label='طلب تنحي عن النقاش والتصويت',required=False)
    document=forms.ModelChoiceField(queryset=Document.objects.none(),required=False,label='دليل')
    def __init__(self,*args,record,user,**kwargs):
        super().__init__(*args,**kwargs);self.fields['item'].queryset=record.agenda_items.filter(removed=False);self.fields['document'].queryset=Document.objects.filter(owner=user,committee=record.committee)
class ElectionForm(forms.ModelForm):
    class Meta:
        model=Election;fields=['committee','title','nominations_start','nominations_end','ballot_end','seats','reference','allow_proxy','proxy_limit','parent']
        labels={'committee':'اللجنة','title':'عنوان الانتخابات','nominations_start':'بداية الترشح','nominations_end':'نهاية الترشح','ballot_end':'نهاية التصويت','seats':'المقاعد','reference':'مرجع القواعد','allow_proxy':'السماح بالإنابة','proxy_limit':'حد الإنابة','parent':'جولة حسم لانتخابات سابقة'}
        widgets={k:forms.DateTimeInput(attrs={'type':'datetime-local'}) for k in ['nominations_start','nominations_end','ballot_end']}
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs);self.fields['committee'].queryset=services.committees_for(user);self.fields['parent'].queryset=Election.objects.filter(committee__in=services.committees_for(user),status='tie')
class CandidateForm(forms.Form):
    statement=forms.CharField(label='طلب الترشح',widget=forms.Textarea)
    document=forms.ModelChoiceField(queryset=Document.objects.none(),label='ملف الأهلية')
    def __init__(self,*args,user,election,**kwargs):
        super().__init__(*args,**kwargs);self.fields['document'].queryset=Document.objects.filter(owner=user,committee=election.committee)
class PlatformForm(ReasonForm):
    values=forms.CharField(label="القيم",required=False,widget=forms.Textarea)
    announcements=forms.CharField(label="الإعلانات",required=False,widget=forms.Textarea)
    ticker=forms.CharField(label="الشريط الإخباري",required=False,max_length=1000)
    name=forms.CharField(label='اسم المنصة')
    about=forms.CharField(label='من نحن',widget=forms.Textarea)
    vision=forms.CharField(label='الرؤية',widget=forms.Textarea)
    mission=forms.CharField(label='الرسالة',widget=forms.Textarea)
    objectives=forms.CharField(label='الأهداف',widget=forms.Textarea)
    contact=forms.CharField(label='بيانات التواصل')
    logo_url=forms.URLField(label='رابط الشعار HTTPS',required=False)
    primary=forms.CharField(label='اللون الرئيسي',initial='#cf6028')
    secondary=forms.CharField(label='اللون الثانوي',initial='#f18927')
    neutral=forms.CharField(label='اللون المحايد',initial='#afa5a2')
    background=forms.CharField(label='لون الخلفية',initial='#f9f4ee')
    support=forms.CharField(label='اللون المساند',initial='#91877d')
    text=forms.CharField(label='لون النص',initial='#575650')
