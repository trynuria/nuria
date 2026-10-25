# Grounded hybrid agent: prospective comparison

This experiment asks whether checked experience improves a fixed pretrained
reasoner's analytical work, and whether a Brian2 inspection strategy contributes
beyond a conventional controller. It is a new capability path, separate from the
retained text-program synthesis experiments. **No comparative capability gain is
claimed by this prospective specification.**

## Capability and boundaries

The agent receives a natural-language question and a registered database identity.
It can inspect tables, read registered knowledge and execute bounded read-only
SQLite queries. It must execute the exact query it commits as its answer. A
host-grounded working-evidence index preserves observations when older chat turns
leave the prompt. The model cannot add facts to that index by assertion.

The fixed model is [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B), using
the pinned [MLX four-bit conversion](runtime/model-manifest.json). Model weights
remain outside this repository. The isolated inference dependency lock is for
macOS ARM64 and Python 3.12; it is not the application's production dependency
set. Loading this model for development does not provision production inference.

Checked outcomes can qualify procedural experience for later retrieval. A
procedure annotation is model-authored and remains a reuse candidate, rather than
a universally valid rule. Failed outcomes are retained. The model's foundation
weights do not change during this experiment.

The optional Brian2 bridge runs a separate 1,024-neuron laboratory circuit. Actual
action-region spikes and declared tool utilities determine an enforced first
inspection tool. Delayed terminal reward is applied to the saved originating
eligibility trace. This establishes a measurable control path; its benefit must
be established by comparison.

## Fixed comparison

All six branches use the same pinned model, tools, teaching order and reserved
course. Each branch starts with its own empty experience history. The neural
branches also start with separate circuit histories.

| Branch        | Persistent retrieval    | First inspection                |
| ------------- | ----------------------- | ------------------------------- |
| Stateless     | None                    | Model chooses                   |
| Episodes      | Checked episode records | Model chooses                   |
| Procedures    | Checked procedures      | Model chooses                   |
| Schema first  | None                    | Host supplies schema inspection |
| Hybrid fixed  | Checked procedures      | Fixed table-list inspection     |
| Hybrid neural | Checked procedures      | Brian2-informed inspection      |

The selected official [Spider](https://yale-lily.github.io/spider) cohort has six
databases: two for six development questions, two for eight teaching questions,
and two for twelve reserved questions. Database identities and task identifiers
are selected before the reserved bodies are opened. A separate frozen protocol
binds the selected artifacts, source identities, model and limits before the
reserved comparison begins.

The runner visits all six branches for each question, rotating their execution
order across questions. A 36,000-completion-token headroom check precedes each
six-job block. Teaching feedback is released only after all six proposals in that
block are committed. Reserved terminal labels are withheld until the complete
reserved inference phase ends; they cannot influence later reserved proposals.
Reference SQL and expected rows remain outside the agent's tool surface.

If a resource limit stops the run early, only completely committed balanced
reserved blocks receive descriptive grading after the stop. That grading does not
settle retrievable experience or apply neural credit. All 120 planned branch/job
records remain in the selected denominator, including unexecuted work.

The checker compares complete finite execution results, retaining duplicate rows,
NULL values, value types, column count and required ordering. This is not the full
official Spider metric or proof of universal SQL equivalence. Spider has been
public since 2018 and may occur in model pretraining. This comparison tests the
agent configuration around a frozen backbone, not newly acquired language
knowledge or uncontaminated benchmark performance. Spider-derived materials retain
their [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) attribution.

## Development check

The latest six-question development run produced six accepted answers, no wrong
submissions and no abstentions. It used 27 tool calls, 8,840 completion tokens and
104,151 prompt tokens, with 588.34 seconds of recorded job time. These questions
had already informed implementation work. This is a development check, not a
reserved result, a comparison win or evidence of a neural contribution. Earlier
failed and incomplete attempts remain part of the evidence.

## Accounting and evidence

Each job has ceilings of twelve model turns, twelve tool calls and 6,000 generated
completion tokens; a single request is capped at 1,536 completion tokens. Generated
reasoning tokens count toward these ceilings. Retrieved memory has a 6,000-byte
view limit. Tool executions also have declared VM-step, time, row and byte limits.

Prompt-token counts vary and are measured. Equal output ceilings are not equal
hardware-compute budgets. A whole-job token reserve is acquired before inference;
new ambiguous usage stops the comparison until reconciled. Earlier bounded
uncertainty stays disclosed and charged. The global comparison allowance includes
prior development use rather than silently resetting its accounting.

The full selected denominator includes failures, abstentions, unfinished attempts
and jobs not executed before a limit. A delivered query does not earn semantic
credit by itself. Source, execution, proposal and terminal-check identities are
retained separately. Wiring tests and isolated correct answers are not reported as
neural superiority or general reasoning gains.

The implementation is in [grounded_agent.py](../../cognition/grounded_agent.py),
with the comparison runner in
[evaluate_grounded_agent.py](../../scripts/evaluate_grounded_agent.py). The
[architecture specification](../../docs/hybrid-reasoning.md),
[procedural-memory specification](../../docs/procedural-memory.md) and
[neural bridge specification](../../docs/hybrid-neural.md) describe the individual
trust and learning boundaries.

The agent has no wallet, arbitrary shell or filesystem tool. Production histories
and the current site design remain separate. This experiment does not establish
consciousness, biological learning or a complete human-brain simulation.
