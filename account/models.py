import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import EmailValidator


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    uid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(
        unique=True,
        validators=[EmailValidator()],
        help_text='GIST email address (@gist.ac.kr)'
    )

    # Basic Information
    name = models.CharField(max_length=100)
    student_id = models.CharField(max_length=20, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    # Profile Information
    birth_year = models.IntegerField(blank=True, null=True)
    gender = models.CharField(
        max_length=1,
        choices=[('M', 'Male'), ('F', 'Female')],
        blank=True,
        null=True
    )
    house = models.CharField(max_length=50, blank=True, null=True)

    # Privacy Settings
    is_age_public = models.BooleanField(default=True)
    is_house_public = models.BooleanField(default=True)

    # OAuth Fields
    kakao_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    naver_id = models.CharField(max_length=100, blank=True, null=True, unique=True)

    # Django Required Fields
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(auto_now=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.email} ({self.name})"

    @property
    def is_gist_email(self):
        return self.email.endswith('@gist.ac.kr')


class Agreement(models.Model):
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='agreement',
        primary_key=True
    )
    terms_of_service = models.BooleanField(default=False)
    privacy_policy = models.BooleanField(default=False)
    agreed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'agreements'
        verbose_name = 'Agreement'
        verbose_name_plural = 'Agreements'

    def __str__(self):
        return f"Agreement for {self.user.email}"
