import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.shortcuts import get_object_or_404,render,redirect
from django.http import JsonResponse,HttpResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db.models import Q
from .models import *
from . import services,workflow_services as ops,execution_services as execution,governance_services as gov,platform_services as platform
from .workflow_forms import *

def record_for(user,pk):return get_object_or_404(services.records_for(user,True),pk=pk)
def failure(exc):return '؛ '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc)
def checked(form):
    if not form.is_valid():raise ValidationError(form.errors.as_text())
    return form.cleaned_data.copy()
def current_revision(record):
    minutes=Minutes.objects.filter(meeting=record).first()
    return minutes.revisions.get(version=minutes.current_version) if minutes and minutes.current_version else None

def handle(user,record,action,data):
    if action not in ['cast','minute_sign','speaker_join','note_save','disclose','feedback']:
        services.require_manage(user,record)
    if action in ['decision_create','stage_save','execution_update']:
        from core.services import require_decision_manage
        require_decision_manage(user)
    if action=='vote_create':
        d=checked(VoteForm(data,record=record));row=ops.create_vote(user,record,**d);return {'vote':row.pk}
    if action in ['vote_open','vote_close','vote_cancel']:
        vote=get_object_or_404(record.votes,pk=data.get('vote'));d=checked(ReasonForm(data));ops.vote_transition(user,vote,action[5:],**d);return {'vote':vote.pk}
    if action=='cast':
        d=checked(CastForm(data,record=record,user=user));proof=ops.cast(user,d['vote'],d['choice'],d['eligibility'].pk if d.get('eligibility') else None);return {'participation':proof.pk,'message':'المشاركة محفوظة؛ لا يظهر الاختيار السري.'}
    if action=='minute_save':
        d=checked(MinutesForm(data,record=record));d['signatories']=[m.pk for m in d['signatories']];r=ops.save_minutes(user,record,**d);return {'revision':r.pk,'digest':r.digest}
    if action.startswith('minute_'):
        r=get_object_or_404(MinuteRevision,pk=data.get('revision'),minutes__meeting=record)
        if action=='minute_official':
            from .signature_provider import start_signature
            req=start_signature(user,r);return {'request':req.pk,'status':req.status}
        if action not in ['minute_review','minute_sign','minute_final']:raise ValidationError('إجراء محضر غير صالح.')
        d=checked(SignForm(data) if action=='minute_sign' else ReasonForm(data));result=ops.minute_transition(user,r,action[7:],**d);return {'status':result.status}
    if action=='timer':
        d=checked(LiveForm(data,record=record));d['action']=d.pop('command');state=ops.live_action(user,record,**d);return {'version':state.version,'state':state.state}
    if action.startswith('speaker_'):
        command=action[8:];row=ops.speaker_action(user,record,command,speaker_id=data.get('speaker'));return {'speaker':row.pk,'status':row.status}
    if action=='note_save':
        d=checked(NoteForm(data,record=record,user=user));note_id=d.pop('note');d['strokes']=d['strokes'] or [];d['instance']=get_object_or_404(Note,pk=note_id,meeting=record,owner=user) if note_id else None
        row=ops.save_note(user,record,**d);return {'note':row.pk,'version':row.version}
    if action=='note_to_minutes':
        note=get_object_or_404(ops.notes_for(user,record),meeting=record,pk=data.get('note'));r=current_revision(record)
        if not r:raise ValidationError('أنشئ مسودة محضر أولًا.')
        d=checked(ReasonForm(data));nr=ops.save_minutes(user,record,r.body+'\n\nملاحظة أضيفت صراحة:\n'+note.body,r.signatories,d['reason'],d['version'],correction=True);return {'revision':nr.pk}
    if action=='decision_create':
        r=current_revision(record)
        if not r:raise ValidationError('لا محضر.')
        d=checked(DecisionFromMinutesForm(data,record=record));d.pop('version');ext=execution.create_decision(user,r,**d);return {'decision':ext.decision_id,'url':f'/decisions/{ext.decision_id}/'}
    if action=='stage_save':
        d=checked(StageForm(data,record=record,user=user));ext=d.pop('execution');stage=d.pop('stage');d['instance']=get_object_or_404(ExecutionStage,pk=stage,execution=ext) if stage else None;row=execution.save_stage(user,ext,**d);return {'stage':row.pk}
    if action=='execution_update':
        ext=get_object_or_404(ExecutionRecord,pk=data.get('execution'),revision__minutes__meeting=record);d=checked(ReasonForm(data));execution.update_execution(user,ext,data.get('command'),**d);return {'execution':ext.pk}
    if action=='disclose':
        d=checked(DisclosureForm(data,record=record,user=user));row=gov.disclose(user,record,**d);return {'disclosure':row.pk}
    if action=='feedback_recommendation':
        f=get_object_or_404(Feedback,pk=data.get('feedback'),meeting=record)
        from core.models import User
        owner=get_object_or_404(User,pk=data.get('owner'),is_active=True)
        date=__import__('datetime').date.fromisoformat(data.get('due_date',''))
        row=gov.feedback_recommendation(user,f,owner,date,data.get('reason',''));return {'recommendation':row.pk}
    if action=='disclosure_approve':
        row=get_object_or_404(Disclosure,pk=data.get('disclosure'),meeting=record);gov.approve_disclosure(user,row,data.get('reason',''));return {'approved':True}
    if action=='feedback':
        form=forms.Form();form.fields={'rating':forms.IntegerField(min_value=1,max_value=5),'body':forms.CharField(max_length=5000),'anonymous':forms.BooleanField(required=False)}
        form.data=data;form.is_bound=True;d=checked(form);row=gov.feedback(user,record,**d);return {'feedback':row.pk}
    raise ValidationError('الإجراء غير معروف.')

