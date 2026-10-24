# A larger verified program curriculum

Increasing the training history did not produce a neural-search advantage. The
spiking ranker accepted **319/384** authored development tasks, compared with
**333/384** for the existing conventional solver and **324/384** for its matched
continuous control. These results are retained as a negative development
experiment. No running worker was replaced.

The question was concrete: did the earlier semantic-priority models lack enough
examples of useful intermediate computations? This follow-up increased the
history to 2,000 generated tasks and learned from the actual programs that
passed independent output checks. It kept the corrected search mechanism and
learning configuration unchanged rather than selecting a new parameter set
after every result.

The [archive manifest](../research/neural-routing-loop/development/archive-manifest-04.json)
binds the completed attempt's source snapshots, models, training records and
delivery traces. Its [recorded handoff](../research/neural-routing-loop/development/semantic-priority-dev-05/handoff-05.json)
contains the full aggregate results. Earlier unsuccessful variants remain in
the [semantic guidance record](semantic-guidance.md).

## Where the teaching came from

The curriculum generated the first 2,000 tasks from seeds 10 through 30 of the
existing authored task generator. These are synthetic tasks, not new natural
world observations, paid purchases or token events. For each task, eight fixed
search profiles each received a finite 50,000-unit interpreter allowance and
the first four disclosed examples. Every delivered program was checked on 24
nondiagnostic buyer rows.

At least one profile succeeded on 1,768 tasks; the default profile succeeded on
1,524. Accepted program identities and final outputs were independently
rechecked before they supplied teaching labels. The learner received semantic
membership in the union of proper subtrees of those accepted programs. The
full solution root was excluded, and input/constant leaves remained included,
following the earlier union-teacher variant. Nonmembership still does not mean
that an intermediate computation can never be useful.

The resulting dataset contains **38,635 records, including 4,271 positives**.
All six fitted controls receive the same records and provenance identities.
The spiking and continuous models train input synapses/biases and their
readout; the frozen-input control trains only its readout. Logistic, Fourier
and nearest-neighbor controls receive the same observable information. Recipe
metadata never enters numerical model inputs or labels.

The acquired history also contains 290 distinct accepted whole programs. A
strong same-history memory control retains all of them, rather than imposing
the older product's 128-program limit. Its serialized program library occupies
72,197 bytes. This is a deliberate conventional challenger, not an ablation
denied the successful history available to the neural model.

## What was and was not held out

The evaluation uses the same 384 already-inspected authored development tasks
and 43 already-inspected Duet development specifications. It is not a fresh
qualification.

Of the 384 authored tasks, **372 share a source-recipe hash with at least one
of all 2,000 generated curriculum jobs**. That count includes unsuccessful
curriculum origins. Restricting the comparison to curriculum origins having an
actually accepted teacher program gives **332/384** overlapping recipe hashes.
Neither number means that the hidden generator recipe was supplied to the
learner; the hashes are used only to describe evaluation overlap.

Only 12 authored tasks are recipe-disjoint from every generated curriculum
origin. Every fitted/static controller in the main comparison accepts two of
those 12. That small subset supplies no demonstrated generalization gain.
Repeated development on the known collections also prevents treating the
remaining results as an independent validation set.

The 162 reserved Duet specification bodies remain unopened in this study.

## Accepted programs, mistakes and work

All main search variants share a 50,000-unit allowance. Guided controllers use a
10,000-unit existing-solver prefix, then spend only the remaining allowance on
semantic search. The standalone existing solver receives the full 50,000 units.
The same-history memory control charges its retained-program scan and gives
only its remaining allowance to the existing solver.

Each controller stops at its first proposal fitting the disclosed examples.
Independent final buyer rejection does not authorize another attempt using the
hidden target. Accepted deliveries and incorrect proposals are reported
separately.

