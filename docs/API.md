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
