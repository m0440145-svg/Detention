import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections,OperationalError,ProgrammingError
from core.services import run_rules
class Command(BaseCommand):
    help='Run rules every 60 seconds. Run a single instance.'
    def handle(self,*args,**options):
        while True:
            close_old_connections()
            try: run_rules()
            except (OperationalError,ProgrammingError) as e: self.stderr.write(f'Database not ready: {type(e).__name__}')
            time.sleep(60)
