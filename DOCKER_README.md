# Docker 배포 가이드

## 개요

G-Match Backend를 Docker로 컨테이너화하여 배포하는 가이드입니다.

---

## 파일 구조

```
g-match-backend/
├── Dockerfile              # 프로덕션 이미지 (tests/ 제외)
├── Dockerfile.dev          # 개발 이미지 (tests/ 포함)
├── docker-compose.yml      # 프로덕션 구성
├── docker-compose.dev.yml  # 개발 구성
├── .dockerignore           # Docker 빌드 제외 파일
├── nginx.conf              # Nginx 설정
└── requirements.txt        # Python 의존성
```

---

## 핵심 차이점

### 프로덕션 빌드 (Dockerfile)
- ✅ **tests/ 디렉토리 제외** (.dockerignore)
- ✅ Multi-stage build (이미지 크기 최소화)
- ✅ Non-root user 사용
- ✅ Gunicorn WSGI 서버
- ✅ Health check 포함

### 개발 빌드 (Dockerfile.dev)
- ✅ **tests/ 디렉토리 포함**
- ✅ Volume mount로 hot-reload
- ✅ Django runserver
- ✅ 개발 도구 포함 (coverage, ipdb 등)

---

## 빠른 시작

### 1. 프로덕션 환경

```bash
# 1. 환경변수 설정
cp .env.example .env
# .env 파일 편집

# 2. 빌드 및 실행
docker-compose up -d

# 3. 마이그레이션
docker-compose exec web python manage.py migrate

# 4. 정적 파일 수집
docker-compose exec web python manage.py collectstatic --noinput

# 5. 슈퍼유저 생성 (선택)
docker-compose exec web python manage.py createsuperuser

# 6. 로그 확인
docker-compose logs -f web
```

**접속**: http://localhost:8000

### 2. 개발 환경

```bash
# 1. 개발 환경 실행
docker-compose -f docker-compose.dev.yml up -d

# 2. 마이그레이션
docker-compose -f docker-compose.dev.yml exec web python manage.py migrate

# 3. 테스트 실행 (tests/ 포함됨)
docker-compose -f docker-compose.dev.yml exec web python manage.py test tests

# 4. 로그 확인
docker-compose -f docker-compose.dev.yml logs -f web
```

**접속**: http://localhost:8000

---

## .dockerignore 설명

프로덕션 빌드 시 다음 파일/디렉토리가 제외됩니다:

```
# Testing
tests/              # ✅ 테스트 코드 제외
.coverage
htmlcov/

# Documentation
CLAUDE/             # ✅ 문서 제외
*.md

# Development
.venv/              # 가상환경
.git/               # Git 저장소
.env                # 환경변수 (런타임에 주입)

# IDE
.vscode/
.idea/
```

**결과**: 프로덕션 이미지 크기 최소화 및 빌드 속도 향상

---

## 이미지 크기 비교

### 프로덕션 (Dockerfile)
```bash
docker images g-match-backend
# REPOSITORY          TAG       SIZE
# g-match-backend     latest    ~200MB  (tests/ 제외)
```

### 개발 (Dockerfile.dev)
```bash
docker images g-match-backend-dev
# REPOSITORY              TAG       SIZE
# g-match-backend-dev     latest    ~250MB  (tests/ 포함)
```

---

## Docker Compose 서비스

### 공통 서비스

#### web (Django)
- 포트: 8000
- 의존성: db, redis
- Health check: `/api/v1alpha1/account/`

#### db (MySQL 8.0)
- 포트: 3306
- 볼륨: `mysql-data` (영구 저장)
- Health check: `mysqladmin ping`

#### redis (Redis 7.2)
- 포트: 6379
- 볼륨: `redis-data` (영구 저장)
- Health check: `redis-cli ping`

### 프로덕션 전용

#### nginx (선택)
- 포트: 80, 443
- 정적 파일 서빙
- 리버스 프록시
- 활성화: `docker-compose --profile production up`

