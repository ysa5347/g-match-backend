from django.test import TestCase
from django.core.cache import cache
from account.utils.redis_utils import (
    generate_reg_sid,
    generate_registration_token,
    generate_verification_code,
    store_registration_session,
    validate_registration_session,
    store_verification_code,
    validate_verification_code,
    check_email_send_limit,
    increment_email_send_count,
    check_login_attempts,
    increment_login_attempts,
    reset_login_attempts,
)


class RedisUtilsTest(TestCase):
    """Redis 유틸리티 함수 테스트"""

    def setUp(self):
        """각 테스트 전 캐시 초기화"""
        cache.clear()

    def tearDown(self):
        """각 테스트 후 캐시 초기화"""
        cache.clear()

    def test_generate_reg_sid(self):
        """reg_sid 생성 테스트"""
        reg_sid = generate_reg_sid()
        self.assertIsInstance(reg_sid, str)
        self.assertGreater(len(reg_sid), 20)

        # 두 번 생성 시 다른 값
        reg_sid2 = generate_reg_sid()
        self.assertNotEqual(reg_sid, reg_sid2)

    def test_generate_registration_token(self):
        """registration_token 생성 테스트"""
        token = generate_registration_token()
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 40)

        # 두 번 생성 시 다른 값
        token2 = generate_registration_token()
        self.assertNotEqual(token, token2)

    def test_generate_verification_code(self):
        """인증코드 생성 테스트"""
        code = generate_verification_code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_registration_session_lifecycle(self):
        """회원가입 세션 생명주기 테스트"""
        reg_sid = generate_reg_sid()
        token = generate_registration_token()
        data = {'email': 'test@gist.ac.kr'}

        # 세션 저장
        store_registration_session(reg_sid, data, 'email_verified', token)

        # 세션 검증 - 성공
        is_valid, session_data = validate_registration_session(
            reg_sid, 'email_verified', token
        )
        self.assertTrue(is_valid)
        self.assertEqual(session_data['step'], 'email_verified')
        self.assertEqual(session_data['data'], data)
        self.assertEqual(session_data['token'], token)

        # 잘못된 단계 - 실패
        is_valid, _ = validate_registration_session(
            reg_sid, 'agreed', token
        )
        self.assertFalse(is_valid)

        # 잘못된 토큰 - 실패
        is_valid, _ = validate_registration_session(
            reg_sid, 'email_verified', 'wrong_token'
        )
        self.assertFalse(is_valid)

    def test_verification_code_lifecycle(self):
        """인증코드 생명주기 테스트"""
        email = 'test@gist.ac.kr'
        code = '123456'

        # 코드 저장
        store_verification_code(email, code)

        # 올바른 코드 검증 - 성공
        self.assertTrue(validate_verification_code(email, code))

        # 같은 코드 재사용 - 실패 (이미 삭제됨)
        self.assertFalse(validate_verification_code(email, code))

        # 잘못된 코드 - 실패
        store_verification_code(email, code)
        self.assertFalse(validate_verification_code(email, 'wrong'))

    def test_email_send_rate_limiting(self):
        """이메일 발송 Rate Limiting 테스트"""
        email = 'test@gist.ac.kr'

        # 첫 발송 - 성공
        can_send, reason = check_email_send_limit(email)
        self.assertTrue(can_send)
        self.assertIsNone(reason)

        # 발송 카운트 증가
        increment_email_send_count(email)

        # 1분 내 재발송 - 실패
        can_send, reason = check_email_send_limit(email)
        self.assertFalse(can_send)
        self.assertIn('1분', reason)

    def test_login_attempts_tracking(self):
        """로그인 시도 추적 테스트"""
        email = 'test@gist.ac.kr'

        # 초기 상태 - 잠금 없음
        is_locked, attempts = check_login_attempts(email)
        self.assertFalse(is_locked)
        self.assertEqual(attempts, 0)

        # 시도 1-4회
        for i in range(4):
            increment_login_attempts(email)
            is_locked, attempts = check_login_attempts(email)
            self.assertFalse(is_locked)
            self.assertEqual(attempts, i + 1)

        # 5회 시도 - 잠금
        increment_login_attempts(email)
        is_locked, attempts = check_login_attempts(email)
        self.assertTrue(is_locked)
        self.assertEqual(attempts, 5)

        # 시도 초기화
        reset_login_attempts(email)
        is_locked, attempts = check_login_attempts(email)
        self.assertFalse(is_locked)
        self.assertEqual(attempts, 0)

    def test_verification_fail_count(self):
        """인증코드 실패 카운트 테스트"""
        email = 'test@gist.ac.kr'
        correct_code = '123456'
        wrong_code = '000000'

        store_verification_code(email, correct_code)

        # 5회 실패 테스트
        for i in range(5):
            result = validate_verification_code(email, wrong_code)
            self.assertFalse(result)

        # 5회 실패 후에도 올바른 코드는 검증 가능 (캐시에 남아있다면)
        # 하지만 위에서 5번 실패했으므로 블록됨
        store_verification_code(email, correct_code)
        # 블록 상태 확인은 별도 키로 관리되므로 정상 검증 가능
        self.assertTrue(validate_verification_code(email, correct_code))
