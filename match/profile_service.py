class InsightService:
    def __init__(self, raw_surveys, raw_weights):
        self.surveys = raw_surveys
        self.weights = raw_weights

        self.keys = {
            # 1. 생활 리듬
            'time_bed': 'time_1',
            'time_wake': 'time_2',
            'time_alarm': 'time_3',
            'time_night': 'time_4',

            # 2. 공간 관리
            'clean_floor': 'clean_1',
            'clean_trash': 'clean_2',
            'clean_bath': 'clean_3',
            'clean_laundry': 'clean_4',

            # 3. 생활 습관
            'habit_sound': 'habit_1',
            'habit_food': 'habit_2',
            'habit_light': 'habit_3',
            'habit_temp': 'habit_4',

            # 4. 사회성
            'social_talk': 'social_1',
            'social_play': 'social_2',
            'social_invite': 'social_3',
            'social_share': 'social_4',
            'social_alone': 'social_5',

            # 5. 기타 (etc Matching)
            'etc_alcohol': 'etc_1',
            'etc_place': 'etc_2',
        }

    def calculate(self):
        scores = self._calculate_category_scores()
        badges = self._generate_badges()
        return scores, badges

    def _get_val(self, key_name):
        real_key = self.keys.get(key_name, key_name)
        return float(self.surveys.get(real_key))

    def _get_weight(self, key_name):
        real_key = self.keys.get(key_name, key_name)
        return float(self.weights.get(real_key))

    def _calculate_category_scores(self):
        categories = {
            '생활 리듬': ['time_bed', 'time_wake', 'time_alarm', 'time_night'],
            '공간 관리': ['clean_floor', 'clean_trash', 'clean_bath', 'clean_laundry'],
            '생활 습관': ['habit_sound', 'habit_food', 'habit_light', 'habit_temp'],
            '사회성':    ['social_talk', 'social_play', 'social_invite', 'social_share', 'social_alone']
        }

        result = {}
        for cat_name, keys in categories.items():
            total = sum(self._get_val(k) for k in keys)
            avg = total / len(keys)
            result[cat_name] = round(avg, 1)

        return result

    def _generate_badges(self):
        candidates = []

        # 헬퍼 함수: 점수 * 가중치 계산
        def add_candidate(name, score, weight_keys):
            if isinstance(weight_keys, list):
                avg_weight = sum(self._get_weight(k) for k in weight_keys) / len(weight_keys)
            else:
                avg_weight = self._get_weight(weight_keys)

            final_score = score * avg_weight
            candidates.append({
                "name": name,
                "final_score": final_score
            })

        # 1. 생활 리듬 (Time)
        avg_time = (self._get_val('time_bed') + self._get_val('time_wake')) / 2
        add_candidate("얼리버드", 6 - avg_time, ['time_bed', 'time_wake'])
        add_candidate("올빼미", avg_time, ['time_bed', 'time_wake'])
        add_candidate("알람몬스터", self._get_val('time_alarm'), 'time_alarm')
        add_candidate("밤샘러", self._get_val('time_night'), 'time_night')

        # 2. 공간 관리 (Clean)
        avg_clean = (self._get_val('clean_floor') + self._get_val('clean_trash') + self._get_val('clean_bath')) / 3
        add_candidate("청소광", 6 - avg_clean, ['clean_floor', 'clean_trash', 'clean_bath'])
        add_candidate("자연인", avg_clean, ['clean_floor', 'clean_trash', 'clean_bath'])
        add_candidate("빨래요정", self._get_val('clean_laundry'), 'clean_laundry')

        # 3. 생활 습관 (Habit)
        val_sound = self._get_val('habit_sound')
        add_candidate("닌자", 6 - val_sound, 'habit_sound')
        add_candidate("스피커", val_sound, 'habit_sound')
        add_candidate("암막커튼", self._get_val('habit_light'), 'habit_light')
        add_candidate("먹방러", self._get_val('habit_food'), 'habit_food')
        val_temp = self._get_val('habit_temp')
        add_candidate("북극곰", 6 - val_temp, 'habit_temp')
        add_candidate("선인장", val_temp, 'habit_temp')

        # 4. 사회성 (Social)
        val_talk = self._get_val('social_talk')
        val_play = self._get_val('social_play')
        val_alone = self._get_val('social_alone')
        score_homebody = ((6 - val_talk) + (6 - val_play) + val_alone) / 3
        add_candidate("자택경비원", score_homebody, ['social_talk', 'social_play', 'social_alone'])
        score_roommate_lover = (val_talk + val_play + (6 - val_alone)) / 3
        add_candidate("룸메러버", score_roommate_lover, ['social_talk', 'social_play', 'social_alone'])
        add_candidate("핵인싸", self._get_val('social_invite'), 'social_invite')
        add_candidate("기부천사", self._get_val('social_share'), 'social_share')

        # 5. 기타 (Etc)
        val_alcohol = self._get_val('etc_alcohol')
        add_candidate("알콜요정", val_alcohol, 'etc_alcohol')
        add_candidate("논알콜", 6 - val_alcohol, 'etc_alcohol')
        val_place = self._get_val('etc_place')
        add_candidate("도서관지박령", val_place, 'etc_place')
        add_candidate("집공러", 6 - val_place, 'etc_place')

        # 최종 선정: 점수 내림차순 정렬 후 상위 3개
        sorted_candidates = sorted(
            candidates,
            key=lambda x: x['final_score'],
            reverse=True
        )
        top_3 = sorted_candidates[:3]

        result = {}
        for i, item in enumerate(top_3):
            result[f"badge{i+1}"] = item['name']

        return result