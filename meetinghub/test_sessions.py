import hashlib,tempfile
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings,Client
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction,DatabaseError
from django.utils import timezone
from rest_framework.test import APIClient
from . import tests as foundation,services,session_services as ops
from .models import *

@override_settings(DEBUG=True,SECURE_SSL_REDIRECT=False,MFA_REQUIRED=False)
class MeetingSessionTests(TestCase):
    @classmethod
    def setUpTestData(cls):foundation.MeetingFoundationTests.setUpTestData.__func__(cls)
    data=foundation.MeetingFoundationTests.data
    record=foundation.MeetingFoundationTests.record
    def setUp(self):
        self.media=tempfile.TemporaryDirectory();self.addCleanup(self.media.cleanup)
        self.settings_override=override_settings(MEDIA_ROOT=self.media.name);self.settings_override.enable();self.addCleanup(self.settings_override.disable)
        self.row=self.record(allow_proxy=True,proxy_limit=1)
        self.actor=self.users['secretary'];self.api=APIClient();self.api.force_authenticate(self.actor)
        self.item=self.item_new('بند عادي')
    def refresh(self):self.row.refresh_from_db();return self.row.version
    def item_new(self,title,**extra):
        return ops.save_item(self.users['secretary'],self.row,{'title':title,'kind':'discussion','duration':15,'presenter':self.members['chair'],'owner':self.members['secretary'],**extra},'إعداد بند',self.refresh())
    def publish(self):return ops.publish_agenda(self.actor,self.row,'نشر جدول معتمد',self.refresh())
    def issue(self):
        if not self.row.agenda_version:self.publish()
        self.refresh()
        if self.row.status=='draft':self.row=services.meeting_action(self.actor,self.row,'schedule','جدولة',self.row.version)
        links=ops.issue_invitations(self.actor,self.row,'دعوة الأعضاء',self.refresh());self.refresh();return links
    def person(self,name):return ops.live_roster(self.row).people.get(member=self.members[name])
    def upload(self,purpose='agenda',item=None):return ops.upload_file(self.actor,self.row,SimpleUploadedFile('evidence.txt',b'fictional evidence',content_type='text/plain'),'وثيقة اختبار',self.refresh(),item,purpose)
    def test_published_edits_preserve_snapshot_hash_and_reason(self):
        revision=self.publish();digest=revision.digest
        ops.save_item(self.actor,self.row,{'title':'بند معدل'},'سبب التعديل',self.refresh(),self.item)
        revision.refresh_from_db();self.assertEqual(revision.items[0]['title'],'بند عادي');self.assertEqual(revision.digest,digest)
        self.refresh();self.assertEqual(self.row.agenda_version,2);self.assertEqual(self.row.agenda_revisions.get(version=2).reason,'سبب التعديل')
        self.assertTrue(services.verify_chain(f'meeting:{self.row.pk}'))
    def test_reason_version_and_parent_cycles(self):
        with self.assertRaises(ValidationError):ops.save_item(self.actor,self.row,{'title':'س'},'',self.refresh(),self.item)
        with self.assertRaises(ValidationError):ops.save_item(self.actor,self.row,{'title':'س'},'سبب',0,self.item)
        child=self.item_new('فرعي',parent=self.item)
        with self.assertRaises(ValidationError):ops.save_item(self.actor,self.row,{'parent':child},'دوران',self.refresh(),self.item)
        with self.assertRaises(ValidationError):ops.reorder(self.actor,self.row,[child.pk,self.item.pk],'ترتيب',self.refresh())
        with self.assertRaises(ValidationError):ops.remove_item(self.actor,self.row,self.item,'حذف',self.refresh())
        ops.reorder(self.actor,self.row,[self.item.pk,child.pk],'ترتيب صحيح',self.refresh())
    def test_cross_meeting_parent_denied(self):
        other=self.record(number='MTG-OTHER')
        with self.assertRaises(ValidationError):ops.save_item(self.actor,other,{'title':'خارج الاجتماع','kind':'discussion','duration':15,'presenter':self.members['chair'],'owner':self.members['secretary'],'parent':self.item},'اختبار',other.version)
    def test_secret_agenda_filtered_in_all_versions_and_downloads(self):
        secret=self.item_new('موضوع مقيد',kind='secret');file=self.upload(item=secret);self.publish();self.refresh()
        for name in ['member','observer']:
            self.api.force_authenticate(self.users[name]);r=self.api.get(f'/api/governance-meetings/{self.row.pk}/agenda/')
            self.assertEqual(r.status_code,200);self.assertNotIn('موضوع مقيد',str(r.data));self.assertFalse(ops.file_allowed(self.users[name],file))
        self.assertTrue(ops.file_allowed(self.actor,file))
    def test_observer_cannot_mutate_even_with_invalid_payload(self):
        self.api.force_authenticate(self.users['observer'])
        for action in ['agenda','attendance','files','proxies']:
            r=self.api.post(f'/api/governance-meetings/{self.row.pk}/{action}/',{},format='json');self.assertEqual(r.status_code,403,(action,r.data))
    def test_current_read_ack_is_idempotent_and_immutable(self):
        first=self.publish()
        self.refresh();a=ops.acknowledge(self.users['member'],self.row,first);self.assertEqual(ops.acknowledge(self.users['member'],self.row,first).pk,a.pk)
        self.publish();self.refresh()
        with self.assertRaises(ValidationError):ops.acknowledge(self.users['member'],self.row,first)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():ReadAcknowledgment.objects.filter(pk=a.pk).delete()
    def test_snapshots_database_immutable(self):
        self.issue()
        for model in [AgendaRevision,Roster,Eligibility]:
            field='reason' if model==AgendaRevision else ('quorum_reference' if model==Roster else 'name')
            with self.assertRaises(DatabaseError):
                with transaction.atomic():model.objects.all().update(**{field:'عبث'})
            with self.assertRaises(DatabaseError):
                with transaction.atomic():model.objects.all().delete()
    def test_tokens_hashed_only_and_public_response_not_attendance(self):
        links=self.issue();token=links[0]['token'];inv=ops.find_invitation(token)
        self.assertEqual(inv.token_hash,hashlib.sha256(token.encode()).hexdigest());self.assertNotIn(token,str(list(Event.objects.values())))
        r=self.client.post('/meetings/rsvp/',{'token':token,'answer':'confirm'});self.assertEqual(r.status_code,200);self.assertIn('no-store',r['Cache-Control']);self.assertEqual(r['Referrer-Policy'],'no-referrer')
        inv.refresh_from_db();self.assertEqual(inv.response,'confirm');self.assertFalse(Attendance.objects.exists());self.assertEqual(ops.quorum(self.row)['actual_present'],0)
        self.assertTrue(services.verify_chain(f'meeting:{self.row.pk}'))
    def test_public_form_csrf_and_token_expiry_revocation(self):
        links=self.issue();token=links[0]['token'];inv=ops.find_invitation(token)
        strict=Client(enforce_csrf_checks=True);self.assertEqual(strict.post('/meetings/rsvp/',{'token':token,'answer':'confirm'}).status_code,403)
        with patch('meetinghub.session_services.timezone.now',return_value=self.row.rsvp_deadline):
            with self.assertRaises(ValidationError):ops.find_invitation(token)
        ops.revoke_invitation(self.actor,self.row,inv,'رابط مكشوف')
        with self.assertRaises(ValidationError):ops.find_invitation(token)
    def test_reissue_invalidates_old_links_and_freezes_denominator(self):
        links=self.issue();roster=ops.live_roster(self.row)
        self.members['member'].role='observer';self.members['member'].save()
        self.assertEqual(ops.quorum(self.row)['eligible'],3);self.assertEqual(roster.required,2)
        self.issue()
        with self.assertRaises(ValidationError):ops.find_invitation(links[0]['token'])
        self.assertEqual(ops.live_roster(self.row).eligible_count,2)
    def test_frozen_settings_require_reset_and_generation_never_reused(self):
        links=self.issue()
        with self.assertRaises(ValidationError):services.save_meeting(self.actor,{'quorum_percent':60},list(self.row.invitees.all()),self.row,'تغيير النصاب',self.refresh())
        ops.reset_invitations(self.actor,self.row,'إعادة جدولة',self.refresh());self.refresh()
        with self.assertRaises(ValidationError):ops.find_invitation(links[0]['token'])
        self.row=services.save_meeting(self.actor,{'quorum_percent':60},list(self.row.invitees.all()),self.row,'تغيير بعد الإبطال',self.row.version)
        self.issue();self.assertEqual(self.row.roster_generation,2)
    def test_actual_presence_exit_disconnection_and_reentry(self):
        self.issue();p=self.person('member')
        with self.assertRaises(ValidationError):ops.mark_attendance(self.actor,self.row,p,'present','تحقق','تسجيل')
        with patch('meetinghub.session_services.timezone.now',return_value=self.row.starts_at):
            a=ops.mark_attendance(self.actor,self.row,p,'remote','تحقق الهوية','اتصال');first=a.first_entered_at
            self.assertEqual(ops.quorum(self.row)['actual_present'],1)
            ops.mark_attendance(self.actor,self.row,p,'disconnected','فقد الاتصال','انقطاع');self.assertEqual(ops.quorum(self.row)['actual_present'],0)
            a=ops.mark_attendance(self.actor,self.row,p,'present','تحقق في القاعة','عودة');self.assertEqual(a.first_entered_at,first)
            ops.mark_attendance(self.actor,self.row,p,'left','سجل خروج','خروج');self.assertEqual(ops.quorum(self.row)['actual_present'],0)
    def test_actual_quorum_requires_real_eligible_members_and_open_window(self):
        self.issue()
        with patch('meetinghub.session_services.timezone.now',return_value=self.row.starts_at):
            for name in ['member','chair','observer']:ops.mark_attendance(self.actor,self.row,self.person(name),'present','هوية','دخول')
            self.assertEqual(ops.quorum(self.row)['actual_present'],2);self.assertTrue(ops.quorum(self.row)['actual_quorum'])
        with patch('meetinghub.session_services.timezone.now',return_value=self.row.ends_at+timedelta(seconds=1)):self.assertFalse(ops.quorum(self.row)['actual_quorum'])
    def test_proxy_scope_limit_and_no_double_count(self):
        self.issue();doc=self.upload(purpose='proxy');p=self.person('member');d=self.person('chair')
        proxy=ops.approve_proxy(self.actor,self.row,p,d,doc,self.row.ends_at,True,False,'توكيل حضور فقط')
        self.assertFalse(proxy.voting_allowed);self.assertFalse(ops.file_allowed(self.users['observer'],doc));self.assertTrue(ops.file_allowed(self.users['member'],doc))
        with self.assertRaises(ValidationError):ops.approve_proxy(self.actor,self.row,self.person('secretary'),d,doc,self.row.ends_at,True,False,'تجاوز الحد')
        with patch('meetinghub.session_services.timezone.now',return_value=self.row.starts_at):
            ops.mark_attendance(self.actor,self.row,d,'present','هوية','دخول');self.assertEqual(ops.quorum(self.row)['actual_present'],2)
            ops.mark_attendance(self.actor,self.row,p,'present','هوية','دخول');self.assertEqual(ops.quorum(self.row)['actual_present'],2)
        ops.revoke_proxy(self.actor,self.row,proxy,'إلغاء');self.assertFalse(ops.file_allowed(self.users['member'],doc))
    def test_proxy_self_observer_and_invalid_document_rejected(self):
        self.issue();doc=self.upload();p=self.person('member');d=self.person('chair')
        for principal,delegate in [(p,p),(p,self.person('observer')),(p,d)]:
            with self.assertRaises(ValidationError):ops.approve_proxy(self.actor,self.row,principal,delegate,doc,self.row.ends_at,True,True,'اختبار رفض')
    def test_upload_encrypted_and_download_authorized_only(self):
        file=self.upload(item=self.item);self.publish();self.refresh()
        with file.file.storage.open(file.file.name,'rb') as f:self.assertEqual(f.read(),b'fictional evidence')
        from pathlib import Path
        self.assertNotIn(b'fictional evidence',Path(file.file.path).read_bytes())
        self.client.force_login(self.users['member']);r=self.client.get(f'/meetings/files/{file.pk}/');self.assertEqual(r.status_code,200);self.assertEqual(b''.join(r.streaming_content),b'fictional evidence')
        self.client.force_login(self.users['outsider']);self.assertEqual(self.client.get(f'/meetings/files/{file.pk}/').status_code,404)
        with self.assertRaises(ValidationError):ops.upload_file(self.actor,self.row,SimpleUploadedFile('bad.exe',b'MZ'),'رفض',self.refresh())
    def test_reminders_due_deduplicated_and_answer_cancels_response_reminders(self):
        self.issue();now=timezone.now();Reminder.objects.all().update(due_at=now-timedelta(seconds=1))
        p=self.person('member');ops.respond(p.invitation,'confirm',self.users['member'])
        count=ops.run_reminders(now);self.assertEqual(count,10);self.assertEqual(ops.run_reminders(now),0)
        self.assertEqual(Reminder.objects.filter(invitation=p.invitation,cancelled_at__isnull=False).count(),2)
        self.assertTrue(services.verify_chain(f'meeting:{self.row.pk}'))
    def test_cancel_invalidates_links_and_pending_reminders(self):
        links=self.issue();self.row=services.meeting_action(self.actor,self.row,'cancel','إلغاء الاجتماع',self.refresh())
        with self.assertRaises(ValidationError):ops.find_invitation(links[0]['token'])
        self.assertEqual(Reminder.objects.filter(cancelled_at__isnull=True).count(),0)
    def test_html_and_api_personal_scope(self):
        self.issue()
        for name in ['secretary','member','observer']:
            self.client.force_login(self.users[name])
            for page in ['agenda','attendance']:self.assertEqual(self.client.get(f'/meetings/{self.row.pk}/{page}/').status_code,200)
        self.api.force_authenticate(self.users['member']);r=self.api.get(f'/api/governance-meetings/{self.row.pk}/attendance/');self.assertEqual(len(r.data['people']),1)
        r=self.api.post(f'/api/governance-meetings/{self.row.pk}/attendance/',{'action':'respond','answer':'confirm'},format='json');self.assertEqual(r.status_code,200,r.data)
    def test_api_issue_once_links_no_store_and_bad_token(self):
        self.publish();self.row=services.meeting_action(self.actor,self.row,'schedule','جدولة',self.refresh())
        r=self.api.post(f'/api/governance-meetings/{self.row.pk}/attendance/',{'action':'issue','reason':'دعوة','version':self.refresh()},format='json')
        self.assertEqual(r.status_code,200,r.data);self.assertEqual(r['Cache-Control'],'no-store');self.assertIn('/meetings/rsvp/#',r.data['links'][0]['url']);self.assertNotIn('token',r.data['links'][0])
        with self.assertRaises(ValidationError):ops.find_invitation('x'*43)
    def test_respond_can_change_but_not_impersonate(self):
        self.issue();p=self.person('member');ops.respond(p.invitation,'confirm',self.users['member']);ops.respond(p.invitation,'decline',self.users['member'])
        p.invitation.refresh_from_db();self.assertEqual(p.invitation.response,'decline');self.assertIsNotNone(p.invitation.responded_at)
        with self.assertRaises(PermissionDenied):ops.respond(p.invitation,'confirm',self.users['chair'])
        self.assertEqual(ops.quorum(self.row)['confirmed'],0)
    def test_rsvp_throttle_does_not_retain_raw_token(self):
        token=self.issue()[0]['token']
        for _ in range(30):self.client.post('/meetings/rsvp/',{'token':token})
        r=self.client.post('/meetings/rsvp/',{'token':token});self.assertEqual(r.status_code,429);self.assertIn('no-store',r['Cache-Control'])
        self.assertNotIn(token,str(list(RSVPAttempt.objects.values())))
    def test_reset_cannot_remove_attendance_or_revive_links(self):
        self.issue()
        with patch('meetinghub.session_services.timezone.now',return_value=self.row.starts_at-timedelta(minutes=20)):
            ops.mark_attendance(self.actor,self.row,self.person('chair'),'present','هوية','دخول')
            with self.assertRaises(ValidationError):ops.reset_invitations(self.actor,self.row,'إعادة إصدار',self.refresh())
            with self.assertRaises(ValidationError):ops.issue_invitations(self.actor,self.row,'إعادة إصدار',self.refresh())
    def test_voting_only_proxy_does_not_change_attendance_quorum(self):
        self.issue();doc=self.upload(purpose='proxy');p=self.person('member');d=self.person('chair')
        ops.approve_proxy(self.actor,self.row,p,d,doc,self.row.ends_at,False,True,'تصويت فقط')
        with patch('meetinghub.session_services.timezone.now',return_value=self.row.starts_at):
            ops.mark_attendance(self.actor,self.row,d,'present','هوية','دخول');self.assertEqual(ops.quorum(self.row)['actual_present'],1)
    def test_proxy_chain_and_expiry(self):
        self.issue();doc=self.upload(purpose='proxy');p=self.person('member');d=self.person('chair')
        expires=self.row.starts_at+timedelta(minutes=10)
        ops.approve_proxy(self.actor,self.row,p,d,doc,expires,True,True,'توكيل')
        with self.assertRaises(ValidationError):ops.approve_proxy(self.actor,self.row,d,self.person('secretary'),doc,self.row.ends_at,True,True,'سلسلة')
        with patch('meetinghub.session_services.timezone.now',return_value=expires+timedelta(seconds=1)):
            ops.mark_attendance(self.actor,self.row,d,'present','هوية','دخول');self.assertEqual(ops.quorum(self.row)['actual_present'],1)
    def test_upload_limits_and_no_proxy_document_in_public_agenda(self):
        with self.assertRaises(ValidationError):ops.upload_file(self.actor,self.row,SimpleUploadedFile('huge.txt',b'x'*(10*1024*1024+1)),'حد',self.refresh())
        with self.assertRaises(ValidationError):ops.upload_file(self.actor,self.row,SimpleUploadedFile('proxy.txt',b'doc'),'توكيل',self.refresh(),self.item,'proxy')
    def test_secret_public_rsvp_does_not_disclose_subject(self):
        self.row=services.save_meeting(self.actor,{'secrecy':'secret'},list(self.row.invitees.all()),self.row,'سرية',self.refresh())
        token=self.issue()[0]['token'];r=self.client.post('/meetings/rsvp/',{'token':token});self.assertNotContains(r,'اجتماع تأسيسي');self.assertContains(r,'اجتماع مقيد')
    def test_scheduler_once_dispatches_internal_reminders(self):
        from django.core.management import call_command
        from io import StringIO
        self.issue();Reminder.objects.all().update(due_at=timezone.now()-timedelta(seconds=1))
        # TestCase owns a transaction; the standalone scheduler check covers real cleanup.
        with patch('core.management.commands.run_scheduler.close_old_connections'):
            call_command('run_scheduler',once=True,stdout=StringIO());self.assertEqual(Reminder.objects.filter(delivered_at__isnull=False).count(),12)
            call_command('run_scheduler',once=True,stdout=StringIO());self.assertEqual(Reminder.objects.filter(delivered_at__isnull=False).count(),12)
    def test_personal_invitation_list_uses_session_identity(self):
        self.issue();self.client.force_login(self.users['member']);r=self.client.get('/meetings/');self.assertContains(r,'دعواتي');self.assertEqual(r.context['personal'].count(),1)
        self.client.force_login(self.users['outsider']);r=self.client.get('/meetings/');self.assertEqual(r.context['personal'].count(),0)
    def test_reclassifying_secret_hides_previous_public_revision(self):
        first=self.publish();self.refresh()
        ops.save_item(self.actor,self.row,{'kind':'secret'},'تصنيف مقيد',self.row.version,self.item);self.refresh()
        self.assertEqual(ops.revision_items(self.users['member'],self.row,first),[])
        self.assertEqual(len(ops.revision_items(self.actor,self.row,first)),1)
