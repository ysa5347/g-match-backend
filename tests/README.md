# G-Match Backend 테스트 가이드

## 개요

이 디렉토리에는 G-Match Backend의 테스트 코드가 포함되어 있습니다.

---

## 테스트 구조

```
tests/
├── __init__.py                           # 테스트 패키지 초기화
├── test_models.py                        # 모델 테스트
├── test_redis_utils.py                   # Redis 유틸리티 테스트
├── test_api.py                           # API 엔드포인트 테스트
├── G-Match_API_Tests.postman_collection.json  # Postman 테스트 컬렉션
└── README.md                             # 이 파일
```

---

## Django 테스트

### 1. 테스트 준비

#### Redis 서버 실행
테스트 실행 전 Redis 서버가 실행 중이어야 합니다.

```bash
# Redis 설치 (macOS)
brew install redis

# Redis 실행
redis-server

# 또는 백그라운드 실행
brew services start redis
```

#### 테스트 데이터베이스 설정
Django는 자동으로 테스트용 데이터베이스를 생성합니다.
- MySQL: `test_g_match` (자동 생성)
- 테스트 완료 후 자동 삭제

### 2. 전체 테스트 실행

```bash
# 가상환경 활성화
source .venv/bin/activate

# 모든 테스트 실행
python manage.py test tests

# 특정 테스트 파일 실행
python manage.py test tests.test_models
python manage.py test tests.test_redis_utils
python manage.py test tests.test_api

# 특정 테스트 클래스 실행
python manage.py test tests.test_models.CustomUserModelTest

# 특정 테스트 메서드 실행
python manage.py test tests.test_models.CustomUserModelTest.test_create_user
```

### 3. 테스트 옵션

```bash
# Verbose 모드 (상세 출력)
python manage.py test tests --verbosity=2

# 실패한 테스트만 재실행
python manage.py test tests --failfast

# 커버리지와 함께 실행 (coverage 설치 필요)
coverage run --source='.' manage.py test tests
coverage report
coverage html  # HTML 리포트 생성
```

### 4. 커버리지 분석 (선택)

```bash
# coverage 설치
pip install coverage

# 커버리지 측정
coverage run --source='account' manage.py test tests
coverage report

# HTML 리포트 생성
coverage html
open htmlcov/index.html
```

---

## 테스트 파일 설명

### test_models.py

**CustomUser 모델 테스트**
- `test_create_user`: 일반 사용자 생성
- `test_create_superuser`: 슈퍼유저 생성
- `test_user_str_representation`: 문자열 표현
- `test_is_gist_email_property`: GIST 이메일 검증
- `test_default_privacy_settings`: 기본 공개 범위
- `test_user_with_full_profile`: 전체 프로필 생성

**Agreement 모델 테스트**
- `test_create_agreement`: 약관 동의 생성
- `test_agreement_str_representation`: 문자열 표현
- `test_one_to_one_relationship`: 1:1 관계 검증
- `test_cascade_delete`: CASCADE 삭제 확인

### test_redis_utils.py

**Redis 유틸리티 함수 테스트**
- `test_generate_reg_sid`: reg_sid 생성
- `test_generate_registration_token`: 토큰 생성
- `test_generate_verification_code`: 인증코드 생성
- `test_registration_session_lifecycle`: 회원가입 세션 생명주기
- `test_verification_code_lifecycle`: 인증코드 생명주기
- `test_email_send_rate_limiting`: 이메일 발송 제한
- `test_login_attempts_tracking`: 로그인 시도 추적

### test_api.py

**Account API 엔드포인트 테스트**

**회원가입 플로우**
- `test_send_verification_code_success`: 인증코드 발송 성공
- `test_send_verification_code_invalid_email`: 잘못된 이메일
- `test_verify_code_success`: 인증코드 검증 성공
- `test_verify_code_invalid`: 잘못된 인증코드
- `test_registration_agree_get`: 약관 조회
- `test_registration_agree_post`: 약관 동의
- `test_registration_basic_info`: 회원가입 완료

