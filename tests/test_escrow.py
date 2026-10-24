"""ExampleEscrow: the safe calling pattern for consuming a Contested Verdict."""
import ast

import pytest

from conftest import (
    AMOUNT, ESCROW_PATH, MAX_SUPPLY, NOW, WINDOW,
    open_case, set_chain_time, total,
)


def bind(env, cid, vid, who="bystander"):
    with env.vm.prank(getattr(env.addr, who)):
        env.contract.bind_verdict(cid, vid)


def settle(env, cid, who="bystander"):
    with env.vm.prank(getattr(env.addr, who)):
        return env.contract.settle(cid)


# 16
def test_issuer_mint_within_cap_others_revert(eenv):
    c = eenv.contract
    assert c.balance_of(eenv.hx.payer) == 10_000
    with eenv.vm.prank(eenv.addr.bystander):
        with pytest.raises(AssertionError):
            c.mint(eenv.hx.bystander, 1)
    with pytest.raises(AssertionError):
        c.mint(eenv.hx.payer, MAX_SUPPLY - 10_000 + 1)
    c.mint(eenv.hx.payer, MAX_SUPPLY - 10_000)
    assert c.get_supply() == {"total_minted": MAX_SUPPLY, "max_supply": MAX_SUPPLY}


# 17
def test_binding_a_verdict_with_a_different_evidence_hash_reverts(eenv):
    cid = open_case(eenv)
    vid = eenv.fake.add(evidence="a friendlier document")
    with pytest.raises(AssertionError):
        bind(eenv, cid, vid)
    other = eenv.fake.add(question="Was anything delivered at all?")
    with pytest.raises(AssertionError):
        bind(eenv, cid, other)
    assert eenv.contract.get_case(cid)["verdict_id"] == ""


# 18
def test_binding_a_verdict_with_a_different_label_set_reverts(eenv):
    cid = open_case(eenv)
    vid = eenv.fake.add(labels_key="pay,partial,refund")
    with pytest.raises(AssertionError):
        bind(eenv, cid, vid)


# 19
def test_clear_pay_pays_the_payee_with_exact_conservation(eenv):
    before = total(eenv)
    cid = open_case(eenv)
    assert eenv.contract.get_vault_balance() == AMOUNT
    bind(eenv, cid, eenv.fake.add(resolved="pay"))
    assert settle(eenv, cid) == "pay"
    case = eenv.contract.get_case(cid)
    assert case["outcome_source"] == "verdict" and case["payee_out"] == AMOUNT and case["payer_out"] == 0
    assert eenv.contract.balance_of(eenv.hx.payee) == AMOUNT
    assert eenv.contract.get_vault_balance() == 0 and total(eenv) == before
    assert case["settled_by"] == eenv.hx.bystander.lower()     # permissionless


# 20
def test_clear_refund_refunds_the_payer(eenv):
    cid = open_case(eenv)
    bind(eenv, cid, eenv.fake.add(resolved="refund"))
    assert settle(eenv, cid) == "refund"
    assert eenv.contract.balance_of(eenv.hx.payer) == 10_000
    assert eenv.contract.balance_of(eenv.hx.payee) == 0


# 21
def test_contested_before_deadline_reverts_and_funds_stay(eenv):
    cid = open_case(eenv, fallback="pay")
    bind(eenv, cid, eenv.fake.add(resolved="", contested=True))
    set_chain_time(eenv.vm, NOW + WINDOW - 1)
    with pytest.raises(AssertionError):
        settle(eenv, cid)
    assert eenv.contract.get_vault_balance() == AMOUNT
    assert eenv.contract.get_case(cid)["status"] == "open"


# 22
def test_contested_then_escalated_to_pay_pays_the_payee(eenv):
    cid = open_case(eenv)
    vid = eenv.fake.add(resolved="", contested=True)
    bind(eenv, cid, vid)
    eenv.fake.escalate(vid, "pay")                 # escalation lands after binding
    assert settle(eenv, cid) == "pay"
    assert eenv.contract.get_case(cid)["outcome_source"] == "escalation"
    assert eenv.contract.balance_of(eenv.hx.payee) == AMOUNT


def test_escalated_unresolved_waits_then_falls_back(eenv):
    cid = open_case(eenv, fallback="refund")
    vid = eenv.fake.add(resolved="", contested=True)
    bind(eenv, cid, vid)
    eenv.fake.escalate(vid, "unresolved")
    with pytest.raises(AssertionError):
        settle(eenv, cid)
    set_chain_time(eenv.vm, NOW + WINDOW)
    assert settle(eenv, cid) == "refund"
    assert eenv.contract.get_case(cid)["outcome_source"] == "fallback"


