# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import datetime
import hashlib

# Example escrow that pays out on a Contested Verdict.
#
# Its job is to TEACH THE SAFE CALLING PATTERN for consuming the primitive:
#   1. Bind the case to its inputs at opening (evidence, question, criteria
#      hashes and the label set), so nobody can bind an unrelated verdict.
#   2. Snapshot the verdict counter at opening and accept only the FIRST
#      matching verdict after it, so nobody can re-roll the judgment until a
#      friendly sample appears.
#   3. Act only on a resolved label. While the verdict is contested, wait.
#   4. Bound the wait: after a deadline on CHAIN time, a fallback that both
#      parties agreed at opening applies. Contestation never locks funds forever.
#
# All cross-contract calls are view reads. Internal ledger, no external token.

VAULT_KEY = "__vault__"
LABELS_KEY = "pay,refund"       # the escrow's two actions, sorted
FALLBACKS = ["refund", "pay", "split"]
MIN_WINDOW = 60                 # seconds
MAX_WINDOW = 30 * 24 * 3600     # 30 days
MAX_QUESTION = 500
MAX_CRITERIA = 2000
HEX = "0123456789abcdef"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _addr(a: str) -> str:
    x = a.strip().lower()
    ok = len(x) == 42 and x.startswith("0x")
    if ok:
        for ch in x[2:]:
            if ch not in HEX:
                ok = False
    assert ok, "invalid address"
    return x


def _is_hash(h: str) -> bool:
    if len(h) != 64:
        return False
    for ch in h:
        if ch not in HEX:
            return False
    return True


