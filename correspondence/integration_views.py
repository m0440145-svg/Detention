import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError,PermissionDenied
from .models import Integration
from .integrations import verify_callback,sync_hr,MAX_PAYLOAD

@csrf_exempt
@require_POST
def hr_callback(request,key):
    config=get_object_or_404(Integration,key=key,kind='hr',active=True)
    try:
        if len(request.body)>MAX_PAYLOAD:raise ValidationError('حزمة كبيرة.')
        verify_callback(config,request.body,request.headers.get('X-Ehsan-Timestamp'),request.headers.get('X-Ehsan-Signature'))
        data=json.loads(request.body)
        result=sync_hr(key,data['event_id'],data['data'])
        return JsonResponse(result)
    except PermissionDenied:return JsonResponse({'detail':'Callback denied'},status=403)
    except (ValidationError,ValueError,TypeError,KeyError):return JsonResponse({'detail':'Invalid HR event'},status=400)

@csrf_exempt
@require_POST
def signature_callback(request,key):
    import base64
    from .governance import complete_signature
    config=get_object_or_404(Integration,key=key,kind='signature',active=True)
    try:
        if len(request.body)>MAX_PAYLOAD:raise ValidationError('حزمة كبيرة.')
        verify_callback(config,request.body,request.headers.get('X-Ehsan-Timestamp'),request.headers.get('X-Ehsan-Signature'))
        data=json.loads(request.body);row=complete_signature(data['request'],base64.b64decode(data['pdf_base64'],validate=True),key)
        return JsonResponse({'state':row.state})
    except PermissionDenied:return JsonResponse({'detail':'Callback denied'},status=403)
    except (ValidationError,ValueError,TypeError,KeyError):return JsonResponse({'detail':'Invalid signature result'},status=400)

@csrf_exempt
@require_POST
def dms_callback(request,key):
    from django.db import transaction
    from .models import Delivery,RetentionCase
    config=get_object_or_404(Integration,key=key,kind='dms',active=True)
    try:
        if len(request.body)>MAX_PAYLOAD:raise ValidationError('Payload too large')
        verify_callback(config,request.body,request.headers.get('X-Ehsan-Timestamp'),request.headers.get('X-Ehsan-Signature'))
        data=json.loads(request.body)
        with transaction.atomic():
            case=RetentionCase.objects.select_for_update().get(uid=data['case'],action='destroy',state='approved')
            delivery=Delivery.objects.get(integration=config,dedup=f'erasure:{config.pk}:{case.uid}')
            if data.get('delivery')!=str(delivery.uid) or data.get('backup_erased') is not True or not data.get('proof_reference'):raise ValidationError('Invalid erasure evidence')
            case.proof={'backup_erasure_verified':True,'provider':key,'reference':str(data['proof_reference'])[:200]};case.save(update_fields=['proof'])
            from .services import event
            event(case.mail,None,'إثبات إتلاف النسخ الاحتياطية','نتيجة موصل الأرشيف الموثقة',new=case.proof)
        return JsonResponse({'verified':True})
    except PermissionDenied:return JsonResponse({'detail':'Callback denied'},status=403)
    except (ValidationError,ValueError,TypeError,KeyError,RetentionCase.DoesNotExist,Delivery.DoesNotExist):return JsonResponse({'detail':'Invalid DMS event'},status=400)

@csrf_exempt
@require_POST
def email_callback(request,key):
    from django.db import transaction
    from .models import SyncReceipt,Party,Correspondence,Classification
    from core.models import User,Unit
    from . import services
    from django.utils import timezone
    from datetime import date
    import hashlib
    config=get_object_or_404(Integration,key=key,kind='email',active=True)
    try:
        if len(request.body)>MAX_PAYLOAD:raise ValidationError('Payload too large')
        verify_callback(config,request.body,request.headers.get('X-Ehsan-Timestamp'),request.headers.get('X-Ehsan-Signature'))
        data=json.loads(request.body);digest=hashlib.sha256(request.body).hexdigest()
        with transaction.atomic():
            prior=SyncReceipt.objects.filter(source='mail:'+key,event_id=data['message_id']).first()
            if prior:
                if prior.digest!=digest:raise ValidationError('Message replay mismatch')
                return JsonResponse(prior.summary)
            actor=User.objects.get(employee_number=config.options['registrar'],is_active=True)
            mail=services.create_mail(actor,{'kind':'IN','subject':data['subject'],'body':data.get('body',''),'party':Party.objects.get(email__iexact=data['sender'],active=True),'classification':Classification.objects.get(pk=config.options['classification']),'unit':Unit.objects.get(pk=config.options['unit']),'owner':User.objects.get(employee_number=config.options['owner'],is_active=True),'external_number':data['message_id'],'original_date':date.fromisoformat(data['date']),'channel':'email','no_attachments':not bool(data.get('attachments'))})
            import base64
            from django.core.files.uploadedfile import SimpleUploadedFile
            for f in data.get('attachments',[]):services.attach(actor,mail,SimpleUploadedFile(f['name'],base64.b64decode(f['base64'],validate=True)),'استقبال من البريد المؤسسي')
            result={'registered':mail.reference};SyncReceipt.objects.create(source='mail:'+key,event_id=data['message_id'],digest=digest,summary=result)
        return JsonResponse(result,status=201)
    except PermissionDenied:return JsonResponse({'detail':'Callback denied'},status=403)
    except (ValidationError,ValueError,TypeError,KeyError,User.DoesNotExist,Unit.DoesNotExist,Party.DoesNotExist,Classification.DoesNotExist):return JsonResponse({'detail':'Invalid or unmapped incoming email'},status=400)
