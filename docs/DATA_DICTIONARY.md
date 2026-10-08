# قاموس البيانات — مولد من النماذج الفعلية

تاريخ 2026-10-08. كل علاقة PROTECT تحمي المرجع التاريخي. حقول FileField تستخدم التخزين الخاص المشفر.

## User

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| password | CharField | False | False | — |
| last_login | DateTimeField | True | False | — |
| is_superuser | BooleanField | False | False | — |
| username | CharField | False | True | — |
| first_name | CharField | False | False | — |
| last_name | CharField | False | False | — |
| is_staff | BooleanField | False | False | — |
| is_active | BooleanField | False | False | — |
| date_joined | DateTimeField | False | False | — |
| email | CharField | False | True | — |
| phone | CharField | True | True | — |
| role | CharField | False | False | — |
| employee_number | CharField | False | True | — |
| job_title | CharField | False | False | — |
| manager | ForeignKey | True | False | User |
| groups | ManyToManyField | False | False | Group |
| user_permissions | ManyToManyField | False | False | Permission |
| units | ManyToManyField | False | False | Unit |

## Unit

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| name | CharField | False | True | — |
| description | TextField | False | False | — |
| head | ForeignKey | True | False | User |
| active | BooleanField | False | False | — |
| created_at | DateTimeField | False | False | — |

## Meeting

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| number | CharField | False | True | — |
| name | CharField | False | False | — |
| committee | CharField | False | False | — |
| date | DateField | False | False | — |

## Decision

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| number | CharField | False | True | — |
| meeting | ForeignKey | False | False | Meeting |
| text | TextField | False | False | — |
| date | DateField | False | False | — |
| followup_owner | ForeignKey | False | False | User |
| issuing_authority | CharField | False | False | — |
| approved_minutes_number | CharField | False | False | — |
| approved_minutes_attachment | ForeignKey | True | False | Attachment |
| due_date | DateField | True | False | — |
| status | CharField | False | False | — |

## Task

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| number | UUIDField | False | True | — |
| title | CharField | False | False | — |
| description | TextField | False | False | — |
| source | CharField | False | False | — |
| task_type | CharField | False | False | — |
| project | CharField | False | False | — |
| unit | ForeignKey | False | False | Unit |
| owner | ForeignKey | False | False | User |
| assignment_mode | CharField | False | False | — |
| start_date | DateField | False | False | — |
| due_date | DateField | False | False | — |
| priority | CharField | False | False | — |
| confidentiality | CharField | False | False | — |
| status | CharField | False | False | — |
| progress | PositiveSmallIntegerField | False | False | — |
| auto_progress | BooleanField | False | False | — |
| expected_result | TextField | False | False | — |
| success_indicator | CharField | False | False | — |
| notes | TextField | False | False | — |
| decision | ForeignKey | True | False | Decision |
| created_by | ForeignKey | False | False | User |
| created_at | DateTimeField | False | False | — |
| updated_at | DateTimeField | False | False | — |
| submitted_at | DateTimeField | True | False | — |
| closed_at | DateTimeField | True | False | — |
| participants | ManyToManyField | False | False | User |
| participating_units | ManyToManyField | False | False | Unit |

## Subtask

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| task | ForeignKey | False | False | Task |
| title | CharField | False | False | — |
| owner | ForeignKey | False | False | User |
| due_date | DateField | False | False | — |
| progress | PositiveSmallIntegerField | False | False | — |
| comment | TextField | False | False | — |

## Update

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| task | ForeignKey | False | False | Task |
| user | ForeignKey | False | False | User |
| progress | PositiveSmallIntegerField | False | False | — |
| comment | TextField | False | False | — |
| accomplished | TextField | False | False | — |
| remaining | TextField | False | False | — |
| created_at | DateTimeField | False | False | — |

## Comment

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| task | ForeignKey | False | False | Task |
| user | ForeignKey | False | False | User |
| body | TextField | False | False | — |
| parent | ForeignKey | True | False | Comment |
| created_at | DateTimeField | False | False | — |
| mentions | ManyToManyField | False | False | User |
| mentioned_units | ManyToManyField | False | False | Unit |

