# Input-grounded project work

The programming worker now observes the structure of enrolled input files before
writing a program. This addresses a concrete failure: two earlier project reports
guessed the source schema, passed their own checks and failed independent host
acceptance. Their failed programs and outcomes remain in
[the earlier work record](../research/outcome-learning/project-work-02/README.md).

`cognition/input_observations.py` derives factual JSON paths, types, occurrence
counts and collection lengths from the supplied files. It binds each observation
to the file's SHA-256 identity. It examines every array member within explicit
node, depth and output bounds; exhausted bounds produce an incomplete observation
rather than a claim of complete inspection. Scalar values are omitted. Large
keyed maps aggregate member types instead of copying their member names.

Only enrolled file contents enter this inspection. The model receives neither
host checkers nor their expected reports. Duplicate JSON keys, non-finite numbers,
unrecognized encodings and oversized compressed payloads do not become invented
facts. A supported `base64+zlib+json` envelope has a separate decompression limit.

## Execute and deliver

The adapter reuses the existing isolated executor, outcome journal, shared model
budget and artifact collector. It supports two established routes:

| Route      | Maximum calls per job | Behavior                                      |
| ---------- | --------------------- | --------------------------------------------- |
| Direct     | 2                     | Propose, execute, then repair once if needed  |
| Challenged | 3                     | Propose, independently challenge, then revise |

Each call permits at most 2,400 completion tokens. These are ceilings, not targets.
The CLI defaults to the direct route. No new model, remote execution service or
payment authority is introduced.

```sh
python -m scripts.run_inspected_course enrollment.json run/project-work \
  --config /private/reasoner.json \
  --resource-root run/shared-resource-plan \
  --strategy direct
```

The resource plan and inference budget must already exist. Additional dependency
roots can be supplied explicitly. Credentials belong outside source control.

An enrolled job declares its specification, source files, independent checker and
expected artifact identities before inference. Delivery checks strengthen the
original acceptance contract. A plausible returned value without the required
output file remains a rejected experience. Accepted and rejected outcomes are
retained. Repeating the same immutable job returns its existing work receipt and
artifact bundle without another model call or program execution.

## Qualification and limits

The [prospective qualification](../research/outcome-learning/project-work-03/)
freezes four reports against actual repository observations: pull requests, commit
activity, component ancestry and reported CI. It also checks generated programs
on withheld actual-source projections, including a different branch's commit
graph. Subsets of an observed collection are labelled as subsets. Reported CI and
source ancestry do not establish direct production service health.

Both routes completed all four reports and their withheld-source checks. The
direct route used four model calls at $0.055066 in configured token accounting;
the challenged route used twelve at $0.190770. The prospective rule therefore
selects the direct route. Eight separate native acceptance replays matched. All
eight immutable jobs and artifact bundles also repeated with zero new model calls
or original executions. The published evidence preserves both routes in full.

This is a bounded functional qualification. Four reports cannot establish general
reasoning or neural superiority. The broader 108-task comparison returned 87
accepted programs for targeted navigation, 87 for static retrieval and 83 without
memory; its improvement gate failed. That result is unchanged.

These rails require host-enrolled tasks and independent acceptance contracts.
They are not an unrestricted self-modification or purchasing service, and are not
activated in the production cognitive worker. New input versions require new
source-bound work identities; identical-job replay does not refresh external data.
