import json,csv,io
from core.models import User
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.http import HttpResponse,FileResponse,Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.db.models import Q,Count
from django.utils import timezone
from django.views.decorators.cache import never_cache
from .models import *
from . import services,governance_services as ops,election_services as elections,workflow_services as workflow,platform_services as platform
from .workflow_forms import EntryForm,DocumentForm,ElectionForm,CandidateForm,ReasonForm,PlatformForm
from .workflow_views import failure,checked

def entry_save(user,kind,data,instance=None):
    form=EntryForm(data,user=user,kind=kind,instance=instance);d=checked(form)
    extra={key[5:]:(value.isoformat() if hasattr(value,'isoformat') else value) for key,value in d.items() if key.startswith('data_')}
    base={key:value for key,value in d.items() if not key.startswith('data_')};row=ops.save_entry(user,kind=kind,data=extra,instance=instance,**base);return row

@login_required
@never_cache
def entry_list(request,kind):
    if kind not in dict(GovernanceEntry.KINDS):raise Http404()
    rows=ops.entries_for(request.user,request.GET.get('archived')=='1').filter(kind=kind).order_by('due_date','pk');error='';instance=None
    if request.GET.get('edit','').isdigit():instance=get_object_or_404(ops.entries_for(request.user),pk=request.GET['edit'],kind=kind)
    if request.method=='POST':
        try:
            if request.POST.get('action')=='save':
                instance=get_object_or_404(ops.entries_for(request.user),pk=request.POST['entry'],kind=kind) if request.POST.get('entry') else None
                entry_save(request.user,kind,request.POST,instance)
            elif request.POST.get('action')=='link':
                from core.views import decisions_for
                from core.policy import tasks_for
                row=get_object_or_404(ops.entries_for(request.user),pk=request.POST.get('entry'),kind='recommendation')
                d=get_object_or_404(decisions_for(request.user),pk=request.POST['decision']) if request.POST.get('decision') else None
                t=get_object_or_404(tasks_for(request.user),pk=request.POST['task']) if request.POST.get('task') else None
                ops.link_recommendation(request.user,row,d,t,request.POST.get('reason',''),int(request.POST.get('version',0)))
            else:
                row=get_object_or_404(ops.entries_for(request.user,True),pk=request.POST.get('entry'),kind=kind);d=checked(ReasonForm(request.POST));ops.entry_transition(request.user,row,request.POST.get('action'),**d)
            messages.success(request,'حفظ السجل في قاعدة البيانات وسجل التدقيق.');return redirect(request.path)
        except (ValidationError,PermissionDenied,ValueError) as exc:error=failure(exc)
    form=EntryForm(request.POST if error and request.POST.get('action')=='save' else None,user=request.user,kind=kind,instance=instance)
    manager=any(services.can_manage(request.user,c) for c in services.committees_for(request.user))
    events=Event.objects.filter(stream__scope__in=[f'committee:{c.pk}' for c in services.committees_for(request.user)]).order_by('-pk')[:30] if manager else []
    if request.GET.get('export')=='csv':
        output=io.StringIO();writer=csv.writer(output);writer.writerow(['النوع','الرقم','العنوان','اللجنة','الحالة','الاستحقاق','الحقول'])
        def csv_safe(value):
            value=str(value)
            return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        for r in rows:writer.writerow([r.get_kind_display(),r.pk,csv_safe(r.title),csv_safe(r.committee.name),r.status,r.due_date,csv_safe(json.dumps(r.data,ensure_ascii=False))])
        response=HttpResponse('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8');response['Content-Disposition']=f'attachment; filename="governance-{kind}.csv"';return response
    return render(request,'meetinghub/governance.html',{'kind':kind,'title':dict(GovernanceEntry.KINDS)[kind],'rows':rows,'form':form,'instance':instance,'error':error,'may_create':manager or kind=='volunteer','events':events,'execution_decisions':__import__('core.views',fromlist=['decisions_for']).decisions_for(request.user) if request.user.role in ['executive','assistant'] else [],'execution_tasks':__import__('core.policy',fromlist=['tasks_for']).tasks_for(request.user) if request.user.role in ['executive','assistant'] else []})

