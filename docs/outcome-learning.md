# Learning from executed work

The outcome worker writes and runs Python programs for explicitly enrolled jobs.
It supports data processing, numerical work, file transformation, visualization
and other bounded offline work through the same execution interface. This is
separate from the original neural worker and its uninterrupted history.

## The loop

1. A host enrolls a specification, source hash, input files and independent checker.
2. The reasoner receives relevant accepted and rejected experience from earlier
   jobs. Ordinary retrieval is deterministic and bounded.
3. It proposes a complete program and smoke checks. Actual execution feedback
   permits one repair, within the same per-job allowance.
4. The host commits the proposal and runs its own acceptance check. A model's
   explanation or successful self-test cannot mark the job accepted.
5. The program, outcome and proposed lesson enter durable, source-bound history.
   Later jobs automatically consult that history.
6. Conditional skill notes can preserve source-linked operational lessons.
   An optional GEPA qualification uses actual training outcomes to propose a
   reusable execution policy. Separate validation selects it; ties retain the
   original. Explicit activation loads the qualified artifact for subsequent work.

Accepted programs remain concrete execution evidence. Reuse through context and
policy learning changes future model input; it does not update the foundation
model's weights. A proposed lesson from a failed task remains a rejected
experience, never a verified skill.

## Execution and recovery

Generated programs receive no credentials. Network and unrelated user-file
access are blocked. Native macOS Seatbelt
or Linux bubblewrap isolates execution; an unavailable backend fails closed.
Each execution gets a fresh writable job directory and explicitly enrolled
read-only inputs. CPU, wall time, open files, file size and captured output have
finite bounds. System Python and enrolled packages remain readable. This is a
finite research backend, not a public multi-tenant code-hosting service.

Identical completed job IDs replay their receipt without another model request
or execution. A changed contract cannot reuse the ID. An interrupted job stays
unresolved until host reconciliation; it is not automatically retried. The
outcome journal binds committed programs and acceptance records. Its local
hashes check consistency, not external attestation.

The optional delayed-check mode commits all proposals before terminal feedback
is disclosed. Held-out evaluation therefore cannot quietly become training.
Ordinary Python unit tests provide acceptance under their specified assertions;
they are not an adversarially secure proof of arbitrary program correctness.

## Use

Install the isolated research requirements and an OS execution backend. Hosted
reasoning needs the explicit private configuration described in
[adaptive reasoning](adaptive-reasoning.md). No provider is discovered from the
environment. The existing durable allowance covers every request and ambiguous
liability; reopening the worker does not renew it.

```sh
python -m scripts.run_outcome_work job.json run/work --config private-client.json
python -m scripts.run_outcome_course enrollment.json run/work --config private-client.json
python -m scripts.learn_outcome_policy course.json run/work/outcomes.sqlite \
  run/policy.json --config private-client.json
python -m scripts.run_outcome_work next-job.json run/work \
  --config private-client.json --policy run/policy.json
```

The job manifest contains `job_id` and `task`. The task includes `task_id`,
`source_sha256`, `prompt`, `checker` and optional text `inputs`. Input names are
simple filenames exposed under `inputs/`. The qualification course supplies
disjoint `training` and `validation` task lists. Checker enrollment is a trusted
host operation; it is never delegated to the proposing model.

The bounded course command consumes an explicit `jobs` list against one shared
history. Each later job consults earlier checked outcomes automatically. It
creates a separate course receipt; repeating the same enrollment preserves the
original outcomes without duplicate inference or execution.

The first broad comparison passed 83/112 external tasks with conditional memory,
versus 81/112 for both simpler controls. That difference failed the improvement
gate. The original policy remains selected; the experimental memory is not
silently promoted. Three actual project-audit jobs separately passed and replayed
without new requests. See the complete linked evidence below.

No continuous service, public execution endpoint, financial authority or
production activation is added by these commands. The
[comparison package](../research/outcome-learning) separates executable-loop
evidence from any claim of improved decisions.
