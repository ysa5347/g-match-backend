import uuid
from django.test import TestCase
from account.models import CustomUser, Agreement


class CustomUserModelTest(TestCase):
    """CustomUser 모델 테스트"""

    def setUp(self):
        """테스트 데이터 설정"""
        self.user_data = {
            'email': 'test@gist.ac.kr',
            'name': '테스트유저',
            'password': 'testpass123!'
        }

    def test_create_user(self):
        """일반 사용자 생성 테스트"""
        user = CustomUser.objects.create_user(**self.user_data)

        self.assertEqual(user.email, 'test@gist.ac.kr')
        self.assertEqual(user.name, '테스트유저')
        self.assertTrue(user.check_password('testpass123!'))
        self.assertIsInstance(user.user_id, uuid.UUID)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_superuser(self):
        """슈퍼유저 생성 테스트"""
        superuser = CustomUser.objects.create_superuser(
            email='admin@gist.ac.kr',
            name='관리자',
            password='adminpass123!'
        )

        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_superuser)
        self.assertTrue(superuser.is_active)

    def test_user_str_representation(self):
        """사용자 문자열 표현 테스트"""
        user = CustomUser.objects.create_user(**self.user_data)
        expected = f"{user.email} ({user.name})"
        self.assertEqual(str(user), expected)

    def test_is_gist_email_property(self):
        """GIST 이메일 검증 프로퍼티 테스트"""
        user = CustomUser.objects.create_user(**self.user_data)
        self.assertTrue(user.is_gist_email)

        # Non-GIST 이메일 (직접 생성으로 우회)
        user.email = 'test@gmail.com'
        self.assertFalse(user.is_gist_email)

    def test_default_privacy_settings(self):
        """기본 공개 범위 설정 테스트"""
        user = CustomUser.objects.create_user(**self.user_data)
        self.assertTrue(user.is_age_public)

    def test_user_with_full_profile(self):
        """전체 프로필 정보를 가진 사용자 생성 테스트"""
        full_data = {
            **self.user_data,
            'student_id': '20241234',
            'phone_number': '010-1234-5678',
            'birth_year': 2000,
            'gender': 'M',
        }
        user = CustomUser.objects.create_user(**full_data)

        self.assertEqual(user.student_id, '20241234')
        self.assertEqual(user.phone_number, '010-1234-5678')
        self.assertEqual(user.birth_year, 2000)
        self.assertEqual(user.gender, 'M')


class AgreementModelTest(TestCase):
    """Agreement 모델 테스트"""

    def setUp(self):
        """테스트 데이터 설정"""
        self.user = CustomUser.objects.create_user(
            email='test@gist.ac.kr',
            name='테스트유저',
            password='testpass123!'
        )

    def test_create_agreement(self):
        """약관 동의 생성 테스트"""
        agreement = Agreement.objects.create(
            user=self.user,
            terms_of_service=True,
            privacy_policy=True
        )

        self.assertEqual(agreement.user, self.user)
        self.assertTrue(agreement.terms_of_service)
        self.assertTrue(agreement.privacy_policy)
        self.assertIsNotNone(agreement.agreed_at)

    def test_agreement_str_representation(self):
        """약관 동의 문자열 표현 테스트"""
        agreement = Agreement.objects.create(
            user=self.user,
            terms_of_service=True,
            privacy_policy=True
        )
        expected = f"Agreement for {self.user.email}"
        self.assertEqual(str(agreement), expected)

    def test_one_to_one_relationship(self):
        """사용자-약관 1:1 관계 테스트"""
        agreement = Agreement.objects.create(
            user=self.user,
            terms_of_service=True,
            privacy_policy=True
        )

        # 사용자로부터 약관 접근
        self.assertEqual(self.user.agreement, agreement)

        # 중복 생성 시도 시 에러 발생 확인
        with self.assertRaises(Exception):
            Agreement.objects.create(
                user=self.user,
                terms_of_service=True,
                privacy_policy=True
            )

    def test_cascade_delete(self):
        """사용자 삭제 시 약관도 삭제되는지 테스트"""
        agreement = Agreement.objects.create(
            user=self.user,
            terms_of_service=True,
            privacy_policy=True
        )
        agreement_id = agreement.user_id

        # 사용자 삭제
        self.user.delete()

        # 약관도 삭제되었는지 확인
        self.assertFalse(
            Agreement.objects.filter(user_id=agreement_id).exists()
        )
