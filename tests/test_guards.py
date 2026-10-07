"""Tests for risk/guards.py — DU guard and read-only guard."""

import pytest
from risk.guards import ReadOnlyError, assert_paper_account, assert_readonly_phase


class TestPaperAccountGuard:
    def test_du_account_passes(self):
        assert_paper_account("DU123456")  # must not raise or exit

    def test_du_mixed_case_passes(self):
        assert_paper_account("DU999999")

    def test_live_account_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            assert_paper_account("U1234567")
        assert "U1234567" in str(exc_info.value)

    def test_empty_account_exits(self):
        with pytest.raises(SystemExit):
            assert_paper_account("")

    def test_lowercase_du_exits(self):
        with pytest.raises(SystemExit):
            assert_paper_account("du123456")

    def test_error_message_contains_account(self):
        with pytest.raises(SystemExit) as exc_info:
            assert_paper_account("F9999999")
        assert "F9999999" in str(exc_info.value)


class TestReadonlyPhaseGuard:
    @pytest.mark.parametrize("phase", [0, 1, 2, 3, 4, 5])
    def test_phases_0_to_5_blocked(self, phase: int):
        with pytest.raises(ReadOnlyError) as exc_info:
            assert_readonly_phase(phase)
        assert str(phase) in str(exc_info.value)

    @pytest.mark.parametrize("phase", [6, 7, 8])
    def test_phase_6_plus_allowed(self, phase: int):
        assert_readonly_phase(phase)  # must not raise

    def test_error_is_permission_error_subclass(self):
        with pytest.raises(PermissionError):
            assert_readonly_phase(0)
