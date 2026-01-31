"""
Match Scheduler (단일 Pod)

[처리 흐름]
1. 락 획득
2. edge 조회 + 유저 데이터 조회
3. 고아 edge 정리 (user-queue에 없는 유저의 edge 삭제)
4. 유효 edge 필터링 (threshold 이상 OR priority 10 이상 유저 포함)
5. priority 합 DESC, score DESC 정렬 → greedy 매칭
6. MatchHistory 저장 + user-queue 삭제
7. 남은 유저 priority 증가 (에이징)
8. 락 해제
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

MGET_BATCH_SIZE = 500

def get_all_edges_and_users(r: redis.Redis) -> tuple[list[dict], dict[int, dict]]:
    """모든 edge와 user-queue 데이터 조회 (MGET 배치 처리)"""
    # Edge 조회 - MGET으로 배치 처리
    edges = []
    edge_keys = r.keys(EDGE_PATTERN)

    if edge_keys:
        for i in range(0, len(edge_keys), MGET_BATCH_SIZE):
            batch_keys = edge_keys[i:i + MGET_BATCH_SIZE]
            batch_values = r.mget(batch_keys)
            for key, data in zip(batch_keys, batch_values):
                if data:
                    edge = json.loads(data)
                    edge['_key'] = key
                    edges.append(edge)

    # User 조회 - MGET으로 배치 처리
    users = {}
    user_keys = r.keys(USER_QUEUE_PATTERN)

    if user_keys:
        for i in range(0, len(user_keys), MGET_BATCH_SIZE):
            batch_keys = user_keys[i:i + MGET_BATCH_SIZE]
            batch_values = r.mget(batch_keys)
            for key, data in zip(batch_keys, batch_values):
                if data:
                    user_data = json.loads(data)
                    user_data['_redis_key'] = key
                    users[user_data['user_pk']] = user_data

    return edges, users


# ============================================================
# 3. 고아 edge 정리
# ============================================================

def cleanup_orphan_edges(r: redis.Redis, edges: list[dict], valid_user_pks: set[int]) -> list[dict]:
    """
    user-queue에 없는 유저가 포함된 edge 삭제
    Returns: 유효한 edge만 필터링된 리스트
    """
    valid_edges = []
    removed_count = 0

    for edge in edges:
        pk_a, pk_b = edge['user_a_pk'], edge['user_b_pk']

        # 두 유저 모두 user-queue에 있어야 유효
        if pk_a in valid_user_pks and pk_b in valid_user_pks:
            valid_edges.append(edge)
        else:
            # 고아 edge 삭제
            r.delete(edge['_key'])
            removed_count += 1

    if removed_count > 0:
        logger.info(f"Cleaned up {removed_count} orphan edges")

    return valid_edges


# ============================================================
# 4. Greedy 매칭 알고리즘
# ============================================================

# priority 임계값: 이 이상이면 threshold 미달이어도 매칭 시도
PRIORITY_THRESHOLD = 10

def find_matching_pairs(edges: list[dict], users: dict[int, dict], threshold: float) -> list[dict]:
    """
    1. 유효 edge 필터링:
       - score >= threshold OR
       - 두 유저 중 하나라도 priority >= PRIORITY_THRESHOLD
    2. priority 합 DESC, score DESC 정렬
    3. greedy로 unique 쌍 추출
    """
    valid_edges = []

    for e in edges:
        user_a = users.get(e['user_a_pk'], {})
        user_b = users.get(e['user_b_pk'], {})
        priority_a = user_a.get('priority', 0)
        priority_b = user_b.get('priority', 0)

        # threshold 이상이거나, 한쪽이라도 priority 10 이상이면 유효
        if e['score'] >= threshold or priority_a >= PRIORITY_THRESHOLD or priority_b >= PRIORITY_THRESHOLD:
            e['_priority_sum'] = priority_a + priority_b
            valid_edges.append(e)

    if not valid_edges:
        return []

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
# 5. MatchHistory 저장 + user-queue 삭제
# ============================================================

def process_matched_pairs(r: redis.Redis, conn, matched_pairs: list[dict], users: dict[int, dict]) -> set[int]:
    """매칭된 쌍 처리: DB 저장 + user-queue 삭제 (edge 정리는 다음 사이클에서)"""
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

    # 고아 edge 정리는 cleanup_orphan_edges()에서 다음 사이클 시작 시 처리
    return removed_users


# ============================================================
# 6. 남은 유저 priority 증가 (에이징)
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

    if not users:
        logger.debug("No users in queue")
        return

    valid_user_pks = set(users.keys())

    # 3. 고아 edge 정리 (매 사이클마다 실행)
    if edges:
        edges = cleanup_orphan_edges(r, edges, valid_user_pks)

    if not edges:
        logger.debug("No valid edges found")
        increment_priorities(r)
        return

    # 4-5. 매칭 쌍 찾기
    matched_pairs = find_matching_pairs(edges, users, MATCH_THRESHOLD)
    if not matched_pairs:
        logger.debug("No matching pairs found")
        increment_priorities(r)
        return

    logger.info(f"Found {len(matched_pairs)} matching pair(s)")

    # 6. 매칭 처리
    process_matched_pairs(r, conn, matched_pairs, users)

    # 7. 에이징
    increment_priorities(r)


def run_scheduler():
    r = get_redis_client()
    logger.info("Match Scheduler started (single pod mode)")

    while True:
        cycle_start = time.time()
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
            # 8. 락 해제
            if release_lock(r, lock_value):
                logger.debug("Lock released")
            else:
                logger.warning("Failed to release lock (may have expired)")

        # 처리 시간을 고려하여 sleep (정확히 SCHEDULER_INTERVAL 간격 유지)
        elapsed = time.time() - cycle_start
        sleep_time = max(0, SCHEDULER_INTERVAL - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            logger.warning(f"Cycle took {elapsed:.1f}s, longer than interval {SCHEDULER_INTERVAL}s")


if __name__ == '__main__':
    run_scheduler()
