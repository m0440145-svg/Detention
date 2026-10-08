from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied
from rest_framework import serializers,viewsets
from rest_framework.decorators import action,api_view
from rest_framework.response import Response
from core.models import User
from .models import *
from . import services

class MailSerializer(serializers.ModelSerializer):
    code=serializers.CharField(read_only=True)
    hijri_date=serializers.CharField(read_only=True)
    status_label=serializers.CharField(source='get_status_display',read_only=True)
    reason=serializers.CharField(write_only=True,required=False)
    class Meta:
        model=Correspondence
        fields=['id','uid','code','reference','kind','subject','body','rich_text','summary','keywords','project_reference','program_reference','personal_data','external_number','original_date','party','classification','template','unit','owner','authorized','cc','secrecy','priority','status','status_label','channel','source_platform','no_attachments','signer','route','approval_roles','approval_index','related','decision','created_at','hijri_date','due_at','referral_due_at','external_deadline','sla_hours','paused_at','signed_at','signature_method','reason']
        read_only_fields=['uid','reference','status','approval_roles','approval_index','created_at','due_at','referral_due_at','sla_hours','paused_at','signed_at','signature_method']
    def create(self,data):
        data.pop('reason',''); authorized=data.pop('authorized',[]);cc=data.pop('cc',[])
        return services.create_mail(self.context['request'].user,data,authorized,cc,ip=self.context['request'].META.get('REMOTE_ADDR',''))
    def update(self,instance,data):
        reason=data.pop('reason','')
        if 'authorized' in data or 'cc' in data: raise serializers.ValidationError('قوائم الوصول لا تتغير عبر التحرير العام.')
        return services.edit_mail(self.context['request'].user,instance,data,reason,ip=self.context['request'].META.get('REMOTE_ADDR',''))

class ActionSerializer(serializers.Serializer):
    action=serializers.CharField()
    reason=serializers.CharField(allow_blank=False)
    evidence=serializers.CharField(required=False,allow_blank=True,default='')

class ReferralSerializer(serializers.ModelSerializer):
    class Meta:
        model=Referral
        fields=['id','recipient','instruction','note','completed_at','task','mode','position']
        read_only_fields=['id','completed_at','task','position']

