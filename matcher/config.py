import os

# Redis 설정
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')
REDIS_DB = int(os.getenv('REDIS_DB', 0))

# Database 설정 (MySQL)
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 3306))
DB_NAME = os.getenv('DB_NAME', 'g_match')
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')

# AWS SES 설정
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '')
AWS_SES_REGION = os.getenv('AWS_SES_REGION', 'ap-northeast-2')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@g-match.org')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://www.g-match.org')

# 이메일 발송 활성화 여부 (개발 환경에서 비활성화)
EMAIL_ENABLED = os.getenv('EMAIL_ENABLED', 'true').lower() in ('true', '1', 'yes')

# Edge Calculator 설정
EDGE_POLLING_INTERVAL = int(os.getenv('EDGE_POLLING_INTERVAL', 10))  # 초

# Match Scheduler 설정
SCHEDULER_INTERVAL = int(os.getenv('SCHEDULER_INTERVAL', 300))  # 초 (5분)
MATCH_THRESHOLD = float(os.getenv('MATCH_THRESHOLD', 80.0))  # 최소 매칭 점수

# Lock 설정
LOCK_KEY = 'match:gc:lock'
LOCK_EXPIRE = int(os.getenv('LOCK_EXPIRE', 120))  # 초

# Redis Key Patterns
USER_QUEUE_PATTERN = 'match:user-queue:*'
USER_QUEUE_PREFIX = 'match:user-queue:'
EDGE_PATTERN = 'match:edge:*'
EDGE_PREFIX = 'match:edge:'
