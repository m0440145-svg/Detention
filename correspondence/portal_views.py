from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.shortcuts import render,redirect,get_object_or_404
from django.http import JsonResponse,HttpResponse,FileResponse
from django.core.exceptions import PermissionDenied,ValidationError
from django.forms import modelform_factory
from django.utils import timezone
from django.contrib import messages
from .models import *
from . import services,governance,reports

@login_required
def center(request):
    user=request.user;error=''
    if request.method=='POST':
        try:
            action=request.POST.get('action');reason=request.POST.get('reason','')
            if action=='privacy':
                kind=request.POST.get('kind')
                if kind not in ['access','correct','destroy','withdraw']:raise ValidationError('طلب غير صالح.')
                services.reason_required(reason)
                SubjectRequest.objects.create(requester=user,kind=kind,description=reason,due_at=timezone.now()+timedelta(days=30))
            elif action=='schedule':
                kind=request.POST.get('report');frequency=request.POST.get('frequency')
                reports.report_rows(user,kind)
                if frequency not in ['daily','weekly','monthly']:raise ValidationError('تكرار غير صالح.')
                ReportSchedule.objects.create(user=user,report=kind,frequency=frequency)
            elif action=='preferences':
                NotificationPreference.objects.update_or_create(user=user,defaults={'email':bool(request.POST.get('email')),'daily_digest':bool(request.POST.get('digest')),'critical_sms':bool(request.POST.get('sms'))})
            elif action=='privacy-response':
                if user.role!='executive' and not services.has_access(user,'compliance'):raise PermissionDenied()
                row=SubjectRequest.objects.get(pk=request.POST.get('request'));services.reason_required(reason)
                if row.state=='completed':raise ValidationError('الطلب مكتمل.')
                row.state='completed';row.response=reason;row.handled_by=user;row.save()
                from core import services as core_services
                core_services.audit(None,user,'استجابة لطلب صاحب بيانات',new={'request':str(row.uid),'kind':row.kind})
            else:raise ValidationError('إجراء غير صالح.')
            messages.success(request,'تم تسجيل الإجراء.');return redirect('/communications/operations/')
        except (PermissionDenied,ValidationError) as e:error=str(e)
    requests=SubjectRequest.objects.all() if user.role=='executive' or services.has_access(user,'compliance') else SubjectRequest.objects.filter(requester=user)
    return render(request,'correspondence/operations.html',{'error':error,'requests':requests,'schedules':ReportSchedule.objects.filter(user=user),'reports':reports.REPORTS,'pref':NotificationPreference.objects.filter(user=user).first(),'cases':RetentionCase.objects.filter(mail__in=services.visible(user)).prefetch_related('approvals'),'compliance':user.role=='executive' or services.has_access(user,'compliance')})

@login_required
def features(request,pk):
    mail=get_object_or_404(services.visible(request.user),pk=pk);error=''
    if request.method=='POST':
        try:
            action=request.POST.get('action');reason=request.POST.get('reason','')
            if action=='signature':governance.request_signature(request.user,mail,reason)
            elif action=='reopen':governance.reopen(request.user,mail,reason)
            elif action=='retention':governance.propose_retention(request.user,mail,request.POST.get('retention_action'),reason)
            elif action=='hold':
                if request.user.role!='executive' and not services.has_access(request.user,'compliance'):raise PermissionDenied()
                services.reason_required(reason);LegalHold.objects.create(mail=mail,imposed_by=request.user,reason=reason)
                services.event(mail,request.user,'حجز قانوني',reason)
            elif action=='grant':
                from core.models import User
                target=get_object_or_404(User,pk=request.POST.get('user'))
                governance.grant_export(request.user,mail,target,timezone.now()+timedelta(hours=24),reason,bool(request.POST.get('download')),bool(request.POST.get('printing')))
            elif action in ['approve-retention','execute-retention']:
                case=get_object_or_404(RetentionCase,mail=mail,pk=request.POST.get('case'))
                if action=='approve-retention':governance.approve_retention(request.user,case,reason)
                else:governance.execute_retention(request.user,case,request.POST.get('minutes',''),reason)
            else:raise ValidationError('إجراء غير صالح.')
            messages.success(request,'تم تسجيل الإجراء.');return redirect('/communications/'+str(pk)+'/features/')
        except (ValidationError,PermissionDenied) as exc:error=str(exc)
    from core.models import User
    related=services.visible(request.user).filter(related=mail)
    return render(request,'correspondence/features.html',{'mail':mail,'error':error,'revisions':governance.revision_diff(mail),'cases':RetentionCase.objects.filter(mail=mail),'holds':LegalHold.objects.filter(mail=mail,active=True),'signatures':mail.signature_requests.all(),'users':[u for u in User.objects.filter(is_active=True) if services.visible(u).filter(pk=mail.pk).exists()],'related':related})

@login_required
def export_report(request,kind,format):
    headers,rows=reports.report_rows(request.user,kind)
    if format=='xlsx':data=reports.xlsx_bytes(headers,rows);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif format=='pdf':data=reports.report_pdf(headers,rows);mime='application/pdf'
    else:raise ValidationError('صيغة غير صالحة.')
    from core import services as cs
    cs.audit(None,request.user,'تصدير تقرير مراسلات',new={'report':kind,'format':format,'rows':len(rows)})
    response=HttpResponse(data,content_type=mime);response['Content-Disposition']=f'attachment; filename="{kind}.{format}"';response['Cache-Control']='no-store';return response