@login_required
@never_cache
def documents(request):
    error='';form=DocumentForm(request.POST or None,request.FILES or None,user=request.user)
    if request.method=='POST' and form.is_valid():
        try:
            data=form.cleaned_data.copy();data['upload']=data.pop('file');ops.upload_document(request.user,**data);messages.success(request,'رفعت الوثيقة وفُحصت وخزنت مشفرة.');return redirect(request.path)
        except (ValidationError,PermissionDenied) as exc:error=failure(exc)
    rows=[d for d in Document.objects.filter(committee__in=services.committees_for(request.user)).select_related('owner','meeting__meeting') if ops.document_allowed(request.user,d)]
    from core.models import Attachment
    from core.views import decisions_for
    from core.policy import tasks_for
    from .session_services import file_allowed
    files=[{'name':f.name,'url':f'/meetings/files/{f.pk}/','source':'اجتماع'} for f in MeetingFile.objects.filter(meeting__in=services.records_for(request.user,True)) if file_allowed(request.user,f)]
    for f in Attachment.objects.filter(Q(decision__in=decisions_for(request.user))|Q(task__in=tasks_for(request.user))|Q(subtask__task__in=tasks_for(request.user))):
        decision=f.decision or (f.task.decision if f.task_id else (f.subtask.task.decision if f.subtask_id else None))
        record=MeetingRecord.objects.filter(meeting_id=decision.meeting_id).first() if decision else None
        if not record or platform.permit_download(record):files.append({'name':f.original_name,'url':f'/files/{f.pk}/','source':'مهمة / قرار'})
    return render(request,'meetinghub/documents.html',{'form':form,'rows':rows,'files':files,'error':error})

@login_required
@never_cache
def document_download(request,pk):
    doc=get_object_or_404(Document,pk=pk)
    if not ops.document_allowed(request.user,doc):raise Http404()
    services.event(f'committee:{doc.committee_id}',request.user,'تنزيل وثيقة حوكمة','وصول مصرح',{'document':doc.pk,'sha256':doc.sha256})
    try:response=FileResponse(doc.file.open('rb'),as_attachment=True,filename=doc.name)
    except FileNotFoundError:raise Http404()
    response['Cache-Control']='no-store';return response

@login_required
def election_list(request):
    error='';form=ElectionForm(request.POST or None,user=request.user)
    if request.method=='POST' and form.is_valid():
        try:e=elections.create_election(request.user,**form.cleaned_data);return redirect('meeting-election',pk=e.pk)
        except (ValidationError,PermissionDenied) as exc:error=failure(exc)
    return render(request,'meetinghub/elections.html',{'rows':Election.objects.filter(committee__in=services.committees_for(request.user)) if request.user.role!='admin' else [],'form':form,'may_create':any(services.can_manage(request.user,c) for c in services.committees_for(request.user)),'error':error})

@login_required
@never_cache
def election_detail(request,pk):
    election=get_object_or_404(Election,pk=pk,committee__in=services.committees_for(request.user));ops.require_committee(request.user,election.committee);error='';form=CandidateForm(user=request.user,election=election)
    if request.method=='POST':
        try:
            action=request.POST.get('action');reason=request.POST.get('reason','')
            if action=='nominate':form=CandidateForm(request.POST,user=request.user,election=election);elections.nominate(request.user,election,**checked(form))
            elif action=='review':elections.review_candidate(request.user,get_object_or_404(election.candidates,pk=request.POST.get('candidate')),request.POST.get('approved')=='yes',reason)
            elif action=='freeze':elections.freeze_election(request.user,election,reason,int(request.POST.get('version','0')))
            elif action=='close':elections.close_election(request.user,election,reason,int(request.POST.get('version','0')))
            elif action=='cast':elections.cast_election(request.user,election,[int(pk) for pk in request.POST.getlist('choices')],represented=get_object_or_404(Member,pk=request.POST['represented']) if request.POST.get('represented') else None)
            elif action=='proxy':elections.approve_election_proxy(request.user,election,get_object_or_404(Member,pk=request.POST.get('principal')),get_object_or_404(Member,pk=request.POST.get('delegate')),get_object_or_404(Document,pk=request.POST.get('document')),ops.stamp(request.POST.get('valid_until')),reason)
            else:raise ValidationError('إجراء غير صالح.')
            messages.success(request,'حفظ إجراء الانتخابات دون كشف الاختيارات السرية.');return redirect(request.path)
        except (ValidationError,PermissionDenied,ValueError) as exc:error=failure(exc)
    manager=services.can_manage(request.user,election.committee);candidates=election.candidates.select_related('member__user','document')
    if not manager:candidates=candidates.filter(Q(approved=True)|Q(member__user=request.user))
    people=services.active_members().filter(committees=election.committee).exclude(role='observer')
    proxy_targets=people.filter(pk__in=ElectionProxy.objects.filter(election=election,delegate__user=request.user,valid_until__gt=timezone.now()).values('principal_id'))
    return render(request,'meetinghub/election.html',{'election':election,'form':form,'candidates':candidates,'manager':manager,'error':error,'result':ElectionResult.objects.filter(election=election).first(),'roll':VoterRoll.objects.filter(election=election).first(),'people':people,'proxy_targets':proxy_targets,'documents':Document.objects.filter(committee=election.committee) if manager else []})

