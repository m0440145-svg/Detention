"""Verify upgrading legacy indicators in an isolated SQLite database."""
import os,sys,tempfile,importlib
from pathlib import Path
os.environ.update(DEBUG='1',DJANGO_SETTINGS_MODULE='config.settings')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from django.conf import settings
with tempfile.TemporaryDirectory() as temp:
 settings.DATABASES['default']={'ENGINE':'django.db.backends.sqlite3','NAME':Path(temp)/'migration.sqlite3'}
 import django
 django.setup()
 from django.db import connection,DatabaseError,transaction
 from django.db.migrations.executor import MigrationExecutor
 executor=MigrationExecutor(connection); executor.migrate([('core','0004_catalog')])
 apps=executor.loader.project_state([('core','0004_catalog')]).apps
 User=apps.get_model('core','User');Unit=apps.get_model('core','Unit');Task=apps.get_model('core','Task');Audit=apps.get_model('core','Audit');Catalog=apps.get_model('core','Catalog')
 from django.utils import timezone
 today=timezone.localdate()
 u=User.objects.create(username='migration',email='migration@example.invalid',employee_number='MIG')
 unit=Unit.objects.create(name='اختبار الترحيل')
 scenarios=[('blocked',40,'waiting'),('overdue',50,'external'),('blocked',0,None),('overdue',100,None)]
 ids=[]; original={}
 for status,progress,prior in scenarios:
  t=Task.objects.create(title='ترحيل',description='اختبار',unit=unit,owner=u,created_by=u,start_date=today,due_date=today,expected_result='نتيجة',success_indicator='مؤشر',status=status,progress=progress)
  ids.append(t.pk)
  if prior:
   a=Audit.objects.create(task=t,action='قديم',old={'status':prior},new={'status':status});original[a.pk]=(a.old,a.new)
 for key in ['blocked','overdue']:Catalog.objects.create(kind='status',key=key,label=key)
 Meeting=apps.get_model('core','Meeting'); Decision=apps.get_model('core','Decision')
 meeting=Meeting.objects.create(number='LEGACY-MTG',name='اجتماع سابق',committee='لجنة',date=today)
 decision=Decision.objects.create(number='LEGACY-DEC',meeting=meeting,text='قرار سابق',date=today,followup_owner=u)
 legacy=Audit.objects.create(actor=u,action='حفظ قرار اجتماع',new={'id':decision.pk})
 executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
 from core.models import Task as NewTask,Audit as NewAudit,Catalog as NewCatalog,Decision as NewDecision
 assert list(NewTask.objects.filter(pk__in=ids).order_by('pk').values_list('status',flat=True))==['waiting','external','assigned','approval']
 for pk,snapshots in original.items():
  a=NewAudit.objects.get(pk=pk);assert (a.old,a.new)==snapshots
 assert NewAudit.objects.filter(action='فصل مؤشرات التأخر والتعثر عن الحالة').count()==4
 assert not NewCatalog.objects.filter(kind='status',key__in=['blocked','overdue']).exists()
 try:
  with transaction.atomic():NewTask.objects.filter(pk=ids[0]).update(status='overdue')
 except DatabaseError:pass
 else:raise AssertionError('Lifecycle constraint missing')
 try:
  with transaction.atomic():NewAudit.objects.filter(pk=next(iter(original))).update(action='tampered')
 except DatabaseError:pass
 else:raise AssertionError('Audit trigger missing')
 migrated=NewDecision.objects.get(pk=decision.pk)
 assert migrated.due_date is None and migrated.issuing_authority==''
 linked=NewAudit.objects.get(decision=migrated,new__original_audit_id=legacy.pk)
 assert linked.actor_id==u.pk and linked.event_time==legacy.created_at
 assert NewAudit.objects.get(pk=legacy.pk).decision_id is None
 assert NewAudit.objects.get(pk=legacy.pk).new=={'id':decision.pk}
 print('Migration verified: recovered lifecycle, fallback, preserved immutable audit, removed catalogs, enforced constraints and linked legacy decision audit without modifying originals.')
