"""Tests for the Dispatch settings and where its token is kept."""

from meeting_recorder.config.dispatch_config import (
    KEYRING_PURPOSE,
    KEYRING_SENTINEL,
    TOKEN_KEY,
    URL_KEY,
    forget_token,
    is_configured,
    normalise_url,
    read_token,
    token_for_config,
    token_store,
    token_warning,
    url_error,
)
from meeting_recorder.config.keyring_store import GEMINI_PURPOSE, KeyringStore


class FakeStore:
    """An in-memory keyring that can be switched off."""

    def __init__(self, reachable=True, refuse=False, held=None):
        self.reachable = reachable
        self.refuse = refuse
        self.held = held
        self.deleted = False

    def available(self):
        return self.reachable

    def get(self):
        return self.held if self.reachable else None

    def set(self, value):
        if self.refuse:
            return False
        self.held = value
        return True

    def delete(self):
        self.deleted = True
        self.held = None
        return True


class TestUrl:
    def test_a_trailing_slash_goes(self):
        assert normalise_url(" http://dispatch:8000/ ") == "http://dispatch:8000"

    def test_https_is_kept(self):
        assert normalise_url("https://dispatch.tail.ts.net") == "https://dispatch.tail.ts.net"

    def test_a_bare_host_is_refused(self):
        assert normalise_url("dispatch:8000") == ""

    def test_another_scheme_is_refused(self):
        assert normalise_url("ftp://dispatch") == ""

    def test_empty_is_not_an_error(self):
        assert url_error("") is None

    def test_a_bad_url_says_what_to_type(self):
        assert "http://" in url_error("dispatch")


class TestConfigured:
    def test_url_and_token_are_both_needed(self):
        assert is_configured({URL_KEY: "http://d:8000", TOKEN_KEY: KEYRING_SENTINEL})
        assert not is_configured({URL_KEY: "http://d:8000", TOKEN_KEY: ""})
        assert not is_configured({URL_KEY: "", TOKEN_KEY: "t"})

    def test_a_bad_url_is_not_configured(self):
        assert not is_configured({URL_KEY: "dispatch", TOKEN_KEY: "t"})

    def test_it_never_reads_the_keyring(self):
        # The sentinel alone says a token exists; resolving it would cost a handshake.
        assert is_configured({URL_KEY: "http://d", TOKEN_KEY: KEYRING_SENTINEL})


class TestToken:
    def test_a_new_token_goes_to_the_keyring(self):
        store = FakeStore()
        assert token_for_config("abc123", store) == KEYRING_SENTINEL
        assert store.held == "abc123"

    def test_without_a_keyring_it_stays_in_the_file(self):
        assert token_for_config("abc123", FakeStore(reachable=False)) == "abc123"

    def test_a_refused_write_stays_in_the_file(self):
        assert token_for_config("abc123", FakeStore(refuse=True)) == "abc123"

    def test_surrounding_space_is_trimmed(self):
        store = FakeStore()
        token_for_config("  abc123\n", store)
        assert store.held == "abc123"

    def test_the_sentinel_resolves_from_the_keyring(self):
        assert read_token({TOKEN_KEY: KEYRING_SENTINEL}, FakeStore(held="abc123")) == "abc123"

    def test_an_unreachable_keyring_reads_as_no_token(self):
        assert read_token({TOKEN_KEY: KEYRING_SENTINEL}, FakeStore(reachable=False)) == ""

    def test_a_plaintext_token_is_read_as_is(self):
        assert read_token({TOKEN_KEY: "abc123"}, FakeStore(held="other")) == "abc123"

    def test_forgetting_clears_the_keyring(self):
        store = FakeStore(held="abc123")
        assert forget_token(store) == ""
        assert store.deleted

    def test_a_space_inside_is_flagged(self):
        assert token_warning("abc 123") is not None
        assert token_warning("abc123") is None


class TestKeyringEntry:
    def test_it_is_not_the_gemini_entry(self):
        assert KEYRING_PURPOSE != GEMINI_PURPOSE

    def test_the_store_is_inert_under_a_sandboxed_home(self, monkeypatch, tmp_path):
        # The same guard as the Gemini key: a redirected HOME never reaches the real keyring.
        monkeypatch.setenv("HOME", str(tmp_path))
        store = token_store()
        assert isinstance(store, KeyringStore)
        assert not store.available()
        assert store.set("abc123") is False