def snapshot(user,record):
    votes=[]
    for v in ops.visible_votes(user,record):
        result=VoteResult.objects.filter(vote=v).first();row={'id':v.pk,'question':v.question,'secret':v.secret,'purpose':v.purpose,'status':v.status,'status_label':v.get_status_display(),'version':v.version,'closes_at':v.closes_at,'participation':v.participation.count()}
        if result:row['result']={'totals':result.totals,'outcome':result.outcome,'outcome_label':{'passed':'معتمد','failed':'غير معتمد','tie':'تعادل؛ لا تمرير تلقائي'}.get(result.outcome,result.outcome),'digest':result.digest}
        if not v.secret and v.status=='closed':row['public_ballots']=list(PublicBallot.objects.filter(participation__vote=v).values('participation__eligibility__name','choice','created_at'))
        votes.append(row)
    revision=current_revision(record);minute=None
    if revision and ops.minute_visible(user,revision):minute={'id':revision.pk,'version':revision.version,'status':revision.minutes.status,'body':revision.body,'snapshot':revision.snapshot,'digest':revision.digest,'signatures':list(revision.signatures.values('member_id','capacity','method','content_digest','created_at'))}
    notes=ops.notes_for(user,record)
    return {'votes':votes,'minute':minute,'notes':list(notes.values('id','owner_id','shared','body','strokes','page','version','item_id')),'speakers':list(record.speakers.values('id','member__user__first_name','member__user__last_name','status'))}

