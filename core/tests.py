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
        return report_obstacle(self.owner,task,{'kind':'نقص موارد','description':'ميزانية المورد غير معتمدة','caused_by':'المورد','needs_decision':True,'requested_action':'اعتماد الميزانية','intervention_owner':self.head,'expected_resolution':self.today+timedelta(days=1),'impact':'high'})
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
        obstacle_data={'kind':'اعتماد مالي','description':'بانتظار الاعتماد','caused_by':'المورد','needs_decision':True,'requested_action':'اعتماد الميزانية','intervention_owner':self.head.pk,'expected_resolution':str(self.today),'impact':'high'}
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
            Task.objects.filter(pk=t.pk).update(status=state)
            for url in ['/tasks/',f'/tasks/{t.pk}/']:
                response=self.client.get(url); self.assertNotContains(response,'data-deadline')
                self.assertNotContains(response,'متأخرة 2 يوم')
        Task.objects.filter(pk=t.pk).update(status=Status.ACTIVE,due_date=self.today)
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
