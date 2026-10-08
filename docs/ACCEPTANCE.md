# سجل المتطلبات ومعايير القبول

الحالة: منجز برمجيًا حيث توجد وظيفة واختبار، جاهز للربط حيث تحتاج خدمة، أو قبول خارجي حيث يحتاج بيانات/بشر/عقد. لا يعد مرور الاختبارات شهادة امتثال أو اختبار اختراق أو توقيع مزود فعلي.

| قسم المصدر | معيار القبول الأساسي | الدليل البرمجي | بوابة التشغيل/القبول |
|---|---|---|---|
| 1 الهدف والنطاق | مسار حتى الأرشفة ≤3 نقرات | views/detail/features وtests | ملاحظة خمسة مستخدمين وزمنهم |
| 2 القوالب | إصدارات/حقول/RTL/جدول/PDF/A | models/forms/documents؛ PDF metadata test | اعتماد الهوية والختم، veraPDF للموقع |
| 3 Metadata | IDs/تسلسل/وارد مكرر/جهات/SHA | services.allocate/create/attach؛ import test | تصدير Snso/آخر Sequence ومعجم الجهات |
| 4 سير العمل | multi-level/رفض/تفويض/referrals/tasks | services.transition/refer/convert；workflow tests | مصفوفة مجلس معتمدة، لا قيم demo |
| 5 الأمن | ACL/MFA/AES/scan/audit/PDPL | security/governance；tests denial/replay/tamper | Saudi hosting/DB encryption/scanner/مراجعة قانونية |
| 6 التوقيع | PAdES/TSA/QR/إبطال/ترتيب | documents/governance/callback؛ unsigned denied | مزود مرخص/شهادة/ثقة/revocation/veraPDF؛ اختبار live |
| 7 الأرشفة | لجنة/حجز/محضر/OCR/search/thread | governance/ocr/portal；retention tests | مدد معتمدة وإثبات DMS فعلي وجودة OCR عربي |
| 8 SLA | أيام عمل/مهلة أقصر/pause/تصعيد/تفضيلات | services.run_mail_rules/outbox | بريد/SMS مؤسسي وتفويض وملاحظة التسليم |
| 9 الربط | HR/OIDC/OAuth/Graph/gateway/webhooks | integrations/governance_api | وثائق Snso/مورد/IdP وربط المشاريع/المالية المرجعي |
| 10 التقارير | KPI/scoped XLSX/PDF/schedule | reports/portal export/tests | بيانات تشغيل واقعية، قبول حساب المؤشرات |
| 11 الاستخدام | أربع لوحات/RTL/لغة/mobile/focus/bulk | dashboard/templates/JS/CSS | WCAG2.1AA/قارئ شاشة/جوال/خمسة مستخدمين |
| 12 التشغيل | ownership/CI/migrations/backup/WAL | repo/lock/scheduler/deploy configs | regionSaudi،p95<2s،RPO1h/RTO4h،restore/pentest |
| 13 الانتقال | sourceIDs/sequence/dryrun/parallel/rollback | import_mail/ROLLOUT/tests | جميع صادرات Snso وجرد وتوقيع reconciliation |

## المخرجات الإحدى عشرة المطلوبة في الملف

| المخرج | الملف |
|---|---|
| SRS وقواعد BR ومعايير كل قسم | SRS.md |
| ERD وقاموس بيانات | DESIGN.md / DATA_DICTIONARY.md |
| مخططات حالات الوارد والصادر | DESIGN.md |
| دور × فعل × سرية | DESIGN.md |
| Wireframes أربع لوحات | DESIGN.md |
| OpenAPI | correspondence-openapi.json |
| مواد/ضوابط امتثال | COMPLIANCE.md؛ مراجعة قانونية لازمة |
| حالات قبول وظيفية وأمنية | SRS.md،ACCEPTANCE.md،core/tests،correspondence/tests/test_governance |
| ترحيل وتشغيل متوازٍ وتحويل ورجوع | ROLLOUT.md |
| خياران وتقدير ومقياس ≥80% | ROLLOUT.md؛ RFP غير منجز دون عروض |
| افتراضات ومخاطر مفتوحة | ROLLOUT.md |

## السيناريوهات اليدوية اللازمة

1. خمسة أشخاص: تسجيل ورفع وإحالة في أقل من 3 دقائق، وسكرتير متمرس في أقل من دقيقة؛ حفظ الوقت والأخطاء ونوع الجهاز.
2. جوال 360/390/768px وSafari/Chrome، RTL/LTR، تسعة أعمدة المهام، لوحة مفاتيح وقارئ شاشة، قياس WCAG AA.
3. ملفات عربية مطبوعة ومسح منخفض الجودة؛ مقارنة النص والحقول المقترحة بالأصل والثقة لكل صفحة، دون اعتماد صامت.
4. مزود توقيع: صالح، منتهي، ملغى، معدّل، TSA غائب، موقّع مختلف، callback قديم ومكرر؛ فقط الصحيح يجتاز.
5. إنهاء HR داخل جلسة حالية ومع token API، لا وصول لاحق ولا إشعار أو صلاحية ضمنية.
6. ترحيل كامل: أعداد وأرقام وSHA ومصرحون ومحاضر متساوية، لا تعارض في Sequence، توقيع تاريخي موضح.
7. استعادة معزولة للقاعدة والملفات والمفاتيح، تدقيق وRPO/RTO، حجب التكاملات قبل scheduler.
8. اختبار حمل واختراق قبل الإطلاق وسنوياً مع حفظ المحضر والإصدار والإصلاحات والنتائج.

لا توجد نتائج قبول خارجي مفبركة في هذا السجل.
