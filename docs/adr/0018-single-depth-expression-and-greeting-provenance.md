# ADR 0018: Single Execution Depth Expression and Greeting Provenance

- **Status**: ACCEPTED
- **Date**: 2026-09-30
- **Decouples from**: [ADR 0013](0013-deepen-screener-feed-pipeline-and-job-store.md) (the deep pipeline this refines), [ADR 0010](0010-single-living-greeting-prompt.md) (the Greeting Prompt the human copy is drafted from), [ADR 0012](0012-job-lifecycle-state-machine-and-cooldown-quota-decoupling.md) (the `applied` ladder this reads)
- **Issues**: #298, #299, #300, #301, #302 (root cause recorded in PR #297 / commit `a312c9a`, reported task `68pxw2b0dijvn01`)

## Context

An `AUTO_APPLY` run scored every posting, drafted a tailored greeting for each one, and
sent nothing. The payload behind the reported run said `preview_only: true, auto_send: false`
while the strategy that produced it said 自动打招呼.

The shape was the bug, not the value. The dispatch gate read two booleans that had to be
true *at the same time*, five different call sites each got to state them, and a caller that
wrote one and not the other produced a payload that looked entirely valid and silently
drafted. Making every caller "remember to pass the right pair" cannot fix a class of defect
whose definition is that somebody forgets.

Two product decisions followed, and they constrain the design:

1. An operator has exactly two execution depths — 深度存JD and 自动打招呼. "Generate it but
   keep it on the device" is not a third. Previewing a greeting is something a human does in
   the Web Dashboard, not a mode the agent runs in.
2. A preview only means something if what is previewed is what gets sent. So a greeting a
   human wrote must reach the employer verbatim, and the agent must not spend tokens
   drafting over it on the next sweep.

## Decision

### Depth is one expression, owned by the launch contract

`target_action` is the single depth statement on the wire, and `task_launch`
(+ its TypeScript mirror `web/src/lib/taskLaunch.ts`) is its only writer. Both languages
are pinned by `config/task_launch.cases.json`, which now asserts the invariant directly:
a search or targeted-application payload states depth exactly once.

The contract also *refuses* the old ways of answering the question: a caller that passes a
`LaunchMode` to a search or a 定向投递, or hand-authors either half of the legacy pair, gets
a `LaunchContractError` rather than a silent default. 拒信清扫 keeps a `mode`, because its
`dry_run` is an inbox drill rather than a greeting depth.

### The worker still reads the legacy pair, with a name on it

`FeedStreamConfig.from_payload` resolves depth in exactly one of four ways and records which:

| Payload | Resolution | `depth_expression` |
| :--- | :--- | :--- |
| no legacy keys, states `target_action` | send iff `auto_apply` | `declared_target_action` |
| both legacy keys | the old gate: `auto_send and not preview_only`, contradictions included | `legacy_preview_pair` |
| exactly one legacy key | that key with the defaults the receiving worker applied (always a draft) | `legacy_preview_half_pair` |
| no depth at all | nothing is sent, as before | `unstated_default` |

Every AUTO_APPLY run logs which shape it read (`depth='...'`), so the transition is
observable rather than assumed.

**Removal condition.** The legacy rows above exist for tasks already in somebody's queue, and
only for those. When `depth=legacy_preview` no longer appears in the fleet's task logs,
delete the pair-reading branch and the `preview_only`/`auto_send` names entirely; the
producer-side refusals stay, so nothing can write them back.

### `matched` is a draft rung, not a delivered one

The Depth Visit Rule (`models.depth_already_reached`) is separate from the Job Lifecycle
write ladder, and `TARGET_ACTION_RANK` now carries it: 深度存JD requires the enrichment rank,
自动打招呼 requires `applied`. A `matched` record — a backend "AI 评估", or a greeting a
spent daily quota held back — is therefore revisited, sent, and only then advanced. An
undelivered greeting claims nothing: no quota slot, no 直招同企避嫌 anchor.

### Greeting provenance: whose words, and what the agent owes them

`greeting_source` (`human` | `agent_draft`, empty when unknown) is a Job Record column. A
human copy is sent byte-identical with **zero** model calls — the salutation normaliser
belongs to the drafting path only — and outranks nothing: the 定向投递 modal's edited copy
outranks the record's own. Unknown provenance is not human provenance, which keeps the
rollout safe on a broker whose collection has not been migrated yet: the cost of guessing
wrong is a regenerated draft, never an unapproved send.

### A usable stored JD is read, not re-collected

`models.jd_is_usable_on_file` composes the two judgements a freshly extracted JD is already
put through (`is_substantive_jd` plus the `查看更多` / `展开` / trailing-ellipsis markers the
detail page itself checks) and applies them to the stored text. When it passes, the visit
skips the expansion, the body re-read and — for a headhunter posting — the distance probe.
It never skips the contact-control probe, the commute ceiling (an unmeasured distance is
still probed; an unknown one stays fail-open), platform-history recording, the same-employer
guard, the quota count or the cancellation probe. And it never skips *generation*: only a
human copy does that.

## Consequences

- **Positive.** "Which depth did this run actually have?" has one answer at one site, so the
  defect cannot recur by omission. A scheduled run and a hand-launched run of one strategy
  are the same task. Drafts stop being dead ends, and previews stop being rewritten.
- **Negative / accepted.** A 自动打招呼 run now genuinely sends: daily-quota consumption is
  real rather than theoretical, and the degraded path had to be re-described as "not sent
  this round, record stays re-sendable" (`JobAction.PENDING_SEND`) instead of an offline
  draft. `greeting_source` requires the `job_records` collection to be re-provisioned
  (`python3 src/boss_agent/broker/provisioner.py --url ... `); until then provenance is
  dropped on write and the human path degrades to drafting, which is the safe direction.
- **Deferred, deliberately.** A legacy 定向投递 payload that stated no depth still drafts —
  the honest reading of what its writer asked for, pinned by
  `test_a_payload_that_states_no_depth_at_all_keeps_its_old_default`.