## Attachment

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| task | ForeignKey | True | False | Task |
| decision | ForeignKey | True | False | Decision |
| subtask | ForeignKey | True | False | Subtask |
| comment | ForeignKey | True | False | Comment |
| uploaded_by | ForeignKey | False | False | User |
| file | FileField | False | False | — |
| original_name | CharField | False | False | — |
| created_at | DateTimeField | False | False | — |

## Obstacle

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| task | ForeignKey | False | False | Task |
| reported_by | ForeignKey | False | False | User |
| kind | CharField | False | False | — |
| description | TextField | False | False | — |
| reason | TextField | False | False | — |
| caused_by | CharField | False | False | — |
| needs_decision | BooleanField | False | False | — |
| requested_action | TextField | False | False | — |
| intervention_owner | ForeignKey | False | False | User |
| expected_resolution | DateField | False | False | — |
| impact | CharField | False | False | — |
| created_at | DateTimeField | False | False | — |
| resolved_at | DateTimeField | True | False | — |
| resolution | TextField | False | False | — |

## Audit

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| task | ForeignKey | True | False | Task |
| decision | ForeignKey | True | False | Decision |
| actor | ForeignKey | True | False | User |
| action | CharField | False | False | — |
| old | JSONField | False | False | — |
| new | JSONField | False | False | — |
| created_at | DateTimeField | False | False | — |

## Notification

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| recipient | ForeignKey | False | False | User |
| task | ForeignKey | True | False | Task |
| text | CharField | False | False | — |
| key | CharField | True | True | — |
| read | BooleanField | False | False | — |
| created_at | DateTimeField | False | False | — |

## Escalation

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| task | ForeignKey | False | False | Task |
| recipient | ForeignKey | False | False | User |
| reason | TextField | False | False | — |
| level | PositiveSmallIntegerField | False | False | — |
| created_at | DateTimeField | False | False | — |

## RuleSettings

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| followup_elapsed | PositiveSmallIntegerField | False | False | — |
| risk_elapsed | PositiveSmallIntegerField | False | False | — |
| risk_progress | PositiveSmallIntegerField | False | False | — |
| stale_days | PositiveSmallIntegerField | False | False | — |
| remind_days | PositiveSmallIntegerField | False | False | — |
| head_after | PositiveSmallIntegerField | False | False | — |
| assistant_after | PositiveSmallIntegerField | False | False | — |
| executive_after | PositiveSmallIntegerField | False | False | — |
| critical_multiplier | FloatField | False | False | — |

## LoginAttempt

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| identifier | CharField | False | False | — |
| ip | GenericIPAddressField | False | False | — |
| succeeded | BooleanField | False | False | — |
| created_at | DateTimeField | False | False | — |

## Catalog

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| kind | CharField | False | False | — |
| key | CharField | False | False | — |
| label | CharField | False | False | — |
| active | BooleanField | False | False | — |

## Party

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| name | CharField | False | True | — |
| email | CharField | False | False | — |
| supervisory | BooleanField | False | False | — |
| active | BooleanField | False | False | — |

## RetentionPolicy

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| name | CharField | False | True | — |
| years | PositiveSmallIntegerField | True | False | — |
| legal_reference | TextField | False | False | — |
| approved | BooleanField | False | False | — |

## Classification

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| name | CharField | False | True | — |
| parent | ForeignKey | True | False | Classification |
| route_unit | ForeignKey | True | False | Unit |
| retention | ForeignKey | False | False | RetentionPolicy |

## MailTemplate

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| name | CharField | False | False | — |
| version | PositiveIntegerField | False | False | — |
| kind | CharField | False | False | — |
| body | TextField | False | False | — |
| active | BooleanField | False | False | — |
| created_at | DateTimeField | False | False | — |

