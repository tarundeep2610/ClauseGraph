"""Settings round-trip for the two new, previously-unreachable AI-indexing
knobs: ai_index_review_rule_prior (combine rule-based + AI) and
ai_index_chunk_overlap_pages (chunk boundary re-sampling). Neither had any
frontend or settings-layer path before this — see the plan's "New scope:
expose BOTH unmeasured AI-indexing knobs" section.

Note: main.py's _SETTINGS_FILE is computed from main.py's own directory,
NOT from database.DATA_DIR, so _load_settings() reads the real local
repo's (gitignored) data/settings.json regardless of test environment —
same pre-existing quirk test_grounding_default.py already routes around.
These tests reuse that same isolation pattern."""

import json

import config
import main as main_module


def _isolate_settings_file(tmp_path, monkeypatch):
    settings_file = str(tmp_path / "settings.json")
    monkeypatch.setattr(main_module, "_SETTINGS_FILE", settings_file)
    monkeypatch.setattr(main_module, "_settings_cache", {})
    monkeypatch.setattr(main_module, "_settings_cache_ts", 0.0)
    return settings_file


def test_both_knobs_default_to_config_values_when_unset(monkeypatch, tmp_path):
    _isolate_settings_file(tmp_path, monkeypatch)
    settings = main_module._load_settings()
    assert settings["ai_index_review_rule_prior"] == config.AI_INDEX_REVIEW_RULE_PRIOR
    assert settings["ai_index_chunk_overlap_pages"] == config.AI_INDEX_CHUNK_OVERLAP_PAGES


def test_persisted_override_wins_for_both_knobs(monkeypatch, tmp_path):
    settings_file = _isolate_settings_file(tmp_path, monkeypatch)
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump({"ai_index_review_rule_prior": True, "ai_index_chunk_overlap_pages": 0}, f)
    monkeypatch.setattr(main_module, "_settings_cache", {})
    monkeypatch.setattr(main_module, "_settings_cache_ts", 0.0)
    settings = main_module._load_settings()
    assert settings["ai_index_review_rule_prior"] is True
    assert settings["ai_index_chunk_overlap_pages"] == 0


def test_save_settings_persists_both_new_keys(monkeypatch, tmp_path):
    _isolate_settings_file(tmp_path, monkeypatch)
    main_module.save_settings({"ai_index_review_rule_prior": True, "ai_index_chunk_overlap_pages": 0})
    monkeypatch.setattr(main_module, "_settings_cache", {})
    monkeypatch.setattr(main_module, "_settings_cache_ts", 0.0)
    settings = main_module._load_settings()
    assert settings["ai_index_review_rule_prior"] is True
    assert settings["ai_index_chunk_overlap_pages"] == 0


def test_save_settings_round_trips_overlap_zero_specifically(monkeypatch, tmp_path):
    """0 is the one value most likely to be mishandled (falsy) — confirm
    save_settings()/_load_settings() treat it as a real, present value."""
    _isolate_settings_file(tmp_path, monkeypatch)
    main_module.save_settings({"ai_index_chunk_overlap_pages": 3})
    main_module.save_settings({"ai_index_chunk_overlap_pages": 0})
    monkeypatch.setattr(main_module, "_settings_cache", {})
    monkeypatch.setattr(main_module, "_settings_cache_ts", 0.0)
    settings = main_module._load_settings()
    assert settings["ai_index_chunk_overlap_pages"] == 0
