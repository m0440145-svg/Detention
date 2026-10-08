from django.core.exceptions import ValidationError
from rest_framework import serializers,viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from core.models import User,Meeting
from .models import *
from . import services

class MemberSerializer(serializers.ModelSerializer):
    name=serializers.CharField(source='user.__str__',read_only=True)
    national_id=serializers.CharField(write_only=True,required=False,max_length=10,allow_blank=True)
    reason=serializers.CharField(write_only=True)
    active=serializers.BooleanField(read_only=True)
    class Meta:
        model=Member
        fields=['id','user','name','role','organization','capacity','starts','ends','archived','active','national_id','reason']
        read_only_fields=['archived']
    def create(self,data):return services.save_member(self.context['request'].user,data,reason=data.pop('reason'),national_id=data.pop('national_id',''))
    def update(self,row,data):return services.save_member(self.context['request'].user,data,row,data.pop('reason',''),data.pop('national_id',''))

class CommitteeSerializer(serializers.ModelSerializer):
    reason=serializers.CharField(write_only=True)
    version=serializers.IntegerField(required=False)
    class Meta:
        model=Committee
        fields=['id','name','kind','mandate','chair','secretary','members','starts','ends','archived','version','reason']
        read_only_fields=['archived']
    def create(self,data):
        reason=data.pop('reason');people=data.pop('members');data.pop('version',None)
        return services.save_committee(self.context['request'].user,data,people,reason=reason)
    def update(self,row,data):
        reason=data.pop('reason','');people=data.pop('members',list(row.members.all()));version=data.pop('version',None)
        return services.save_committee(self.context['request'].user,data,people,row,reason,version)

class MeetingSerializer(serializers.ModelSerializer):
    number=serializers.CharField(source='meeting.number',max_length=60)
    name=serializers.CharField(source='meeting.name',max_length=200)
    core_meeting_id=serializers.IntegerField(source='meeting_id',read_only=True)
    legacy=serializers.PrimaryKeyRelatedField(queryset=Meeting.objects.filter(governance__isnull=True),required=False,allow_null=True,write_only=True)
    reason=serializers.CharField(write_only=True)
    version=serializers.IntegerField(required=False)
    quorum=serializers.SerializerMethodField()
    can_manage=serializers.SerializerMethodField()
    class Meta:
        model=MeetingRecord
        fields=['id','core_meeting_id','legacy','number','name',*services.MEETING_FIELDS,'invitees','status','archived','version','created_at','updated_at','reason','quorum','can_manage']
        read_only_fields=['status','archived','created_at','updated_at']
    def get_quorum(self,row):return services.quorum(row)
    def get_can_manage(self,row):return services.can_manage(self.context['request'].user,row.committee)
    def create(self,data):
        data.update(data.pop('meeting'));reason=data.pop('reason');people=data.pop('invitees');legacy=data.pop('legacy',None);data.pop('version',None)
        return services.save_meeting(self.context['request'].user,data,people,reason=reason,legacy=legacy)
    def update(self,row,data):
        data.update(data.pop('meeting',{}));reason=data.pop('reason','');version=data.pop('version',None);people=data.pop('invitees',list(row.invitees.all()));data.pop('legacy',None)
        return services.save_meeting(self.context['request'].user,data,people,row,reason,version)

class ArchiveAction(serializers.Serializer):
    action=serializers.ChoiceField(choices=['archive','restore'])
    reason=serializers.CharField()

class AdministrativeViewSet(viewsets.ModelViewSet):
    def create(self,request,*args,**kwargs):
        services.require_admin(request.user)
        return super().create(request,*args,**kwargs)
    def update(self,request,*args,**kwargs):
        services.require_admin(request.user)
        return super().update(request,*args,**kwargs)

class MemberViewSet(AdministrativeViewSet):
    serializer_class=MemberSerializer
    http_method_names=['get','post','patch','head','options']
    def get_queryset(self):
        services.require_admin(self.request.user)
        return Member.objects.select_related('user')
    @action(detail=True,methods=['post'])
    def transition(self,request,pk=None):
        s=ArchiveAction(data=request.data);s.is_valid(raise_exception=True)
        row=services.archive_member(request.user,self.get_object(),s.validated_data['action']=='archive',s.validated_data['reason'])
        return Response(self.get_serializer(row).data)

class CommitteeViewSet(AdministrativeViewSet):
    serializer_class=CommitteeSerializer
    http_method_names=['get','post','patch','head','options']
    def get_queryset(self):return services.committees_for(self.request.user)
    @action(detail=True,methods=['post'])
    def transition(self,request,pk=None):
        s=ArchiveAction(data=request.data);s.is_valid(raise_exception=True)
        row=services.archive_committee(request.user,self.get_object(),s.validated_data['action']=='archive',s.validated_data['reason'])
        return Response(self.get_serializer(row).data)

class MeetingAction(serializers.Serializer):
    action=serializers.ChoiceField(choices=['schedule','cancel','archive','restore'])
    reason=serializers.CharField()
    version=serializers.IntegerField(min_value=1)

