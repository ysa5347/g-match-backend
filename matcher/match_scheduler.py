"""
Match Scheduler (단일 Pod)

[처리 흐름]
1. 락 획득
2. edge 조회 + 유저 데이터 조회
3. 고아 edge 정리 (user-queue에 없는 유저의 edge 삭제)
4. 유효 edge 필터링 (threshold 이상만)
5. priority 합 DESC, score DESC 정렬 → greedy 매칭
6. MatchHistory 저장 + user-queue 삭제
7. 만료 유저 제거 (24시간 초과 → match_status=9)
8. 남은 유저 priority 증가 (에이징)
9. 락 해제
"""

import json
import time
import uuid
import logging
from datetime import datetime, timezone, timedelta
import pymysql
import redis

from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    SCHEDULER_INTERVAL, MATCH_THRESHOLD, LOCK_KEY, LOCK_EXPIRE,
    USER_QUEUE_PATTERN, USER_QUEUE_PREFIX, EDGE_PATTERN
)
from email_notifier import get_notifier

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


# == 1. 락 획득/해제 ==========================================
def acquire_lock(r: redis.Redis, lock_value: str) -> bool:
    result = r.set(LOCK_KEY, lock_value, nx=True, ex=LOCK_EXPIRE)
    return result is True


def release_lock(r: redis.Redis, lock_value: str) -> bool:
    unlock = r.register_script(UNLOCK_SCRIPT)
    result = unlock(keys=[LOCK_KEY], args=[lock_value])
    return result == 1


# == 2. Edge 및 유저 데이터 조회 ==============================
MGET_BATCH_SIZE = 500

def get_all_edges_and_users(r: redis.Redis) -> tuple[list[dict], dict[str, dict]]:
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
                    users[user_data['user_id']] = user_data

    return edges, users


# == 3. 고아 edge 정리 =======================================
def cleanup_orphan_edges(r: redis.Redis, edges: list[dict], valid_user_ids: set[str]) -> list[dict]:
    """
    user-queue에 없는 유저가 포함된 edge 삭제
    Returns: 유효한 edge만 필터링된 리스트
    """
    valid_edges = []
    removed_count = 0

    for edge in edges:
        id_a, id_b = edge['user_a_id'], edge['user_b_id']

        if id_a in valid_user_ids and id_b in valid_user_ids:
            valid_edges.append(edge)
        else:
            r.delete(edge['_key'])
            removed_count += 1

    if removed_count > 0:
        logger.info(f"Cleaned up {removed_count} orphan edges")

    return valid_edges


# == 4. Greedy 매칭 알고리즘 =================================
def find_matching_pairs(edges: list[dict], users: dict[str, dict], threshold: float) -> list[dict]:
    """
    1. 유효 edge 필터링: score >= threshold
    2. priority 합 DESC, score DESC 정렬 (높은 priority 유저 우선 매칭)
    3. greedy로 unique 쌍 추출
    """
    valid_edges = []

    for e in edges:
        user_a = users.get(e['user_a_id'], {})
        user_b = users.get(e['user_b_id'], {})
        priority_a = user_a.get('priority', 0)
        priority_b = user_b.get('priority', 0)

        if e['score'] >= threshold:
            e['_priority_sum'] = priority_a + priority_b
            valid_edges.append(e)

    if not valid_edges:
        return []

    valid_edges.sort(key=lambda e: (e['_priority_sum'], e['score']), reverse=True)

    matched_users = set()
    matched_pairs = []

    for edge in valid_edges:
        a, b = edge['user_a_id'], edge['user_b_id']
        if a not in matched_users and b not in matched_users:
            matched_pairs.append(edge)
            matched_users.add(a)
            matched_users.add(b)

    return matched_pairs


# == Helper: UUID 문자열을 MySQL UUIDField 형식(하이픈 없는 32자)으로 변환 ==
def normalize_uuid(uuid_str: str) -> str:
    """
    Django의 UUIDField는 MySQL에서 CHAR(32)로 저장됨 (하이픈 없음).
    Redis에서 읽은 UUID 문자열(36자, 하이픈 포함)을 32자로 변환.
    """
    return uuid.UUID(uuid_str).hex


