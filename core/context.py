def navigation(request):
    return {'unread_count':request.user.notifications.filter(read=False).count() if request.user.is_authenticated else 0}