class MailViewSet(viewsets.ModelViewSet):
    serializer_class=MailSerializer
    http_method_names=['get','post','patch','head','options']
    def get_queryset(self):
        qs=services.visible(self.request.user)
        for field in ['kind','status','priority','secrecy','party','classification','project_reference','program_reference']:
            if self.request.query_params.get(field): qs=qs.filter(**{field:self.request.query_params[field]})
        if self.request.query_params.get('q'):
            from django.db.models import Q
            q=self.request.query_params['q']; qs=qs.filter(Q(subject__icontains=q)|Q(body__icontains=q)|Q(reference__icontains=q)|Q(external_number__icontains=q)|Q(search_text__contains=services.normalize_arabic(q)))
        return qs
    def retrieve(self,request,*args,**kwargs):
        mail=self.get_object(); services.event(mail,request.user,'اطلاع API','قراءة المراسلة عبر API',ip=request.META.get('REMOTE_ADDR',''))
        return Response(self.get_serializer(mail).data)
    @action(detail=False,methods=['get'])
    def metrics(self,request):
        from .reports import metrics
        return Response(metrics(request.user))
    @action(detail=True,methods=['post'])
    def signature(self,request,pk=None):
        from .governance import request_signature
        row=request_signature(request.user,self.get_object(),request.data.get('reason',''))
        return Response({'request':str(row.uid),'state':row.state},status=202)
    @action(detail=True,methods=['get'])
    def revisions(self,request,pk=None):
        from .governance import revision_diff
        return Response(revision_diff(self.get_object()))
    @action(detail=True,methods=['post'])
    def transition(self,request,pk=None):
        data=ActionSerializer(data=request.data);data.is_valid(raise_exception=True)
        mail=services.transition(request.user,self.get_object(),**data.validated_data,ip=request.META.get('REMOTE_ADDR',''))
        return Response(self.get_serializer(mail).data)
    @action(detail=True,methods=['get','post'])
    def attachments(self,request,pk=None):
        mail=self.get_object()
        if request.method=='GET': return Response([{'id':a.pk,'name':a.name,'size':a.size,'sha256':a.sha256,'url':f'/communications/files/{a.pk}/','download_allowed':mail.secrecy not in ['secret','restricted']} for a in mail.attachments.all()])
        if not request.FILES.get('file'): raise serializers.ValidationError('اختر ملفًا.')
        a=services.attach(request.user,mail,request.FILES['file'],request.data.get('reason',''),request.META.get('REMOTE_ADDR',''))
        return Response({'id':a.pk,'sha256':a.sha256},status=201)
    @action(detail=True,methods=['get','post'])
    def referrals(self,request,pk=None):
        mail=self.get_object()
        if request.method=='GET':
            rows=[]
            from core.policy import tasks_for
            for row in mail.referrals.all():
                data=ReferralSerializer(row).data
                if row.task_id and tasks_for(request.user).filter(pk=row.task_id).exists():
                    data['task_tracking']={'url':f'/tasks/{row.task_id}/','status':row.task.status,'status_label':row.task.get_status_display(),'progress':row.task.progress,'due_date':str(row.task.due_date),'deadline_label':row.task.deadline_label,'is_overdue':row.task.is_overdue}
                else:
                    data['task']=None
                rows.append(data)
            return Response(rows)
        s=ReferralSerializer(data=request.data);s.is_valid(raise_exception=True)
        d=s.validated_data; rows=services.refer(request.user,mail,[d['recipient']],d['instruction'],d.get('note',''),request.META.get('REMOTE_ADDR',''),mode=d.get('mode','parallel'))
        return Response(ReferralSerializer(rows,many=True).data,status=201)
    @action(detail=True,methods=['post'],url_path='complete-referral')
    def complete_referral(self,request,pk=None):
        row=get_object_or_404(self.get_object().referrals,pk=request.data.get('referral'))
        services.complete_referral(request.user,row,request.data.get('reason',''),request.META.get('REMOTE_ADDR',''))
        return Response(ReferralSerializer(row).data)
    @action(detail=True,methods=['post'],url_path='convert-to-task')
    def convert_to_task(self,request,pk=None):
        mail=self.get_object()
        due=serializers.DateField().run_validation(request.data.get('due_date'))
        if request.data.get('referral'):
            row=get_object_or_404(mail.referrals,pk=request.data.get('referral'))
            task=services.convert_to_task(request.user,row,{'due_date':due},request.META.get('REMOTE_ADDR',''))
        else:
            owner_id=serializers.IntegerField(min_value=1).run_validation(request.data.get('owner'))
            owner=get_object_or_404(User,pk=owner_id)
            reason=serializers.CharField().run_validation(request.data.get('reason'))
            task=services.incoming_to_task(request.user,mail,owner,due,reason,request.META.get('REMOTE_ADDR',''))
        return Response({'task':task.pk,'url':f'/tasks/{task.pk}/'},status=201)
    @action(detail=True,methods=['post'],url_path='extend-due')
    def extend_due(self,request,pk=None):
        due=serializers.DateTimeField().run_validation(request.data.get('due_at'))
        mail=services.extend_due(request.user,self.get_object(),due,request.data.get('reason',''),request.META.get('REMOTE_ADDR',''))
        return Response(self.get_serializer(mail).data)
    @action(detail=True,methods=['post'])
    def extract_text(self,request,pk=None):
        from .ocr import request_extraction
        attachment=get_object_or_404(self.get_object().attachments,pk=request.data.get('attachment'))
        job=request_extraction(request.user,attachment,request.META.get('REMOTE_ADDR',''))
        return Response({'job':job.pk,'state':job.state},status=202)
    @action(detail=True,methods=['get'])
    def extraction(self,request,pk=None):
        return Response(list(OCRJob.objects.filter(attachment__mail=self.get_object()).values('id','attachment_id','state','text','confidence','page_confidence','proposals','error')))
    @action(detail=True,methods=['get'])
    def audit(self,request,pk=None):
        mail=self.get_object()
        return Response({'chain_valid':services.verify_chain(mail),'events':list(mail.events.values('id','actor_id','action','reason','ip','old','new','previous_hash','digest','created_at'))})

@api_view(['GET'])
def master_data(request):
    if request.user.role== 'admin': raise PermissionDenied('الدليل التشغيلي للأدوار المخولة بالمراسلات.')
    return Response({'parties':list(Party.objects.filter(active=True).values('id','name','supervisory')),'classifications':list(Classification.objects.values('id','name','parent_id','route_unit_id','retention_id')),'routes':list(ApprovalRoute.objects.filter(active=True).values('id','name','kind','levels','authority_reference')),'templates':list(MailTemplate.objects.filter(active=True).values('id','name','version','kind'))})


@api_view(['POST'])
def bulk(request):
    ids=serializers.ListField(child=serializers.IntegerField(min_value=1),min_length=1,max_length=100).run_validation(request.data.get('mail_ids'))
    return Response(services.bulk_action(request.user,ids,request.data.get('action'),request.data.get('reason',''),request.META.get('REMOTE_ADDR','')))
