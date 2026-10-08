"""Deterministic dry-run and atomic import; existing reference numbers preserved."""
import json,hashlib,re
from datetime import datetime
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.utils import timezone
from correspondence.models import *
from correspondence import services
from core.models import User,Unit

class Command(BaseCommand):
    help='Validate or import a mapped Snso JSON export. Dry run is default.'
    def add_arguments(self,p):
        p.add_argument('file');p.add_argument('--actor',required=True);p.add_argument('--source',default='Snso');p.add_argument('--commit',action='store_true')
    def handle(self,*args,**o):
        try:
            actor=User.objects.get(employee_number=o['actor'],is_active=True,role='executive')
            raw=open(o['file'],'rb').read();digest=hashlib.sha256(raw).hexdigest();payload=json.loads(raw)
            if not isinstance(payload,list) or len(payload)>10000:raise ValueError('Expected array of at most 10000 records')
            with transaction.atomic():
                previous=MigrationBatch.objects.filter(digest=digest,committed=True).first()
                if previous:self.stdout.write(json.dumps(previous.summary));return
                references=set();created=0
                for row in payload:
                    reference=row['reference'];match=re.fullmatch(r'(IN|OUT|INT|CIR|DEC|RPL)-(\d{4})-(\d+)',reference)
                    if reference in references:raise ValueError('Duplicate source reference '+reference)
                    references.add(reference)
                    if Correspondence.objects.filter(reference=reference).exists():raise ValueError('Reference exists '+reference)
                    kind=row['kind'];status=row.get('status','registered' if kind=='IN' else 'draft')
                    if status not in ['registered','archived','done','cancelled','draft','issued','sent','received','referred','active','waiting','review','approval','signing','returned']:raise ValueError('Historical signed output requires separately verified signature import')
                    mail=Correspondence(kind=kind,reference=reference,legacy_reference=str(row['legacy_id']),subject=row['subject'],body=row.get('body',''),external_number=row.get('external_number',''),original_date=row.get('original_date'),party=Party.objects.get(pk=row['party']),classification=Classification.objects.get(pk=row['classification']),unit=Unit.objects.get(pk=row['unit']),owner=User.objects.get(employee_number=row['owner']),created_by=actor,status=status,secrecy=row.get('secrecy','internal'),priority=row.get('priority','normal'),channel='api',source_platform=o['source'],no_attachments=True)
                    access=row.get('authorized',[])
                    if mail.secrecy in ['secret','restricted'] and not access:raise ValueError('Protected history requires named access mapping')
                    if status in ['issued','sent','received']:
                        mail.signed_at=datetime.fromisoformat(row['signed_at']);mail.signature_method='legacy_unverified';mail.signature_evidence='Historical signature requires independent validation'
                        if timezone.is_naive(mail.signed_at):raise ValueError('signed_at must include time zone')
                    services.validate_people(mail,actor);mail.full_clean();mail.save()
                    named=list(User.objects.filter(employee_number__in=access,is_active=True).exclude(role='admin'))
                    if len(named)!=len(set(access)):raise ValueError('Unknown protected access employee')
                    mail.authorized.set(named)
                    if status=='archived':
                        mail.archived_at=datetime.fromisoformat(row['archived_at'])
                        mail.retain_until=row.get('retain_until');mail.save()
                    if row.get('created_at'):
                        at=datetime.fromisoformat(row['created_at'])
                        if timezone.is_naive(at):raise ValueError('Historical date must include time zone')
                        Correspondence.objects.filter(pk=mail.pk).update(created_at=at);mail.refresh_from_db()
                    if match:
                        sequence,_=Sequence.objects.get_or_create(kind=match[1],year=int(match[2]))
                        sequence.last=max(sequence.last,int(match[3]));sequence.save()
                    services.rebuild_search(mail);services.event(mail,actor,'ترحيل تاريخي','حفظ رقم القيد الأصلي',new={'source':o['source'],'legacy_id':mail.legacy_reference,'reference':reference,'batch_hash':digest});created+=1
                    from pathlib import Path
                    from django.core.files.uploadedfile import SimpleUploadedFile
                    from core.services import validate_upload
                    for attachment in row.get('attachments',[]):
                        root=Path(o['file']).resolve().parent
                        path=(root/attachment['path']).resolve()
                        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():raise ValueError('Attachment path outside export')
                        data=path.read_bytes()
                        if hashlib.sha256(data).hexdigest()!=attachment['sha256']:raise ValueError('Attachment checksum mismatch')
                        upload=SimpleUploadedFile(path.name,data);validate_upload(upload)
                        if o['commit']:MailAttachment.objects.create(mail=mail,file=upload,name=path.name,sha256=attachment['sha256'],size=len(data),uploaded_by=actor)
                summary={'source':o['source'],'records':created,'unique_references':len(references),'sha256':digest,'mode':'committed' if o['commit'] else 'dry-run'}
                MigrationBatch.objects.create(source=o['source'],digest=digest,actor=actor,committed=o['commit'],summary=summary)
                if not o['commit']:transaction.set_rollback(True)
                self.stdout.write(json.dumps(summary,ensure_ascii=False))
        except Exception as e:raise CommandError(str(e))
