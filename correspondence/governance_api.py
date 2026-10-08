from datetime import timedelta
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied,ValidationError
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import *
from . import services,governance,reports

@api_view(['GET','POST'])
def privacy(request):
    user=request.user;allowed=user.role=='executive' or services.has_access(user,'compliance')
    if request.method=='GET':
        qs=SubjectRequest.objects.all() if allowed else SubjectRequest.objects.filter(requester=user)
        return Response(list(qs.values('id','uid','kind','state','description','due_at','response','created_at')))
    kind=request.data.get('kind');reason=services.reason_required(request.data.get('description',''))
    if kind not in ['access','correct','destroy','withdraw']:raise ValidationError('نوع غير صالح.')
    row=SubjectRequest.objects.create(requester=user,kind=kind,description=reason,due_at=timezone.now()+timedelta(days=30))
    return Response({'id':row.pk,'uid':str(row.uid),'due_at':row.due_at},status=201)

@api_view(['POST'])
def retention(request,pk):
    mail=get_object_or_404(services.visible(request.user),pk=pk);reason=request.data.get('reason','');action=request.data.get('action')
    if action=='propose':row=governance.propose_retention(request.user,mail,request.data.get('retention_action'),reason)
    else:
        row=get_object_or_404(RetentionCase,mail=mail,pk=request.data.get('case'))
        if action=='approve':row=governance.approve_retention(request.user,row,reason)
        elif action=='execute':row=governance.execute_retention(request.user,row,request.data.get('minutes',''),reason)
        else:raise ValidationError('إجراء غير صالح.')
    return Response({'id':row.pk,'uid':str(row.uid),'state':row.state})

@api_view(['GET'])
def report(request,kind):
    headers,rows=reports.report_rows(request.user,kind);return Response({'headers':headers,'rows':rows})

@api_view(['POST'])
def bulk_referral(request):
    from django.db import transaction
    from core.models import User
    ids=request.data.get('mail_ids');people=request.data.get('recipients')
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(x,int) for x in ids):raise ValidationError('دفعة غير صالحة.')
    if not isinstance(people,list) or not people or len(people)>100 or any(not isinstance(x,int) for x in people):raise ValidationError('مستلمون غير صالحين.')
    recipients=list(User.objects.filter(pk__in=set(people),is_active=True))
    if len(recipients)!=len(set(people)):raise ValidationError('مستلم غير معروف.')
    with transaction.atomic():
        rows=list(services.visible(request.user).filter(pk__in=set(ids)))
        if len(rows)!=len(set(ids)):raise PermissionDenied('دفعة خارج الصلاحية.')
        result=[]
        for mail in rows:
            result.extend(services.refer(request.user,mail,recipients,request.data.get('instruction'),request.data.get('note',''),mode=request.data.get('mode','parallel')))
    return Response({'processed':len(rows),'referrals':[x.pk for x in result]})
