# Consistent outcome feedback

A host check can be wrong or unstable. In the fresh reasoning comparison,
unchanged programs received different verdicts when replayed. The canonical
reference also failed its own timing check during fixed physical repetitions.
Repeating the same verdict does not resolve that contradiction.

The optional consistent-work entry point uses Nuria's existing outcome journal
to retain additional host observations and withhold contradictory jobs from
future automatic experience. It preserves original grades and receipts.

| Evidence                                              | Automatic learning treatment                  |
| ----------------------------------------------------- | --------------------------------------------- |
| One independently checked verdict                     | Eligible within its recorded scope            |
| Additional matching observations                      | Still eligible; duplicate IDs add no evidence |
| Opposite verdict on the same job, program and checker | Withheld as uncertain                         |
| Ungraded proposal                                     | Excluded                                      |
| Different program, checker or original receipt        | Observation refused                           |

Both positive and negative credit are withheld after a contradiction. Later
majority votes cannot restore eligibility. Raw records remain available for
audit; the guard neither edits past neural credit nor changes benchmark scores.
The scope is one enrolled job, including its recorded source contract. It does
not infer that every task using the same library is unreliable.

## Work interface

```sh
python -m scripts.run_consistent_course enrollment.json run/work \
  --config private-client.json --resource-root run/resource-authority \
  --method direct
```

The existing direct and three-stage work protocols are reused. `navigation`,
`retrieval` and `no_memory` are also supported. The shared client and resource
authority enforce their existing limits. This is a finite command, not a new
service or production activation.

A trusted host appends an actual check using
`ConsistentOutcomeStore.observe(job_id, receipt_id, checker, result_sha256=...)`.
The original receipt hash, stored job contract, exact program hash and original
checker hash bind each observation. The host remains responsible for running
the original enrolled inputs. Hashes do not attest an honest execution or give
a model authority to grade itself. This interface is not exposed publicly.

The new learning view reopens durably and reads one consistent SQLite snapshot.
Its source records and observations must agree with the append-only journal.
An identical completed job still returns its original receipt without another
model request or execution. The original course entry points and frozen
experimental implementations remain unchanged.

## Complete recorded-evidence check

The [integration record](../research/outcome-learning/feedback-consistency-07/)
imports all 156 original programs and 168 actual native observations from the
published comparison and its fixed physical characterizations. Two jobs have
contradictory verdicts; 154 remain eligible for future automatic experience.
Replaying every observation adds no journal event. The raw history hash is
identical before and after, and the original comparison remains 56/78 per arm.

This uses revealed historical evidence. It demonstrates durable feedback
handling and its effect on the work interface. It does not demonstrate improved
fresh-task accuracy, neural superiority or general intelligence.
