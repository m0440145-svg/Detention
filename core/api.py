from functools import wraps
from django.core.exceptions import ValidationError as DjangoValidationError,PermissionDenied as DjangoPermissionDenied
from django.shortcuts import get_object_or_404
from rest_framework import serializers,viewsets,status
from rest_framework.decorators import action,api_view
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError as APIValidationError, PermissionDenied as APIPermissionDenied
from rest_framework.views import exception_handler
from .models import *
from .policy import *
from . import services
from .views import filter_tasks, kpis,decisions_for,decision_metrics,decision_activities

def exceptions(exc,context):
    if isinstance(exc,DjangoValidationError): exc=APIValidationError(exc.messages)
    if isinstance(exc,DjangoPermissionDenied): exc=APIPermissionDenied(str(exc))
    return exception_handler(exc,context)
class TaskSerializer(serializers.ModelSerializer):
    code=serializers.CharField(read_only=True)
    owner_name=serializers.CharField(source='owner.get_full_name',read_only=True)
    unit_name=serializers.CharField(source='unit.name',read_only=True)
    status_label=serializers.CharField(source='get_status_display',read_only=True)
    deadline_label=serializers.CharField(read_only=True)
    is_overdue=serializers.BooleanField(read_only=True)
    is_blocked=serializers.BooleanField(read_only=True)
    flags=serializers.SerializerMethodField()
    reason=serializers.CharField(write_only=True,required=False)
    class Meta:
        model=Task
        fields=['id','number','code','title','description','source','task_type','project','unit','unit_name','owner','owner_name','assignment_mode','participants','participating_units','start_date','due_date','priority','confidentiality','status','status_label','progress','auto_progress','expected_result','success_indicator','notes','decision','created_at','updated_at','submitted_at','closed_at','flags','deadline_label','is_overdue','is_blocked','reason']
        read_only_fields=['number','status','progress','created_at','updated_at','submitted_at','closed_at']
    def get_flags(self,obj): return services.flags(obj)
    def validate(self,attrs):
        user=self.context['request'].user
        if attrs.get('decision') and not decisions_for(user).filter(pk=attrs['decision'].pk).exists(): raise APIValidationError('القرار خارج صلاحياتك.')
        return attrs
    def create(self,data):
        participants=data.pop('participants',[]); units=data.pop('participating_units',[]); data.pop('reason',None)
        return services.create_task(self.context['request'].user,data,participants,units)
    def update(self,instance,data):
        reason=data.pop('reason',''); participants=data.pop('participants',list(instance.participants.all())); units=data.pop('participating_units',list(instance.participating_units.all()))
        return services.edit_task(self.context['request'].user,instance,data,participants,units,reason)
class ProgressSerializer(serializers.Serializer):
    progress=serializers.IntegerField(min_value=0,max_value=100)
    comment=serializers.CharField()
    accomplished=serializers.CharField()
    remaining=serializers.CharField()
class CommentSerializer(serializers.ModelSerializer):
    class Meta:
        model=Comment
        fields=['id','body','parent','mentions','mentioned_units','user','created_at']
        read_only_fields=['id','user','created_at']
class ObstacleSerializer(serializers.ModelSerializer):
    class Meta:
        model=Obstacle
        fields=['id','kind','description','reason','caused_by','needs_decision','requested_action','intervention_owner','expected_resolution','impact','reported_by','created_at','resolved_at','resolution']
        read_only_fields=['id','reported_by','created_at','resolved_at','resolution']
        extra_kwargs={'reason':{'required':True,'allow_blank':False}}
class SubtaskSerializer(serializers.ModelSerializer):
    class Meta:
        model=Subtask
        fields=['id','title','owner','due_date','progress','comment']
