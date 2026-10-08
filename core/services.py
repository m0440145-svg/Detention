import math, re, uuid, zipfile
from datetime import timedelta
from pathlib import Path
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg
from django.utils import timezone
from .models import *
from .policy import *
TERMINAL={Status.CLOSED,Status.CANCELLED}
TRANSITIONS={
 Status.NEW:{Status.ASSIGNED,Status.CANCELLED},
 Status.ASSIGNED:{Status.ACTIVE,Status.WAITING,Status.EXTERNAL,Status.PAUSED,Status.CANCELLED},
 Status.ACTIVE:{Status.WAITING,Status.EXTERNAL,Status.PAUSED,Status.CANCELLED},
 Status.WAITING:{Status.ACTIVE,Status.EXTERNAL,Status.PAUSED,Status.CANCELLED},
 Status.EXTERNAL:{Status.ACTIVE,Status.PAUSED,Status.CANCELLED},
 Status.RETURNED:{Status.ACTIVE,Status.WAITING,Status.PAUSED,Status.CANCELLED},
 Status.PAUSED:{Status.ACTIVE,Status.CANCELLED},
 Status.COMPLETED:{Status.APPROVAL},Status.APPROVAL:{Status.CLOSED,Status.RETURNED},
 Status.CLOSED:set(),Status.CANCELLED:set()}
def task_snapshot(task):
    fields=['title','description','source','task_type','project','unit_id','owner_id','start_date','due_date','priority','confidentiality','expected_result','success_indicator','notes','auto_progress','decision_id','assignment_mode']
    data={k:(str(getattr(task,k)) if k in ['start_date','due_date'] else getattr(task,k)) for k in fields}
    data['participants']=list(task.participants.values_list('pk',flat=True)); data['participating_units']=list(task.participating_units.values_list('pk',flat=True))
    return data
def audit(task,user,action,old=None,new=None):
    old=old or {}; new=new or {}
    item=Audit.objects.create(task=task,actor=user,action=action,old=old,new=new)
    if task and task.decision_id and 'subtask' not in new and any(k in old for k in ['progress','status']):
        d=Decision.objects.select_for_update().get(pk=task.decision_id)
        values=list(d.tasks.values_list('progress','status')); total=len(values)
        if total:
            progress=round(sum(p for p,s in values)/total)
            approved=round(sum(s==Status.CLOSED for p,s in values)/total*100)
            previous_progress=round((sum(p for p,s in values)-task.progress+old.get('progress',task.progress))/total)
            previous_closed=sum(s==Status.CLOSED for p,s in values)-(task.status==Status.CLOSED)+(old.get('status',task.status)==Status.CLOSED)
            previous_approved=round(previous_closed/total*100)
            if (previous_progress,previous_approved)!=(progress,approved):
                Audit.objects.create(decision=d,actor=user,action='تحديث نسب القرار من '+task.code,old={'progress':previous_progress,'approved_progress':previous_approved},new={'progress':progress,'approved_progress':approved,'derived':True,'source_task_id':task.pk,'reason':new.get('reason') or new.get('comment') or action})
    return item
def assignment_members(task,participants):
    ids={u.pk for u in participants}
    unit_ids=[task.unit_id] if task.assignment_mode=='unit' else [task.unit_id,*task.participating_units.values_list('pk',flat=True)] if task.assignment_mode=='units' else []
    if unit_ids: ids.update(User.objects.filter(units__pk__in=unit_ids,is_active=True).exclude(role__in=[Role.VIEWER,Role.ADMIN,Role.BOARD]).values_list('pk',flat=True))
    ids.discard(task.owner_id)
    return User.objects.filter(pk__in=ids,is_active=True).exclude(role__in=[Role.ADMIN,Role.BOARD]).distinct()
def recipients(task):
    ids={task.owner_id,*task.participants.values_list('pk',flat=True)}
    if task.unit.head_id: ids.add(task.unit.head_id)
    return User.objects.filter(pk__in=ids,is_active=True).exclude(role__in=[Role.ADMIN,Role.BOARD])