| Controller                | Accepted / 384 | Wrong proposals | Search work | Audit work | Buyer work |
| ------------------------- | -------------: | --------------: | ----------: | ---------: | ---------: |
| Existing strong solver    |            333 |               1 |   6,740,256 |     17,782 |    221,512 |
| Retained whole programs   |            331 |               1 |   3,978,995 |     17,782 |    222,137 |
| Matched continuous ranker |            324 |               1 |   5,614,007 |     17,782 |    214,062 |
| Edit-similarity priority  |            323 |               1 |   5,947,709 |     17,782 |    213,056 |
| Trained spiking ranker    |            319 |               1 |   5,828,534 |     17,782 |    209,031 |
| Fourier-feature ranker    |            314 |               1 |   5,728,683 |     17,782 |    204,465 |
| Linear ranker             |            312 |               1 |   5,735,865 |     17,782 |    202,002 |
| Nearest neighbors         |            301 |               1 |   6,679,550 |     17,782 |    198,697 |
| Property priority         |            289 |               1 |   6,683,769 |     17,782 |    173,322 |
| Frozen neural inputs      |            268 |               1 |   7,475,137 |     17,782 |    175,423 |
| Size priority             |            230 |               1 |   9,264,752 |     17,782 |    152,455 |

The spiking model outperforms its frozen-input control, so the trained input
synapses affect useful behavior. It still trails the continuous, edit-rule,
retained-memory and existing-solver alternatives. That distinction prevents a
neural-specific improvement claim.

On the 43 external development specifications, the fitted/static main
controllers each accept **22/43**, with one wrong proposal. Eighteen unsupported
or unscorable specifications remain in the denominator. The same-history
whole-program memory accepts **23/43 with zero wrong proposals**. It selects
that answer using the four disclosed examples, before final output checking.

This is a consequential benefit from retaining successful executable programs.
It is conventional program memory, not a result produced by the neural
ranker's synaptic learning. It also does not establish a universal winner: the
memory control accepts two fewer authored tasks than the existing solver.

## Cost of acquiring this history

The eight-profile teacher course records 467,003,223 search work units and
6,141,551 buyer-verification work units. Its recorded process CPU is about
192.95 seconds. Membership extraction adds about 36.70 seconds of recorded
process CPU. These historical costs are common prerequisites to the fitted
models and same-history retained-memory control; they cannot disappear from
an acquisition-inclusive efficiency comparison.

The spiking fit records about 4.24 seconds of process CPU; its authored search
records about 5.99 seconds, before buyer verification and the common teacher
costs. The continuous fit/search records about 1.31/5.30 seconds, and the
existing solver's authored search about 2.95 seconds. Nearest-neighbor scoring
records substantially greater search CPU in this run. These are retained
process measurements, not independently certified wall-clock or hosting-cost
benchmarks.

Interpreter counters, process CPU, memory and monetary cost are different
quantities. No financial transaction or paid service was used in this study,
and the search allowance is not a budget in dollars.

## Independent replay boundary

The [independent replay](../research/neural-routing-loop/development/semantic05-independent-replay-01.json)
executes all 16,000 recorded teacher program evaluations and all 4,090
authored/external evaluation programs with the separate output verifier. It
checks exact outputs, correct-row counts, acceptance, buyer work, disclosed
audit charges and aggregate arithmetic. It also reconstructs the retained
program order from the actual accepted teacher history and reruns its complete
first-fit scan and remaining-budget fallback.

The main guided-search visitation and gradient history are not reconstructed
by an output replay. Their search counters are checked for charged-component
coherence, finite bounds and agreement with aggregates. Recorded timing is not
independently witnessed. These limits apply even when every output reproduces.

## The next mechanism test

A prospective follow-up asks whether the model is scoring the wrong decision.
The completed variants score an intermediate computation after it has been
executed, while expansion still traverses fixed operation-family order. The
next design would jointly choose an existing operand and a prospective
operation family **before** executing the parent computation.

That plan has no result here. Its gates include shared observations and legal
operations for every controller, a conventional comparator with the same
operand/family context, exact source-bound prior outcome teaching, metered
exploration and materialization work, recipe-group separation, and independent
verification of accepted programs. Mechanical helper tests cannot establish
comparative usefulness. The live site, recurrent neural state, commerce
authority and existing solver remain unchanged while this question is tested.
