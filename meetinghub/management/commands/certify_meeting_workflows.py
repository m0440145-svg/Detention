"""Certify internal workflows only after an actual isolated acceptance-test run."""
import os,re,subprocess,sys
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from core.models import User,Role
from meetinghub.models import FeatureState
from meetinghub import services
class Command(BaseCommand):
    help='Run internal meeting acceptance tests and persist the actual result; never certifies an external integration.'
    def add_arguments(self,parser):parser.add_argument('--owner',required=True)
    def handle(self,*args,**options):
        owner=User.objects.filter(username=options['owner'],role=Role.ADMIN,is_active=True).first()
        if not owner:raise CommandError('An active system administrator owner is required.')
        environment={**os.environ,'DEBUG':'1','DJANGO_SETTINGS_MODULE':'config.settings'}
        result=subprocess.run([sys.executable,str(settings.BASE_DIR/'manage.py'),'test','meetinghub.test_workflows','--noinput'],cwd=settings.BASE_DIR,env=environment,capture_output=True,text=True,timeout=300)
        evidence=re.search(r'Ran (\d+) tests',result.stderr)
        if not evidence:raise CommandError('No acceptance-test result was produced; no state changed.')
        successful=result.returncode==0 and int(evidence.group(1))>=44
        f,_=FeatureState.objects.get_or_create(key='internal_meeting_workflows',defaults={'name':'المسارات الداخلية للاجتماعات والحوكمة','requirements':'ترحيل قاعدة البيانات واختبارات قبول الصلاحيات وسلامة السجل'})
        from meetinghub.platform_services import record_provider_test
        record_provider_test(owner,f.key,successful,f'اختبارات قبول داخلية: {evidence.group(1)}؛ exit={result.returncode}؛ لا يشمل مزودًا خارجيًا أو اختبارًا بصريًا','automated_tests')
        if not successful:raise CommandError('Acceptance tests failed; feature state is error.')
        self.stdout.write(f'Internal acceptance tests passed ({evidence.group(1)}); external integrations unchanged.')
