"""Acceptance checks for phases 3–6 using persisted state and adversarial roles."""
import json,tempfile,os,hmac,hashlib
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings,Client
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction,DatabaseError
from django.utils import timezone
from rest_framework.test import APIClient
from . import tests as foundation,services,session_services as session,workflow_services as ops,governance_services as gov,election_services as elections,platform_services as platform
from .models import *

@override_settings(DEBUG=True,SECURE_SSL_REDIRECT=False,MFA_REQUIRED=False,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):foundation.MeetingFoundationTests.setUpTestData.__func__(cls)
    data=foundation.MeetingFoundationTests.data
    record=foundation.MeetingFoundationTests.record
    def setUp(self):
        self.media=tempfile.TemporaryDirectory();self.addCleanup(self.media.cleanup)
        setting=override_settings(MEDIA_ROOT=self.media.name);setting.enable();self.addCleanup(setting.disable)
        self.row=self.record();self.actor=self.users['secretary'];self.actor.set_password('test-password');self.actor.save()
        self.item=session.save_item(self.actor,self.row,{'title':'قرار موثق','kind':'decision','duration':15,'presenter':self.members['chair'],'owner':self.members['secretary']},'إعداد بند',self.refresh())
        session.publish_agenda(self.actor,self.row,'نشر',self.refresh());self.row=services.meeting_action(self.actor,self.row,'schedule','جدولة',self.refresh());session.issue_invitations(self.actor,self.row,'دعوة',self.refresh());self.refresh()
        self.clock=patch('django.utils.timezone.now',return_value=self.row.starts_at+timedelta(minutes=5));self.clock.start();self.addCleanup(lambda:self.clock.stop())
        for name in ['secretary','chair']:session.mark_attendance(self.actor,self.row,self.person(name),'present','تحقق فعلي','حضور')
        self.api=APIClient();self.api.force_authenticate(self.actor)
    def refresh(self):self.row.refresh_from_db();return self.row.version
    def person(self,name):return session.live_roster(self.row).people.get(member=self.members[name])
    def vote(self,secret=True,open=True):
        v=ops.create_vote(self.actor,self.row,self.item,'هل يعتمد القرار؟',secret,'eligible',51,'قاعدة اختبار',self.row.ends_at)
        return ops.vote_transition(self.actor,v,'open','فتح',v.version) if open else v
    def revision(self):return ops.save_minutes(self.actor,self.row,'نقاش حقيقي للاختبار',[self.members['secretary'].pk],'إصدار مسودة')
    def document(self,owner=None):return gov.upload_document(owner or self.actor,self.committee,SimpleUploadedFile('proof.txt',b'test evidence',content_type='text/plain'),meeting=self.row,secret=True)
    def test_secret_ballot_has_no_choice_timestamp_or_public_row(self):
        v=self.vote();proof=ops.cast(self.actor,v,'yes');ops.cast(self.users['chair'],v,'yes')
        self.assertNotIn('choice',[f.name for f in proof._meta.fields]);self.assertNotIn('created_at',[f.name for f in proof._meta.fields]);self.assertFalse(PublicBallot.objects.exists())
        self.assertNotIn('yes',str(list(Event.objects.filter(action='إثبات مشاركة في التصويت').values())))
        v=ops.vote_transition(self.actor,v,'close','ختم',v.version);self.assertEqual(v.result.totals,{'yes':2,'no':0,'abstain':0});self.assertEqual(v.result.outcome,'passed')
    def test_duplicate_vote_and_observer_denied(self):
        v=self.vote();ops.cast(self.actor,v,'yes')
        with self.assertRaises(ValidationError):ops.cast(self.actor,v,'no')
        with self.assertRaises(PermissionDenied):ops.cast(self.users['observer'],v,'yes')
        self.assertEqual(v.participation.count(),1)
    def test_lost_quorum_blocks_cast_and_result(self):
        v=self.vote();session.mark_attendance(self.actor,self.row,self.person('chair'),'left','خروج','فقد نصاب')
        with self.assertRaises(ValidationError):ops.cast(self.actor,v,'yes')
        with self.assertRaises(ValidationError):ops.vote_transition(self.actor,v,'close','إغلاق',v.version)
        self.assertFalse(VoteResult.objects.exists())
    def test_quorum_required_before_open(self):
        v=self.vote(open=False);session.mark_attendance(self.actor,self.row,self.person('chair'),'left','خروج','فقد نصاب')
        with self.assertRaises(ValidationError):ops.vote_transition(self.actor,v,'open','فتح',v.version)
    def test_public_ballot_only_released_after_close(self):
        v=self.vote(secret=False);ops.cast(self.actor,v,'yes')
        response=self.api.get(f'/api/governance-meetings/{self.row.pk}/workflow/');self.assertNotIn('public_ballots',response.data['votes'][0])
        ops.vote_transition(self.actor,v,'close','ختم',v.version)
        response=self.api.get(f'/api/governance-meetings/{self.row.pk}/workflow/');self.assertEqual(response.data['votes'][0]['public_ballots'][0]['choice'],'yes')
    def test_recusal_blocks_vote_and_self_approval(self):
        disclosure=gov.disclose(self.users['chair'],self.row,self.item,'صلة بالجهة','تعارض',True)
        with self.assertRaises(PermissionDenied):gov.approve_disclosure(self.users['chair'],disclosure,'اعتماد')
        gov.approve_disclosure(self.actor,disclosure,'تنح معتمد');v=self.vote()
        with self.assertRaises(PermissionDenied):ops.cast(self.users['chair'],v,'yes')
    def test_immutable_results_protected_from_bulk_updates(self):
        v=self.vote();ops.cast(self.actor,v,'yes');ops.vote_transition(self.actor,v,'close','ختم',v.version)
        with self.assertRaises(DatabaseError),transaction.atomic():VoteResult.objects.filter(vote=v).update(outcome='passed')
        with self.assertRaises(DatabaseError),transaction.atomic():Participation.objects.all().delete()
    def test_minute_reauthentication_final_and_correction(self):
        r=self.revision();m=ops.minute_transition(self.actor,r,'review','عرض',version=r.minutes.version)
        with self.assertRaises(ValidationError):ops.minute_transition(self.actor,r,'sign','توقيع',password='wrong',capacity='أمين',version=m.version)
        m=ops.minute_transition(self.actor,r,'sign','توقيع',password='test-password',capacity='أمين',version=m.version)
        m=ops.minute_transition(self.actor,r,'final','اعتماد',version=m.version);self.assertEqual(m.status,'final');self.assertTrue(hasattr(r,'seal'))
        self.refresh();self.assertIsNotNone(self.row.closed_at)
        with self.assertRaises(ValidationError):session.save_item(self.actor,self.row,{'title':'تلاعب'},'تغيير',self.row.version,self.item)
        with self.assertRaises(ValidationError):ops.save_minutes(self.actor,self.row,'تعديل',[self.members['secretary'].pk],'تعديل',m.version)
        new=ops.save_minutes(self.actor,self.row,'تصحيح صريح',[self.members['secretary'].pk],'سبب تصحيح',m.version,True);r.refresh_from_db();self.assertEqual(new.version,2);self.assertEqual(r.body,'نقاش حقيقي للاختبار')
    def test_minute_final_needs_all_signatures(self):
        r=self.revision();m=ops.minute_transition(self.actor,r,'review','عرض',version=r.minutes.version)
        with self.assertRaises(ValidationError):ops.minute_transition(self.actor,r,'final','اعتماد',version=m.version)
    def test_minute_snapshot_and_signature_raw_update_denied(self):
        r=self.revision()
        with self.assertRaises(DatabaseError),transaction.atomic():MinuteRevision.objects.filter(pk=r.pk).update(body='edited')
    def test_reclassification_hides_old_minute_and_shared_note(self):
        r=self.revision();note=ops.save_note(self.users['member'],self.row,'ملاحظة',[ ],True,self.item)
        self.assertTrue(ops.minute_visible(self.users['member'],r));self.assertTrue(ops.notes_for(self.users['member'],self.row).exists())
        session.save_item(self.actor,self.row,{'kind':'secret'},'تصنيف سري',self.refresh(),self.item)
        self.assertFalse(ops.minute_visible(self.users['member'],r));self.assertFalse(ops.notes_for(self.users['member'],self.row).exists())
    def test_private_notes_excluded_from_other_members_and_audit(self):
        note=ops.save_note(self.users['member'],self.row,'private-test',[[[.2,.4],[.4,.5]]],False,self.item)
        self.assertFalse(ops.notes_for(self.actor,self.row).exists());self.assertNotIn('private-test',str(list(Event.objects.values())))
        with self.assertRaises(ValidationError):ops.save_note(self.users['member'],self.row,'new',[],False,self.item,note,0)
    def test_invalid_strokes_and_share_policy_denied(self):
        with self.assertRaises(ValidationError):ops.save_note(self.actor,self.row,'x',[[[2,0]]],False)
        p=ClassificationPolicy.objects.create(level='internal',reference='اختبار',sharing_allowed=False);RecordPolicy.objects.create(meeting=self.row,policy=p)
        with self.assertRaises(PermissionDenied):ops.save_note(self.actor,self.row,'x',[],True)
    def test_timer_persists_and_detects_stale_update(self):
        state=ops.live_action(self.actor,self.row,'start','بدء',1,self.item);self.assertEqual(LiveState.objects.get().state,'running')
        with self.assertRaises(ValidationError):ops.live_action(self.actor,self.row,'pause','إيقاف',1,self.item)
        state=ops.live_action(self.actor,self.row,'pause','إيقاف',state.version,self.item);self.assertEqual(state.state,'paused')
    def test_speaker_duplicate_and_parallel_speaking_denied(self):
        a=ops.speaker_action(self.actor,self.row,'join');b=ops.speaker_action(self.users['chair'],self.row,'join')
        with self.assertRaises(ValidationError):ops.speaker_action(self.actor,self.row,'join')
        ops.speaker_action(self.actor,self.row,'speaking',speaker_id=a.pk)
        with self.assertRaises(ValidationError):ops.speaker_action(self.actor,self.row,'speaking',speaker_id=b.pk)
    def test_admin_cannot_vote_manage_or_read_workspace(self):
        self.api.force_authenticate(self.users['admin']);self.assertEqual(self.api.get(f'/api/governance-meetings/{self.row.pk}/workflow/').status_code,404)
        with self.assertRaises(PermissionDenied):ops.create_vote(self.users['admin'],self.row,self.item,'x',True,'eligible',51,'مرجع',self.row.ends_at)
    def test_all_new_views_render(self):
        self.client.force_login(self.actor);self.revision()
        paths=[f'/meetings/{self.row.pk}/{p}/' for p in ['votes','minutes','live','execution','privacy']]+['/meetings/'+p+'/' for p in ['documents','elections','ideas','search','reports','help','integrations']]+['/meetings/governance/'+k+'/' for k,l in GovernanceEntry.KINDS]
        for path in paths:
            with self.subTest(path=path):self.assertEqual(self.client.get(path).status_code,200)
        self.client.force_login(self.users['admin']);self.assertEqual(self.client.get('/meetings/platform/').status_code,200)
        self.assertEqual(Client().get('/meetings/public/').status_code,200);self.assertEqual(Client().get('/meetings/otp/').status_code,200)
    def test_document_acl_and_download_policy(self):
        d=self.document();self.assertTrue(gov.document_allowed(self.actor,d));self.assertFalse(gov.document_allowed(self.users['member'],d))
        p=ClassificationPolicy.objects.create(level='secret',reference='اختبار',download_allowed=False);RecordPolicy.objects.create(meeting=self.row,policy=p)
        self.assertFalse(gov.document_allowed(self.actor,d))
    def test_service_resource_overbooking_denied(self):
        data={'resource':'قاعة أ','quantity':1,'requirements':'تجهيز معتمد','starts':self.row.starts_at.isoformat(),'ends':self.row.ends_at.isoformat()}
        Resource.objects.create(committee=self.committee,name='قاعة أ',capacity=1)
        content={'resources':[{'committee':self.committee.pk,'name':'قاعة أ','quantity':1}]}
        revision=PlatformRevision.objects.create(version=1,content=content,created_by=self.users['admin'],reason='مخزون');PlatformSettings.objects.create(pk=1,draft=revision,published=revision)
        gov.save_entry(self.actor,'service',self.committee,'تجهيز قاعة',self.actor,data,'حجز',meeting=self.row)
        with self.assertRaises(ValidationError):gov.save_entry(self.actor,'service',self.committee,'حجز متعارض',self.actor,data,'حجز',meeting=self.row)
    def test_volunteer_hours_use_actual_closed_attendance(self):
        start=timezone.now();self.clock.stop();self.clock=patch('django.utils.timezone.now',return_value=start+timedelta(minutes=30));self.clock.start()
        session.mark_attendance(self.actor,self.row,self.person('chair'),'left','خروج','إنهاء جلسة');span=AttendanceSpan.objects.get(eligibility=self.person('chair'))
        data={'source':'attendance','starts':span.starts.isoformat(),'ends':span.ends.isoformat()}
        row=gov.save_entry(self.users['chair'],'volunteer',self.committee,'مشاركة فعلية',self.users['chair'],data,'توثيق',meeting=self.row,member=self.members['chair']);self.assertEqual(row.data['hours'],'0.50')
        with self.assertRaises(ValidationError):gov.save_entry(self.users['chair'],'volunteer',self.committee,'تكرار',self.users['chair'],data,'توثيق',meeting=self.row,member=self.members['chair'])
        row=gov.entry_transition(self.users['chair'],row,'submit','تقديم',row.version)
        with self.assertRaises(PermissionDenied):gov.entry_transition(self.users['chair'],row,'approve','ذاتي',row.version)
        self.assertEqual(gov.entry_transition(self.actor,row,'approve','مراجعة مستقلة',row.version).status,'approved')
    def test_feature_cannot_be_marked_working_administratively(self):
        f=platform.features().get(key='parq')
        with self.assertRaises(ValidationError):platform.set_feature(self.users['admin'],f,'works','موافقة','production')
        self.assertFalse(platform.provider_ready('parq'))
    def test_activation_duplicate_and_scope(self):
        row=platform.request_activation(self.actor,'sms','احتياج')
        with self.assertRaises(ValidationError):platform.request_activation(self.users['chair'],'sms','طلب آخر')
        with self.assertRaises(PermissionDenied):platform.activation_transition(self.actor,row,'review','مراجعة',row.version)
        row=platform.activation_transition(self.users['admin'],row,'review','مراجعة',row.version)
        row=platform.activation_transition(self.users['admin'],row,'approved','موافقة',row.version)
        row=platform.activation_transition(self.users['admin'],row,'complete','إكمال إداري',row.version);self.assertFalse(platform.provider_ready('sms'))
    def test_otp_failure_attempts_persist_and_replay_denied(self):
        nonce=__import__('uuid').uuid4();pepper=os.getenv('OTP_PEPPER','') or __import__('django.conf',fromlist=['settings']).settings.SECRET_KEY
        code='12345678';hash_value=hmac.new(pepper.encode(),(str(nonce)+':'+code).encode(),hashlib.sha256).hexdigest()
        challenge=OTPChallenge.objects.create(nonce=nonce,user=self.actor,code_hash=hash_value,expires_at=timezone.now()+timedelta(minutes=5),delivered=True)
        self.assertIsNone(platform.verify_otp(nonce,'wrong'));challenge.refresh_from_db();self.assertEqual(challenge.attempts,1)
        self.assertEqual(platform.verify_otp(nonce,code).pk,self.actor.pk)
        with self.assertRaises(ValidationError):platform.verify_otp(nonce,code)
        # Hashing is exercised using the implementation's configured pepper.
    def test_election_secret_ballot_tie_and_frozen_roll(self):
        now=timezone.now();e=elections.create_election(self.actor,self.committee,'انتخاب اختبار',now-timedelta(minutes=1),now+timedelta(minutes=1),now+timedelta(hours=1),1,'مرجع اختبار')
        for name in ['chair','member']:
            d=self.document(self.users[name]);c=elections.nominate(self.users[name],e,'برنامج',d);elections.review_candidate(self.actor,c,True,'أهلية')
        self.clock.stop();self.clock=patch('django.utils.timezone.now',return_value=now+timedelta(minutes=2));self.clock.start();roll=elections.freeze_election(self.actor,e,'تثبيت',1);e.refresh_from_db()
        elections.cast_election(self.users['chair'],e,[self.members['chair'].pk]);elections.cast_election(self.users['member'],e,[self.members['member'].pk])
        with self.assertRaises(ValidationError):elections.cast_election(self.users['chair'],e,[self.members['member'].pk])
        with self.assertRaises(DatabaseError),transaction.atomic():VoterRoll.objects.filter(pk=roll.pk).update(voters=[])
        self.clock.stop();self.clock=patch('django.utils.timezone.now',return_value=e.ballot_end+timedelta(seconds=1));self.clock.start();result=elections.close_election(self.actor,e,'ختم',e.version)
        self.assertEqual(result.winners,[]);self.assertEqual(set(result.tied),{self.members['chair'].pk,self.members['member'].pk});self.assertNotIn('choices',[f.name for f in ElectionParticipation._meta.fields])
    def test_csv_export_and_report_scope(self):
        self.client.force_login(self.actor)
        for path in ['/meetings/reports/?export=csv','/meetings/governance/compliance/?export=csv']:self.assertEqual(self.client.get(path).status_code,200)
    def test_unauthenticated_posts_require_csrf(self):
        c=Client(enforce_csrf_checks=True);self.assertEqual(c.post('/meetings/otp/',{'action':'request','email':self.actor.email}).status_code,403)
    def test_ideas_have_no_author_and_vote_is_unique(self):
        idea=gov.submit_idea(self.users['member'],self.committee,'اقتراح تحسين الاجتماع')
        self.assertNotIn('owner',[f.name for f in idea._meta.fields]);self.assertNotIn('created_at',[f.name for f in idea._meta.fields])
        gov.vote_idea(self.users['member'],idea)
        with self.assertRaises(ValidationError):gov.vote_idea(self.users['member'],idea)
    def test_compliance_does_not_invent_legal_status(self):
        data={'regulator':'جهة للاختبار','article':'مادة مدخلة','action':'مراجعة','frequency':'سنوي','gap':'لا تحقق','remediation':'تحقق'}
        r=gov.save_entry(self.actor,'compliance',self.committee,'التزام للاختبار',self.actor,data,'إعداد',due_date=timezone.localdate())
        self.assertEqual(r.data['legal_status'],'يتطلب تحققًا قانونيًا')
        data={**data,'official_url':'https://example.com/fake-rule'}
        with self.assertRaises(ValidationError):gov.save_entry(self.actor,'compliance',self.committee,'مصدر غير رسمي',self.actor,data,'اختبار',due_date=timezone.localdate())
    def test_finance_needs_evidence_before_approval(self):
        r=gov.save_entry(self.actor,'finance',self.committee,'مؤشر مالي',self.actor,{'period':'2026','source':'دفتر اختبار','amount':'14.0','category':'برنامج'},'إعداد')
        r=gov.entry_transition(self.actor,r,'submit','تقديم',r.version)
        with self.assertRaises(ValidationError):gov.entry_transition(self.actor,r,'approve','اعتماد',r.version)
    def test_secret_governance_records_do_not_leak(self):
        r=gov.save_entry(self.actor,'finance',self.committee,'سري للاختبار',self.actor,{'period':'2026','source':'اختبار','amount':'14','category':'برنامج'},'إعداد')
        self.assertFalse(gov.entries_for(self.users['member']).filter(pk=r.pk).exists());self.api.force_authenticate(self.users['member']);response=self.api.get('/api/governance-records/finance/');self.assertEqual(response.data,[])
    def test_otp_disabled_and_expired(self):
        with self.assertRaises(ValidationError):platform.request_otp(self.actor.email)
        c=OTPChallenge.objects.create(user=self.actor,code_hash='hash',expires_at=timezone.now()-timedelta(seconds=1),delivered=True)
        with self.assertRaises(ValidationError):platform.verify_otp(c.nonce,'12345678')
    def test_otp_locks_after_five_attempts(self):
        c=OTPChallenge.objects.create(user=self.actor,code_hash='hash',expires_at=timezone.now()+timedelta(minutes=1),delivered=True)
        for _ in range(5):self.assertIsNone(platform.verify_otp(c.nonce,'wrong'))
        with self.assertRaises(ValidationError):platform.verify_otp(c.nonce,'wrong')
    def test_public_settings_draft_publish_and_rollback(self):
        d={k:'محتوى معتمد للاختبار' for k in ['name','about','vision','mission','objectives','contact']}
        r=platform.save_platform(self.users['admin'],d,'مسودة',1)
        self.assertNotContains(Client().get('/meetings/public/'),'محتوى معتمد للاختبار')
        row=PlatformSettings.objects.get(pk=1);platform.publish_platform(self.users['admin'],r.pk,'نشر',row.version)
        self.assertContains(Client().get('/meetings/public/'),'محتوى معتمد للاختبار')
        row.refresh_from_db();r2=platform.save_platform(self.users['admin'],{**d,'name':'نسخة جديدة'},'تعديل',row.version);row.refresh_from_db();platform.publish_platform(self.users['admin'],r2.pk,'نشر',row.version)
        row.refresh_from_db();platform.publish_platform(self.users['admin'],r.pk,'رجوع موثق',row.version);self.assertContains(Client().get('/meetings/public/'),'محتوى معتمد للاختبار')
    def test_invalid_brand_color_rejected(self):
        d={k:'محتوى' for k in ['name','about','vision','mission','objectives','contact']};d['primary']='red;display:none'
        with self.assertRaises(ValidationError):platform.save_platform(self.users['admin'],d,'اختبار',1)
    def test_signature_fail_closed_without_credentials(self):
        from .signature_provider import start_signature
        r=self.revision()
        with patch.dict(os.environ,{'PARQ_API_URL':'','PARQ_API_KEY':'','PARQ_HMAC_KEY':''}):
            with self.assertRaises(ValidationError):start_signature(self.actor,r)
        self.assertFalse(SignatureRequest.objects.exists())
    def test_signature_callback_rejects_unsigned_and_partial_signers(self):
        from .signature_provider import callback
        r=self.revision();m=ops.minute_transition(self.actor,r,'review','عرض',version=r.minutes.version);req=SignatureRequest.objects.create(revision=r,provider_id='vendor-test')
        conf={'PARQ_API_URL':'https://ehsan.parq.sa/api','PARQ_API_KEY':'test-key','PARQ_HMAC_KEY':'test-secret'}
        stamp=str(int(timezone.now().timestamp()));data={'event_id':'test-1','request_nonce':str(req.nonce),'request_id':'vendor-test','content_hash':r.digest,'status':'complete','signed_memberships':[]}
        body=json.dumps(data).encode();signature=hmac.new(b'test-secret',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
        with patch.dict(os.environ,conf):
            with self.assertRaises(PermissionDenied):callback(body,stamp,'invalid')
            with self.assertRaises(ValidationError):callback(body,stamp,signature)
        self.assertFalse(ProviderReceipt.objects.exists());self.assertFalse(MinuteSignature.objects.exists())
    def test_provider_callback_idempotent_content_bound(self):
        from .signature_provider import callback
        r=self.revision();req=SignatureRequest.objects.create(revision=r,provider_id='vendor-test');stamp=str(int(timezone.now().timestamp()))
        data={'event_id':'test-1','request_nonce':str(req.nonce),'request_id':'vendor-test','content_hash':r.digest,'status':'pending'}
        def send(d):
            body=json.dumps(d).encode();sig=hmac.new(b'test-secret',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest();return callback(body,stamp,sig)
        with patch.dict(os.environ,{'PARQ_API_URL':'https://ehsan.parq.sa/api','PARQ_API_KEY':'test-key','PARQ_HMAC_KEY':'test-secret'}):
            send(data);send(data)
            with self.assertRaises(PermissionDenied):send({**data,'status':'failed'})
        self.assertEqual(ProviderReceipt.objects.count(),1)
    def test_execution_requires_final_minute(self):
        from .execution_services import create_decision
        r=self.revision()
        with self.assertRaises(ValidationError):create_decision(self.users['exec'],r,self.item,'DEC-X','نص',timezone.localdate(),self.users['exec'],self.users['exec'],[], 'normal',24,24,'مخرج','تحويل')
    def test_execution_creates_existing_core_decision_once(self):
        from .execution_services import create_decision
        from core.models import Decision
        r=self.revision();m=ops.minute_transition(self.actor,r,'review','عرض',version=r.minutes.version);m=ops.minute_transition(self.actor,r,'sign','توقيع',password='test-password',capacity='أمين',version=m.version);ops.minute_transition(self.actor,r,'final','اعتماد',version=m.version)
        args=(self.users['exec'],r,self.item,'DEC-X','نص',timezone.localdate()+timedelta(days=30),self.users['exec'],self.users['exec'],[], 'normal',24,24,'مخرج','تحويل')
        ext=create_decision(*args);self.assertEqual(Decision.objects.count(),1);self.assertEqual(ext.decision.meeting_id,self.row.meeting_id)
        with self.assertRaises(ValidationError):create_decision(*args)
        self.client.force_login(self.users['exec']);self.assertContains(self.client.get(f'/meetings/{self.row.pk}/execution/'),'DEC-X')
    def test_feedback_unique_and_convert_recommendation(self):
        self.row.closed_at=timezone.now();self.row.save(update_fields=['closed_at']);f=gov.feedback(self.users['chair'],self.row,4,'تحسين تنظيم الوقت',True)
        with self.assertRaises(ValidationError):gov.feedback(self.users['chair'],self.row,5,'تكرار',True)
        row=gov.feedback_recommendation(self.actor,f,self.users['exec'],timezone.localdate()+timedelta(days=10),'تحسين');self.assertEqual(row.feedback_source_id,f.pk)
        with self.assertRaises(ValidationError):gov.feedback_recommendation(self.actor,f,self.users['exec'],timezone.localdate()+timedelta(days=10),'تكرار')
    def test_document_upload_html_persists_encrypted_file(self):
        self.client.force_login(self.actor);response=self.client.post('/meetings/documents/',{'committee':self.committee.pk,'meeting':self.row.pk,'secret':'on','file':SimpleUploadedFile('real-proof.txt',b'plain-test-proof',content_type='text/plain')})
        self.assertEqual(response.status_code,302);d=Document.objects.get();self.assertEqual(d.file.open('rb').read(),b'plain-test-proof')
        from pathlib import Path
        self.assertNotIn(b'plain-test-proof',Path(d.file.path).read_bytes())
    def test_vote_api_roundtrip_and_version_conflict(self):
        payload={'action':'vote_create','item':self.item.pk,'question':'هل يعتمد؟','secret':True,'purpose':'decision','denominator':'eligible','threshold':'51.0','reference':'قاعدة اختبار','closes_at':self.row.ends_at.isoformat()}
        url=f'/api/governance-meetings/{self.row.pk}/workflow/';response=self.api.post(url,payload,format='json');self.assertEqual(response.status_code,200,response.data);v=Vote.objects.get()
        response=self.api.post(url,{'action':'vote_open','vote':v.pk,'reason':'فتح','version':0},format='json');self.assertEqual(response.status_code,400)
        response=self.api.post(url,{'action':'vote_open','vote':v.pk,'reason':'فتح','version':1},format='json');self.assertEqual(response.status_code,200,response.data)
        response=self.api.post(url,{'action':'cast','vote':v.pk,'choice':'yes'},format='json');self.assertEqual(response.status_code,200,response.data)
    def test_legal_hold_blocks_destruction(self):
        d=self.document();p=ClassificationPolicy.objects.create(level='internal',reference='سياسة اختبار',days=1);RecordPolicy.objects.create(meeting=self.row,policy=p,legal_hold=True,hold_reason='حجز',expires_at=timezone.now()-timedelta(days=1))
        with self.assertRaises(ValidationError):platform.destruction(self.actor,d,'إتلاف')
        self.assertFalse(DestructionRequest.objects.exists())
    def test_double_approval_needs_independent_people(self):
        d=self.document();p=ClassificationPolicy.objects.create(level='internal',reference='سياسة اختبار',days=1);RecordPolicy.objects.create(meeting=self.row,policy=p,expires_at=timezone.now()-timedelta(days=1))
        req=platform.destruction(self.actor,d,'انتهاء الاحتفاظ')
        with self.assertRaises(PermissionDenied):platform.approve_destruction(self.actor,req,'ذاتي')
        req=platform.approve_destruction(self.users['chair'],req,'مراجعة مستقلة')
        with self.assertRaises(PermissionDenied):platform.approve_destruction(self.users['chair'],req,'تكرار نفس الموافق')
        with self.captureOnCommitCallbacks(execute=True):req=platform.approve_destruction(self.users['exec'],req,'موافقة ثانية مستقلة')
        self.assertIsNotNone(req.executed_at);self.assertFalse(gov.document_allowed(self.actor,d))
    def test_resource_capacity_uses_peak_concurrent_bookings(self):
        Resource.objects.create(committee=self.committee,name='أجهزة',capacity=2)
        start=self.row.starts_at;mid=start+timedelta(hours=1);end=self.row.ends_at
        for a,b in [(start,mid),(mid,end)]:gov.save_entry(self.actor,'service',self.committee,'حجز جهاز',self.actor,{'resource':'أجهزة','quantity':1,'requirements':'اختبار','starts':a.isoformat(),'ends':b.isoformat()},'حجز',meeting=self.row)
        gov.save_entry(self.actor,'service',self.committee,'حجز متزامن صحيح',self.actor,{'resource':'أجهزة','quantity':1,'requirements':'اختبار','starts':start.isoformat(),'ends':end.isoformat()},'حجز',meeting=self.row)
        with self.assertRaises(ValidationError):gov.save_entry(self.actor,'service',self.committee,'تجاوز سعة',self.actor,{'resource':'أجهزة','quantity':1,'requirements':'اختبار','starts':start.isoformat(),'ends':end.isoformat()},'حجز',meeting=self.row)
    def test_sandbox_does_not_certify_production_signature(self):
        platform.record_provider_test(self.users['admin'],'parq',True,'اختبار وهمي في الاختبار فقط','sandbox')
        self.assertFalse(platform.provider_ready('parq','production'))
    def test_feature_runtime_failure_preserves_test_evidence(self):
        f=platform.record_provider_test(self.users['admin'],'parq',True,'دليل محفوظ','production');stamp=f.tested_at
        platform.runtime_failure('parq','فشل تشغيل');f.refresh_from_db();self.assertEqual(f.state,'error');self.assertEqual(f.evidence,'دليل محفوظ');self.assertEqual(f.tested_at,stamp)
    def test_observer_private_write_and_vote_forms_denied(self):
        with self.assertRaises(PermissionDenied):ops.save_note(self.users['observer'],self.row,'كتابة غير مخولة',[],False)
        with self.assertRaises(PermissionDenied):ops.speaker_action(self.users['observer'],self.row,'join')
        self.client.force_login(self.users['observer']);response=self.client.get(f'/meetings/{self.row.pk}/votes/');self.assertEqual(response.status_code,200);self.assertNotContains(response,'تسجيل الصوت');self.assertNotContains(response,'تقديم إفصاح')
