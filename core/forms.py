from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import *
from .policy import GLOBAL_ROLES
class TaskForm(forms.ModelForm):
    reason=forms.CharField(label='سبب التعديل',required=False,widget=forms.Textarea(attrs={'rows':2}))
    class Meta:
        model=Task
        fields=['title','description','source','task_type','project','unit','owner','assignment_mode','participants','participating_units','start_date','due_date','priority','confidentiality','expected_result','success_indicator','notes','auto_progress','decision']
        labels={'title':'عنوان المهمة','description':'الوصف','source':'المصدر','task_type':'نوع المهمة','project':'المشروع','unit':'الوحدة القائدة','owner':'المسؤول الرئيسي','assignment_mode':'طريقة الإسناد','participants':'الموظفون المشاركون','participating_units':'الوحدات المشاركة','start_date':'تاريخ البدء','due_date':'الاستحقاق','priority':'الأولوية','confidentiality':'السرية','expected_result':'النتيجة المطلوبة','success_indicator':'مؤشر النجاح','notes':'ملاحظات','auto_progress':'احتساب الإنجاز من المهام الفرعية','decision':'القرار المرتبط'}
        widgets={k:forms.DateInput(attrs={'type':'date'}) for k in ['start_date','due_date']}
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['owner'].queryset=User.objects.filter(is_active=True).exclude(role__in=[Role.VIEWER,Role.ADMIN])
        self.fields['participants'].queryset=self.fields['owner'].queryset
        self.fields['unit'].queryset=Unit.objects.filter(active=True)
        if user.role==Role.HEAD:
            self.fields['unit'].queryset=self.fields['unit'].queryset.filter(head=user)
            from .views import decisions_for
            self.fields['decision'].queryset=decisions_for(user)
        self.fields['priority'].choices=[(key,Catalog.objects.filter(kind='priority',key=key).values_list('label',flat=True).first() or label) for key,label in Priority.choices if not Catalog.objects.filter(kind='priority',key=key,active=False).exists()]
        custom_types=Catalog.objects.filter(kind='task_type',active=True)
        if Catalog.objects.filter(kind='task_type').exists(): self.fields['task_type'].widget=forms.Select(choices=[(t.key,t.label) for t in custom_types])
        for field in self.fields.values():
            if isinstance(field.widget,forms.Textarea): field.widget.attrs['rows']=3
class ProgressForm(forms.Form):
    progress=forms.IntegerField(label='نسبة الإنجاز',min_value=0,max_value=100)
    comment=forms.CharField(label='تعليق التحديث',widget=forms.Textarea(attrs={'rows':2}))
    accomplished=forms.CharField(label='الأعمال المنجزة',widget=forms.Textarea(attrs={'rows':2}))
    remaining=forms.CharField(label='الأعمال المتبقية (اكتب لا يوجد عند الإكمال)',widget=forms.Textarea(attrs={'rows':2}))
class CommentForm(forms.Form):
    body=forms.CharField(label='التعليق / الرد',widget=forms.Textarea(attrs={'rows':3}))
    parent=forms.ModelChoiceField(queryset=Comment.objects.none(),required=False,label='رد على تعليق')
    mentions=forms.ModelMultipleChoiceField(queryset=User.objects.none(),required=False,label='الإشارة إلى موظفين @')
    mentioned_units=forms.ModelMultipleChoiceField(queryset=Unit.objects.none(),required=False,label='الإشارة إلى وحدة')
    def __init__(self,*args,task,**kwargs):
        super().__init__(*args,**kwargs)
        from .services import recipients
        self.fields['parent'].queryset=task.comments.all()
        self.fields['mentions'].queryset=recipients(task)
        self.fields['mentioned_units'].queryset=Unit.objects.filter(pk__in=[task.unit_id,*task.participating_units.values_list('pk',flat=True)])
class ObstacleForm(forms.ModelForm):
    class Meta:
        model=Obstacle
        fields=['kind','description','caused_by','needs_decision','requested_action','intervention_owner','expected_resolution','impact']
        labels={'kind':'نوع العائق','description':'وصف المشكلة','caused_by':'الجهة المسببة','needs_decision':'يحتاج قرارًا إداريًا','requested_action':'الإجراء المطلوب','intervention_owner':'المطلوب تدخله','expected_resolution':'الحل المتوقع','impact':'درجة التأثير'}
        widgets={'expected_resolution':forms.DateInput(attrs={'type':'date'}),'description':forms.Textarea(attrs={'rows':2}),'requested_action':forms.Textarea(attrs={'rows':2})}
    def __init__(self,*args,task,**kwargs):
        super().__init__(*args,**kwargs)
        from .policy import tasks_for
        ids=[u.pk for u in User.objects.filter(is_active=True).exclude(role__in=[Role.VIEWER,Role.ADMIN]) if tasks_for(u).filter(pk=task.pk).exists()]
        self.fields['intervention_owner'].queryset=User.objects.filter(pk__in=ids)
