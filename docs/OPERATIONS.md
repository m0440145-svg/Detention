# تشغيل واستعادة

## بدء الإنتاج

استخدم `compose.yaml` و`.env` الخاص بالخادم. `DEBUG=0`، مضيفون ونطاق محددان، ومفتاح مستقل عشوائي طويل. خدمة web لا تنشر منفذها؛ Caddy هو المدخل الوحيد ويعيد توجيه HTTP إلى HTTPS. `TRUST_PROXY=1` صالح فقط خلف هذا الوكيل الموثوق.

في البداية أنشئ مسؤولًا عبر `createsuperuser` ثم أضف موظفي الجمعية ووحداتهم. Seed مخصص لقاعدة تطوير منفصلة. لا تحفظ الملفات أو النسخ الاحتياطية أو البيانات الحقيقية في GitHub.

## القواعد والتنبيهات

خدمة scheduler تفحص القواعد كل دقيقة. إعدادات الافتراضي: 30% من مدة المهمة دون إنجاز، 70% من المدة وإنجاز أقل من 30%، خمسة أيام بلا تحديث، تذكير قبل ثلاثة أيام، وتصعيد للرئيس بعد يوم، للمساعد بعد ثلاثة، وللمدير بعد سبعة. المهام الحرجة تستخدم معامل 0.5. القواعد من لوحة الإعدادات. التنبيه داخل التطبيق لا يتطلب فتح الموظف للمنصة وقت إنشاء السجل.

خدمة واحدة للمجدول. عند التشغيل من cron استعمل `evaluate_tasks` بدل تشغيل حلقتين؛ التفرد يقلل التكرار لكن التشغيل المتزامن ليس نمط التشغيل المعتمد.

## النسخ الاحتياطي

نفذ من مجلد المشروع:

```bash
sh deploy/backup.sh
```

ينتج ملف PostgreSQL بصيغة custom وأرشيف media. الجدولة في cron الخاص بالخادم مثل تشغيل يومي 02:00، ضمن مسار المشروع الصحيح وبصلاحيات محدودة. انسخ الملفات إلى وجهة مشفرة مستقلة وحدد مدة احتفاظ واختبر الاستعادة دوريًا. بيانات Caddy قابلة لإعادة الإنشاء، لكن .env ومفاتيح الوصول تحفظ في مخزن أسرار مستقل.

## الاستعادة

اختبر أولًا على بيئة معزولة. هذه الأوامر **تستبدل قاعدة البيانات الموجودة**؛ لا تنفذها على الإنتاج قبل التحقق من النسخة وأخذ نسخة حديثة:

```bash
docker compose stop web scheduler
cat backups/database-YYYYMMDDTHHMMSSZ.dump | docker compose exec -T db sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose run --rm --no-deps -T web tar -xzf - -C /app < backups/media-YYYYMMDDTHHMMSSZ.tar.gz
docker compose up -d web scheduler
```

التطبيق يثبت ترحيلات أحدث عند بدء الخدمة؛ لا تستعد قاعدة قديمة ببرنامج غير متوافق قبل اختبارها. تأكد من الحسابات وتنزيل المرفقات والتقارير وسجل النشاط بعد الاستعادة.

## المراقبة

`docker compose logs web scheduler proxy` لتشخيص التشغيل. مراقبة توافر HTTP، حجم القرص، عمر آخر نسخة احتياطية، تعطل scheduler، وأخطاء PostgreSQL مطلوبة من مشغل الخادم. لا توجد منصة مراقبة خارجية أو مزود بريد مهيأ في هذا المستودع.

طلبات API ذات الجلسة لها throttle من DRF. الإعداد الافتراضي للذاكرة محلي لكل عملية؛ عند التوسع استخدم cache مشتركة مثل Redis أو تحديد معدل على الوكيل. الحد الخاص بمحاولات تسجيل الدخول محفوظ في قاعدة البيانات ويشمل العمليات المختلفة.

## تحديث

راجع فحوص CI ثم اسحب الإصدار، خذ نسخة احتياطية، شغل `docker compose up --build -d`، وافحص سجلات الترحيل ودورة مهمة اختبارية. تثبيت المكتبات المضبوط في `requirements.lock`، والمديات المسموح بها في `requirements.txt`. لا تشغّل Django runserver للإنتاج.

## Runtime activation and verification

The hosted Sites preview is static; it does not run Django, authenticate employees, store uploads or execute scheduled rules. Production activation requires a Django/PostgreSQL host and domain/proxy configuration. Do not treat publishing the preview or pushing GitHub as starting these services.

On the configured production host:

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 scheduler
docker compose exec -T scheduler python manage.py run_scheduler --once
```

The scheduler evaluates immediately and then every 60 seconds, without browser traffic. Each successful cycle logs its Riyadh timestamp and new escalation count; a database-not-ready error is logged and retried, while `--once` exits with an error for monitoring. Read the actual scheduler logs to verify activation. `--interval` is available for other deployment environments; run one continuous scheduler instance.

Rules re-read saved settings each cycle. Escalation boundaries are calendar days after the due date in Asia/Riyadh, including pending approval/completed tasks, until closure or cancellation. Critical deadlines use `ceil(configured_days * critical_multiplier)`, with a minimum of one day. Each task/recipient/level is created once; repeated evaluations do not duplicate alerts. Audit records include the actual delay, effective threshold and reason. Stale updates use the Riyadh date of the last update.

Acceptance before real use: log in through `/api/auth/login/`, upload a fictional TXT attachment, download it from an authorized account, verify another unit cannot download it, then verify a test overdue task reaches its configured escalation threshold. Confirm media survives a web-container restart and keep the scheduler logs as operational evidence.