## ApprovalRoute

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| name | CharField | False | False | — |
| kind | CharField | False | False | — |
| levels | JSONField | False | False | — |
| authority_reference | CharField | False | False | — |
| active | BooleanField | False | False | — |

## MailSettings

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| sla_days | JSONField | False | False | — |
| referral_hours | JSONField | False | False | — |
| weekend | JSONField | False | False | — |
| work_start | PositiveSmallIntegerField | False | False | — |
| work_end | PositiveSmallIntegerField | False | False | — |
| reminder_percent | PositiveSmallIntegerField | False | False | — |
| manager_percent | PositiveSmallIntegerField | False | False | — |
| executive_percent | PositiveSmallIntegerField | False | False | — |
| source_system | CharField | False | False | — |
| brand_name | CharField | False | False | — |
| license_number | CharField | False | False | — |
| national_address | CharField | False | False | — |
| footer | TextField | False | False | — |
| signature_policy | JSONField | False | False | — |
| retention_quorum | PositiveSmallIntegerField | False | False | — |
| retention_committee | ManyToManyField | False | False | User |

## Holiday

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| date | DateField | False | True | — |
| name | CharField | False | False | — |

## MailAccess

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| user | OneToOneField | False | True | User |
| register | BooleanField | False | False | — |
| templates | BooleanField | False | False | — |
| compliance | BooleanField | False | False | — |

## Sequence

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| kind | CharField | False | False | — |
| year | PositiveSmallIntegerField | False | False | — |
| last | PositiveIntegerField | False | False | — |

## Correspondence

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| uid | UUIDField | False | True | — |
| reference | CharField | True | True | — |
| kind | CharField | False | False | — |
| subject | CharField | False | False | — |
| body | TextField | False | False | — |
| search_text | TextField | False | False | — |
| external_number | CharField | False | False | — |
| original_date | DateField | True | False | — |
| party | ForeignKey | False | False | Party |
| classification | ForeignKey | False | False | Classification |
| template | ForeignKey | True | False | MailTemplate |
| unit | ForeignKey | False | False | Unit |
| owner | ForeignKey | False | False | User |
| created_by | ForeignKey | False | False | User |
| secrecy | CharField | False | False | — |
| priority | CharField | False | False | — |
| status | CharField | False | False | — |
| channel | CharField | False | False | — |
| source_platform | CharField | False | False | — |
| no_attachments | BooleanField | False | False | — |
| signer | ForeignKey | True | False | User |
| route | ForeignKey | True | False | ApprovalRoute |
| approval_roles | JSONField | False | False | — |
| approval_index | PositiveIntegerField | False | False | — |
| related | ForeignKey | True | False | Correspondence |
| decision | ForeignKey | True | False | Decision |
| created_at | DateTimeField | False | False | — |
| updated_at | DateTimeField | False | False | — |
| due_at | DateTimeField | True | False | — |
| referral_due_at | DateTimeField | True | False | — |
| external_deadline | DateTimeField | True | False | — |
| sla_hours | FloatField | False | False | — |
| paused_hours | FloatField | False | False | — |
| paused_at | DateTimeField | True | False | — |
| signature_method | CharField | False | False | — |
| signature_evidence | TextField | False | False | — |
| signed_by | ForeignKey | True | False | User |
| signed_at | DateTimeField | True | False | — |
| sent_evidence | TextField | False | False | — |
| archived_at | DateTimeField | True | False | — |
| retain_until | DateField | True | False | — |
| rich_text | BooleanField | False | False | — |
| summary | TextField | False | False | — |
| keywords | JSONField | False | False | — |
| project_reference | CharField | False | False | — |
| program_reference | CharField | False | False | — |
| personal_data | BooleanField | False | False | — |
| first_referred_at | DateTimeField | True | False | — |
| completed_at | DateTimeField | True | False | — |
| legacy_reference | CharField | False | False | — |
| cc | ManyToManyField | False | False | User |
| authorized | ManyToManyField | False | False | User |

