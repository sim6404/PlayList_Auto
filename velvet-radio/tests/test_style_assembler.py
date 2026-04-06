"""
Velvet Radio — style_assembler 단위 테스트
"""
import pytest

from src.common.models import Mood, Track, VocalPersona
from src.phase1_concept.style_assembler import (
    NEGATIVE_TAGS,
    assemble_style_prompt,
    build_suno_payload,
)


def make_track(mood=Mood.COZY, sub_genre="soft jazz pop", bpm=85,
               hook=False, order=1) -> Track:
    return Track(
        order=order, title="Test Track",
        mood=mood, sub_genre=sub_genre,
        bpm=bpm, key="G major",
        vocal=VocalPersona.VR_F1,
        hook_priority=hook,
    )


class TestAssembleStylePrompt:
    def test_prompt_length_under_1000(self):
        track = make_track()
        result = assemble_style_prompt(track)
        assert len(result.prompt) <= 1000, f"프롬프트 길이: {len(result.prompt)}"

    def test_negative_tags_present(self):
        track = make_track()
        result = assemble_style_prompt(track)
        assert "no EDM drops" in result.negative

    def test_hook_priority_adds_keyword(self):
        hook_track = make_track(hook=True)
        result = assemble_style_prompt(hook_track)
        assert "memorable hook" in result.prompt

    def test_mood_descriptor_in_prompt(self):
        dreamy_track = make_track(mood=Mood.DREAMY)
        result = assemble_style_prompt(dreamy_track)
        assert "ethereal" in result.prompt or "dreamy" in result.prompt.lower()

    def test_sub_genre_in_prompt(self):
        track = make_track(sub_genre="acoustic bossa nova pop")
        result = assemble_style_prompt(track)
        assert "nylon guitar" in result.prompt

    def test_bpm_in_prompt(self):
        track = make_track(bpm=92)
        result = assemble_style_prompt(track)
        assert "92 BPM" in result.prompt

    def test_vocal_in_prompt(self):
        track = make_track()
        result = assemble_style_prompt(track)
        assert "warm female" in result.prompt

    @pytest.mark.parametrize("sub_genre", [
        "acoustic bossa nova pop",
        "soft jazz pop",
        "lo-fi dream pop",
        "warm retro city pop",
        "mellow folk pop",
        "ambient indie pop",
        "neo-soul light",
        "smooth adult contemporary",
    ])
    def test_all_sub_genres(self, sub_genre):
        track = make_track(sub_genre=sub_genre)
        result = assemble_style_prompt(track)
        assert len(result.prompt) > 0
        assert len(result.prompt) <= 1000


class TestBuildSunoPayload:
    def test_payload_structure(self):
        track = make_track()
        lyrics = "[Verse]\nMorning light\n[Chorus]\nStay a while"
        payload = build_suno_payload(track, lyrics)
        assert payload.track_order == 1
        assert "[Verse" in payload.lyrics
        assert "Negative:" in payload.style_prompt
        assert payload.model == "v5"

    def test_payload_not_instrumental_by_default(self):
        track = make_track()
        payload = build_suno_payload(track, "[Verse]\nX\n[Chorus]\nY")
        assert payload.instrumental is False

    def test_style_prompt_length(self):
        track = make_track()
        payload = build_suno_payload(track, "[Verse]\nX\n[Chorus]\nY")
        assert len(payload.style_prompt) <= 1000
