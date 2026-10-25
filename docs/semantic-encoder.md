# Fixed semantic input

The optional encoder converts a question into a fixed pretrained language
representation. A separate numerical mapping turns that representation into
bounded currents for the spiking circuit. Model, source and job identities bind
receipts and recovery; they never become embedding text or sensory features.

The distinction matters: a digest distinguishes two questions but does not
preserve their meaning. A sentence encoder provides a reusable semantic geometry.
That competence comes from its pretrained weights. Whether the spiking controller
learns to use it better requires a separate outcome comparison.

## Representation and visibility

The pinned [MiniLM model](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
returns 384-dimensional sentence representations. The implementation uses the
official ARM64 int8 ONNX artifact, attention-mask-weighted mean pooling and L2
normalization. The
[model manifest](../research/semantic-input/runtime/model-manifest.json)
records immutable weight and tokenizer identities.

Before the first tool, encode the supplied question alone. Do not insert schemas,
database rows, expected answers or future tool results. Later observed information
may be encoded only after its actual tool receipt, with the same observation and
encoding cost available to conventional controls. This module does not decide
whether the calling host has earned access to a particular observation.

The encoder refuses empty input, more than 5,000 UTF-8 bytes or more than 256
wordpieces. It does not silently truncate a question. Every returned vector must
be finite, nonzero and have the declared dimension. Receipts identify the actual
text, runtime binding, token count and float32 vector bytes.

## Bounded neural mapping

A fixed projection maps the normalized representation to 64 dimensions. Its
PCG64 seed is independent of questions, sources, identities and outcomes. After
normalization and a declared `tanh(4z)` compression, positive and negative values
use separate groups of 64 sensory channels. Every current stays within the
existing circuit's `0.2` bound. The 64 memory features preserve the signed signal
around a midpoint of `0.5`; salience and energy are fixed at `1.0`.

`semantic_projection()` and `semantic_currents(vector, matrix)` accept no job ID,
source identity or policy hash. The projection and representation must be
identical across compared controllers. A different identity must not randomize
an otherwise identical semantic input.

## Runtime and recovery contract

`FrozenSemanticEncoder(model_directory, manifest_path, max_calls=12)` loads only
explicitly provisioned local files. Importing the module loads no model and makes
no network request. Runtime packages are optional and isolated in the
[CPU lock](../research/semantic-input/runtime/requirements-cpu.lock); existing
workers need no additional dependency to import the surrounding project.

The tested artifact uses a sequential CPU execution provider with two intra-op
threads and one inter-op thread. The manifest pins the runtime versions. Other
platforms need their own compatibility and resource check. Sentence Transformers'
[backend guidance](https://sbert.net/docs/sentence_transformer/usage/efficiency.html)
explains why CPU placement can reduce competition with a generative model.
Actual latency and memory remain workload-dependent.

The detached `binding` and read-only `binding_sha256` identify the model, tokenizer,
pooling, implementation, finite limits and runtime. `observe(question)` returns a
vector and a hashed observation receipt. Weight, tokenizer, implementation and
manifest identities are checked around execution. An execution failure stops that
encoder instance.

The in-memory call ceiling is not a durable budget. The host must reserve a job
before invoking the encoder and retain interrupted or ambiguous work across
restarts. Recreating an encoder does not authorize another attempt. Weights and
private observations remain outside source control.

## Evidence and further tests

Scripted-runtime unit tests check file identities, pooling, bounds, refusal after
an ambiguous execution, CPU-only configuration and identity-independent current
mapping. Those fixtures are not live semantic results.

A separate retained prototype used the same weights, pooling and numerical
mapping for twelve predeclared texts and four matched-checkpoint Brian2 windows.
The hardened public wrapper has additional binding and refusal checks; it has
not been silently substituted for that prototype's recorded source identity.
Semantic wiring establishes neither useful decision improvement nor new language
learning by the spiking model. A future comparison needs the same representation,
observations, feedback and resource budget in a strong conventional controller,
along with frozen-state, shuffled-representation and identity-texture controls.