## Referral

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| mail | ForeignKey | False | False | Correspondence |
| recipient | ForeignKey | False | False | User |
| mode | CharField | False | False | — |
| batch | UUIDField | False | False | — |
| position | PositiveIntegerField | False | False | — |
| instruction | CharField | False | False | — |
| note | TextField | False | False | — |
| created_by | ForeignKey | False | False | User |
| created_at | DateTimeField | False | False | — |
| completed_at | DateTimeField | True | False | — |
| task | OneToOneField | True | True | Task |

## Delegation

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| principal | ForeignKey | False | False | User |
| delegate | ForeignKey | False | False | User |
| starts | DateTimeField | False | False | — |
| ends | DateTimeField | False | False | — |
| reason | TextField | False | False | — |

## MailAttachment

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| mail | ForeignKey | False | False | Correspondence |
| file | FileField | False | False | — |
| name | CharField | False | False | — |
| sha256 | CharField | False | False | — |
| size | PositiveIntegerField | False | False | — |
| uploaded_by | ForeignKey | False | False | User |
| created_at | DateTimeField | False | False | — |

## MailEvent

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| mail | ForeignKey | False | False | Correspondence |
| actor | ForeignKey | True | False | User |
| action | CharField | False | False | — |
| reason | TextField | False | False | — |
| ip | CharField | False | False | — |
| old | JSONField | False | False | — |
| new | JSONField | False | False | — |
| previous_hash | CharField | False | False | — |
| digest | CharField | False | False | — |
| created_at | DateTimeField | False | False | — |

## MailAlert

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| mail | ForeignKey | False | False | Correspondence |
| recipient | ForeignKey | False | False | User |
| stage | CharField | False | False | — |
| text | CharField | False | False | — |
| key | CharField | False | True | — |
| created_at | DateTimeField | False | False | — |

## OCRJob

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| attachment | OneToOneField | False | True | MailAttachment |
| requested_by | ForeignKey | False | False | User |
| state | CharField | False | False | — |
| text | TextField | False | False | — |
| page_confidence | JSONField | False | False | — |
| proposals | JSONField | False | False | — |
| confidence | FloatField | True | False | — |
| error | TextField | False | False | — |
| created_at | DateTimeField | False | False | — |
| completed_at | DateTimeField | True | False | — |

## SavedSearch

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| user | ForeignKey | False | False | User |
| name | CharField | False | False | — |
| filters | JSONField | False | False | — |

## Integration

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| key | CharField | False | True | — |
| kind | CharField | False | False | — |
| endpoint | CharField | False | False | — |
| secret_env | CharField | False | False | — |
| active | BooleanField | False | False | — |
| events | JSONField | False | False | — |
| options | JSONField | False | False | — |
| last_sync | DateTimeField | True | False | — |

## Delivery

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| uid | UUIDField | False | True | — |
| integration | ForeignKey | False | False | Integration |
| event | CharField | False | False | — |
| payload | JSONField | False | False | — |
| dedup | CharField | False | True | — |
| state | CharField | False | False | — |
| attempts | PositiveIntegerField | False | False | — |
| next_attempt | DateTimeField | False | False | — |
| lease_until | DateTimeField | True | False | — |
| error | CharField | False | False | — |
| created_at | DateTimeField | False | False | — |
| delivered_at | DateTimeField | True | False | — |

## ExternalIdentity

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| source | CharField | False | False | — |
| external_id | CharField | False | False | — |
| user | OneToOneField | False | True | User |
| synced_at | DateTimeField | False | False | — |

## ExternalUnit

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| source | CharField | False | False | — |
| external_id | CharField | False | False | — |
| unit | OneToOneField | False | True | Unit |

## SyncReceipt

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| source | CharField | False | False | — |
| event_id | CharField | False | False | — |
| digest | CharField | False | False | — |
| summary | JSONField | False | False | — |
| created_at | DateTimeField | False | False | — |

