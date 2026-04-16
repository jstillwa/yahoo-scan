"""Tests for inbox_cleaner decision logic and helper functions."""

import sqlite3
import base64

import pytest

from inbox_cleaner.cli import (
    calculate_historical_bias,
    decide_action,
    decode_email_header,
    extract_domain,
)
from inbox_cleaner.db import SeenStore


# ── extract_domain ──────────────────────────────────────────────────────


class TestExtractDomain:
    def test_angle_bracket_format(self) -> None:
        assert extract_domain("Name <user@example.com>") == "example.com"

    def test_bare_email(self) -> None:
        assert extract_domain("user@example.com") == "example.com"

    def test_no_at_sign(self) -> None:
        assert extract_domain("no-at-sign") == ""

    def test_subdomain(self) -> None:
        assert extract_domain("Name <user@mail.example.com>") == "mail.example.com"

    def test_case_insensitive(self) -> None:
        assert extract_domain("User@EXAMPLE.COM") == "example.com"


# ── decode_email_header ─────────────────────────────────────────────────


class TestDecodeEmailHeader:
    def test_plain_ascii(self) -> None:
        assert decode_email_header("Hello World") == "Hello World"

    def test_empty_string(self) -> None:
        assert decode_email_header("") == ""

    def test_encoded_utf8_base64(self) -> None:
        encoded = "=?utf-8?B?" + base64.b64encode("Héllo".encode()).decode() + "?="
        assert decode_email_header(encoded) == "Héllo"

    def test_encoded_utf8_quoted_printable(self) -> None:
        assert decode_email_header("=?utf-8?Q?H=C3=A9llo?=") == "Héllo"


# ── calculate_historical_bias ───────────────────────────────────────────


class TestCalculateHistoricalBias:
    def test_empty_dict_returns_none(self) -> None:
        assert calculate_historical_bias({}) is None

    def test_below_min_samples_returns_none(self) -> None:
        assert calculate_historical_bias({"trash": 1, "skip": 1}, min_samples=3) is None

    def test_valid_data_returns_percentages(self) -> None:
        result = calculate_historical_bias({"trash": 3, "skip": 7}, min_samples=3)
        assert result is not None
        assert result["trash"] == pytest.approx(0.3)
        assert result["skip"] == pytest.approx(0.7)
        assert result["promotional"] == pytest.approx(0.0)
        assert result["total"] == 10

    def test_exact_min_samples(self) -> None:
        result = calculate_historical_bias({"skip": 3}, min_samples=3)
        assert result is not None
        assert result["skip"] == pytest.approx(1.0)


# ── decide_action ───────────────────────────────────────────────────────


def _rspamd(score: float = 0.0, action: str = "") -> dict[str, object]:
    return {"score": score, "action": action}


class TestDecideActionHighConfidenceSpam:
    """Priority 1: very high-confidence spam — never overridden."""

    def test_reject_action(self) -> None:
        assert decide_action(_rspamd(action="reject"), "normal", 6.0) == "trash"

    def test_score_at_spam_threshold(self) -> None:
        assert decide_action(_rspamd(score=10.0), "normal", 6.0, spam_threshold=10.0) == "trash"

    def test_score_above_spam_threshold(self) -> None:
        assert decide_action(_rspamd(score=15.0), "normal", 6.0, spam_threshold=10.0) == "trash"

    def test_reject_overrides_keep_history(self) -> None:
        history = {"skip": 100}
        assert decide_action(
            _rspamd(action="reject"), "normal", 6.0,
            domain_history=history, history_min_samples=3,
        ) == "trash"


class TestDecideActionStrongHistory:
    """Priority 2: strong user history (>=5 samples, >=80% agreement)."""

    def test_strong_skip_history_keeps(self) -> None:
        history = {"skip": 9, "trash": 1}  # 90% skip, 10 samples
        assert decide_action(
            _rspamd(score=5.0), "normal", 6.0,
            domain_history=history, history_min_samples=3,
        ) == "keep"

    def test_strong_trash_history_with_some_score(self) -> None:
        history = {"trash": 9, "skip": 1}  # 90% trash, 10 samples
        # score >= score_threshold * 0.3 → 6.0 * 0.3 = 1.8
        assert decide_action(
            _rspamd(score=2.0), "normal", 6.0,
            domain_history=history, history_min_samples=3,
        ) == "trash"

    def test_strong_trash_history_needs_some_score(self) -> None:
        history = {"trash": 9, "skip": 1}  # 90% trash, 10 samples
        # score 0.0 < 1.8 threshold, so strong trash doesn't fire
        result = decide_action(
            _rspamd(score=0.0), "normal", 6.0,
            domain_history=history, history_min_samples=3,
        )
        assert result != "trash" or result == "keep"

    def test_strong_promotional_history(self) -> None:
        history = {"promotional": 9, "skip": 1}  # 90% promotional, 10 samples
        assert decide_action(
            _rspamd(score=0.0), "normal", 6.0,
            domain_history=history, history_min_samples=3,
        ) == "promotional"

    def test_not_enough_samples_for_strong(self) -> None:
        # 4 samples, need >=5 for strong history
        history = {"skip": 4}
        assert decide_action(
            _rspamd(score=0.0), "normal", 6.0,
            domain_history=history, history_min_samples=3,
        ) == "keep"


