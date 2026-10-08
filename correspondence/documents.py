"""Local PDF/A generation: network fetches are prohibited, brand fonts embedded."""
import hashlib,io,json,re
from pathlib import Path
from django.conf import settings
from django.utils.html import escape
from django.core.exceptions import ValidationError
from . import services

ALLOWED_TAGS=['p','br','strong','em','u','h2','h3','ul','ol','li','table','thead','tbody','tr','td','th','blockquote']
def clean_richtext(value):
    import bleach
    return bleach.clean(value,tags=ALLOWED_TAGS,attributes={'td':['colspan','rowspan'],'th':['colspan','rowspan'],'*':['dir']},strip=True)

def content_hash(mail):
    snapshot=services.snapshot(mail)
    for key in ['status','reference','approval_index','signed_at','signed_by_id','signature_method']:snapshot.pop(key,None)
    snapshot['attachments']=list(mail.attachments.order_by('pk').values_list('sha256',flat=True))
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def html_document(mail,reference=None,watermark='',qr=False):
    from .models import MailSettings
    brand=MailSettings.current();body=services.rendered_body(mail)
    text=clean_richtext(body) if mail.rich_text else '<p>'+escape(body).replace('\n','<br>')+'</p>'
    fonts=Path(settings.BASE_DIR)/'core/static/core/fonts'
    font=fonts/'Tajawal-Regular.ttf'
    font_css=f"@font-face{{font-family:Brand;src:url('{font.as_uri()}')}}" if font else ''
    qr_html=''
    if qr:
        import base64,qrcode
        buffer=io.BytesIO();qrcode.make(verification_url(mail)).save(buffer,format='PNG')
        qr_html='<img width="110" alt="QR" src="data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()+'">'
    return f'''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><style>{font_css}@page{{size:A4;margin:22mm}}body{{font-family:Brand,DejaVu Sans;font-size:12pt;line-height:1.8}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #777;padding:6px}}.watermark{{color:#999;text-align:center}}footer{{border-top:1px solid #ccc;margin-top:30px}}</style><header><h1>{escape(brand.brand_name)}</h1><p>ترخيص {escape(brand.license_number)} · {escape(brand.national_address)}</p></header><p>{escape(reference or mail.code)} · {escape(mail.hijri_date)} هـ · {mail.created_at:%Y-%m-%d} م</p><p>إلى: {escape(mail.party.name)}</p><h2>{escape(mail.subject)}</h2><p class="watermark">{escape(watermark)}</p><main>{text}</main><p>الموقّع: {escape(str(mail.signer or '—'))}</p>{qr_html}<footer>{escape(brand.footer)}</footer></html>'''

from weasyprint.urls import URLFetcher
class LocalFetcher(URLFetcher):
    def fetch(self,url,headers=None):
        from urllib.parse import urlsplit,unquote
        if url.startswith('data:'):return super().fetch(url,headers)
        fonts=(Path(settings.BASE_DIR)/'core/static/core/fonts').resolve()
        parsed=urlsplit(url)
        if parsed.scheme=='file' and Path(unquote(parsed.path)).resolve().is_relative_to(fonts):return super().fetch(url,headers)
        raise ValidationError('تحميل موارد خارجية في المستند ممنوع.')
local_fetcher=LocalFetcher(allowed_protocols=['data','file'],allow_redirects=False,fail_on_errors=True)

def pdf_bytes(mail,reference=None,watermark='',qr=False):
    from weasyprint import HTML
    return HTML(string=html_document(mail,reference,watermark,qr),url_fetcher=local_fetcher).write_pdf(pdf_variant='pdf/a-3b')

def verification_url(mail):
    origin=settings.PUBLIC_ORIGIN.rstrip('/')
    return origin+'/verify/'+str(mail.uid)+'/'


def validate_pades(data):
    """Require an explicitly trusted Saudi provider chain and cryptographic timestamp."""
    import os
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    from pyhanko.keys import load_cert_from_pemder
    roots=os.getenv('SIGNATURE_TRUST_ROOTS','')
    if not roots:raise ValidationError('جذور الثقة للمزود المرخص غير مهيأة.')
    certs=[load_cert_from_pemder(path) for path in roots.split(',')]
    reader=PdfFileReader(io.BytesIO(data));signatures=reader.embedded_signatures
    if not signatures:raise ValidationError('PDF بلا توقيع PAdES مضمّن.')
    from pyhanko.sign.validation.dss import DocumentSecurityStore
    from pyhanko.pdf_utils.misc import PdfReadError
    kwargs={'trust_roots':certs,'allow_fetching':False,'revocation_mode':'hard-fail'}
    try:context=DocumentSecurityStore.read_dss(reader).as_validation_context(kwargs)
    except PdfReadError:context=ValidationContext(**kwargs)
    result=[]
    for sig in signatures:
        status=validate_pdf_signature(sig,signer_validation_context=context,ts_validation_context=context)
        timestamp=status.timestamp_validity
        if not status.bottom_line or timestamp is None or not timestamp.bottom_line:raise ValidationError('التوقيع أو الطابع الزمني أو حالة الإلغاء لم تتحقق.')
        result.append({'fingerprint':sig.signer_cert.sha256_fingerprint,'intact':status.intact,'valid':status.valid,'trusted':status.trusted})
    # Signing must preserve PDF/A; independent veraPDF validation is mandatory for provider return.
    import shutil,subprocess,tempfile
    validator=shutil.which('verapdf')
    if not validator:raise ValidationError('veraPDF مطلوب للتحقق من نسخة المزود النهائية.')
    with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
        f.write(data);f.flush();report=subprocess.run([validator,'--format','xml',f.name],capture_output=True,text=True,timeout=60,check=True).stdout
    if 'isCompliant="true"' not in report:raise ValidationError('المستند الموقع لا يجتاز PDF/A.')
    return result