# == 5. MatchHistory 저장 + user-queue 삭제 + 이메일 알림 ==================
def process_matched_pairs(r: redis.Redis, conn, matched_pairs: list[dict], users: dict[str, dict]) -> set[str]:
    """매칭된 쌍 처리: DB 저장 + user-queue 삭제 + 이메일 알림 (edge 정리는 다음 사이클에서)"""
    removed_users = set()
    cursor = conn.cursor()
    notifier = get_notifier()

    for edge in matched_pairs:
        user_a_id, user_b_id = edge['user_a_id'], edge['user_b_id']
        user_a, user_b = users.get(user_a_id), users.get(user_b_id)

        if not user_a or not user_b:
            logger.warning(f"Missing user data: {user_a_id} <-> {user_b_id}")
            continue

        try:
            # UUID를 MySQL UUIDField 형식(하이픈 없는 32자)으로 변환
            uuid_a = normalize_uuid(user_a['user_id'])
            uuid_b = normalize_uuid(user_b['user_id'])

            cursor.execute("""
                INSERT INTO match_history (
                    matched_at, user_a_id, user_b_id,
                    prop_a_id, prop_b_id, surv_a_id, surv_b_id,
                    compatibility_score, a_approval, b_approval, final_match_status
                ) VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, 0, 0, 0)
            """, (
                uuid_a, uuid_b,
                user_a['property_id'], user_b['property_id'],
                user_a['survey_id'], user_b['survey_id'],
                edge['score']
            ))
            # match_properties의 match_status를 2(MATCHED)로 변경
            cursor.execute(
                "UPDATE match_properties SET match_status = 2 WHERE property_id IN (%s, %s)",
                (user_a['property_id'], user_b['property_id'])
            )
            logger.info(f"MatchHistory saved: {user_a_id} <-> {user_b_id} (score: {edge['score']})")

            # 매칭 완료 이메일 알림 발송
            _send_match_notifications(conn, cursor, user_a, user_b, edge['score'], notifier)

        except Exception as e:
            logger.error(f"Failed to save match history: {e}")
            continue

        r.delete(f"{USER_QUEUE_PREFIX}{user_a_id}")
        r.delete(f"{USER_QUEUE_PREFIX}{user_b_id}")
        removed_users.add(user_a_id)
        removed_users.add(user_b_id)

    conn.commit()
    cursor.close()

    return removed_users


def _send_match_notifications(conn, cursor, user_a: dict, user_b: dict, score: float, notifier):
    """매칭된 양쪽 사용자에게 이메일 알림 발송"""
    try:
        # UUID를 MySQL 형식으로 변환
        uuid_a = normalize_uuid(user_a['user_id'])
        uuid_b = normalize_uuid(user_b['user_id'])

        # 사용자 정보 조회 (이메일, 닉네임)
        cursor.execute(
            "SELECT user_id, email, nickname, name FROM account_customuser WHERE user_id IN (%s, %s)",
            (uuid_a, uuid_b)
        )
        # DB에서 반환되는 user_id도 32자 hex이므로 그대로 사용
        user_info = {row[0]: {'email': row[1], 'nickname': row[2], 'name': row[3]} for row in cursor.fetchall()}

        info_a = user_info.get(uuid_a, {})
        info_b = user_info.get(uuid_b, {})

        # User A에게 알림
        if info_a.get('email'):
            notifier.notify_matched(
                user_email=info_a['email'],
                user_name=info_a.get('nickname') or info_a.get('name') or '사용자',
                partner_nickname=info_b.get('nickname'),
                compatibility_score=score
            )

        # User B에게 알림
        if info_b.get('email'):
            notifier.notify_matched(
                user_email=info_b['email'],
                user_name=info_b.get('nickname') or info_b.get('name') or '사용자',
                partner_nickname=info_a.get('nickname'),
                compatibility_score=score
            )

    except Exception as e:
        logger.error(f"Failed to send match notifications: {e}")


# == 6. 만료 유저 제거 (24시간 초과) ==========================
EXPIRE_HOURS = 24

