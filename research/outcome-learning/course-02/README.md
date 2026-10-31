# Fresh-task outcome learning

**The conditional playbook scored slightly higher, but did not demonstrate a
reliable advantage over either simpler agent.** No learned policy was promoted.

| Method                             | Accepted / 112 |                 Provider calls | Configured inference cost |
| ---------------------------------- | -------------: | -----------------------------: | ------------------------: |
| Ordinary retrieval and self-checks |             81 |                            114 |                 $1.158132 |
| One-reflection learner             |             81 |                            112 |                 $1.337606 |
| Conditional outcome playbook       |             83 |                            113 |                 $1.768448 |
| GEPA-selected policy               |             81 | Shared with ordinary retrieval |       No duplicate charge |

The playbook won three tasks and lost one against ordinary retrieval: one-sided
exact paired p=0.3125, or 0.9375 after the three-comparison correction. Against
single reflection it won two and lost none: p=0.25, adjusted 0.75. Neither
cleared the prospectively specified threshold. Additional context cost more;
this is not a cost-matched improvement claim.

## What executed

Each agent wrote complete Python programs and smoke checks, ran those checks
in an OS-isolated workspace, and could repair once. A separate host checker
then ran the unchanged upstream unit tests. All 336 physical proposals were
committed before terminal test feedback was disclosed. The duplicate GEPA
condition shares the identical base execution and is explicitly marked as an
alias throughout the evidence.

The 112 tasks cover tabular processing, plotting, numerical work, structured
data, files and archives, text, and collection algorithms: sixteen per family.
They come from BigCodeBench v0.1.4, revision
`b74c0d0bf70d2c0bc459be537895cca163007f1a`. Selection used only task IDs and
library metadata with a fixed hash ordering. No test performance, winning seed
or hand-selected task replacement affected the cohort.

The full task specification was given to every method. Reference solutions
and test bodies were not sent to the model. These are externally authored tasks
held out from this experiment, not a claim that a pretrained model never saw
the public benchmark. The normalized five-word overlap audit is descriptive;
it did not filter or replace tasks.

## Learning and qualification

Twenty of the 24 complete-spec training programs passed their independent
checks. Every method received the same frozen history through the same bounded
ordinary retrieval. Single reflection condensed the outcomes into a general
policy. The conditional learner created twelve source-linked skill notes;
their origins and observed acceptance are retained. Notes are transferable
hypotheses, not newly verified executions by themselves.

GEPA evaluated two revisions. Original, revised and conditional-memory policies
all scored 12/16 on validation. The simpler original policy therefore remained
the runtime selection before testing. The fresh playbook difference also
failed its qualification gate, so that selection remains unchanged.

The model was `gpt-6.1-sol`, low reasoning effort, with the same registered
execution tools and ceilings: two calls and 2,400 completion tokens per call.
Actual usage differs and is reported rather than treated as equal compute.
Policy-search and curation work consumed additional development resources.
No foundation-model weights or original Brian2 history were changed.

## Independent checking

The native replay executed all **336 distinct test programs** again and matched
every recorded acceptance result. Linux CI independently runs the same package
inside bubblewrap. The original tests remain intact. Three of the 112 reference
programs failed preflight in the enrolled environment; those tasks remain in
the denominator and their complete evidence is published.

```sh
python -m pip install -r research/outcome-learning/requirements.txt
python -m scripts.verify_outcome_course research/outcome-learning/course-02 \
  --workspace run/outcome-replay
```

Use a new workspace. The command requires macOS Seatbelt or Linux bubblewrap
with working namespace isolation. It makes no model or blockchain requests.
Ordinary unit-test acceptance is not an adversarial proof of arbitrary program
correctness.

## Evidence map

| File                                    | Contents                                                      |
| --------------------------------------- | ------------------------------------------------------------- |
| `protocol.json`, amendment files        | Rules published before test inference                         |
| `public-policy-freeze-before-test.json` | GitHub commit and exact policy-byte readback                  |
| `test-execution-freeze.json`            | Task identities, source hashes, policies and common history   |
| `upstream-tasks.jsonl.gz`               | Original selected upstream records                            |
| `tasks.jsonl.gz`                        | Enrolled specifications and independent checkers              |
| `executions.jsonl.gz`                   | All programs, attempts, outcomes and explicit aliases         |
| `provider-text.jsonl.gz`                | Visible model requests/replies, excluding private identifiers |
| `outcome-playbook.json`                 | Twelve conditional notes and actual source episodes           |
| `held-out-results.json`                 | Complete scores, paired comparisons and failed gates          |
| `inference-costs.json`                  | Actual parsed token counts at configured uncached rates       |
| `native-replay.json`                    | Independent re-execution of every physical proposal           |

Costs are configured-rate accounting, not a provider invoice. This course
establishes a functioning execution and memory path, not neural superiority,
consciousness, live token learning or autonomous financial purchases. The
[actual Nuria work sequence](../project-work-01) separately demonstrates useful
project outputs and duplicate-job recovery.
