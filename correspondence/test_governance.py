import base64,io,json,os,tempfile,hashlib,hmac,time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch,Mock
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.management import call_command
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from core.models import User,Role
from .models import *
from . import services,governance,reports,integrations
from .security import seal,unseal,MAGIC,verify_totp
from . import tests as baseline_tests

@override_settings(DEBUG=True,SECURE_SSL_REDIRECT=False,MFA_REQUIRED=False)
class GovernanceAcceptanceTests(TestCase):
    setUpTestData=classmethod(baseline_tests.CorrespondenceAcceptanceTests.setUpTestData.__func__)
    data=baseline_tests.CorrespondenceAcceptanceTests.data
    mail=baseline_tests.CorrespondenceAcceptanceTests.mail
    internal=baseline_tests.CorrespondenceAcceptanceTests.internal
    signed=baseline_tests.CorrespondenceAcceptanceTests.signed
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.override=override_settings(MEDIA_ROOT=self.temp.name);self.override.enable();self.addCleanup(self.override.disable)
    def test_aes256_authentication_and_private_storage(self):
        raw='بيانات محمية'.encode();cipher=seal(raw)
        self.assertNotIn(raw,cipher);self.assertEqual(unseal(cipher),raw)
        with self.assertRaises(Exception):unseal(cipher[:-1]+bytes([cipher[-1]^1]))
        m=self.mail();a=services.attach(self.people[Role.EMPLOYEE],m,SimpleUploadedFile('x.txt',raw),'اختبار')
        self.assertTrue(Path(a.file.path).read_bytes().startswith(MAGIC));self.assertEqual(a.file.read(),raw)
    def test_mfa_replay_and_account_lock(self):
        import pyotp
        u=self.people[Role.EMPLOYEE];secret=pyotp.random_base32()
        device=MFADevice.objects.create(user=u,secret=base64.b64encode(seal(secret.encode())).decode())
        code=pyotp.TOTP(secret).now();self.assertTrue(verify_totp(u,code,activate=True));self.assertFalse(verify_totp(u,code))
        for _ in range(5):verify_totp(u,'000000')
        device.refresh_from_db();self.assertGreater(device.locked_until,timezone.now())
    def test_mfa_required_session_does_not_reach_tasks(self):
        self.client.force_login(self.people[Role.EXECUTIVE])
        with override_settings(MFA_REQUIRED=True):
            self.assertEqual(self.client.get('/tasks/').status_code,302)
            self.assertEqual(self.client.get('/api/tasks/').status_code,403)
    def test_hr_authoritative_idempotent_termination_and_no_role_elevation(self):
        payload={'units':[{'id':'U1','name':'الرعاية'}],'employees':[{'id':'E1','employee_number':'HR-1','email':'hr@example.invalid','first_name':'اسم','units':['U1'],'role':'executive'}]}
        result=integrations.sync_hr('Snso','ev1',payload);self.assertEqual(result['employees'],1)
        self.assertEqual(integrations.sync_hr('Snso','ev1',payload),result)
        u=ExternalIdentity.objects.get(source='Snso',external_id='E1').user;self.assertEqual(u.role,'employee');self.assertFalse(u.has_usable_password())
        payload['employees'][0]['terminated']=True
        result=integrations.sync_hr('Snso','ev2',payload);u.refresh_from_db();self.assertFalse(u.is_active);self.assertEqual(result['disabled'],1)
        with self.assertRaises(ValidationError):integrations.sync_hr('Snso','ev1',payload)
    def test_hr_invalid_event_rolls_back_entire_batch(self):
        payload={'units':[{'id':'U1','name':'الرعاية'}],'employees':[{'id':'E1','employee_number':'HR-1','email':'hr@example.invalid','units':['missing']}]}
        with self.assertRaises(ValidationError):integrations.sync_hr('Snso','ev1',payload)
        self.assertFalse(ExternalIdentity.objects.exists());self.assertFalse(ExternalUnit.objects.exists())
    def test_signed_callback_tampering_and_timestamp(self):
        cfg=Integration.objects.create(key='hr',kind='hr',endpoint='https://example.com',secret_env='TEST_HR_KEY',active=True)
        body=b'{"event_id":"x"}';stamp=str(int(time.time()));secret='k'*32
        sig=hmac.new(secret.encode(),stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
        with patch.dict(os.environ,{'TEST_HR_KEY':secret}):
            integrations.verify_callback(cfg,body,stamp,sig)
            with self.assertRaises(PermissionDenied):integrations.verify_callback(cfg,body+b'x',stamp,sig)
            with self.assertRaises(PermissionDenied):integrations.verify_callback(cfg,body,'1',sig)
    def test_outbox_retry_dedup_and_secret_webhook_suppression(self):
        cfg=Integration.objects.create(key='events',kind='webhook',endpoint='https://example.com',secret_env='KEY',active=True,events=['registered'])
        m=self.mail();self.assertEqual(Delivery.objects.count(),1)
        hidden=self.mail(external_number='hidden',secrecy='secret');self.assertEqual(Delivery.objects.count(),1)
        row=Delivery.objects.first();self.assertNotIn('subject',row.payload)
        with patch('correspondence.integrations.validate_endpoint',return_value=cfg.endpoint),patch.dict(os.environ,{'KEY':'x'*32}),patch('correspondence.integrations.requests.post',return_value=Mock(status_code=503)):
            self.assertEqual(integrations.deliver_pending(),0)
        row.refresh_from_db();self.assertEqual(row.state,'retry');self.assertEqual(row.attempts,1)
    def test_export_grant_scoped_time_bounded_and_audited(self):
        m=self.mail(secrecy='secret');u=self.people[Role.EMPLOYEE]
        self.assertFalse(governance.can_export(u,m))
        with self.assertRaises(PermissionDenied):governance.grant_export(u,m,u,timezone.now()+timedelta(hours=1),'سبب',True)
        g=governance.grant_export(self.people[Role.EXECUTIVE],m,u,timezone.now()+timedelta(hours=1),'سبب',True)
        self.assertTrue(governance.can_export(u,m));self.assertFalse(governance.can_export(u,m,'printing'))
        ExportGrant.objects.filter(pk=g.pk).update(expires=timezone.now()-timedelta(seconds=1));self.assertFalse(governance.can_export(u,m))
        self.assertTrue(services.verify_chain(m))
    def test_retention_quorum_separation_legal_hold_and_permanent(self):
        m=self.mail();m.status='archived';m.retain_until=timezone.localdate()-timedelta(days=1);m.save()
        rules=MailSettings.current();rules.retention_committee.set([self.people[Role.HEAD],self.people[Role.ASSISTANT]])
        case=governance.propose_retention(self.people[Role.EXECUTIVE],m,'permanent','سبب')
        with self.assertRaises(PermissionDenied):governance.approve_retention(self.people[Role.EXECUTIVE],case,'سبب')
        governance.approve_retention(self.people[Role.HEAD],case,'سبب');self.assertEqual(case.state,'proposed')
        governance.approve_retention(self.people[Role.ASSISTANT],case,'سبب');self.assertEqual(case.state,'approved')
        LegalHold.objects.create(mail=m,reason='نزاع',imposed_by=self.people[Role.EXECUTIVE])
        with self.assertRaises(ValidationError):governance.execute_retention(self.people[Role.EXECUTIVE],case,'MIN-1','سبب')
        LegalHold.objects.filter(mail=m).update(active=False)
        governance.execute_retention(self.people[Role.EXECUTIVE],case,'MIN-1','سبب');m.refresh_from_db();self.assertIsNone(m.retain_until);self.assertTrue(services.verify_chain(m))
    def test_reopen_invalidates_signature_and_requires_reapproval(self):
        m=self.signed();old=m.reference
        governance.reopen(self.people[Role.EXECUTIVE],m,'تعديل مصادق عليه');m.refresh_from_db()
        self.assertIsNone(m.signed_at);self.assertEqual(m.status,'returned');self.assertEqual(m.reference,old)
        self.assertEqual(self.client.get('/verify/'+str(m.uid)+'/').json()['valid'],False)
        self.assertTrue(services.verify_chain(m))
    def test_public_verifier_never_leaks_secret(self):
        m=self.mail(secrecy='secret')
        response=self.client.get('/verify/'+str(m.uid)+'/');self.assertEqual(response.status_code,404);self.assertNotContains(response,m.subject,status_code=404)
    def test_pdfa_and_richtext_are_real_and_safe(self):
        from .documents import pdf_bytes,clean_richtext
        m=self.mail();data=pdf_bytes(m,qr=True)
        self.assertTrue(data.startswith(b'%PDF-'))
        from pyhanko.pdf_utils.reader import PdfFileReader
        reader=PdfFileReader(io.BytesIO(data));metadata=reader.root['/Metadata'].data
        self.assertIn(b'pdfaid:part',metadata);self.assertIn(b'3',metadata)
        self.assertNotIn('<script',clean_richtext('<p onclick="x()">نص</p><script>alert(1)</script>'))
    def test_xlsx_does_not_execute_formulas(self):
        from openpyxl import load_workbook
        data=reports.xlsx_bytes(['subject'],[['=HYPERLINK("evil")']]);sheet=load_workbook(io.BytesIO(data)).active
        self.assertEqual(sheet['A2'].data_type,'s');self.assertTrue(sheet['A2'].value.startswith("'="))
    def test_report_schedule_uses_recipient_current_scope(self):
        m=self.mail();cfg=Integration.objects.create(key='mail',kind='email',endpoint='https://example.com',secret_env='KEY',active=True)
        schedule=ReportSchedule.objects.create(user=self.outsider,report='register',frequency='daily')
        reports.run_schedules();row=Delivery.objects.get(event='report.scheduled');self.assertEqual(row.payload['rows'],[])
        self.outsider.is_active=False;self.outsider.save();schedule.next_run=timezone.now();schedule.save();reports.run_schedules();schedule.refresh_from_db();self.assertFalse(schedule.active)
    def test_migration_dryrun_atomic_preserve_reference_and_sequence(self):
        row={'reference':'IN-1448-000099','legacy_id':'SNS-1','kind':'IN','subject':'مراسلة تاريخية','external_number':'OLD','original_date':'2026-01-01','owner':self.people[Role.EMPLOYEE].employee_number,'unit':self.unit.pk,'party':self.party.pk,'classification':self.classification.pk}
        path=Path(self.temp.name)/'history.json';path.write_text(json.dumps([row]))
        call_command('import_mail',str(path),actor='executive',stdout=io.StringIO());self.assertFalse(Correspondence.objects.exists())
        call_command('import_mail',str(path),actor='executive',commit=True,stdout=io.StringIO());self.assertEqual(Correspondence.objects.get().reference,row['reference']);self.assertEqual(Sequence.objects.get(year=1448,kind='IN').last,99)
    def test_portals_protected_config_and_privacy_workflow(self):
        self.client.force_login(self.people[Role.EMPLOYEE]);self.assertEqual(self.client.get('/communications/operations/').status_code,200)
        result=self.client.post('/communications/operations/',{'action':'privacy','kind':'access','reason':'بياناتي'});self.assertEqual(result.status_code,302)
        self.assertEqual(SubjectRequest.objects.get().requester,self.people[Role.EMPLOYEE])
        self.assertEqual(self.client.get('/communications/configure/integrations/').status_code,403)
    def test_unsigned_pdf_cannot_complete_signature(self):
        from .documents import validate_pades
        with patch.dict(os.environ,{'SIGNATURE_TRUST_ROOTS':''}):
            with self.assertRaises(ValidationError):validate_pades(b'%PDF-1.7 unsigned')
    def test_oauth_read_scope_never_authorizes_write(self):
        from oauth2_provider.models import Application,AccessToken
        u=self.people[Role.EXECUTIVE]
        app=Application.objects.create(name='test',user=u,client_type='confidential',authorization_grant_type='authorization-code')
        token=AccessToken.objects.create(user=u,application=app,token='test-read-token',scope='read',expires=timezone.now()+timedelta(hours=1))
        api=APIClient();api.credentials(HTTP_AUTHORIZATION='Bearer test-read-token')
        self.assertEqual(api.get('/api/communications/').status_code,200)
        self.assertEqual(api.post('/api/communications/',{},format='json').status_code,403)
    def test_destruction_requires_verified_backup_erasure_then_preserves_register(self):
        m=self.mail();a=services.attach(self.people[Role.EMPLOYEE],m,SimpleUploadedFile('proof.txt',b'proof'),'سبب')
        m.status='archived';m.retain_until=timezone.localdate()-timedelta(days=1);m.save()
        rules=MailSettings.current();rules.retention_committee.set([self.people[Role.HEAD],self.people[Role.ASSISTANT]])
        case=governance.propose_retention(self.people[Role.EXECUTIVE],m,'destroy','سند حفظ منته')
        governance.approve_retention(self.people[Role.HEAD],case,'موافقة');governance.approve_retention(self.people[Role.ASSISTANT],case,'موافقة')
        with self.assertRaises(ValidationError):governance.execute_retention(self.people[Role.EXECUTIVE],case,'M-1','إجراء')
        case.proof={'backup_erasure_verified':True,'provider':'mock-tested-contract','reference':'proof-1'};case.save()
        governance.execute_retention(self.people[Role.EXECUTIVE],case,'M-1','إجراء');a.refresh_from_db()
        self.assertFalse(a.file.name);self.assertTrue(Correspondence.objects.filter(pk=m.pk).exists());self.assertTrue(services.verify_chain(m))
    def test_english_correspondence_and_language_input(self):
        self.client.force_login(self.people[Role.EXECUTIVE])
        self.assertEqual(self.client.post('/security/language/',{'language':'en'}).status_code,302)
        response=self.client.get('/communications/');self.assertContains(response,'Executive dashboard');self.assertContains(response,'dir="ltr"')
        self.assertEqual(self.client.post('/security/language/',{'language':'../bad'}).status_code,403)
    def test_executive_override_records_branches_and_keeps_linked_task(self):
        m=self.mail();rows=services.refer(self.people[Role.EXECUTIVE],m,[self.people[Role.EMPLOYEE]],'execute','طلب')
        with self.assertRaises(PermissionDenied):services.transition(self.people[Role.HEAD],m,'done-override','سبب')
        services.transition(self.people[Role.EXECUTIVE],m,'done-override','قرار تنفيذي موثق');m.refresh_from_db()
        self.assertEqual(m.status,'done');self.assertIsNotNone(m.referrals.get().completed_at);self.assertTrue(services.verify_chain(m))
    def test_attachment_preview_scopes_types_and_audit(self):
        m=self.mail();a=services.attach(self.people[Role.EMPLOYEE],m,SimpleUploadedFile('proof.txt',b'proof'),'سبب')
        self.client.force_login(self.people[Role.EMPLOYEE]);r=self.client.get(f'/communications/files/{a.pk}/preview/');self.assertEqual(r.status_code,200);self.assertEqual(r['Cache-Control'],'no-store');r.close()
        self.client.force_login(self.outsider);self.assertEqual(self.client.get(f'/communications/files/{a.pk}/preview/').status_code,404)
        self.assertTrue(services.verify_chain(m))
