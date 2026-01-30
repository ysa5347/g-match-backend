"""
Edge Calculator (단일 Pod)
- Redis user-queue를 polling하여 신규 유저 감지 (edge_calculated: false)
- basic 정보로 양방향 필터링
- 유사도 계산 후 edge 저장
- 처리 완료 시 edge_calculated: true로 변경
"""

import json
import time
import logging
import redis

from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB,
    EDGE_POLLING_INTERVAL, USER_QUEUE_PATTERN, USER_QUEUE_PREFIX,
    EDGE_PREFIX
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('edge_calculator')


def get_redis_client():
    """Redis 클라이언트 생성"""
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
        db=REDIS_DB,
        decode_responses=True
    )


def get_all_queue_users(r: redis.Redis) -> list[dict]:
    """user-queue의 모든 유저 데이터 조회"""
    users = []
    keys = r.keys(USER_QUEUE_PATTERN)

    for key in keys:
        data = r.get(key)
        if data:
            user_data = json.loads(data)
            # ==================================================
            user_data['_redis_key'] = key  # 나중에 업데이트용
            users.append(user_data)

    return users


def get_new_users(all_users: list[dict]) -> list[dict]:
    new_users = [u for u in all_users if not u.get('edge_calculated', False)]
    new_users.sort(key=lambda x: x['registered_at'])

    return new_users


def get_calculated_users(all_users: list[dict]) -> list[dict]:
    return [u for u in all_users if u.get('edge_calculated', False)]


def check_basic_filter(user_a: dict, user_b: dict) -> bool:
    """
    양방향 basic 필터링
    - 상관없음(0)은 통과
    - 선호(1)는 상대가 해당 조건 충족해야 통과
    - 비선호(2)는 상대가 해당 조건 미충족해야 통과

    Returns:
        True: 양방향 모두 통과 (매칭 후보)
        False: 한쪽이라도 필터링됨
    """
    basic_a = user_a['basic']
    basic_b = user_b['basic']

    if basic_a['gender'] != basic_b['gender']:
        return False

    if basic_a['dorm_building'] != basic_b['dorm_building']:
        return False

    if basic_a['stay_period'] != basic_b['stay_period']:
        return False

    if not _check_one_direction(basic_a, basic_b):
        return False

    if not _check_one_direction(basic_b, basic_a):
        return False

    return True


def _check_one_direction(checker: dict, target: dict) -> bool:
    """
    단방향 선호도 체크 (checker가 target을 평가)

    mate_xxx 값:
    - 0: 상관없음 → 항상 통과
    - 1: 선호 → target이 True여야 통과
    - 2: 비선호 → target이 False여야 통과
    """
    mate_smoker = checker.get('mate_smoker', 0)
    if mate_smoker == 1 and not target['is_smoker']:
        return False
    if mate_smoker == 2 and target['is_smoker']:
        return False

    mate_fridge = checker.get('mate_fridge', 0)
    if mate_fridge == 1 and not target['has_fridge']:
        return False
    if mate_fridge == 2 and target['has_fridge']:
        return False

    mate_router = checker.get('mate_router', 0)
    if mate_router == 1 and not target['has_router']:
        return False
    if mate_router == 2 and target['has_router']:
        return False

    return True


def calculate_similarity(user_a: dict, user_b: dict) -> float:
    """
    양방향 유사도 계산

    Compatibility(A, B) = 100 × (Score_A→B + Score_B→A) / 2
    Score_X→Y = Σ(w_X,i × sim_i) / Σw_X,i
    sim_i = 1 - |scale_A - scale_B| / 4
    """
    score_a_to_b = _calculate_one_direction(user_a, user_b)
    score_b_to_a = _calculate_one_direction(user_b, user_a)

    compatibility = 100 * (score_a_to_b + score_b_to_a) / 2
    return round(compatibility, 2)


