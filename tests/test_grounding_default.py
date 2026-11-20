"""Grounding (crop_mode) default flip — a09aa767's real measured OCR time
(18.1s/page) showed gundam mode is far cheaper on the GPU service than the
2-4x slowdown the original opt-in-only decision was based on, so the
per-job checkbox now defaults to on rather than off. See config.py's
ENABLE_UNLIMITED_GROUNDING and OCR_INDEXING_AND_PERFORMANCE_DECISIONS.md §8.

Per-job opt-out (explicit grounding_enabled=True/False passed to
run_unlimited_ocr_for_image_with_raw) already has full coverage in
test_grounding_prompt.py — these tests cover only the *default* that
feeds into a new job's frozen engine_params when no explicit choice has
been made yet.

Note: main.py's _SETTINGS_FILE is computed from main.py's own directory
(`os.path.dirname(__file__)`), NOT from database.DATA_DIR — unlike
database.py, it doesn't respect the DATA_DIR env var conftest.py sets for
test isolation, so _load_settings() reads the real local repo's
(gitignored) data/settings.json regardless of test environment. Pre-
existing, unrelated to this change — these tests route around it by
pointing _SETTINGS_FILE at a throwaway path instead, rather than relying
on that file being absent."""

import json

import config
import main as main_module


def test_config_default_is_true_with_no_env_var_override(monkeypatch):
    monkeypatch.delenv("ENABLE_UNLIMITED_GROUNDING", raising=False)
    import importlib
    reloaded = importlib.reload(config)
    try:
        assert reloaded.ENABLE_UNLIMITED_GROUNDING is True
    finally:
        importlib.reload(config)  # restore the module's normal test-session state


def _isolate_settings_file(tmp_path, monkeypatch):
    settings_file = str(tmp_path / "settings.json")
    monkeypatch.setattr(main_module, "_SETTINGS_FILE", settings_file)
    monkeypatch.setattr(main_module, "_settings_cache", {})
    monkeypatch.setattr(main_module, "_settings_cache_ts", 0.0)
    return settings_file


def test_load_settings_defaults_grounding_on_with_no_persisted_override(monkeypatch, tmp_path):
    _isolate_settings_file(tmp_path, monkeypatch)
    settings = main_module._load_settings()
    assert settings["enable_unlimited_grounding"] is True


def test_explicit_persisted_opt_out_still_overrides_the_default(monkeypatch, tmp_path):
    settings_file = _isolate_settings_file(tmp_path, monkeypatch)
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump({"enable_unlimited_grounding": False}, f)
    monkeypatch.setattr(main_module, "_settings_cache", {})
    monkeypatch.setattr(main_module, "_settings_cache_ts", 0.0)
    settings = main_module._load_settings()
    assert settings["enable_unlimited_grounding"] is False
