# عقود الربط — JSON v1

جميع اتصالات الإنتاج HTTPS. المفاتيح متغيرات بيئة لا قيم في Integration. allowlist عبر INTEGRATION_ALLOWED_HOSTS مع حجب private IP وredirects؛ يلزم أيضاً egress firewall لدى المستضيف لمنع DNS rebinding. لا تفعل Integration قبل اختبار مورد مرخص/معتمد على بيئة الجمعية.

## OAuth2 وSSO

`/o/authorize/`, `/o/token/`, `/o/revoke_token/`: django-oauth-toolkit، authorization code مع PKCE S256. لا تستخدم password grant. scopes `read` و`write` لا تمنح أدواراً؛ user الأساسي يبقى تحت ACL. تطبيقات service-to-service يجب أن ترتبط بحساب تنفيذي محدود الوصول، لا superuser. Session+CSRF للمتصفح. MFA إلزامي للإنتاج. OIDC Authlib يتحقق من state/nonce/ID token/issuer مع `OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_CALLBACK_URL`. `ExternalIdentity(source=OIDC_IDENTITY_SOURCE,external_id=sub)` يجب أن يُربط مسبقاً؛ لا إنشاء مستخدم تلقائي بالبريد. ربط SAML يكون عبر broker OIDC مع اعتماد metadata والتوقيع لدى المؤسسة. نفاذ يحتاج حساب وخدمة وعقد هوية مزود؛ لا نتيجة وهمية أو تحويل TOTP إلى إثبات نفاذ.

## Callback HMAC

`X-Ehsan-Timestamp`: وقت Unix، فرق ≤300s. `X-Ehsan-Signature`: hex HMAC-SHA256(secret, timestamp + '.' + raw request bytes). لا يجوز إعادة ترتيب JSON بعد التوقيع. حد2MiB. الأحداث تحتاج معرفاً ثابتاً؛ تغير المحتوى لنفس المعرف يرفض. لا تستخدم CSRF في callbacks لأنها لا تعتمد جلسة مستخدم؛ يعتمد التحقق HMAC والنشاط وعقد البيانات.

## HR/Snso

`POST /integrations/hr/<key>/`

```json
{"event_id":"source-event-123","data":{"units":[{"id":"U1","name":"الخدمات الرعوية","active":true}],"employees":[{"id":"E1","employee_number":"001","email":"employee@example.org","first_name":"اسم","last_name":"الموظف","job_title":"أخصائي","units":["U1"],"active":true,"terminated":false}]}}
```

اختياري `units.head_id`, `employees.manager_id`. IDs مصدر ثابتة. لا يتغير role من حمولة HR. terminated/active=false يعطل الحساب ويلغي OAuth access/refresh، جلسات Django لا تقبل المستخدم غير النشط. لدفعة يومية GET endpoint يعيد نفس `{event_id,data}`، Bearer secret. المصدر الفعلي قد يحتاج موصلاً يحول استجابة Snso إلى هذا العقد؛ لا نعرف عقد Snso من دون وثائقه وبيانات الدخول. missing employees ليست إنهاء خدمة.

CLI ملف رسمي: `python manage.py sync_hr export.json --source Snso --event event-123`.

## قائمة الإرسال

POST endpoint للمورد:

```json
{"version":1,"id":"delivery-uuid","event":"notification.email","data":{"to":"employee@example.org","text":"لديك إشعار جديد","url":"/communications/"}}
```

Idempotency-Key=deliveryUUID. الأحداث notification.email/digest/sms وreport.scheduled وsignature.request وretention.backup-erasure؛ HTTP2xx يثبت قبول المورد لا وصول المستلم. ثماني محاولات، retry مع تأخير متزايد حتى ساعة، فشل ظاهر من الضبط. activation لا يُجري رسالة اختبار دون مهمة صريحة.

### Microsoft Graph

options: `{ "adapter":"microsoft-graph","tenant":"tenant-guid","client_id":"application-id","mailbox":"mailbox@example.org" }`. secret_env يشير إلى client secret. app permission `Mail.Send` مع admin consent وتقييد البريد لصندوق الجمعية. `/users/{mailbox}/sendMail`، التقرير XLSX attachment. المستند الرسمي: https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0 . التسليم عبر Microsoft يحتاج مراجعة موقع المعالجة ونقل البيانات واعتماد PDPL؛ استضافة التطبيق السعودية لا تحدد منطقة البريد تلقائياً.

### استقبال البريد

`POST /integrations/email/<key>/` HMAC من موصل البريد (Exchange/M365) مع options registrar/owner (employee_number) وunit/classification (IDs). party must already exist and match sender email.

```json
{"message_id":"provider-stable-id","sender":"known-party@example.org","subject":"طلب بيانات","body":"النص","date":"2026-10-08","attachments":[{"name":"letter.pdf","base64":"BASE64"}]}
```

رسالة واحدة = وارد واحد؛ المكرر نفس المحتوى يرجع المرجع، المختلف يرفض. يطبق فحص المرفقات والتشفير. عناوين غير معرفة تستلزم مراجعة دليل الجهات؛ لا تنشأ party من رسالة مجهولة.

## التوقيع

signature.request يرسل requestUUID وpdf_base64 وsha256 وsigner_employee. المورد يوقع PDF تراكميّاً ويحفظ كامل النسخة الأصلية. `POST /integrations/signature/<key>/` HMAC:

```json
{"request":"signature-request-uuid","pdf_base64":"SIGNED-PDF-BASE64"}
```

سياسة نموذج: `{ "OUT":{"methods":["pades"],"signers":[{"employee":"001","fingerprint":"CERT-SHA256-FINGERPRINT"}]},"INT":["internal_attestation"] }`. قائمة الشهادات وترتيبها معتمدة ومربوطة بالموظفين؛ الجذور عبر SIGNATURE_TRUST_ROOTS، revocation hard-fail، TSA، veraPDF. عند نقص verifier أو ثقة أو شهادة ترفض النتيجة. شهادة الإنتاج وتوثيق نفاذ ومزودOTP الموثق تحتاج حساب المزود؛ هذه ليست حالات اختبار وهمية.

## DMS وإتلاف النسخ الاحتياطية

retention.backup-erasure: caseUUID/reference/attachment IDs+hashes. الموصل السعودي ينفذ الإتلاف وفق القانون والسياسة المعتمدة ويرجع دليل التنفيذ، لا مجرد قبول طلب.

`POST /integrations/dms/<key>/` HMAC:

```json
{"case":"retention-case-uuid","delivery":"matching-delivery-uuid","backup_erased":true,"proof_reference":"DMS-MINUTES-123"}
```

الطلب معتمد من اللجنة، والرد يرتبط بالdelivery الحقيقي؛ يظل تنفيذ إتلاف مرفقات التطبيق إجراء منفصلاً بمبرر ومحضر. السجل والتدقيق لا يحذفان. يلزم عقد موصل يؤكد جميع النسخ/replicas/السحابة وآجالها؛ لا تعتمد metadata كدليل أن نسخة احتياطية أزيلت.

## Webhooks

registered/referred/late/closed، لا subject/body/party. يستبعد restricted/secret كلياً. payload يحمل uid/reference/event/version1. التوقيع والوقت وIdempotency-Key كما أعلاه. فشل webhook لا يلغي المعاملة المحلية.
