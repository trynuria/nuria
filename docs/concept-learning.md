# Learning a reusable search vocabulary

Nuria can use [Stitch](https://github.com/mlb2251/stitch) to extract parameterized
concepts from checked programs, retain those concepts, and compose them when
solving another problem. The implemented domain is bounded text transformation.
This changes its program search vocabulary; it does not train a foundation model
or establish general intelligence.

The learned library is JSON. Each proposed program expands into the existing
`safe-text-v1` interpreter, with its original operation, depth, size and execution
limits. Concept applications pay for their expanded primitive operations.
Fitting examples produces an **unverified proposal**, not an accepted result.

This uses an established library-learning implementation instead of extending
Nuria's earlier custom abstraction miner. That earlier miner and its broader-input
failures remain available in [the earlier record](text-abstractions.md).
The [Stitch paper](https://arxiv.org/abs/2211.16605) and
[LILO](https://arxiv.org/abs/2310.19791) motivate the approach; neither supplies a
demonstrated recipe for AGI.

## Measured result and limitations

The [development comparison](../research/concept-learning/summary.json) uses all
167 tasks in the existing Microsoft PROSE export. Each task family belongs to
one of three deterministic folds. A fold's concepts are learned only from
checked programs in the other two folds. The solver receives disclosed examples;
the evaluator alone receives scoring outputs.

At the same 50,000-unit serving ceiling per task:

| Method                                             | Exact tasks / 167 | Incorrect proposals |
| -------------------------------------------------- | ----------------: | ------------------: |
| Existing composed search                           |                80 |                  39 |
| Retained whole programs plus composed search       |                80 |                  39 |
| Existing field-template reuse plus composed search |                79 |                  40 |
| Existing native operations and custom abstractions |                13 |                   9 |
| Stitch concepts, composition, then composed search |                83 |                  38 |

The new method adds three exact tasks without losing an earlier exact task.
Two wins share a phone-formatting family; the third is an address task.
This is a small result across **two families**, not three independent domains.
It uses 2,520,515 serving work units versus 2,313,380 for composed search
(about 9% more). CPU, learning, teacher acquisition and baseline preparation
are reported separately. More successful search is not a speed claim.

These PROSE tasks were previously inspected elsewhere in Nuria. This is a
development reanalysis with family-separated learning, not a new untouched
qualification. Finite scoring rows can miss errors, and 38 proposals are known
to be wrong. Do not use the learner's output as an automatic production change.

The first development attempt learned from 124 historical synthetic-program
solutions and merely reapplied fragments. It tied at 80/167 with extra work.
Its [record](../research/concept-learning/first-attempt.json.gz) is retained.
Changing both the teaching data and the composition search produced the later
result; the comparison does not isolate their individual contributions.

## Use

Learning requires Python 3.12 and the optional dependency:

```sh
python -m pip install -r requirements-concepts.txt
```

Inference and execution require only the Python standard library. An examples
file contains JSON strings representing records, paired with desired outputs:

```json
{
  "inputs": ["[\"red fox\"]", "[\"blue whale\"]"],
  "outputs": ["RED", "BLUE"]
}
```

```sh
python -m cognition.concept_learning solve research/concept-learning/library.json examples.json > proposal.json
python -m cognition.concept_learning apply proposal.json inputs.json
```

`inputs.json` is an array of JSON record strings. `apply` returns an output or a
bounded execution error for each row. Check results independently before use.

To learn, supply an array of experiences. Each entry contains `program`, `inputs`
and `outputs`. `program` is a native `safe-text-v1` program; the rows are checks
supplied by the calling application. Every row is re-executed before retention.
Matching supplied checks does not authenticate their source or prove general
correctness.

```sh
python -m cognition.concept_learning learn experiences.json next-library.json --previous research/concept-learning/library.json
```

The destination must be new. Earlier libraries are preserved. Learning deduplicates
programs and learns at most 12 concepts from at most 512 distinct programs.
These are explicit capacity bounds, not a claim of unlimited learning.

The published serving library uses all 80 checked teacher programs. It is
separate from the three fold-specific libraries used to produce the comparison.
No evaluation score is claimed for that combined serving library.

## Reproduce

The evaluator uses the existing private PROSE job export; benchmark text is not
republished. Its content hash, source hashes, fold memberships, learned libraries,
all proposed programs and scoring counts are in
[results.json.gz](../research/concept-learning/results.json.gz).

```sh
python -m unittest tests.test_concept_learning
python -m scripts.evaluate_concepts jobs.private.json new-result-directory
```

The five-arm comparison additionally needs NumPy from Nuria's existing main
requirements. The learner uses no model API, network request or paid service.