def notify(task,text,users=None,key=None):
    for user in users if users is not None else recipients(task):
        unique=f'{key}:{user.pk}' if key else None
        if unique: Notification.objects.get_or_create(key=unique,defaults={'task':task,'recipient':user,'text':text})
        else: Notification.objects.create(task=task,recipient=user,text=text)
@transaction.atomic
def create_task(user,data,participants=(),units=()):
    require_manage(user)
    linked_decision=data.get('decision')
    decision_before=None
    if linked_decision:
        linked_decision=Decision.objects.select_for_update().get(pk=linked_decision.pk)
        decision_before={'progress':linked_decision.progress,'approved_progress':linked_decision.approved_progress}
    task=Task(created_by=user,**data)
    if user.role==Role.HEAD and task.unit.head_id!=user.pk: raise PermissionDenied('الإسناد خارج الوحدة غير مصرح.')
    if not task.owner.is_active or task.owner.role in {Role.VIEWER,Role.ADMIN,Role.BOARD}: raise ValidationError('المسؤول الرئيسي يجب أن يكون حسابًا نشطًا له صلاحية التنفيذ.')
    if Catalog.objects.filter(kind='priority',key=task.priority,active=False).exists(): raise ValidationError('الأولوية غير مفعلة للتكليفات الجديدة.')
    if Catalog.objects.filter(kind='task_type').exists() and not Catalog.objects.filter(kind='task_type',key=task.task_type,active=True).exists(): raise ValidationError('نوع المهمة غير مفعل.')
    task.full_clean()
    task.status=Status.ASSIGNED
    task.progress=0
    task.save()
    task.participating_units.set(units)
    task.participants.set(assignment_members(task,participants))
    audit(task,user,'إنشاء وإسناد المهمة',new={'owner':task.owner_id,'participants':list(task.participants.values_list('pk',flat=True)),'unit':task.unit_id,'due_date':str(task.due_date),'reason':'إنشاء وإسناد المهمة'})
    if linked_decision:
        Audit.objects.create(decision=linked_decision,actor=user,action='ربط '+task.code,old=decision_before,new={'progress':linked_decision.progress,'approved_progress':linked_decision.approved_progress,'derived':True,'source_task_id':task.pk,'reason':'إنشاء وإسناد مهمة مرتبطة بالقرار'})
    notify(task,f'تم إسناد {task.code}: {task.title}')
    return task
@transaction.atomic
def change_status(user,task,status,comment):
    task=Task.objects.select_for_update().get(pk=task.pk)
    require_work(user,task)
    if Catalog.objects.filter(kind='status',key=status,active=False).exists(): raise ValidationError('هذه الحالة موقوفة للتغييرات الجديدة.')
    if status not in TRANSITIONS.get(task.status,set()): raise ValidationError('انتقال الحالة غير مسموح.')
    if not comment.strip(): raise ValidationError('ملاحظة التغيير مطلوبة.')
    if status in {Status.CLOSED,Status.RETURNED,Status.PAUSED,Status.CANCELLED}: require_manage(user,task)
    if status==Status.CLOSED:
        if task.owner_id==user.pk: raise ValidationError('لا يجوز اعتماد المهمة بواسطة مسؤولها الرئيسي.')
        if task.progress!=100 or task.obstacles.filter(resolved_at__isnull=True).exists(): raise ValidationError('حل العوائق واستكمال الإنجاز مطلوب قبل الاعتماد.')
        task.closed_at=timezone.now()
    old={'status':task.status,'progress':task.progress}
    task.status=status
    if status==Status.RETURNED:
        task.progress=min(task.progress,99)
        task.submitted_at=None
    task.validate_status_progress()
    task.updated_at=timezone.now(); task.save()
    audit(task,user,'تغيير الحالة',old,{'status':status,'progress':task.progress,'reason':comment})
    notify(task,f'{task.code}: {task.get_status_display()} — {comment}')
    return task
