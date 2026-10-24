"""Shared harness for the in-process (gltest direct) tests.

The real contract code runs. Only the outside world is stubbed:
  - the LLM (ExecPromptTemplate) via vm._gl_call_hook
  - web fetches via vm._live_web_handler
  - cross-contract view reads (CallContract) via vm._gl_call_hook
"""
import datetime
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from gltest.direct import VMContext, create_test_addresses, deploy_contract

SDK = "v0.2.16"
ROOT = Path(__file__).resolve().parent.parent
VERDICT_PATH = ROOT / "contracts" / "contested_verdict.py"
ESCROW_PATH = ROOT / "contracts" / "example_escrow.py"

NOW = 1_800_000_000
SHA = "a" * 40
PINNED_URL = f"https://raw.githubusercontent.com/acme/deliveries/{SHA}/order-17.md"

QUESTION = "Did the vendor deliver order 17 as agreed?"
CRITERIA = "Delivery must arrive by Friday and include the tracking_id field."
EVIDENCE = "Order 17 delivered Thursday 14:02. Fields: order_id, carrier, signed_by."
LABELS = "pay,refund"

FORCING_MARKER = "Two readings of the same evidence disagree"


def set_chain_time(vm, ts: int) -> None:
    """Set the transaction timestamp the contract reads as chain time."""
    iso = datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    vm.warp(iso)
    gl_mod = sys.modules.get("genlayer.gl")
    if gl_mod is not None and getattr(gl_mod, "message_raw", None) is not None:
        gl_mod.message_raw["datetime"] = iso


def readings(strict, lenient, strict_reason=None, lenient_reason=None, fenced=False):
    """A two-lens model answer as the raw string the model would return."""
    body = json.dumps({
        "strict_label": strict,
        "strict_reasoning": strict_reason or f"Literal reading: the criteria name tracking_id and the evidence lists no such field, so {strict}.",
        "lenient_label": lenient,
        "lenient_reasoning": lenient_reason or f"Substantive reading: order 17 arrived Thursday, before the Friday deadline, so {lenient}.",
    })
    return f"```json\n{body}\n```" if fenced else body


def forcing(final, reason=None, minority=None):
    return json.dumps({
        "final_label": final,
        "reasoning": reason or f"The deadline was the purpose of the agreement and it was met, so {final}.",
        "minority_note": minority or "The criteria name tracking_id explicitly and it is absent.",
    })


class LLMStub:
    """Answers ExecPromptTemplate calls. Records every input it was shown."""

    def __init__(self):
        self.two_lens = readings("pay", "pay")
        self.forcing = forcing("pay")
        self.calls = 0
        self.inputs = []

    def answer(self, req):
        self.calls += 1
        t = req["ExecPromptTemplate"]
        self.inputs.append(t.get("input", ""))
        if FORCING_MARKER in t.get("task", ""):
            return self.forcing
        return self.two_lens


class WebWorld:
    """Serves web fetches and counts them."""

    def __init__(self):
        self.pages = {}
        self.calls = []

    def handle(self, data):
        url = data.get("url", "")
        self.calls.append(url)
        if url not in self.pages:
            return {"ok": {"response": {"status": 404, "headers": {}, "body": b""}}}
        return {"ok": {"response": {"status": 200, "headers": {}, "body": self.pages[url].encode("utf-8")}}}


def make_hook(llm, fake=None):
    def hook(vm, req):
        from genlayer.py import calldata
        if "ExecPromptTemplate" in req:
            return calldata.encode({"ok": llm.answer(req)})
        if "CallContract" in req and fake is not None:
            cd = req["CallContract"]["calldata"]
            result = getattr(fake, cd["method"])(*cd.get("args", []))
            return bytes([0]) + calldata.encode(result)
        return None
    return hook


def _addresses():
    raw = create_test_addresses(5)
    names = ["deployer", "requester", "bystander", "payer", "payee"]
    addr = SimpleNamespace(**dict(zip(names, raw)))
    hx = SimpleNamespace(**{n: "0x" + b.hex() for n, b in zip(names, raw)})
    return addr, hx


@contextmanager
def verdict_env():
    vm = VMContext()
    with vm.activate():
        addr, hx = _addresses()
        llm = LLMStub()
        web = WebWorld()
        vm._gl_call_hook = make_hook(llm)
        vm._live_web_handler = web.handle
        vm.sender = addr.deployer
        set_chain_time(vm, NOW)
        contract = deploy_contract(VERDICT_PATH, vm, sdk_version=SDK)
        vm.sender = addr.requester
        set_chain_time(vm, NOW)
        yield SimpleNamespace(vm=vm, llm=llm, web=web, addr=addr, hx=hx, contract=contract)


@pytest.fixture
def venv():
    with verdict_env() as env:
        yield env


def request(env, question=QUESTION, criteria=CRITERIA, labels=LABELS, url="", text=EVIDENCE):
    return env.contract.request_verdict(question, criteria, labels, url, text)


# ---------------- escrow harness ----------------

def sha(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class FakeVerdict:
    """Answers the escrow's cross-contract view reads exactly as ContestedVerdict would."""

    def __init__(self):
        self.rows = []

    def add(self, resolved="pay", contested=False, escalated=False, final="",
            evidence=EVIDENCE, question=QUESTION, criteria=CRITERIA, labels_key="pay,refund"):
        self.rows.append({
            "evidence_hash": sha(evidence),
            "question_hash": sha(question),
            "criteria_hash": sha(criteria),
            "labels_key": labels_key,
            "contested": contested,
            "escalated": escalated,
            "final_label": final,
            "resolved_label": resolved,
        })
        return str(len(self.rows) - 1)

    def escalate(self, vid, final):
        r = self.rows[int(vid)]
        r["escalated"] = True
        r["final_label"] = final
        r["resolved_label"] = "" if final == "unresolved" else final

    # views the escrow calls
    def get_verdict_count(self):
        return len(self.rows)

    def get_verdict_binding(self, vid):
        return dict(self.rows[int(vid)])


AMOUNT = 1000
WINDOW = 3600
MAX_SUPPLY = 1_000_000


@contextmanager
def escrow_env():
    vm = VMContext()
    with vm.activate():
        addr, hx = _addresses()
        fake = FakeVerdict()
        llm = LLMStub()
        vm._gl_call_hook = make_hook(llm, fake)
        vm.sender = addr.deployer
        set_chain_time(vm, NOW)
        verdict_hx = "0x" + "cd" * 20
        contract = deploy_contract(ESCROW_PATH, vm, verdict_hx, hx.deployer, MAX_SUPPLY, sdk_version=SDK)
        set_chain_time(vm, NOW)
        env = SimpleNamespace(vm=vm, fake=fake, addr=addr, hx=hx, contract=contract, verdict=verdict_hx)
        contract.mint(hx.payer, 10_000)
        yield env


@pytest.fixture
def eenv():
    with escrow_env() as env:
        yield env


def open_case(env, amount=AMOUNT, fallback="refund", window=WINDOW, evidence=EVIDENCE):
    with env.vm.prank(env.addr.payer):
        return env.contract.open_case(env.hx.payee, QUESTION, CRITERIA, sha(evidence), amount, window, fallback)


def total(env):
    c = env.contract
    return c.balance_of(env.hx.payer) + c.balance_of(env.hx.payee) + c.get_vault_balance()
