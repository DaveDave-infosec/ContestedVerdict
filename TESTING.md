# Testing

## How to run

```bash
pip install genlayer-test==0.29.2 genlayer-py
python -m pytest -q                                      # 54 in-process tests
python -m pytest -m onchain tests/test_onchain.py -q     # 8 read-only checks against Studio
```

The first in-process run downloads the GenVM SDK (`v0.2.16`, about 200 MB) once and can sit silent for several minutes. The direct runner never touches the network after that.

## What is real and what is stubbed

The in-process tests deploy the **actual contract files** with `gltest.direct` and call their public methods. Only the outside world is replaced:

| Stubbed | How | Why |
|---|---|---|
| LLM (`prompt_non_comparative`) | `vm._gl_call_hook` answers `ExecPromptTemplate` with a chosen model string | Lets a test feed agreeing, diverging, fenced, malformed or out-of-set output and assert what the contract's own code does with it |
| Web fetch (`gl.nondet.web.get`) | `vm._live_web_handler`, which also counts calls | Proves "fetched once" and "never refetched" |
| Cross-contract reads from the escrow | `vm._gl_call_hook` answers `CallContract` from a `FakeVerdict` returning exactly the shape of `get_verdict_binding` | The SDK allows one contract class per VM process, so the escrow is tested against a faithful fake; the real pair is proven together on chain |
| Chain time | `vm.warp` plus `gl.message_raw["datetime"]` | Deadlines are tested at the exact second |

Zero skips. On-chain tests are a separate, explicitly selected marker, not skipped tests.

## Mutation checks

Each guard was removed from the contract and the suite re-run. Each removal fails a named test:

| Guard removed | Test that fails |
|---|---|
| `contested = sl != ll` flipped | 9 verdict tests, including `test_diverging_readings_are_contested_in_code` |
| Escalation reads stored evidence (made to refetch) | `test_escalate_reads_stored_evidence_never_refetches` |
| Re-roll guard (earlier matching verdict) | `test_later_matching_verdict_cannot_bind_when_an_earlier_one_exists` |
| Verdict floor snapshot | `test_verdict_requested_before_the_case_opened_cannot_bind` |
| Deadline gate on contested settlement | `test_contested_before_deadline_reverts_and_funds_stay`, `test_escalated_unresolved_waits_then_falls_back` |

## Test to property map

### `tests/test_verdict.py` (35 cases)

