from rest_framework import serializers
from .models import CustomUser, Agreement


class UserRegistrationSerializer(serializers.ModelSerializer):
    """회원가입용 Serializer"""
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
    """로그인용 Serializer"""
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=True)


class UserInfoSerializer(serializers.ModelSerializer):
    """사용자 정보 조회용 Serializer"""
    class Meta:
        model = CustomUser
        fields = [
            'uid', 'email', 'name', 'student_id', 'phone_number',
            'birth_year', 'gender', 'house',
            'is_age_public', 'is_house_public',
            'date_joined'
        ]
        read_only_fields = ['uid', 'email', 'date_joined']


class UserUpdateSerializer(serializers.ModelSerializer):
    """사용자 정보 수정용 Serializer"""
    class Meta:
        model = CustomUser
        fields = [
            'name', 'student_id', 'phone_number',
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


class EmailVerificationSerializer(serializers.Serializer):
    """이메일 인증코드 발송용 Serializer"""
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        """GIST 이메일 검증"""
        if not value.endswith('@gist.ac.kr'):
            raise serializers.ValidationError('GIST 이메일만 사용 가능합니다.')
        if CustomUser.objects.filter(email=value).exists():
            raise serializers.ValidationError('이미 등록된 이메일입니다.')
        return value


class CodeVerificationSerializer(serializers.Serializer):
    """인증코드 검증용 Serializer"""
    email = serializers.EmailField(required=True)
    code = serializers.CharField(max_length=6, min_length=6, required=True)

    def validate_code(self, value):
        """숫자 6자리 확인"""
        if not value.isdigit():
            raise serializers.ValidationError('인증코드는 6자리 숫자입니다.')
        return value
