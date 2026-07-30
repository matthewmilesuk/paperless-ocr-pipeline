from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """
    Custom user model -- currently identical to Django's built-in User.

    Introduced with no extra fields (yet) specifically because swapping
    AUTH_USER_MODEL after other models have migrations referencing
    auth.User is a painful, largely-manual migration to walk back later.
    Doing it now, before any app migrations exist, avoids that per
    Django's own docs on substituting a custom user model.
    """

    pass