# 23
@pytest.mark.parametrize("amount,to_payee,to_payer", [(1000, 500, 500), (1001, 500, 501), (1, 0, 1)])
def test_contested_past_deadline_split_is_exact(eenv, amount, to_payee, to_payer):
    before = total(eenv)
    cid = open_case(eenv, amount=amount, fallback="split")
    bind(eenv, cid, eenv.fake.add(resolved="", contested=True))
    set_chain_time(eenv.vm, NOW + WINDOW)          # deadline is inclusive
    assert settle(eenv, cid) == "split"
    case = eenv.contract.get_case(cid)
    assert case["payee_out"] == to_payee and case["payer_out"] == to_payer
    assert case["outcome_source"] == "fallback"
    assert eenv.contract.balance_of(eenv.hx.payee) == to_payee
    assert total(eenv) == before and eenv.contract.get_vault_balance() == 0


# 24
def test_double_settle_reverts(eenv):
    cid = open_case(eenv)
    bind(eenv, cid, eenv.fake.add(resolved="pay"))
    settle(eenv, cid)
    with pytest.raises(AssertionError):
        settle(eenv, cid)
    assert eenv.contract.balance_of(eenv.hx.payee) == AMOUNT


# 25
def test_second_bind_reverts(eenv):
    cid = open_case(eenv)
    first = eenv.fake.add(resolved="refund")
    bind(eenv, cid, first)
    second = eenv.fake.add(resolved="pay")
    with pytest.raises(AssertionError):
        bind(eenv, cid, second, who="payee")
    assert eenv.contract.get_case(cid)["verdict_id"] == first


# 26
def test_later_matching_verdict_cannot_bind_when_an_earlier_one_exists(eenv):
    """No re-rolls: requesting the same judgment again until it says pay gains nothing."""
    cid = open_case(eenv)
    unfavourable = eenv.fake.add(resolved="refund")
    eenv.fake.add(evidence="unrelated case")       # non-matching noise in between
    favourable = eenv.fake.add(resolved="pay")
    with pytest.raises(AssertionError):
        bind(eenv, cid, favourable, who="payee")
    bind(eenv, cid, unfavourable)
    assert settle(eenv, cid) == "refund"


# 27
def test_verdict_requested_before_the_case_opened_cannot_bind(eenv):
    early = eenv.fake.add(resolved="pay")          # pre-computed before opening
    cid = open_case(eenv)
    assert eenv.contract.get_case(cid)["verdict_floor"] == 1
    with pytest.raises(AssertionError):
        bind(eenv, cid, early, who="payee")
    fresh = eenv.fake.add(resolved="pay")
    bind(eenv, cid, fresh)
    assert eenv.contract.get_case(cid)["verdict_id"] == fresh


def test_unbound_case_cannot_settle_early_but_falls_back_after_deadline(eenv):
    cid = open_case(eenv, fallback="refund")
    with pytest.raises(AssertionError):
        settle(eenv, cid)
    set_chain_time(eenv.vm, NOW + WINDOW)
    assert settle(eenv, cid) == "refund"
    assert eenv.contract.balance_of(eenv.hx.payer) == 10_000


def test_resolved_label_still_wins_after_the_deadline(eenv):
    cid = open_case(eenv, fallback="refund")
    vid = eenv.fake.add(resolved="", contested=True)
    bind(eenv, cid, vid)
    set_chain_time(eenv.vm, NOW + WINDOW + 10)
    eenv.fake.escalate(vid, "pay")
    assert settle(eenv, cid) == "pay"


def test_open_case_validation(eenv):
    with eenv.vm.prank(eenv.addr.payer):
        c = eenv.contract
        from conftest import CRITERIA, QUESTION, EVIDENCE, sha
        h = sha(EVIDENCE)
        for args in [
            (eenv.hx.payer, QUESTION, CRITERIA, h, 10, WINDOW, "refund"),          # payee == payer
            (eenv.hx.payee, QUESTION, CRITERIA, "abc", 10, WINDOW, "refund"),      # bad hash
            (eenv.hx.payee, QUESTION, CRITERIA, h, 0, WINDOW, "refund"),           # zero amount
            (eenv.hx.payee, QUESTION, CRITERIA, h, 10, 59, "refund"),              # window too short
            (eenv.hx.payee, QUESTION, CRITERIA, h, 10, WINDOW, "coinflip"),        # bad fallback
            (eenv.hx.payee, QUESTION, CRITERIA, h, 10_001, WINDOW, "refund"),      # over balance
            ("0x1234", QUESTION, CRITERIA, h, 10, WINDOW, "refund"),               # bad address
        ]:
            with pytest.raises(AssertionError):
                c.open_case(*args)
    assert eenv.contract.get_case_count() == 0
    assert eenv.contract.balance_of(eenv.hx.payer) == 10_000


def test_no_function_accepts_a_caller_supplied_clock():
    tree = ast.parse(ESCROW_PATH.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ExampleEscrow")
    for f in cls.body:
        if isinstance(f, ast.FunctionDef):
            names = [a.arg for a in f.args.args]
            assert not any(n in ("now", "timestamp", "time", "current_time") for n in names), f.name