class SubtaskForm(forms.ModelForm):
    class Meta:
        model=Subtask
        fields=['title','owner','due_date','progress','comment']
        labels={'title':'عنوان المهمة الفرعية','owner':'المسؤول','due_date':'الاستحقاق','progress':'الإنجاز','comment':'التعليق'}
        widgets={'due_date':forms.DateInput(attrs={'type':'date'}),'comment':forms.Textarea(attrs={'rows':2})}
class UnitForm(forms.ModelForm):
    class Meta:
        model=Unit
        fields=['name','description','head','active']
        labels={'name':'اسم الوحدة','description':'الوصف','head':'رئيس الوحدة','active':'نشطة'}
class EmployeeForm(UserCreationForm):
    class Meta:
        model=User
        fields=['username','first_name','last_name','employee_number','email','phone','job_title','manager','role','units','is_active']
        labels={'username':'اسم المستخدم','first_name':'الاسم الأول','last_name':'الاسم الأخير','employee_number':'الرقم الوظيفي','email':'البريد','phone':'الجوال','job_title':'المسمى الوظيفي','manager':'المدير المباشر','role':'الدور','units':'الوحدات','is_active':'الحساب نشط'}
class EmployeeEditForm(forms.ModelForm):
    class Meta(EmployeeForm.Meta): pass
class MeetingForm(forms.ModelForm):
    class Meta:
        model=Meeting
        fields=['number','name','committee','date']
        labels={'number':'رقم الاجتماع','name':'اسم الاجتماع','committee':'اللجنة','date':'تاريخ الاجتماع'}
        widgets={'date':forms.DateInput(attrs={'type':'date'})}
class DecisionForm(forms.ModelForm):
    minutes_file=forms.FileField(label='مرفق المحضر المعتمد',required=False,help_text='إرفاق المحضر مطلوب عند إدخال رقمه؛ الحد الأعلى 10 ميجابايت.')
    reason=forms.CharField(label='سبب التعديل',required=False,widget=forms.Textarea(attrs={'rows':2}))
    class Meta:
        model=Decision
        fields=['number','issuing_authority','meeting','approved_minutes_number','minutes_file','text','date','due_date','status','followup_owner']
        labels={'number':'رقم القرار','issuing_authority':'الجهة المصدرة','meeting':'الاجتماع','approved_minutes_number':'رقم المحضر المعتمد','text':'نص القرار','date':'تاريخ القرار','due_date':'تاريخ استحقاق القرار','status':'حالة القرار','followup_owner':'مسؤول المتابعة'}
        widgets={key:forms.DateInput(attrs={'type':'date'}) for key in ['date','due_date']}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['issuing_authority'].required=True; self.fields['due_date'].required=True
        self.fields['reason'].required=bool(self.instance.pk)
        self.fields['followup_owner'].queryset=User.objects.filter(is_active=True).exclude(role__in=[Role.ADMIN,Role.VIEWER])
    def clean(self):
        data=super().clean()
        if data.get('minutes_file') and not data.get('approved_minutes_number'): self.add_error('approved_minutes_number','رقم المحضر مطلوب مع المرفق.')
        if data.get('approved_minutes_number') and not data.get('minutes_file') and not self.instance.approved_minutes_attachment_id: self.add_error('minutes_file','أرفق المحضر المعتمد.')
        return data
class RulesForm(forms.ModelForm):
    class Meta:
        model=RuleSettings
        exclude=['id']
        labels={'followup_elapsed':'متابعة بعد مرور نسبة مدة (%)','risk_elapsed':'ترشيح للتعثر بعد مرور مدة (%)','risk_progress':'إنجاز أقل من (%) لترشيح التعثر','stale_days':'أيام بدون تحديث','remind_days':'تذكير قبل الاستحقاق بأيام','head_after':'تصعيد للرئيس بعد أيام تأخير','assistant_after':'تصعيد للمساعد بعد أيام','executive_after':'تصعيد للمدير بعد أيام','critical_multiplier':'معامل تسريع المهام الحرجة (0.1–1)'}
    def clean(self):
        d=super().clean()
        if all(k in d for k in ['head_after','assistant_after','executive_after']) and not 0<d['head_after']<=d['assistant_after']<=d['executive_after']: raise forms.ValidationError('رتّب مواعيد التصعيد من الرئيس إلى المدير، بدءًا من يوم واحد.')
        return d