def _calculate_one_direction(from_user: dict, to_user: dict) -> float:
    """
    단방향 점수 계산
    Score = Σ(w_i × sim_i) / Σw_i
    """
    weights = from_user['weights']
    survey_from = from_user['survey']
    survey_to = to_user['survey']

    weighted_sum = 0.0
    weight_total = 0.0

    for key, weight in weights.items():
        scale_from = survey_from.get(key, 3)  # 기본값 3 (중간)
        scale_to = survey_to.get(key, 3)

        # sim_i = 1 - |scale_A - scale_B| / 4
        sim_i = 1 - abs(scale_from - scale_to) / 4

        weighted_sum += weight * sim_i
        weight_total += weight

    if weight_total == 0:
        return 0.0

    return weighted_sum / weight_total


def create_edge_key(user_pk_a: int, user_pk_b: int) -> str:
    """Edge key 생성 (pk 오름차순)"""
    min_pk = min(user_pk_a, user_pk_b)
    max_pk = max(user_pk_a, user_pk_b)
    return f"{EDGE_PREFIX}{min_pk}:{max_pk}"


def save_edge(r: redis.Redis, user_a: dict, user_b: dict, score: float):
    """Edge 저장"""
    edge_key = create_edge_key(user_a['user_pk'], user_b['user_pk'])

    edge_data = {
        'user_a_pk': min(user_a['user_pk'], user_b['user_pk']),
        'user_b_pk': max(user_a['user_pk'], user_b['user_pk']),
        'score': score,
        'created_at': int(time.time())
    }

    r.set(edge_key, json.dumps(edge_data))
    logger.debug(f"Edge saved: {edge_key} with score {score}")


def mark_as_calculated(r: redis.Redis, user_data: dict):
    """유저의 edge_calculated를 true로 변경"""
    user_data['edge_calculated'] = True
    redis_key = user_data['_redis_key']

    # _redis_key는 저장하지 않음
    save_data = {k: v for k, v in user_data.items() if k != '_redis_key'}
    r.set(redis_key, json.dumps(save_data))


def process_new_user(r: redis.Redis, new_user: dict, existing_users: list[dict]):
    """
    신규 유저에 대해 기존 유저들과의 edge 계산
    """
    user_pk = new_user['user_pk']
    edge_count = 0

    for existing in existing_users:
        # 자기 자신은 제외
        if existing['user_pk'] == user_pk:
            continue

        # 양방향 필터링
        if not check_basic_filter(new_user, existing):
            continue

        # 유사도 계산
        score = calculate_similarity(new_user, existing)

        # Edge 저장
        save_edge(r, new_user, existing, score)
        edge_count += 1

    # 처리 완료 마킹
    mark_as_calculated(r, new_user)
    logger.info(f"User {user_pk}: created {edge_count} edges")


def run_polling():
    """메인 polling 루프"""
    r = get_redis_client()
    logger.info("Edge Calculator started (single pod mode)")

    while True:
        try:
            # 모든 대기열 유저 조회
            all_users = get_all_queue_users(r)

            if not all_users:
                logger.debug("No users in queue")
                time.sleep(EDGE_POLLING_INTERVAL)
                continue

            # 신규 유저 필터링 (registered_at 오름차순)
            new_users = get_new_users(all_users)

            if not new_users:
                logger.debug("No new users to process")
                time.sleep(EDGE_POLLING_INTERVAL)
                continue

            logger.info(f"Processing {len(new_users)} new user(s)")

            # 기존 유저 (이미 edge 계산 완료된 유저들)
            calculated_users = get_calculated_users(all_users)

            # 신규 유저 순차 처리
            for new_user in new_users:
                # 기존 유저 + 이번 라운드에서 이미 처리된 신규 유저들
                process_new_user(r, new_user, calculated_users)

                # 처리된 유저를 기존 유저 목록에 추가 (다음 신규 유저 처리 시 포함)
                calculated_users.append(new_user)

        except redis.RedisError as e:
            logger.error(f"Redis error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")

        time.sleep(EDGE_POLLING_INTERVAL)


if __name__ == '__main__':
    run_polling()