@login_required
def pdf(request,pk):
    mail=get_object_or_404(services.visible(request.user),pk=pk)
    if not governance.can_export(request.user,mail,'download'):raise PermissionDenied('إذن الطباعة مطلوب.')
    from .documents import pdf_bytes
    signed=mail.signature_requests.filter(state='completed').order_by('-pk').first()
    if signed and mail.signature_method=='pades':
        services.event(mail,request.user,'تنزيل PDF موقع','تنزيل النسخة الموقعة الأصلية')
        return FileResponse(signed.signed_pdf.open('rb'),as_attachment=True,filename=mail.code+'.pdf')
    response=HttpResponse(pdf_bytes(mail,watermark=str(request.user) if mail.secrecy in ['secret','restricted'] else '',qr=bool(mail.reference)),content_type='application/pdf')
    response['Content-Disposition']='inline; filename="correspondence.pdf"';response['Cache-Control']='no-store'
    services.event(mail,request.user,'طباعة PDF/A','تصدير مستند بصيغة PDF/A',ip=request.META.get('REMOTE_ADDR',''))
    return response

def verify_public(request,uid):
    mail=get_object_or_404(Correspondence,uid=uid,reference__isnull=False)
    # Public verifier never leaks protected metadata.
    if mail.secrecy in ['secret','restricted']:
        return JsonResponse({'detail':'التحقق من هذه المراسلة يتم عبر الجهة المصدرة.'},status=404)
    signature=mail.signature_requests.filter(state='completed').order_by('-pk').first()
    valid=bool(signature and mail.signed_at and signature.content_hash==__import__('correspondence.documents',fromlist=['content_hash']).content_hash(mail) and mail.status in ['issued','sent','received','archived'])
    response=JsonResponse({'reference':mail.reference,'date':mail.signed_at.isoformat() if mail.signed_at else None,'party':mail.party.name,'signer':str(mail.signed_by or ''),'valid':valid,'method':mail.signature_method,'verification':'cryptographic' if valid else 'not-verified'})
    response['Cache-Control']='no-store';return response

CONFIG={'integrations':(Integration,['key','kind','endpoint','secret_env','active','events','options']),'processing':(ProcessingActivity,['name','purpose','legal_basis','categories','recipients','retention_reference','owner'])}
@login_required
def configuration(request,kind):
    if kind not in CONFIG:raise PermissionDenied()
    if kind=='integrations' and request.user.role!='admin':raise PermissionDenied()
    if kind=='processing' and request.user.role!='executive' and not services.has_access(request.user,'compliance'):raise PermissionDenied()
    model,fields=CONFIG[kind]
    instance=get_object_or_404(model,pk=request.GET['edit']) if request.GET.get('edit','').isdigit() else None
    form=modelform_factory(model,fields=fields)(request.POST or None,instance=instance)
    if request.method=='POST' and form.is_valid():
        if kind=='integrations' and form.cleaned_data['active']:
            from .integrations import validate_endpoint,secret
            try:validate_endpoint(form.cleaned_data['endpoint']);secret(form.instance)
            except ValidationError as exc:form.add_error(None,exc)
        if not form.errors:
            saved=form.save()
            from core import services as cs
            cs.audit(None,request.user,'ضبط التكامل/سجل المعالجة',new={'kind':kind,'id':saved.pk});return redirect(request.path)
    return render(request,'correspondence/governance_config.html',{'form':form,'kind':kind,'rows':model.objects.all(),'deliveries':Delivery.objects.order_by('-pk')[:30] if kind=='integrations' else []})

@login_required
def preview_attachment(request,pk):
    from pathlib import Path
    attachment=get_object_or_404(MailAttachment,pk=pk,mail__in=services.visible(request.user))
    if not attachment.file.name:raise ValidationError('تم إتلاف الملف وفق المحضر.')
    extension=Path(attachment.name).suffix.lower()
    protected=attachment.mail.secrecy in ['restricted','secret']
    # Protected binary viewers require an explicit export grant; a browser cannot reliably prevent saving its bytes.
    if protected and not governance.can_export(request.user,attachment.mail,'download'):raise PermissionDenied('عرض الملف الثنائي يتطلب إذن إخراج؛ النص المفهرس يظل ضمن الوصول.')
    types={'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.txt':'text/plain; charset=utf-8','.csv':'text/plain; charset=utf-8'}
    if extension not in types:raise ValidationError('استخدم التنزيل للملفات المكتبية؛ لا يعرض كود نشط في المتصفح.')
    services.event(attachment.mail,request.user,'اطلاع على مرفق','معاينة ضمن الصلاحية',new={'attachment':attachment.pk},ip=request.META.get('REMOTE_ADDR',''))
    response=FileResponse(attachment.file.open('rb'),content_type=types[extension],as_attachment=False,filename=attachment.name)
    response['Cache-Control']='no-store';response['X-Frame-Options']='SAMEORIGIN';response['Content-Security-Policy']="default-src 'none'; sandbox; frame-ancestors 'self'";return response
