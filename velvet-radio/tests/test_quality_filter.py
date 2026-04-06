"""
Velvet Radio — quality_filter 단위 테스트
"""
import pytest
from src.phase2_music.quality_filter import score_audio


class TestScoreAudio:
    """품질 점수 산출 로직 테스트"""

    def test_perfect_audio(self):
        analysis = {
            "duration": 210.0,     # 3분 30초 (이상적)
            "lufs": -12.0,         # 이상적 LUFS
            "silence_ratio": 0.01,  # 1% 무음
            "clipping": False,
            "spectral_bandwidth": 2500.0,
        }
        score = score_audio(analysis)
        assert score >= 0.9, f"이상적 음원 점수가 너무 낮음: {score}"

    def test_short_audio_penalty(self):
        analysis = {
            "duration": 60.0,      # 너무 짧음
            "lufs": -12.0,
            "silence_ratio": 0.01,
            "clipping": False,
            "spectral_bandwidth": 2500.0,
        }
        score = score_audio(analysis)
        assert score < 0.9, "짧은 음원에 패널티가 없음"

    def test_clipping_penalty(self):
        analysis = {
            "duration": 210.0,
            "lufs": -12.0,
            "silence_ratio": 0.01,
            "clipping": True,      # 클리핑 감지
            "spectral_bandwidth": 2500.0,
        }
        score = score_audio(analysis)
        no_clip = score_audio({**analysis, "clipping": False})
        assert score < no_clip, "클리핑 패널티가 없음"

    def test_high_silence_penalty(self):
        analysis = {
            "duration": 210.0,
            "lufs": -12.0,
            "silence_ratio": 0.5,  # 50% 무음
            "clipping": False,
            "spectral_bandwidth": 2500.0,
        }
        score = score_audio(analysis)
        assert score < 0.7, "무음 비율 패널티가 없음"

    def test_score_range(self):
        """점수는 항상 0~1 범위여야 함"""
        cases = [
            {"duration": 0, "lufs": -30, "silence_ratio": 1.0, "clipping": True, "spectral_bandwidth": 0},
            {"duration": 210, "lufs": -12, "silence_ratio": 0.01, "clipping": False, "spectral_bandwidth": 3000},
        ]
        for analysis in cases:
            score = score_audio(analysis)
            assert 0.0 <= score <= 1.0, f"범위 초과: {score}"

    def test_lufs_out_of_range(self):
        very_loud = {
            "duration": 210.0,
            "lufs": -6.0,          # 너무 큰 볼륨
            "silence_ratio": 0.01,
            "clipping": False,
            "spectral_bandwidth": 2500.0,
        }
        ideal = {**very_loud, "lufs": -12.0}
        assert score_audio(ideal) > score_audio(very_loud)
