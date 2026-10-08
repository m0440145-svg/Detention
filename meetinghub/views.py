from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from core.models import Meeting
from .models import *
from .forms import MemberForm,CommitteeForm,MeetingForm,ActionForm
from . import services

@login_required
def listing(request):
    if request.user.role=='admin':return redirect('meeting-members')
    archived=request.GET.get('archived')=='1'
    records=services.records_for(request.user,archived)
    if archived:records=records.filter(archived=True)
    if request.GET.get('q'):records=records.filter(meeting__name__icontains=request.GET['q'])
    if request.GET.get('committee','').isdigit():records=records.filter(committee_id=request.GET['committee'])
    if request.GET.get('status') in ['draft','scheduled','cancelled']:records=records.filter(status=request.GET['status'])
    from django.core.paginator import Paginator
    committees=services.committees_for(request.user)
    return render(request,'meetinghub/list.html',{'rows':Paginator(records,25).get_page(request.GET.get('page')),'committees':committees,'may_create':any(services.can_manage(request.user,c) for c in committees),'legacy':Meeting.objects.filter(governance__isnull=True) if request.user.role in ['executive','assistant'] else Meeting.objects.none(),'archived':archived})

@login_required
def compose(request,pk=None):
    record=get_object_or_404(services.records_for(request.user,True),pk=pk) if pk else None
    if record:services.require_manage(request.user,record)
    elif not any(services.can_manage(request.user,c) for c in services.committees_for(request.user)):raise PermissionDenied('لا توجد لجنة مخولة لك لإنشاء اجتماع.')
    form=MeetingForm(request.POST or None,user=request.user,instance=record)
    if request.method=='POST' and form.is_valid():
        try:
            values=form.cleaned_data.copy();reason=values.pop('reason');version=values.pop('version',None);people=values.pop('invitees');legacy=values.pop('legacy',None)
            row=services.save_meeting(request.user,values,people,record,reason,version,legacy)
            messages.success(request,'حُفظ الاجتماع والمدعوون ونسبة النصاب في الخادم.');return redirect('meeting-detail',pk=row.pk)
        except (ValidationError,PermissionDenied) as exc:form.add_error(None,exc)
    return render(request,'meetinghub/form.html',{'form':form,'title':'تعديل الاجتماع' if record else 'إنشاء اجتماع','hint':'نسبة النصاب ومرجعها إلزاميان. روابط الاجتماعات المدخلة يدويًا لا تعني وجود ربط مع Zoom أو Teams.'})

@login_required
def detail(request,pk):
    record=get_object_or_404(services.records_for(request.user,True),pk=pk)
    form=ActionForm(request.POST or None,initial={'version':record.version})
    error=''
    if request.method=='POST' and form.is_valid():
        try:
            services.meeting_action(request.user,record,**form.cleaned_data)
            messages.success(request,'تم توثيق الإجراء في سجل الاجتماع.');return redirect('meeting-detail',pk=record.pk)
        except (ValidationError,PermissionDenied) as exc:error='؛ '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc)
    services.event(f'meeting:{record.pk}',request.user,'اطلاع على اجتماع','فتح صفحة الاجتماع',{})
    from core.views import decisions_for
    decisions=decisions_for(request.user).filter(meeting=record.meeting)
    return render(request,'meetinghub/detail.html',{'record':record,'quorum':services.quorum(record),'can_manage':services.can_manage(request.user,record.committee),'form':form,'error':error,'decisions':decisions,'events':Event.objects.filter(stream__scope=f'meeting:{record.pk}').select_related('actor').order_by('-pk')[:50],'chain_valid':services.verify_chain(f'meeting:{record.pk}')})

@login_required
def members(request):
    services.require_admin(request.user)
    return render(request,'meetinghub/directory.html',{'title':'الأعضاء وأدوار الاجتماعات','kind':'members','rows':Member.objects.select_related('user').order_by('archived','pk')})

@login_required
def committees(request):
    return render(request,'meetinghub/directory.html',{'title':'المجالس واللجان','kind':'committees','rows':services.committees_for(request.user),'admin':services.administrative(request.user)})

@login_required
def directory_form(request,kind,pk=None):
    services.require_admin(request.user)
    model,klass=(Member,MemberForm) if kind=='members' else (Committee,CommitteeForm)
    instance=get_object_or_404(model,pk=pk) if pk else None
    form=klass(request.POST or None,instance=instance)
    error=''
    if request.method=='POST':
        try:
            if request.POST.get('action') in ['archive','restore']:
                if not instance:raise ValidationError('السجل غير موجود.')
                handler=services.archive_member if kind=='members' else services.archive_committee
                handler(request.user,instance,request.POST['action']=='archive',request.POST.get('reason',''))
                return redirect('meeting-'+kind)
            if form.is_valid():
                values=form.cleaned_data.copy();reason=values.pop('reason')
                if kind=='members':
                    national_id=values.pop('national_id','');services.save_member(request.user,values,instance,reason,national_id)
                else:
                    people=values.pop('members');version=values.pop('version',None);services.save_committee(request.user,values,people,instance,reason,version)
                messages.success(request,'حُفظ السجل والتغيير في سجل التدقيق.');return redirect('meeting-'+kind)
        except (ValidationError,PermissionDenied) as exc:form.add_error(None,exc)
    scope=('member' if kind=='members' else 'committee')+':'+str(pk)
    return render(request,'meetinghub/form.html',{'form':form,'title':('تعديل ' if pk else 'إضافة ')+('عضو' if kind=='members' else 'لجنة'),'instance':instance,'events':Event.objects.filter(stream__scope=scope).select_related('actor').order_by('-pk')[:30] if pk else []})

@login_required
def phases(request):return render(request,'meetinghub/phases.html')
