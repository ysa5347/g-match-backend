"""
Edge Calculator (단일 Pod)

[처리 흐름]
1. Redis user-queue polling → 신규 유저 감지 (edge_calculated: false)
2. basic 정보 처리 (Hard Filter: 성별 / Soft Score: 나머지)
3. 유사도 계산 + 감점 적용 후 edge 저장
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

# 불일치 시 감점
PENALTY_SCORE = 5


def get_redis_client():
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        db=REDIS_DB,
        decode_responses=True
    )


# ============================================================
# 1. Redis user-queue polling → 신규 유저 감지
# ============================================================

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


# ============================================================
# 2. basic 정보 처리 (Hard Filter + Soft Score)
# ============================================================

def check_hard_filter(user_a: dict, user_b: dict) -> bool:
    """Hard Filter: 성별만 체크"""
    basic_a = user_a['basic']
    basic_b = user_b['basic']

    if basic_a['gender'] != basic_b['gender']:
        return False

    return True


def calculate_basic_penalty(user_a: dict, user_b: dict) -> float:
    """
    Soft Score: 불일치 항목마다 -5점 (양방향)
    - 기숙사동 불일치
    - 입주기간 불일치
    - 선호도 불만족 (흡연/냉장고/공유기)
    """
    basic_a = user_a['basic']
    basic_b = user_b['basic']
    penalty = 0

    if basic_a['dorm_building'] != basic_b['dorm_building']:
        penalty += PENALTY_SCORE

    if basic_a['stay_period'] != basic_b['stay_period']:
        penalty += PENALTY_SCORE

    # 양방향 선호도 감점
    penalty += _calculate_preference_penalty(basic_a, basic_b)
    penalty += _calculate_preference_penalty(basic_b, basic_a)

    return penalty


def _calculate_preference_penalty(checker: dict, target: dict) -> float:
    """
    단방향 선호도 감점 계산
    - 선호(1)인데 상대가 미충족 → -5점
    - 비선호(2)인데 상대가 충족 → -5점
    """
    penalty = 0

    mate_smoker = checker.get('mate_smoker', 0)
    if mate_smoker == 1 and not target['is_smoker']:
        penalty += PENALTY_SCORE
    if mate_smoker == 2 and target['is_smoker']:
        penalty += PENALTY_SCORE

    mate_fridge = checker.get('mate_fridge', 0)
    if mate_fridge == 1 and not target['has_fridge']:
        penalty += PENALTY_SCORE
    if mate_fridge == 2 and target['has_fridge']:
        penalty += PENALTY_SCORE

    mate_router = checker.get('mate_router', 0)
    if mate_router == 1 and not target['has_router']:
        penalty += PENALTY_SCORE
    if mate_router == 2 and target['has_router']:
        penalty += PENALTY_SCORE

    return penalty


# ============================================================
# 3. 유사도 계산 후 edge 저장
# ============================================================

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
    """유사도 점수 - 감점 = 최종 점수"""
    similarity = calculate_similarity(user_a, user_b)
    penalty = calculate_basic_penalty(user_a, user_b)
    final_score = max(0, similarity - penalty)  # 0점 미만 방지
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


# ============================================================
# 4. 처리 완료 시 edge_calculated: true로 변경
# ============================================================

def mark_as_calculated(r: redis.Redis, user_data: dict):
    """유저의 edge_calculated를 true로 변경 (race condition 방지)"""
    redis_key = user_data['_redis_key']

    # 최신 데이터를 다시 읽어서 edge_calculated만 변경
    current_data = r.get(redis_key)
    if current_data:
        fresh_data = json.loads(current_data)
        fresh_data['edge_calculated'] = True
        r.set(redis_key, json.dumps(fresh_data))


# ============================================================
# 메인 처리 로직
# ============================================================

def process_new_user(r: redis.Redis, new_user: dict, calculated_users: list[dict]):
    """신규 유저와 기존 유저들 간의 edge 계산"""
    user_pk = new_user['user_pk']
    edge_count = 0

    for existing in calculated_users:
        if existing['user_pk'] == user_pk:
            continue

        # 2. Hard Filter (성별만)
        if not check_hard_filter(new_user, existing):
            continue

        # 3. 유사도 계산 + 감점 적용
        score = calculate_final_score(new_user, existing)
        save_edge(r, new_user, existing, score)
        edge_count += 1

    # 4. 처리 완료 마킹
    mark_as_calculated(r, new_user)
    logger.info(f"User {user_pk}: created {edge_count} edges")


def run_polling():
    """메인 polling 루프"""
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
