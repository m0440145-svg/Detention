from django.db.models import Q
from django.core.exceptions import PermissionDenied
from .models import Task, Role
GLOBAL_ROLES = {Role.ADMIN,Role.EXECUTIVE,Role.ASSISTANT}
def tasks_for(user):
    qs=Task.objects.select_related('owner','unit','decision','decision__meeting').prefetch_related('participants','participating_units')
    if user.role in GLOBAL_ROLES: return qs
    direct=Q(owner=user)|Q(participants=user)
    unit_ids=user.units.values_list('pk',flat=True)
    if user.role in [Role.HEAD,Role.VIEWER]:
        scope=direct|Q(unit_id__in=unit_ids)|Q(participating_units__in=unit_ids)
    else: scope=direct
    # Restricted records are visible only to direct assignees or the lead unit head.
    allowed=Q(confidentiality='normal')|direct
    if user.role==Role.HEAD: allowed |= Q(unit__head=user)
    return qs.filter(scope if user.role in [Role.HEAD,Role.VIEWER] else direct).filter(allowed).distinct()
def can_manage(user,task=None):
    return user.role in GLOBAL_ROLES or (user.role==Role.HEAD and (task is None or task.unit.head_id==user.pk))
def can_work(user,task):
    return user.role!=Role.VIEWER and (can_manage(user,task) or task.owner_id==user.pk or task.participants.filter(pk=user.pk).exists())
def require_work(user,task):
    if not can_work(user,task): raise PermissionDenied('لا تملك صلاحية تحديث هذه المهمة.')
def require_manage(user,task=None):
    if not can_manage(user,task): raise PermissionDenied('لا تملك صلاحية الإدارة أو الاعتماد.')