@login_required
@never_cache
def ideas(request):
    committees=services.committees_for(request.user);error=''
    if request.user.role=='admin':raise PermissionDenied()
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='submit':ops.submit_idea(request.user,get_object_or_404(committees,pk=request.POST.get('committee')),request.POST.get('body',''))
            elif action=='vote':ops.vote_idea(request.user,get_object_or_404(Idea,pk=request.POST.get('idea'),committee__in=committees))
            elif action=='accept':ops.accept_idea(request.user,get_object_or_404(Idea,pk=request.POST.get('idea'),committee__in=committees),get_object_or_404(User,is_active=True,pk=request.POST.get('owner')),__import__('datetime').date.fromisoformat(request.POST.get('due_date','')),request.POST.get('expected_impact',''),request.POST.get('reason',''))
            else:raise ValidationError('إجراء غير صالح.')
            return redirect(request.path)
        except (ValidationError,PermissionDenied,ValueError) as exc:error=failure(exc)
    rows=Idea.objects.filter(committee__in=committees).annotate(vote_count=Count('votes'))
    return render(request,'meetinghub/ideas.html',{'rows':rows,'committees':committees,'owners':User.objects.filter(is_active=True).exclude(role__in=['admin','viewer']),'error':error,'manager':any(services.can_manage(request.user,c) for c in committees)})

@login_required
@never_cache
def search(request):
    from core.views import decisions_for
    from core.policy import tasks_for
    q=request.GET.get('q','').strip()[:150];records=services.records_for(request.user,True);results=[]
    if q:
        for r in records.filter(Q(meeting__name__icontains=q)|Q(meeting__number__icontains=q)|Q(committee__name__icontains=q)):results.append({'label':r.meeting.name,'type':'اجتماع','url':f'/meetings/{r.pk}/','reference':r.meeting.number})
        for r in MinuteRevision.objects.filter(minutes__meeting__in=records).filter(Q(body__icontains=q)|Q(minutes__meeting__meeting__number__icontains=q)):
            if workflow.minute_visible(request.user,r):results.append({'label':r.body[:160],'type':'محضر','url':f'/meetings/minutes/{r.pk}/print/','reference':f'إصدار {r.version} · {r.digest}'})
        for d in decisions_for(request.user).filter(Q(number__icontains=q)|Q(text__icontains=q)):results.append({'label':d.text[:160],'type':'قرار','url':f'/decisions/{d.pk}/','reference':d.number})
        for t in tasks_for(request.user).filter(Q(title__icontains=q)|Q(description__icontains=q)):results.append({'label':t.title,'type':'مهمة','url':f'/tasks/{t.pk}/','reference':t.code})
        for f in Document.objects.filter(committee__in=services.committees_for(request.user),name__icontains=q):
            if ops.document_allowed(request.user,f):results.append({'label':f.name,'type':'وثيقة','url':f'/meetings/documents/{f.pk}/','reference':f.sha256})
        for f in MeetingFile.objects.filter(meeting__in=records,name__icontains=q):
            if workflow.session.file_allowed(request.user,f):results.append({'label':f.name,'type':'مرفق اجتماع','url':f'/meetings/files/{f.pk}/','reference':f.sha256})
    return render(request,'meetinghub/search.html',{'results':results,'q':q,'semantic':platform.provider_ready('semantic')})

