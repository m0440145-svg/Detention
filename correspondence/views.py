import csv,io
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.forms import modelform_factory
from django.http import FileResponse,Http404,HttpResponse,JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.models import Role,User,Task
from core.policy import tasks_for
from core import services as core_services
from .models import *
from .forms import *
from . import services

def ip(request): return request.META.get('REMOTE_ADDR','')
def fail(exc): return '؛ '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc)

def query(request):
    qs=services.visible(request.user)
    if request.GET.get('saved','').isdigit():
        saved=get_object_or_404(SavedSearch,user=request.user,pk=request.GET['saved'])
        values=saved.filters.copy(); values.update({k:v for k,v in request.GET.items() if k!='saved'})
        from django.http import QueryDict
        params=QueryDict(mutable=True);params.update(values);request.GET=params
    if request.GET.get('q'): qs=qs.filter(Q(subject__icontains=request.GET['q'])|Q(reference__icontains=request.GET['q'])|Q(external_number__icontains=request.GET['q'])|Q(body__icontains=request.GET['q'])|Q(party__name__icontains=request.GET['q'])|Q(search_text__contains=services.normalize_arabic(request.GET['q'])))
    for field in ['kind','status','priority','secrecy']:
        if request.GET.get(field): qs=qs.filter(**{field:request.GET[field]})
    if request.GET.get('unit','').isdigit(): qs=qs.filter(unit_id=request.GET['unit'])
    if request.GET.get('late')=='1': qs=qs.filter(due_at__lt=timezone.now(),paused_at__isnull=True).exclude(status__in=services.TERMINAL)
    return qs

@login_required
def dashboard(request):
    if request.user.role==Role.ADMIN: return redirect('mail-settings')
    qs=services.visible(request.user); alerts=MailAlert.objects.filter(recipient=request.user,mail__in=qs).select_related('mail')[:12]
    metrics=[('إجمالي المراسلات',qs.count()),('وارد مفتوح',qs.filter(kind=Kind.IN).exclude(status__in=services.TERMINAL).count()),('بانتظار الاعتماد',qs.filter(status__in=[MailStatus.REVIEW,MailStatus.APPROVAL,MailStatus.SIGNING]).count()),('متأخرة',qs.filter(due_at__lt=timezone.now(),paused_at__isnull=True).exclude(status__in=services.TERMINAL).count())]
    return render(request,'correspondence/dashboard.html',{'metrics':metrics,'rows':qs[:8],'alerts':alerts})

@login_required
def listing(request,box='all'):
    if request.user.role==Role.ADMIN: raise PermissionDenied('مسؤول النظام يدير إعدادات الاتصالات فقط.')
    qs=query(request)
    if box=='inbox': qs=qs.filter(kind=Kind.IN)
    elif box=='outbox': qs=qs.exclude(kind=Kind.IN)
    elif box=='archive': qs=qs.filter(status=MailStatus.ARCHIVED)
    elif box=='mine': qs=qs.filter(Q(owner=request.user)|Q(referrals__recipient=request.user,referrals__completed_at__isnull=True)).distinct()
    from django.core.paginator import Paginator
    title={'all':'سجل المراسلات','inbox':'صندوق الوارد','outbox':'صندوق الصادر','archive':'أرشيف المراسلات','mine':'بانتظار إجرائي'}.get(box,'المراسلات')
    return render(request,'correspondence/list.html',{'title':title,'rows':Paginator(qs,30).get_page(request.GET.get('page')),'kinds':Kind.choices,'statuses':MailStatus.choices,'priorities':Correspondence._meta.get_field('priority').choices})

