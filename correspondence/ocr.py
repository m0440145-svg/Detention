"""Local extraction. Metadata remains a proposal and is never silently overwritten."""
import csv,io,os,shutil,subprocess,tempfile
from pathlib import Path
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import OCRJob,Correspondence,MailAttachment
from . import services

@transaction.atomic
def request_extraction(user,attachment,ip=''):
    services.require_edit(user,attachment.mail)
    row,created=OCRJob.objects.get_or_create(attachment=attachment,defaults={'requested_by':user})
    if not created and row.state=='failed':
        row.state='pending';row.error='';row.save(update_fields=['state','error'])
    services.event(attachment.mail,user,'طلب استخراج نص','استخراج محلي؛ النتيجة تتطلب مراجعة',new={'attachment':attachment.pk,'job':row.pk},ip=ip)
    return row

def extract(file):
    ext=Path(file.name).suffix.lower()
    if ext in ['.txt','.csv']:
        with file.open('rb') as stream: return stream.read().decode('utf-8-sig'),None
    if ext not in ['.png','.jpg','.jpeg','.pdf']: raise ValidationError('الاستخراج المحلي يدعم الصور وPDF وTXT/CSV فقط.')
    if not shutil.which('tesseract'): raise ValidationError('محرك Tesseract غير مثبت على الخادم.')
    languages=subprocess.run(['tesseract','--list-langs'],capture_output=True,text=True,timeout=10,check=True).stdout.splitlines()
    if 'ara' not in languages: raise ValidationError('حزمة اللغة العربية لمحرك OCR غير مثبتة؛ لا يُستخدم بديل إنجليزي صامت.')
    with tempfile.TemporaryDirectory(prefix='mail-ocr-') as temp:
        source=Path(temp)/('source'+ext)
        with file.open('rb') as stream: source.write_bytes(stream.read())
        images=[source]
        if ext=='.pdf':
            if not shutil.which('pdftoppm'): raise ValidationError('محرك تحويل صفحات PDF غير مثبت.')
            subprocess.run(['pdftoppm','-f','1','-l','5','-scale-to','2200','-png',str(source),str(Path(temp)/'page')],capture_output=True,timeout=60,check=True)
            images=sorted(Path(temp).glob('page-*.png'))
        texts=[];confidences=[]
        for page in images:
            result=subprocess.run(['tesseract',str(page),'stdout','-l','ara+eng','tsv'],capture_output=True,text=True,timeout=60,check=True)
            words=[]
            for row in csv.DictReader(io.StringIO(result.stdout),delimiter='\t'):
                if row.get('text','').strip():
                    words.append(row['text']);confidence=float(row['conf'])
                    if confidence>=0:confidences.append(confidence)
            texts.append(' '.join(words))
        if not any(texts): raise ValidationError('لم يعثر المحرك على نص قابل للاستخراج.')
        text='\n\n'.join(texts)
        if ext=='.pdf':text+='\n[استخرجت أول خمس صفحات كحد أقصى؛ راجع الملف الأصلي.]'
        return text[:200000],round(sum(confidences)/len(confidences),1) if confidences else None

@transaction.atomic
def run_jobs(limit=2):
    count=0
    for job in OCRJob.objects.select_for_update().filter(state='pending').select_related('attachment','requested_by')[:limit]:
        try:
            job.text,job.confidence=extract(job.attachment.file);job.state='done';job.error=''
        except (ValidationError,subprocess.SubprocessError,OSError,UnicodeError) as exc:
            job.state='failed';job.error=str(exc)[:1000]
        job.completed_at=timezone.now();job.save(update_fields=['text','confidence','state','error','completed_at'])
        mail=Correspondence.objects.select_for_update().get(pk=job.attachment.mail_id)
        services.event(mail,None,'نتيجة استخراج النص','نتيجة آلية تحتاج مراجعة؛ لم تتغير البيانات التعريفية',new={'job':job.pk,'state':job.state,'confidence':job.confidence,'error':job.error})
        if job.state=='done': services.rebuild_search(mail)
        count+=1
    return count
