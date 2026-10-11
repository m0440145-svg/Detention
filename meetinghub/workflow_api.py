from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied,ValidationError
from .models import *
from .workflow_views import handle,record_for,snapshot
from . import governance_services as gov,election_services as elections,platform_services as platform,services
from .governance_views import entry_save

@api_view(['GET','POST'])
def meeting_workflow(request,pk):
    record=record_for(request.user,pk)
    response=Response(snapshot(request.user,record) if request.method=='GET' else handle(request.user,record,request.data.get('action',''),request.data));response['Cache-Control']='no-store';return response

@api_view(['GET','POST'])
def governance(request,kind):
    if kind not in dict(GovernanceEntry.KINDS):raise ValidationError('نوع غير صالح.')
    if request.method=='GET':return Response(list(gov.entries_for(request.user).filter(kind=kind).values('id','committee_id','meeting_id','title','status','due_date','data','version','secret','document_id')))
    action=request.data.get('action')
    if action=='save':
        row=get_object_or_404(gov.entries_for(request.user),pk=request.data['entry'],kind=kind) if request.data.get('entry') else None
        saved=entry_save(request.user,kind,request.data,row);return Response({'id':saved.pk,'version':saved.version})
    row=get_object_or_404(gov.entries_for(request.user,True),pk=request.data.get('entry'),kind=kind);gov.entry_transition(request.user,row,action,request.data.get('reason',''),request.data.get('version'));return Response({'id':row.pk,'saved':True})

@api_view(['GET','POST'])
def election(request,pk):
    row=get_object_or_404(Election,pk=pk,committee__in=services.committees_for(request.user));gov.require_committee(request.user,row.committee)
    if request.method=='GET':
        result=ElectionResult.objects.filter(election=row).first();return Response({'id':row.pk,'title':row.title,'status':row.status,'version':row.version,'participation':row.participation.count(),'candidates':list(row.candidates.filter(approved=True).values('member_id','statement')),'result':{'totals':result.totals,'winners':result.winners,'reserves':result.reserves,'tied':result.tied,'digest':result.digest} if result else None})
    d=request.data;action=d.get('action')
    if action=='cast':proof=elections.cast_election(request.user,row,d.get('choices'),get_object_or_404(Member,pk=d['represented']) if d.get('represented') else None);return Response({'participation':proof.pk})
    if action=='freeze':elections.freeze_election(request.user,row,d.get('reason',''),d.get('version'));return Response({'frozen':True})
    if action=='close':elections.close_election(request.user,row,d.get('reason',''),d.get('version'));return Response({'closed':True})
    raise ValidationError('إجراء غير صالح.')

@api_view(['GET','POST'])
def features(request):
    if request.method=='GET':return Response(list(platform.features().values('key','name','state','requirements','tested_at','environment','message')))
    row=platform.request_activation(request.user,request.data.get('feature'),request.data.get('reason',''));return Response({'request':row.pk,'status':row.status},status=201)
