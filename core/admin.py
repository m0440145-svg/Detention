from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import *
@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets=UserAdmin.fieldsets+(("الموظف",{'fields':('role','employee_number','phone','job_title','manager','units')}),)
    add_fieldsets=UserAdmin.add_fieldsets+(("الموظف",{'fields':('email','employee_number','role')}),)
    filter_horizontal=UserAdmin.filter_horizontal+('units',)
# Operational writes go through audited services, not Django admin shortcuts.
@admin.register(Audit,Task,Unit,Meeting,Decision,Obstacle,Attachment,Update,Comment,Escalation,RuleSettings,LoginAttempt,Notification,Subtask)
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False
