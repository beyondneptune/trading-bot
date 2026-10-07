"""Phase 7 tests: learning system — R stats, null model, shadow mode, proposals, readiness."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

UTC = timezone.utc


def _setup_db(tmp_path: Path):
    from data.database import get_connection, init_db
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    return conn, db_path


def _insert_closed_position(conn, realised_r: float, signal_type: str = "breakout"):
    """Insert a signal + closed position with given R."""
    conn.execute(
        """INSERT INTO signals
           (symbol, exchange, signal_type, setup_version, action, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("AZN", "LSE", signal_type, "v1", "BUY", datetime.now(UTC).isoformat()),
    )
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO positions
           (symbol, exchange, quantity, avg_cost, currency, opened_at,
            closed_at, close_price, realised_r, open_signal_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("AZN", "LSE", 10, 7000.0, "GBP",
         datetime.now(UTC).isoformat(),
         datetime.now(UTC).isoformat(),
         7000.0 + realised_r * 350.0,
         realised_r, sid),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# learning/r_stats.py
# ---------------------------------------------------------------------------

class TestRStats:
    def test_empty_returns_zero_stats(self):
        from learning.r_stats import compute_r_stats
        stats = compute_r_stats([])
        assert stats.n == 0
        assert stats.expectancy == 0.0

    def test_expectancy_calculation(self):
        from learning.r_stats import compute_r_stats
        stats = compute_r_stats([2.0, -1.0, 1.5, -1.0, 3.0])
        assert stats.expectancy == pytest.approx(0.9, rel=0.01)

    def test_win_rate(self):
        from learning.r_stats import compute_r_stats
        stats = compute_r_stats([1.0, -1.0, 2.0, -1.0, 1.5])
        assert stats.win_rate == pytest.approx(0.6, rel=0.01)

    def test_profit_factor(self):
        from learning.r_stats import compute_r_stats
        stats = compute_r_stats([2.0, 1.0, -1.0])  # winners=3, losers=1
        assert stats.profit_factor == pytest.approx(3.0, rel=0.01)

    def test_max_consec_losses(self):
        from learning.r_stats import compute_r_stats
        stats = compute_r_stats([1.0, -1.0, -1.0, -1.0, 2.0])
        assert stats.max_consec_losses == 3

    def test_viable_requires_30_trades(self):
        from learning.r_stats import compute_r_stats
        # 10 trades with good stats — not viable yet
        stats = compute_r_stats([2.0] * 7 + [-0.5] * 3)
        assert stats.is_viable is False

    def test_viable_with_good_stats(self):
        from learning.r_stats import compute_r_stats
        # 30 trades: 20 winners @ 2R, 10 losers @ -1R → expectancy = 1.0, PF=4
        rs = [2.0] * 20 + [-1.0] * 10
        stats = compute_r_stats(rs)
        assert stats.is_viable is True

    def test_load_r_multiples_from_db(self, tmp_path: Path):
        from learning.r_stats import load_r_multiples
        conn, _ = _setup_db(tmp_path)
        _insert_closed_position(conn, 2.0)
        _insert_closed_position(conn, -1.0)
        rs = load_r_multiples(conn)
        assert len(rs) == 2
        assert 2.0 in rs
        assert -1.0 in rs

    def test_load_r_multiples_by_setup(self, tmp_path: Path):
        from learning.r_stats import load_r_multiples
        conn, _ = _setup_db(tmp_path)
        _insert_closed_position(conn, 2.0, signal_type="breakout")
        _insert_closed_position(conn, -1.0, signal_type="dip_to_support")
        rs = load_r_multiples(conn, setup_name="breakout")
        assert len(rs) == 1
        assert rs[0] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# learning/null_model.py
# ---------------------------------------------------------------------------

class TestNullModel:
    def test_insufficient_data(self):
        from learning.null_model import run_permutation_test
        result = run_permutation_test([1.0, -1.0] * 5)  # only 10 trades
        assert result.passed is False
        assert "INSUFFICIENT_DATA" in result.verdict

    def test_clear_edge_detected(self):
        from learning.null_model import run_permutation_test
        # 30 trades: 24 wins @ +2R, 6 losses @ -0.5R → expectancy = 1.5R
        rs = [2.0] * 24 + [-0.5] * 6
        result = run_permutation_test(rs, n_simulations=2000, seed=42)
        assert result.passed is True
        assert "EDGE_DETECTED" in result.verdict

    def test_random_walk_no_edge(self):
        from learning.null_model import run_permutation_test
        # Exactly 15 wins @ +1R and 15 losses @ -1R → expectancy = 0.0
        rs = [1.0] * 15 + [-1.0] * 15
        result = run_permutation_test(rs, n_simulations=1000, seed=42)
        # Zero expectancy cannot beat null model
        assert result.passed is False

    def test_p_value_between_0_and_1(self):
        from learning.null_model import run_permutation_test
        rs = [1.5, -1.0] * 15
        result = run_permutation_test(rs, n_simulations=500, seed=1)
        assert 0.0 <= result.p_value <= 1.0

    def test_verdict_string_present(self):
        from learning.null_model import run_permutation_test
        rs = [1.0] * 35
        result = run_permutation_test(rs, n_simulations=100)
        assert len(result.verdict) > 0


# ---------------------------------------------------------------------------
# learning/shadow_mode.py
# ---------------------------------------------------------------------------

class TestShadowMode:
    def _setup(self, tmp_path: Path):
        conn, _ = _setup_db(tmp_path)
        conn.execute(
            """INSERT INTO signals
               (symbol, exchange, signal_type, setup_version, action, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("AZN", "LSE", "breakout", "v1", "BUY", datetime.now(UTC).isoformat()),
        )
        conn.commit()
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn, sid

    def test_record_shadow_trade(self, tmp_path: Path):
        from learning.shadow_mode import record_shadow_trade
        conn, sid = self._setup(tmp_path)
        record_shadow_trade(conn, sid, "REJECTED")
        row = conn.execute("SELECT * FROM shadow_trades WHERE signal_id=?", (sid,)).fetchone()
        assert row is not None
        assert row["approval_decision"] == "REJECTED"

    def test_record_idempotent(self, tmp_path: Path):
        from learning.shadow_mode import record_shadow_trade
        conn, sid = self._setup(tmp_path)
        record_shadow_trade(conn, sid, "APPROVED")
        record_shadow_trade(conn, sid, "APPROVED")  # duplicate ignored
        count = conn.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE signal_id=?", (sid,)
        ).fetchone()[0]
        assert count == 1

    def test_score_shadow_trade(self, tmp_path: Path):
        from learning.shadow_mode import record_shadow_trade, score_shadow_trade
        conn, sid = self._setup(tmp_path)
        record_shadow_trade(conn, sid, "REJECTED")
        shadow_id = conn.execute("SELECT id FROM shadow_trades WHERE signal_id=?", (sid,)).fetchone()[0]
        score_shadow_trade(conn, shadow_id, 0.5, 1.5, 2.0)
        row = conn.execute("SELECT * FROM shadow_trades WHERE id=?", (shadow_id,)).fetchone()
        assert row["outcome_1d_r"] == pytest.approx(1.5)

    def test_shadow_accuracy_empty(self, tmp_path: Path):
        from learning.shadow_mode import get_shadow_accuracy
        conn, _ = _setup_db(tmp_path)
        result = get_shadow_accuracy(conn)
        assert result["n_approved"] == 0
        assert result["n_rejected"] == 0


# ---------------------------------------------------------------------------
# learning/proposals.py
# ---------------------------------------------------------------------------

class TestProposals:
    def _make_draft(self, proposal_type: str = "stop_distance"):
        from learning.proposals import ProposalDraft
        return ProposalDraft(
            proposal_type=proposal_type,
            description="Widen stop from 5% to 6% for FTSE100 shares",
            evidence="30 trades show 12% of stops triggered within 4.5-5%",
            sample_size=30,
            proposed_config={"stop_distance_pct": 0.06},
            safety_rule_check="Confirmed: 6% stop with 12% target = 2:1 R:R (passes Rule 3)",
        )

    def test_create_proposal(self, tmp_path: Path):
        from learning.proposals import create_proposal
        conn, _ = _setup_db(tmp_path)
        pid = create_proposal(conn, self._make_draft())
        assert pid > 0

    def test_invalid_type_raises(self, tmp_path: Path):
        from learning.proposals import create_proposal, ProposalDraft
        conn, _ = _setup_db(tmp_path)
        draft = self._make_draft("invalid_type")
        with pytest.raises(ValueError):
            create_proposal(conn, draft)

    def test_approve_proposal(self, tmp_path: Path):
        from learning.proposals import create_proposal, approve_proposal
        conn, _ = _setup_db(tmp_path)
        pid = create_proposal(conn, self._make_draft())
        approve_proposal(conn, pid, "trader")
        row = conn.execute("SELECT decision FROM proposals WHERE id=?", (pid,)).fetchone()
        assert row["decision"] == "APPROVED"

    def test_reject_proposal(self, tmp_path: Path):
        from learning.proposals import create_proposal, reject_proposal
        conn, _ = _setup_db(tmp_path)
        pid = create_proposal(conn, self._make_draft())
        reject_proposal(conn, pid, "trader", reason="Not enough evidence")
        row = conn.execute("SELECT decision FROM proposals WHERE id=?", (pid,)).fetchone()
        assert row["decision"] == "REJECTED"

    def test_get_pending_proposals(self, tmp_path: Path):
        from learning.proposals import create_proposal, get_pending_proposals
        conn, _ = _setup_db(tmp_path)
        create_proposal(conn, self._make_draft())
        pending = get_pending_proposals(conn)
        assert len(pending) == 1

    def test_approved_not_in_pending(self, tmp_path: Path):
        from learning.proposals import create_proposal, approve_proposal, get_pending_proposals
        conn, _ = _setup_db(tmp_path)
        pid = create_proposal(conn, self._make_draft())
        approve_proposal(conn, pid, "trader")
        assert len(get_pending_proposals(conn)) == 0


# ---------------------------------------------------------------------------
# learning/readiness.py
# ---------------------------------------------------------------------------

class TestReadiness:
    def test_empty_db_fails_all(self, tmp_path: Path):
        from learning.readiness import check_readiness
        conn, _ = _setup_db(tmp_path)
        report = check_readiness(conn)
        assert report.overall is False
        assert len(report.blockers) > 0

    def test_report_has_all_items(self, tmp_path: Path):
        from learning.readiness import check_readiness
        conn, _ = _setup_db(tmp_path)
        report = check_readiness(conn)
        names = [i.name for i in report.items]
        assert "Enough trades" in names
        assert "Positive expectancy" in names
        assert "Clean reconciliation" in names
        assert "AI spend within budget" in names

    def test_summary_string(self, tmp_path: Path):
        from learning.readiness import check_readiness
        conn, _ = _setup_db(tmp_path)
        report = check_readiness(conn)
        assert "criteria met" in report.summary

    def test_ai_spend_passes_when_zero(self, tmp_path: Path):
        from learning.readiness import check_readiness
        conn, _ = _setup_db(tmp_path)
        report = check_readiness(conn)
        ai_item = next(i for i in report.items if "AI spend" in i.name)
        assert ai_item.passed is True  # zero spend is fine

    def test_enough_trades_fails_with_few(self, tmp_path: Path):
        from learning.readiness import check_readiness
        conn, _ = _setup_db(tmp_path)
        for _ in range(5):
            _insert_closed_position(conn, 1.0)
        report = check_readiness(conn)
        trades_item = next(i for i in report.items if "trades" in i.name.lower())
        assert trades_item.passed is False

    def test_readiness_api_endpoint(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        import api.app as app_module
        from api.app import app
        from data.database import init_db
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        monkeypatch.setattr(app_module, "_DB_PATH", db_path)
        client = TestClient(app)
        resp = client.get("/api/readiness")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "overall" in data