---

## 환경변수 (.env)

```bash
# Django
SECRET_KEY=your-super-secret-key-here
DEBUG=False

# Database
DB_NAME=g_match
DB_USER=django-server
DB_PASSWORD=secure-password
DB_ROOT_PASSWORD=root-password
DB_HOST=db
DB_PORT=3306

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Email
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=noreply@g-match.com
```

---

## 주요 명령어

### 빌드

```bash
# 프로덕션 이미지 빌드
docker-compose build

# 개발 이미지 빌드
docker-compose -f docker-compose.dev.yml build

# 캐시 없이 빌드 (강제 재빌드)
docker-compose build --no-cache
```

### 실행

```bash
# 프로덕션 환경 시작
docker-compose up -d

# 개발 환경 시작
docker-compose -f docker-compose.dev.yml up -d

# 특정 서비스만 시작
docker-compose up -d web redis

# 로그 확인
docker-compose logs -f
docker-compose logs -f web
```

### 관리

```bash
# 컨테이너 접속
docker-compose exec web bash
docker-compose exec db mysql -u root -p

# Django 명령어 실행
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
docker-compose exec web python manage.py collectstatic

# 테스트 실행 (개발 환경)
docker-compose -f docker-compose.dev.yml exec web python manage.py test tests
```

### 중지 및 정리

```bash
# 중지
docker-compose down

# 중지 + 볼륨 삭제 (데이터 삭제 주의!)
docker-compose down -v

# 중지 + 이미지 삭제
docker-compose down --rmi all

# 완전 정리
docker-compose down -v --rmi all
docker system prune -a
```

---

## 테스트 실행

### 개발 환경에서 테스트

```bash
# 개발 컨테이너에서 테스트 (tests/ 포함)
docker-compose -f docker-compose.dev.yml exec web python manage.py test tests

# 특정 테스트만
docker-compose -f docker-compose.dev.yml exec web python manage.py test tests.test_models

# 커버리지 측정
docker-compose -f docker-compose.dev.yml exec web coverage run --source='account' manage.py test tests
docker-compose -f docker-compose.dev.yml exec web coverage report
```

### CI/CD에서 테스트

```bash
# 임시 컨테이너로 테스트 실행
docker-compose -f docker-compose.dev.yml run --rm web python manage.py test tests

# 테스트 완료 후 자동 삭제
docker-compose -f docker-compose.dev.yml run --rm web sh -c "
  python manage.py migrate &&
  python manage.py test tests
"
```

---

## 프로덕션 배포

### 1. AWS ECR 배포

```bash
# 1. ECR 로그인
aws ecr get-login-password --region ap-northeast-2 | \
  docker login --username AWS --password-stdin <account-id>.dkr.ecr.ap-northeast-2.amazonaws.com

# 2. 이미지 빌드
docker build -t g-match-backend .

# 3. 태그
docker tag g-match-backend:latest \
  <account-id>.dkr.ecr.ap-northeast-2.amazonaws.com/g-match-backend:latest

# 4. 푸시
docker push <account-id>.dkr.ecr.ap-northeast-2.amazonaws.com/g-match-backend:latest
```

### 2. Docker Hub 배포

```bash
# 1. 로그인
docker login

# 2. 빌드 및 태그
docker build -t yourusername/g-match-backend:latest .

# 3. 푸시
docker push yourusername/g-match-backend:latest
```

### 3. 서버에서 실행

```bash
# 1. 이미지 pull
docker pull yourusername/g-match-backend:latest

# 2. 실행
docker-compose up -d

# 3. 마이그레이션
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py collectstatic --noinput
```

---

## Health Check

### Web 서비스

```bash
# 컨테이너 상태 확인
docker-compose ps

# Health check 로그
docker inspect --format='{{json .State.Health}}' g-match-web | jq

# 수동 health check
curl http://localhost:8000/api/v1alpha1/account/
```

