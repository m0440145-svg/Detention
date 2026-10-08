import hashlib
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.http import FileResponse,Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .models import *
from .forms import AgendaForm,AttendanceForm,ProxyForm
from . import services,session_services as ops


def error(exc):return '؛ '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc)
def record_for(request,pk):return get_object_or_404(services.records_for(request.user),pk=pk)

@login_required
def agenda(request,pk):
    record=record_for(request,pk);failure=''
    if request.method=='POST':
        try:
            action=request.POST.get('action');version=int(request.POST.get('version','0'));reason=request.POST.get('reason','')
            if action=='publish':ops.publish_agenda(request.user,record,reason,version)
            elif action=='order':
                ids=[int(pk) for pk in request.POST.get('order','').split(',') if pk.strip()];ops.reorder(request.user,record,ids,reason,version)
            elif action=='remove':ops.remove_item(request.user,record,get_object_or_404(record.agenda_items,pk=request.POST.get('item')),reason,version)
            elif action=='upload':
                upload=request.FILES.get('file')
                if not upload:raise ValidationError('اختر مرفقًا.')
                item=get_object_or_404(record.agenda_items,pk=request.POST['item']) if request.POST.get('item') else None
                ops.upload_file(request.user,record,upload,reason,version,item)
            elif action=='read':ops.acknowledge(request.user,record,get_object_or_404(record.agenda_revisions,version=request.POST.get('revision')))
            else:raise ValidationError('إجراء غير صالح.')
            messages.success(request,'حُفظ الإجراء والإصدار في سجل الاجتماع.');return redirect('meeting-agenda',pk=pk)
        except (ValidationError,PermissionDenied,ValueError) as exc:failure=error(exc)
    record.refresh_from_db();manager=services.can_manage(request.user,record.committee)
    revision=record.agenda_revisions.filter(version=request.GET.get('revision',record.agenda_version)).first() if str(request.GET.get('revision',record.agenda_version)).isdigit() else None
    rows=list(ops.draft_items(request.user,record)) if manager else []
    payload=ops.revision_items(request.user,record,revision) if revision else []
    total=sum(item['duration'] for item in ops.agenda_payload(record)) if manager else sum(item['duration'] for item in payload)
    window=int((record.ends_at-record.starts_at).total_seconds()/60)
    return render(request,'meetinghub/agenda.html',{'record':record,'rows':rows,'payload':payload,'revision':revision,'revisions':record.agenda_revisions.order_by('-version'),'can_manage':manager,'error':failure,'total_minutes':total,'overflow':total>window,'window_minutes':window,'files':[f for f in record.files.filter(purpose='agenda',item__isnull=True) if ops.file_allowed(request.user,f)],'already_read':revision and revision.acknowledgments.filter(member__user=request.user).exists(),'may_ack':record.invitees.filter(user=request.user).exists(),'revision_current':revision and revision.version==record.agenda_version})

@login_required
def agenda_form(request,pk,item_pk=None):
    record=record_for(request,pk);services.require_manage(request.user,record)
    item=get_object_or_404(record.agenda_items,pk=item_pk,removed=False) if item_pk else None
    form=AgendaForm(request.POST or None,instance=item,record=record,user=request.user)
    if request.method=='POST' and form.is_valid():
        try:
            data=form.cleaned_data.copy();reason=data.pop('reason');version=data.pop('version');ops.save_item(request.user,record,data,reason,version,item)
            return redirect('meeting-agenda',pk=pk)
        except (ValidationError,PermissionDenied) as exc:form.add_error(None,exc)
    return render(request,'meetinghub/form.html',{'form':form,'title':'تعديل بند' if item else 'إضافة بند','hint':'كل تعديل لجدول منشور يصدر نسخة جديدة بمبرر، ويحفظ السابقة.'})

