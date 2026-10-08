import io,tempfile
from datetime import datetime,timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError,transaction
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User,Unit,Role,Status
from core import services as task_services
from .models import *
from . import services

@override_settings(DEBUG=True,SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False)
class CorrespondenceAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unit=Unit.objects.create(name='الخدمات الرعوية')
        cls.other=Unit.objects.create(name='التقنية')
        cls.people={}
        for role in [Role.ADMIN,Role.EXECUTIVE,Role.ASSISTANT,Role.HEAD,Role.EMPLOYEE,Role.VIEWER,Role.BOARD]:
            u=User.objects.create_user(username=role,email=role+'@example.invalid',employee_number=role,role=role)
            u.units.add(cls.unit);cls.people[role]=u
        cls.outsider=User.objects.create_user(username='other',email='other@example.invalid',employee_number='other',role=Role.EMPLOYEE); cls.outsider.units.add(cls.other)
        cls.unit.head=cls.people[Role.HEAD];cls.unit.save()
        cls.party=Party.objects.create(name='جهة اختبار')
        cls.retention=RetentionPolicy.objects.create(name='سياسة اختبار معتمدة',years=5,legal_reference='مرجع اختبار',approved=True)
        cls.classification=Classification.objects.create(name='خدمات',retention=cls.retention)
        cls.route=ApprovalRoute.objects.create(name='اعتماد داخلي',kind=Kind.INT,levels=['head','executive'],authority_reference='مصفوفة اختبار')
    def setUp(self):
        self.media=tempfile.TemporaryDirectory();self.over=override_settings(MEDIA_ROOT=self.media.name);self.over.enable();self.addCleanup(self.over.disable);self.addCleanup(self.media.cleanup)
        self.api=APIClient();self.api.force_authenticate(self.people[Role.EXECUTIVE])
    def data(self,**extra):
        return {'kind':Kind.IN,'subject':'طلب دعم','body':'مراسلة اختبار موثقة','unit':self.unit,'owner':self.people[Role.EMPLOYEE],'party':self.party,'classification':self.classification,'external_number':'EXT-1','original_date':timezone.localdate(),'no_attachments':True,**extra}
    def mail(self,**extra): return services.create_mail(self.people[Role.EXECUTIVE],self.data(**extra))
    def internal(self): return services.create_mail(self.people[Role.EMPLOYEE],self.data(kind=Kind.INT,route=self.route,signer=self.people[Role.EXECUTIVE],external_number=''))
    def signed(self):
        m=self.internal()
        for actor,action in [(Role.EMPLOYEE,'submit'),(Role.HEAD,'review'),(Role.HEAD,'approve'),(Role.EXECUTIVE,'approve'),(Role.EXECUTIVE,'sign')]:
            m=services.transition(self.people[actor],m,action,'سبب الاختبار',evidence='دليل داخلي')
        return m
    def test_incoming_number_and_duplicate_detection(self):
        m=self.mail(); self.assertRegex(m.reference,r'^IN-\d{4}-000001$');self.assertEqual(m.status,MailStatus.REGISTERED)
        with self.assertRaises(ValidationError): self.mail()
        m2=self.mail(external_number='EXT-2');self.assertTrue(m2.reference.endswith('000002'))
        services.transition(self.people[Role.EXECUTIVE],m,'cancel','إلغاء الاختبار')
        m3=self.mail(external_number='EXT-3');self.assertTrue(m3.reference.endswith('000003'))
    def test_outgoing_number_waits_for_approval_and_signing(self):
        m=self.internal();self.assertIsNone(m.reference)
        m=services.transition(self.people[Role.EMPLOYEE],m,'submit','تقديم')
        with self.assertRaises(ValidationError): services.transition(self.people[Role.EXECUTIVE],m,'sign','محاولة تجاوز',evidence='دليل')
        m=services.transition(self.people[Role.HEAD],m,'review','تدقيق')
        with self.assertRaises(PermissionDenied): services.transition(self.people[Role.EXECUTIVE],m,'approve','تجاوز المستوى')
        m=services.transition(self.people[Role.HEAD],m,'approve','اعتماد رئيس الوحدة')
        m=services.transition(self.people[Role.EXECUTIVE],m,'approve','اعتماد المدير')
        m=services.transition(self.people[Role.EXECUTIVE],m,'sign','إثبات توقيع داخلي',evidence='دليل اختبار')
        self.assertRegex(m.reference,r'^INT-\d{4}-000001$');self.assertEqual(m.status,MailStatus.ISSUED)
        with self.assertRaises(ValidationError): services.edit_mail(self.people[Role.EXECUTIVE],m,{'body':'تغيير نهائي'},'تغيير')
        with self.assertRaises(ValidationError): services.attach(self.people[Role.EXECUTIVE],m,SimpleUploadedFile('a.txt',b'change'),'تغيير')
    def test_creator_cannot_self_approve_and_admin_cannot_issue(self):
        m=services.create_mail(self.people[Role.EXECUTIVE],self.data(kind=Kind.INT,route=self.route,signer=self.people[Role.EXECUTIVE]))
        m=services.transition(self.people[Role.EXECUTIVE],m,'submit','تقديم');m=services.transition(self.people[Role.HEAD],m,'review','تدقيق');m=services.transition(self.people[Role.HEAD],m,'approve','اعتماد')
        with self.assertRaises(PermissionDenied):services.transition(self.people[Role.EXECUTIVE],m,'approve','اعتماد ذاتي')
        with self.assertRaises(PermissionDenied):services.transition(self.people[Role.ADMIN],m,'cancel','محاولة')
    def test_external_signature_is_not_faked(self):
        route=ApprovalRoute.objects.create(name='خارجي',kind=Kind.OUT,levels=['head'],authority_reference='مرجع')
        m=services.create_mail(self.people[Role.EMPLOYEE],self.data(kind=Kind.OUT,route=route,signer=self.people[Role.EXECUTIVE]))
        for actor,action in [(Role.EMPLOYEE,'submit'),(Role.HEAD,'review'),(Role.HEAD,'approve')]:m=services.transition(self.people[actor],m,action,'سبب')
        with self.assertRaises(ValidationError):services.transition(self.people[Role.EXECUTIVE],m,'sign','توقيع غير موثق',evidence='نص حر')
        m.refresh_from_db();self.assertIsNone(m.reference);self.assertIsNone(m.signed_at)
    def test_secret_hidden_in_html_api_search_and_reports(self):
        m=self.mail(secrecy=Secrecy.SECRET,subject='عنوان سري لا يظهر')
        for user in [self.people[Role.ADMIN],self.people[Role.ASSISTANT],self.outsider,self.people[Role.VIEWER]]:
            self.assertFalse(services.visible(user).filter(pk=m.pk).exists())
            self.api.force_authenticate(user)
            self.assertEqual(self.api.get(f'/api/communications/{m.pk}/').status_code,404)
            self.assertNotIn('عنوان سري لا يظهر',str(self.api.get('/api/communications/?q=عنوان').data))
        self.client.force_login(self.people[Role.ASSISTANT]);self.assertNotContains(self.client.get('/communications/reports/'),'عنوان سري لا يظهر')
        self.assertEqual(self.client.get('/communications/files/9999/').status_code,404)
    def test_upload_hash_download_audit_and_secret_download_denial(self):
        m=self.mail();a=services.attach(self.people[Role.EMPLOYEE],m,SimpleUploadedFile('proof.txt',b'proof'),'مرفق')
        import hashlib
        self.assertEqual(a.sha256,hashlib.sha256(b'proof').hexdigest())
        self.client.force_login(self.people[Role.EMPLOYEE]);response=self.client.get(f'/communications/files/{a.pk}/');self.assertEqual(b''.join(response.streaming_content),b'proof')
        self.assertTrue(m.events.filter(action='تنزيل مرفق').exists())
        secret=self.mail(external_number='SEC-1',secrecy=Secrecy.SECRET)
        a=services.attach(self.people[Role.EXECUTIVE],secret,SimpleUploadedFile('secret.txt',b'secret'),'مرفق سري')
        self.client.force_login(self.people[Role.EXECUTIVE]);self.assertEqual(self.client.get(f'/communications/files/{a.pk}/').status_code,403)
        self.assertEqual(self.client.get(f'/communications/{secret.pk}/print/').status_code,403)
    def test_parallel_referrals_and_linked_task_sync(self):
        m=self.mail();rows=services.refer(self.people[Role.EXECUTIVE],m,[self.people[Role.EMPLOYEE],self.people[Role.HEAD]],'execute','تنفيذ')
        due=timezone.localdate()+timedelta(days=4);task=services.convert_to_task(self.people[Role.EXECUTIVE],rows[0],{'due_date':due})
        self.assertEqual(services.convert_to_task(self.people[Role.EXECUTIVE],rows[0],{'due_date':due}).pk,task.pk)
        with self.assertRaises(ValidationError): services.complete_referral(self.people[Role.EMPLOYEE],rows[0],'لم تغلق المهمة')
        task_services.update_progress(self.people[Role.EMPLOYEE],task,100,'تحديث','مخرج','لا يوجد');task.refresh_from_db();task_services.change_status(self.people[Role.HEAD],task,Status.CLOSED,'اعتماد')
        services.run_mail_rules();rows[0].refresh_from_db();self.assertIsNotNone(rows[0].completed_at)
        with self.assertRaises(ValidationError): services.transition(self.people[Role.EXECUTIVE],m,'done','إغلاق قبل اكتمال الفروع')
        services.complete_referral(self.people[Role.HEAD],rows[1],'تمت المعالجة')
        m=services.transition(self.people[Role.EXECUTIVE],m,'done','اكتمال الفروع');self.assertEqual(m.status,MailStatus.DONE)
    def test_protected_task_contains_no_secret_subject_or_body(self):
        m=self.mail(secrecy=Secrecy.SECRET,subject='اسم مستفيد سري',body='هوية سرية')
        rows=services.refer(self.people[Role.EXECUTIVE],m,[self.people[Role.EMPLOYEE]],'execute')
        task=services.convert_to_task(self.people[Role.EXECUTIVE],rows[0],{'due_date':timezone.localdate()+timedelta(days=2)})
        self.assertNotIn('سري',task.title);self.assertNotIn('هوية',task.description)
    def test_business_calendar_weekend_holiday_and_sla_pause(self):
        r=MailSettings.current();start=timezone.make_aware(datetime(2026,10,8,16))
        self.assertEqual(services.add_work_hours(start,2).isoformat(),timezone.make_aware(datetime(2026,10,11,10)).isoformat())
        Holiday.objects.create(date=datetime(2026,10,11).date(),name='إجازة اختبار')
        self.assertEqual(services.add_work_hours(start,2).isoformat(),timezone.make_aware(datetime(2026,10,12,10)).isoformat())
        self.assertEqual(services.work_hours_between(start,timezone.make_aware(datetime(2026,10,12,10))),2)
        m=self.mail();m=services.transition(self.people[Role.EXECUTIVE],m,'start','بدء');m=services.transition(self.people[Role.EXECUTIVE],m,'pause','بانتظار رد موثق')
        before=MailAlert.objects.count();services.run_mail_rules(now=timezone.now()+timedelta(days=60));self.assertEqual(MailAlert.objects.count(),before)
        m=services.transition(self.people[Role.EXECUTIVE],m,'resume','ورد الرد');self.assertIsNone(m.paused_at)
    def test_sla_thresholds_from_settings_and_deduplication(self):
        m=self.mail();start=timezone.make_aware(datetime(2026,10,11,9));m.created_at=start;m.due_at=services.add_work_hours(start,10);m.referral_due_at=m.due_at;m.save()
        services.run_mail_rules(now=services.add_work_hours(start,7.9));self.assertFalse(MailAlert.objects.filter(stage='reminder').exists())
        services.run_mail_rules(now=services.add_work_hours(start,8));self.assertTrue(MailAlert.objects.filter(stage='reminder').exists());self.assertFalse(MailAlert.objects.filter(stage='manager').exists())
        services.run_mail_rules(now=services.add_work_hours(start,10));self.assertTrue(MailAlert.objects.filter(stage='manager').exists())
        services.run_mail_rules(now=services.add_work_hours(start,15));self.assertTrue(MailAlert.objects.filter(stage='executive').exists())
        count=MailAlert.objects.count();services.run_mail_rules(now=services.add_work_hours(start,15));self.assertEqual(MailAlert.objects.count(),count)
    def test_immutable_hash_chain_and_db_triggers(self):
        m=self.mail();e=m.events.first();self.assertTrue(services.verify_chain(m))
        with self.assertRaises(ValidationError):e.save()
        with self.assertRaises(ValidationError):e.delete()
        for field in ['reason','digest']:
            with self.assertRaises(DatabaseError),transaction.atomic():MailEvent.objects.filter(pk=e.pk).update(**{field:'tamper'})
        with self.assertRaises(DatabaseError),transaction.atomic():MailEvent.objects.filter(pk=e.pk).delete()
        self.assertTrue(services.verify_chain(m))
    def test_api_write_permissions_and_reason_enforcement(self):
        m=self.mail()
        self.api.force_authenticate(self.outsider);self.assertEqual(self.api.post(f'/api/communications/{m.pk}/transition/',{'action':'cancel','reason':'رفض'},format='json').status_code,404)
        self.api.force_authenticate(self.people[Role.EXECUTIVE]);self.assertEqual(self.api.post(f'/api/communications/{m.pk}/transition/',{'action':'cancel','reason':''},format='json').status_code,400)
        self.api.force_authenticate(self.people[Role.ADMIN]);self.assertEqual(self.api.post('/api/communications/',{},format='json').status_code,400)
        self.client.force_login(self.people[Role.EMPLOYEE]);self.assertEqual(self.client.get('/communications/settings/').status_code,403)
        self.client.force_login(self.people[Role.ADMIN]);self.assertEqual(self.client.get('/communications/settings/').status_code,200)
    def test_retention_gate_and_reply_link(self):
        m=self.mail();m=services.transition(self.people[Role.EXECUTIVE],m,'done','إنجاز')
        self.retention.approved=False;self.retention.save()
        with self.assertRaises(ValidationError):services.transition(self.people[Role.EXECUTIVE],m,'archive','حفظ')
        self.retention.approved=True;self.retention.save();m=services.transition(self.people[Role.EXECUTIVE],m,'archive','حفظ وفق السياسة');self.assertIsNotNone(m.retain_until)
        route=ApprovalRoute.objects.create(name='رد',kind=Kind.RPL,levels=['head'],authority_reference='مرجع')
        reply=services.create_mail(self.people[Role.EMPLOYEE],self.data(kind=Kind.RPL,related=m,route=route,signer=self.people[Role.EXECUTIVE]))
        self.assertEqual(reply.related_id,m.pk)
    def test_all_module_pages_render_with_audited_detail(self):
        m=self.mail();self.client.force_login(self.people[Role.EXECUTIVE])
        for path in ['/communications/','/communications/box/inbox/','/communications/box/outbox/','/communications/box/mine/','/communications/box/archive/','/communications/register/',f'/communications/{m.pk}/','/communications/reports/']:
            self.assertEqual(self.client.get(path).status_code,200,path)
        self.assertTrue(m.events.filter(action='اطلاع').exists())
        self.client.force_login(self.people[Role.ADMIN])
        for kind in ['party','classification','retention','template','route','holiday','access','delegation']:
            self.assertEqual(self.client.get('/communications/settings/'+kind+'/').status_code,200,kind)

    def test_sequential_referral_blocks_future_stage(self):
        m=self.mail();rows=services.refer(self.people[Role.EXECUTIVE],m,[self.people[Role.EMPLOYEE],self.people[Role.HEAD]],'execute','تسلسل',mode='sequential')
        with self.assertRaises(ValidationError): services.complete_referral(self.people[Role.HEAD],rows[1],'قبل الدور')
        services.complete_referral(self.people[Role.EMPLOYEE],rows[0],'انتهت المرحلة الأولى')
        services.complete_referral(self.people[Role.HEAD],rows[1],'انتهت المرحلة الثانية')
        self.assertTrue(m.alerts.filter(recipient=self.people[Role.HEAD],stage='referral').exists())

    def test_bulk_action_rejects_entire_unauthorized_batch(self):
        m=self.mail();other=self.mail(external_number='EXT-OTHER',unit=self.other,owner=self.outsider)
        with self.assertRaises(PermissionDenied): services.bulk_action(self.people[Role.HEAD],[m.pk,other.pk],'remind','سبب')
        self.assertFalse(MailAlert.objects.filter(stage='bulk').exists())
        services.bulk_action(self.people[Role.EXECUTIVE],[m.pk,m.pk],'remind','سبب')
        services.bulk_action(self.people[Role.EXECUTIVE],[m.pk],'remind','سبب')
        self.assertEqual(MailAlert.objects.filter(stage='bulk').count(),1)

    def test_text_extraction_is_queued_audited_and_searchable(self):
        from .ocr import request_extraction,run_jobs
        m=self.mail();a=services.attach(self.people[Role.EMPLOYEE],m,SimpleUploadedFile('proof.txt','إفادة للأُسرة'.encode()),'مرفق')
        job=request_extraction(self.people[Role.EMPLOYEE],a);self.assertEqual(job.state,'pending')
        run_jobs();job.refresh_from_db();self.assertEqual(job.state,'done');self.assertIn('إفادة',job.text)
        self.assertIsNone(job.confidence)
        self.api.force_authenticate(self.people[Role.EMPLOYEE]);self.assertEqual(self.api.get('/api/communications/?q=افادة').data['count'],1)
        self.assertEqual(m.subject,'طلب دعم')

    def test_ocr_missing_arabic_language_fails_explicitly(self):
        from .ocr import extract
        from types import SimpleNamespace
        a=SimpleUploadedFile('image.png',b'\x89PNG\r\n\x1a\n')
        with patch('correspondence.ocr.shutil.which',return_value='/usr/bin/tesseract'),patch('correspondence.ocr.subprocess.run',return_value=SimpleNamespace(stdout='eng\nosd\n')):
            with self.assertRaisesMessage(ValidationError,'حزمة اللغة العربية'): extract(a)

    def test_api_creates_mail_and_uploads_using_checked_server_fields(self):
        payload={'kind':'IN','subject':'وارد من API','body':'نص موثق','party':self.party.pk,'classification':self.classification.pk,'unit':self.unit.pk,'owner':self.people[Role.EMPLOYEE].pk,'external_number':'API-IN-1','original_date':str(timezone.localdate()),'no_attachments':True,'status':'archived'}
        response=self.api.post('/api/communications/',payload,format='json')
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(response.data['status'],MailStatus.REGISTERED)
        pk=response.data['id'];response=self.api.post(f'/api/communications/{pk}/attachments/',{'file':SimpleUploadedFile('api.txt',b'api file'),'reason':'مرفق API'},format='multipart')
        self.assertEqual(response.status_code,201,response.data)
        self.api.force_authenticate(self.people[Role.ADMIN]);payload['external_number']='API-IN-2'
        self.assertEqual(self.api.post('/api/communications/',payload,format='json').status_code,403)

    def test_html_registration_saves_in_server_not_browser(self):
        self.client.force_login(self.people[Role.EXECUTIVE])
        data={'kind':'IN','subject':'وارد من النموذج','body':'نص','party':self.party.pk,'classification':self.classification.pk,'unit':self.unit.pk,'owner':self.people[Role.EMPLOYEE].pk,'external_number':'FORM-1','original_date':str(timezone.localdate()),'no_attachments':'on','secrecy':'internal','priority':'normal','channel':'manual'}
        result=self.client.post('/communications/register/',data)
        self.assertEqual(result.status_code,302,result.content[:1000])
        self.assertTrue(Correspondence.objects.filter(external_number='FORM-1',reference__startswith='IN-').exists())
    def test_direct_incoming_task_conversion_is_atomic_and_idempotent(self):
        m=self.mail();due=timezone.localdate()+timedelta(days=3)
        url=f'/api/communications/{m.pk}/convert-to-task/'
        data={'owner':self.people[Role.EMPLOYEE].pk,'due_date':str(due),'reason':'تجهيز الرد والمرفقات'}
        first=self.api.post(url,data);self.assertEqual(first.status_code,201,first.data)
        second=self.api.post(url,data);self.assertEqual(second.data['task'],first.data['task'])
        self.assertEqual(m.referrals.count(),1)
        row=m.referrals.get();self.assertEqual(row.task.source,'inbound')
        self.assertTrue(m.events.filter(action='تحويل إحالة إلى مهمة',reason=data['reason']).exists())
        task_services.update_progress(self.people[Role.EMPLOYEE],row.task,50,'تقدم التنفيذ','مسودة الرد','لا يوجد')
        tracked=self.api.get(f'/api/communications/{m.pk}/referrals/').data[0]
        self.assertEqual(tracked['task_tracking']['progress'],50)
        self.client.force_login(self.people[Role.EXECUTIVE])
        html=self.client.get(f'/communications/{m.pk}/');self.assertContains(html,'متابعة مهام البريد الوارد');self.assertContains(html,'50%')
    def test_direct_conversion_rolls_back_invalid_due_and_denies_unauthorized(self):
        m=self.mail();due=timezone.localdate()-timedelta(days=1)
        with self.assertRaises(ValidationError):services.incoming_to_task(self.people[Role.EXECUTIVE],m,self.people[Role.EMPLOYEE],due,'تكليف')
        self.assertEqual(m.referrals.count(),0);m.refresh_from_db();self.assertEqual(m.status,MailStatus.REGISTERED)
        for role in [Role.ADMIN,Role.BOARD,Role.VIEWER,Role.EMPLOYEE]:
            with self.assertRaises(PermissionDenied):services.incoming_to_task(self.people[role],m,self.people[Role.EMPLOYEE],timezone.localdate(),'تكليف')
        secret=self.mail(external_number='SEC-DIRECT',secrecy=Secrecy.SECRET)
        with self.assertRaises(PermissionDenied):services.incoming_to_task(self.people[Role.EXECUTIVE],secret,self.outsider,timezone.localdate(),'تكليف')
        self.assertEqual(secret.referrals.count(),0)
    def test_direct_conversion_html_and_closed_mail(self):
        m=self.mail();self.client.force_login(self.people[Role.EXECUTIVE])
        response=self.client.post(f'/communications/{m.pk}/',{'form_type':'incoming-task','owner':self.people[Role.EMPLOYEE].pk,'due_date':str(timezone.localdate()),'reason':'تنفيذ طلب الجهة'})
        self.assertEqual(response.status_code,302);self.assertIsNotNone(m.referrals.get().task_id)
        m=services.transition(self.people[Role.EXECUTIVE],m,'cancel','إلغاء الطلب')
        self.assertNotContains(self.client.get(f'/communications/{m.pk}/'),'id="incoming-task"')
        with self.assertRaises(ValidationError):services.incoming_to_task(self.people[Role.EXECUTIVE],m,self.people[Role.EMPLOYEE],timezone.localdate(),'تكليف')
