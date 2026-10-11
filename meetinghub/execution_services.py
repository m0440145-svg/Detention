from datetime import timedelta
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone
from core import services as core
from core.models import Decision,DecisionStatus,Audit,Status,Notification,User,Role
from core.policy import GLOBAL_ROLES
from .models import *
from . import services,workflow_services as workflow

@transaction.atomic
def create_decision(user,revision,item,number,text,due_date,followup_owner,supervisor,team,priority,sla_hours,escalation_hours,output,reason):
    core.require_decision_manage(user);meeting=MeetingRecord.objects.select_for_update().get(pk=revision.minutes.meeting_id);workflow.readable(user,meeting);reason=services.reason_required(reason)
    if not hasattr(revision,'seal') or revision.version!=revision.minutes.current_version:raise ValidationError('القرار يحتاج إصدار محضر نهائيًا مختومًا.')
    if item.meeting_id!=meeting.pk or item.removed or item.kind not in ['decision','vote']:raise ValidationError('اختر بند قرار لهذا المحضر.')
    if not any(i['id']==item.pk for i in revision.snapshot['agenda']):raise ValidationError('البند غير موجود في الإصدار النهائي.')
    if item.votes.filter(purpose='decision').exists() and not VoteResult.objects.filter(vote__item=item,outcome='passed').exists():raise ValidationError('لم تعتمد نتيجة هذا البند؛ وثق قرارًا معتمدًا أولًا.')
    if not output.strip() or sla_hours<1 or escalation_hours<1:raise ValidationError('المخرج القابل للقياس وSLA والتصعيد مطلوبة.')
    if not supervisor.is_active or supervisor.role not in GLOBAL_ROLES|{Role.HEAD}:raise ValidationError('اختر جهة إشراف مخولة.')
    if any(not u.is_active or u.role in [Role.ADMIN,Role.VIEWER,Role.BOARD] for u in team):raise ValidationError('فريق التنفيذ يحتاج حسابات تنفيذ نشطة.')
    if ExecutionRecord.objects.filter(revision=revision,item=item).exists():raise ValidationError('حُوّل البند من هذا الإصدار مسبقًا؛ لا قرار مكرر.')
    decision=core.save_decision(user,{'number':number,'meeting':meeting.meeting,'text':text,'date':timezone.localdate(),'issuing_authority':meeting.committee.kind,'due_date':due_date,'followup_owner':followup_owner},reason=reason)
    ext=ExecutionRecord.objects.create(decision=decision,revision=revision,item=item,supervisor=supervisor,priority=priority,sla_hours=sla_hours,escalation_hours=escalation_hours,output=output);ext.team.set(team)
    workflow.emit(meeting,user,'قرار من محضر نهائي',reason,{'decision':decision.pk,'revision':revision.pk,'item':item.pk,'digest':revision.digest});return ext

@transaction.atomic
def update_execution(user,ext,action,reason,version):
    core.require_decision_manage(user);ext=ExecutionRecord.objects.select_for_update().select_related('decision','revision__minutes__meeting').get(pk=ext.pk);reason=services.reason_required(reason)
    from core.views import decisions_for
    if not decisions_for(user).filter(pk=ext.decision_id).exists():raise PermissionDenied()
    if ext.version!=version:raise ValidationError('تعارض إصدار القرار.')
    if ext.decision.status in ['executed','cancelled']:raise ValidationError('القرار المنتهي ثابت؛ يصحح بقرار جديد موثق.')
    if action=='defer':ext.deferred=True
    elif action=='resume':ext.deferred=False
    elif action=='cancel':ext.decision.status=DecisionStatus.CANCELLED;ext.decision.save(update_fields=['status'])
    elif action=='complete':
        if not ext.decision.tasks.exists() or ext.decision.tasks.exclude(status=Status.CLOSED).exists():raise ValidationError('الإغلاق بعد اعتماد جميع المهام.')
        if not ext.decision.attachments.exists() or ext.stages.exclude(progress=100).exists() or ext.stages.filter(progress=100,evidence__isnull=True,link='').exists():raise ValidationError('أرفق شاهد تنفيذ قبل الإغلاق.')
        ext.decision.status=DecisionStatus.EXECUTED;ext.decision.save(update_fields=['status'])
    else:raise ValidationError('إجراء غير صالح.')
    ext.status_reason=reason;ext.version+=1;ext.save();workflow.emit(ext.revision.minutes.meeting,user,'تحديث تنفيذ قرار',reason,{'decision':ext.decision_id,'action':action,'version':ext.version});return ext

