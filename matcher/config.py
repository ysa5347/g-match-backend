import os

# Redis 설정
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')
REDIS_DB = int(os.getenv('REDIS_DB', 0))

# Database 설정
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME', 'g_match')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')

# Edge Calculator 설정
EDGE_POLLING_INTERVAL = int(os.getenv('EDGE_POLLING_INTERVAL', 10))  # 초

# Match Scheduler 설정
SCHEDULER_INTERVAL = int(os.getenv('SCHEDULER_INTERVAL', 60))  # 초 (1분)
MATCH_THRESHOLD = float(os.getenv('MATCH_THRESHOLD', 70.0))  # 최소 매칭 점수

# Lock 설정
LOCK_KEY = 'match:gc:lock'
LOCK_EXPIRE = int(os.getenv('LOCK_EXPIRE', 120))  # 초

# Redis Key Patterns
USER_QUEUE_PATTERN = 'match:user-queue:*'
USER_QUEUE_PREFIX = 'match:user-queue:'
EDGE_PREFIX = 'match:edge:'