class TaskViewSet(viewsets.ModelViewSet):
    serializer_class=TaskSerializer
    http_method_names=['get','post','put','patch','head','options']
    def get_queryset(self): return filter_tasks(self.request,tasks_for(self.request.user))
    @action(detail=True,methods=['post'])
    def progress(self,request,pk=None):
        task=self.get_object()
        s=ProgressSerializer(data=request.data); s.is_valid(raise_exception=True)
        task=services.update_progress(request.user,task,**s.validated_data)
        return Response(self.get_serializer(task).data)
    @action(detail=True,methods=['post'],url_path='transition')
    def transition(self,request,pk=None):
        task=services.change_status(request.user,self.get_object(),request.data.get('status'),request.data.get('comment',''))
        return Response(self.get_serializer(task).data)
    @action(detail=True,methods=['get','post'])
    def comments(self,request,pk=None):
        task=self.get_object()
        if request.method=='GET': return Response(CommentSerializer(task.comments.all(),many=True).data)
        s=CommentSerializer(data=request.data); s.is_valid(raise_exception=True)
        comment=services.add_comment(request.user,task,**s.validated_data)
        return Response(CommentSerializer(comment).data,status=201)
    @action(detail=True,methods=['get','post'])
    def obstacles(self,request,pk=None):
        task=self.get_object()
        if request.method=='GET': return Response(ObstacleSerializer(task.obstacles.all(),many=True).data)
        s=ObstacleSerializer(data=request.data); s.is_valid(raise_exception=True)
        obstacle=services.report_obstacle(request.user,task,s.validated_data)
        return Response(ObstacleSerializer(obstacle).data,status=201)
    @action(detail=True,methods=['post'],url_path='resolve-obstacle')
    def resolve_obstacle(self,request,pk=None):
        obstacle=get_object_or_404(self.get_object().obstacles,pk=request.data.get('obstacle'))
        services.resolve_obstacle(request.user,obstacle,request.data.get('resolution',''))
        return Response(ObstacleSerializer(obstacle).data)
    @action(detail=True,methods=['get','post'])
    def subtasks(self,request,pk=None):
        task=self.get_object()
        if request.method=='GET': return Response(SubtaskSerializer(task.subtasks.all(),many=True).data)
        instance=get_object_or_404(task.subtasks,pk=request.data['id']) if request.data.get('id') else None
        s=SubtaskSerializer(data=request.data); s.is_valid(raise_exception=True)
        sub=services.save_subtask(request.user,task,s.validated_data,instance)
        return Response(SubtaskSerializer(sub).data)
    @action(detail=True,methods=['get','post'])
    def attachments(self,request,pk=None):
        task=self.get_object()
        if request.method=='GET': return Response([{'id':a.pk,'name':a.original_name,'url':f'/files/{a.pk}/'} for a in task.attachments.all()])
        if not request.FILES.get('file'): raise APIValidationError('اختر ملفًا.')
        subtask=get_object_or_404(task.subtasks,pk=request.data['subtask']) if request.data.get('subtask') else None
        a=services.attach(request.user,task,request.FILES['file'],subtask=subtask)
        return Response({'id':a.pk,'name':a.original_name,'url':f'/files/{a.pk}/'},status=201)
    @action(detail=True,methods=['get'])
    def audit(self,request,pk=None):
        task=self.get_object()
        return Response(list(task.activities.values('id','actor_id','action','old','new','created_at')))
    @action(detail=True,methods=['post'])
    def escalate(self,request,pk=None):
        services.escalate(request.user,self.get_object(),request.data.get('reason',''))
        return Response({'message':'تم التصعيد'})
class UnitSerializer(serializers.ModelSerializer):
    class Meta: model=Unit; fields=['id','name','description','head','active','created_at']; read_only_fields=['created_at']
class UnitViewSet(viewsets.ModelViewSet):
    serializer_class=UnitSerializer
    http_method_names=['get','post','patch','put','head','options']
    def get_queryset(self):
        return Unit.objects.all() if self.request.user.role in DIRECTORY_ROLES else self.request.user.units.all()
    def perform_create(self,serializer): self.write(serializer)
    def perform_update(self,serializer): self.write(serializer)
    def write(self,serializer):
        if self.request.user.role!=Role.ADMIN: raise APIPermissionDenied()
        unit=serializer.save()
        if unit.head: unit.head.units.add(unit)
        services.audit(None,self.request.user,'حفظ وحدة',new={'id':unit.pk})
class DecisionSerializer(serializers.ModelSerializer):
    progress=serializers.SerializerMethodField()
    approved_progress=serializers.SerializerMethodField()
    metric_scope=serializers.SerializerMethodField()
    status_label=serializers.CharField(source='get_status_display',read_only=True)
    issuing_authority_label=serializers.CharField(source='get_issuing_authority_display',read_only=True)
    minutes_file=serializers.FileField(write_only=True,required=False)
    reason=serializers.CharField(write_only=True,required=False)
    class Meta:
        model=Decision
        fields=['id','number','meeting','text','date','followup_owner','issuing_authority','issuing_authority_label','approved_minutes_number','approved_minutes_attachment','minutes_file','due_date','status','status_label','progress','approved_progress','metric_scope','reason']
        read_only_fields=['approved_minutes_attachment']
    def get_progress(self,obj): return decision_metrics(self.context['request'].user,obj)['progress']
    def get_approved_progress(self,obj): return decision_metrics(self.context['request'].user,obj)['approved_progress']
    def get_metric_scope(self,obj): return 'all' if self.context['request'].user.role in DECISION_READ_ROLES else 'visible'
    def create(self,data):
        reason=data.pop('reason',''); upload=data.pop('minutes_file',None)
        return services.save_decision(self.context['request'].user,data,reason=reason,minutes_file=upload)
    def update(self,instance,data):
        reason=data.pop('reason',''); upload=data.pop('minutes_file',None)
        return services.save_decision(self.context['request'].user,data,instance,reason,upload)