@login_required
@never_cache
def attendance(request,pk):
    record=record_for(request,pk);failure='';links=[];manager=services.can_manage(request.user,record.committee)
    if request.method=='POST':
        try:
            action=request.POST.get('action');reason=request.POST.get('reason','')
            if action=='issue':links=ops.issue_invitations(request.user,record,reason,int(request.POST.get('version','0')))
            elif action=='reset':ops.reset_invitations(request.user,record,reason,int(request.POST.get('version','0')))
            elif action=='revoke':ops.revoke_invitation(request.user,record,get_object_or_404(Invitation,pk=request.POST.get('invitation')),reason)
            elif action=='mark':
                form=AttendanceForm(request.POST,record=record)
                if not form.is_valid():raise ValidationError(str(form.errors))
                ops.mark_attendance(request.user,record,**form.cleaned_data)
            elif action=='respond':
                eligibility=get_object_or_404(Eligibility,roster__meeting=record,roster__generation=record.roster_generation,member__user=request.user,invited=True)
                ops.respond(eligibility.invitation,request.POST.get('answer'),request.user)
            elif action=='proxy':
                form=ProxyForm(request.POST,record=record)
                if not form.is_valid():raise ValidationError(str(form.errors))
                ops.approve_proxy(request.user,record,**form.cleaned_data)
            elif action=='revoke-proxy':ops.revoke_proxy(request.user,record,get_object_or_404(Proxy,pk=request.POST.get('proxy')),reason)
            elif action=='proxy-file':
                if not request.FILES.get('file'):raise ValidationError('اختر وثيقة توكيل.')
                ops.upload_file(request.user,record,request.FILES['file'],reason,int(request.POST.get('version','0')),purpose='proxy')
            else:raise ValidationError('إجراء غير صالح.')
            if not links:
                messages.success(request,'سُجل الإجراء في الخادم.');return redirect('meeting-attendance',pk=pk)
        except (ValidationError,PermissionDenied,ValueError) as exc:failure=error(exc)
    record.refresh_from_db();roster=ops.live_roster(record)
    people=list(roster.people.filter(invited=True).select_related('member__user','invitation','attendance')) if roster else []
    if not manager:people=[p for p in people if p.member.user_id==request.user.pk]
    for link in links:link['url']=request.build_absolute_uri('/meetings/rsvp/')+'#'+link.pop('token')
    return render(request,'meetinghub/attendance.html',{'record':record,'can_manage':manager,'error':failure,'links':links,'people':people,'roster':roster,'quorum':services.quorum(record),'attendance_form':AttendanceForm(record=record),'proxy_form':ProxyForm(record=record),'proxies':roster.proxies.select_related('principal','delegate','document') if roster and manager else [],'reminders':Reminder.objects.filter(invitation__eligibility__roster=roster).select_related('invitation__eligibility') if roster and manager else []})

@login_required
@never_cache
def download(request,pk):
    file=get_object_or_404(MeetingFile.objects.select_related('meeting__committee','item'),pk=pk)
    if not ops.file_allowed(request.user,file):raise Http404()
    services.event(f'meeting:{file.meeting_id}',request.user,'تنزيل مرفق اجتماع','تنزيل مصرح',{'file':file.pk,'sha256':file.sha256})
    try:response=FileResponse(file.file.open('rb'),as_attachment=True,filename=file.name)
    except FileNotFoundError:raise Http404()
    response['Cache-Control']='no-store';return response

@never_cache
@require_http_methods(['GET','POST'])
def rsvp(request):
    failure='';invitation=None;token='';answer=''
    if request.method=='POST':
        token=request.POST.get('token','')
        keys=[(hashlib.sha256(('ip:'+request.META.get('REMOTE_ADDR','')).encode()).hexdigest(),300),(hashlib.sha256(('token:'+token[:100]).encode()).hexdigest(),30)]
        if any(RSVPAttempt.objects.filter(key=key,created_at__gte=timezone.now()-timedelta(minutes=15)).count()>=limit for key,limit in keys):
            response=render(request,'meetinghub/rsvp.html',{'error':'محاولات كثيرة؛ حاول لاحقًا.'},status=429)
            response['Referrer-Policy']='no-referrer';response['X-Robots-Tag']='noindex, nofollow';return response
        RSVPAttempt.objects.bulk_create([RSVPAttempt(key=key) for key,limit in keys])
        try:
            invitation=ops.find_invitation(token)
            if request.POST.get('answer'):
                invitation=ops.respond(invitation,request.POST['answer']);answer='تم تسجيل الرد. تأكيدك المسبق لا يثبت الحضور الفعلي.'
        except ValidationError as exc:failure=error(exc);token=''
    response=render(request,'meetinghub/rsvp.html',{'invitation':invitation,'record':invitation.eligibility.roster.meeting if invitation else None,'token':token,'error':failure,'success':answer})
    response['Referrer-Policy']='no-referrer';response['X-Robots-Tag']='noindex, nofollow';response['Cache-Control']='no-store'
    return response
