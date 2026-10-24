# Composed skills

Nuria's versioned text learner can search for combinations of operations that
the earlier learner could execute but could not reliably discover. On a frozen
576-job comparison, it solved **438 jobs exactly**, against **337** for the
preregistered conventional control using the same operation family, four audit
answers and 50,000 search units per job.

This is a measured improvement in bounded program search. It does not establish
a benefit from the neural architecture or a result about consciousness.

## What changed

The learner can combine extraction with another operation, such as uppercasing
an extracted token, trimming a delimited field or shortening an extracted code.
It can join the resulting pieces into a complete output. Intermediate results
are cached, and equivalent results on the disclosed examples can share further
search work. The enumerative control uses the same ordered operation family and
cache, with this equivalence pruning disabled.

The earlier approach also exposed two applicability failures: a stored program
could match two examples by coincidence, and checking a growing library could
consume the allowance needed to find a new program. A separate guarded policy
tests distinguishing examples and protects a fresh-search allowance. Its extra
mechanisms remain experimental; it was not selected as the production default.

Equivalence on training examples is limited evidence. Two programs can agree on
every disclosed answer and disagree on a new input. Independent final checking
and explicit rejection remain necessary.

## Qualified result

The [published protocol](../research/guarded-skills/protocol.json) selected the
four-answer composed policy before generating twelve fresh synthetic streams.
Each stream contains 48 jobs, with six possible diagnostic inputs and 24 final
rows per job. Task families, deliberate ambiguous prefixes and recurrences are
declared in the generator. These are authored engineering tasks, not customer
traffic or independently authored external tasks. All nine methods and every
failed job remain in the result.

| Method                            | Exact jobs / 576 | Wrong proposals | Audit answers |   Counted work |
| --------------------------------- | ---------------: | --------------: | ------------: | -------------: |
| Earlier search, four answers      |              129 |               0 |         2,304 |     24,724,219 |
| Enumerative search, three answers |              352 |               3 |         1,728 |     14,258,611 |
| Enumerative search, four answers  |              337 |               1 |         2,304 |     14,934,915 |
| Composed search, three answers    |              423 |              16 |         1,728 |     11,482,079 |
| **Composed search, four answers** |          **438** |           **1** |     **2,304** | **12,673,045** |
| Guarded selection and correction  |              455 |               5 |         1,848 |     17,238,110 |

Against the primary four-answer enumerative control, the selected method gained
**101 exact jobs without losing any control success**. Both methods made one
incorrect proposal. The exact-job improvement was 17.5 percentage points; the
paired whole-stream bootstrap 95% interval was **15.1–20.0 percentage points**.
The preregistered competence gate passed.

Observed total work was 15.1% lower and locally measured complete-pipeline CPU
was 9.4% lower. The stricter registered efficiency gate nevertheless failed:
it required identical correctness and proposal coverage, and those quantities
changed. These cost observations are reported separately from that gate.

Guarded selection solved more jobs, with more wrong proposals and more work. The
three-answer policies spent fewer labels. No method won every axis. Frozen
guarded uses a different controller from the selected method and does not isolate
the causal effect of retention in the four-answer policy.

The [complete comparison](../research/guarded-skills/reserved/comparison.json)
reports all controls, paired wins and losses, cost ratios and uncertainty. The
independent checker verified all **5,184 method-job records**. It reconstructs
outputs, receipts, library transitions and accounting; it does not replay search
or independently witness physical CPU consumption.

## External reanalysis

The previously observed Microsoft PROSE tasks provide a separate, explicitly
seen-set check. Both learners used the original seven-program checkpoint, the
original first two diagnostic rows and all remaining original scoring rows.
All 14 unsupported tasks remain within the 167-task denominator.

At 10,000 search units, exact tasks increased from **59 to 72**: 24 wins and
11 regressions. At 50,000 units, they increased from **73 to 78**: nine wins and
four regressions. Both comparisons used more total work and CPU. This is an
accuracy improvement on a known dataset, not fresh external qualification or an
efficiency claim. The [reanalysis reports](../research/guarded-skills/external-reanalysis/summary.json)
preserve both budgets and their complete outcomes. Original benchmark bodies
remain outside the public repository.

The earlier [negative external qualification](../research/active-skills/README.md)
remains unchanged as historical evidence. It is not retroactively relabelled as
a success.

## Prepared production route

The separate `local-composed-records-v1` provider is restricted to the
`nuria:composed-record-repair-v1` source and `composed_fixed4` policy. Its reviewed
authority requires the text language and four distinct audit answers. A task
cannot broaden that authority through its own metadata.

The learner receives diagnostic inputs and the answers to its recorded requests.
Recipes and final inputs and targets stay with the buyer. One committed program
is delivered before final checking. Only exact buyer acceptance can admit a new
program; a correct artifact from a terminal abstention can be checked and
consumed without being retained or counted as a delivered success.

The prepared adapter uses durable reservations, immutable action receipts and
source-bound checkpoints. Interrupted physical work retains uncertain liability
instead of being silently repeated. After buyer evaluation, one outcome is
returned to its originating neural context and acknowledged only after the
cognitive checkpoint is committed. This route uses included compute with zero
wallet payments. Live integration requires its separate completion certificate;
the offline comparison alone does not establish it.

## Reproduce the checks

The public synthetic corpora and traces are losslessly compressed in
`research/guarded-skills/reserved`. From the repository root, expand them into a
new evidence directory and run the checker:

```sh
python - <<'PY'
import gzip
from pathlib import Path

source = Path("research/guarded-skills/reserved")
target = Path("../nuria-guarded-replay")
target.mkdir(mode=0o700)
for name in ("started.json", "summary.json", "corpora.json", "traces.json"):
    path = source / name
    raw = path.read_bytes() if path.exists() else gzip.decompress((source / (name + ".gz")).read_bytes())
    with (target / name).open("xb") as stream:
        stream.write(raw)
PY
python -m scripts.verify_guarded_skill_results ../nuria-guarded-replay
```

Verification requires the exact source identities recorded in `started.json`.
The [comparison script](../scripts/summarize_guarded_skill_comparison.py) fixes
the chosen method and primary control, then resamples whole seed streams rather
than treating dependent rows as independent trials. Twelve clusters limit the
precision of its intervals. Secondary comparisons are exploratory and
unadjusted; they do not establish a broad superiority claim.