@transaction.atomic
def update_progress(user,task,progress,comment,accomplished,remaining,from_subtasks=False):
    task=Task.objects.select_for_update().get(pk=task.pk)
    require_work(user,task)
    if task.status in TERMINAL|{Status.APPROVAL,Status.COMPLETED,Status.PAUSED}: raise ValidationError('المهمة لا تقبل تحديث التنفيذ في حالتها الحالية.')
    if task.auto_progress and task.subtasks.exists() and not from_subtasks: raise ValidationError('النسبة محتسبة من المهام الفرعية؛ حدّثها أولًا.')
    if not 0<=progress<=100 or not comment.strip() or not accomplished.strip() or not remaining.strip(): raise ValidationError('أدخل نسبة صحيحة ووصفًا للمنجز والمتبقي وتعليقًا.')
    if progress==100 and task.obstacles.filter(resolved_at__isnull=True).exists(): raise ValidationError('يجب معالجة العوائق المفتوحة قبل طلب الاعتماد.')
    old={'progress':task.progress,'status':task.status}
    task.progress=progress; task.updated_at=timezone.now()
    if progress==100:
        task.status=Status.APPROVAL; task.submitted_at=timezone.now()
    elif task.status in {Status.ASSIGNED,Status.NEW,Status.RETURNED}: task.status=Status.ACTIVE
    task.validate_status_progress(); task.save()
    Update.objects.create(task=task,user=user,progress=progress,comment=comment,accomplished=accomplished,remaining=remaining)
    audit(task,user,'تحديث الإنجاز',old,{'progress':progress,'status':task.status,'reason':comment})
    if progress==100: notify(task,f'{task.code}: طلب اعتماد الإنجاز')
    return task
@transaction.atomic
def add_comment(user,task,body,parent=None,mentions=(),mentioned_units=()):
    require_work(user,task)
    if not body.strip(): raise ValidationError('التعليق مطلوب.')
    if parent and parent.task_id!=task.pk: raise ValidationError('الرد يجب أن يكون داخل المهمة نفسها.')
    valid=set(recipients(task).values_list('pk',flat=True))
    if any(u.pk not in valid for u in mentions): raise ValidationError('لا يمكن الإشارة لمستخدم خارج فريق المهمة.')
    allowed_units={task.unit_id,*task.participating_units.values_list('pk',flat=True)}
    if any(u.pk not in allowed_units for u in mentioned_units): raise ValidationError('الوحدة المشار إليها ليست ضمن المهمة.')
    comment=Comment.objects.create(task=task,user=user,body=body,parent=parent)
    comment.mentions.set(mentions); comment.mentioned_units.set(mentioned_units)
    audit(task,user,'إضافة رد' if parent else 'إضافة تعليق',new={'comment_id':comment.pk,'body':body})
    notify(task,f'{task.code}: تعليق من {user}')
    for unit in mentioned_units:
        visible=[u for u in unit.employees.filter(is_active=True) if tasks_for(u).filter(pk=task.pk).exists()]
        notify(task,f'{task.code}: تمت الإشارة إلى وحدتك',visible)
    return comment
@transaction.atomic
def report_obstacle(user,task,data):
    task=Task.objects.select_for_update().get(pk=task.pk); require_work(user,task)
    if task.status in TERMINAL|{Status.APPROVAL,Status.PAUSED}: raise ValidationError('الحالة الحالية لا تسمح بتسجيل تعثر.')
    if not data.get('reason','').strip(): raise ValidationError('سبب التعثر مطلوب.')
    target=data['intervention_owner']
    if not tasks_for(target).filter(pk=task.pk).exists(): raise ValidationError('المطلوب تدخله يجب أن يملك صلاحية الاطلاع على المهمة.')
    obstacle=Obstacle(task=task,reported_by=user,**data); obstacle.full_clean(); obstacle.save()
    old=task.status; task.updated_at=timezone.now(); task.save(update_fields=['updated_at'])
    audit(task,user,'تسجيل عائق',{'status':old},{'status':task.status,'obstacle':obstacle.pk,'description':obstacle.description,'reason':obstacle.reason})
    notify(task,f'{task.code}: تسجيل تعثر — {obstacle.kind}')
    notify(task,f'{task.code}: مطلوب تدخلك لمعالجة العائق',[target])
    return obstacle
