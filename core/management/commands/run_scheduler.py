import time
from django.core.management.base import BaseCommand,CommandError
from django.db import close_old_connections,OperationalError,ProgrammingError
from django.utils import timezone
from core.services import run_rules

class Command(BaseCommand):
    help='Evaluate task rules continuously, or once for an external scheduler.'
    def add_arguments(self,parser):
        parser.add_argument('--once',action='store_true')
        parser.add_argument('--interval',type=int,default=60)
    def handle(self,*args,**options):
        interval=options['interval']
        if interval<1: raise CommandError('Interval must be at least one second.')
        while True:
            close_old_connections()
            try:
                count=run_rules()
                from correspondence.ocr import run_jobs
                extracted=run_jobs()
                from correspondence.integrations import schedule_notifications,deliver_pending,poll_hr
                poll_hr();schedule_notifications();deliver_pending()
                from correspondence.reports import run_schedules
                run_schedules()
                if extracted: self.stdout.write(f'Extraction jobs completed: {extracted}')
                self.stdout.write(f'{timezone.localtime().isoformat()} rules evaluated; new escalations: {count}')
                self.stdout.flush()
            except (OperationalError,ProgrammingError) as exc:
                if options['once']: raise CommandError(f'Database not ready: {type(exc).__name__}') from exc
                self.stderr.write(f'Database not ready: {type(exc).__name__}')
            finally: close_old_connections()
            if options['once']: return
            time.sleep(interval)
