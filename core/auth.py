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
