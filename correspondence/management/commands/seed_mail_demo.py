from datetime import timedelta
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.utils import timezone
from core.models import User,Unit,Role,Decision
from correspondence.models import *
from correspondence import services

class Command(BaseCommand):
    help='Add fictional correspondence to the existing demo users and units only.'
    def handle(self,*args,**options):
        if Correspondence.objects.filter(subject__endswith=' — تجريبي').exists(): return
        executive=User.objects.filter(username='demo-01').first()
        if not executive: return
        welfare=Unit.objects.get(name='وحدة الخدمات الرعوية'); tech=Unit.objects.get(name='وحدة التقنية والتحول الرقمي')
        owner=User.objects.filter(role=Role.EMPLOYEE,units=welfare,is_active=True).first()
        tech_owner=User.objects.filter(role=Role.EMPLOYEE,units=tech,is_active=True).first() or owner
        tech_owner.units.add(tech)
        retention=RetentionPolicy.objects.create(name='استبقاء تجريبي — يحتاج اعتماد الجمعية',years=5,legal_reference='',approved=False)
        classification=Classification.objects.create(name='الخدمات الاجتماعية — تجريبي',route_unit=welfare,retention=retention)
        tech_class=Classification.objects.create(name='التحول الرقمي — تجريبي',route_unit=tech,retention=retention)
        donor=Party.objects.create(name='جهة داعمة افتراضية — تجريبي',email='donor@example.invalid')
        regulator=Party.objects.create(name='جهة إشرافية افتراضية — تجريبي',supervisory=True)
        internal=Party.objects.create(name='إدارات الجمعية — جهة داخلية تجريبية')
        for kind in [Kind.OUT,Kind.RPL,Kind.INT,Kind.CIR,Kind.DEC]:
            ApprovalRoute.objects.create(name=Kind(kind).label+' — مسار تجريبي',kind=kind,levels=['head','executive'],authority_reference='DEMO: مرجع تجريبي لا يمثل مصفوفة الجمعية المعتمدة')
        for kind,name in [(Kind.OUT,'خطاب رسمي لجهة حكومية'),(Kind.OUT,'خطاب لمانح / داعم'),(Kind.RPL,'رد على وارد'),(Kind.INT,'مذكرة داخلية'),(Kind.CIR,'تعميم'),(Kind.DEC,'قرار إداري'),(Kind.OUT,'خطاب تعريف / إفادة'),(Kind.OUT,'طلب بيانات')]:
            MailTemplate.objects.create(name=name+' — تجريبي',kind=kind,body='إلى {{الجهة_المرسل_إليها}}\nالموضوع: {{الموضوع}}\nنفيدكم بالتالي وفق التكليف المسجل أدناه.\n{{اسم_الموقّع}}\n{{مسمى_الموقّع}}')
        MailSettings.current()
        MailAccess.objects.get_or_create(user=owner,defaults={'register':True})
        base={'unit':welfare,'owner':owner,'classification':classification,'channel':'manual','priority':'normal','no_attachments':True,'body':'سجل تجريبي لا يمثل مراسلة أو تكليفًا فعليًا للجمعية.'}
        incoming=[]
        for i,title in enumerate(['طلب تقرير أثر السلال الغذائية','متابعة تنفيذ متطلبات إشرافية','طلب تنسيق برنامج مجتمعي','طلب معلومات من جهة خارجية']):
            mail=services.create_mail(executive,{**base,'kind':Kind.IN,'subject':title+' — تجريبي','party':regulator if i==1 else donor,'external_number':f'EXT-DEMO-{i+1:03d}','original_date':timezone.localdate()-timedelta(days=2)},upload=ContentFile('مرفق نصي تجريبي للمعاينة فقط.'.encode(),name=f'letter-demo-{i+1}.txt'))
            incoming.append(mail)
        services.transition(executive,incoming[0],'start','بدء معالجة وارد تجريبي')
        row=incoming[0].referrals.first()
        if row: services.convert_to_task(executive,row,{'due_date':timezone.localdate()+timedelta(days=7)})
        services.transition(executive,incoming[3],'start','التنسيق مع الجهة')
        services.transition(executive,incoming[3],'pause','انتظار البيانات المطلوبة من الجهة الخارجية')
        Correspondence.objects.filter(pk=incoming[2].pk).update(created_at=timezone.now()-timedelta(days=15),due_at=timezone.now()-timedelta(days=2))
        for kind,title in [(Kind.OUT,'تقرير موجه للجهة الداعمة'),(Kind.RPL,'رد على طلب تقرير الأثر'),(Kind.INT,'مذكرة متابعة الخدمات'),(Kind.DEC,'قرار تنظيمي مرتبط بقرار الاجتماع')]:
            route=ApprovalRoute.objects.filter(kind=kind).first()
            mail=services.create_mail(owner,{**base,'kind':kind,'subject':title+' — تجريبي','party':internal if kind==Kind.INT else donor,'related':incoming[0] if kind==Kind.RPL else None,'signer':executive,'route':route,'template':MailTemplate.objects.filter(kind=kind).first(),'decision':Decision.objects.first() if kind==Kind.DEC else None})
            if kind in [Kind.OUT,Kind.INT]:
                services.transition(owner,mail,'submit','تقديم مسودة تجريبية')
                services.transition(welfare.head,mail,'review','اكتمل تدقيق المسودة التجريبية')
                services.transition(welfare.head,mail,'approve','اعتماد رئيس الوحدة — تجريبي')
                if kind==Kind.INT:
                    services.transition(executive,mail,'approve','اعتماد المدير التنفيذي — تجريبي')
                    services.transition(executive,mail,'sign','إثبات توقيع داخلي — تجريبي',evidence='DEMO: إثبات داخلي للمعاينة، وليس شهادة رقمية')
        services.run_mail_rules()
        self.stdout.write('Created eight fictional correspondences, templates, routes and a linked task; existing users/units reused.')
