def navigation(request):
    from meetinghub.models import GovernanceEntry,PlatformSettings
    singleton=PlatformSettings.objects.select_related('published').filter(pk=1).first()
    return {'published_brand':singleton.published.content if singleton and singleton.published else None,'governance_kinds':GovernanceEntry.KINDS,'unread_count':request.user.notifications.filter(read=False).count() if request.user.is_authenticated else 0}
