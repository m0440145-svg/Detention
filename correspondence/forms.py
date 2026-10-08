from django import forms
from core.models import User,Role,Unit
from .models import *
from .services import visible
from .templatetags.mail_ui import TRANSLATIONS

def localize(form,user):
    if getattr(user,"ui_language","ar")=="en":
        for name,field in form.fields.items():
            field.label=TRANSLATIONS.get(str(field.label),str(field.label))
            if isinstance(field,forms.ChoiceField) and not isinstance(field,forms.ModelChoiceField):field.choices=[(key,TRANSLATIONS.get(str(label),str(label))) for key,label in field.choices]


class MailForm(forms.ModelForm):
    attachment=forms.FileField(label='المرفق الأساسي',required=False)
    reason=forms.CharField(label='سبب التعديل',required=False)
    authorized=forms.ModelMultipleChoiceField(label='المصرح لهم بالاسم',queryset=User.objects.none(),required=False)
    cc=forms.ModelMultipleChoiceField(label='نسخة إلى',queryset=User.objects.none(),required=False)
    class Meta:
        model=Correspondence
        fields=['kind','subject','party','external_number','original_date','unit','owner','classification','secrecy','priority','channel','source_platform','external_deadline','related','decision','template','signer','route','body','rich_text','summary','project_reference','program_reference','personal_data','no_attachments']
        labels={'kind':'نوع المراسلة','subject':'الموضوع','party':'الجهة الخارجية / الداخلية','external_number':'رقم الخطاب الأصلي (للوارد)','original_date':'تاريخ الخطاب الأصلي','unit':'الوحدة القائدة','owner':'المسؤول','classification':'التصنيف وفئة الاستبقاء','secrecy':'درجة السرية','priority':'الأولوية','channel':'وسيلة الاستلام / الإرسال','source_platform':'المنصة المصدر','external_deadline':'مهلة الجهة المرسلة إن وجدت','related':'المراسلة المرتبطة','decision':'القرار المرتبط','template':'قالب الصادر','signer':'الموقّع المعتمد','route':'مسار الاعتماد','body':'نص المراسلة','no_attachments':'تأكيد بدون مرفقات'}
        widgets={'original_date':forms.DateInput(attrs={'type':'date'}),'external_deadline':forms.DateTimeInput(attrs={'type':'datetime-local'}),'body':forms.Textarea(attrs={'rows':5})}
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        users=User.objects.filter(is_active=True).exclude(role__in=[Role.ADMIN,Role.VIEWER,Role.BOARD])
        self.fields['owner'].queryset=users; self.fields['signer'].queryset=users.filter(role__in=[Role.EXECUTIVE,Role.ASSISTANT,Role.HEAD])
        self.fields['authorized'].queryset=User.objects.filter(is_active=True).exclude(role=Role.ADMIN); self.fields['cc'].queryset=self.fields['authorized'].queryset
        self.fields['related'].queryset=visible(user)
        from core.views import decisions_for
        self.fields['decision'].queryset=decisions_for(user)
        self.fields['party'].queryset=Party.objects.filter(active=True); self.fields['template'].queryset=MailTemplate.objects.filter(active=True); self.fields['route'].queryset=ApprovalRoute.objects.filter(active=True)
        if user.role not in [Role.EXECUTIVE,Role.ASSISTANT]: self.fields['unit'].queryset=user.units.filter(active=True)
        if user.role!=Role.EXECUTIVE:
            self.fields['secrecy'].choices=[p for p in Secrecy.choices if p[0]!=Secrecy.SECRET]
        localize(self,user)
        self.fields['reason'].required=bool(self.instance.pk)
        if self.instance.pk:
            self.fields['kind'].disabled=True
            if user.role!=Role.EXECUTIVE:
                self.fields['authorized'].disabled=True;self.fields['cc'].disabled=True

