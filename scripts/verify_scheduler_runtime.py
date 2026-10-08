"""Run the real scheduler lifecycle against a disposable database, with no web request."""
import os,sys,tempfile,io
from pathlib import Path
from datetime import timedelta
os.environ.update(DEBUG='1',DJANGO_SETTINGS_MODULE='config.settings')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from django.conf import settings
with tempfile.TemporaryDirectory(prefix='scheduler-acceptance-') as temp:
    settings.DATABASES['default']={'ENGINE':'django.db.backends.sqlite3','NAME':Path(temp)/'runtime.sqlite3'}
    import django
    django.setup()
    from django.core.management import call_command
    from django.utils import timezone
    from django.db import connections
    from core.models import Unit,User,Role,Escalation,Notification,Audit
    from core.services import create_task
    call_command('migrate',verbosity=0)
    unit=Unit.objects.create(name='اختبار المجدول المستقل')
    people={}
    for role in [Role.EXECUTIVE,Role.ASSISTANT,Role.HEAD,Role.EMPLOYEE]:
        user=User.objects.create_user(username=role,email=role+'@example.invalid',employee_number=role,role=role)
        user.units.add(unit); people[role]=user
    unit.head=people[Role.HEAD]; unit.save()
    today=timezone.localdate()
    task=create_task(people[Role.EXECUTIVE],{'title':'اختبار مستقل','description':'اختبار','unit':unit,'owner':people[Role.EMPLOYEE],'start_date':today-timedelta(days=10),'due_date':today-timedelta(days=3),'expected_result':'نتيجة','success_indicator':'مؤشر'},[])
    output=io.StringIO(); call_command('run_scheduler',once=True,stdout=output)
    assert 'rules evaluated' in output.getvalue()
    assert set(Escalation.objects.filter(task=task).values_list('level',flat=True))=={1,2}
    assert Audit.objects.filter(task=task,action='تصعيد آلي').count()==2
    before=Notification.objects.count()
    call_command('run_scheduler',once=True,stdout=io.StringIO())
    assert Notification.objects.count()==before
    connections.close_all()
print('Standalone scheduler verified: real connection cleanup, configured escalation, audit and repeat deduplication without web traffic.')
