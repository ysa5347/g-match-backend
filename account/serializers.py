from rest_framework import serializers
from .models import CustomUser, Agreement


class UserInfoSerializer(serializers.ModelSerializer):
    """사용자 정보 조회용 Serializer"""
    is_oidc_user = serializers.BooleanField(read_only=True)

    class Meta:
        model = CustomUser
        fields = [
            'uid', 'email', 'name', 'student_id', 'phone_number',
            'birth_year', 'gender', 'house',
            'is_age_public', 'is_house_public',
            'is_oidc_user', 'date_joined'
        ]
        read_only_fields = [
            'uid', 'email', 'name', 'student_id', 'phone_number',
            'is_oidc_user', 'date_joined'
        ]


class UserUpdateSerializer(serializers.ModelSerializer):
    """
    사용자 정보 수정용 Serializer

    Note: email, name, student_id, phone_number는 GIST IdP에서 관리하므로
    사용자가 직접 수정할 수 없습니다.
    """
    class Meta:
        model = CustomUser
        fields = [
            'birth_year', 'gender', 'house',
            'is_age_public', 'is_house_public'
        ]


class AgreementSerializer(serializers.ModelSerializer):
    """약관 동의용 Serializer"""
    class Meta:
        model = Agreement
        fields = ['terms_of_service', 'privacy_policy']

    def validate(self, data):
        """모든 약관 동의 필수"""
        if not data.get('terms_of_service'):
            raise serializers.ValidationError({
                'terms_of_service': '서비스 이용약관에 동의해야 합니다.'
            })
        if not data.get('privacy_policy'):
            raise serializers.ValidationError({
                'privacy_policy': '개인정보 처리방침에 동의해야 합니다.'
            })
        return data


class OIDCUserInfoSerializer(serializers.Serializer):
    """
    GIST IdP OIDC로부터 받은 사용자 정보 검증용 Serializer

    GIST IdP id_token claims:
    - sub: 고유 식별자
    - email: GIST 이메일
    - name: 사용자 이름
    - student_id: 학번
    - phone_number: 전화번호
    """
    sub = serializers.CharField(required=True, help_text='GIST IdP 고유 식별자')
    email = serializers.EmailField(required=True, help_text='GIST 이메일')
    name = serializers.CharField(required=False, allow_blank=True, default='')
    student_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    phone_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    email_verified = serializers.BooleanField(required=False, default=False)

    def validate_email(self, value):
        """GIST 이메일 검증"""
        if not (value.endswith('@gist.ac.kr') or value.endswith('@gm.gist.ac.kr')):
            raise serializers.ValidationError('GIST 이메일만 사용 가능합니다.')
        return value


class OIDCCallbackSerializer(serializers.Serializer):
    """OIDC Callback 파라미터 검증용 Serializer"""
    code = serializers.CharField(required=True, help_text='Authorization code')
    state = serializers.CharField(required=True, help_text='State parameter')
    error = serializers.CharField(required=False, allow_blank=True)
    error_description = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        """에러 응답 확인"""
        if data.get('error'):
            raise serializers.ValidationError({
                'error': data.get('error'),
                'error_description': data.get('error_description', 'Unknown error')
            })
        return data


# ==============================================
# 아래 Serializer들은 더 이상 사용되지 않음 (OIDC 전환으로 인해)
# 기존 코드 호환성을 위해 유지하되, Deprecated 표시
# ==============================================

class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    [DEPRECATED] 회원가입용 Serializer

    Note: GIST IdP OIDC 인증으로 전환되어 더 이상 사용되지 않습니다.
    사용자 정보는 GIST IdP에서 직접 제공받습니다.
    """
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = CustomUser
        fields = [
            'email', 'password', 'password_confirm', 'name',
            'student_id', 'phone_number', 'birth_year', 'gender', 'house'
        ]
        extra_kwargs = {
            'password': {'write_only': True},
            'email': {'required': True},
            'name': {'required': True},
        }

    def validate_email(self, value):
        """GIST 이메일 검증"""
        if not value.endswith('@gist.ac.kr'):
            raise serializers.ValidationError('GIST 이메일만 사용 가능합니다.')
        if CustomUser.objects.filter(email=value).exists():
            raise serializers.ValidationError('이미 등록된 이메일입니다.')
        return value

    def validate(self, data):
        """비밀번호 확인"""
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError({
                'password_confirm': '비밀번호가 일치하지 않습니다.'
            })
        return data

    def create(self, validated_data):
        """사용자 생성"""
        validated_data.pop('password_confirm')
        password = validated_data.pop('password')
        user = CustomUser.objects.create_user(
            password=password,
            **validated_data
        )
        return user


class UserLoginSerializer(serializers.Serializer):
    """
    [DEPRECATED] 로그인용 Serializer

    Note: GIST IdP OIDC 인증으로 전환되어 더 이상 사용되지 않습니다.
    로그인은 GIST IdP를 통해 처리됩니다.
    """
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=True)


class EmailVerificationSerializer(serializers.Serializer):
    """
    [DEPRECATED] 이메일 인증코드 발송용 Serializer

    Note: GIST IdP OIDC 인증으로 전환되어 더 이상 사용되지 않습니다.
    이메일 인증은 GIST IdP에서 처리됩니다.
    """
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        """GIST 이메일 검증"""
        if not value.endswith('@gist.ac.kr'):
            raise serializers.ValidationError('GIST 이메일만 사용 가능합니다.')
        if CustomUser.objects.filter(email=value).exists():
            raise serializers.ValidationError('이미 등록된 이메일입니다.')
        return value


class CodeVerificationSerializer(serializers.Serializer):
    """
    [DEPRECATED] 인증코드 검증용 Serializer

    Note: GIST IdP OIDC 인증으로 전환되어 더 이상 사용되지 않습니다.
    """
    email = serializers.EmailField(required=True)
    code = serializers.CharField(max_length=8, min_length=8, required=True)

    def validate_code(self, value):
        """숫자 8자리 확인"""
        if not value.isdigit():
            raise serializers.ValidationError('인증코드는 8자리 숫자입니다.')
        return value