@login_required
@never_cache
def reports(request):
    from core.views import decisions_for
    from core.policy import tasks_for
    records=services.records_for(request.user,True);committee=request.GET.get('committee','');start=request.GET.get('start','');end=request.GET.get('end','')
    try:
        if committee.isdigit():records=records.filter(committee_id=committee)
        if start:records=records.filter(starts_at__date__gte=__import__('datetime').date.fromisoformat(start))
        if end:records=records.filter(starts_at__date__lte=__import__('datetime').date.fromisoformat(end))
    except ValueError:raise ValidationError('تاريخ مرشح غير صالح.')
    if request.GET.get('status') in ['draft','scheduled','cancelled']:records=records.filter(status=request.GET['status'])
    ds=decisions_for(request.user).filter(meeting__governance__in=records);tasks=tasks_for(request.user).filter(decision__in=ds)
    if request.GET.get('unit','').isdigit():tasks=tasks.filter(unit_id=request.GET['unit'])
    if request.GET.get('priority'):tasks=tasks.filter(priority=request.GET['priority'])
    attendance=Attendance.objects.filter(eligibility__roster__meeting__in=records,eligibility__roster__generation=__import__('django.db.models',fromlist=['F']).F('eligibility__roster__meeting__roster_generation'))
    eligible=Eligibility.objects.filter(roster__meeting__in=records,roster__generation=__import__('django.db.models',fromlist=['F']).F('roster__meeting__roster_generation'),invited=True)
    items=[('إجمالي الاجتماعات',records.count(),'الاجتماعات ضمن نطاق المستخدم والمرشحات'),('نسبة الحضور %',round(attendance.exclude(first_entered_at=None).count()/eligible.count()*100,1) if eligible.exists() else 0,'مدعوون سجلوا دخولًا فعليًا / المدعوين المثبتين؛ يشمل المراقب'),('نصاب فعلي مكتمل الآن',sum(bool(workflow.session.quorum(r) and workflow.session.quorum(r)['actual_quorum']) for r in records),'الحضور الحالي ضمن وقت الاجتماع؛ لا مؤشر تاريخي مفترض'),('المشاركة في التصويت',Participation.objects.filter(vote_id__in=[v.pk for r in records for v in workflow.visible_votes(request.user,r)]).count(),'إثباتات مشاركة، دون اختيارات الاقتراع السري'),('المحاضر النهائية',sum(workflow.minute_visible(request.user,m.revisions.get(version=m.current_version)) for m in Minutes.objects.filter(meeting__in=records,status='final')),'المحاضر الحالية نهائية ومختومة'),('قرارات نشطة',ds.exclude(status__in=['executed','cancelled']).count(),'سجل القرارات الموحد'),('قرارات متأخرة',ds.exclude(status__in=['executed','cancelled']).filter(due_date__lt=timezone.localdate()).count(),'تجاوز تاريخ الاستحقاق دون إنهاء'),('تجاوز SLA',sum((timezone.now()-e.revision.created_at).total_seconds()/3600>=e.sla_hours for e in ExecutionRecord.objects.filter(decision__in=ds).exclude(decision__status__in=['executed','cancelled'])),'من وقت إصدار مرجع المحضر وفق SLA المحدد'),('مهام متعثرة',tasks.exclude(status__in=['closed','cancelled']).filter(obstacles__resolved_at__isnull=True,obstacles__isnull=False).distinct().count(),'عائق قائم؛ التعثر مؤشر منفصل'),('إنجاز المهام %',round(sum(tasks.values_list('progress',flat=True))/tasks.count(),1) if tasks.exists() else 0,'متوسط إنجاز المهام المصرح بها فقط')]
    if request.GET.get('export')=='csv':
        output=io.StringIO();writer=csv.writer(output);writer.writerow(['المؤشر','القيمة','التعريف'])
        for item in items:writer.writerow(item)
        response=HttpResponse('\ufeff'+output.getvalue(),content_type='text/csv; charset=utf-8');response['Content-Disposition']='attachment; filename="meeting-report.csv"';return response
    return render(request,'meetinghub/reports.html',{'items':items,'committees':services.committees_for(request.user),'records':records,'generated_at':timezone.now(),'units':__import__('core.models',fromlist=['Unit']).Unit.objects.filter(pk__in=tasks_for(request.user).values('unit_id'))})