@login_required
def compose(request,pk=None):
    mail=get_object_or_404(services.visible(request.user),pk=pk) if pk else None
    if mail: services.require_edit(request.user,mail)
    elif request.user.role in [Role.ADMIN,Role.VIEWER,Role.BOARD]: raise PermissionDenied()
    initial={}
    if request.GET.get('reply','').isdigit():
        source=get_object_or_404(services.visible(request.user),pk=request.GET['reply']); initial={'kind':Kind.RPL,'related':source,'party':source.party,'unit':source.unit,'owner':request.user,'subject':'رد: '+source.subject}
    form=MailForm(request.POST or None,request.FILES or None,instance=mail,user=request.user,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            d=form.cleaned_data.copy(); upload=d.pop('attachment',None); reason=d.pop('reason',''); authorized=d.pop('authorized',[]); cc=d.pop('cc',[])
            from django.db import transaction
            with transaction.atomic():
                if mail:
                    d.pop('kind',None); mail=services.edit_mail(request.user,mail,d,reason,ip(request))
                    if request.user.role==Role.EXECUTIVE:services.update_access(request.user,mail,authorized,cc,reason,ip(request))
                    if upload: services.attach(request.user,mail,upload,reason,ip(request))
                else: mail=services.create_mail(request.user,d,authorized,cc,upload,ip(request))
            messages.success(request,'تم حفظ المراسلة في سجل الخادم.'); return redirect('mail-detail',pk=mail.pk)
        except (ValidationError,PermissionDenied) as exc: form.add_error(None,fail(exc))
    return render(request,'correspondence/form.html',{'title':'تعديل المراسلة' if mail else 'تسجيل مراسلة جديدة','form':form})

@login_required
def detail(request,pk):
    mail=get_object_or_404(services.visible(request.user),pk=pk); error=''
    if request.method=='POST':
        try:
            form_type=request.POST.get('form_type')
            if form_type=='action':
                form=ActionForm(request.POST)
                if not form.is_valid(): raise ValidationError(str(form.errors))
                mail=services.transition(request.user,mail,**form.cleaned_data,ip=ip(request))
            elif form_type=='refer':
                form=ReferralForm(request.POST,mail=mail)
                if not form.is_valid(): raise ValidationError(str(form.errors))
                services.refer(request.user,mail,**form.cleaned_data,ip=ip(request))
            elif form_type=='attachment':
                if not request.FILES.get('file'): raise ValidationError('اختر مرفقًا.')
                services.attach(request.user,mail,request.FILES['file'],request.POST.get('reason',''),ip(request))
            elif form_type=='ocr':
                from .ocr import request_extraction
                attachment=get_object_or_404(mail.attachments,pk=request.POST.get('attachment'))
                request_extraction(request.user,attachment,ip(request))
            elif form_type=='complete':
                row=get_object_or_404(mail.referrals,pk=request.POST.get('referral')); services.complete_referral(request.user,row,request.POST.get('reason',''),ip(request))
            elif form_type=='task':
                from datetime import date
                row=get_object_or_404(mail.referrals,pk=request.POST.get('referral'))
                try: due=date.fromisoformat(request.POST.get('due_date',''))
                except ValueError: raise ValidationError('حدد موعدًا صالحًا للمهمة.')
                services.convert_to_task(request.user,row,{'due_date':due},ip(request))
            elif form_type=='extend':
                form=ExtendForm(request.POST)
                if not form.is_valid(): raise ValidationError(str(form.errors))
                services.extend_due(request.user,mail,form.cleaned_data['due_at'],form.cleaned_data['reason'],ip(request))
            else: raise ValidationError('إجراء غير معروف.')
            messages.success(request,'سُجّل الإجراء وسببه في سجل التدقيق.'); return redirect('mail-detail',pk=mail.pk)
        except (ValidationError,PermissionDenied) as exc: error=fail(exc)
    services.event(mail,request.user,'اطلاع','عرض المراسلة',ip=ip(request))
    from core.views import decisions_for
    decision_visible=bool(mail.decision_id and decisions_for(request.user).filter(pk=mail.decision_id).exists())
    related_visible=bool(mail.related_id and services.visible(request.user).filter(pk=mail.related_id).exists())
    referrals=list(mail.referrals.select_related('recipient','created_by','task'))
    for row in referrals: row.task_visible=bool(row.task_id and tasks_for(request.user).filter(pk=row.task_id).exists())
    return render(request,'correspondence/detail.html',{'decision_visible':decision_visible,'related_visible':related_visible,'mail':mail,'body':services.rendered_body(mail),'error':error,'action_form':ActionForm(),'referral_form':ReferralForm(mail=mail),'extend_form':ExtendForm(),'referrals':referrals,'events':mail.events.select_related('actor').order_by('-pk')[:100],'chain_valid':services.verify_chain(mail),'can_manage':services.can_manage(request.user,mail),'can_write':request.user.role not in [Role.ADMIN,Role.VIEWER,Role.BOARD],'can_download':mail.secrecy not in [Secrecy.RESTRICTED,Secrecy.SECRET],'can_edit':mail.status in [MailStatus.DRAFT,MailStatus.REGISTERED,MailStatus.RETURNED]})

@login_required
def download(request,pk):
    attachment=get_object_or_404(MailAttachment.objects.select_related('mail'),pk=pk)
    if not services.visible(request.user).filter(pk=attachment.mail_id).exists(): raise Http404()
    if attachment.mail.secrecy in [Secrecy.RESTRICTED,Secrecy.SECRET]: raise PermissionDenied('التنزيل محظور افتراضيًا للمراسلات المقيدة والسرية.')
    try: stream=attachment.file.open('rb')
    except FileNotFoundError: raise Http404()
    services.event(attachment.mail,request.user,'تنزيل مرفق','تنزيل مصرح',new={'attachment':attachment.pk,'sha256':attachment.sha256},ip=ip(request))
    return FileResponse(stream,as_attachment=True,filename=attachment.name)

@login_required
def print_mail(request,pk):
    mail=get_object_or_404(services.visible(request.user),pk=pk)
    if mail.secrecy in [Secrecy.RESTRICTED,Secrecy.SECRET]: raise PermissionDenied('الطباعة غير متاحة للمراسلات المقيدة والسرية.')
    services.event(mail,request.user,'فتح نسخة للطباعة','عرض نسخة قابلة للطباعة؛ لا يثبت وقوع الطباعة',ip=ip(request))
    return render(request,'correspondence/print.html',{'mail':mail,'body':services.rendered_body(mail)})

@login_required
def reports(request):
    qs=query(request)
    if request.user.role==Role.ADMIN: raise PermissionDenied()
    if request.GET.get('export')=='csv':
        response=HttpResponse(content_type='text/csv; charset=utf-8'); response['Content-Disposition']='attachment; filename="correspondence.csv"';response.write('\ufeff'); writer=csv.writer(response);writer.writerow(['القيد','النوع','الموضوع','الجهة','الوحدة','الحالة','الاستحقاق'])
        for m in qs.exclude(secrecy__in=[Secrecy.RESTRICTED,Secrecy.SECRET]):
            values=[m.code,m.get_kind_display(),m.subject,m.party.name,m.unit.name,m.get_status_display(),str(m.due_at)]
            writer.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r')) else v for v in values]);services.event(m,request.user,'تصدير تقرير','تصدير CSV مصرح',ip=ip(request))
        return response
    units=[]
    for unit in Unit.objects.filter(pk__in=qs.values('unit_id')).distinct():
        rows=qs.filter(unit=unit); units.append({'name':unit.name,'total':rows.count(),'open':rows.exclude(status__in=services.TERMINAL).count(),'late':rows.filter(due_at__lt=timezone.now(),paused_at__isnull=True).exclude(status__in=services.TERMINAL).count(),'done':rows.filter(status__in=[MailStatus.DONE,MailStatus.ARCHIVED]).count()})
    return render(request,'correspondence/reports.html',{'units':units,'rows':qs[:30]})

@login_required
def settings_view(request,kind=None,pk=None):
    template_manager=kind=='template' and services.has_access(request.user,'templates')
    if request.user.role!=Role.ADMIN and not template_manager: raise PermissionDenied('إدارة الضبط لمسؤول النظام، والقوالب للمخول.')
    if not kind:
        form=SettingsForm(request.POST or None,instance=MailSettings.current())
        if request.method=='POST' and form.is_valid():
            saved=form.save(); core_services.audit(None,request.user,'ضبط SLA المراسلات',new={'settings':saved.pk}); return redirect('mail-settings')
        return render(request,'correspondence/settings.html',{'form':form,'config_labels':CONFIG_LABELS,'settings':MailSettings.current(),'links':[(key,label) for key,label in CONFIG_LABELS.items()]})
    if kind not in CONFIG_MODELS: raise Http404()
    model=CONFIG_MODELS[kind]; instance=get_object_or_404(model,pk=pk) if pk else None
    if kind=='template' and instance: raise PermissionDenied('القالب المعتمد لا يُعدّل؛ أنشئ إصدارًا جديدًا بالاسم نفسه ورقم إصدار أعلى.')
    labels={'name':'الاسم','email':'البريد الإلكتروني','supervisory':'جهة إشرافية','active':'نشط','parent':'التصنيف الأب','route_unit':'وحدة التوجيه التلقائي','retention':'فئة الاستبقاء','years':'سنوات الحفظ (فارغ للحفظ الدائم)','legal_reference':'مرجع السياسة المعتمدة','approved':'معتمد وفق مرجع الجمعية','version':'رقم الإصدار','kind':'النوع','body':'نص القالب والمتغيرات','levels':'ترتيب أدوار الاعتماد (JSON)','authority_reference':'مرجع مصفوفة الصلاحيات','date':'التاريخ','user':'المستخدم الحالي','register':'تسجيل وإحالة الوارد','templates':'إدارة القوالب','compliance':'مسؤول امتثال','principal':'صاحب الصلاحية','delegate':'النائب','starts':'بداية التفويض','ends':'نهاية التفويض','reason':'سبب التفويض'}
    form_class=modelform_factory(model,exclude=['created_at'],labels=labels); form=form_class(request.POST or None,instance=instance)
    if request.method=='POST' and form.is_valid():
        saved=form.save();core_services.audit(None,request.user,'ضبط المراسلات: '+kind,new={'id':saved.pk});messages.success(request,'تم حفظ إعدادات الوحدة.');return redirect('mail-config',kind=kind)
    return render(request,'correspondence/config.html',{'title':CONFIG_LABELS[kind],'kind':kind,'form':form,'records':model.objects.all()})


@login_required
def saved_searches(request):
    if request.user.role==Role.ADMIN:raise PermissionDenied()
    error=''
    if request.method=='POST':
        name=request.POST.get('name','').strip()[:120]
        if not name:error='اسم البحث مطلوب.'
        else:
            allowed=['q','kind','status','priority','secrecy','unit','late']
            filters={k:request.POST[k] for k in allowed if request.POST.get(k)}
            SavedSearch.objects.update_or_create(user=request.user,name=name,defaults={'filters':filters})
            return redirect('mail-searches')
    return render(request,'correspondence/searches.html',{'rows':SavedSearch.objects.filter(user=request.user),'error':error,'kinds':Kind.choices,'statuses':MailStatus.choices})


@login_required
@require_POST
def bulk_view(request):
    try:
        ids=[int(x) for x in request.POST.getlist('mail_ids')]
        result=services.bulk_action(request.user,ids,request.POST.get('action'),request.POST.get('reason',''),ip(request))
        messages.success(request,f"سجل الإجراء على {result['processed']} مراسلة.")
    except (ValueError,ValidationError,PermissionDenied) as exc: messages.error(request,fail(exc))
    return redirect('mail-list',box='all')