@transaction.atomic
def resolve_obstacle(user,obstacle,resolution):
    task=Task.objects.select_for_update().get(pk=obstacle.task_id)
    require_manage(user,task)
    if obstacle.resolved_at: raise ValidationError('العائق معالج بالفعل.')
    if not resolution.strip(): raise ValidationError('وصف المعالجة مطلوب.')
    obstacle.resolution=resolution; obstacle.resolved_at=timezone.now(); obstacle.save()
    old=task.status
    task.updated_at=timezone.now(); task.save(update_fields=['updated_at'])
    audit(task,user,'معالجة العائق',{'status':old},{'status':task.status,'obstacle':obstacle.pk,'resolution':resolution})
    notify(task,f'{task.code}: تمت معالجة العائق')
def validate_upload(upload):
    extension=Path(upload.name).suffix.lower()
    if upload.size>10*1024*1024: raise ValidationError('الحد الأعلى للملف 10 ميجابايت.')
    if extension not in {'.pdf','.png','.jpg','.jpeg','.docx','.xlsx','.csv','.txt'}: raise ValidationError('نوع الملف غير مسموح. استخدم PDF أو PNG/JPG أو DOCX/XLSX أو CSV/TXT.')
    header=upload.read(16); upload.seek(0)
    signatures={'.pdf':b'%PDF-', '.png':b'\x89PNG\r\n\x1a\n','.jpg':b'\xff\xd8\xff','.jpeg':b'\xff\xd8\xff'}
    if extension in signatures and not header.startswith(signatures[extension]): raise ValidationError('محتوى الملف لا يطابق نوعه.')
    if extension in {'.docx','.xlsx'}:
        try:
            with zipfile.ZipFile(upload) as z:
                names=z.namelist()
                if sum(i.file_size for i in z.infolist())>50*1024*1024: raise ValidationError('الأرشيف يتجاوز الحد المسموح.')
                expected='word/document.xml' if extension=='.docx' else 'xl/workbook.xml'
                if expected not in names or any('vbaproject' in n.lower() for n in names): raise ValidationError('ملف Office غير صالح أو يحتوي وحدات ماكرو.')
        except zipfile.BadZipFile: raise ValidationError('ملف Office غير صالح.')
        finally: upload.seek(0)
    if extension in {'.txt','.csv'}:
        try: upload.read().decode('utf-8-sig')
        except UnicodeDecodeError: raise ValidationError('الملف النصي يجب أن يكون UTF-8.')
        finally: upload.seek(0)
@transaction.atomic
def attach(user,task,upload,comment=None,subtask=None):
    require_work(user,task); validate_upload(upload)
    if comment and comment.task_id!=task.pk: raise ValidationError('تعليق خارج المهمة.')
    if subtask and subtask.task_id!=task.pk: raise ValidationError('مهمة فرعية خارج المهمة.')
    original=Path(upload.name).name
    upload.name=uuid.uuid4().hex+Path(original).suffix.lower()
    item=Attachment.objects.create(task=task,uploaded_by=user,file=upload,original_name=original,comment=comment,subtask=subtask)
    audit(task,user,'رفع ملف',new={'attachment':item.pk,'name':original})
    return item
