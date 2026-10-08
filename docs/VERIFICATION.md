# سجل التحقق

التاريخ: 8 أكتوبر 2026. الإصدارات المستخدمة مثبتة في requirements.lock.

| الفحص | النتيجة |
|---|---|
| Django system check | نجاح دون تحذيرات |
| فحص إعدادات الإنتاج check --deploy مع مفتاح عشوائي وPostgreSQL مهيأ | نجاح دون تحذيرات؛ الفحص لا يفتح اتصال قاعدة البيانات |
| ترحيلات SQLite محليًا | نجاح: 0001–0007 |
| makemigrations --check --dry-run | لا تغييرات مفقودة |
| 46 اختبار قبول وأمان على SQLite | نجاح |
| collectstatic | نجاح، 168 ملفًا بما فيها ملفات Django وDRF والخط المحلي |
| JavaScript node --check | نجاح |
| فحص صفحات الخادم | نجاح تقديم HTML للشاشات الأساسية لكل الأدوار، وصفحات الإدارة للمسؤول والتقارير الاثني عشر |
| PostgreSQL محليًا | تعذر تشغيل خادم مؤقت بسبب قيود صلاحيات مستخدم العملية؛ ليس اختبارًا ناجحًا |
| PostgreSQL في GitHub Actions | نجحت النسخة السابقة؛ نتيجة هذه التعديلات تتحقق من تشغيل workflow المرتبط بالـ commit |
| Docker/Caddy والنسخ والاستعادة | ملفات جاهزة، لم تُشغّل في هذه البيئة لعدم توفر Docker وخادم نشر |
| QA بصري داخل متصفح وأجهزة فعلية | لم يُنفذ؛ المتاح هنا اختبار تقديم HTML والاستجابات وليس فحصًا بصريًا |
| نشر نطاق وHTTPS عام | غير منفذ؛ لا توجد بيانات استضافة محددة |

اختبار القبول الرئيسي ينفذ طلبات HTTP على API: إنشاء المهمة والإسناد وإشعار المسؤول والمشارك، فتح الموظف، تحديث الإنجاز، رفع ملف، تسجيل عائق وإشعار الرئيس، رد الرئيس ومعالجة العائق، إرسال 100%، اعتماد الرئيس، إغلاق، ثم مؤشرات وتقرير وسجل. اختبار مستقل يحول قرارًا إلى عدة مهام ويتحقق من نسبة التنفيذ والاعتماد.

الحماية تختبر وصول موظف آخر ورئيس وحدة أخرى، تنزيل الملفات، مستخدم العرض، منع تحديث الحالة عبر PATCH عام، الإلزام بمسؤول تابع للوحدة، منع الاعتماد الذاتي، الإعادة وإعادة التسليم، CSRF، الإشارات والردود العابرة للمهام، بنية الملفات، تفرد التنبيهات والتصعيدات، وعدم قابلية تعديل وحذف سجل النشاط من ORM وقاعدة البيانات.

تعديلات 8 أكتوبر: نجاح حالات العدّاد قبل الاستحقاق واليوم وبعده مع انتظار الاعتماد وإخفائه للمغلقة والملغاة. اختُبر اجتماع التأخر والعائق دون تغيير «بانتظار رد»، ومعالجة عوائق متعددة، وظهور انتظار الاعتماد في تقرير التأخر. مسؤول النظام مرفوض في التنفيذ والاعتماد حتى كمشارك أو superuser، ويستمر في إدارة الوحدات والمستخدمين والإعدادات.

`scripts/verify_indicator_migration.py` يختبر ترحيل قاعدة SQLite مع حالات قديمة: استعادة الحالة من Audit، بدائل غياب التاريخ، الحفاظ على سجل النشاط القديم ومحفز حمايته، حذف الخيارين من Catalog، ومنع حالة overdue بقيد قاعدة البيانات. يستخدم قاعدة مؤقتة مستقلة ولا يغيّر قاعدة التشغيل.

تحديث القرارات: نجاح إنشاء HTML مع محضر مرفق، وإنشاء وتعديل REST عبر خدمة تدقيق موحدة، وإلزام سبب التعديل، وضبط تواريخ الاستحقاق، وحفظ نسخة المحضر السابقة، ومنع تنزيله خارج نطاق القراءة. النسب الكلية والنسب ضمن المهام المرئية اختُبرت مع مهمة سرية، بما في ذلك عدم كشف نسبها في سجل التدقيق. اختُبرت قيم قبل/بعد لعودة 100% إلى 99% ولزيادة الاعتماد من 0% إلى 100%. حماية Audit بقيت فعّالة بعد ترحيل 0006؛ الاختبار المستقل يتحقق أيضًا من ربط أثر قرار سابق دون تعديل الأصل أو اختلاق بيانات استحقاق قديمة.

تحديث المجلس والمتابعة: نجاح نطاق القراءة للمجلس مع مهمة قرار سرية ومنع المهام المستقلة والكتابة وتقارير الموظفين. مركز المتابعة اختُبر بمهمة واحدة تتداخل في مجموعات المخاطر الأربع. التذكير الجماعي اختُبر لتفرد المهام وعدم التكرار اليومي وحفظ سجل السبب، والتصعيد لإشعار الإدارة. دفعة تحتوي مهمة خارج النطاق رُفضت قبل إنشاء أي تنبيه. أسباب التعثر والتمديد والإعادة اختُبرت مع مسافات فارغة. الكتابة المباشرة لحالة/نسبة متعارضتين رُفضت بقيد قاعدة البيانات. اختبار الترحيل يغطي حالات 100% أثناء التنفيذ واعتماد/إغلاق ناقص وعائق مع 100%، ويحفظ نسب الإنجاز الناقصة دون رفعها.

Numbers and demo data: Arabic templates use ASCII digits, a decimal dot and no thousands grouping. Demo lead units follow task topics; food baskets and water are assigned to welfare services, HR policies to support services. The supplier-blocked water task (TSK-00017 in a fresh demo) is waiting at 25%, rather than assigned at 0%. Existing operational records are not rewritten by the seed command.

Phone layout: at viewport widths up to 700px, the nine-column task list becomes two-column cards, with a label for every value and full-width title/actions. Desktop and print keep the table. HTML/export checks verify all nine values are preserved. Visual browser testing at phone widths remains pending because the managed Sites environment does not expose the required browser capability.

Runtime acceptance: real session login through the JSON endpoint (without force_authenticate), CSRF before login and after token rotation, multipart upload and byte-for-byte protected download, another unit's denial, session logout invalidation, inactive-account denial, and a shared HTML/API failure limit. Escalations tested at configured 2/4/6-day boundaries including pending approval, repeat deduplication, and terminal-task exclusion. The scheduler command was executed once through Django's command system and generated notifications and audit records without a web request. These are isolated application tests, not evidence of a production scheduler running. Production Docker/host activation remains dependent on access to the target host.