class MeetingViewSet(viewsets.ModelViewSet):
    serializer_class=MeetingSerializer
    http_method_names=['get','post','patch','head','options']
    def get_queryset(self):return services.records_for(self.request.user,self.request.query_params.get('archived')=='1')
    def create(self,request,*args,**kwargs):
        if not any(services.can_manage(request.user,c) for c in services.committees_for(request.user)):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied('لا تملك صلاحية إنشاء اجتماع.')
        return super().create(request,*args,**kwargs)
    def update(self,request,*args,**kwargs):
        services.require_manage(request.user,self.get_object())
        return super().update(request,*args,**kwargs)
    def retrieve(self,request,*args,**kwargs):
        row=self.get_object();services.event(f'meeting:{row.pk}',request.user,'اطلاع API','قراءة الاجتماع عبر API',{})
        return Response(self.get_serializer(row).data)
    @action(detail=True,methods=['post'])
    def transition(self,request,pk=None):
        s=MeetingAction(data=request.data);s.is_valid(raise_exception=True)
        row=services.meeting_action(request.user,self.get_object(),**s.validated_data)
        return Response(self.get_serializer(row).data)
    @action(detail=True,methods=['get'])
    def events(self,request,pk=None):
        row=self.get_object();scope=f'meeting:{row.pk}'
        return Response({'chain_valid':services.verify_chain(scope),'events':list(Event.objects.filter(stream__scope=scope).values('id','actor_id','action','reason','details','created_at','previous_hash','digest'))})
    @action(detail=True,methods=['get','post'])
    def agenda(self,request,pk=None):
        from . import session_services as ops,session_api
        row=self.get_object()
        if request.method=='POST':return Response(session_api.agenda_post(request.user,row,request.data))
        version=request.query_params.get('revision',row.agenda_version)
        if not str(version).isdigit():raise serializers.ValidationError('رقم إصدار غير صالح.')
        revision=row.agenda_revisions.filter(version=version).first()
        return Response({'version':revision.version if revision else None,'digest':revision.digest if revision else None,'items':ops.revision_items(request.user,row,revision) if revision else [],'draft':[{'id':i.pk,'title':i.title,'parent':i.parent_id,'position':i.position,'duration':i.duration} for i in ops.draft_items(request.user,row)] if services.can_manage(request.user,row.committee) else []})
    @action(detail=True,methods=['get','post'])
    def attendance(self,request,pk=None):
        from . import session_services as ops,session_api
        row=self.get_object()
        if request.method=='POST':
            result=session_api.attendance_post(request.user,row,request.data)
            for link in result.get('links',[]):link['url']=request.build_absolute_uri('/meetings/rsvp/')+'#'+link.pop('token')
            response=Response(result);response['Cache-Control']='no-store';return response
        roster=ops.live_roster(row);people=[]
        if roster:
            qs=roster.people.filter(invited=True).select_related('member__user','attendance','invitation')
            if not services.can_manage(request.user,row.committee):qs=qs.filter(member__user=request.user)
            for p in qs:
                a=getattr(p,'attendance',None);i=getattr(p,'invitation',None)
                people.append({'eligibility':p.pk,'name':p.name,'quorum_eligible':p.quorum_eligible,'voting_eligible':p.voting_eligible,'response':i.response if i else None,'attendance':a.status if a else None,'entered_at':a.entered_at if a else None,'exited_at':a.exited_at if a else None})
        return Response({'quorum':services.quorum(row),'people':people})
    @action(detail=True,methods=['post'])
    def files(self,request,pk=None):
        from . import session_services as ops
        from django.shortcuts import get_object_or_404
        row=self.get_object();services.require_manage(request.user,row);version=serializers.IntegerField().run_validation(request.data.get('version'))
        if not request.FILES.get('file'):raise serializers.ValidationError('اختر مرفقًا.')
        item=get_object_or_404(row.agenda_items,pk=request.data['item']) if request.data.get('item') else None
        f=ops.upload_file(request.user,row,request.FILES['file'],request.data.get('reason',''),version,item,request.data.get('purpose','agenda'))
        return Response({'id':f.pk,'sha256':f.sha256,'url':f'/meetings/files/{f.pk}/'},status=201)
    @action(detail=True,methods=['post'])
    def proxies(self,request,pk=None):
        from . import session_services as ops,session_api
        row=self.get_object()
        services.require_manage(request.user,row)
        if request.data.get('action')=='revoke':
            from django.shortcuts import get_object_or_404
            ops.revoke_proxy(request.user,row,get_object_or_404(Proxy,pk=request.data.get('proxy')),request.data.get('reason',''));return Response({'revoked':True})
        s=session_api.ProxyInput(data=request.data);s.is_valid(raise_exception=True)
        proxy=ops.approve_proxy(request.user,row,**s.validated_data);return Response({'proxy':proxy.pk},status=201)