**로그인/로그아웃**
- `test_login_success`: 로그인 성공
- `test_login_invalid_credentials`: 잘못된 로그인 정보
- `test_login_nonexistent_user`: 존재하지 않는 사용자
- `test_logout`: 로그아웃

**사용자 정보**
- `test_user_info_get_authenticated`: 인증된 사용자 정보 조회
- `test_user_info_get_unauthenticated`: 비인증 사용자 정보 조회
- `test_user_info_update`: 사용자 정보 수정

---

## Postman 테스트

### 1. Postman 설치

- [Postman 다운로드](https://www.postman.com/downloads/)

### 2. 컬렉션 Import

1. Postman 실행
2. **Import** 버튼 클릭
3. `tests/G-Match_API_Tests.postman_collection.json` 선택
4. Import 완료

### 3. 환경 변수 설정

컬렉션에 이미 다음 변수가 설정되어 있습니다:
- `base_url`: `http://localhost:8000`
- `api_version`: `v1alpha1`
- `test_email`: `test@gist.ac.kr`
- `registration_token`: (자동 설정)
- `verification_code`: (수동 입력 필요)

### 4. 테스트 실행 순서

#### 📋 회원가입 플로우 (순서대로 실행)

1. **1-1. Send Verification Code**
   - 이메일 인증코드 발송
   - ⚠️ 이메일 서버가 설정되지 않았다면 500 에러 (정상)

2. **1-2. Verify Code**
   - **중요**: 실제 환경에서는 이메일로 받은 코드 입력
   - 테스트 환경: Redis에서 코드 확인 필요
   ```bash
   # Redis CLI에서 코드 확인
   redis-cli
   > KEYS verification_code:*
   > GET verification_code:test@gist.ac.kr
   ```
   - 받은 코드를 `verification_code` 변수에 설정

3. **1-3. Get Agreement Terms**
   - 약관 내용 조회

4. **1-4. Agree to Terms**
   - 약관 동의

5. **1-5. Complete Registration**
   - 회원가입 완료

#### 🔐 로그인/로그아웃

6. **2-1. Login**
   - 로그인 성공

7. **2-2. Login with Wrong Password**
   - 잘못된 비밀번호 (401 에러 예상)

8. **2-3. Logout**
   - 로그아웃

#### 👤 사용자 정보

9. **3-1. Get User Info (Authenticated)**
   - 로그인 후 정보 조회

10. **3-2. Get User Info (Unauthenticated)**
    - 로그인 없이 정보 조회 (401 에러 예상)

11. **3-3. Update User Info**
    - 사용자 정보 수정

#### ✅ 유효성 검사

12. **4-1. Invalid Email (Non-GIST)**
    - GIST 이메일이 아닌 경우 (400 에러 예상)

13. **4-2. Missing Required Fields**
    - 필수 필드 누락 (400 에러 예상)

### 5. 자동 테스트 실행

Postman의 Collection Runner 사용:

1. 컬렉션 우클릭 → **Run Collection**
2. 실행할 폴더 선택
3. **Run** 클릭
4. 결과 확인

### 6. Newman (CLI) 실행

```bash
# Newman 설치
npm install -g newman

# 컬렉션 실행
newman run tests/G-Match_API_Tests.postman_collection.json

# HTML 리포트 생성
newman run tests/G-Match_API_Tests.postman_collection.json \
  --reporters cli,html \
  --reporter-html-export newman-report.html
```

---

## 테스트 시나리오

### 시나리오 1: 신규 사용자 회원가입

```bash
# 1. 인증코드 발송
curl -X POST http://localhost:8000/api/v1alpha1/account/auth/registration/email/verification-code \
  -H "Content-Type: application/json" \
  -d '{"email": "newuser@gist.ac.kr"}'

# 2. Redis에서 인증코드 확인
redis-cli
> GET verification_code:newuser@gist.ac.kr
"123456"

# 3. 인증코드 검증
curl -X POST http://localhost:8000/api/v1alpha1/account/auth/registration/email/verification-code/verify \
  -H "Content-Type: application/json" \
  -d '{"email": "newuser@gist.ac.kr", "code": "123456"}' \
  -c cookies.txt

# 4. 약관 동의
curl -X POST http://localhost:8000/api/v1alpha1/account/auth/registration/agree \
  -H "Content-Type: application/json" \
  -H "X-Registration-Token: <token_from_step_3>" \
  -d '{"terms_of_service": true, "privacy_policy": true}' \
  -b cookies.txt -c cookies.txt

# 5. 회원가입 완료
curl -X POST http://localhost:8000/api/v1alpha1/account/auth/registration/basic-info \
  -H "Content-Type: application/json" \
  -H "X-Registration-Token: <token_from_step_4>" \
  -d '{
    "password": "testpass123!",
    "password_confirm": "testpass123!",
    "name": "신규유저",
    "student_id": "20241234"
  }' \
  -b cookies.txt
```

### 시나리오 2: 로그인 및 정보 수정

```bash
# 1. 로그인
curl -X POST http://localhost:8000/api/v1alpha1/account/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "newuser@gist.ac.kr", "password": "testpass123!"}' \
  -c cookies.txt

# 2. 사용자 정보 조회
curl -X GET http://localhost:8000/api/v1alpha1/account/info \
  -b cookies.txt

# 3. 사용자 정보 수정
curl -X PUT http://localhost:8000/api/v1alpha1/account/info \
  -H "Content-Type: application/json" \
  -d '{"name": "수정된이름", "is_age_public": false}' \
  -b cookies.txt

# 4. 로그아웃
curl -X POST http://localhost:8000/api/v1alpha1/account/auth/logout \
  -b cookies.txt
```

---

## 테스트 팁

### 1. 테스트 데이터 초기화

```bash
# 테스트 후 데이터베이스 초기화
python manage.py flush

# 또는 마이그레이션 재실행
python manage.py migrate --run-syncdb
```

### 2. Redis 캐시 초기화

```bash
# Redis CLI
redis-cli
> FLUSHDB

# 또는 특정 키 삭제
> DEL verification_code:test@gist.ac.kr
```

### 3. 테스트용 슈퍼유저 생성

```bash
python manage.py createsuperuser
# Email: admin@gist.ac.kr
# Name: 관리자
# Password: admin123!
```

### 4. Django Admin 확인

테스트 데이터 확인:
1. http://localhost:8000/admin 접속
2. 슈퍼유저로 로그인
3. Users, Agreements 확인

---

## 문제 해결

### Redis 연결 오류

```
ConnectionRefusedError: [Errno 61] Connection refused
```

**해결**: Redis 서버 실행 확인
```bash
redis-server
```

### 이메일 발송 실패

```
SMTPException: Email send failed
```

**해결**:
1. `.env` 파일에 이메일 서버 설정 확인
2. 테스트 환경에서는 500 에러가 정상 (이메일 설정 없음)
3. 또는 Django Console Backend 사용:
   ```python
   # settings.py (개발 환경)
   EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
   ```

### 테스트 데이터베이스 권한 오류

```
Access denied for user 'django-server'@'localhost' to database 'test_g_match'
```

**해결**: MySQL 사용자에게 테스트 DB 권한 부여
```sql
GRANT ALL PRIVILEGES ON test_g_match.* TO 'django-server'@'localhost';
FLUSH PRIVILEGES;
```

---

## CI/CD 통합 (향후)

### GitHub Actions 예시

```yaml
name: Django Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      mysql:
        image: mysql:8.0
        env:
          MYSQL_ROOT_PASSWORD: root
          MYSQL_DATABASE: g_match
        ports:
          - 3306:3306

      redis:
        image: redis:7.2
        ports:
          - 6379:6379

    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.13'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt

      - name: Run tests
        run: |
          python manage.py test tests
        env:
          DB_HOST: 127.0.0.1
          DB_PORT: 3306
          REDIS_HOST: 127.0.0.1
          REDIS_PORT: 6379
```

---

## 참고 자료

### Django Testing
- https://docs.djangoproject.com/en/5.2/topics/testing/

### DRF Testing
- https://www.django-rest-framework.org/api-guide/testing/

### Postman
- https://learning.postman.com/docs/writing-scripts/test-scripts/

### Newman
- https://learning.postman.com/docs/running-collections/using-newman-cli/
