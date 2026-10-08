from django.contrib import admin
from django.urls import path,include
from rest_framework.routers import DefaultRouter
from core import views,api
router=DefaultRouter()
router.register('tasks',api.TaskViewSet,basename='api-task')
router.register('units',api.UnitViewSet,basename='api-unit')
router.register('decisions',api.DecisionViewSet,basename='api-decision')
router.register('meetings',api.MeetingViewSet,basename='api-meeting')
router.register('notifications',api.NotificationViewSet,basename='api-notification')
urlpatterns=[path('login/',views.signin,name='login'),path('logout/',views.signout,name='logout'),path('',views.dashboard,name='dashboard'),path('tasks/',views.task_list,name='tasks'),path('tasks/new/',views.task_form,name='task-new'),path('tasks/<int:pk>/',views.task_detail,name='task-detail'),path('tasks/<int:pk>/edit/',views.task_form,name='task-edit'),path('followup/',views.followup,name='followup'),path('decisions/',views.decisions,name='decisions'),path('decisions/<int:pk>/',views.decision_detail,name='decision-detail'),path('directory/',views.directory,name='directory'),path('manage/<str:kind>/new/',views.generic_form,name='manage-new'),path('manage/<str:kind>/<int:pk>/',views.generic_form,name='manage-edit'),path('reports/',views.reports,name='reports'),path('notifications/',views.notifications,name='notifications'),path('settings/',views.settings,name='settings'),path('settings/catalogs/',views.catalogs,name='catalogs'),path('settings/permissions/',views.permissions,name='permissions'),path('files/<int:pk>/',views.download,name='download'),path('api/metrics/',api.metrics),path('api/',include(router.urls)),path('admin/',admin.site.urls)]
