"""
Edge Calculator (단일 Pod) -> 추후 병렬 계산 로직 생각필요

[처리 흐름]
1. Redis user-queue polling → 신규 유저 감지 (edge_calculated: false)
2. basic 정보 처리 (Hard Filter: 성별, 흡연 / Soft Score: 나머지 20점)
3. 유사도 계산 + soft score 적용 후 edge 저장
4. 처리 완료 시 edge_calculated: true로 변경
"""

import json
import time
import logging
import redis

from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB,
    EDGE_POLLING_INTERVAL, USER_QUEUE_PATTERN,
    EDGE_PREFIX
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('edge_calculator')

SOFT_SCORE_MAX = 20
SOFT_SCORE_DEDUCT = 5


def get_redis_client():
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        db=REDIS_DB,
        decode_responses=True
    )


# == 1. Redis user-queue polling → 신규 유저 감지 =============
def get_all_queue_users(r: redis.Redis) -> list[dict]:
    users = []
    keys = r.keys(USER_QUEUE_PATTERN)

    for key in keys:
        data = r.get(key)
        if data:
            user_data = json.loads(data)
            user_data['_redis_key'] = key
            users.append(user_data)

    return users


def get_new_users(all_users: list[dict]) -> list[dict]:
    new_users = [u for u in all_users if not u.get('edge_calculated', False)]
    new_users.sort(key=lambda x: x['registered_at'])
    return new_users


def get_calculated_users(all_users: list[dict]) -> list[dict]:
    return [u for u in all_users if u.get('edge_calculated', False)]


# == 2. basic 정보 처리 (Hard Filter + Soft Score) ============
def check_hard_filter(user_a: dict, user_b: dict) -> bool:
    basic_a = user_a['basic']
    basic_b = user_b['basic']

    if basic_a['gender'] != basic_b['gender']:
        return False

    # 흡연 여부가 같아야 매칭
    if basic_a.get('is_smoker', False) != basic_b.get('is_smoker', False):
        return False

    return True


def calculate_basic_score(user_a: dict, user_b: dict) -> float:
    """Soft Score: 20점 만점, 불일치 항목마다 -5점"""
    basic_a = user_a['basic']
    basic_b = user_b['basic']
    score = SOFT_SCORE_MAX

    if basic_a['dorm_building'] != 'A' and basic_a['dorm_building'] != basic_b['dorm_building']:
        score -= SOFT_SCORE_DEDUCT

    if basic_a['stay_period'] != basic_b['stay_period']:
        score -= SOFT_SCORE_DEDUCT

    if _check_preference_mismatch(basic_a, basic_b, 'mate_fridge', 'has_fridge'):
        score -= SOFT_SCORE_DEDUCT

    if _check_preference_mismatch(basic_a, basic_b, 'mate_router', 'has_router'):
        score -= SOFT_SCORE_DEDUCT

    return max(0, score)


def _check_preference_mismatch(basic_a: dict, basic_b: dict, pref_key: str, has_key: str) -> bool:
    # A → B 방향
    mate_pref_a = basic_a.get(pref_key, 0)
    if mate_pref_a == 0 and not basic_b.get(has_key, False):
        return True
    if mate_pref_a == 1 and basic_b.get(has_key, False):
        return True

    # B → A 방향
    mate_pref_b = basic_b.get(pref_key, 0)
    if mate_pref_b == 0 and not basic_a.get(has_key, False):
        return True
    if mate_pref_b == 1 and basic_a.get(has_key, False):
        return True

    return False


# == 3. 유사도 계산 후 edge 저장 ==============================
def calculate_similarity(user_a: dict, user_b: dict) -> float:
    """
    양방향 유사도 계산
    Compatibility(A, B) = 100 × (Score_A→B + Score_B→A) / 2
    """
    score_a_to_b = _calculate_one_direction(user_a, user_b)
    score_b_to_a = _calculate_one_direction(user_b, user_a)

    compatibility = 100 * (score_a_to_b + score_b_to_a) / 2
    return round(compatibility, 2)