def remove_expired_users(r: redis.Redis, conn):
    """registered_at으로부터 24시간 초과된 유저를 user-queue에서 제거하고 match_status=9로 변경"""
    now = datetime.now(timezone.utc)
    expired_users = []  # (user_id, property_id) 튜플 리스트
    removed_count = 0

    for key in r.keys(USER_QUEUE_PATTERN):
        data = r.get(key)
        if not data:
            continue

        user_data = json.loads(data)
        registered_at_str = user_data.get('registered_at')
        if not registered_at_str:
            continue

        registered_at = datetime.fromisoformat(registered_at_str)
        if now - registered_at > timedelta(hours=EXPIRE_HOURS):
            user_id = user_data.get('user_id')
            property_id = user_data.get('property_id')
            if user_id and property_id:
                expired_users.append((user_id, property_id))
            r.delete(key)
            removed_count += 1
            logger.info(f"Expired user removed: {user_id} (registered_at: {registered_at_str})")

    if expired_users:
        expired_property_ids = [p[1] for p in expired_users]
        # UUID를 MySQL 형식(32자 hex)으로 변환
        expired_user_ids = [normalize_uuid(u[0]) for u in expired_users]

        cursor = conn.cursor()
        try:
            placeholders = ','.join(['%s'] * len(expired_property_ids))
            cursor.execute(
                f"UPDATE match_properties SET match_status = 9 WHERE property_id IN ({placeholders})",
                expired_property_ids
            )
            conn.commit()
            logger.info(f"Updated match_status=9 for {cursor.rowcount} expired properties")

            # 만료된 유저들에게 이메일 알림 발송
            _send_expired_notifications(conn, cursor, expired_user_ids)

        except Exception as e:
            logger.error(f"Failed to update match_status for expired users: {e}")
            conn.rollback()
        finally:
            cursor.close()

    if removed_count > 0:
        logger.info(f"Removed {removed_count} expired user(s) from queue")


def _send_expired_notifications(conn, cursor, expired_user_ids: list):
    """만료된 사용자들에게 이메일 알림 발송"""
    notifier = get_notifier()
    if not notifier.enabled:
        return

    try:
        placeholders = ','.join(['%s'] * len(expired_user_ids))
        cursor.execute(
            f"SELECT user_id, email, nickname, name FROM account_customuser WHERE user_id IN ({placeholders})",
            expired_user_ids
        )
        users = cursor.fetchall()

        for user in users:
            user_id, email, nickname, name = user
            if email:
                user_name = nickname or name or '사용자'
                notifier.notify_expired(
                    user_email=email,
                    user_name=user_name
                )
                logger.info(f"Sent expired notification to {email}")

    except Exception as e:
        logger.error(f"Failed to send expired notifications: {e}")


# == 7. 남은 유저 aging ======================================
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


# == 메인 스케줄러 루프 ======================================
def run_matching_cycle(r: redis.Redis, conn):
    edges, users = get_all_edges_and_users(r)

    if not users:
        logger.debug("No users in queue")
        return

    valid_user_ids = set(users.keys())

    if edges:
        edges = cleanup_orphan_edges(r, edges, valid_user_ids)

    if not edges:
        logger.debug("No valid edges found")
        remove_expired_users(r, conn)
        increment_priorities(r)
        return

    matched_pairs = find_matching_pairs(edges, users, MATCH_THRESHOLD)
    if not matched_pairs:
        logger.debug("No matching pairs found")
        remove_expired_users(r, conn)
        increment_priorities(r)
        return

    logger.info(f"Found {len(matched_pairs)} matching pair(s)")

    process_matched_pairs(r, conn, matched_pairs, users)

    remove_expired_users(r, conn)
    increment_priorities(r)


def run_scheduler():
    r = get_redis_client()
    logger.info("Match Scheduler started (single pod mode)")

    while True:
        cycle_start = time.time()
        lock_value = str(uuid.uuid4())

        try:
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
            if release_lock(r, lock_value):
                logger.debug("Lock released")
            else:
                logger.warning("Failed to release lock (may have expired)")

        # 처리 시간을 고려하여 sleep
        elapsed = time.time() - cycle_start
        sleep_time = max(0, SCHEDULER_INTERVAL - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            logger.warning(f"Cycle took {elapsed:.1f}s, longer than interval {SCHEDULER_INTERVAL}s")


if __name__ == '__main__':
    run_scheduler()
