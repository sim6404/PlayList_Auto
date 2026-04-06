"""
Velvet Radio — subtitle_generator 단위 테스트
"""
import pytest
from pathlib import Path
import tempfile

from src.phase3_video.subtitle_generator import _strip_metatags, generate_srt


class TestStripMetatags:
    def test_removes_section_tags(self):
        text = "[Verse]\nMorning light\n[Chorus]\nStay a while\n[Bridge]"
        lines = _strip_metatags(text)
        assert "Morning light" in lines
        assert "Stay a while" in lines
        assert "[Verse]" not in lines

    def test_removes_performance_tags(self):
        text = "[Verse]\n[Soft] Morning light\n[Chorus]\n[Breathy] Stay here"
        lines = _strip_metatags(text)
        assert any("Morning light" in l for l in lines)
        assert not any("[Soft]" in l for l in lines)

    def test_removes_empty_lines(self):
        text = "[Verse]\n\n\nMorning light\n\n[Chorus]\nStay"
        lines = _strip_metatags(text)
        assert "" not in lines

    def test_returns_list(self):
        result = _strip_metatags("[Verse]\nHello\n[Chorus]\nWorld")
        assert isinstance(result, list)
        assert len(result) == 2


class TestGenerateSRT:
    def test_creates_srt_file(self):
        lyrics = "[Verse]\n[Soft] Morning light through window\n[Chorus]\nStay a while longer here"
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
            output_path = Path(f.name)
        generate_srt(lyrics, 180.0, output_path)
        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "-->" in content
        output_path.unlink()

    def test_srt_format_valid(self):
        lyrics = "[Verse]\nHello world\n[Chorus]\nGoodbye world"
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
            output_path = Path(f.name)
        generate_srt(lyrics, 120.0, output_path)
        content = output_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        # 첫 번째 항목: 번호, 타임스탬프, 텍스트
        assert lines[0] == "1"
        assert "-->" in lines[1]
        output_path.unlink()

    def test_empty_lyrics_creates_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
            output_path = Path(f.name)
        generate_srt("[Instrumental]", 120.0, output_path)
        content = output_path.read_text(encoding="utf-8")
        assert content == ""
        output_path.unlink()
