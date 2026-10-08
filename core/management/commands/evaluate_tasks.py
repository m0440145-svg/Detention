from django.core.management.base import BaseCommand
from core.services import run_rules
class Command(BaseCommand):
    help='Evaluate due dates, risk flags and idempotent escalation notifications.'
    def handle(self,*args,**options): self.stdout.write(f'Processed changes: {run_rules()}')
