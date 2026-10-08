from django.contrib import admin
from django.urls import path,include
from rest_framework.routers import DefaultRouter
from core import views,api
from correspondence import identity_views,integration_views,portal_views,governance_api
from correspondence import api as mail_api
from meetinghub import api as meeting_api
router=DefaultRouter()
router.register("meeting-members",meeting_api.MemberViewSet,basename="api-meeting-member")
router.register("meeting-committees",meeting_api.CommitteeViewSet,basename="api-meeting-committee")
router.register("governance-meetings",meeting_api.MeetingViewSet,basename="api-governance-meeting")
router.register('communications',mail_api.MailViewSet,basename='api-mail')
router.register('tasks',api.TaskViewSet,basename='api-task')
router.register('units',api.UnitViewSet,basename='api-unit')
router.register('decisions',api.DecisionViewSet,basename='api-decision')
router.register('meetings',api.MeetingViewSet,basename='api-meeting')
router.register('notifications',api.NotificationViewSet,basename='api-notification')
urlpatterns=[path("meetings/",include("meetinghub.urls")),path("api/privacy/",governance_api.privacy),path("api/communications/<int:pk>/retention/",governance_api.retention),path("api/communications-reports/<str:kind>/",governance_api.report),path("api/communications-bulk-referral/",governance_api.bulk_referral),path("security/language/",identity_views.language),path("integrations/dms/<str:key>/",integration_views.dms_callback),path("integrations/email/<str:key>/",integration_views.email_callback),path("verify/<uuid:uid>/",portal_views.verify_public),path("integrations/signature/<str:key>/",integration_views.signature_callback),path("integrations/hr/<str:key>/",integration_views.hr_callback),path("o/",include("oauth2_provider.urls",namespace="oauth2_provider")),path("security/enroll/",identity_views.enrollment),path("security/sso/",identity_views.oidc_start),path("security/callback/",identity_views.oidc_callback),path('communications/',include('correspondence.urls')),path('api/communications-master/',mail_api.master_data),path('api/communications-bulk/',mail_api.bulk),path('api/auth/csrf/',views.api_csrf),path('api/auth/login/',views.api_login),path('api/auth/me/',api.session_user),path('api/auth/logout/',api.session_logout),path('login/',views.signin,name='login'),path('logout/',views.signout,name='logout'),path('',views.dashboard,name='dashboard'),path('tasks/',views.task_list,name='tasks'),path('tasks/new/',views.task_form,name='task-new'),path('tasks/<int:pk>/',views.task_detail,name='task-detail'),path('tasks/<int:pk>/edit/',views.task_form,name='task-edit'),path('followup/',views.followup,name='followup'),path('decisions/',views.decisions,name='decisions'),path('decisions/<int:pk>/',views.decision_detail,name='decision-detail'),path('directory/',views.directory,name='directory'),path('manage/<str:kind>/new/',views.generic_form,name='manage-new'),path('manage/<str:kind>/<int:pk>/',views.generic_form,name='manage-edit'),path('reports/',views.reports,name='reports'),path('notifications/',views.notifications,name='notifications'),path('settings/',views.settings,name='settings'),path('settings/catalogs/',views.catalogs,name='catalogs'),path('settings/permissions/',views.permissions,name='permissions'),path('files/<int:pk>/',views.download,name='download'),path('api/followup/bulk/',api.bulk_followup),path('api/metrics/',api.metrics),path('api/',include(router.urls)),path('admin/',admin.site.urls)]
