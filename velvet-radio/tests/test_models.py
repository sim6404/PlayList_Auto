"""
Velvet Radio — 공통 모델 단위 테스트
"""
import pytest
from pydantic import ValidationError

from src.common.models import (
    Lyrics,
    Mood,
    Playlist,
    QualityReport,
    SEOMetadata,
    StylePrompt,
    SunoPayload,
    Track,
    VocalPersona,
)


class TestTrack:
    def test_valid_track(self):
        t = Track(order=1, title="Morning Glow", mood=Mood.COZY,
                  sub_genre="soft jazz pop", bpm=85, key="G major",
                  vocal=VocalPersona.VR_F1, hook_priority=True)
        assert t.order == 1
        assert t.hook_priority is True

    def test_bpm_range(self):
        with pytest.raises(ValidationError):
            Track(order=1, title="X", mood=Mood.COZY, sub_genre="jazz",
                  bpm=150, key="C", vocal=VocalPersona.VR_F1)

    def test_order_range(self):
        with pytest.raises(ValidationError):
            Track(order=25, title="X", mood=Mood.COZY, sub_genre="jazz",
                  bpm=85, key="C", vocal=VocalPersona.VR_F1)


class TestLyrics:
    def test_valid_lyrics(self):
        content = "[Verse]\nMorning light through the window\n[Chorus]\nStay a while longer"
        lyr = Lyrics(track_order=1, content=content)
        assert lyr.char_count == len(content)

    def test_missing_verse_tag(self):
        with pytest.raises(ValidationError, match="메타태그 누락"):
            Lyrics(track_order=1, content="[Chorus]\nHello world")

    def test_missing_chorus_tag(self):
        with pytest.raises(ValidationError, match="메타태그 누락"):
            Lyrics(track_order=1, content="[Verse]\nHello world")

    def test_char_limit(self):
        long_content = "[Verse]\n" + "A" * 3000 + "\n[Chorus]\nToo long"
        with pytest.raises(ValidationError, match="3,000자"):
            Lyrics(track_order=1, content=long_content)


class TestStylePrompt:
    def test_valid_prompt(self):
        sp = StylePrompt(track_order=1, prompt="cozy, jazz pop, piano", negative="no EDM")
        assert sp.track_order == 1

    def test_prompt_too_long(self):
        with pytest.raises(ValidationError, match="1,000자"):
            StylePrompt(track_order=1, prompt="x" * 1001, negative="")


class TestPlaylist:
    def _make_track(self, i):
        return Track(order=i, title=f"Track {i}", mood=Mood.COZY,
                     sub_genre="soft jazz pop", bpm=85, key="G major",
                     vocal=VocalPersona.VR_F1)

    def test_valid_playlist(self):
        tracks = [self._make_track(i) for i in range(1, 21)]
        p = Playlist(id="20260406_test", theme="Test Theme",
                     concept="A test concept", tracks=tracks)
        assert len(p.tracks) == 20

    def test_too_few_tracks(self):
        tracks = [self._make_track(i) for i in range(1, 10)]
        with pytest.raises(ValidationError, match="범위"):
            Playlist(id="20260406_test", theme="Test", concept="X", tracks=tracks)


class TestSEOMetadata:
    def test_title_length(self):
        with pytest.raises(ValidationError, match="100자"):
            SEOMetadata(
                playlist_id="test",
                title_en="X" * 101,
                description_en="desc",
            )

    def test_valid_seo(self):
        seo = SEOMetadata(
            playlist_id="20260406_test",
            title_en="Easy Listening Pop | Velvet Radio",
            description_en="♫ 60 minutes of pure relaxation. Study, work, unwind.",
            tags=["easy listening", "chill", "study music"],
            hashtags=["#VelvetRadio", "#ChillMusic"],
        )
        assert seo.title_en
        assert len(seo.tags) == 3
