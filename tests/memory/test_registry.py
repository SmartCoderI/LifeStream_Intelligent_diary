"""F10: speaker registry parsing, lookup and name application."""

import pytest

from lifestream.memory.registry import RegistryError, SpeakerRegistry, apply_names, rename_text
from lifestream.schemas import MeetingPoint


def test_missing_file_is_empty(tmp_path):
    assert SpeakerRegistry(tmp_path / "none.yaml").names_for("v1") == {}


def test_parse_valid_file(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("videos:\n  v1:\n    SPEAKER_00: Alice\n    SPEAKER_01: '  '\n  v2:\n    SPEAKER_00: Bob\n")
    reg = SpeakerRegistry(path)
    assert reg.names_for("v1") == {"SPEAKER_00": "Alice"}  # blank names ignored
    assert reg.names_for("v2") == {"SPEAKER_00": "Bob"}  # labels are per video
    assert reg.names_for("v3") == {}


@pytest.mark.parametrize("content", ["videos: [1, 2", "- just a list", "videos:\n  v1: [a, b]"])
def test_invalid_files_raise_clear_error(tmp_path, content):
    path = tmp_path / "s.yaml"
    path.write_text(content)
    with pytest.raises(RegistryError):
        SpeakerRegistry(path)


def test_set_names_saves_and_removes(tmp_path):
    path = tmp_path / "s.yaml"
    reg = SpeakerRegistry(path)
    reg.set_names("v1", {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"})
    assert SpeakerRegistry(path).names_for("v1") == {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
    reg.set_names("v1", {"SPEAKER_01": ""})
    assert SpeakerRegistry(path).names_for("v1") == {"SPEAKER_00": "Alice"}
    with pytest.raises(RegistryError):
        reg.set_names("v1", {"alice": "Alice"})


def test_rename_text_handles_llm_style_labels():
    names = {"SPEAKER_00": "Alice"}
    assert rename_text("SPEAKER_00 (Ali) agreed with SPEAKER_01.", names) == "Alice agreed with SPEAKER_01."
    assert rename_text("SPEAKER_000 is different", names) == "SPEAKER_000 is different"


def test_apply_names_to_models_and_nested_data():
    point = MeetingPoint(speaker="SPEAKER_00", timestamp="00:00:05", content="SPEAKER_00 proposed it")
    assert apply_names(point, {"SPEAKER_00": "Alice"}).speaker == "Alice"
    data = {"attendees": ["SPEAKER_00", "SPEAKER_01"], "n": 3}
    assert apply_names(data, {"SPEAKER_00": "Alice"}) == {"attendees": ["Alice", "SPEAKER_01"], "n": 3}
    assert apply_names(data, {}) is data
