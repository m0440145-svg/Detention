import json
from django.core.management.base import BaseCommand,CommandError
from correspondence.integrations import sync_hr
class Command(BaseCommand):
    help='Apply an explicit HR source snapshot/event; omitted users are not deleted.'
    def add_arguments(self,p):p.add_argument('file');p.add_argument('--source',required=True);p.add_argument('--event',required=True)
    def handle(self,*args,**o):
        try:
            with open(o['file']) as f:payload=json.load(f)
            self.stdout.write(json.dumps(sync_hr(o['source'],o['event'],payload)))
        except Exception as e:raise CommandError(str(e))
