import hashlib,base64,difflib
from datetime import timedelta
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.core.files.base import ContentFile
from .models import *
from . import services
from .documents import content_hash,pdf_bytes,validate_pades


def can_export(user,mail,operation='download'):
    services.require_visible(user,mail)
    if mail.secrecy not in ['secret','restricted']:return True
    return ExportGrant.objects.filter(mail=mail,user=user,expires__gt=timezone.now(),**{operation:True}).exists()

@transaction.atomic
def grant_export(actor,mail,user,expires,reason,download=False,printing=False):
    services.require_visible(actor,mail);services.reason_required(reason)
    if actor.role!='executive':raise PermissionDenied('إذن الإخراج للمدير التنفيذي.')
    services.require_visible(user,mail)
    if expires<=timezone.now() or expires>timezone.now()+timedelta(days=7):raise ValidationError('الإذن محدود إلى سبعة أيام.')
    row=ExportGrant.objects.create(mail=mail,user=user,granted_by=actor,expires=expires,reason=reason,download=download,printing=printing)
    services.event(mail,actor,'إذن إخراج محمي',reason,new={'user':user.pk,'expires':expires.isoformat(),'download':download,'printing':printing})
    return row

@transaction.atomic
def reopen(actor,mail,reason):
    services.require_manage(actor,mail);services.reason_required(reason)
    if actor.role!='executive' or mail.kind=='IN' or mail.status not in ['issued','sent','received']:raise PermissionDenied('إعادة إصدار الصادر للمدير التنفيذي فقط.')
    old=services.snapshot(mail)
    mail.signature_requests.filter(state='completed').update(state='invalidated')
    mail.signed_by=None;mail.signed_at=None;mail.signature_method='';mail.signature_evidence='';mail.approval_index=0;mail.status='returned'
    # The previously issued reference is retained and never reassigned; new issue remains on the same record.
    mail.save();services.event(mail,actor,'إبطال توقيع وإعادة اعتماد',reason,old,services.snapshot(mail))
    return mail

def revision_diff(mail):
    edits=list(mail.events.exclude(old={}).order_by('pk'))
    return [{'event':e.pk,'actor':str(e.actor),'at':e.created_at.isoformat(),'reason':e.reason,'diff':'\n'.join(difflib.unified_diff(str(e.old.get('body','')).splitlines(),str(e.new.get('body','')).splitlines(),fromfile='قبل',tofile='بعد',lineterm='')),'old':e.old,'new':e.new} for e in edits]

@transaction.atomic
def request_signature(actor,mail,reason):
    mail=Correspondence.objects.select_for_update().get(pk=mail.pk);services.require_visible(actor,mail);services.reason_required(reason)
    if mail.status!='signing' or actor.pk!=mail.signer_id:raise PermissionDenied('التوقيع للموقّع المحدد بعد اكتمال الاعتماد.')
    policy=MailSettings.current().signature_policy.get(mail.kind,['pades'])
    methods=policy.get('methods',[]) if isinstance(policy,dict) else policy
    if 'pades' not in methods:raise ValidationError('مسار PAdES غير معتمد لهذا النوع.')
    config=Integration.objects.filter(kind='signature',active=True).first()
    if not config:raise ValidationError('مزود التوقيع غير مفعل.')
    current=mail.signature_requests.filter(state='pending',expires__gt=timezone.now(),content_hash=content_hash(mail)).first()
    if current:return current
    number=mail.reference or services.allocate_number(mail.kind)
    data=pdf_bytes(mail,reference=number,qr=True)
    row=SignatureRequest.objects.create(mail=mail,signer=actor,content_hash=content_hash(mail),pdf_hash=hashlib.sha256(data).hexdigest(),reserved_reference=number,method='pades',expires=timezone.now()+timedelta(hours=24))
    from .integrations import enqueue
    enqueue(config,'signature.request',{'request':str(row.uid),'signer_employee':actor.employee_number,'sha256':row.pdf_hash,'pdf_base64':base64.b64encode(data).decode()},f'sign:{row.uid}')
    services.event(mail,actor,'طلب توقيع PAdES',reason,new={'request':str(row.uid),'content_hash':row.content_hash,'pdf_hash':row.pdf_hash})
    return row

@transaction.atomic
def complete_signature(uid,data,provider):
    row=SignatureRequest.objects.select_for_update().select_related('mail','signer').get(uid=uid)
    mail=Correspondence.objects.select_for_update().get(pk=row.mail_id)
    if row.state=='completed':
        if row.evidence.get('sha256')!=hashlib.sha256(data).hexdigest():raise ValidationError('تكرار الطلب بمستند مختلف.')
        return row
    if row.state!='pending' or row.expires<timezone.now() or mail.status!='signing' or content_hash(mail)!=row.content_hash or not row.signer.is_active:raise ValidationError('طلب توقيع منتهي أو محتوى تغير.')
    # Binding the returned signed bytes to the unsigned source is checked through original revision bytes.
    from pyhanko.pdf_utils.reader import PdfFileReader
    import io
    reader=PdfFileReader(io.BytesIO(data))
    if not reader.embedded_signatures:raise ValidationError('لا يوجد توقيع PDF.')
    first=reader.embedded_signatures[0]
    ranges=list(map(int,first.sig_object['/ByteRange']))
    original_end=ranges[2]+ranges[3]
    # Provider may add signature fields incrementally; require the original whole document as prefix.
    pending=Delivery.objects.get(dedup='sign:'+str(row.uid)).payload
    original=base64.b64decode(pending['pdf_base64'])
    if not data.startswith(original):raise ValidationError('المزود يجب أن يوقع بتحديث تراكمي يحفظ المستند الأصلي.')
    evidence=validate_pades(data)
    policy=MailSettings.current().signature_policy.get(mail.kind,{})
    signers=policy.get('signers',[]) if isinstance(policy,dict) else []
    required_count=len(signers) or (policy.get('count',1) if isinstance(policy,dict) else 1)
    if not signers:raise ValidationError('يجب ربط الموقّعين ببصمات الشهادات المعتمدة في سياسة النوع.')
    if [x['fingerprint'].replace(' ','').upper() for x in signers]!=[x['fingerprint'].replace(' ','').upper() for x in evidence]:raise ValidationError('هوية أو ترتيب الموقّعين لا يطابق المصفوفة.')
    if len(evidence)<required_count:raise ValidationError('عدد التوقيعات أقل من سياسة النوع.')
    row.signed_pdf.save(str(row.uid)+'.pdf',ContentFile(data),save=False)
    row.state='completed';row.completed_at=timezone.now();row.evidence={'provider':provider,'signatures':evidence,'sha256':hashlib.sha256(data).hexdigest()};row.save()
    mail.reference=row.reserved_reference;mail.signed_at=row.completed_at;mail.signed_by=row.signer;mail.signature_method='pades';mail.signature_evidence=str(row.uid);mail.status='issued';mail.save()
    services.event(mail,row.signer,'توقيع PAdES موثق','تحقق الخادم من التوقيع والطابع الزمني والمستند',new=row.evidence)
    return row