## NotificationPreference

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| user | OneToOneField | False | True | User |
| email | BooleanField | False | False | — |
| critical_sms | BooleanField | False | False | — |
| daily_digest | BooleanField | False | False | — |
| last_digest | DateField | True | False | — |

## MFADevice

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| user | OneToOneField | False | True | User |
| secret | TextField | False | False | — |
| confirmed | BooleanField | False | False | — |
| last_step | BigIntegerField | False | False | — |
| failed_attempts | PositiveIntegerField | False | False | — |
| locked_until | DateTimeField | True | False | — |

## ExportGrant

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| mail | ForeignKey | False | False | Correspondence |
| user | ForeignKey | False | False | User |
| granted_by | ForeignKey | False | False | User |
| expires | DateTimeField | False | False | — |
| download | BooleanField | False | False | — |
| printing | BooleanField | False | False | — |
| reason | TextField | False | False | — |

## ProcessingActivity

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| name | CharField | False | False | — |
| purpose | TextField | False | False | — |
| legal_basis | TextField | False | False | — |
| categories | JSONField | False | False | — |
| recipients | TextField | False | False | — |
| retention_reference | TextField | False | False | — |
| owner | ForeignKey | False | False | User |
| updated_at | DateTimeField | False | False | — |

## SubjectRequest

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| uid | UUIDField | False | True | — |
| requester | ForeignKey | False | False | User |
| kind | CharField | False | False | — |
| description | TextField | False | False | — |
| state | CharField | False | False | — |
| due_at | DateTimeField | False | False | — |
| response | TextField | False | False | — |
| handled_by | ForeignKey | True | False | User |
| created_at | DateTimeField | False | False | — |

## RetentionCase

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| uid | UUIDField | False | True | — |
| mail | ForeignKey | False | False | Correspondence |
| action | CharField | False | False | — |
| state | CharField | False | False | — |
| created_by | ForeignKey | False | False | User |
| reason | TextField | False | False | — |
| minutes_reference | CharField | False | False | — |
| proof | JSONField | False | False | — |
| completed_at | DateTimeField | True | False | — |

## RetentionApproval

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| case | ForeignKey | False | False | RetentionCase |
| actor | ForeignKey | False | False | User |
| reason | TextField | False | False | — |
| created_at | DateTimeField | False | False | — |

## LegalHold

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| mail | ForeignKey | False | False | Correspondence |
| reason | TextField | False | False | — |
| active | BooleanField | False | False | — |
| imposed_by | ForeignKey | False | False | User |

## SignatureRequest

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| uid | UUIDField | False | True | — |
| mail | ForeignKey | False | False | Correspondence |
| signer | ForeignKey | False | False | User |
| reserved_reference | CharField | False | False | — |
| pdf_hash | CharField | False | False | — |
| content_hash | CharField | False | False | — |
| method | CharField | False | False | — |
| state | CharField | False | False | — |
| expires | DateTimeField | False | False | — |
| signed_pdf | FileField | False | False | — |
| evidence | JSONField | False | False | — |
| completed_at | DateTimeField | True | False | — |

## MigrationBatch

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| uid | UUIDField | False | True | — |
| source | CharField | False | False | — |
| digest | CharField | False | True | — |
| actor | ForeignKey | False | False | User |
| summary | JSONField | False | False | — |
| committed | BooleanField | False | False | — |
| created_at | DateTimeField | False | False | — |

## ReportSchedule

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| user | ForeignKey | False | False | User |
| report | CharField | False | False | — |
| frequency | CharField | False | False | — |
| active | BooleanField | False | False | — |
| next_run | DateTimeField | False | False | — |
| last_run | DateTimeField | True | False | — |

## APICallLog

| الحقل | النوع | nullable | فريد | العلاقة |
|---|---|---|---|---|
| actor | ForeignKey | True | False | User |
| method | CharField | False | False | — |
| path | CharField | False | False | — |
| status | PositiveIntegerField | False | False | — |
| duration_ms | PositiveIntegerField | False | False | — |
| created_at | DateTimeField | False | False | — |