@transaction.atomic
def save_subtask(user,task,data,instance=None):
    task=Task.objects.select_for_update().get(pk=task.pk); require_work(user,task)
    if task.status in TERMINAL|{Status.APPROVAL,Status.PAUSED}: raise ValidationError('الحالة الحالية لا تسمح بتعديل المهام الفرعية.')
    if instance is None: require_manage(user,task)
    elif not can_manage(user,task) and instance.owner_id!=user.pk: raise PermissionDenied()
    if data['owner'].pk not in set(recipients(task).values_list('pk',flat=True)): raise ValidationError('مسؤول المهمة الفرعية يجب أن يكون من فريق المهمة.')
    if data['due_date']>task.due_date or data['due_date']<task.start_date: raise ValidationError('موعد المهمة الفرعية خارج نطاق المهمة الرئيسية.')
    if instance and data['due_date']>instance.due_date and not data.get('comment','').strip(): raise ValidationError('سبب تمديد استحقاق المهمة الفرعية مطلوب.')
    old_sub={'progress':instance.progress,'due_date':str(instance.due_date)} if instance else {}
    sub=instance or Subtask(task=task)
    for k,v in data.items(): setattr(sub,k,v)
    sub.full_clean(); sub.save()
    audit(task,user,'تحديث مهمة فرعية',old=old_sub,new={'subtask':sub.pk,'progress':sub.progress,'due_date':str(sub.due_date),'reason':data.get('comment') or 'إنشاء مهمة فرعية'})
    if task.auto_progress:
        progress=round(task.subtasks.aggregate(p=Avg('progress'))['p'])
        update_progress(user,task,progress,'احتساب من المهام الفرعية','تحديث إنجاز المهام الفرعية','لا يوجد' if progress==100 else 'استكمال المهام الفرعية',True)
    return sub
@transaction.atomic
def edit_task(user,task,data,participants,units,reason):
    task=Task.objects.select_for_update().get(pk=task.pk); require_manage(user,task)
    if task.status in TERMINAL: raise ValidationError('المهمة المغلقة أو الملغاة لا تقبل التعديل.')
    old=task_snapshot(task)
    old_decision=task.decision
    old_metrics={'progress':old_decision.progress,'approved_progress':old_decision.approved_progress} if old_decision else None
    for k,v in data.items(): setattr(task,k,v)
    if user.role==Role.HEAD and task.unit.head_id!=user.pk: raise PermissionDenied()
    if not reason.strip(): raise ValidationError('سبب التعديل مطلوب.')
    task.full_clean(); task.updated_at=timezone.now(); task.save(); task.participating_units.set(units); task.participants.set(assignment_members(task,participants))
    audit(task,user,'تعديل المهمة',old,{**task_snapshot(task),'reason':reason})
    if old.get('decision_id')!=task.decision_id:
        if old_decision:
            Audit.objects.create(decision=old_decision,actor=user,action='فك ارتباط '+task.code,old=old_metrics,new={'progress':old_decision.progress,'approved_progress':old_decision.approved_progress,'derived':True,'source_task_id':task.pk,'reason':reason})
        if task.decision_id:
            values=list(task.decision.tasks.exclude(pk=task.pk).values_list('progress','status'))
            before={'progress':round(sum(p for p,s in values)/len(values)) if values else 0,'approved_progress':round(sum(s==Status.CLOSED for p,s in values)/len(values)*100) if values else 0}
            Audit.objects.create(decision=task.decision,actor=user,action='ربط '+task.code,old=before,new={'progress':task.decision.progress,'approved_progress':task.decision.approved_progress,'derived':True,'source_task_id':task.pk,'reason':reason})
    notify(task,f'{task.code}: تعديل التكليف — {reason}')
    return task
@transaction.atomic
def escalate(user,task,reason):
    require_work(user,task)
    if not reason.strip(): raise ValidationError('سبب التصعيد مطلوب.')
    targets=User.objects.filter(role=Role.EXECUTIVE,is_active=True)
    for target in targets:
        e,created=Escalation.objects.get_or_create(task=task,recipient=target,level=99,defaults={'reason':reason})
        if created: notify(task,f'{task.code}: تصعيد يدوي — {reason}',[target]); audit(task,user,'تصعيد يدوي',new={'recipient':target.pk,'reason':reason})
