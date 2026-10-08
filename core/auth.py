from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
from .models import User
class EmailPhoneBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        try:
            user = User.objects.get(Q(email__iexact=username) | Q(phone=username) | Q(username=username))
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            User().set_password(password)
            return None
        return user if user.check_password(password) and self.user_can_authenticate(user) else None


class LoginRateLimited(Exception):
    pass


def authenticate_attempt(request, identifier, password):
    """Share the same database-backed login limit between HTML and JSON clients."""
    from datetime import timedelta
    from django.contrib.auth import authenticate
    from django.utils import timezone
    from .models import LoginAttempt
    identifier = identifier.strip().casefold()[:254]
    ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
    cutoff = timezone.now() - timedelta(minutes=15)
    failures = LoginAttempt.objects.filter(created_at__gte=cutoff, succeeded=False).filter(Q(ip=ip) | Q(identifier=identifier)).count()
    if failures >= 5:
        raise LoginRateLimited('محاولات كثيرة. حاول بعد 15 دقيقة.')
    user = authenticate(request, username=identifier, password=password)
    LoginAttempt.objects.create(identifier=identifier, ip=ip, succeeded=bool(user))
    return user