@login_required
@never_cache
def workspace(request,pk,page):
    record=record_for(request.user,pk);error='';bound=None
    if request.method=='POST':
        try:handle(request.user,record,request.POST.get('action',''),request.POST);messages.success(request,'حُفظ الإجراء في الخادم وسجل التدقيق.');return redirect(request.path)
        except (ValidationError,PermissionDenied,ValueError) as exc:error=failure(exc)
    manager=services.can_manage(request.user,record.committee);r=current_revision(record)
    if r and not ops.minute_visible(request.user,r):r=None
    notes=ops.notes_for(request.user,record).select_related('owner','item')
    from core.views import decisions_for
    exts=ExecutionRecord.objects.filter(decision__in=decisions_for(request.user),revision__minutes__meeting=record).select_related('decision','revision').prefetch_related('stages')
    current_member=services.active_members().filter(user=request.user).first()
    context={'record':record,'page':page,'may_participate':bool(current_member and current_member.role!='observer' and record.invitees.filter(pk=current_member.pk).exists()),'can_manage':manager,'can_execute':request.user.role in ['executive','assistant'],'error':error,'votes':snapshot(request.user,record)['votes'],'revision':r,'minute':r.minutes if r else None,'minute_versions':[rev for rev in MinuteRevision.objects.filter(minutes__meeting=record).order_by('-version') if ops.minute_visible(request.user,rev)],'may_sign':r and current_member and current_member.pk in r.signatories,'vote_form':VoteForm(record=record),'cast_form':CastForm(record=record,user=request.user),'minutes_form':MinutesForm(record=record),'sign_form':SignForm(initial={'version':r.minutes.version if r else 0}),'live_form':LiveForm(record=record),'note_form':NoteForm(record=record,user=request.user),'notes':notes,'speakers':record.speakers.select_related('member__user').order_by('pk'),'live_state':LiveState.objects.filter(meeting=record).first(),'decision_form':DecisionFromMinutesForm(record=record),'stage_form':StageForm(record=record,user=request.user),'executions':exts,'disclosure_form':DisclosureForm(record=record,user=request.user),'disclosures':record.disclosures.select_related('member__user') if manager else record.disclosures.filter(member__user=request.user),'feedback_rows':Feedback.objects.filter(meeting=record) if manager else [],'feedback_owners':__import__('core.models',fromlist=['User']).User.objects.filter(is_active=True).exclude(role__in=['admin','viewer']),'provider_ready':platform.provider_ready('parq','production')}
    if request.GET.get('stage','').isdigit():
        stage=get_object_or_404(ExecutionStage,pk=request.GET['stage'],execution__in=exts)
        context['stage_form']=StageForm(record=record,user=request.user,initial={'stage':stage.pk,'execution':stage.execution_id,'version':stage.execution.version,'title':stage.title,'due_date':stage.due_date,'progress':stage.progress,'evidence':stage.evidence_id,'link':stage.link})
    if request.GET.get('note','').isdigit():
        note=get_object_or_404(Note,pk=request.GET['note'],meeting=record,owner=request.user)
        context['note_form']=NoteForm(record=record,user=request.user,instance=note,initial={'note':note.pk,'version':note.version})
    if request.method=='POST' and error:
        mapping={'vote_create':('vote_form',VoteForm,{'record':record}),'cast':('cast_form',CastForm,{'record':record,'user':request.user}),'minute_save':('minutes_form',MinutesForm,{'record':record}),'note_save':('note_form',NoteForm,{'record':record,'user':request.user}),'decision_create':('decision_form',DecisionFromMinutesForm,{'record':record}),'disclose':('disclosure_form',DisclosureForm,{'record':record,'user':request.user})}
        if request.POST.get('action') in mapping:
            key,form,kwargs=mapping[request.POST['action']];context[key]=form(request.POST,**kwargs)
    return render(request,'meetinghub/workspace.html',context)

@login_required
@never_cache
def minute_print(request,revision_pk):
    revision=get_object_or_404(MinuteRevision.objects.select_related('minutes__meeting__meeting'),pk=revision_pk)
    if not ops.minute_visible(request.user,revision):raise PermissionDenied()
    if not platform.permit_download(revision.minutes.meeting):raise PermissionDenied('سياسة التصنيف تمنع تنزيل المحضر.')
    services.event(f'meeting:{revision.minutes.meeting_id}',request.user,'نسخة محضر للطباعة','اطلاع وتنزيل مصرح',{'revision':revision.pk,'digest':revision.digest})
    return render(request,'meetinghub/minute_print.html',{'revision':revision,'official':SignatureRequest.objects.filter(revision=revision,status='complete').first(),'sealed':hasattr(revision,'seal')})

@csrf_exempt
@require_POST
def signature_callback(request):
    from .signature_provider import callback
    try:r=callback(request.body,request.headers.get('X-Ehsan-Timestamp'),request.headers.get('X-Ehsan-Signature'));return JsonResponse({'status':r.status})
    except PermissionDenied:return JsonResponse({'error':'توقيع رد غير صالح'},status=403)
    except (ValidationError,ValueError):return JsonResponse({'error':'رد مزود غير صالح أو غير مطابق'},status=400)
