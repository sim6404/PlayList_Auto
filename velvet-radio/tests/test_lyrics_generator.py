"""
Velvet Radio — lyrics_generator 단위 테스트
"""
import pytest

from src.phase1_concept.lyrics_generator import _validate_and_fix
from src.common.models import Mood, Track, VocalPersona


def make_track(order=1) -> Track:
    return Track(
        order=order, title="Test",
        mood=Mood.DREAMY, sub_genre="soft jazz pop",
        bpm=85, key="G major", vocal=VocalPersona.VR_F1,
    )


class TestValidateAndFix:
    def test_adds_verse_if_missing(self):
        content = "[Chorus]\nHello world"
        fixed = _validate_and_fix(content, make_track())
        assert "[Verse" in fixed

    def test_adds_chorus_if_missing(self):
        content = "[Verse]\nHello world"
        fixed = _validate_and_fix(content, make_track())
        assert "[Chorus]" in fixed

    def test_trims_to_3000_chars(self):
        # 긴 가사 자동 축소
        long_bridge = "[Bridge]\n" + ("Line of text\n" * 300)
        content = f"[Verse]\nFirst verse\n[Chorus]\nChorus here\n{long_bridge}"
        fixed = _validate_and_fix(content, make_track())
        assert len(fixed) <= 3000

    def test_valid_content_unchanged(self):
        content = "[Verse]\nMorning light\n[Chorus]\nStay a while\n[Bridge]\nQuiet moment\n[Outro]\nGoodnight"
        fixed = _validate_and_fix(content, make_track())
        assert "[Verse" in fixed
        assert "[Chorus]" in fixed

    def test_returns_string(self):
        content = "[Verse]\nA\n[Chorus]\nB"
        result = _validate_and_fix(content, make_track())
        assert isinstance(result, str)
        assert len(result) > 0
