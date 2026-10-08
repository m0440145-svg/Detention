from django.shortcuts import get_object_or_404
from rest_framework import serializers
from .models import *
from . import session_services as ops,services

class AgendaInput(serializers.ModelSerializer):
    class Meta:
        model=AgendaItem
        fields=['title','kind','duration','presenter','owner','parent','link','previous_decision','notes']

class ActionInput(serializers.Serializer):
    action=serializers.ChoiceField(choices=['save','publish','order','remove','read'])
    reason=serializers.CharField(required=False,default='')
    version=serializers.IntegerField(required=False)
    item=serializers.IntegerField(required=False)
    revision=serializers.IntegerField(required=False)
    order=serializers.ListField(child=serializers.IntegerField(min_value=1),required=False)
    data=serializers.DictField(required=False)

class AttendInput(serializers.Serializer):
    action=serializers.ChoiceField(choices=['issue','reset','revoke','respond','mark'])
    reason=serializers.CharField(required=False,default='')
    version=serializers.IntegerField(required=False)
    invitation=serializers.IntegerField(required=False)
    answer=serializers.ChoiceField(choices=['confirm','decline','proxy'],required=False)
    eligibility=serializers.IntegerField(required=False)
    status=serializers.ChoiceField(choices=['present','remote','left','disconnected','absent'],required=False)
    verification=serializers.CharField(required=False)

class ProxyInput(serializers.Serializer):
    principal=serializers.PrimaryKeyRelatedField(queryset=Eligibility.objects.all())
    delegate=serializers.PrimaryKeyRelatedField(queryset=Eligibility.objects.all())
    document=serializers.PrimaryKeyRelatedField(queryset=MeetingFile.objects.all())
    valid_until=serializers.DateTimeField()
    attendance_allowed=serializers.BooleanField(default=False)
    voting_allowed=serializers.BooleanField(default=False)
    reason=serializers.CharField()

def require_keys(data,*keys):
    missing=[k for k in keys if k not in data]
    if missing:raise serializers.ValidationError({'missing':missing})

def agenda_post(user,record,payload):
    if payload.get('action')!='read':services.require_manage(user,record)
    s=ActionInput(data=payload);s.is_valid(raise_exception=True);d=s.validated_data;action=d['action']
    if action=='read':
        require_keys(d,'revision');row=ops.acknowledge(user,record,get_object_or_404(record.agenda_revisions,version=d['revision']));return {'acknowledgment':row.pk}
    require_keys(d,'version')
    if action=='publish':row=ops.publish_agenda(user,record,d['reason'],d['version']);return {'revision':row.version,'digest':row.digest}
    if action=='save':
        require_keys(d,'data');item=get_object_or_404(record.agenda_items,pk=d['item']) if d.get('item') else None
        form=AgendaInput(data=d['data'],partial=bool(item));form.is_valid(raise_exception=True)
        row=ops.save_item(user,record,form.validated_data,d['reason'],d['version'],item);return {'item':row.pk}
    if action=='order':require_keys(d,'order');ops.reorder(user,record,d['order'],d['reason'],d['version']);return {'saved':True}
    if action=='remove':require_keys(d,'item');ops.remove_item(user,record,get_object_or_404(record.agenda_items,pk=d['item']),d['reason'],d['version']);return {'saved':True}

def attendance_post(user,record,payload):
    if payload.get('action')!='respond':services.require_manage(user,record)
    s=AttendInput(data=payload);s.is_valid(raise_exception=True);d=s.validated_data;action=d['action'];reason=d['reason']
    if action in ['issue','reset']:
        require_keys(d,'version')
        if action=='issue':return {'links':ops.issue_invitations(user,record,reason,d['version']),'delivery':'internal_only'}
        ops.reset_invitations(user,record,reason,d['version']);return {'reset':True}
    if action=='revoke':require_keys(d,'invitation');ops.revoke_invitation(user,record,get_object_or_404(Invitation,pk=d['invitation']),reason);return {'revoked':True}
    if action=='respond':
        require_keys(d,'answer');elig=get_object_or_404(Eligibility,roster__meeting=record,roster__generation=record.roster_generation,member__user=user,invited=True)
        row=ops.respond(elig.invitation,d['answer'],user);return {'response':row.response}
    if action=='mark':
        require_keys(d,'eligibility','status','verification');row=ops.mark_attendance(user,record,get_object_or_404(Eligibility,pk=d['eligibility']),d['status'],d['verification'],reason);return {'attendance':row.pk,'status':row.status}
