# Contested Verdict

**A reusable dissent primitive for GenLayer Intelligent Contracts.**

Any contract can ask Contested Verdict a question about some evidence and get back **two independently reasoned readings of the same evidence**, a strict one and a lenient one, plus a `contested` flag that is computed **in code** when they disagree. A permissionless escalation resolves contested verdicts, and the included example escrow shows how a consumer bounds the wait with a fallback, so a contested case can never lock funds forever.

| | Studio (chainId 61999) |
|---|---|
| `ContestedVerdict` | [`0x0549a4F70EDA9c759df5fbf1fDeCAD46d45bbF14`](https://explorer-studio.genlayer.com/address/0x0549a4F70EDA9c759df5fbf1fDeCAD46d45bbF14) |
| `ExampleEscrow` | [`0x8290202AcB24CB07AF74722BbBEaa2305FE629cB`](https://explorer-studio.genlayer.com/address/0x8290202AcB24CB07AF74722BbBEaa2305FE629cB) |

---

## 1. The problem: a contract cannot see the validator split

Builders keep wanting the same thing: **pay out when the judgment is clear, hold when it is shaky.**

An Intelligent Contract cannot observe that directly. It receives only the accepted leader result. How the validators voted, and what each of them reasoned, is not persisted where the contract can read it. GenLayer's guidance is that when you need multiple perspectives, you model them **explicitly, as validated fields in the result**.

So a contract that reads one confident label has no idea whether the call was easy or a coin flip. It either ignores disagreement entirely or pretends a single answer is safe.

## 2. The pattern

Contested Verdict packages the sanctioned pattern once, so every consumer gets it right:

1. **Two lenses, one evidence set.** One `prompt_non_comparative` call returns a **strict** reading (literal compliance, no benefit of the doubt) and a **lenient** reading (substantive outcome, tolerate immaterial deviations). Each picks one allowed label and must cite the evidence through its own lens. Validators check that each reading is defensible under its lens.
2. **`contested` is decided in code.** `contested = strict_label != lenient_label`. The model never sets it, and no model string is ever cast to a boolean.
3. **Evidence is fixed.** Either inline text, or a `raw.githubusercontent.com` URL pinned to a 40-hex commit SHA. A URL is fetched **once** inside `strict_eq`, stored, and hashed. Every later step, including escalation, reads the stored copy.
4. **Escalation is permissionless and single-shot.** A contested verdict can be escalated once by anyone. A forcing pass over the stored evidence and both readings picks one label or answers `unresolved`, and records a minority note for the losing side.
5. **Consumers bound the wait.** The primitive never moves funds. The example escrow shows the consumer side: act on a resolved label, wait while contested, and after a deadline on **chain time** apply a fallback both parties locked in at opening.

> **Honest framing.** The two readings are *modelled lenses inside one validated result*, not separate validator votes. That is exactly why this works on GenLayer today, and it is the pattern the docs recommend.

### Live, on chain: the same prompt agrees on clear evidence and splits on arguable evidence

**Order 17** (inline evidence, tracking_id present): both readings `pay`, not contested.

**Order 18** ([pinned evidence file](https://raw.githubusercontent.com/DaveDave-infosec/ContestedVerdict/b640285f87246942183babdf41125187430b0899/evidence/order-18.md): on time, signed by the customer, but `tracking_id: not issued`), read from `get_verdict("1")`:

| | Label | Reasoning (validators' actual words) |
|---|---|---|
| **Strict** | `refund` | "The criteria explicitly require a tracking_id in the delivery record. The evidence states 'tracking_id: not issued,' meaning this literal requirement is unmet." |
| **Lenient** | `pay` | "The purpose of the tracking_id is 'so the customer can confirm the parcel reached them.' The evidence shows the parcel was 'Received and signed by: A. Okafor' ... rendering the missing tracking_id an immaterial deviation." |
| **Escalated** | `refund` | Minority note kept on chain: "The strongest case for pay is that the criteria say the tracking_id exists so the customer can confirm receipt, and the evidence shows stronger confirmation." |

A second, independent verdict over the same pinned evidence (`get_verdict("2")`) split the same way and escalated to the same label: the ambiguity is a property of the evidence, not noise.

---

## 3. API reference: `contracts/contested_verdict.py`

No constructor arguments. No owner. No setters. Never moves funds.

### `request_verdict(question, criteria, labels_csv, evidence_url, evidence_text) -> str` (write)

| Argument | Type | Rules |
|---|---|---|
| `question` | str | 1 to 500 chars. Over cap reverts, never truncated. Stored stripped. |
| `criteria` | str | 1 to 2,000 chars. Same rules. |
| `labels_csv` | str | 2 to 6 labels, comma separated. Trimmed and lowercased. `[a-z0-9_-]`, max 32 chars each. Duplicates and the reserved word `unresolved` revert. |
| `evidence_url` | str | Either `""` or `https://raw.githubusercontent.com/<owner>/<repo>/<40-hex-sha>/<path>`. No query, fragment, whitespace or trailing slash. |
| `evidence_text` | str | Either empty or up to 12,000 chars. **Exactly one** of `evidence_url` / `evidence_text` must be given. |

Returns the `verdict_id` (decimal string). Fetched evidence over 12,000 chars reverts. Model output that is unreadable, or carries a label outside the set, reverts. `requested_at` is chain time.

### `escalate(verdict_id) -> str` (write, permissionless)

Only for contested verdicts, once per verdict. Never refetches. Returns `final_label`: one allowed label or `unresolved`. `contested` stays `true` forever as history.

### Views

| View | Returns |
|---|---|
| `get_verdict(id)` | Everything: question, criteria, labels, evidence text/url/hash, question and criteria hashes, requester, `requested_at`, both readings, `contested`, `escalated`, `final_label`, `final_reasoning`, `minority_note`, `escalated_by`, `escalated_at`, `resolved_label` |
| `get_verdict_binding(id)` | The light view for consuming contracts (no evidence text): `evidence_hash`, `question_hash`, `criteria_hash`, `labels_key`, `contested`, `escalated`, `final_label`, `resolved_label` |
| `get_verdict_count()` | int |
| `get_verdict_ids()` | list of ids |

**`resolved_label`** is the one field a consumer acts on:
- not contested: the agreed label
- contested and escalated to a label: that label
- otherwise: `""` (nothing actionable yet)

`labels_key` is the label set sorted and comma joined, so `refund,pay` and `pay,refund` bind identically.

---

## 4. How to call it from your contract

The binding check is the whole safety story. Copy it.

```python
# At opening: commit to the inputs, and snapshot the verdict counter.
floor = int(gl.get_contract_at(Address(VERDICTS)).view().get_verdict_count())
self.c_evidence_hash[cid] = evidence_hash            # sha256 of the evidence text
self.c_question_hash[cid] = sha256(question.strip())
self.c_criteria_hash[cid] = sha256(criteria.strip())
self.c_verdict_floor[cid] = u256(floor)

def _matches(self, cid, b) -> bool:
    return (b["evidence_hash"] == self.c_evidence_hash[cid]
            and b["question_hash"] == self.c_question_hash[cid]
            and b["criteria_hash"] == self.c_criteria_hash[cid]
            and b["labels_key"] == "pay,refund")

# Binding (permissionless): the FIRST matching verdict after opening wins.
v = gl.get_contract_at(Address(VERDICTS)).view()
assert vid >= floor, "verdict was requested before the case opened"
assert self._matches(cid, v.get_verdict_binding(str(vid)))
for i in range(floor, vid):
    assert not self._matches(cid, v.get_verdict_binding(str(i))), "an earlier matching verdict exists"

# Settling (permissionless): act only on resolved_label; otherwise wait, then fall back.
label = v.get_verdict_binding(bound_id)["resolved_label"]
if label in ("pay", "refund"):
    act(label)
else:
    assert self._now() >= deadline, "verdict contested, awaiting escalation"
    act(fallback)
```

Why each line exists:

| Guard | Attack it stops |
|---|---|
| Hash match on evidence, question, criteria, labels | Binding an unrelated or friendlier verdict |
| `vid >= floor` | Binding a verdict computed before the case existed |
| First match after the floor wins | **Re-rolling:** requesting the same judgment until a favourable sample appears |
| `resolved_label` only | Acting on one lens of a contested verdict |
| Deadline on `gl.message_raw["datetime"]` | A caller passing a far-future "now" to trigger the fallback early |
| Fallback fixed at opening | Either party choosing the fallback after seeing the verdict |

`contracts/example_escrow.py` is a complete, tested consumer: internal ledger, issuer-capped mint, `open_case`, `bind_verdict`, `settle`. All its cross-contract calls are view reads.

## 5. Choosing a fallback policy

| Fallback | Use when | Effect |
|---|---|---|
| `refund` | The payee carries the burden of proof (deliveries, milestones, bounties) | Arguable evidence returns funds to the payer |
| `pay` | The payer carries the burden (disputes raised against a delivered service) | Arguable evidence releases funds |
| `split` | Neither side should carry it alone | Halves; an odd unit goes to the payer |

Even after the deadline, a **resolved** label still wins if one exists when `settle` runs. The fallback applies only when nothing actionable exists. A case that settles is final: a late escalation cannot reopen it (proven live below).

## 6. Honest limitations

- **Modelled lenses, not separate votes.** The two readings come from one validated leader result. They are deliberately different lenses, not independent validators.
- **Two fixed lenses in V1.** Strict and lenient only. Custom lenses are a natural V2.
- **Evidence capped at 12,000 chars**, and URL evidence is limited to commit-pinned `raw.githubusercontent.com` files so it cannot change after the fact.
- **Escalation is one forcing pass.** It can answer `unresolved`, which is why consumers need a fallback.
- **The re-roll guard scans** verdicts between a case's floor and the bound id. Cost grows with unrelated traffic in between; bind promptly.
- **Escrow labels are fixed** to `pay` and `refund` in the example, so it never receives a label it cannot act on.

## 7. Tests

**54 in-process tests, 0 skips**, driving the real contract code with `gltest.direct` (SDK `v0.2.16`). Only the outside world is stubbed: the LLM, web fetches, and the escrow's cross-contract reads. **8 read-only on-chain tests** check the live deployment above.

```bash
pip install genlayer-test==0.29.2 genlayer-py
python -m pytest -q                                          # 54 in-process
python -m pytest -m onchain tests/test_onchain.py -q         # 8 against Studio
```

Every guard was mutation checked: removing the contested rule, the stored-evidence rule, the re-roll guard, the floor, or the deadline gate each makes a named test fail. See [`TESTING.md`](TESTING.md) for the test-to-property map.

## 8. Live receipts (Studio explorer)

| # | What it proves | Tx |
|---|---|---|
| 1 | Clear verdict, both readings `pay`, escrow pays 1000 | [`0xac8907…3956d`](https://explorer-studio.genlayer.com/tx/0xac890770be5ae7b9c0eedb395fc4c8df3668fa9ec51c3aec9b36177e92d3956d) |
| 2 | Contested verdict blocks settlement: reverts `verdict contested, awaiting escalation`, vault untouched | [`0xa79021…fc950`](https://explorer-studio.genlayer.com/tx/0xa7902160adcf7d5b0be58e6b745cc644803baa9ef9d655c0cf6bd5b5d66fc950) |
| 3 | Escalation resolves verdict 1 to `refund` with a minority note | [`0x18f593…bfa73`](https://explorer-studio.genlayer.com/tx/0x18f593f0d06dbb80fafc3747b6bca2cdccfa3ae7ac7d58d9bde29df3becbfa73) |
| 4 | Case 1 settles on the escalated label; the **payee itself** executes a refund against its own interest (settle has no discretion) | [`0x53ce9b…1a9ab`](https://explorer-studio.genlayer.com/tx/0x53ce9bb590b56684b7830c91a076a3dc143fddb4f95b96517b3792cbbba1a9ab) |
| 5 | **Bounded contestation:** unescalated contested case, deadline passed, a **third-party bystander** settles; `split` 500 / 501 | [`0x244941…658e8`](https://explorer-studio.genlayer.com/tx/0x24494186112a1a9b5d19075af78f09c377d6a8e809fef42abf9fcef4519658e8) |
| 6 | **Permissionless escalation by the same bystander**, after settlement; case 2 stays `split` (late escalation cannot reopen) | [`0x8964c2…1731e`](https://explorer-studio.genlayer.com/tx/0x8964c21872677dddb6503e27f7633989fa943956a23242822a58ce7ae9d1731e) |

Accounts: payer `0x7bbc…6a54`, payee `0xd7cd…7cc3`, bystander `0x4fb1…fbe1` (neither party, not the deployer).

Final ledger: payer 8,500 + payee 1,500 + vault 0 = 10,000 minted. Every unit accounted for.

## 9. Lineage

Contested Verdict is extracted from **Covenant V2**'s dissent mechanism, where a strict and a lenient reading of the same API checkpoint marked it contested and blocked settlement until more evidence arrived. Covenant disclosed one gap: a permanently ambiguous agreement could stay locked forever. This primitive generalises the mechanism to any question and any label set, and closes that gap with escalation plus a consumer-chosen fallback on chain time.

## Repository

```
contracts/contested_verdict.py   the primitive
contracts/example_escrow.py      the safe consumer
evidence/order-18.md             the pinned evidence used live
tests/                           54 in-process + 8 on-chain
```
