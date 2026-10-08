from django.core.management.base import BaseCommand,CommandError
from correspondence.models import Correspondence
from correspondence.services import verify_chain
class Command(BaseCommand):
    def handle(self,*args,**o):
        invalid=[m.code for m in Correspondence.objects.all() if not verify_chain(m)]
        if invalid:raise CommandError('Invalid audit chains: '+', '.join(invalid))
        self.stdout.write('All correspondence audit chains verified')