| Test | Property |
|---|---|
| `test_agreeing_readings_are_not_contested` | Agreement gives `contested = false`; `resolved_label` is the agreed label; requester is caller-bound |
| `test_diverging_readings_are_contested_in_code` | Divergence gives `contested = true`; nothing actionable until escalated |
| `test_label_outside_the_set_reverts` (3) | A label outside the set, including `unresolved`, reverts and stores nothing |
| `test_fenced_json_parses_and_model_strings_never_decide_contested` | ```` ```json ```` fences parse; a model-written `"contested": "false"` or `"true"` is ignored, code decides |
| `test_unreadable_model_output_reverts` (5) | Prose, broken JSON, arrays, empty output and missing keys all revert |
| `test_exactly_one_evidence_source` | Both sources, or neither, revert before any fetch or model call |
| `test_non_pinned_evidence_url_reverts` (9) | Branch names, short or non-hex SHAs, query, fragment, trailing text, trailing slash, other hosts and plain http all revert before fetching |
| `test_oversized_inputs_revert_never_truncate` (3) | One char over the cap reverts; exactly at the cap is stored whole |
| `test_fetched_evidence_over_cap_reverts` | A fetched file over 12,000 chars reverts before the model call |
| `test_pinned_url_fetched_once_and_hash_matches_stored_text` | One fetch; stored text and its sha256 match; question and criteria hashes match; the binding view omits the text |
| `test_escalate_on_non_contested_reverts` | Clear verdicts and unknown ids cannot be escalated |
| `test_escalate_resolves_to_a_label_once` | Bystander escalation resolves; `contested` stays true as history; a second escalation reverts |
| `test_escalate_rejects_label_outside_the_set` | The forcing pass is held to the label set too |
| `test_escalate_reads_stored_evidence_never_refetches` | After the source changes (with an injection attempt), escalation still sees the stored evidence and both prior readings, with no new fetch |
| `test_escalate_unresolved_stays_unresolved` | `unresolved` leaves `resolved_label` empty |
| `test_bystander_escalation_matches_requester_escalation` | Who escalates has no effect on the outcome |
| `test_no_owner_and_no_setter` | The only write methods are `request_verdict` and `escalate`; no owner or admin field; the deployer has no special path |
| `test_requested_at_is_chain_time` | Timestamps come from the transaction, not the caller |
| `test_labels_are_normalized_and_duplicates_rejected` | Trimming and lowercasing; duplicates, singletons, `unresolved`, spaces, more than 6 and empty labels revert |

### `tests/test_escrow.py` (19 cases)

| Test | Property |
|---|---|
| `test_issuer_mint_within_cap_others_revert` | Only the issuer mints, never past `max_supply` |
| `test_binding_a_verdict_with_a_different_evidence_hash_reverts` | A friendlier document, or a different question, cannot bind |
| `test_binding_a_verdict_with_a_different_label_set_reverts` | A verdict over a different label set cannot bind |
| `test_clear_pay_pays_the_payee_with_exact_conservation` | Clear `pay` pays the payee; a bystander can settle; exact conservation |
| `test_clear_refund_refunds_the_payer` | Clear `refund` refunds the payer |
| `test_contested_before_deadline_reverts_and_funds_stay` | Contested before the deadline: settle reverts and the vault is untouched |
| `test_contested_then_escalated_to_pay_pays_the_payee` | An escalation landing after binding is honoured; source is `escalation` |
| `test_escalated_unresolved_waits_then_falls_back` | `unresolved` waits for the deadline, then the fallback applies |
| `test_contested_past_deadline_split_is_exact` (3) | `split` is exact for even, odd and 1-unit amounts; the odd unit goes to the payer; the deadline is inclusive |
| `test_double_settle_reverts` | Settles once |
| `test_second_bind_reverts` | Binds once |
| `test_later_matching_verdict_cannot_bind_when_an_earlier_one_exists` | **No re-rolls:** a later favourable sample cannot displace the first matching one |
| `test_verdict_requested_before_the_case_opened_cannot_bind` | **Floor:** a verdict computed before opening cannot bind |
| `test_unbound_case_cannot_settle_early_but_falls_back_after_deadline` | Even with no verdict at all, funds are released after the deadline |
| `test_resolved_label_still_wins_after_the_deadline` | Past the deadline, a resolved label still beats the fallback |
| `test_open_case_validation` | Self-dealing, malformed hash, zero amount, short window, unknown fallback, overdraft and bad address all revert with no state change |
| `test_no_function_accepts_a_caller_supplied_clock` | No method takes a `now`/`timestamp` argument |

### `tests/test_onchain.py` (8, read-only, live Studio pair)

| Test | Property |
|---|---|
| `test_clear_case_paid_on_an_uncontested_verdict` | Verdict 0 agreed on `pay` with two distinct reasonings; case 0 paid 1000 |
| `test_pinned_evidence_was_fetched_once_stored_and_hashed` | Verdict 1 stored the pinned URL, and its stored text is byte for byte the file in this repo |
| `test_contested_split_is_two_real_readings_decided_in_code` | Verdict 1: strict `refund`, lenient `pay`, contested, escalated to `refund` with a minority note |
| `test_contested_case_settled_only_after_escalation` | Case 1 settled `refund` from the escalation |
| `test_unescalated_contest_fell_back_to_split_after_deadline` | Case 2 split 500 / 501 after its deadline, settled by a third party |
| `test_bystander_escalation_and_late_escalation_cannot_reopen` | Verdict 2 escalated by a non-party after case 2 settled; the case stayed `split` |
| `test_every_case_bound_the_first_matching_verdict_after_opening` | Every live binding matches its case hashes and no earlier matching verdict sits above its floor |
| `test_ledger_conserves_every_unit` | Payer 8,500 + payee 1,500 + vault 0 = 10,000 minted |