@transaction.atomic
def save_stage(user,ext,title,due_date,progress,evidence,link,reason,version,instance=None):
    core.require_decision_manage(user);ext=ExecutionRecord.objects.select_for_update().get(pk=ext.pk);reason=services.reason_required(reason)
    from core.views import decisions_for
    if not decisions_for(user).filter(pk=ext.decision_id).exists():raise PermissionDenied()
    if ext.version!=version or ext.decision.status in ['executed','cancelled']:raise ValidationError('تعارض أو قرار منتهٍ.')
    if due_date>ext.decision.due_date or due_date<ext.decision.date:raise ValidationError('مرحلة خارج مواعيد القرار.')
    if evidence and evidence.decision_id!=ext.decision_id:raise ValidationError('شاهد خارج القرار.')
    if link and not link.startswith('https://'):raise ValidationError('الرابط يجب أن يستخدم HTTPS.')
    if progress==100 and not evidence and not link:raise ValidationError('اكتمال المرحلة يحتاج شاهدًا.')
    row=ExecutionStage.objects.select_for_update().get(pk=instance.pk,execution=ext) if instance else ExecutionStage(execution=ext)
    for key,value in dict(title=title,due_date=due_date,progress=progress,evidence=evidence,link=link,reason=reason).items():setattr(row,key,value)
    row.full_clean();row.save();ext.version+=1;ext.save(update_fields=['version']);workflow.emit(ext.revision.minutes.meeting,user,'مرحلة تنفيذ',reason,{'decision':ext.decision_id,'stage':row.pk});return row

def task_changed(task,user):
    if not task.decision_id:return
    ext=ExecutionRecord.objects.filter(decision_id=task.decision_id).select_related('revision__minutes__meeting').first()
    if not ext:return
    workflow.emit(ext.revision.minutes.meeting,user,'تحديث مهمة القرار','تحديث تنفيذ مرتبط بالقرار',{'decision':task.decision_id,'task':task.pk,'progress':task.progress,'status':task.status})
    # Approval remains with task roles; 100% progress alone never closes a decision.
    if task.decision.status not in [DecisionStatus.EXECUTED,DecisionStatus.CANCELLED] and task.decision.tasks.exists() and not task.decision.tasks.exclude(status=Status.CLOSED).exists() and task.decision.attachments.exists() and not ext.stages.exclude(progress=100).exists():
        Decision.objects.filter(pk=task.decision_id).update(status=DecisionStatus.EXECUTED)
        Audit.objects.create(decision=task.decision,actor=user,action='اكتمال جميع المهام المعتمدة',new={'reason':'جميع المهام مغلقة مع شاهد تنفيذ','derived':True})

@transaction.atomic
def run_decision_alerts(now=None):
    now=now or timezone.now();count=0
    for ext in ExecutionRecord.objects.select_related('decision','supervisor','revision__minutes__meeting').exclude(decision__status__in=['executed','cancelled']):
        d=ext.decision;due=timezone.make_aware(__import__('datetime').datetime.combine(d.due_date,__import__('datetime').time.max));elapsed=(now-ext.revision.created_at).total_seconds()/3600
        kinds=[]
        if 0<=(due-now).total_seconds()<=86400:kinds.append('due_soon')
        if now>due:kinds.append('overdue')
        if elapsed>=ext.sla_hours:kinds.append('sla')
        if now>=due+timedelta(hours=ext.escalation_hours):kinds.append('escalation_1')
        if now>=due+timedelta(hours=ext.escalation_hours*2):kinds.append('escalation_2')
        for kind in kinds:
            key=f'decision:{d.pk}:{d.due_date}:{kind}'
            alert,created=DecisionAlert.objects.get_or_create(key=key,defaults={'decision':d,'kind':kind})
            if created:
                recipients=[ext.supervisor] if kind.startswith('escalation') else [d.followup_owner]
                if kind=='escalation_2':recipients=list(User.objects.filter(is_active=True,role=Role.EXECUTIVE))
                from core.views import decisions_for
                for u in recipients:
                    if decisions_for(u).filter(pk=d.pk).exists():Notification.objects.create(recipient=u,text=f'تنبيه قرار ({kind}): {d.number} /decisions/{d.pk}/')
                workflow.emit(ext.revision.minutes.meeting,None,'تنبيه قرار داخلي آلي','استحقاق أو SLA أو تصعيد',{'alert':alert.pk,'decision':d.pk,'kind':kind});count+=1
    return count
