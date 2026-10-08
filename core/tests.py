import io, tempfile
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,Client,override_settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError,transaction
from django.utils import timezone
from rest_framework.test import APIClient
from openpyxl import load_workbook
from .models import *
from .services import *
from .policy import tasks_for
from .views import kpis

@override_settings(DEBUG=True,SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False)
class AcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today=timezone.localdate()
        cls.unit=Unit.objects.create(name='وحدة التقنية والتحول الرقمي')
        cls.other_unit=Unit.objects.create(name='وحدة الموارد البشرية')
        def user(name,role,unit):
            u=User.objects.create_user(username=name,email=name+'@example.invalid',password='Test-Only-Strong-Password!',employee_number=name,role=role,first_name=name,phone='+9665'+str(len(User.objects.all())).zfill(8)); u.units.add(unit); return u
        cls.executive=user('executive',Role.EXECUTIVE,cls.unit)
        cls.admin=user('admin',Role.ADMIN,cls.unit)
        cls.assistant=user('assistant',Role.ASSISTANT,cls.unit)
        cls.head=user('head',Role.HEAD,cls.unit); cls.unit.head=cls.head; cls.unit.save()
        cls.owner=user('owner',Role.EMPLOYEE,cls.unit)
        cls.participant=user('participant',Role.EMPLOYEE,cls.unit)
        cls.outsider=user('outsider',Role.EMPLOYEE,cls.other_unit)
        cls.other_head=user('otherhead',Role.HEAD,cls.other_unit); cls.other_unit.head=cls.other_head; cls.other_unit.save()
        cls.viewer=user('viewer',Role.VIEWER,cls.unit)
    def setUp(self):
        self.api=APIClient(); self.api.force_authenticate(self.executive)
        self.media=tempfile.TemporaryDirectory(); self.override=override_settings(MEDIA_ROOT=self.media.name); self.override.enable()
        self.addCleanup(self.override.disable); self.addCleanup(self.media.cleanup)
    def data(self,**kwargs):
        return {'title':'إطلاق البرنامج التدريبي','description':'إعداد المحتوى والتنسيق','unit':self.unit,'owner':self.owner,'start_date':self.today-timedelta(days=2),'due_date':self.today+timedelta(days=5),'expected_result':'محتوى معتمد','success_indicator':'اعتماد رئيس الوحدة',**kwargs}
    def task(self,**kwargs): return create_task(self.executive,self.data(**kwargs),[self.participant])
    def payload(self,**kwargs):
        d=self.data(**kwargs)
        return {k:(str(v) if hasattr(v,'isoformat') else v.pk if hasattr(v,'pk') else v) for k,v in d.items()}|{'participants':[self.participant.pk]}
    def progress(self,task,p): return update_progress(self.owner,task,p,'تحديث التنفيذ','تم تجهيز المحتوى','لا يوجد' if p==100 else 'بقية المحتوى')
    def obstacle(self,task):
        return report_obstacle(self.owner,task,{'kind':'نقص موارد','description':'ميزانية المورد غير معتمدة','reason':'الموارد غير معتمدة','caused_by':'المورد','needs_decision':True,'requested_action':'اعتماد الميزانية','intervention_owner':self.head,'expected_resolution':self.today+timedelta(days=1),'impact':'high'})
    def test_complete_required_journey_over_api(self):
        response=self.api.post('/api/tasks/',self.payload(),format='json'); self.assertEqual(response.status_code,201,response.data)
        pk=response.data['id']; task=Task.objects.get(pk=pk)
        self.assertTrue(Notification.objects.filter(task=task,recipient=self.owner).exists())
        self.assertTrue(Notification.objects.filter(task=task,recipient=self.participant).exists())
        self.api.force_authenticate(self.owner)
        self.assertEqual(self.api.get(f'/api/tasks/{pk}/').status_code,200)
        update={'progress':50,'comment':'أنجزنا النصف','accomplished':'إعداد المحتوى','remaining':'اعتماد الميزانية'}
        self.assertEqual(self.api.post(f'/api/tasks/{pk}/progress/',update,format='json').status_code,200)
        upload=SimpleUploadedFile('evidence.pdf',b'%PDF-1.4\nTest evidence',content_type='application/pdf')
        file_response=self.api.post(f'/api/tasks/{pk}/attachments/',{'file':upload},format='multipart'); self.assertEqual(file_response.status_code,201,file_response.data)
        obstacle_data={'kind':'اعتماد مالي','description':'بانتظار الاعتماد','reason':'لم يصدر الاعتماد المالي','caused_by':'المورد','needs_decision':True,'requested_action':'اعتماد الميزانية','intervention_owner':self.head.pk,'expected_resolution':str(self.today),'impact':'high'}
        blocked=self.api.post(f'/api/tasks/{pk}/obstacles/',obstacle_data,format='json'); self.assertEqual(blocked.status_code,201,blocked.data)
        self.assertTrue(Notification.objects.filter(task=task,recipient=self.head,text__contains='تعثر').exists())
        self.api.force_authenticate(self.head)
        self.assertEqual(self.api.post(f'/api/tasks/{pk}/comments/',{'body':'تم الاعتماد والتنسيق'},format='json').status_code,201)
        self.assertEqual(self.api.post(f'/api/tasks/{pk}/resolve-obstacle/',{'obstacle':blocked.data['id'],'resolution':'اعتماد الميزانية'},format='json').status_code,200)
        self.api.force_authenticate(self.owner); update.update(progress=100,remaining='لا يوجد')
        complete=self.api.post(f'/api/tasks/{pk}/progress/',update,format='json'); self.assertEqual(complete.status_code,200,complete.data); self.assertEqual(complete.data['status'],Status.APPROVAL)
        self.api.force_authenticate(self.head)
        closed=self.api.post(f'/api/tasks/{pk}/transition/',{'status':Status.CLOSED,'comment':'اعتماد الإنجاز'},format='json'); self.assertEqual(closed.status_code,200,closed.data)
        self.assertEqual(self.api.get('/api/metrics/').data['closed'],1)
        self.assertGreaterEqual(Audit.objects.filter(task=task).count(),7)
        self.client.force_login(self.head); self.assertEqual(self.client.get('/reports/?report=closed&export=xlsx').status_code,200)
    def test_decision_to_multiple_tasks_and_approval(self):
        m=Meeting.objects.create(number='M01',name='اجتماع تنفيذي',committee='اللجنة التنفيذية',date=self.today)
        d=Decision.objects.create(number='D01',meeting=m,text='إطلاق برنامج تدريبي',date=self.today,followup_owner=self.executive)
        for title in ['إعداد المحتوى','تجهيز الميزانية']:
            payload=self.payload(title=title); response=self.api.post(f'/api/decisions/{d.pk}/convert-to-task/',payload,format='json'); self.assertEqual(response.status_code,201,response.data)
        tasks=list(d.tasks.all()); self.progress(tasks[0],100); tasks[0].refresh_from_db(); change_status(self.head,tasks[0],Status.CLOSED,'تم الاعتماد')
        self.assertEqual(d.progress,50); self.assertEqual(d.approved_progress,50)
        self.assertEqual(tasks[0].decision_id,d.pk); self.assertEqual(tasks[0].source,Source.DECISION)
    def test_employee_and_other_head_cannot_access_task_or_files(self):
        task=self.task(); a=attach(self.owner,task,SimpleUploadedFile('proof.txt',b'proof'))
        for u in [self.outsider,self.other_head]:
            self.api.force_authenticate(u)
            self.assertEqual(self.api.get(f'/api/tasks/{task.pk}/').status_code,404)
            self.assertEqual(self.api.post(f'/api/tasks/{task.pk}/progress/',{'progress':20}).status_code,404)
            self.client.force_login(u); self.assertEqual(self.client.get(f'/files/{a.pk}/').status_code,404)
    def test_viewer_is_read_only_and_secrets_are_scoped(self):
        normal=self.task(); secret=self.task(confidentiality='secret')
        self.api.force_authenticate(self.viewer)
        self.assertEqual(self.api.get(f'/api/tasks/{normal.pk}/').status_code,200)
        self.assertEqual(self.api.get(f'/api/tasks/{secret.pk}/').status_code,404)
        self.assertEqual(self.api.post(f'/api/tasks/{normal.pk}/comments/',{'body':'غير مصرح'}).status_code,403)
        self.assertEqual(self.api.patch(f'/api/tasks/{normal.pk}/',{'title':'غير مصرح','reason':'تعديل'}).status_code,403)
    def test_primary_owner_required_and_must_be_in_lead_unit(self):
        payload=self.payload(); payload.pop('owner'); self.assertEqual(self.api.post('/api/tasks/',payload,format='json').status_code,400)
        payload=self.payload(owner=self.outsider); self.assertEqual(self.api.post('/api/tasks/',payload,format='json').status_code,400)
    def test_cannot_set_status_or_progress_via_generic_update(self):
        t=self.task(); self.api.force_authenticate(self.owner)
        response=self.api.patch(f'/api/tasks/{t.pk}/',{'status':'closed','progress':100,'reason':'تجاوز'},format='json'); self.assertEqual(response.status_code,403)
        t.refresh_from_db(); self.assertEqual(t.progress,0); self.assertEqual(t.status,Status.ASSIGNED)
    def test_owner_cannot_approve_and_open_obstacle_prevents_submission(self):
        t=self.task(); self.obstacle(t)
        with self.assertRaises(ValidationError): self.progress(t,100)
        t.refresh_from_db(); self.assertEqual(t.status,Status.ASSIGNED)
        o=t.obstacles.first(); resolve_obstacle(self.head,o,'اعتماد الموارد'); self.progress(t,100); t.refresh_from_db()
        self.api.force_authenticate(self.owner); self.assertEqual(self.api.post(f'/api/tasks/{t.pk}/transition/',{'status':'closed','comment':'اعتماد ذاتي'}).status_code,403)
    def test_approval_rejection_keeps_history_and_resubmission(self):
        t=self.task(); self.progress(t,100); t.refresh_from_db(); change_status(self.head,t,Status.RETURNED,'أكمل الشواهد'); t.refresh_from_db()
        self.assertEqual(t.status,Status.RETURNED); self.assertEqual(t.progress,99); self.assertIsNone(t.submitted_at)
        self.progress(t,100); t.refresh_from_db(); change_status(self.head,t,Status.CLOSED,'تمت المراجعة')
        self.assertEqual(t.updates.count(),2); self.assertEqual(kpis(Task.objects.all())['closed'],1)
    def test_invalid_workflow_and_edit_closed_rejected(self):
        t=self.task()
        with self.assertRaises(ValidationError): change_status(self.head,t,Status.CLOSED,'انتقال غير مسموح')
        self.progress(t,100); t.refresh_from_db(); change_status(self.head,t,Status.CLOSED,'اعتماد'); t.refresh_from_db()
        with self.assertRaises(ValidationError): edit_task(self.executive,t,{'title':'تغيير'},[],[],'تعديل')
    def test_subtasks_calculate_progress_and_enforce_ownership(self):
        t=self.task(auto_progress=True)
        one=save_subtask(self.head,t,{'title':'المحتوى','owner':self.owner,'due_date':t.due_date,'progress':50,'comment':'تحديث'})
        two=save_subtask(self.head,t,{'title':'الإعلان','owner':self.participant,'due_date':t.due_date,'progress':0,'comment':'بدء'})
        t.refresh_from_db(); self.assertEqual(t.progress,25)
        with self.assertRaises(ValidationError): self.progress(t,90)
        one=save_subtask(self.owner,t,{'title':one.title,'owner':self.owner,'due_date':t.due_date,'progress':100,'comment':'اكتمل'},one)
        two=save_subtask(self.participant,t,{'title':two.title,'owner':self.participant,'due_date':t.due_date,'progress':100,'comment':'اكتمل'},two)
        t.refresh_from_db(); self.assertEqual(t.progress,100); self.assertEqual(t.status,Status.APPROVAL)
    def test_thread_and_mentions_reject_cross_task_references(self):
        t=self.task(); other=self.task(); c=add_comment(self.owner,other,'رد')
        with self.assertRaises(ValidationError): add_comment(self.owner,t,'رد',parent=c)
        with self.assertRaises(ValidationError): add_comment(self.owner,t,'إشارة',mentions=[self.outsider])
        root=add_comment(self.owner,t,'تحديث',mentions=[self.participant]); reply=add_comment(self.head,t,'اطلعت',parent=root)
        self.assertEqual(reply.parent_id,root.pk)
    def test_file_content_checks_and_private_download(self):
        t=self.task()
        for name,body in [('x.pdf',b'<html>bad</html>'),('x.svg',b'<svg/>'),('x.docx',b'PK-invalid'),('x.txt',b'\xff\xff')]:
            with self.assertRaises(ValidationError): attach(self.owner,t,SimpleUploadedFile(name,body))
        a=attach(self.owner,t,SimpleUploadedFile('report.txt',b'content'))
        self.client.force_login(self.owner); response=self.client.get(f'/files/{a.pk}/'); self.assertEqual(response.status_code,200); self.assertIn('attachment;',response['Content-Disposition'])
        self.assertEqual(self.client.get('/protected-media/'+a.file.name).status_code,404)
    def test_rules_preserve_blocked_state_and_deduplicate_escalations(self):
        t=self.task(start_date=self.today-timedelta(days=14),due_date=self.today-timedelta(days=8),priority=Priority.CRITICAL)
        self.obstacle(t); Task.objects.filter(pk=t.pk).update(updated_at=timezone.now()-timedelta(days=6)); t.refresh_from_db()
        run_rules(); t.refresh_from_db(); self.assertEqual(t.status,Status.ASSIGNED); self.assertTrue(t.is_overdue)
        self.assertIn('بدون تحديث',flags(t)); self.assertTrue(Escalation.objects.filter(task=t,recipient=self.executive).exists())
        before=Notification.objects.count(); escalation_count=Escalation.objects.count(); run_rules(); self.assertEqual(Notification.objects.count(),before); self.assertEqual(Escalation.objects.count(),escalation_count)
    def test_rules_mark_overdue_without_faking_progress_update(self):
        t=self.task(start_date=self.today-timedelta(days=10),due_date=self.today-timedelta(days=1)); timestamp=t.updated_at
        run_rules(); t.refresh_from_db(); self.assertEqual(t.status,Status.ASSIGNED); self.assertEqual(t.updated_at,timestamp)
    def test_rule_configuration_changes_risk_detection(self):
        t=self.task(start_date=self.today-timedelta(days=8),due_date=self.today+timedelta(days=2)); r=RuleSettings.current()
        self.assertIn('مرشحة للتعثر',flags(t,r)); r.risk_elapsed=90; r.save(); self.assertNotIn('مرشحة للتعثر',flags(t,r))
    def test_audit_immutable_at_model_and_database_levels(self):
        t=self.task(); a=t.activities.first()
        with self.assertRaises(ValidationError): a.delete()
        with self.assertRaises(ValidationError): a.save()
        with self.assertRaises(DatabaseError),transaction.atomic(): Audit.objects.filter(pk=a.pk).update(action='تلاعب')
        with self.assertRaises(DatabaseError),transaction.atomic(): Audit.objects.filter(pk=a.pk).delete()
    def test_reports_scope_and_formula_injection_prevention(self):
        visible=self.task(title='=DANGEROUS()'); hidden=create_task(self.executive,self.data(title='مهمة سرية خارج الوحدة',unit=self.other_unit,owner=self.outsider),[])
        self.client.force_login(self.owner)
        csv_response=self.client.get('/reports/?report=overdue&export=csv'); self.assertEqual(csv_response.status_code,200)
        xlsx=self.client.get('/reports/?report=stale&export=xlsx'); self.assertEqual(xlsx.status_code,200)
        self.client.force_login(self.executive)
        export=self.client.get('/reports/?report=units&export=xlsx'); book=load_workbook(io.BytesIO(export.content)); self.assertTrue(book.active.sheet_view.rightToLeft)
        self.assertEqual(__import__('core.views',fromlist=['safe_cell']).safe_cell('=DANGEROUS()'),"'=DANGEROUS()")
        self.assertNotIn(hidden.pk,list(tasks_for(self.owner).values_list('pk',flat=True)))
    def test_auth_email_phone_and_rate_limit(self):
        for identifier in [self.owner.email,self.owner.phone]:
            client=Client(); response=client.post('/login/',{'username':identifier,'password':'Test-Only-Strong-Password!'}); self.assertEqual(response.status_code,302)
        c=Client()
        for i in range(5): c.post('/login/',{'username':'bad@example.invalid','password':'wrong'})
        response=c.post('/login/',{'username':'bad@example.invalid','password':'wrong'}); self.assertContains(response,'محاولات كثيرة')
    def test_csrf_on_html_and_api_writes(self):
        t=self.task(); c=Client(enforce_csrf_checks=True); c.force_login(self.owner)
        self.assertEqual(c.post(f'/tasks/{t.pk}/',{'action':'comment','body':'تجاوز'}).status_code,403)
        self.assertEqual(c.post(f'/api/tasks/{t.pk}/comments/',{'body':'تجاوز'}).status_code,403)
    def test_notification_read_is_owner_scoped(self):
        t=self.task(); n=Notification.objects.filter(task=t,recipient=self.owner).first()
        self.api.force_authenticate(self.outsider); self.assertEqual(self.api.patch(f'/api/notifications/{n.pk}/',{'read':True}).status_code,404)
        n.refresh_from_db(); self.assertFalse(n.read)
    def test_admin_management_and_role_escalation_blocked(self):
        self.client.force_login(self.owner)
        for url in ['/settings/','/manage/employee/new/','/manage/unit/new/','/manage/decision/new/']:
            self.assertEqual(self.client.get(url).status_code,403)
        self.api.force_authenticate(self.owner); self.assertEqual(self.api.post('/api/units/',{'name':'غير مصرح'}).status_code,403)
    def test_all_html_pages_and_reports_render_for_roles(self):
        t=self.task(); self.obstacle(t)
        from .views import REPORTS
        for user in [self.executive,self.assistant,self.head,self.owner,self.viewer]:
            self.client.force_login(user)
            for url in ['/','/tasks/','/followup/','/decisions/','/directory/','/notifications/',f'/tasks/{t.pk}/']:
                response=self.client.get(url); self.assertEqual(response.status_code,200,f'{user.role}: {url}')
        self.client.force_login(self.executive)
        for kind,label in REPORTS: self.assertEqual(self.client.get('/reports/?report='+kind).status_code,200,kind)
        for url in ['/tasks/new/','/manage/meeting/new/','/manage/decision/new/',f'/tasks/{t.pk}/edit/']:
            self.assertEqual(self.client.get(url).status_code,200,url)
    def test_seed_has_required_arabic_data(self):
        from django.core.management import call_command
        with patch.dict('os.environ',{'DEMO_PASSWORD':'Temporary-Demo-Password!'}): call_command('seed_demo',stdout=io.StringIO())
        self.assertEqual(Task.objects.filter(title__endswith='— تجريبي').count(),30)
        self.assertGreaterEqual(User.objects.filter(username__startswith='demo').count(),15)
        self.assertEqual(Unit.objects.count(),16)
        self.assertTrue(Task.objects.filter(status=Status.CLOSED).exists()); self.assertTrue(Task.objects.filter(obstacles__resolved_at__isnull=True,obstacles__isnull=False).exists())
    def test_assign_to_entire_unit_notifies_all_active_members(self):
        t=create_task(self.executive,self.data(assignment_mode='unit'))
        self.assertTrue(t.participants.filter(pk=self.participant.pk).exists())
        self.assertTrue(Notification.objects.filter(task=t,recipient=self.participant).exists())
        self.assertFalse(t.participants.filter(pk=self.viewer.pk).exists())
        self.assertTrue(tasks_for(self.participant).filter(pk=t.pk).exists())
    def test_multiple_unit_assignment_preserves_one_primary_owner(self):
        t=create_task(self.executive,self.data(assignment_mode='units'),[],[self.other_unit])
        self.assertEqual(t.owner_id,self.owner.pk)
        self.assertTrue(t.participants.filter(pk=self.outsider.pk).exists())
        self.assertTrue(tasks_for(self.outsider).filter(pk=t.pk).exists())
    def test_catalog_administration_and_display_labels(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/settings/catalogs/').status_code,200)
        response=self.client.post('/settings/catalogs/',{'kind':'priority','key':'critical','label':'حرجة جدًا','active':'on'})
        self.assertEqual(response.status_code,302)
        t=self.task(priority=Priority.CRITICAL); self.assertEqual(t.get_priority_display(),'حرجة جدًا')
        self.assertEqual(self.client.post('/settings/catalogs/',{'kind':'status','key':'closed','label':'مغلقة'}).status_code,200)
        self.assertTrue(Catalog.objects.get(kind='status',key='closed').active)
        self.assertEqual(self.client.get('/settings/permissions/').status_code,200)
        self.client.force_login(self.owner); self.assertEqual(self.client.get('/settings/catalogs/').status_code,403)

    def test_deadline_labels_include_approval_and_hide_terminal(self):
        for delta,label in [(-3,'متأخرة 3 يوم'),(0,'تستحق اليوم'),(2,'متبقي 2 يوم')]:
            t=self.task(start_date=self.today-timedelta(days=5),due_date=self.today+timedelta(days=delta))
            for state in Status.values:
                t.status=state
                self.assertEqual(t.deadline_label,'' if state in TERMINAL else label,state)
                self.assertEqual(t.is_overdue,delta<0 and state not in TERMINAL,state)
        self.assertNotIn('blocked',Status.values); self.assertNotIn('overdue',Status.values)

    def test_approval_is_overdue_in_list_followup_metrics_and_export(self):
        t=self.task(start_date=self.today-timedelta(days=5),due_date=self.today-timedelta(days=2))
        self.progress(t,100); t.refresh_from_db()
        self.assertEqual(t.status,Status.APPROVAL)
        self.assertIn('متأخرة',flags(t)); self.assertEqual(kpis(tasks_for(self.head))['overdue'],1)
        self.client.force_login(self.head)
        for url in ['/tasks/','/followup/?tab=overdue',f'/tasks/{t.pk}/']:
            response=self.client.get(url); self.assertContains(response,'متأخرة 2 يوم'); self.assertContains(response,'بانتظار الاعتماد')
            self.assertNotContains(response,'متبقي -'); self.assertNotContains(response,'<option value="overdue"'); self.assertNotContains(response,'<option value="blocked"')
        csv=self.client.get('/reports/?report=overdue&export=csv').content.decode('utf-8-sig')
        self.assertIn(t.title,csv); self.assertIn('بانتظار الاعتماد',csv)
        run_rules(); t.refresh_from_db(); self.assertEqual(t.status,Status.APPROVAL)
        self.api.force_authenticate(self.head); data=self.api.get(f'/api/tasks/{t.pk}/').data
        self.assertTrue(data['is_overdue']); self.assertEqual(data['deadline_label'],'متأخرة 2 يوم')

    def test_closed_and_cancelled_do_not_render_counter(self):
        t=self.task(start_date=self.today-timedelta(days=5),due_date=self.today-timedelta(days=2))
        self.client.force_login(self.head)
        for state in TERMINAL:
            Task.objects.filter(pk=t.pk).update(status=state,progress=100 if state==Status.CLOSED else 0)
            for url in ['/tasks/',f'/tasks/{t.pk}/']:
                response=self.client.get(url); self.assertNotContains(response,'data-deadline')
                self.assertNotContains(response,'متأخرة 2 يوم')
        Task.objects.filter(pk=t.pk).update(status=Status.ACTIVE,progress=0,due_date=self.today)
        self.assertContains(self.client.get('/tasks/'),'تستحق اليوم')
        self.assertNotContains(self.client.get('/tasks/'),'متبقي 0 يوم')

    def test_obstacle_and_lateness_preserve_waiting_status(self):
        t=self.task(start_date=self.today-timedelta(days=6),due_date=self.today-timedelta(days=1))
        change_status(self.owner,t,Status.WAITING,'بانتظار رد المورد'); t.refresh_from_db()
        first=self.obstacle(t); second=self.obstacle(t); t.refresh_from_db()
        self.assertEqual(t.status,Status.WAITING); self.assertTrue(t.is_blocked)
        run_rules(); t.refresh_from_db(); self.assertEqual(t.status,Status.WAITING)
        self.assertIn('متأخرة',flags(t)); self.assertIn('متعثرة',flags(t))
        self.client.force_login(self.head)
        self.assertEqual(self.client.get('/followup/?tab=blocked').context['page'].paginator.count,1)
        self.assertContains(self.client.get('/followup/?tab=blocked'),'بانتظار رد')
        resolve_obstacle(self.head,first,'اعتماد الميزانية'); t.refresh_from_db(); self.assertTrue(t.is_blocked)
        resolve_obstacle(self.head,second,'توفير الموارد'); t.refresh_from_db()
        self.assertFalse(t.is_blocked); self.assertEqual(t.status,Status.WAITING)
        self.assertNotIn('متعثرة',flags(t)); self.assertIn('متأخرة',flags(t))
        self.assertEqual(self.client.get('/followup/?tab=blocked').context['page'].paginator.count,0)

    def test_admin_cannot_execute_or_approve_even_when_participant(self):
        from django.core.exceptions import PermissionDenied
        t=self.task(); t.participants.add(self.admin); self.progress(t,100); t.refresh_from_db()
        self.assertFalse(tasks_for(self.admin).exists())
        for operation in [lambda: change_status(self.admin,t,Status.CLOSED,'اعتماد'),lambda: change_status(self.admin,t,Status.RETURNED,'إعادة'),lambda: update_progress(self.admin,t,50,'تحديث','عمل','باقي'),lambda: create_task(self.admin,self.data()),lambda: add_comment(self.admin,t,'تعليق')]:
            with self.assertRaises(PermissionDenied): operation()
        self.api.force_authenticate(self.admin)
        self.assertEqual(self.api.post(f'/api/tasks/{t.pk}/transition/',{'status':'closed','comment':'اعتماد'}).status_code,404)
        self.assertEqual(self.api.post('/api/tasks/',self.payload(),format='json').status_code,403)
        self.assertEqual(self.api.get('/api/metrics/').status_code,403)
        self.assertEqual(self.api.get('/api/decisions/').data['count'],0)
        self.client.force_login(self.admin)
        for url in ['/tasks/','/followup/','/reports/','/decisions/','/notifications/','/tasks/new/','/manage/decision/new/','/manage/meeting/new/']:
            self.assertEqual(self.client.get(url).status_code,403,url)
        self.assertEqual(self.client.get(f'/tasks/{t.pk}/').status_code,403)
        for url in ['/','/directory/','/settings/','/settings/catalogs/','/settings/permissions/','/manage/unit/new/','/manage/employee/new/']:
            self.assertEqual(self.client.get(url).status_code,200,url)
        dashboard=self.client.get('/'); self.assertContains(dashboard,'إدارة المنصة'); self.assertNotContains(dashboard,'href="/tasks/'); self.assertNotContains(dashboard,'href="/decisions/')
        self.assertEqual(self.api.post('/api/units/',{'name':'وحدة اختبار'},format='json').status_code,201)
        t.refresh_from_db(); self.assertEqual(t.status,Status.APPROVAL)

    def test_admin_cannot_be_assigned_as_operational_owner(self):
        with self.assertRaises(ValidationError): self.task(owner=self.admin)
        t=self.task(assignment_mode='unit')
        self.assertFalse(t.participants.filter(pk=self.admin.pk).exists())
        self.assertFalse(can_work(self.admin,t)); self.assertFalse(can_manage(self.admin,t))

    def test_admin_superuser_cannot_access_operational_django_admin(self):
        from django.contrib import admin
        from django.test import RequestFactory
        self.admin.is_staff=True; self.admin.is_superuser=True; self.admin.save()
        request=RequestFactory().get('/admin/'); request.user=self.admin
        self.assertFalse(admin.site._registry[Task].has_view_permission(request))
        self.assertFalse(admin.site._registry[Task].has_change_permission(request))
        self.assertTrue(admin.site._registry[Unit].has_view_permission(request))

    def decision_data(self,**kwargs):
        meeting=Meeting.objects.create(number='MEETING-'+str(Meeting.objects.count()),name='اجتماع المجلس',committee='مجلس الإدارة',date=self.today)
        return {'number':'DECISION-'+str(Decision.objects.count()),'meeting':meeting,'text':'اعتماد برنامج تنمية المهارات','date':self.today,'due_date':self.today+timedelta(days=10),'issuing_authority':'board','status':DecisionStatus.OPEN,'followup_owner':self.executive,**kwargs}

    def test_decision_fields_form_and_protected_minutes(self):
        data=self.decision_data(approved_minutes_number='MIN-01')
        payload={key:str(value) if hasattr(value,'isoformat') else value.pk if hasattr(value,'pk') else value for key,value in data.items()}
        payload['minutes_file']=SimpleUploadedFile('minutes.pdf',b'%PDF-1.4\nApproved minutes')
        self.client.force_login(self.executive)
        response=self.client.post('/manage/decision/new/',payload)
        self.assertEqual(response.status_code,302,response.content.decode()[:300])
        d=Decision.objects.get(number=data['number']); self.assertIsNotNone(d.approved_minutes_attachment_id)
        self.assertEqual(d.approved_minutes_attachment.decision_id,d.pk)
        self.assertEqual(d.issuing_authority,'board'); self.assertEqual(d.due_date,data['due_date'])
        self.assertTrue(d.activities.filter(action='إنشاء القرار',actor=self.executive).exists())
        self.assertContains(self.client.get(f'/decisions/{d.pk}/'),'مرفق المحضر المعتمد')
        self.assertEqual(self.client.get(f'/files/{d.approved_minutes_attachment_id}/').status_code,200)
        self.client.force_login(self.outsider); self.assertEqual(self.client.get(f'/files/{d.approved_minutes_attachment_id}/').status_code,404)
        self.client.force_login(self.admin); self.assertEqual(self.client.get('/manage/decision/new/').status_code,403)

    def test_decision_status_due_changes_require_reason_and_audit(self):
        d=save_decision(self.executive,self.decision_data())
        self.api.force_authenticate(self.executive)
        target=self.today+timedelta(days=15)
        response=self.api.patch(f'/api/decisions/{d.pk}/',{'status':'amended','due_date':str(target)},format='json')
        self.assertEqual(response.status_code,400)
        d.refresh_from_db(); self.assertEqual(d.status,DecisionStatus.OPEN)
        response=self.api.patch(f'/api/decisions/{d.pk}/',{'status':'amended','due_date':str(target),'reason':'تمديد بقرار المجلس'},format='json')
        self.assertEqual(response.status_code,200,response.data)
        event=d.activities.filter(action='تعديل القرار').first()
        self.assertEqual(event.old['status'],'open'); self.assertEqual(event.new['status'],'amended')
        self.assertEqual(event.old['due_date'],str(self.today+timedelta(days=10))); self.assertEqual(event.new['due_date'],str(target))
        self.assertEqual(event.actor,self.executive); self.assertEqual(event.reason,'تمديد بقرار المجلس')
        self.client.force_login(self.executive); page=self.client.get(f'/decisions/{d.pk}/')
        for text in ['سجل تدقيق القرار','تمديد بقرار المجلس','قبل التغيير','بعد التغيير','معدّل']: self.assertContains(page,text)
        for state in DecisionStatus.values:
            changed=save_decision(self.executive,{'status':state},d,'تغيير موثق'); self.assertEqual(changed.status,state)
        with self.assertRaises(DatabaseError),transaction.atomic(): Audit.objects.filter(pk=event.pk).update(new={})
        with self.assertRaises(DatabaseError),transaction.atomic(): Audit.objects.filter(pk=event.pk).delete()
        self.assertEqual(self.api.delete(f'/api/decisions/{d.pk}/').status_code,405)
        self.assertEqual(self.api.patch(f'/api/decisions/{d.pk}/audit/',{'new':{}},format='json').status_code,405)

    def test_decision_metrics_and_audit_do_not_reveal_hidden_tasks(self):
        d=save_decision(self.executive,self.decision_data())
        visible=self.task(decision=d); hidden=create_task(self.executive,self.data(decision=d,unit=self.other_unit,owner=self.outsider,confidentiality='secret'))
        self.progress(visible,50)
        update_progress(self.outsider,hidden,100,'تسليم سري','اكتمل','لا يوجد'); hidden.refresh_from_db()
        change_status(self.other_head,hidden,Status.CLOSED,'اعتماد سري')
        for user,expected in [(self.executive,(75,50,True)),(self.assistant,(75,50,True)),(self.owner,(50,0,False))]:
            self.client.force_login(user); page=self.client.get(f'/decisions/{d.pk}/'); m=page.context['metrics']
            self.assertEqual((m['progress'],m['approved_progress'],m['full_scope']),expected)
            listing=self.client.get('/decisions/'); row=listing.context['decisions'][0]
            self.assertEqual((row.visible_progress,row.visible_approved_progress),expected[:2])
            self.api.force_authenticate(user); data=self.api.get(f'/api/decisions/{d.pk}/').data
            self.assertEqual((data['progress'],data['approved_progress']),expected[:2])
            events=self.api.get(f'/api/decisions/{d.pk}/audit/').data
            if user==self.owner:
                self.assertTrue(events); self.assertFalse(any(e['new'].get('derived') for e in events))
                self.assertNotContains(page,'اعتماد سري'); self.assertNotContains(page,'تسليم سري')
        self.api.force_authenticate(self.outsider)
        self.assertEqual(self.api.patch(f'/api/decisions/{d.pk}/',{'status':'cancelled','reason':'غير مصرح'},format='json').status_code,403)
        self.api.force_authenticate(self.admin); self.assertEqual(self.api.get(f'/api/decisions/{d.pk}/audit/').status_code,404)

    def test_task_percentage_status_due_audit_includes_actor_time_reason(self):
        d=save_decision(self.executive,self.decision_data()); t=self.task(decision=d)
        self.progress(t,50); t.refresh_from_db()
        event=t.activities.filter(action='تحديث الإنجاز').first()
        self.assertEqual(event.old['progress'],0); self.assertEqual(event.new['progress'],50)
        self.assertEqual(event.actor,self.owner); self.assertTrue(event.created_at); self.assertEqual(event.reason,'تحديث التنفيذ')
        edit_task(self.executive,t,{'due_date':self.today+timedelta(days=7)},[self.participant],[],'موعد جديد مع المورد'); t.refresh_from_db()
        due_event=t.activities.filter(action='تعديل المهمة').first()
        self.assertNotEqual(due_event.old['due_date'],due_event.new['due_date'])
        self.assertEqual(due_event.reason,'موعد جديد مع المورد')
        self.progress(t,100); t.refresh_from_db(); change_status(self.head,t,Status.RETURNED,'نقص الشواهد'); t.refresh_from_db()
        returned=t.activities.filter(action='تغيير الحالة').first()
        self.assertEqual(returned.old['progress'],100); self.assertEqual(returned.new['progress'],99)
        metric_event=d.activities.filter(new__derived=True).first()
        self.assertEqual(metric_event.old['progress'],100); self.assertEqual(metric_event.new['progress'],99); self.assertEqual(metric_event.reason,'نقص الشواهد')
        self.progress(t,100); t.refresh_from_db(); change_status(self.head,t,Status.CLOSED,'اعتماد الشواهد'); t.refresh_from_db()
        metric_event=d.activities.filter(new__derived=True).first()
        self.assertEqual(metric_event.old['approved_progress'],0); self.assertEqual(metric_event.new['approved_progress'],100)
        self.client.force_login(self.head); page=self.client.get(f'/tasks/{t.pk}/')
        for text in ['سجل التدقيق','موعد جديد مع المورد','تحديث التنفيذ','اعتماد الشواهد','نسبة التنفيذ','تاريخ الاستحقاق']: self.assertContains(page,text)

    def test_decision_validation_and_minutes_replacement_preserves_history(self):
        d=save_decision(self.executive,self.decision_data(approved_minutes_number='MIN-01'),minutes_file=SimpleUploadedFile('first.pdf',b'%PDF-1.4\nfirst'))
        old_file=d.approved_minutes_attachment_id
        with self.assertRaises(ValidationError): save_decision(self.executive,{'due_date':self.today-timedelta(days=1)},d,'موعد غير صحيح')
        with self.assertRaises(ValidationError): save_decision(self.executive,self.decision_data(approved_minutes_number='MIN-MISSING'))
        with self.assertRaises(ValidationError): attach_decision(self.executive,d,SimpleUploadedFile('bad.pdf',b'invalid'),'استبدال',True)
        self.api.force_authenticate(self.executive)
        response=self.api.post(f'/api/decisions/{d.pk}/attachments/',{'file':SimpleUploadedFile('second.pdf',b'%PDF-1.4\nsecond'),'approved_minutes':'true','reason':'اعتماد نسخة مصححة'},format='multipart')
        self.assertEqual(response.status_code,201,response.data); d.refresh_from_db()
        self.assertNotEqual(d.approved_minutes_attachment_id,old_file)
        self.assertTrue(d.attachments.filter(pk=old_file).exists()); self.assertEqual(d.attachments.count(),2)
        e=d.activities.filter(action='إرفاق المحضر المعتمد').first()
        self.assertEqual(e.old['approved_minutes_attachment_id'],old_file); self.assertEqual(e.reason,'اعتماد نسخة مصححة')
        self.api.force_authenticate(self.owner)
        self.assertEqual(self.api.post('/api/decisions/',{},format='json').status_code,400)

    def test_api_decision_creation_uses_audited_service(self):
        data=self.decision_data(issuing_authority='assembly')
        payload={key:str(value) if hasattr(value,'isoformat') else value.pk if hasattr(value,'pk') else value for key,value in data.items()}
        response=self.api.post('/api/decisions/',payload,format='json'); self.assertEqual(response.status_code,201,response.data)
        d=Decision.objects.get(pk=response.data['id']); self.assertTrue(d.activities.filter(action='إنشاء القرار').exists())
        self.assertEqual(response.data['issuing_authority_label'],'الجمعية العمومية')
        self.assertEqual(response.data['metric_scope'],'all')
        self.api.force_authenticate(self.owner); payload.update(number='UNAUTHORIZED')
        self.assertEqual(self.api.post('/api/decisions/',payload,format='json').status_code,403)

    def board_user(self):
        return User.objects.create_user(username='board',email='board@example.invalid',employee_number='BOARD',role=Role.BOARD)
    def test_board_can_read_decisions_and_execution_reports_only(self):
        board=self.board_user(); d=save_decision(self.executive,self.decision_data())
        linked=self.task(decision=d,confidentiality='secret'); independent=self.task()
        self.progress(linked,100); linked.refresh_from_db(); change_status(self.head,linked,Status.CLOSED,'اعتماد')
        self.api.force_authenticate(board)
        self.assertEqual(self.api.get(f'/api/decisions/{d.pk}/').data['approved_progress'],100)
        self.assertEqual(self.api.get(f'/api/decisions/{d.pk}/').data['metric_scope'],'all')
        self.assertEqual(self.api.get(f'/api/tasks/{independent.pk}/').status_code,404)
        for action,payload in [('progress',{'progress':20,'comment':'تغيير','accomplished':'عمل','remaining':'باقي'}),('transition',{'status':'returned','comment':'إعادة'}),('comments',{'body':'تعديل'})]:
            self.assertEqual(self.api.post(f'/api/tasks/{linked.pk}/{action}/',payload,format='json').status_code,403)
        self.assertEqual(self.api.patch(f'/api/decisions/{d.pk}/',{'status':'cancelled','reason':'تعديل'},format='json').status_code,403)
        self.client.force_login(board)
        for url in ['/decisions/',f'/decisions/{d.pk}/','/reports/?report=execution','/reports/?report=decisions&export=xlsx',f'/tasks/{linked.pk}/']:
            self.assertEqual(self.client.get(url).status_code,200,url)
        for url in ['/tasks/','/followup/','/directory/','/settings/','/reports/?report=employees','/tasks/new/']:
            self.assertEqual(self.client.get(url).status_code,403,url)
        page=self.client.get('/decisions/'); self.assertNotContains(page,'href="/tasks/new/"'); self.assertNotContains(page,'href="/followup/"')
        self.assertFalse(can_work(board,linked)); self.assertFalse(can_manage(board,linked))

    def test_followup_groups_differ_from_task_list_and_overlap(self):
        t=self.task(start_date=self.today-timedelta(days=10),due_date=self.today-timedelta(days=1)); self.obstacle(t)
        Task.objects.filter(pk=t.pk).update(updated_at=timezone.now()-timedelta(days=7)); escalate(self.owner,t,'طلب تدخل')
        self.client.force_login(self.head); page=self.client.get('/followup/')
        self.assertTemplateUsed(page,'core/followup.html')
        self.assertEqual([g['key'] for g in page.context['groups']],['overdue','blocked','stale','escalated'])
        self.assertTrue(all(g['count']==1 for g in page.context['groups']))
        self.assertContains(page,'مجموعات المخاطر وأولويات التدخل'); self.assertContains(page,'إجراء جماعي')
        self.assertTemplateUsed(self.client.get('/tasks/'),'core/tasks.html')
        self.client.force_login(self.owner); self.assertNotContains(self.client.get('/followup/'),'id="bulk-followup"')

    def test_bulk_reminders_are_unique_audited_and_do_not_change_progress(self):
        first=self.task(); second=self.task(); self.api.force_authenticate(self.head)
        payload={'task_ids':[first.pk,second.pk,first.pk],'action':'remind','reason':'تحديث موقف التنفيذ'}
        before=Notification.objects.count(); response=self.api.post('/api/followup/bulk/',payload,format='json')
        self.assertEqual(response.status_code,200,response.data); self.assertEqual(response.data['processed'],2)
        self.assertGreater(Notification.objects.count(),before)
        count=Notification.objects.count(); repeated=self.api.post('/api/followup/bulk/',payload,format='json')
        self.assertEqual(repeated.data,{'processed':0,'skipped':2}); self.assertEqual(Notification.objects.count(),count)
        first.refresh_from_db(); self.assertEqual(first.progress,0); self.assertEqual(first.status,Status.ASSIGNED)
        self.assertEqual(first.activities.filter(action='تذكير جماعي',new__reason='تحديث موقف التنفيذ').count(),1)
        payload.update(action='escalate',reason='تدخل الإدارة مطلوب')
        self.assertEqual(self.api.post('/api/followup/bulk/',payload,format='json').data['processed'],2)
        self.assertTrue(Escalation.objects.filter(task=second,recipient=self.executive).exists())

    def test_bulk_scope_and_required_reason_are_atomic(self):
        own=self.task(); outside=create_task(self.executive,self.data(unit=self.other_unit,owner=self.outsider))
        self.api.force_authenticate(self.head); before=Notification.objects.count()
        response=self.api.post('/api/followup/bulk/',{'task_ids':[own.pk,outside.pk],'action':'remind','reason':'متابعة'},format='json')
        self.assertEqual(response.status_code,403); self.assertEqual(Notification.objects.count(),before)
        for user in [self.owner,self.viewer,self.admin,self.board_user()]:
            self.api.force_authenticate(user)
            self.assertEqual(self.api.post('/api/followup/bulk/',{'task_ids':[own.pk],'action':'escalate','reason':'متابعة'},format='json').status_code,403)
        self.api.force_authenticate(self.head)
        self.assertEqual(self.api.post('/api/followup/bulk/',{'task_ids':[own.pk],'action':'remind','reason':' '},format='json').status_code,400)
        self.client.force_login(self.head)
        self.assertEqual(self.client.post('/followup/',{'task_ids':[own.pk],'action':'remind','reason':'تذكير HTML'}).status_code,302)

    def test_reasons_required_for_obstacle_extension_and_return(self):
        t=self.task()
        data={'kind':'نقص موارد','description':'وصف المشكلة','reason':' ','caused_by':'المورد','needs_decision':False,'requested_action':'تدخل','intervention_owner':self.head,'expected_resolution':self.today,'impact':'high'}
        with self.assertRaises(ValidationError): report_obstacle(self.owner,t,data)
        self.assertEqual(t.obstacles.count(),0)
        data['reason']='تأخر صرف الموارد'; report_obstacle(self.owner,t,data)
        self.assertEqual(t.activities.filter(action='تسجيل عائق').first().reason,'تأخر صرف الموارد')
        with self.assertRaises(ValidationError): edit_task(self.head,t,{'due_date':t.due_date+timedelta(days=2)},[],[],' ')
        t.refresh_from_db(); resolve_obstacle(self.head,t.obstacles.first(),'توفير الموارد'); self.progress(t,100); t.refresh_from_db()
        with self.assertRaises(ValidationError): change_status(self.head,t,Status.RETURNED,' ')
        t.refresh_from_db(); self.assertEqual(t.status,Status.APPROVAL)

    def test_status_progress_consistency_enforced_in_model_services_and_database(self):
        t=self.task()
        with self.assertRaises(ValidationError): change_status(self.owner,t,Status.APPROVAL,'غير متسق')
        for status,progress in [(Status.ACTIVE,100),(Status.APPROVAL,50),(Status.CLOSED,20),(Status.COMPLETED,99)]:
            t.status=status; t.progress=progress
            with self.assertRaises(ValidationError): t.validate_status_progress()
            with self.assertRaises(DatabaseError),transaction.atomic(): Task.objects.filter(pk=t.pk).update(status=status,progress=progress)
        t.refresh_from_db(); self.progress(t,100); t.refresh_from_db()
        self.assertEqual((t.status,t.progress),(Status.APPROVAL,100))
        change_status(self.head,t,Status.RETURNED,'نقص شواهد'); t.refresh_from_db()
        self.assertEqual((t.status,t.progress),(Status.RETURNED,99))

    def test_board_cannot_bypass_role_via_django_admin_permissions(self):
        from django.contrib import admin
        from django.contrib.auth.models import Permission
        from django.test import RequestFactory
        board=self.board_user(); board.is_staff=True; board.save()
        board.user_permissions.add(Permission.objects.get(content_type__app_label='core',codename='change_user'))
        self.assertTrue(board.has_perm('core.change_user'))
        request=RequestFactory().get('/admin/core/user/'); request.user=board
        self.assertFalse(admin.site._registry[User].has_change_permission(request,self.owner))
        self.assertFalse(admin.site._registry[Task].has_view_permission(request))

    def test_arabic_locale_uses_ascii_numbers_and_decimal_dot(self):
        from decimal import Decimal
        from django.template import Template,Context
        from django.utils import translation
        with translation.override('ar'):
            rendered=Template('{{ n }}|{{ n|floatformat:1 }}|{{ count }}').render(Context({'n':Decimal('14.0'),'count':1234}))
        self.assertEqual(rendered,'14.0|14.0|1234')

    @override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
    def test_demo_assignments_and_waiting_supplier_are_consistent(self):
        from django.core.management import call_command
        with patch.dict('os.environ',{'DEMO_PASSWORD':'Test-Demo-Only-Password!'}):
            call_command('seed_demo',stdout=io.StringIO())
        food=Task.objects.get(title='إعداد خطة السلال الغذائية — تجريبي')
        hr=Task.objects.get(title='تحديث سياسات الموارد البشرية — تجريبي')
        water=Task.objects.get(title='متابعة مبادرة سقيا الماء — تجريبي')
        self.assertEqual(food.unit.name,'وحدة الخدمات الرعوية')
        self.assertEqual(hr.unit.name,'وحدة الخدمات المساندة')
        self.assertEqual(water.unit.name,'وحدة الخدمات الرعوية')
        self.assertEqual((water.status,water.progress),(Status.WAITING,25))
        self.assertTrue(water.is_blocked)
        for task in Task.objects.filter(title__endswith=' — تجريبي'):
            self.assertTrue(task.owner.units.filter(pk=task.unit_id).exists())
            task.validate_status_progress()

    def test_real_api_session_upload_download_permissions_and_logout(self):
        t=self.task()
        c=APIClient(enforce_csrf_checks=True)
        self.assertEqual(c.get('/api/auth/me/').status_code,403)
        self.assertEqual(c.post('/api/auth/login/',{'username':self.owner.email,'password':'Test-Only-Strong-Password!'},format='json').status_code,403)
        token=c.get('/api/auth/csrf/').json()['csrf_token']
        result=c.post('/api/auth/login/',{'username':self.owner.email,'password':'Test-Only-Strong-Password!'},format='json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(result.status_code,200,result.content)
        token=result.json()['csrf_token']
        self.assertEqual(c.get('/api/auth/me/').json()['id'],self.owner.pk)
        self.assertEqual(c.post(f'/api/tasks/{t.pk}/attachments/',{'file':SimpleUploadedFile('proof.txt',b'actual upload')},format='multipart').status_code,403)
        result=c.post(f'/api/tasks/{t.pk}/attachments/',{'file':SimpleUploadedFile('proof.txt',b'actual upload')},format='multipart',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(result.status_code,201,result.content)
        download=c.get(result.json()['url'])
        self.assertEqual(b''.join(download.streaming_content),b'actual upload')
        self.assertEqual(c.post('/api/auth/logout/',{},format='json',HTTP_X_CSRFTOKEN=token).status_code,200)
        self.assertEqual(c.get('/api/auth/me/').status_code,403)
        token=c.get('/api/auth/csrf/').json()['csrf_token']
        result=c.post('/api/auth/login/',{'username':self.outsider.phone,'password':'Test-Only-Strong-Password!'},format='json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(c.get(f'/files/{Attachment.objects.get(task=t).pk}/').status_code,404)
        self.assertEqual(c.post(f'/api/tasks/{t.pk}/attachments/',{'file':SimpleUploadedFile('proof.txt',b'forbidden')},format='multipart',HTTP_X_CSRFTOKEN=result.json()['csrf_token']).status_code,404)
        self.assertEqual(Attachment.objects.filter(task=t).count(),1)

    def test_api_login_limit_shared_with_html_and_inactive_user_denied(self):
        c=APIClient(enforce_csrf_checks=True)
        token=c.get('/api/auth/csrf/').json()['csrf_token']
        for i in range(5):
            response=c.post('/api/auth/login/',{'username':' BAD@example.invalid ','password':'bad'},format='json',HTTP_X_CSRFTOKEN=token)
            self.assertEqual(response.status_code,401)
        self.assertEqual(c.post('/api/auth/login/',{'username':'bad@example.invalid','password':'bad'},format='json',HTTP_X_CSRFTOKEN=token).status_code,429)
        self.assertContains(self.client.post('/login/',{'username':'bad@example.invalid','password':'bad'}),'محاولات كثيرة')
        self.owner.is_active=False; self.owner.save()
        LoginAttempt.objects.all().delete()
        self.assertEqual(c.post('/api/auth/login/',{'username':self.owner.email,'password':'Test-Only-Strong-Password!'},format='json',HTTP_X_CSRFTOKEN=token).status_code,401)

    def test_scheduler_uses_exact_configured_days_and_includes_pending_approval(self):
        r=RuleSettings.current(); r.head_after=2; r.assistant_after=4; r.executive_after=6; r.save()
        t=self.task(start_date=self.today-timedelta(days=20),due_date=self.today)
        self.progress(t,100); t.refresh_from_db()
        for day,levels in [(1,[]),(2,[1]),(3,[1]),(4,[1,2]),(5,[1,2]),(6,[1,2,3])]:
            run_rules(today=self.today+timedelta(days=day))
            self.assertEqual(list(t.escalations.order_by('level').values_list('level',flat=True)),levels)
        before=Notification.objects.count(); run_rules(today=self.today+timedelta(days=6))
        self.assertEqual(Notification.objects.count(),before)
        t.refresh_from_db(); self.assertEqual((t.status,t.progress),(Status.APPROVAL,100))
        change_status(self.head,t,Status.CLOSED,'اعتماد')
        before=Notification.objects.count(); run_rules(today=self.today+timedelta(days=7))
        self.assertEqual(Notification.objects.count(),before)

@override_settings(DEBUG=True,SECURE_SSL_REDIRECT=False)
class SchedulerRuntimeTests(TestCase):
    def test_scheduler_command_executes_rules_without_web_request(self):
        from django.core.management import call_command
        today=timezone.localdate()
        unit=Unit.objects.create(name='وحدة اختبار المجدول')
        people={}
        for role in [Role.EXECUTIVE,Role.ASSISTANT,Role.HEAD,Role.EMPLOYEE]:
            user=User.objects.create_user(username='scheduler-'+role,email=role+'@example.invalid',employee_number=role,role=role)
            user.units.add(unit); people[role]=user
        unit.head=people[Role.HEAD]; unit.save()
        t=create_task(people[Role.EXECUTIVE],{'title':'اختبار تشغيل المجدول','description':'اختبار مستقل','unit':unit,'owner':people[Role.EMPLOYEE],'start_date':today-timedelta(days=10),'due_date':today-timedelta(days=3),'expected_result':'نتيجة','success_indicator':'مؤشر'},[])
        output=io.StringIO()
        # Preserve the enclosing TestCase transaction; standalone command lifecycle
        # is verified separately against an isolated database.
        with patch('core.management.commands.run_scheduler.close_old_connections'):
            call_command('run_scheduler',once=True,stdout=output)
        self.assertIn('rules evaluated',output.getvalue())
        self.assertTrue(t.escalations.filter(level=1,recipient=people[Role.HEAD]).exists())
        self.assertTrue(t.escalations.filter(level=2,recipient=people[Role.ASSISTANT]).exists())
        self.assertFalse(t.escalations.filter(level=3).exists())
        self.assertTrue(t.activities.filter(action='تصعيد آلي').exists())