class TestDecideActionMediumSignals:
    """Priority 3: medium-confidence signals (LLM spam, rspamd score, rspamd action, LLM promotional)."""

    def test_llm_spam(self) -> None:
        assert decide_action(_rspamd(score=0.0), "spam", 6.0) == "trash"

    def test_score_at_threshold(self) -> None:
        assert decide_action(_rspamd(score=6.0), "normal", 6.0) == "promotional"

    def test_score_above_threshold(self) -> None:
        assert decide_action(_rspamd(score=8.0), "normal", 6.0) == "promotional"

    def test_history_weight_raises_effective_threshold(self) -> None:
        # history_weight=0.3, >50% skip → effective = 6.0 * 1.3 = 7.8
        history = {"skip": 4, "trash": 1}  # 80% skip, 5 samples
        # Score 7.0 is below effective 7.8 but above base 6.0
        # However strong history (80% skip, 5 samples) fires first → keep
        # Use a weaker skip ratio so strong history doesn't fire
        history2 = {"skip": 3, "trash": 1, "promotional": 1}  # 60% skip, 5 samples
        result = decide_action(
            _rspamd(score=7.0), "normal", 6.0,
            domain_history=history2, history_weight=0.3, history_min_samples=3,
        )
        # effective threshold = 6.0 * 1.3 = 7.8, score 7.0 < 7.8 → not promotional from score
        # No LLM promotional, no rspamd action → falls through to moderate history
        # 60% skip but trash is only 20% → not moderate trash or promo tiebreaker
        assert result == "keep"

    def test_rspamd_add_header_action(self) -> None:
        assert decide_action(_rspamd(score=0.0, action="add header"), "normal", 6.0) == "promotional"

    def test_rspamd_quarantine_action(self) -> None:
        assert decide_action(_rspamd(score=0.0, action="quarantine"), "normal", 6.0) == "promotional"

    def test_llm_promotional(self) -> None:
        assert decide_action(_rspamd(score=0.0), "promotional", 6.0) == "promotional"

    def test_llm_marketing(self) -> None:
        assert decide_action(_rspamd(score=0.0), "marketing", 6.0) == "promotional"

    def test_llm_ads(self) -> None:
        assert decide_action(_rspamd(score=0.0), "ads", 6.0) == "promotional"

    def test_llm_promotional_overridden_by_moderate_keep_history(self) -> None:
        # >60% skip in history overrides LLM promotional label
        history = {"skip": 7, "trash": 1, "promotional": 2}  # 70% skip, 10 samples
        assert decide_action(
            _rspamd(score=0.0), "promotional", 6.0,
            domain_history=history, history_min_samples=3,
        ) == "keep"


class TestDecideActionModerateHistory:
    """Priority 4: moderate history tiebreakers for borderline cases."""

    def test_moderate_trash_tiebreaker(self) -> None:
        # >60% trash + score >= threshold * 0.5
        history = {"trash": 7, "skip": 3}  # 70% trash, 10 samples
        # score_threshold * 0.5 = 3.0, score 3.0 >= 3.0
        assert decide_action(
            _rspamd(score=3.0), "normal", 6.0,
            domain_history=history, history_min_samples=3,
        ) == "trash"

    def test_moderate_promotional_tiebreaker(self) -> None:
        # >60% promotional + score >= threshold * 0.5
        history = {"promotional": 7, "skip": 3}  # 70% promotional, 10 samples
        assert decide_action(
            _rspamd(score=3.0), "normal", 6.0,
            domain_history=history, history_min_samples=3,
        ) == "promotional"


class TestDecideActionDefault:
    """Priority 5: default to keep."""

    def test_default_keep(self) -> None:
        assert decide_action(_rspamd(), "normal", 6.0) == "keep"

    def test_low_score_normal_label(self) -> None:
        assert decide_action(_rspamd(score=2.0), "normal", 6.0) == "keep"

    def test_no_history(self) -> None:
        assert decide_action(
            _rspamd(score=2.0), "normal", 6.0, domain_history=None,
        ) == "keep"


# ── get_domain_history (in-memory SQLite) ───────────────────────────────


class TestGetDomainHistory:
    @pytest.fixture()
    def store(self, tmp_path: pytest.TempPathFactory) -> SeenStore:
        db_path = str(tmp_path / "test.sqlite")  # type: ignore[operator]
        return SeenStore(db_path)

    def _insert(self, store: SeenStore, from_addr: str, action: str) -> None:
        store.record_action(
            uidvalidity="1",
            uid=hash(from_addr + action) & 0x7FFFFFFF,
            from_addr=from_addr,
            subject="test",
            rspamd_score=0.0,
            llm_label="normal",
            recommended_action=action,
            final_action=action,
            mode="auto",
        )

    def test_matches_exact_domain(self, store: SeenStore) -> None:
        self._insert(store, "user@example.com", "trash")
        self._insert(store, "other@example.com", "skip")
        result = store.get_domain_history("example.com")
        assert result == {"trash": 1, "skip": 1}

    def test_does_not_match_subdomain_suffix(self, store: SeenStore) -> None:
        """The LIKE pattern %@domain must NOT match user@example.com.evil.org."""
        self._insert(store, "user@example.com.evil.org", "trash")
        result = store.get_domain_history("example.com")
        assert result == {}

    def test_empty_domain_returns_empty(self, store: SeenStore) -> None:
        assert store.get_domain_history("") == {}

    def test_no_records_returns_empty(self, store: SeenStore) -> None:
        assert store.get_domain_history("nonexistent.com") == {}
