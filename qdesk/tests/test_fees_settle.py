"""common/fees.py + common/settle.py (INTERFACES §5): fee rounding at p=0.5/0.97, 5-dp rounding,
rebate tiers, settle_pnl pair arithmetic, Brier."""
import pytest

fees = pytest.importorskip("qdesk.common.fees")
settle = pytest.importorskip("qdesk.common.settle")


def test_taker_fee_examples():
    assert fees.taker_fee(0.5, 100) == pytest.approx(1.75, abs=1e-9)
    assert fees.taker_fee(0.97, 100) == pytest.approx(0.2037, abs=1e-9)
    assert fees.taker_fee(0.03, 100) == pytest.approx(0.2037, abs=1e-9)      # symmetric in p


def test_taker_fee_rounds_to_5dp():
    for p, s in ((0.333, 7), (0.123, 11.111111), (0.5, 0.33333), (0.81, 2.7182818)):
        f = fees.taker_fee(p, s)
        assert f == round(0.07 * p * (1 - p) * s, 5)
        assert f == round(f, 5)
    assert fees.taker_fee(0.5, 0) == 0.0


def test_maker_fee_and_rebate():
    assert fees.maker_fee(0.5, 100) == 0.0
    assert fees.maker_fee(0.97, 1) == 0.0
    assert fees.maker_rebate(0.5, 100) == pytest.approx(0.20 * 1.75, abs=1e-6)


def test_taker_rebate_tiers():
    assert fees.taker_rebate_pct(0) == ("None", 0.0)
    assert fees.taker_rebate_pct(1999.99) == ("None", 0.0)
    assert fees.taker_rebate_pct(2000) == ("Bronze", 0.03)
    assert fees.taker_rebate_pct(1e12) == ("Obsidian", 0.50)
    pcts = [fees.taker_rebate_pct(v)[1] for v in (0, 2e3, 2e4, 2e5, 1e6, 4e6, 1e7, 1e9)]
    assert pcts == sorted(pcts) and 0.0 <= min(pcts) and max(pcts) == 0.50


def test_settle_single_leg():
    fee = fees.taker_fee(0.45, 10)
    r = settle.settle_pnl([{"side": "Up", "price": 0.45, "size": 10, "fee": fee}], "Up")
    assert r["cost"] == pytest.approx(4.5) and r["payout"] == pytest.approx(10.0)
    assert r["fees"] == pytest.approx(fee) and r["pairs"] == 0
    assert r["net"] == pytest.approx(10 - 4.5 - fee, abs=1e-6)
    r = settle.settle_pnl([{"side": "Up", "price": 0.45, "size": 10, "fee": fee}], "Down")
    assert r["payout"] == 0 and r["net"] == pytest.approx(-4.5 - fee, abs=1e-6)


def test_settle_pairs():
    legs = [{"side": "Up", "price": 0.48, "size": 10, "fee": 0}, {"side": "Down", "price": 0.49, "size": 10, "fee": 0}]
    for w in ("Up", "Down"):
        r = settle.settle_pnl(legs, w)
        assert r["pairs"] == pytest.approx(10) and r["cost"] == pytest.approx(9.7)
        assert r["payout"] == pytest.approx(10) and r["net"] == pytest.approx(0.3, abs=1e-6)
    legs = [{"side": "Up", "price": 0.48, "size": 10, "fee": 0.1}, {"side": "Down", "price": 0.49, "size": 4, "fee": 0.05}]
    r = settle.settle_pnl(legs, "Down")
    assert r["pairs"] == pytest.approx(4) and r["payout"] == pytest.approx(4)
    assert r["net"] == pytest.approx(4 - (4.8 + 1.96) - 0.15, abs=1e-6)
    assert settle.settle_pnl([], "Up")["net"] == 0


def test_brier():
    assert settle.brier(1.0, "Up") == 0
    assert settle.brier(0.0, "Up") == 1
    assert settle.brier(0.5, "Up") == pytest.approx(0.25) and settle.brier(0.5, "Down") == pytest.approx(0.25)
    assert settle.brier(0.7, "Down") == pytest.approx(0.49)
    assert settle.brier(0.7, "Up") == pytest.approx(0.09)