class DecisionViewSet(viewsets.ModelViewSet):
    serializer_class=DecisionSerializer
    http_method_names=['get','post','patch','put','head','options']
    def get_queryset(self): return decisions_for(self.request.user)
    def perform_create(self,serializer): self.write(serializer)
    def perform_update(self,serializer): self.write(serializer)
    def write(self,serializer):
        if self.request.user.role not in GLOBAL_ROLES: raise APIPermissionDenied()
        serializer.save()
    @action(detail=True,methods=['get'])
    def audit(self,request,pk=None):
        decision=self.get_object()
        return Response(list(decision_activities(request.user,decision).values('id','actor_id','action','old','new','created_at')))
    @action(detail=True,methods=['post'])
    def attachments(self,request,pk=None):
        decision=self.get_object()
        if not request.FILES.get('file'): raise APIValidationError('اختر ملفًا.')
        item=services.attach_decision(request.user,decision,request.FILES['file'],request.data.get('reason',''),approved_minutes=str(request.data.get('approved_minutes','')).lower() in ['true','1','on'])
        return Response({'id':item.pk,'name':item.original_name,'url':f'/files/{item.pk}/'},status=201)
    @action(detail=True,methods=['post'],url_path='convert-to-task')
    def convert(self,request,pk=None):
        d=self.get_object(); data=request.data.copy(); data.update({'decision':d.pk,'source':Source.DECISION,'description':d.text})
        data.setdefault('title',d.text[:250]); data.setdefault('start_date',str(d.date)); data.setdefault('expected_result','تنفيذ القرار '+d.number); data.setdefault('success_indicator','اعتماد تنفيذ القرار')
        s=TaskSerializer(data=data,context={'request':request}); s.is_valid(raise_exception=True); task=s.save()
        return Response(TaskSerializer(task,context={'request':request}).data,status=201)
class MeetingSerializer(serializers.ModelSerializer):
    class Meta: model=Meeting; fields=['id','number','name','committee','date']
class MeetingViewSet(viewsets.ModelViewSet):
    serializer_class=MeetingSerializer
    http_method_names=['get','post','patch','put','head','options']
    def get_queryset(self):
        return Meeting.objects.all() if self.request.user.role in GLOBAL_ROLES else Meeting.objects.filter(decisions__in=decisions_for(self.request.user)).distinct()
    def perform_create(self,serializer): self.write(serializer)
    def perform_update(self,serializer): self.write(serializer)
    def write(self,serializer):
        if self.request.user.role not in GLOBAL_ROLES: raise APIPermissionDenied()
        meeting=serializer.save(); services.audit(None,self.request.user,'حفظ اجتماع',new={'id':meeting.pk})
class NotificationSerializer(serializers.ModelSerializer):
    class Meta: model=Notification; fields=['id','task','text','read','created_at']; read_only_fields=['id','task','text','created_at']
class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class=NotificationSerializer
    http_method_names=['get','patch','head','options']
    def get_queryset(self):
        require_operational(self.request.user)
        return self.request.user.notifications.all()
@api_view(['GET'])
def metrics(request):
    require_operational(request.user)
    return Response(kpis(filter_tasks(request,tasks_for(request.user))))


class BulkFollowupSerializer(serializers.Serializer):
    task_ids=serializers.ListField(child=serializers.IntegerField(min_value=1),min_length=1,max_length=100)
    action=serializers.ChoiceField(choices=['remind','escalate'])
    reason=serializers.CharField(allow_blank=False)
@api_view(['POST'])
def bulk_followup(request):
    serializer=BulkFollowupSerializer(data=request.data); serializer.is_valid(raise_exception=True)
    return Response(services.bulk_followup(request.user,**serializer.validated_data))


@api_view(['GET'])
def session_user(request):
    return Response({'id':request.user.pk,'name':str(request.user),'role':request.user.role})

@api_view(['POST'])
def session_logout(request):
    from django.contrib.auth import logout
    services.audit(None,request.user,'تسجيل خروج')
    logout(request)
    return Response({'detail':'تم تسجيل الخروج'})
