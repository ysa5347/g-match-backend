"""
Match Scheduler (단일 Pod)

[처리 흐름]
1. 락 획득
2. 70점 이상 edge 조회 + 유저 데이터 조회
3. priority 합 DESC, score DESC 정렬 → greedy 매칭
4. MatchHistory 저장 + user-queue 삭제 + 고아 edge 정리
5. 남은 유저 priority 증가 (에이징)
6. 락 해제
"""

import json
import time
import uuid
import logging
import pymysql
import redis

from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    SCHEDULER_INTERVAL, MATCH_THRESHOLD, LOCK_KEY, LOCK_EXPIRE,
    USER_QUEUE_PATTERN, USER_QUEUE_PREFIX, EDGE_PATTERN, EDGE_PREFIX
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
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        db=REDIS_DB,
        decode_responses=True
    )


def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        charset='utf8mb4'
    )


# ============================================================
# 1. 락 획득/해제
# ============================================================

def acquire_lock(r: redis.Redis, lock_value: str) -> bool:
    result = r.set(LOCK_KEY, lock_value, nx=True, ex=LOCK_EXPIRE)
    return result is True


def release_lock(r: redis.Redis, lock_value: str) -> bool:
    unlock = r.register_script(UNLOCK_SCRIPT)
    result = unlock(keys=[LOCK_KEY], args=[lock_value])
    return result == 1


# ============================================================
# 2. Edge 및 유저 데이터 조회
# ============================================================

def get_all_edges_and_users(r: redis.Redis) -> tuple[list[dict], dict[int, dict]]:
    """모든 edge와 user-queue 데이터 조회"""
    # Edge 조회
    edges = []
    for key in r.keys(EDGE_PATTERN):
        data = r.get(key)
        if data:
            edge = json.loads(data)
            edge['_key'] = key
            edges.append(edge)

    # User 조회
    users = {}
    for key in r.keys(USER_QUEUE_PATTERN):
        data = r.get(key)
        if data:
            user_data = json.loads(data)
            user_data['_redis_key'] = key
            users[user_data['user_pk']] = user_data

    return edges, users


# ============================================================
# 3. Greedy 매칭 알고리즘
# ============================================================

def find_matching_pairs(edges: list[dict], users: dict[int, dict], threshold: float) -> list[dict]:
    """
    1. threshold 이상 edge 필터링
    2. priority 합 DESC, score DESC 정렬
    3. greedy로 unique 쌍 추출
    """
    valid_edges = [e for e in edges if e['score'] >= threshold]
    if not valid_edges:
        return []

    # priority 합산
    for edge in valid_edges:
        user_a = users.get(edge['user_a_pk'], {})
        user_b = users.get(edge['user_b_pk'], {})
        edge['_priority_sum'] = user_a.get('priority', 0) + user_b.get('priority', 0)

    # 정렬: priority 합 DESC, score DESC
    valid_edges.sort(key=lambda e: (e['_priority_sum'], e['score']), reverse=True)

    # Greedy 매칭
    matched_users = set()
    matched_pairs = []

    for edge in valid_edges:
        a, b = edge['user_a_pk'], edge['user_b_pk']
        if a not in matched_users and b not in matched_users:
            matched_pairs.append(edge)
            matched_users.add(a)
            matched_users.add(b)

    return matched_pairs


# ============================================================
# 4. MatchHistory 저장 + user-queue 삭제 + 고아 edge 정리
# ============================================================

def process_matched_pairs(r: redis.Redis, conn, matched_pairs: list[dict], users: dict[int, dict]) -> set[int]:
    """매칭된 쌍 처리: DB 저장 + Redis 정리"""
    removed_users = set()
    cursor = conn.cursor()

    for edge in matched_pairs:
        user_a_pk, user_b_pk = edge['user_a_pk'], edge['user_b_pk']
        user_a, user_b = users.get(user_a_pk), users.get(user_b_pk)

        if not user_a or not user_b:
            logger.warning(f"Missing user data: {user_a_pk} <-> {user_b_pk}")
            continue

        # MatchHistory INSERT
        try:
            cursor.execute("""
                INSERT INTO match_history (
                    matched_at, user_a_pk, user_b_pk,
                    prop_a_id, prop_b_id, surv_a_id, surv_b_id,
                    compatibility_score, a_approval, b_approval, final_match_status
                ) VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, 0, 0, 0)
            """, (
                user_a['user_pk'], user_b['user_pk'],
                user_a['property_id'], user_b['property_id'],
                user_a['survey_id'], user_b['survey_id'],
                edge['score']
            ))
            logger.info(f"MatchHistory saved: {user_a_pk} <-> {user_b_pk} (score: {edge['score']})")
        except Exception as e:
            logger.error(f"Failed to save match history: {e}")
            continue

        # user-queue 삭제
        r.delete(f"{USER_QUEUE_PREFIX}{user_a_pk}")
        r.delete(f"{USER_QUEUE_PREFIX}{user_b_pk}")
        removed_users.add(user_a_pk)
        removed_users.add(user_b_pk)

    conn.commit()
    cursor.close()

    # 고아 edge 정리
    if removed_users:
        removed_count = 0
        for key in r.keys(EDGE_PATTERN):
            parts = key.replace(EDGE_PREFIX, '').split(':')
            if len(parts) == 2:
                pk_a, pk_b = int(parts[0]), int(parts[1])
                if pk_a in removed_users or pk_b in removed_users:
                    r.delete(key)
                    removed_count += 1
        logger.info(f"Removed {removed_count} orphan edges")

    return removed_users


# ============================================================
# 5. 남은 유저 priority 증가 (에이징)
# ============================================================

def increment_priorities(r: redis.Redis):
    updated = 0
    for key in r.keys(USER_QUEUE_PATTERN):
        data = r.get(key)
        if data:
            user_data = json.loads(data)
            user_data['priority'] = user_data.get('priority', 0) + 1
            r.set(key, json.dumps(user_data))
            updated += 1
    logger.info(f"Incremented priority for {updated} users")


# ============================================================
# 메인 스케줄러 루프
# ============================================================

def run_matching_cycle(r: redis.Redis, conn):
    # 2. 데이터 조회
    edges, users = get_all_edges_and_users(r)
    if not edges:
        logger.debug("No edges found")
        return

    # 3. 매칭 쌍 찾기
    matched_pairs = find_matching_pairs(edges, users, MATCH_THRESHOLD)
    if not matched_pairs:
        logger.debug("No matching pairs found")
        increment_priorities(r)
        return

    logger.info(f"Found {len(matched_pairs)} matching pair(s)")

    # 4. 매칭 처리
    process_matched_pairs(r, conn, matched_pairs, users)

    # 5. 에이징
    increment_priorities(r)


def run_scheduler():
    r = get_redis_client()
    logger.info("Match Scheduler started (single pod mode)")

    while True:
        lock_value = str(uuid.uuid4())

        try:
            # 1. 락 획득
            if not acquire_lock(r, lock_value):
                logger.debug("Failed to acquire lock")
                time.sleep(SCHEDULER_INTERVAL)
                continue

            logger.info("Lock acquired, running matching cycle")
            conn = get_db_connection()

            try:
                run_matching_cycle(r, conn)
            finally:
                conn.close()

        except redis.RedisError as e:
            logger.error(f"Redis error: {e}")
        except pymysql.Error as e:
            logger.error(f"Database error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            # 6. 락 해제
            if release_lock(r, lock_value):
                logger.debug("Lock released")
            else:
                logger.warning("Failed to release lock (may have expired)")

        time.sleep(SCHEDULER_INTERVAL)


if __name__ == '__main__':
    run_scheduler()
