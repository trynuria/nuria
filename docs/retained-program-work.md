# Retained programs on new source data

An accepted program can become a reusable tool without another model request.
The host assigns a finite function profile, identifies its accepted originating
job and supplies the completed artifact receipt. Acquisition verifies the original
outcome journal, exact program, original task and independent delivery identities.
It reads the originating database without changing it.

This extends the existing outcome worker and artifact collector. It introduces
neither another skill database nor another model service.

## A fresh execution

Each later job supplies new immutable inputs, its source identity, specification,
independent checker and required artifact identities. The exact retained program
runs in the existing OS isolation boundary. Its new outcome enters a separate
work journal. Successful output is collected into an immutable artifact bundle;
failed transfer remains rejected history and produces no accepted delivery.

An identical job returns its existing outcome and artifact receipt without another
program execution. Rebinding an existing job ID to different data or checks is
refused. Modified code, an incompatible profile, changed origin evidence or a
corrupted originating journal are also refused. Interrupted execution requires
explicit reconciliation rather than an automatic duplicate attempt.

The host acquisition interface is `acquire()` in
[`scripts/run_retained_work.py`](../scripts/run_retained_work.py). It requires the
exact compiled originating task, its accepted job ID, verified origin database,
completed delivery receipt and an explicit profile. This is host enrollment,
rather than an agent granting itself a new tool. The CLI uses the compiled
`enrollment-<hash>.json` written by the original delivered-work runner:

```sh
python -m scripts.acquire_retained_program origin-enrollment.json skill.json \
  --origin-store /private/origin/outcomes.sqlite \
  --origin-delivery /private/origin/receipt.json \
  --profile profile.json --job-id accepted-job
```

After enrollment, run a finite delivered-work sequence with:

```sh
python -m scripts.run_retained_work skill.json later-enrollment.json run/later \
  --origin-store /private/origin/outcomes.sqlite \
  --profile profile.json
```

The skill and profile files contain no signing or model credential. The CLI needs
no reasoner configuration. Input and output limits, independent acceptance and
the existing isolation backend still apply. A course contains at most 32 enrolled
jobs; retained storage and long-running source policies remain host obligations.

## Actual transfer record

The [later-source course](../research/outcome-learning/retained-work-04/) acquired
the four accepted model programs from the earlier project-work qualification.
Their exact code then processed later repository observations: 25 pull requests,
142 commits reachable from an immutable branch head and 30 recent workflow runs.
The component commit identities were explicitly retained historical reports, not
a new direct service-health observation.

| Method                                   | Accepted fresh reports | New model calls |
| ---------------------------------------- | ---------------------- | --------------- |
| Retained accepted programs               | 4/4                    | 0               |
| Independent conventional implementations | 4/4                    | 0               |
| Previous answer snapshots                | 0/4                    | 0               |

Every accepted retained report was delivered. Repeating the four jobs added no
executions, changed no work journal and returned the existing artifact bundles.
The original acquisition database stayed byte-identical. A public replay
reconstructs the original journal, reacquires the exact tools and repeats all
twelve transfer and control outcomes without provider access.

This demonstrates useful executable retention and changed-data execution. The
strong conventional baseline ties accuracy and also uses no model calls. These
are four known host-enrolled report profiles; no general reasoning, neural
superiority or autonomous production activation is established. Program and
journal hashes establish local consistency, not independent remote authentication
or correctness beyond the enrolled checks.
