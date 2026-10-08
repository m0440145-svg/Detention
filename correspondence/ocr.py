"""Local extraction. Metadata remains a proposal and is never silently overwritten."""
import csv,io,os,re,shutil,subprocess,tempfile
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

def extract(file,details=False):
    ext=Path(file.name).suffix.lower()
    if ext in ['.txt','.csv']:
        with file.open('rb') as stream:
            text=stream.read().decode('utf-8-sig')
            return (text,None,[]) if details else (text,None)
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
            info=subprocess.run(['pdfinfo',str(source)],capture_output=True,text=True,timeout=20,check=True).stdout
            count=int(re.search(r'Pages:\s+(\d+)',info).group(1))
            if count>int(os.getenv('OCR_MAX_PAGES','100')):raise ValidationError('عدد صفحات الملف يتجاوز حد OCR؛ ارفع الحد أو جزئ الملف للمراجعة.')
            subprocess.run(['pdftoppm','-f','1','-l',str(count),'-scale-to','2200','-png',str(source),str(Path(temp)/'page')],capture_output=True,timeout=60,check=True)
            images=sorted(Path(temp).glob('page-*.png'))
        texts=[];confidences=[];pages=[]
        for number,page in enumerate(images,1):
            result=subprocess.run(['tesseract',str(page),'stdout','-l','ara+eng','tsv'],capture_output=True,text=True,timeout=60,check=True)
            words=[];page_scores=[]
            for row in csv.DictReader(io.StringIO(result.stdout),delimiter='\t'):
                if row.get('text','').strip():
                    words.append(row['text']);confidence=float(row['conf'])
                    if confidence>=0:confidences.append(confidence);page_scores.append(confidence)
            texts.append(' '.join(words))
            score=round(sum(page_scores)/len(page_scores),1) if page_scores else 0
            pages.append({'page':number,'confidence':score,'needs_review':score<70})
        if not any(texts): raise ValidationError('لم يعثر المحرك على نص قابل للاستخراج.')
        text='\n\n'.join(texts)

        score=round(sum(confidences)/len(confidences),1) if confidences else None
        return (text[:200000],score,pages) if details else (text[:200000],score)

@transaction.atomic
def run_jobs(limit=2):
    count=0
    for job in OCRJob.objects.select_for_update().filter(state='pending').select_related('attachment','requested_by')[:limit]:
        try:
            job.text,job.confidence,job.page_confidence=extract(job.attachment.file,details=True);job.proposals=propose_metadata(job.text);job.state='done';job.error=''
        except (ValidationError,subprocess.SubprocessError,OSError,UnicodeError) as exc:
            job.state='failed';job.error=str(exc)[:1000]
        job.completed_at=timezone.now();job.save(update_fields=['text','confidence','page_confidence','proposals','state','error','completed_at'])
        mail=Correspondence.objects.select_for_update().get(pk=job.attachment.mail_id)
        services.event(mail,None,'نتيجة استخراج النص','نتيجة آلية تحتاج مراجعة؛ لم تتغير البيانات التعريفية',new={'job':job.pk,'state':job.state,'confidence':job.confidence,'error':job.error})
        if job.state=='done': services.rebuild_search(mail)
        count+=1
    return count


def propose_metadata(text):
    proposals={}
    patterns={'external_number':r'(?:رقم|الرقم)\s*[:：]\s*([^\n]{1,80})','original_date':r'(?:التاريخ|تاريخ)\s*[:：]\s*([^\n]{1,30})','party':r'(?:الجهة|من)\s*[:：]\s*([^\n]{1,120})','subject':r'(?:الموضوع)\s*[:：]\s*([^\n]{1,250})'}
    for key,pattern in patterns.items():
        match=re.search(pattern,text)
        if match:proposals[key]=match.group(1).strip()
    proposals['summary']=text[:500]
    return proposals