class ReferralForm(forms.Form):
    mode=forms.ChoiceField(label="نوع الإحالة",choices=[("parallel","متوازية"),("sequential","متسلسلة حسب ترتيب الاختيار")])
    recipients=forms.ModelMultipleChoiceField(label='المحال إليهم (يمكن اختيار أكثر من موظف)',queryset=User.objects.none())
    instruction=forms.ChoiceField(label='التأشيرة',choices=Referral._meta.get_field('instruction').choices)
    note=forms.CharField(label='ملاحظة الإحالة',required=False,widget=forms.Textarea(attrs={'rows':2}))
    def __init__(self,*args,mail,**kwargs):
        super().__init__(*args,**kwargs)
        users=User.objects.filter(is_active=True).exclude(role__in=[Role.ADMIN,Role.VIEWER,Role.BOARD])
        if mail.secrecy in [Secrecy.RESTRICTED,Secrecy.SECRET]: users=users.filter(pk__in=[p.pk for p in users if visible(p).filter(pk=mail.pk).exists()])
        self.fields['recipients'].queryset=users

class IncomingTaskForm(forms.Form):
    owner=forms.ModelChoiceField(label='الموظف المسؤول عن المهمة',queryset=User.objects.none())
    due_date=forms.DateField(label='تاريخ استحقاق المهمة',widget=forms.DateInput(attrs={'type':'date'}))
    reason=forms.CharField(label='التكليف المطلوب / سبب التحويل',widget=forms.Textarea(attrs={'rows':2}))
    def __init__(self,*args,mail,user,**kwargs):
        super().__init__(*args,**kwargs)
        users=ReferralForm(mail=mail).fields['recipients'].queryset.filter(units__active=True)
        if user.role==Role.HEAD: users=users.filter(units__head=user)
        self.fields['owner'].queryset=users.distinct()
        localize(self,user)

class ActionForm(forms.Form):
    action=forms.ChoiceField(label='الإجراء',choices=[('submit-incoming-approval','إرسال الوارد للاعتماد'),('suspend','تعليق بمبرر'),('unsuspend','استئناف معالجة المعلق'),('done-override','إغلاق الوارد باستثناء تنفيذي'),('submit','تقديم للتدقيق'),('review','إنهاء التدقيق'),('approve','اعتماد المستوى الحالي'),('reject','إعادة للتصحيح'),('sign','إثبات توقيع داخلي وإصدار الرقم'),('send','إثبات إرسال يدوي'),('receive','إثبات استلام'),('start','بدء المعالجة'),('pause','انتظار جهة خارجية وإيقاف SLA'),('resume','استئناف SLA'),('done','إنجاز الوارد'),('archive','أرشفة'),('cancel','إلغاء بمبرر')])
    reason=forms.CharField(label='سبب الإجراء',widget=forms.Textarea(attrs={'rows':2}))
    evidence=forms.CharField(label='مرجع دليل التوقيع أو الإرسال/الاستلام (عند الحاجة)',required=False)

class ExtendForm(forms.Form):
    due_at=forms.DateTimeField(label='الاستحقاق الجديد',widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    reason=forms.CharField(label='سبب التمديد')

class SettingsForm(forms.ModelForm):
    class Meta:
        model=MailSettings
        exclude=['id']
        labels={'sla_days':'مدة الإنجاز حسب الأولوية (أيام عمل)','referral_hours':'مهلة الإحالة الأولى (ساعات عمل)','weekend':'أيام العطلة الأسبوعية','work_start':'بدء ساعات العمل','work_end':'نهاية ساعات العمل','reminder_percent':'تذكير عند (%)','manager_percent':'تصعيد للرئيس عند (%)','executive_percent':'تصعيد للمدير التنفيذي عند (%)','source_system':'مصدر الموظفين والوحدات / حالة الربط'}
        widgets={k:forms.Textarea(attrs={'rows':2}) for k in ['sla_days','referral_hours','weekend']}

CONFIG_MODELS={'party':Party,'classification':Classification,'retention':RetentionPolicy,'template':MailTemplate,'route':ApprovalRoute,'holiday':Holiday,'access':MailAccess,'delegation':Delegation}
CONFIG_LABELS={'party':'دليل الجهات','classification':'التصنيفات','retention':'فئات الاستبقاء','template':'قوالب المراسلات وإصداراتها','route':'مسارات الاعتماد','holiday':'الإجازات الرسمية','access':'صلاحيات الاتصالات الإدارية','delegation':'التفويض والإنابة'}