### MySQL

```bash
# 연결 확인
docker-compose exec db mysqladmin ping -h localhost
docker-compose exec db mysql -u django-server -p -e "SELECT 1"
```

### Redis

```bash
# 연결 확인
docker-compose exec redis redis-cli ping
docker-compose exec redis redis-cli INFO
```

---

## 트러블슈팅

### 1. 이미지 빌드 실패

**문제**: `tests/` 디렉토리 관련 오류

**해결**:
```bash
# .dockerignore 확인
cat .dockerignore | grep tests

# 캐시 없이 재빌드
docker-compose build --no-cache
```

### 2. 데이터베이스 연결 오류

**문제**: `Can't connect to MySQL server`

**해결**:
```bash
# DB 컨테이너 상태 확인
docker-compose ps db

# DB 로그 확인
docker-compose logs db

# DB health check
docker-compose exec db mysqladmin ping
```

### 3. Redis 연결 오류

**문제**: `Error connecting to Redis`

**해결**:
```bash
# Redis 컨테이너 확인
docker-compose ps redis

# Redis 연결 테스트
docker-compose exec redis redis-cli ping
```

### 4. 볼륨 권한 문제

**문제**: `Permission denied` 오류

**해결**:
```bash
# 볼륨 삭제 후 재생성
docker-compose down -v
docker-compose up -d

# 또는 권한 변경
docker-compose exec web chown -R appuser:appuser /app
```

---

## 모니터링

### 로그 확인

```bash
# 실시간 로그
docker-compose logs -f

# 특정 서비스 로그
docker-compose logs -f web

# 마지막 100줄
docker-compose logs --tail=100 web
```

### 리소스 사용량

```bash
# 컨테이너 리소스 사용량
docker stats

# 특정 컨테이너
docker stats g-match-web
```

### 디스크 사용량

```bash
# Docker 전체 디스크 사용량
docker system df

# 볼륨 목록
docker volume ls

# 사용하지 않는 리소스 정리
docker system prune -a --volumes
```

---

## 백업 및 복구

### 데이터베이스 백업

```bash
# 백업
docker-compose exec db mysqldump -u root -p g_match > backup_$(date +%Y%m%d).sql

# 복구
docker-compose exec -T db mysql -u root -p g_match < backup_20260122.sql
```

### 볼륨 백업

```bash
# MySQL 볼륨 백업
docker run --rm -v g-match-backend_mysql-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/mysql-backup.tar.gz -C /data .

# Redis 볼륨 백업
docker run --rm -v g-match-backend_redis-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/redis-backup.tar.gz -C /data .
```

---

## 성능 최적화

### 이미지 크기 최적화

```bash
# Multi-stage build 사용 (이미 적용됨)
# .dockerignore로 불필요한 파일 제외
# Alpine 기반 이미지 사용 고려

# 이미지 크기 확인
docker images g-match-backend
```

### 컨테이너 리소스 제한

```yaml
# docker-compose.yml
services:
  web:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 512M
        reservations:
          cpus: '0.5'
          memory: 256M
```

---

## 참고 자료

- **Docker 공식 문서**: https://docs.docker.com/
- **Docker Compose**: https://docs.docker.com/compose/
- **Django Docker**: https://docs.djangoproject.com/en/5.2/howto/deployment/
- **Multi-stage builds**: https://docs.docker.com/build/building/multi-stage/

---

## 요약

✅ **프로덕션 빌드**: `tests/` 제외, 최소 이미지 크기
✅ **개발 빌드**: `tests/` 포함, 테스트 가능
✅ **완전히 독립적**: tests/ 없이 런타임 정상 동작
✅ **Multi-stage build**: 빌드 의존성과 런타임 분리

프로덕션 환경에서는 `tests/` 디렉토리가 **완전히 제외**되어도 아무 문제 없습니다!
