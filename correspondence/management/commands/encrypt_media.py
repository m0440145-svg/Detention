from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from correspondence.security import MAGIC,seal
class Command(BaseCommand):
    help='Encrypt existing private media in place atomically; take an encrypted backup first.'
    def add_arguments(self,p):p.add_argument('--commit',action='store_true')
    def handle(self,*args,**o):
        count=0
        for path in Path(settings.MEDIA_ROOT).rglob('*'):
            if not path.is_file() or path.is_symlink():continue
            data=path.read_bytes()
            if data.startswith(MAGIC):continue
            count+=1
            if o['commit']:
                temporary=path.with_name(path.name+'.encrypted-tmp');temporary.write_bytes(seal(data));temporary.chmod(0o600);temporary.replace(path)
        self.stdout.write(f'{count} legacy files; mode={"commit" if o["commit"] else "dry-run"}')