def flags(task,rules=None,today=None):
    r=rules or RuleSettings.current(); today=today or timezone.localdate()
    if task.status in TERMINAL: return []
    result=[]
    if task.due_date<today: result.append('متأخرة')
    if task.is_blocked: result.append('متعثرة')
    if (today-task.updated_at.date()).days>=r.stale_days: result.append('بدون تحديث')
    duration=max((task.due_date-task.start_date).days,1)
    elapsed=max(0,(today-task.start_date).days)/duration*100
    if elapsed>=r.followup_elapsed and task.progress==0: result.append('بحاجة متابعة')
    if elapsed>=r.risk_elapsed and task.progress<r.risk_progress: result.append('مرشحة للتعثر')
    return result
@transaction.atomic
def run_rules(today=None):
    today=today or timezone.localdate(); r=RuleSettings.current(); count=0
    for task in Task.objects.select_for_update().exclude(status__in=TERMINAL).select_related('unit','owner'):
        for flag in flags(task,r,today): notify(task,f'{task.code}: {flag}',key=f'flag:{task.pk}:{flag}:{today}')
        if task.status in {Status.APPROVAL,Status.COMPLETED}: continue
        days=(task.due_date-today).days
        if 0<=days<=r.remind_days:
            targets=recipients(task) if days==0 else User.objects.filter(pk__in=[task.owner_id,*task.participants.values_list('pk',flat=True)],is_active=True)
            notify(task,f'{task.code}: '+('تستحق اليوم' if days==0 else f'تستحق خلال {days} يوم'),targets,key=f'due:{task.pk}:{today}')
        late=-days
        factor=r.critical_multiplier if task.priority==Priority.CRITICAL else 1
        levels=[(1,max(1,math.ceil(r.head_after*factor)),[task.unit.head] if task.unit.head else []),(2,max(1,math.ceil(r.assistant_after*factor)),list(User.objects.filter(role=Role.ASSISTANT,is_active=True))),(3,max(1,math.ceil(r.executive_after*factor)),list(User.objects.filter(role=Role.EXECUTIVE,is_active=True)))]
        for level,threshold,targets in levels:
            if late<threshold: continue
            for target in targets:
                e,created=Escalation.objects.get_or_create(task=task,recipient=target,level=level,defaults={'reason':f'تأخير {late} أيام'})
                if created: notify(task,f'{task.code}: تصعيد تأخير {late} أيام',[target]); audit(task,None,'تصعيد آلي',new={'level':level,'recipient':target.pk}); count+=1
    return count


DECISION_FIELDS=['number','meeting_id','text','date','followup_owner_id','issuing_authority','approved_minutes_number','approved_minutes_attachment_id','due_date','status']
def decision_snapshot(decision):
    return {key:str(getattr(decision,key)) if key in ['date','due_date'] and getattr(decision,key) else getattr(decision,key) for key in DECISION_FIELDS}
def require_decision_manage(user):
    if user.role not in GLOBAL_ROLES: raise PermissionDenied('إدارة القرارات من صلاحيات الإدارة التنفيذية.')
