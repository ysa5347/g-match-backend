"""
Match Scheduler
- 주기적으로 70점 이상 edge 조회
- priority DESC, score DESC 정렬
- greedy 매칭으로 unique 쌍 추출
- MatchHistory 저장 + user-queue 삭제 + 고아 edge 정리
- 남은 유저 priority 증가 (에이징)
"""

import json
import time
import uuid
import logging
import psycopg2
import redis

from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    SCHEDULER_INTERVAL, MATCH_THRESHOLD, LOCK_KEY, LOCK_EXPIRE,
    USER_QUEUE_PATTERN, USER_QUEUE_PREFIX, EDGE_PREFIX
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('match_scheduler')

# Lua script: 본인 락인지 확인 후 삭제
UNLOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def get_redis_client():
    """Redis 클라이언트 생성"""
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        db=REDIS_DB,
        decode_responses=True
    )


def get_db_connection():
    """PostgreSQL 연결"""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


def acquire_lock(r: redis.Redis, lock_value: str) -> bool:
    """분산 락 획득 (SETNX + EXPIRE)"""
    result = r.set(LOCK_KEY, lock_value, nx=True, ex=LOCK_EXPIRE)
    return result is True


def release_lock(r: redis.Redis, lock_value: str) -> bool:
    """분산 락 해제 (Lua script로 atomic하게)"""
    unlock = r.register_script(UNLOCK_SCRIPT)
    result = unlock(keys=[LOCK_KEY], args=[lock_value])
    return result == 1


def get_all_edges(r: redis.Redis) -> list[dict]:
    """모든 edge 조회"""
    edges = []
    pattern = f"{EDGE_PREFIX}*"
    keys = r.keys(pattern)

    for key in keys:
        data = r.get(key)
        if data:
            edge = json.loads(data)
            edge['_key'] = key
            edges.append(edge)

    return edges


def get_user_priorities(r: redis.Redis) -> dict[int, int]:
    """모든 유저의 priority 조회"""
    priorities = {}
    keys = r.keys(USER_QUEUE_PATTERN)

    for key in keys:
        data = r.get(key)
        if data:
            user_data = json.loads(data)
            priorities[user_data['user_pk']] = user_data.get('priority', 0)

    return priorities


def get_user_queue_data(r: redis.Redis) -> dict[int, dict]:
    """모든 유저 큐 데이터 조회"""
    users = {}
    keys = r.keys(USER_QUEUE_PATTERN)

    for key in keys:
        data = r.get(key)
        if data:
            user_data = json.loads(data)
            users[user_data['user_pk']] = user_data

    return users


def find_matching_pairs(edges: list[dict], priorities: dict[int, int], threshold: float) -> list[dict]:
    """
    Greedy 매칭 알고리즘
    1. threshold 이상인 edge만 필터링
    2. 두 유저의 priority 합 기준 내림차순 정렬
    3. 같은 priority면 score 기준 내림차순
    4. 앞에서부터 unique 쌍 추출
    """
    # threshold 필터링
    valid_edges = [e for e in edges if e['score'] >= threshold]

    if not valid_edges:
        return []

    # priority 합산 + 정렬
    for edge in valid_edges:
        priority_a = priorities.get(edge['user_a_pk'], 0)
        priority_b = priorities.get(edge['user_b_pk'], 0)
        edge['_priority_sum'] = priority_a + priority_b

    # 정렬: priority 합 DESC, score DESC
    valid_edges.sort(key=lambda e: (e['_priority_sum'], e['score']), reverse=True)

    # Greedy 매칭
    matched_users = set()
    matched_pairs = []

    for edge in valid_edges:
        user_a = edge['user_a_pk']
        user_b = edge['user_b_pk']

        if user_a not in matched_users and user_b not in matched_users:
            matched_pairs.append(edge)
            matched_users.add(user_a)
            matched_users.add(user_b)

    return matched_pairs


def save_match_history(conn, user_a_data: dict, user_b_data: dict, score: float):
    """MatchHistory 테이블에 저장"""
    cursor = conn.cursor()

    compatibility_score = json.dumps({
        'score': score,
        'category_scores': {}  # 추후 카테고리별 점수 추가 가능
    })

    cursor.execute("""
        INSERT INTO match_history (
            matched_at, user_a_pk, user_b_pk,
            prop_a_id, prop_b_id, surv_a_id, surv_b_id,
            compatibility_score, a_approval, b_approval, final_match_status
        ) VALUES (
            NOW(), %s, %s, %s, %s, %s, %s, %s, 0, 0, 0
        )
    """, (
        user_a_data['user_pk'],
        user_b_data['user_pk'],
        user_a_data['property_id'],
        user_b_data['property_id'],
        user_a_data['survey_id'],
        user_b_data['survey_id'],
        compatibility_score
    ))

    conn.commit()
    cursor.close()

    logger.info(f"MatchHistory saved: {user_a_data['user_pk']} <-> {user_b_data['user_pk']} (score: {score})")


