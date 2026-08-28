"""Tests for Gemini model discovery, filtering, and caching."""

import json

from meeting_recorder.config.defaults import GEMINI_MODELS
from meeting_recorder.services import gemini_models


class FakeModel:
    def __init__(self, name, supported_actions=("generateContent",)):
        self.name = name
        self.supported_actions = list(supported_actions)


class FakeClient:
    def __init__(self, models, api_key=None):
        self._models = models
        self.api_key = api_key

    class _Models:
        def __init__(self, models):
            self._models = models

        def list(self):
            return self._models

    @property
    def models(self):
        return self._Models(self._models)


def factory_for(models):
    return lambda api_key=None: FakeClient(models, api_key=api_key)


class TestIsOffered:
    def test_keeps_general_purpose_gemini_models(self):
        assert gemini_models.is_offered("gemini-3-flash-preview")
        assert gemini_models.is_offered("gemini-flash-latest")

    def test_rejects_non_gemini_families(self):
        assert not gemini_models.is_offered("gemma-4-31b-it")
        assert not gemini_models.is_offered("lyria-3-pro-preview")

    def test_rejects_specialised_variants(self):
        for model in (
            "gemini-3-pro-image",
            "gemini-2.5-flash-preview-tts",
            "gemini-robotics-er-2-preview",
            "gemini-2.5-computer-use-preview-10-2025",
            "gemini-3.1-pro-preview-customtools",
        ):
            assert not gemini_models.is_offered(model), model


class TestFilterModels:
    def test_deduplicates_and_puts_latest_aliases_first(self):
        out = gemini_models.filter_models(
            ["gemini-3-flash-preview", "gemini-flash-latest", "gemini-3-flash-preview"]
        )
        assert out == ["gemini-flash-latest", "gemini-3-flash-preview"]

    def test_drops_everything_unusable(self):
        assert gemini_models.filter_models(["gemma-4-31b-it", "gemini-3-pro-image"]) == []


class TestFetchModels:
    def test_returns_filtered_ids_without_the_models_prefix(self):
        models = [
            FakeModel("models/gemini-3-flash-preview"),
            FakeModel("models/gemini-3-pro-image"),
            FakeModel("models/gemma-4-31b-it"),
        ]
        out = gemini_models.fetch_models("key", client_factory=factory_for(models))
        assert out == ["gemini-3-flash-preview"]

    def test_skips_models_that_cannot_generate_content(self):
        models = [
            FakeModel("models/gemini-flash-latest", supported_actions=("embedContent",)),
            FakeModel("models/gemini-3-flash-preview"),
        ]
        out = gemini_models.fetch_models("key", client_factory=factory_for(models))
        assert out == ["gemini-3-flash-preview"]


class TestCache:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "models.json"
        gemini_models.write_cache(["gemini-3-flash-preview"], now=1000.0, path=path)
        assert gemini_models.read_cache(now=1000.0, path=path) == ["gemini-3-flash-preview"]

    def test_expired_cache_is_ignored(self, tmp_path):
        path = tmp_path / "models.json"
        gemini_models.write_cache(["gemini-3-flash-preview"], now=0.0, path=path)
        stale = gemini_models.CACHE_TTL_SECONDS + 1
        assert gemini_models.read_cache(now=stale, path=path) is None

    def test_missing_cache_returns_none(self, tmp_path):
        assert gemini_models.read_cache(path=tmp_path / "absent.json") is None

    def test_malformed_cache_returns_none(self, tmp_path):
        path = tmp_path / "models.json"
        path.write_text("not json", encoding="utf-8")
        assert gemini_models.read_cache(path=path) is None

    def test_cache_without_model_list_returns_none(self, tmp_path):
        path = tmp_path / "models.json"
        path.write_text(json.dumps({"fetched_at": 1.0}), encoding="utf-8")
        assert gemini_models.read_cache(now=1.0, path=path) is None


class TestAvailableModels:
    def test_falls_back_to_static_list_without_a_key(self, monkeypatch):
        monkeypatch.setattr(gemini_models, "read_cache", lambda *a, **k: None)
        assert gemini_models.available_models("") == list(GEMINI_MODELS)

    def test_falls_back_to_static_list_when_the_fetch_fails(self, monkeypatch):
        monkeypatch.setattr(gemini_models, "read_cache", lambda *a, **k: None)

        def boom(api_key=None):
            raise RuntimeError("network down")

        assert gemini_models.available_models("key", client_factory=boom) == list(GEMINI_MODELS)

    def test_uses_a_fresh_cache_without_calling_the_api(self, monkeypatch):
        monkeypatch.setattr(gemini_models, "read_cache", lambda *a, **k: ["cached-model"])

        def boom(api_key=None):
            raise AssertionError("must not hit the API when the cache is fresh")

        assert gemini_models.available_models("key", client_factory=boom) == ["cached-model"]

    def test_force_refresh_bypasses_the_cache(self, monkeypatch):
        monkeypatch.setattr(gemini_models, "read_cache", lambda *a, **k: ["cached-model"])
        monkeypatch.setattr(gemini_models, "write_cache", lambda *a, **k: None)
        models = [FakeModel("models/gemini-3-flash-preview")]
        out = gemini_models.available_models(
            "key", force_refresh=True, client_factory=factory_for(models)
        )
        assert out == ["gemini-3-flash-preview"]

    def test_never_returns_an_empty_list(self, monkeypatch):
        monkeypatch.setattr(gemini_models, "read_cache", lambda *a, **k: None)
        out = gemini_models.available_models("key", client_factory=factory_for([]))
        assert out == list(GEMINI_MODELS)


class TestEnsureSelected:
    def test_keeps_list_unchanged_when_current_is_present(self):
        ids = ["gemini-flash-latest", "gemini-3-flash-preview"]
        assert gemini_models.ensure_selected(ids, "gemini-flash-latest") == ids

    def test_prepends_a_configured_model_the_api_no_longer_lists(self):
        # A retired model must stay selectable so settings does not silently
        # switch the user to a different one.
        out = gemini_models.ensure_selected(["gemini-3-flash-preview"], "gemini-2.5-flash")
        assert out == ["gemini-2.5-flash", "gemini-3-flash-preview"]

    def test_empty_current_is_a_no_op(self):
        assert gemini_models.ensure_selected(["gemini-3-flash-preview"], "") == [
            "gemini-3-flash-preview"
        ]
