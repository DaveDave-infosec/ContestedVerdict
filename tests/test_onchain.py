"""Read-only checks against the LIVE pair on GenLayer Studio (chainId 61999).

Keyless: a throwaway unfunded keypair only supplies a caller address for view calls.
Nothing here writes. Run explicitly:

    python -m pytest -m onchain tests/test_onchain.py -q
"""
import hashlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.onchain

VERDICT = "0x0549a4F70EDA9c759df5fbf1fDeCAD46d45bbF14"
ESCROW = "0x8290202AcB24CB07AF74722BbBEaa2305FE629cB"
PAYER = "0x7bbcac9c77aabc2aca19cd34f944fbc015f06a54"
PAYEE = "0xd7cd43cbc01f767a74909943cec29e20db4e7cc3"
PINNED = (
    "https://raw.githubusercontent.com/DaveDave-infosec/ContestedVerdict/"
    "b640285f87246942183babdf41125187430b0899/evidence/order-18.md"
)
EVIDENCE_FILE = Path(__file__).resolve().parent.parent / "evidence" / "order-18.md"


@pytest.fixture(scope="module")
def chain():
    from genlayer_py import create_account, create_client
    from genlayer_py.chains import studionet
    client = create_client(chain=studionet, account=create_account())

    def read(address, fn, *args):
        return client.read_contract(address=address, function_name=fn, args=list(args))

    return read


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_clear_case_paid_on_an_uncontested_verdict(chain):
    v = chain(VERDICT, "get_verdict", "0")
    assert v["contested"] is False
    assert v["strict_label"] == v["lenient_label"] == v["resolved_label"] == "pay"
    assert v["strict_reasoning"] != v["lenient_reasoning"]
    c = chain(ESCROW, "get_case", "0")
    assert c["status"] == "settled" and c["outcome"] == "pay" and c["outcome_source"] == "verdict"
    assert int(c["payee_out"]) == 1000 and int(c["payer_out"]) == 0


def test_pinned_evidence_was_fetched_once_stored_and_hashed(chain):
    v = chain(VERDICT, "get_verdict", "1")
    assert v["evidence_url"] == PINNED
    assert v["evidence_hash"] == sha(v["evidence_text"])
    # The stored text is byte-for-byte the file committed in this repo at that SHA.
    assert v["evidence_text"] == EVIDENCE_FILE.read_bytes().decode("utf-8")


def test_contested_split_is_two_real_readings_decided_in_code(chain):
    v = chain(VERDICT, "get_verdict", "1")
    assert v["contested"] is True
    assert v["strict_label"] == "refund" and v["lenient_label"] == "pay"
    assert v["strict_reasoning"] != v["lenient_reasoning"]
    assert v["escalated"] is True and v["final_label"] == "refund" and v["resolved_label"] == "refund"
    assert v["minority_note"] != ""


def test_contested_case_settled_only_after_escalation(chain):
    c = chain(ESCROW, "get_case", "1")
    assert c["status"] == "settled"
    assert c["outcome"] == "refund" and c["outcome_source"] == "escalation"
    assert int(c["payer_out"]) == 1000 and int(c["payee_out"]) == 0


def test_unescalated_contest_fell_back_to_split_after_deadline(chain):
    c = chain(ESCROW, "get_case", "2")
    assert c["status"] == "settled" and c["outcome"] == "split" and c["outcome_source"] == "fallback"
    assert int(c["payee_out"]) == 500 and int(c["payer_out"]) == 501      # odd unit to payer
    assert int(c["settled_at"]) >= int(c["deadline"])
    assert c["settled_by"] not in (PAYER, PAYEE)                          # third-party settle


def test_bystander_escalation_and_late_escalation_cannot_reopen(chain):
    c = chain(ESCROW, "get_case", "2")
    v = chain(VERDICT, "get_verdict", "2")
    assert v["contested"] is True and v["escalated"] is True
    assert v["escalated_by"] not in (PAYER, PAYEE)                        # permissionless escalate
    assert int(v["escalated_at"]) > int(c["settled_at"])                  # escalated after settlement
    assert c["outcome"] == "split"                                        # and the case stayed settled


def test_every_case_bound_the_first_matching_verdict_after_opening(chain):
    for cid in ("0", "1", "2"):
        c = chain(ESCROW, "get_case", cid)
        vid = int(c["verdict_id"])
        floor = int(c["verdict_floor"])
        assert vid >= floor
        b = chain(VERDICT, "get_verdict_binding", str(vid))
        for k in ("evidence_hash", "question_hash", "criteria_hash"):
            assert b[k] == c[k]
        assert b["labels_key"] == "pay,refund"
        for earlier in range(floor, vid):
            e = chain(VERDICT, "get_verdict_binding", str(earlier))
            assert not all(e[k] == c[k] for k in ("evidence_hash", "question_hash", "criteria_hash"))


def test_ledger_conserves_every_unit(chain):
    supply = chain(ESCROW, "get_supply")
    assert int(chain(ESCROW, "get_vault_balance")) == 0
    payer = int(chain(ESCROW, "balance_of", PAYER))
    payee = int(chain(ESCROW, "balance_of", PAYEE))
    assert payer == 8500 and payee == 1500
    assert payer + payee == int(supply["total_minted"]) == 10000