@transaction.atomic
def save_decision(user,data,instance=None,reason='',minutes_file=None):
    require_decision_manage(user)
    creating=instance is None
    decision=Decision() if creating else Decision.objects.select_for_update().get(pk=instance.pk)
    old={} if creating else decision_snapshot(decision)
    if not creating and not reason.strip(): raise ValidationError('سبب تعديل القرار مطلوب.')
    for key,value in data.items(): setattr(decision,key,value)
    if not decision.followup_owner.is_active or decision.followup_owner.role in {Role.ADMIN,Role.VIEWER,Role.BOARD}: raise ValidationError('مسؤول متابعة القرار يجب أن يكون حسابًا نشطًا له صلاحية التنفيذ.')
    if not decision.issuing_authority or not decision.due_date: raise ValidationError('الجهة المصدرة وتاريخ استحقاق القرار مطلوبان.')
    if minutes_file and not decision.approved_minutes_number.strip(): raise ValidationError('أدخل رقم المحضر المعتمد مع مرفقه.')
    if decision.approved_minutes_number and not minutes_file and not decision.approved_minutes_attachment_id: raise ValidationError('أرفق المحضر المعتمد عند إدخال رقمه.')
    if minutes_file: validate_upload(minutes_file)
    decision.full_clean(); decision.save()
    if minutes_file:
        attach_decision(user,decision,minutes_file,reason or 'إرفاق المحضر عند إنشاء القرار',approved_minutes=True)
        decision.refresh_from_db()
    Audit.objects.create(decision=decision,actor=user,action='إنشاء القرار' if creating else 'تعديل القرار',old=old,new={**decision_snapshot(decision),'reason':reason.strip() or 'إنشاء القرار'})
    return decision
@transaction.atomic
def attach_decision(user,decision,upload,reason,approved_minutes=False):
    require_decision_manage(user)
    decision=Decision.objects.select_for_update().get(pk=decision.pk)
    if not reason.strip(): raise ValidationError('سبب رفع المرفق أو استبداله مطلوب.')
    if approved_minutes and not decision.approved_minutes_number: raise ValidationError('أدخل رقم المحضر المعتمد أولًا.')
    validate_upload(upload)
    original=Path(upload.name).name; upload.name=uuid.uuid4().hex+Path(original).suffix.lower()
    item=Attachment.objects.create(decision=decision,uploaded_by=user,file=upload,original_name=original)
    old={}; new={'attachment_id':item.pk,'name':original,'reason':reason}
    if approved_minutes:
        old={'approved_minutes_attachment_id':decision.approved_minutes_attachment_id}
        decision.approved_minutes_attachment=item; decision.save(update_fields=['approved_minutes_attachment'])
        new['approved_minutes_attachment_id']=item.pk
    Audit.objects.create(decision=decision,actor=user,action='إرفاق المحضر المعتمد' if approved_minutes else 'رفع مرفق قرار',old=old,new=new)
    return item


@transaction.atomic
def bulk_followup(user,task_ids,action,reason):
    require_manage(user)
    if action not in ['remind','escalate']: raise ValidationError('اختر التذكير أو التصعيد.')
    if not reason.strip(): raise ValidationError('سبب الإجراء الجماعي مطلوب.')
    try: ids=sorted({int(pk) for pk in task_ids})
    except (TypeError,ValueError): raise ValidationError('معرّفات المهام غير صحيحة.')
    if not ids or len(ids)>100 or any(pk<=0 for pk in ids): raise ValidationError('اختر من مهمة إلى 100 مهمة.')
    visible=set(tasks_for(user).filter(pk__in=ids).values_list('pk',flat=True))
    if visible!=set(ids): raise PermissionDenied('بعض المهام خارج نطاق صلاحياتك.')
    tasks=list(Task.objects.select_for_update(of=('self',)).filter(pk__in=ids).select_related('unit','owner').order_by('pk'))
    if len(tasks)!=len(ids): raise PermissionDenied('بعض المهام خارج نطاق صلاحياتك.')
    for task in tasks:
        require_manage(user,task)
        if task.status in TERMINAL: raise ValidationError('لا يمكن متابعة مهمة مغلقة أو ملغاة.')
    processed=0; skipped=0; today=timezone.localdate()
    label='تذكير جماعي' if action=='remind' else 'تصعيد جماعي'
    for task in tasks:
        if Audit.objects.filter(task=task,actor=user,action=label,created_at__date=today).exists():
            skipped+=1; continue
        if action=='remind': notify(task,f'{task.code}: تذكير متابعة — {reason}',key=f'bulk-remind:{user.pk}:{task.pk}:{today}')
        else: escalate(user,task,reason)
        audit(task,user,label,new={'reason':reason,'bulk':True})
        processed+=1
    return {'processed':processed,'skipped':skipped}
