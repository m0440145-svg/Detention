import os
from django.core.files.base import ContentFile
from datetime import timedelta
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.utils import timezone
from core.models import *
from core import services
UNITS=['مجلس الإدارة','المدير التنفيذي','السكرتارية','مساعد المدير التنفيذي','وحدة الاتصال المؤسسي','وحدة التقنية والتحول الرقمي','وحدة الخدمات الرعوية','وحدة خدمة المجتمع','وحدة الخدمات التنموية','وحدة التطوع','وحدة الموارد البشرية','وحدة الشؤون المالية والإدارية','وحدة المراجعة الداخلية','وحدة الاستثمار','وحدة التميز المؤسسي','وحدة الخدمات المساندة']
NAMES=['أحمد العتيبي','خالد الحربي','عبدالله السالم','يوسف الغامدي','ماجد الدوسري','صالح الرشيد','عمر القحطاني','حسن الزهراني','عبدالعزيز المطيري','إبراهيم الشمري','فهد العنزي','ناصر الفهيد','سلمان البدر','راشد الراجحي','سعد الناصر','وليد الحميد','عادل العمري','محمد الفارس']
TITLES=['إعداد خطة السلال الغذائية','تحديث بيانات المستفيدين','تجهيز تقرير الربع الثالث','إطلاق حملة التطوع','مراجعة عروض الموردين','تحديث سياسات الموارد البشرية','إعداد مادة البرنامج التدريبي','اعتماد ميزانية البرنامج','تصميم إعلان البرنامج','حجز القاعة التدريبية','مراجعة أمن أنظمة الجمعية','متابعة تكليفات اللجنة التنفيذية','تجهيز تقرير أثر التبرعات','إعداد دراسة فرصة استثمارية','توثيق شواهد التميز المؤسسي','صيانة تجهيزات المكتب','متابعة مبادرة سقيا الماء','تنسيق شراكات مجتمعية','مراجعة إجراءات التسجيل','تحسين نموذج رضا المستفيدين','أرشفة محاضر الاجتماعات','تجهيز خطة تدريب الموظفين','متابعة التزامات الشراكات','تحديث سجل المخاطر','تجهيز التقرير المالي','تطوير بوابة الخدمات','مراجعة خطة التواصل','إعداد دليل المتطوع','تحسين إجراءات صرف الدعم','إغلاق توصيات المراجعة']
class Command(BaseCommand):
    help='Create fictional Arabic demo records. Requires DEMO_PASSWORD and never runs automatically in production.'
    @transaction.atomic
    def handle(self,*args,**options):
        password=os.getenv('DEMO_PASSWORD')
        if not password or len(password)<12: raise CommandError('Set DEMO_PASSWORD to a unique password of at least 12 characters.')
        if User.objects.filter(username='demo-admin').exists():
            self.stdout.write('Demo already exists; no changes made.'); return
        today=timezone.localdate(); units=[Unit.objects.get_or_create(name=n,defaults={'description':'بيانات تجريبية — '+n})[0] for n in UNITS]
        users=[]
        for i,name in enumerate(NAMES):
            role=Role.ADMIN if i==0 else Role.EXECUTIVE if i==1 else Role.ASSISTANT if i==2 else Role.HEAD if i<9 else Role.EMPLOYEE
            u=User.objects.create_user(username='demo-admin' if i==0 else f'demo-{i:02d}',email=f'demo{i:02d}@example.invalid',password=password,employee_number=f'DEMO-{i:03d}',first_name=name.split(' ')[0],last_name=name.split(' ')[1],role=role,job_title=Role(role).label)
            u.units.add(units[4+(i%12)]); users.append(u)
        for i in range(4,16):
            head=users[3+(i-4)%6]; units[i].head=head; units[i].save(); head.units.add(units[i])
        for i in range(9,len(users)): users[i].manager=units[4+(i%12)].head; users[i].save()
        m=Meeting.objects.create(number='MTG-DEMO-01',name='اجتماع اللجنة التنفيذية — تجريبي',committee='اللجنة التنفيذية',date=today-timedelta(days=8))
        decisions=[services.save_decision(users[1],{'number':f'DEC-DEMO-{i+1:02d}','meeting':m,'text':text,'date':m.date,'due_date':today+timedelta(days=7+i*3),'issuing_authority':['board','assembly','committee'][i],'approved_minutes_number':f'MIN-DEMO-{i+1:02d}','followup_owner':users[1],'status':DecisionStatus.ACTIVE},minutes_file=ContentFile('محضر تجريبي للمعاينة فقط، لا يمثل محضرًا معتمدًا للجمعية.'.encode('utf-8'),name=f'minutes-demo-{i+1:02d}.txt')) for i,text in enumerate(['إطلاق برنامج لتنمية مهارات المستفيدين','تحسين آلية متابعة تنفيذ التكليفات','تطوير خدمات الجمعية الرقمية'])]
        for i,title in enumerate(TITLES):
            owner=users[9+i%9]; unit=owner.units.first(); due=today+timedelta(days=(i%12)-5)
            task=services.create_task(users[1],{'title':title+' — تجريبي','description':'تكليف تجريبي لا يمثل نشاطًا فعليًا للجمعية. يوثق التنفيذ والمخرجات المطلوبة.','unit':unit,'owner':owner,'start_date':today-timedelta(days=14),'due_date':due,'priority':Priority.values[i%5],'expected_result':'مخرج موثّق قابل للمراجعة والاعتماد','success_indicator':'اعتماد المخرج من رئيس الوحدة','decision':decisions[i%3] if i<10 else None,'source':Source.DECISION if i<10 else Source.PLAN},[users[9+(i+1)%9]])
            mode=i%7
            if mode==0:
                services.update_progress(owner,task,100,'اكتمل التنفيذ التجريبي','إنجاز وتسليم المخرج','لا يوجد'); task.refresh_from_db(); services.change_status(unit.head,task,Status.CLOSED,'اعتماد مخرج تجريبي')
            elif mode==1: services.update_progress(owner,task,100,'طلب اعتماد تجريبي','اكتمل العمل','لا يوجد')
            elif mode==2:
                services.report_obstacle(owner,task,{'kind':'انتظار اعتماد','description':'اعتماد مورد مطلوب — تجريبي','reason':'انتظار اعتماد المورد — تجريبي','caused_by':'وحدة أخرى','needs_decision':True,'requested_action':'مراجعة الطلب وتحديد الإجراء','intervention_owner':unit.head,'expected_resolution':today+timedelta(days=2),'impact':'high'})
            else: services.update_progress(owner,task,[25,50,75,10][mode-3],'تحديث تجريبي','تنفيذ جزء من المطلوب','استكمال المخرج')
            services.add_comment(owner,task,'تحديث تجريبي: تم بدء التنسيق مع فريق العمل.')
            if i%4==0: Task.objects.filter(pk=task.pk).update(updated_at=timezone.now()-timedelta(days=6))
        services.run_rules(); RuleSettings.current()
        viewer=User.objects.create_user(username='demo-viewer',email='demo-viewer@example.invalid',password=password,employee_number='DEMO-VIEW',first_name='مراقب',last_name='تجريبي',role=Role.VIEWER); viewer.units.add(units[4])
        User.objects.create_user(username='demo-board',email='demo-board@example.invalid',password=password,employee_number='DEMO-BOARD',first_name='أمين',last_name='المجلس',role=Role.BOARD)
        self.stdout.write(self.style.SUCCESS(f'Created {len(units)} units, {len(users)+2} users, 30 tasks and 3 decisions. Accounts: demo-admin, demo-01 (executive), demo-03 (head), demo-09 (employee), demo-viewer, demo-board.'))
