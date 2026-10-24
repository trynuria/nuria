# Negative recurrent-memory attempt

The first approach trained a 64-neuron recurrent LIF model to retain two task
variables through noncommuting commands. Contrastive alignment of observed-future
classes was added to its prediction loss. A GRU and the same SNN without alignment
received matched candidate-query budgets. All training used surrogate-gradient
backpropagation, not the production Brian2/STDP rule.

The approach failed. Across final seeds 711–713, independent-nuisance joint
accuracy after 48 commands was 24.93% for the aligned SNN, 24.09% for the plain
SNN, and 100% for the GRU. Four-state chance is 25%. Selective state transfer
followed by eight commands also failed for the SNN; the matched GRU transfer
assay worked. This result rules out this attempted configuration as a successful
Nuria memory mechanism, not spiking networks in general.

The first loss aligned spike traces while omitting membrane voltage from the
complete state. A correction aligned both, and a final correction added
query-budget-matched disagreement sampling, successor alignment and persistence.
Neither repaired long composition. The frozen [final source](recurrent-lif-source.py.txt),
[initial source](recurrent-lif-v1-source.py.txt), [per-seed findings](recurrent-lif-findings.json),
and numerical held-out observations/predictions are retained here. Source files
are byte-preserved text snapshots; Python can execute the final `.py.txt` file
directly with its documented CLI. Its original numerical reports remain
exploratory and do not share the later binary circuit's exact invariant proof.

The nine `raw/*.npz` archives contain numerical arrays only and load with
`allow_pickle=False`. They expose held-out command streams, observed query
responses, predicted logits, complete final neural state and transfer outcomes.
The adjacent reports retain training counts and selected intervention masks.
`manifest.json` binds this retained evidence; the main release checker verifies
the published file hashes. No market, wallet, human-subject or private input data
is included.
