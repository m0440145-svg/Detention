"""Only explicit DEBUG demonstration; never imported by migrations or app startup."""
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.utils import timezone
from core.models import User,Meeting
from meetinghub.models import Member,Committee,MeetingRecord
from meetinghub import services

class Command(BaseCommand):
    help='Create visibly fictional meeting data in an explicitly isolated DEBUG demo.'
    def add_arguments(self,parser):parser.add_argument('--isolated-demo',action='store_true')
    def handle(self,*args,**options):
        if not settings.DEBUG or not options['isolated_demo']:raise CommandError('Demo creation requires DEBUG=1 and --isolated-demo; use a separate database.')
        admin=User.objects.get(username='demo-admin');today=timezone.localdate()
        members=[]
        for username,role in [('demo-board','chair'),('demo-02','secretary'),('demo-09','member'),('demo-viewer','observer')]:
            user=User.objects.get(username=username)
            row=Member.objects.filter(user=user).first()
            if not row:row=services.save_member(admin,{'user':user,'role':role,'starts':today-timedelta(days=30),'organization':'بيانات عرض تجريبية','capacity':'صفة تجريبية'},reason='عضوية تجريبية لا تمثل عضوية حقيقية')
            members.append(row)
        committee=Committee.objects.filter(name='لجنة عرض تجريبية — غير رسمية').first()
        if not committee:committee=services.save_committee(admin,{'name':'لجنة عرض تجريبية — غير رسمية','kind':'committee','mandate':'اختبار واجهات الاجتماعات فقط؛ لا تمثل لجنة أو تفويضًا فعليًا للجمعية.','chair':members[0],'secretary':members[1],'starts':today-timedelta(days=30)},members,reason='إنشاء لجنة عرض في قاعدة تجريبية مستقلة')
        if not MeetingRecord.objects.filter(meeting__number='DEMO-MTG-001').exists():
            starts=timezone.now()+timedelta(days=4)
            executive=User.objects.get(username='demo-01')
            row=services.save_meeting(executive,{'number':'DEMO-MTG-001','name':'اجتماع تجريبي — خطة التنفيذ','committee':committee,'kind':'ordinary','mode':'onsite','starts_at':starts,'ends_at':starts+timedelta(hours=2),'venue':'قاعة افتراضية للعرض','rsvp_deadline':starts-timedelta(days=1),'quorum_percent':Decimal('50'),'quorum_reference':'قيمة تجريبية فقط؛ لا تمثل نسبة معتمدة للجمعية','allow_proxy':False,'proxy_limit':0,'secrecy':'internal'},members,reason='إنشاء اجتماع تجريبي مستقل')
            services.meeting_action(executive,row,'schedule','جدولة تجريبية دون إرسال دعوات',row.version)
        self.stdout.write('Created explicitly fictional meeting members, committee and one scheduled meeting; no invitation sent.')
