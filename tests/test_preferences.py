from __future__ import annotations

import json

import pytest


def test_legacy_config_retains_existing_fields_and_adds_preferences(isolated_paths):
    from app.config import config_path, load_config, save_config

    directory = str(isolated_paths.root / "downloads")
    for schema in ({}, {"schema_version": 1}):
        config_path().write_text(json.dumps({**schema, "download_dir": directory, "theme": "dark", "max_parallel": 3}), encoding="utf-8")
        settings = load_config()
        assert (settings.download_dir, settings.theme, settings.max_parallel) == (directory, "dark", 3)
        assert (settings.remember_download_preferences, settings.download_mode, settings.preferred_quality) == (True, "audio_video", None)
        settings.download_mode = "audio_mp3"
        settings.preferred_quality = 1080
        settings.remember_download_preferences = False
        save_config(settings)
        assert load_config() == settings


@pytest.mark.parametrize("field,value", [
    ("remember_download_preferences", 1), ("remember_download_preferences", "false"),
    ("download_mode", "mp4"), ("download_mode", None), ("download_mode", []),
    ("preferred_quality", True), ("preferred_quality", "1080"), ("preferred_quality", 1080.0),
    ("preferred_quality", 0), ("preferred_quality", -1), ("preferred_quality", 16385), ("preferred_quality", {}),
])
def test_invalid_preference_rejected_in_memory_but_only_that_field_recovers_on_load(isolated_paths, field, value):
    from app.config import AppConfig, ConfigValidationError, config_diagnostics, config_path, load_config

    with pytest.raises(ConfigValidationError):
        AppConfig(**{field: value})
    data = {"schema_version": 1, "download_dir": str(isolated_paths.root / "kept"), "theme": "dark", "max_parallel": 3,
            "remember_download_preferences": False, "download_mode": "audio_mp3", "preferred_quality": 1080}
    data[field] = value
    config_path().write_text(json.dumps(data), encoding="utf-8")
    loaded = load_config()
    assert loaded.download_dir == data["download_dir"] and loaded.theme == "dark" and loaded.max_parallel == 3
    for name in ("remember_download_preferences", "download_mode", "preferred_quality"):
        assert getattr(loaded, name) == (getattr(AppConfig(), name) if name == field else data[name])
    assert any(field in message for message in config_diagnostics())


@pytest.mark.parametrize("height", [None, 240, 360, 480, 720, 1080, 1440, 2160, 4320, 900])
def test_quality_persists_by_height_instead_of_per_video_format_id(isolated_paths, height):
    from app.config import AppConfig, load_config, save_config

    settings = AppConfig(preferred_quality=height)
    save_config(settings)
    assert load_config().preferred_quality == height
