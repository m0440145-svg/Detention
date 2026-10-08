# REST API

المصادقة بجلسة Django بعد تسجيل الدخول. كل طلب كتابة يحتاج رمز CSRF من الصفحة/الكوكي مع `X-CSRFToken`. بيانات الرد paginated بواقع 30 سجلًا، والكتابة محكومة بصلاحيات الدور ونطاق المهمة. لا توجد عمليات حذف للموارد التشغيلية.

| المسار | الطرق | الوظيفة |
|---|---|---|
| `/api/tasks/` | GET / POST | قائمة وإنشاء مهمة |
| `/api/tasks/{id}/` | GET / PUT / PATCH | تفاصيل وتعديل تكليف بواسطة مدير مخول؛ سبب التعديل إلزامي |
| `/api/tasks/{id}/progress/` | POST | progress، comment، accomplished، remaining |
| `/api/tasks/{id}/transition/` | POST | status، comment؛ انتقال معتمد فقط |
| `/api/tasks/{id}/comments/` | GET / POST | body، parent اختياري، mentions وmentioned_units |
| `/api/tasks/{id}/obstacles/` | GET / POST | عوائق؛ النوع والوصف والجهة والإجراء ومسؤول التدخل والحل المتوقع والتأثير |
| `/api/tasks/{id}/resolve-obstacle/` | POST | obstacle، resolution؛ رئيس الوحدة أو الإدارة |
| `/api/tasks/{id}/subtasks/` | GET / POST | إنشاء فرعية أو تحديث مع id، title، owner، due_date، progress، comment |
| `/api/tasks/{id}/attachments/` | GET / POST | رفع multipart بحقل file وsubtask اختياري |
| `/api/tasks/{id}/audit/` | GET | سجل النشاط والقيم القديمة والجديدة |
| `/api/tasks/{id}/escalate/` | POST | reason؛ تصعيد للمدير التنفيذي |
| `/api/meetings/` | GET / POST | الاجتماعات؛ تعديل عبر /id/ |
| `/api/decisions/` | GET / POST | القرارات؛ تعديل عبر /id/ |
| `/api/decisions/{id}/audit/` | GET | سجل تدقيق القرار؛ لا يقبل تعديلًا أو حذفًا |
| `/api/decisions/{id}/attachments/` | POST | file وreason، وapproved_minutes اختياري لتعيين مرفق المحضر |
| `/api/decisions/{id}/convert-to-task/` | POST | إنشاء مهمة مرتبطة؛ يمكن تكراره لمهام متعددة مقصودة |
| `/api/units/` | GET / POST | الوحدات؛ الكتابة لمسؤول النظام |
| `/api/notifications/` | GET | تنبيهات صاحب الجلسة فقط |
| `/api/notifications/{id}/` | PATCH | تحديث read فقط |
| `/api/metrics/` | GET | مؤشرات ضمن نطاق المستخدم والفلاتر |
| `/files/{id}/` | GET | تنزيل محمي بصلاحيات المهمة أو القرار |

مثال إنشاء:

```json
{
  "title": "إعداد المحتوى التدريبي",
  "description": "تجهيز المادة ومراجعتها",
  "unit": 5,
  "owner": 12,
  "participants": [13, 14],
  "participating_units": [6],
  "assignment_mode": "manual",
  "start_date": "2026-10-08",
  "due_date": "2026-10-15",
  "priority": "important",
  "confidentiality": "normal",
  "expected_result": "حقيبة تدريبية معتمدة",
  "success_indicator": "اعتماد رئيس الوحدة",
  "source": "plan"
}
```

الأرقام مثال فقط وتستبدل بمعرفات قاعدة بياناتك. assignment_mode: `manual` أو `unit` أو `units`. المسؤول ينتمي للوحدة القائدة. status/progress لا تقبل الكتابة العامة؛ استخدم المسارات المخصصة حتى تطبق قواعد الاعتماد والتوثيق.

فلاتر GET: q، unit، owner، status، priority، source، project، decision، decision__meeting، from، to، progress_min، progress_max، page. `from` يحد بداية المهمة و`to` يحد الاستحقاق. لم تصمم هذه الواجهات بعد لجهة خارجية غير موثوقة أو لحسابات API مستقلة.

رد المهمة يتضمن `status` للحالة الفعلية و`is_overdue` و`is_blocked` كمؤشرين و`deadline_label` للعدّاد. التأخر والتعثر ليسا قيمتين مقبولتين في مسار transition. انتظار الاعتماد يظل ضمن التأخر عند تجاوز الاستحقاق. العدّاد فارغ للمغلقة والملغاة.

مسؤول النظام يدير المستخدمين والوحدات والإعدادات فقط. لا يرى الموارد التشغيلية ولا يحدّثها أو يعتمدها، حتى عند إدراجه سابقًا ضمن المشاركين. واجهات القوائم التشغيلية تعطي نطاقًا فارغًا؛ الإجراءات التشغيلية مرفوضة.

القرار يضم `issuing_authority` بالقيم board/assembly/committee، و`approved_minutes_number`، و`due_date`، و`status` بالقيم open/active/executed/amended/cancelled. الجهة المصدرة والاستحقاق إلزاميان عند الإنشاء. مرفق المحضر يرسل عبر `minutes_file` في طلب multipart عند إنشاء/تعديل القرار؛ إدخال رقم المحضر يتطلب مرفقًا جديدًا أو مرفقًا قائمًا. `approved_minutes_attachment` للقراءة فقط، والتنزيل عبر المسار المحمي `/files/{id}/`. جميع التعديلات تتطلب `reason`.

`progress` و`approved_progress` للقراءة فقط. `metric_scope=all` للإدارة التنفيذية والمساعد، و`visible` لبقية الأدوار ضمن مهامها المصرح بها. تغييرات النسب الكلية المشتقة تظهر في سجل القرار للإدارة التنفيذية؛ لا تُكشف عبر سجل القرار لمستخدم محدود النطاق.

`POST /api/followup/bulk/`: الحقول task_ids (1–100 معرف)، action (remind/escalate)، reason. يتطلب إدارة جميع المهام المختارة، ويعيد processed وskipped، ويرفض الدفعة كلها عند وجود مهمة غير مصرح بها أو نهائية. التكرار اليومي للمستخدم نفسه يُتجاوز. التذكير يرسل تنبيهًا داخليًا والتصعيد يحيل للإدارة التنفيذية.

`POST /api/tasks/{id}/obstacles/` يتطلب reason مستقلًا وغير فارغ. تمديد استحقاق المهمة عبر PATCH يتطلب reason، والإعادة عبر transition تتطلب comment يوضح السبب. قيد حالة/نسبة الإنجاز يطبّق حتى على الكتابة المباشرة لقاعدة البيانات.

الدور board (مجلس الإدارة / أمين المجلس) قراءة فقط: القرارات ونسبها الكلية والمهام المرتبطة. لا ينفّذ أي إجراء كتابة على المهام أو القرارات أو المتابعة الجماعية.
