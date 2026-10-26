# A conventional semantic controller

`SemanticLinearControl` supplies the strong conventional comparator for a future
semantic-input experiment. It sees the same earned question representation and
observed tool statistics as the optional neural route. It chooses between
`list_tables` and `read_knowledge`; the host must enforce that first tool in the
analyst and separately verify the final outcome.

This component is implemented with scripted-vector tests. It has not yet been
evaluated on useful model-driven tasks or enabled in the public application.
Neither its existence nor the neural wiring record establishes a capability win.

## The declared rule

The selector uses the disjoint linear upper-confidence rule introduced by
[Li et al.](https://arxiv.org/abs/1003.0146). Each tool has a separate regularized
linear reward estimate. The score is its predicted terminal reward plus an
uncertainty bonus. The fixed constants are `lambda=1.0` and `alpha=0.5`; they have
not been tuned on a task cohort. A tie chooses `list_tables` by declared tool
order. Other learning rules or tuned comparators could be stronger.

Each context retains all 384 pretrained semantic dimensions. A bias and four
host-observed per-tool values are appended: the beta-posterior execution-success
mean, its standard deviation, mean measured cost divided by 100,000 and clipped
at one, and an unknown-cost indicator. The complete 389-dimensional context is
L2-normalized. A valid tool execution remains distinct from a correct answer.
Cost units are the host's measured work, not a guessed monetary value.

The neural input path uses a fixed 64-dimensional projection of the same language
representation. This conventional controller deliberately keeps the full vector.
Both have access to the same earned semantic information, but their feature maps,
parameter counts and computation differ. This is a strong conventional control,
not an exact parameter-count or representation-capacity match. An evaluation must
measure those costs rather than equating equal language-model token limits with
equal total computation.

Only an independently verified terminal acceptance or rejection updates the
regression, with rewards `+1` and `-1`. An unknown outcome cannot become a label.
Delayed updates use the exact vector, observed statistics and chosen tool retained
at the originating decision. Later observations never replace that context.
Repeated matching outcomes are idempotent; contradictory feedback is refused.

## Observation and identity boundaries

The host provides a normalized finite vector and its actual observation receipt.
The receipt must bind the exact float32 vector bytes, encoder identity and text
digest. This module loads no encoder, makes no request, executes no query and
reads no benchmark answer. It cannot establish that an arbitrary caller earned
an observation or is an honest checker. Source enrollment, observation charging,
question binding and outcome authority belong to the host.

Job IDs, source identities, model revisions and policy hashes are provenance
metadata. None becomes a regression feature or score. Changing only those fields
in matched fresh histories must leave the numerical features and choice unchanged.
Changing the source, encoder, implementation, NumPy version or fixed controller
terms refuses an existing history rather than quietly reinterpreting it.

## Persistence and finite operation

The controller keeps canonical JSON decision and settlement records in a separate
SQLite journal. Full transactions and an exclusive writer lock serialize updates.
Every record extends a hash chain. Before use, the journal is checked and the
linear coefficients are reconstructed from its origin/outcome records. No mutable
cached coefficient array can silently change a later decision.

State directories require mode `0700`; database and lock files require `0600`.
The laboratory has 128 job slots and at most 64 pending outcomes. It is a finite
experiment, not a production scheduler. A failed transaction leaves the previous
committed state. An interruption after commit may leave a committed decision or
outcome whose return value was lost; reopening and repeating the identical input
returns that record without another learning update. This module itself acquires
no external resource that could be silently purchased again.

Local hashes establish consistency, not an independent witness. A trusted
`expected_head` supplied on reopening can detect rollback from a separately
retained head; without it, a consistent old journal prefix is not distinguishable
from the latest history. Existing files and test evidence are preserved. Exact
floating-point replay can refuse a history on a different numerical backend or
hardware; a NumPy version alone does not pin that complete execution environment.

## Host composition

```python
from cognition.semantic_control import SemanticLinearControl

controller = SemanticLinearControl(
    private_new_history,
    source_id=enrolled_source_revision,
    model_id=analyst_model_identity,
    tool_version=enrolled_tool_contract,
    encoder_binding=earned_encoder_binding,
)
try:
    decision = controller.choose(
        job_id, context_sha256, vector, observation_receipt, observed_tool_stats
    )
    # Enforce decision["first_tool"] in the actual analyst attempt.
    # A separate host checker must bind its final query, source and job before:
    controller.settle(job_id, verified_acceptance, terminal_evidence_sha256)
finally:
    controller.close()
```

A fair future course must freeze the tasks, observations, outcome checker,
controller constants, backbone and budgets before inspecting task results. It
must retain failed and unexecuted jobs and count the encoder and controller work.
An improvement over this control must be measured; it cannot be inferred from
additional neural complexity or a different spike train.
