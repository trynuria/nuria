# Learning mechanisms and comparison boundaries

Nuria separates three kinds of change: retrieving checked experience, changing a
spiking controller's state, and updating a language model's weights. They require
different evidence. A successful query does not establish all three.

## Stable reasoning and evolving experience

[MemRL](https://arxiv.org/abs/2601.03192v1) studies a frozen reasoner with an
external experience store. Its retrieval first identifies relevant memories and
then considers utility learned from outcomes. This distinction matters because a
similar past episode can still be unhelpful. The paper reports its own benchmark
results; those results are not Nuria measurements.

Nuria's grounded analyst retains executed observations and separately checked
terminal outcomes. Its current procedural retrieval qualifies individual
experience records. It does not implement MemRL's complete value-aware retrieval
algorithm or establish that a procedure generalizes from one successful use.
Failure retention, source binding and a strong no-memory control remain necessary.

## Meaning before neural control

The original hybrid inspection experiment supplies the circuit with identity
texture and observed tool statistics. An identity digest distinguishes inputs;
it does not preserve semantic similarity. Results from that experiment cannot
establish that the circuit understands a question.

The optional [semantic input path](../../docs/semantic-encoder.md) uses the fixed
[MiniLM sentence encoder](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
to obtain a 384-dimensional representation. This follows the model's declared
mean-pooling and normalization contract. A fixed mapping then supplies bounded
currents to Brian2. Pretrained semantic competence belongs to the encoder, while
the spiking model's contribution must be measured separately.

The relevant control receives the same representation, observations, outcomes and
resource allowance without the spiking mechanism. Useful comparison also needs
frozen-plasticity and shuffled-representation conditions. A changed spike train
or a passing paraphrase probe alone does not establish better decisions.

## Changes to the reasoner itself

[MemSkill](https://arxiv.org/abs/2602.02474v2) distinguishes selecting existing
memory skills from developing new memory operations through a separate designer.
Nuria's checked procedure annotations are experience candidates. They are not
automatically installed executable skills or evidence of an evolving designer.

[ASCENT](https://arxiv.org/abs/2610.05303v1) takes a different route: it distills
verified agent trajectories into persistent LoRA updates. Its authors report that
directly imitating a single generated trajectory can destabilize the policy. The
method uses a frozen reference model with hindsight information to produce the
distillation signal. This is an October 2026 preprint, not independent validation
of Nuria or a guarantee of safe online improvement.

Nuria's current comparison keeps the reasoner's weights fixed. A future adapter
experiment would need a separate model identity, retained verified training
trajectories, protected evaluation tasks, rollback checkpoints and a measured
compute allowance. It must not change the backbone during a frozen comparison or
silently transfer results from a different architecture.

## What the current evidence can resolve

The grounded comparison measures committed answers, strict execution correctness,
abstentions, tool use and observed token consumption. Its primary matched pair
isolates a particular first-inspection policy around the same reasoner and
procedural-memory mechanism. Other simpler controls require their own completed
coverage. A small selected cohort cannot establish broad superiority.

The independent work-snapshot task checks whether the analyst can answer a
concrete operational question from retained source records. Recorded delivery and
recorded outcomes are distinct from independent real-world utility. Included
hosting capacity is distinct from an external purchase.

These mechanisms provide testable routes toward useful adaptation. They do not
establish consciousness, biological learning or a complete human-brain model.