class ExampleEscrow(gl.Contract):
    verdict_address: str
    issuer: str
    max_supply: u256
    total_minted: u256
    balances: TreeMap[str, u256]

    case_count: u256
    c_payer: TreeMap[str, str]
    c_payee: TreeMap[str, str]
    c_amount: TreeMap[str, u256]
    c_evidence_hash: TreeMap[str, str]
    c_question_hash: TreeMap[str, str]
    c_criteria_hash: TreeMap[str, str]
    c_fallback: TreeMap[str, str]
    c_opened_at: TreeMap[str, u256]
    c_deadline: TreeMap[str, u256]
    c_verdict_floor: TreeMap[str, u256]
    c_verdict_id: TreeMap[str, str]
    c_bound_by: TreeMap[str, str]
    c_status: TreeMap[str, str]
    c_outcome: TreeMap[str, str]
    c_outcome_source: TreeMap[str, str]
    c_settled_by: TreeMap[str, str]
    c_settled_at: TreeMap[str, u256]
    c_payee_out: TreeMap[str, u256]
    c_payer_out: TreeMap[str, u256]

    def __init__(self, verdict_address: str, issuer: str, max_supply: int):
        assert int(max_supply) > 0, "max_supply must be positive"
        self.verdict_address = _addr(verdict_address)
        self.issuer = _addr(issuer)
        self.max_supply = u256(int(max_supply))
        self.total_minted = u256(0)
        self.case_count = u256(0)

    # ---------- internal ----------

    def _now(self) -> int:
        """Chain time: the transaction timestamp set by consensus, never a value the caller supplies."""
        s = str(gl.message_raw["datetime"]).strip()
        base = datetime.datetime.fromisoformat(s[:19]).replace(tzinfo=datetime.timezone.utc)
        tail = s[19:]
        if tail.startswith("."):
            i = 1
            while i < len(tail) and tail[i].isdigit():
                i += 1
            tail = tail[i:]
        offset = 0
        if len(tail) >= 6 and (tail[0] == "+" or tail[0] == "-"):
            mins = int(tail[1:3]) * 60 + int(tail[4:6])
            offset = mins * 60 if tail[0] == "+" else -mins * 60
        return int(base.timestamp()) - offset

    def _sender(self) -> str:
        return gl.message.sender_address.as_hex.lower()

    def _bal(self, who: str) -> int:
        v = self.balances.get(who)
        return 0 if v is None else int(v)

    def _verdicts(self):
        return gl.get_contract_at(Address(self.verdict_address)).view()

    def _assert_case(self, case_id: str) -> None:
        ok = len(case_id) > 0 and case_id.isdigit() and str(int(case_id)) == case_id
        assert ok and int(case_id) < int(self.case_count), "unknown case"

    def _matches(self, case_id: str, b: dict) -> bool:
        return (
            str(b["evidence_hash"]) == self.c_evidence_hash[case_id]
            and str(b["question_hash"]) == self.c_question_hash[case_id]
            and str(b["criteria_hash"]) == self.c_criteria_hash[case_id]
            and str(b["labels_key"]) == LABELS_KEY
        )

    # ---------- ledger ----------

    @gl.public.write
    def mint(self, to: str, amount: int) -> None:
        assert self._sender() == self.issuer, "only the issuer may mint"
        a = int(amount)
        assert a > 0, "amount must be positive"
        assert int(self.total_minted) + a <= int(self.max_supply), "mint exceeds max supply"
        t = _addr(to)
        self.balances[t] = u256(self._bal(t) + a)
        self.total_minted = u256(int(self.total_minted) + a)

    # ---------- cases ----------

    @gl.public.write
    def open_case(
        self,
        payee: str,
        question: str,
        criteria: str,
        evidence_hash: str,
        amount: int,
        window_seconds: int,
        fallback: str,
    ) -> str:
        payer = self._sender()
        pe = _addr(payee)
        assert pe != payer, "payee must differ from payer"
        assert len(question) <= MAX_QUESTION and len(criteria) <= MAX_CRITERIA, "input over cap"
        q = question.strip()
        c = criteria.strip()
        assert len(q) > 0 and len(c) > 0, "question and criteria required"
        eh = evidence_hash.strip().lower()
        assert _is_hash(eh), "evidence_hash must be a 64-hex sha256"
        a = int(amount)
        assert a > 0, "amount must be positive"
        w = int(window_seconds)
        assert MIN_WINDOW <= w <= MAX_WINDOW, "window must be 60 seconds to 30 days"
        fb = fallback.strip().lower()
        assert fb in FALLBACKS, "fallback must be refund, pay or split"
        assert self._bal(payer) >= a, "insufficient balance"

        # Snapshot: only verdicts requested AFTER this case opens can bind to it.
        floor = int(self._verdicts().get_verdict_count())

        now = self._now()
        cid = str(int(self.case_count))
        self.balances[payer] = u256(self._bal(payer) - a)
        self.balances[VAULT_KEY] = u256(self._bal(VAULT_KEY) + a)

        self.c_payer[cid] = payer
        self.c_payee[cid] = pe
        self.c_amount[cid] = u256(a)
        self.c_evidence_hash[cid] = eh
        self.c_question_hash[cid] = _sha(q)
        self.c_criteria_hash[cid] = _sha(c)
        self.c_fallback[cid] = fb
        self.c_opened_at[cid] = u256(now)
        self.c_deadline[cid] = u256(now + w)
        self.c_verdict_floor[cid] = u256(floor)
        self.c_verdict_id[cid] = ""
        self.c_bound_by[cid] = ""
        self.c_status[cid] = "open"
        self.c_outcome[cid] = ""
        self.c_outcome_source[cid] = ""
        self.c_settled_by[cid] = ""
        self.c_settled_at[cid] = u256(0)
        self.c_payee_out[cid] = u256(0)
        self.c_payer_out[cid] = u256(0)
        self.case_count = u256(int(self.case_count) + 1)
        return cid

    @gl.public.write
    def bind_verdict(self, case_id: str, verdict_id: str) -> None:
        """Permissionless. Binds the FIRST verdict after opening whose inputs match the case."""
        self._assert_case(case_id)
        assert self.c_status[case_id] == "open", "case already settled"
        assert self.c_verdict_id[case_id] == "", "case already bound"
        ok = len(verdict_id) > 0 and verdict_id.isdigit() and str(int(verdict_id)) == verdict_id
        assert ok, "invalid verdict id"
        vid = int(verdict_id)
        floor = int(self.c_verdict_floor[case_id])
        assert vid >= floor, "verdict was requested before the case opened"

        v = self._verdicts()
        assert vid < int(v.get_verdict_count()), "unknown verdict"
        assert self._matches(case_id, v.get_verdict_binding(verdict_id)), "verdict inputs do not match the case"

        # No re-rolls: an earlier matching verdict after the floor always wins.
        i = floor
        while i < vid:
            assert not self._matches(case_id, v.get_verdict_binding(str(i))), "an earlier matching verdict exists; bind that one"
            i += 1

        self.c_verdict_id[case_id] = verdict_id
        self.c_bound_by[case_id] = self._sender()

    @gl.public.write
    def settle(self, case_id: str) -> str:
        """Permissionless. Time is chain time; no caller-supplied clock."""
        self._assert_case(case_id)
        assert self.c_status[case_id] == "open", "case already settled"
        now = self._now()
        past_deadline = now >= int(self.c_deadline[case_id])
        vid = self.c_verdict_id[case_id]

        action = ""
        source = ""
        if vid != "":
            b = self._verdicts().get_verdict_binding(vid)
            label = str(b["resolved_label"])
            if label == "pay" or label == "refund":
                action = label
                source = "escalation" if bool(b["contested"]) else "verdict"

        if action == "":
            if vid == "":
                assert past_deadline, "no verdict bound yet"
            else:
                assert past_deadline, "verdict contested, awaiting escalation"
            action = self.c_fallback[case_id]
            source = "fallback"

        amount = int(self.c_amount[case_id])
        if action == "pay":
            to_payee = amount
        elif action == "refund":
            to_payee = 0
        else:
            to_payee = amount // 2          # odd remainder goes to the payer
        to_payer = amount - to_payee

        assert self._bal(VAULT_KEY) >= amount, "vault cannot fund this case"
        payee = self.c_payee[case_id]
        payer = self.c_payer[case_id]
        self.balances[VAULT_KEY] = u256(self._bal(VAULT_KEY) - amount)
        if to_payee > 0:
            self.balances[payee] = u256(self._bal(payee) + to_payee)
        if to_payer > 0:
            self.balances[payer] = u256(self._bal(payer) + to_payer)
        assert to_payee + to_payer == amount, "conservation"

        self.c_status[case_id] = "settled"
        self.c_outcome[case_id] = action
        self.c_outcome_source[case_id] = source
        self.c_settled_by[case_id] = self._sender()
        self.c_settled_at[case_id] = u256(now)
        self.c_payee_out[case_id] = u256(to_payee)
        self.c_payer_out[case_id] = u256(to_payer)
        return action

    # ---------- views ----------

    @gl.public.view
    def get_case(self, case_id: str) -> dict:
        self._assert_case(case_id)
        k = case_id
        return {
            "case_id": k,
            "payer": self.c_payer[k],
            "payee": self.c_payee[k],
            "amount": int(self.c_amount[k]),
            "evidence_hash": self.c_evidence_hash[k],
            "question_hash": self.c_question_hash[k],
            "criteria_hash": self.c_criteria_hash[k],
            "fallback": self.c_fallback[k],
            "opened_at": int(self.c_opened_at[k]),
            "deadline": int(self.c_deadline[k]),
            "verdict_floor": int(self.c_verdict_floor[k]),
            "verdict_id": self.c_verdict_id[k],
            "bound_by": self.c_bound_by[k],
            "status": self.c_status[k],
            "outcome": self.c_outcome[k],
            "outcome_source": self.c_outcome_source[k],
            "settled_by": self.c_settled_by[k],
            "settled_at": int(self.c_settled_at[k]),
            "payee_out": int(self.c_payee_out[k]),
            "payer_out": int(self.c_payer_out[k]),
        }

    @gl.public.view
    def balance_of(self, who: str) -> int:
        return self._bal(_addr(who))

    @gl.public.view
    def get_vault_balance(self) -> int:
        return self._bal(VAULT_KEY)

    @gl.public.view
    def get_case_count(self) -> int:
        return int(self.case_count)

    @gl.public.view
    def get_supply(self) -> dict:
        return {"total_minted": int(self.total_minted), "max_supply": int(self.max_supply)}

    @gl.public.view
    def get_config(self) -> dict:
        return {"verdict_address": self.verdict_address, "issuer": self.issuer, "labels_key": LABELS_KEY}