def _calculate_one_direction(from_user: dict, to_user: dict) -> float:
    """
    단방향 점수: Score = Σ(w_i × sim_i) / Σw_i
    sim_i = 1 - |scale_from - scale_to| / 4
    """
    weights = from_user['weights']
    survey_from = from_user['survey']
    survey_to = to_user['survey']

    weighted_sum = 0.0
    weight_total = 0.0

    for key, weight in weights.items():
        scale_from = survey_from.get(key)
        scale_to = survey_to.get(key)

        if scale_from is None or scale_to is None:
            continue

        sim_i = 1 - abs(scale_from - scale_to) / 4
        weighted_sum += weight * sim_i
        weight_total += weight

    if weight_total == 0:
        return 0.0

    return weighted_sum / weight_total


def calculate_final_score(user_a: dict, user_b: dict) -> float:
    similarity = calculate_similarity(user_a, user_b)
    basic_score = calculate_basic_score(user_a, user_b)

    final_score = similarity + basic_score
    return round(final_score, 2)


def save_edge(r: redis.Redis, user_a: dict, user_b: dict, score: float):
    """Edge 저장 (key는 pk 오름차순)"""
    pk_a, pk_b = user_a['user_pk'], user_b['user_pk']
    min_pk, max_pk = min(pk_a, pk_b), max(pk_a, pk_b)

    edge_key = f"{EDGE_PREFIX}{min_pk}:{max_pk}"
    edge_data = {
        'user_a_pk': min_pk,
        'user_b_pk': max_pk,
        'score': score,
        'created_at': int(time.time())
    }

    r.set(edge_key, json.dumps(edge_data))
    logger.debug(f"Edge saved: {edge_key} with score {score}")


# == 4. 처리 완료 시 edge_calculated: true로 변경 =============
def mark_as_calculated(r: redis.Redis, user_data: dict):
    """유저의 edge_calculated를 true로 변경 (race condition 방지)"""
    redis_key = user_data['_redis_key']

    # 최신 데이터를 다시 읽어서 edge_calculated만 변경
    current_data = r.get(redis_key)
    if current_data:
        fresh_data = json.loads(current_data)
        fresh_data['edge_calculated'] = True
        r.set(redis_key, json.dumps(fresh_data))


# == 메인 처리 로직 ===========================================
def process_new_user(r: redis.Redis, new_user: dict, calculated_users: list[dict]):
    """신규 유저와 기존 유저들 간의 edge 계산"""
    user_pk = new_user['user_pk']
    edge_count = 0

    for existing in calculated_users:
        if existing['user_pk'] == user_pk:
            continue

        if not check_hard_filter(new_user, existing):
            continue

        score = calculate_final_score(new_user, existing)
        save_edge(r, new_user, existing, score)
        edge_count += 1

    mark_as_calculated(r, new_user)
    logger.info(f"User {user_pk}: created {edge_count} edges")


def run_polling():
    r = get_redis_client()
    logger.info("Edge Calculator started (single pod mode)")

    while True:
        try:
            all_users = get_all_queue_users(r)

            if not all_users:
                logger.debug("No users in queue")
                time.sleep(EDGE_POLLING_INTERVAL)
                continue

            new_users = get_new_users(all_users)

            if not new_users:
                logger.debug("No new users to process")
                time.sleep(EDGE_POLLING_INTERVAL)
                continue

            logger.info(f"Processing {len(new_users)} new user(s)")

            calculated_users = get_calculated_users(all_users)

            for new_user in new_users:
                process_new_user(r, new_user, calculated_users)
                calculated_users.append(new_user)

        except redis.RedisError as e:
            logger.error(f"Redis error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")

        time.sleep(EDGE_POLLING_INTERVAL)


if __name__ == '__main__':
    run_polling()