def remove_user_from_queue(r: redis.Redis, user_pk: int):
    """user-queue에서 유저 제거"""
    key = f"{USER_QUEUE_PREFIX}{user_pk}"
    r.delete(key)
    logger.debug(f"Removed user {user_pk} from queue")


def remove_orphan_edges(r: redis.Redis, removed_users: set[int]):
    """제거된 유저와 관련된 모든 edge 삭제"""
    if not removed_users:
        return

    pattern = f"{EDGE_PREFIX}*"
    keys = r.keys(pattern)
    removed_count = 0

    for key in keys:
        # key format: match:edge:{user_a_pk}:{user_b_pk}
        parts = key.replace(EDGE_PREFIX, '').split(':')
        if len(parts) == 2:
            user_a_pk = int(parts[0])
            user_b_pk = int(parts[1])

            if user_a_pk in removed_users or user_b_pk in removed_users:
                r.delete(key)
                removed_count += 1

    logger.info(f"Removed {removed_count} orphan edges")


def increment_priorities(r: redis.Redis):
    """남은 유저들의 priority 증가 (에이징)"""
    keys = r.keys(USER_QUEUE_PATTERN)
    updated_count = 0

    for key in keys:
        data = r.get(key)
        if data:
            user_data = json.loads(data)
            user_data['priority'] = user_data.get('priority', 0) + 1
            r.set(key, json.dumps(user_data))
            updated_count += 1

    logger.info(f"Incremented priority for {updated_count} users")


def run_matching_cycle(r: redis.Redis, conn):
    """매칭 사이클 실행"""
    # 1. 모든 edge 조회
    edges = get_all_edges(r)
    if not edges:
        logger.debug("No edges found")
        return

    # 2. 유저 데이터 조회
    user_queue_data = get_user_queue_data(r)
    priorities = {pk: data.get('priority', 0) for pk, data in user_queue_data.items()}

    # 3. 매칭 쌍 찾기
    matched_pairs = find_matching_pairs(edges, priorities, MATCH_THRESHOLD)

    if not matched_pairs:
        logger.debug("No matching pairs found")
        # 매칭 없어도 priority 증가
        increment_priorities(r)
        return

    logger.info(f"Found {len(matched_pairs)} matching pair(s)")

    # 4. 매칭 처리
    removed_users = set()

    for edge in matched_pairs:
        user_a_pk = edge['user_a_pk']
        user_b_pk = edge['user_b_pk']

        user_a_data = user_queue_data.get(user_a_pk)
        user_b_data = user_queue_data.get(user_b_pk)

        if not user_a_data or not user_b_data:
            logger.warning(f"Missing user data for edge: {user_a_pk} <-> {user_b_pk}")
            continue

        # MatchHistory 저장
        try:
            save_match_history(conn, user_a_data, user_b_data, edge['score'])
        except Exception as e:
            logger.error(f"Failed to save match history: {e}")
            continue

        # user-queue에서 제거
        remove_user_from_queue(r, user_a_pk)
        remove_user_from_queue(r, user_b_pk)
        removed_users.add(user_a_pk)
        removed_users.add(user_b_pk)

    # 5. 고아 edge 정리
    remove_orphan_edges(r, removed_users)

    # 6. 남은 유저 priority 증가
    increment_priorities(r)


def run_scheduler():
    """메인 스케줄러 루프"""
    r = get_redis_client()
    logger.info("Match Scheduler started")

    while True:
        lock_value = str(uuid.uuid4())

        try:
            # 락 획득 시도
            if not acquire_lock(r, lock_value):
                logger.debug("Failed to acquire lock, another scheduler is running")
                time.sleep(SCHEDULER_INTERVAL)
                continue

            logger.info("Lock acquired, running matching cycle")

            # DB 연결
            conn = get_db_connection()

            try:
                run_matching_cycle(r, conn)
            finally:
                conn.close()

        except redis.RedisError as e:
            logger.error(f"Redis error: {e}")
        except psycopg2.Error as e:
            logger.error(f"Database error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            # 락 해제
            if release_lock(r, lock_value):
                logger.debug("Lock released")
            else:
                logger.warning("Failed to release lock (may have expired)")

        time.sleep(SCHEDULER_INTERVAL)


if __name__ == '__main__':
    run_scheduler()