@transaction.atomic
def propose_retention(actor,mail,action,reason):
    services.require_manage(actor,mail);services.reason_required(reason)
    if action not in ['destroy','permanent']:raise ValidationError('إجراء استبقاء غير صالح.')
    if mail.status!='archived' or not mail.retain_until or mail.retain_until>timezone.localdate() or LegalHold.objects.filter(mail=mail,active=True).exists():raise ValidationError('السجل غير مؤهل أو خاضع لحجز قانوني.')
    if mail.retentioncase_set.filter(state__in=['proposed','approved']).exists():raise ValidationError('يوجد طلب قائم.')
    row=RetentionCase.objects.create(mail=mail,created_by=actor,action=action,reason=reason)
    services.event(mail,actor,'ترشيح الاستبقاء',reason,new={'case':str(row.uid),'action':action})
    return row

@transaction.atomic
def approve_retention(actor,case,reason):
    services.require_visible(actor,case.mail);services.reason_required(reason)
    committee=MailSettings.current().retention_committee
    if actor.pk==case.created_by_id or not committee.filter(pk=actor.pk,is_active=True).exists():raise PermissionDenied('الاعتماد لعضو لجنة آخر مخول.')
    if case.state!='proposed':raise ValidationError('الطلب ليس بانتظار اللجنة.')
    RetentionApproval.objects.get_or_create(case=case,actor=actor,defaults={'reason':reason})
    quorum=max(2,MailSettings.current().retention_quorum)
    if case.approvals.filter(actor__is_active=True,actor__in=committee.all()).count()>=quorum:
        case.state='approved';case.save(update_fields=['state'])
        if case.action=='destroy':
            from .integrations import enqueue
            for config in Integration.objects.filter(kind='dms',active=True):
                enqueue(config,'retention.backup-erasure',{'case':str(case.uid),'reference':case.mail.code,'attachments':list(case.mail.attachments.values('id','sha256'))},f'erasure:{config.pk}:{case.uid}')
    services.event(case.mail,actor,'اعتماد لجنة الاستبقاء',reason,new={'case':str(case.uid),'state':case.state})
    return case

@transaction.atomic
def execute_retention(actor,case,minutes,reason):
    case=RetentionCase.objects.select_for_update().get(pk=case.pk);mail=case.mail
    services.require_manage(actor,mail);services.reason_required(reason)
    if case.state!='approved' or not minutes.strip() or LegalHold.objects.filter(mail=mail,active=True).exists():raise ValidationError('يلزم اعتماد اللجنة ومحضر وعدم وجود حجز.')
    committee=MailSettings.current().retention_committee
    if case.approvals.filter(actor__is_active=True,actor__in=committee.all()).count()<max(2,MailSettings.current().retention_quorum):raise ValidationError('تغيرت عضوية اللجنة؛ أعد الاعتماد.')
    # Never erase the official register/audit; physical destruction requires a separate verified backup lifecycle.
    if case.action=='destroy':
        if not case.proof.get('backup_erasure_verified'):raise ValidationError('يلزم إثبات من موصل الأرشيف بإتلاف نسخ المرفقات الاحتياطية قبل التنفيذ.')
        erased=[]
        for attachment in mail.attachments.all():
            if attachment.file.name:
                name=attachment.file.name;attachment.file.storage.delete(name)
                attachment.file.name='';attachment.save(update_fields=['file']);erased.append({'id':attachment.pk,'sha256':attachment.sha256,'name':attachment.name})
                OCRJob.objects.filter(attachment=attachment).update(text='',proposals={},state='done')
        for signature in mail.signature_requests.exclude(signed_pdf=''):
            signature.signed_pdf.storage.delete(signature.signed_pdf.name);signature.signed_pdf.name='';signature.save(update_fields=['signed_pdf'])
        case.state='completed';case.completed_at=timezone.now();case.minutes_reference=minutes;case.proof={**case.proof,'attachments_erased':erased,'audit_retained':True};case.save()
        services.rebuild_search(mail);services.event(mail,actor,'إتلاف مرفقات موثق',reason,new=case.proof)
        return case
    mail.retain_until=None;mail.save(update_fields=['retain_until'])
    case.state='completed';case.completed_at=timezone.now();case.minutes_reference=minutes;case.proof={'reference':mail.code,'audit_retained':True,'action':'permanent'};case.save()
    services.event(mail,actor,'حفظ دائم موثق',reason,new=case.proof)
    return case
