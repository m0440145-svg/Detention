from datetime import timedelta
from decimal import Decimal
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction,DatabaseError
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User,Role,Meeting,Decision
from .models import *
from . import services

@override_settings(DEBUG=True,SECURE_SSL_REDIRECT=False,MFA_REQUIRED=False)
class MeetingFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today=timezone.localdate();cls.users={}
        for name,role in [('admin','admin'),('exec','executive'),('chair','board'),('secretary','board'),('member','board'),('observer','viewer'),('outsider','employee')]:
            cls.users[name]=User.objects.create_user(username=name,email=name+'@example.invalid',employee_number=name,role=role)
        cls.members={}
        for name,role in [('chair','chair'),('secretary','secretary'),('member','member'),('observer','observer')]:
            cls.members[name]=services.save_member(cls.users['admin'],{'user':cls.users[name],'role':role,'starts':today-timedelta(days=1)},reason='إضافة عضو للاختبار')
        cls.committee=services.save_committee(cls.users['admin'],{'name':'لجنة اختبار','kind':'committee','mandate':'اختبار الحوكمة','chair':cls.members['chair'],'secretary':cls.members['secretary'],'starts':today-timedelta(days=1)},list(cls.members.values()),reason='إنشاء لجنة للاختبار')
    def setUp(self):self.api=APIClient();self.api.force_authenticate(self.users['secretary'])
    def data(self,**extra):
        starts=timezone.now()+timedelta(days=4)
        return {'number':'MTG-TEST-1','name':'اجتماع تأسيسي','committee':self.committee,'kind':'ordinary','mode':'onsite','starts_at':starts,'ends_at':starts+timedelta(hours=2),'venue':'قاعة اختبار','connection_url':'','rsvp_deadline':starts-timedelta(hours=6),'quorum_percent':Decimal('50.01'),'quorum_reference':'النظام الأساسي المعتمد — مرجع اختبار','allow_proxy':False,'proxy_limit':0,'secrecy':'internal',**extra}
    def record(self,**extra):return services.save_meeting(self.users['secretary'],self.data(**extra),list(self.members.values()),reason='إنشاء اجتماع اختبار')
    def payload(self,**extra):
        values=self.data(**extra);values['committee']=self.committee.pk;values['invitees']=[m.pk for m in self.members.values()];values['reason']='إنشاء من API'
        for k in ['starts_at','ends_at','rsvp_deadline']:values[k]=values[k].isoformat()
        values['quorum_percent']=str(values['quorum_percent']);return values
    def test_creation_uses_core_meeting_and_quorum_excludes_observer(self):
        row=self.record();self.assertEqual(Meeting.objects.count(),1);self.assertEqual(row.meeting.name,'اجتماع تأسيسي')
        q=services.quorum(row);self.assertEqual(q['eligible'],3);self.assertEqual(q['required'],2);self.assertIsNone(q['actual_present']);self.assertIsNone(q['confirmed'])
        self.assertTrue(services.verify_chain(f'meeting:{row.pk}'))
    def test_api_persistence_and_required_quorum(self):
        payload=self.payload();payload.pop('quorum_percent');response=self.api.post('/api/governance-meetings/',payload,format='json');self.assertEqual(response.status_code,400)
        response=self.api.post('/api/governance-meetings/',self.payload(),format='json');self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(self.api.get(f"/api/governance-meetings/{response.data['id']}/").data['core_meeting_id'],Meeting.objects.get().pk)
    def test_invalid_quorum_and_times_roll_back_all_tables(self):
        for percent in [Decimal('0'),Decimal('100.01')]:
            with self.assertRaises(ValidationError):self.record(quorum_percent=percent)
        data=self.data();data['ends_at']=data['starts_at']
        with self.assertRaises(ValidationError):services.save_meeting(self.users['secretary'],data,list(self.members.values()),reason='اختبار')
        self.assertEqual(Meeting.objects.count(),0);self.assertEqual(MeetingRecord.objects.count(),0)
    def test_admin_and_observer_cannot_create_operational_meeting(self):
        for name in ['admin','observer','member','outsider']:
            with self.assertRaises(PermissionDenied):services.save_meeting(self.users[name],self.data(),list(self.members.values()),reason='محاولة')
        self.assertFalse(__import__('core.policy',fromlist=['can_manage']).can_manage(self.users['secretary']))
    def test_secret_record_has_no_horizontal_or_legacy_api_leak(self):
        row=services.save_meeting(self.users['secretary'],self.data(secrecy='secret'),[self.members['chair'],self.members['secretary']],reason='اجتماع مقيد')
        for name in ['outsider','member','observer','admin','exec']:
            self.api.force_authenticate(self.users[name]);self.assertEqual(self.api.get(f'/api/governance-meetings/{row.pk}/').status_code,404)
            self.assertEqual(self.api.get(f'/api/meetings/{row.meeting_id}/').status_code,404)
            self.client.force_login(self.users[name]);self.assertEqual(self.client.get(f'/meetings/{row.pk}/').status_code,404)
    def test_cross_committee_invitee_and_expired_membership_denied(self):
        outsider=services.save_member(self.users['admin'],{'user':self.users['outsider'],'role':'member','starts':timezone.localdate()},reason='عضو آخر')
        with self.assertRaises(ValidationError):services.save_meeting(self.users['secretary'],self.data(),[outsider],reason='خارج اللجنة')
        member=self.members['member'];member.ends=timezone.localdate()+timedelta(days=1);member.save()
        with self.assertRaises(ValidationError):self.record()
    def test_optimistic_edit_and_api_readonly_lifecycle(self):
        row=self.record();updated=services.save_meeting(self.users['secretary'],{'name':'عنوان جديد'},list(row.invitees.all()),row,'تعديل',row.version)
        with self.assertRaises(ValidationError):services.save_meeting(self.users['secretary'],{'name':'تعديل قديم'},list(row.invitees.all()),row,'تعارض',1)
        self.assertEqual(updated.version,2)
        response=self.api.patch(f'/api/governance-meetings/{row.pk}/',{'status':'scheduled','version':2,'reason':'محاولة تغيير حالة'},format='json');self.assertEqual(response.status_code,200,response.data);self.assertEqual(response.data['status'],'draft')
    def test_schedule_cancel_archive_restore_are_audited(self):
        row=self.record()
        for action in ['schedule','cancel','archive','restore']:
            row=services.meeting_action(self.users['chair'],row,action,'سبب '+action,row.version)
        self.assertEqual(row.status,'draft');self.assertFalse(row.archived);self.assertTrue(services.verify_chain(f'meeting:{row.pk}'))
    def test_logical_archive_preserves_data_and_decisions_block_it(self):
        row=self.record();decision=Decision.objects.create(number='DEC-1',meeting=row.meeting,text='نص قرار',date=timezone.localdate(),followup_owner=self.users['exec'])
        with self.assertRaises(ValidationError):services.meeting_action(self.users['secretary'],row,'archive','أرشفة',row.version)
        self.assertEqual(decision.meeting_id,row.meeting_id)
    def test_legacy_meeting_is_extended_without_duplicating_decision_fk(self):
        old=Meeting.objects.create(number='OLD-1',name='اجتماع سابق',committee='لجنة قديمة',date=timezone.localdate())
        decision=Decision.objects.create(number='DEC-LEGACY',meeting=old,text='نص',date=timezone.localdate(),followup_owner=self.users['exec'])
        row=services.save_meeting(self.users['exec'],self.data(number=old.number),list(self.members.values()),reason='توسعة الاجتماع',legacy=old)
        decision.refresh_from_db();self.assertEqual(row.meeting_id,old.pk);self.assertEqual(decision.meeting_id,old.pk);self.assertEqual(Meeting.objects.count(),1)
    def test_national_identity_encrypted_unique_and_absent_from_api_audit(self):
        row=services.save_member(self.users['admin'],{'user':self.users['outsider'],'role':'member','starts':timezone.localdate()},reason='عضوية',national_id='1234567890')
        self.assertNotIn('1234567890',row.identity_encrypted)
        self.api.force_authenticate(self.users['admin']);response=self.api.get(f'/api/meeting-members/{row.pk}/');self.assertNotIn('1234567890',str(response.data));self.assertNotIn('identity_hash',response.data)
        self.assertNotIn('1234567890',str(list(Event.objects.filter(stream__scope=f'member:{row.pk}').values())))
        with self.assertRaises(ValidationError):services.save_member(self.users['admin'],{'user':self.users['exec'],'role':'member','starts':timezone.localdate()},reason='تكرار',national_id='1234567890')
    def test_member_role_changes_admin_only_and_recorded(self):
        with self.assertRaises(PermissionDenied):services.save_member(self.users['secretary'],{'role':'chair'},self.members['member'],'تغيير دور')
        services.save_member(self.users['admin'],{'role':'observer'},self.members['secretary'],'تغيير دور')
        self.assertFalse(services.can_manage(self.users['secretary'],self.committee))
        self.api.force_authenticate(self.users['observer']);self.assertEqual(self.api.get('/api/meeting-members/').status_code,403)
    def test_immutable_audit_enforced_in_database(self):
        row=self.record();event=Event.objects.filter(stream__scope=f'meeting:{row.pk}').first()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():Event.objects.filter(pk=event.pk).update(reason='عبث')
        with self.assertRaises(DatabaseError):
            with transaction.atomic():Event.objects.filter(pk=event.pk).delete()
        self.assertTrue(services.verify_chain(f'meeting:{row.pk}'))
    def test_html_forms_and_empty_states(self):
        self.client.force_login(self.users['secretary']);self.assertContains(self.client.get('/meetings/'),'لا اجتماعات ضمن نطاقك')
        row=self.record();response=self.client.get(f'/meetings/{row.pk}/');self.assertContains(response,'النصاب المعتمد');self.assertContains(response,'لم يبدأ تسجيل الحضور')
        self.assertEqual(self.client.get(f'/manage/meeting/{row.meeting_id}/').status_code,302)
        self.client.force_login(self.users['admin']);self.assertContains(self.client.get('/meetings/members/'),'الأعضاء وأدوار الاجتماعات');self.assertEqual(self.client.get('/meetings/new/').status_code,403)
    def test_no_delete_or_invalid_proxy_link_and_rsvp(self):
        row=self.record();self.assertEqual(self.api.delete(f'/api/governance-meetings/{row.pk}/').status_code,405)
        for extra in [{'allow_proxy':True,'proxy_limit':0},{'mode':'remote','connection_url':''},{'connection_url':'http://example.invalid'},{'rsvp_deadline':timezone.now()+timedelta(days=8)}]:
            with self.assertRaises(ValidationError):services.save_meeting(self.users['secretary'],self.data(number='MTG-OTHER',**extra),list(self.members.values()),reason='تحقق')
    def test_member_archive_restore_and_committee_lock(self):
        with self.assertRaises(ValidationError):services.archive_member(self.users['admin'],self.members['chair'],True,'أرشفة')
        member=services.archive_member(self.users['admin'],self.members['member'],True,'أرشفة');self.assertFalse(member.active)
        member=services.archive_member(self.users['admin'],member,False,'استرجاع');self.assertTrue(member.active)
        self.record()
        with self.assertRaises(ValidationError):services.archive_committee(self.users['admin'],self.committee,True,'أرشفة')

    def test_legacy_api_cannot_bypass_required_quorum(self):
        self.api.force_authenticate(self.users['exec'])
        result=self.api.post('/api/meetings/',{'number':'BYPASS','name':'اسم','committee':'نص','date':str(timezone.localdate())},format='json')
        self.assertEqual(result.status_code,400);self.assertEqual(Meeting.objects.count(),0)
    def test_secret_meeting_decision_link_is_blocked_until_scope_extension(self):
        row=self.record(secrecy='secret')
        from core.services import save_decision
        with self.assertRaises(ValidationError):save_decision(self.users['exec'],{'meeting':row.meeting,'number':'SECRET-DEC','text':'نص','date':timezone.localdate(),'due_date':timezone.localdate(),'issuing_authority':'committee','followup_owner':self.users['exec']})
        self.assertFalse(Decision.objects.exists())
        self.client.force_login(self.users['exec']);self.assertNotContains(self.client.get('/manage/decision/new/'),'اجتماع تأسيسي')

    def test_quorum_denominator_cannot_be_reduced_by_inviting_one_member(self):
        row=services.save_meeting(self.users['secretary'],self.data(),[self.members['chair']],reason='دعوة فردية للاختبار')
        q=services.quorum(row);self.assertEqual(q['eligible'],3);self.assertEqual(q['invited_eligible'],1);self.assertEqual(q['required'],2)

    def test_quorum_decimal_lower_and_upper_bound_are_valid(self):
        low=self.record(quorum_percent=Decimal('0.01'));self.assertEqual(services.quorum(low)['required'],1)
        high=self.record(number='MTG-HIGH',quorum_percent=Decimal('100'));self.assertEqual(services.quorum(high)['required'],3)
    def test_forbidden_api_write_is_rejected_before_data_validation(self):
        self.api.force_authenticate(self.users['observer'])
        for url in ['/api/meeting-members/','/api/meeting-committees/','/api/governance-meetings/']:
            self.assertEqual(self.api.post(url,{},format='json').status_code,403,url)
        row=self.record();self.assertEqual(self.api.patch(f'/api/governance-meetings/{row.pk}/',{},format='json').status_code,403)
