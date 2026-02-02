import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import EmailValidator


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        """
        OIDC 사용자 생성 (password는 선택적)
        GIST IdP를 통해 인증하므로 password가 없을 수 있음
        """
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # OIDC 사용자는 비밀번호 없음
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

    def get_or_create_oidc_user(self, oidc_user_info):
        """
        GIST IdP OIDC로부터 받은 사용자 정보로 사용자 조회 또는 생성

        Args:
            oidc_user_info: dict containing:
                - sub: GIST IdP 고유 ID
                - email: GIST 이메일
                - name: 사용자 이름
                - student_id: 학번
                - phone_number: 전화번호

        Returns:
            tuple: (user, created)
        """
        gist_id = oidc_user_info.get('sub')
        email = oidc_user_info.get('email')

        # gist_id로 먼저 조회
        try:
            user = self.get(gist_id=gist_id)
            # 기존 사용자: IdP 정보로 업데이트
            user.email = email
            user.name = oidc_user_info.get('name', user.name)
            user.student_id = oidc_user_info.get('student_id', user.student_id)
            user.phone_number = oidc_user_info.get('phone_number', user.phone_number)
            user.save()
            return user, False
        except self.model.DoesNotExist:
            pass

        # 이메일로 조회 (기존 사용자가 있을 수 있음)
        try:
            user = self.get(email=email)
            # 기존 이메일 사용자에 gist_id 연결
            user.gist_id = gist_id
            user.name = oidc_user_info.get('name', user.name)
            user.student_id = oidc_user_info.get('student_id', user.student_id)
            user.phone_number = oidc_user_info.get('phone_number', user.phone_number)
            user.save()
            return user, False
        except self.model.DoesNotExist:
            pass

        # 새 사용자 생성
        user = self.create_user(
            email=email,
            gist_id=gist_id,
            name=oidc_user_info.get('name', ''),
            student_id=oidc_user_info.get('student_id'),
            phone_number=oidc_user_info.get('phone_number'),
        )
        return user, True


class CustomUser(AbstractBaseUser, PermissionsMixin):
    uid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(
        unique=True,
        validators=[EmailValidator()],
        help_text='GIST email address (@gist.ac.kr)'
    )

    # GIST IdP OIDC identifier (sub claim)
    gist_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text='GIST IdP OIDC subject identifier'
    )

    # Basic Information (managed by GIST IdP)
    name = models.CharField(max_length=100)
    student_id = models.CharField(max_length=20, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    # Profile Information (managed by our service)
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

    @property
    def is_oidc_user(self):
        """GIST IdP OIDC로 인증된 사용자인지 확인"""
        return bool(self.gist_id)


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
