# Retained report-transfer course

This package contains all 98 original course files, including 12 fresh generated
ledger revisions, the exact prospective protocol, selected and completed cases,
query receipts and the procedural-memory and skill journals. No model, neural
simulation or evaluator runs during portable verification.

| Method                | Correct / selected | Wrong reports | Report queries | Schema queries |
| --------------------- | -----------------: | ------------: | -------------: | -------------: |
| Qualified skill       |              12/12 |             0 |             12 |             12 |
| Exact query cache     |              12/12 |             0 |             12 |              0 |
| Conventional template |              12/12 |             0 |             12 |              0 |

The origin report and promotion add one report query and one schema query. All
methods use zero model calls. Checked exact-report reuse works on these generated
rows, and its accuracy ties both controls with additional verification work. This
does not demonstrate better reasoning, neural superiority or model-call savings.

The origin program is a declared conventional query. The report and generator
were designed together; this is a finite functional course rather than an unseen
external benchmark or real production outcome.

## Verify

From the repository root, choose a new workspace whose parent already exists:

```sh
python research/analytical-skills/challenge-01/verify.py \
  --repository . \
  --workspace /absolute/path/to/new-evidence-workspace \
  --output /absolute/path/to/new-verification.json

python -m unittest discover \
  -s research/analytical-skills/challenge-01 -p test_package.py -v
```

The wrapper pins Protocol 03 to
`5130c4fd6aa3b992d55cc75038bcb18bf8d749e61d4652b553bb9454bab7c2e3`
and checks all seven frozen implementation files before invoking the existing
bounded independent verifier. It checks archive inventory, regular-file type,
paths, sizes and hashes before extracting into a fresh private workspace. It
refuses existing workspaces and retains extracted evidence if replay fails.

Limits are 128 members, 2 MiB compressed evidence, 8 MiB expanded evidence and a
120-second verifier timeout. A 16 GiB free-disk floor applies to extraction. The
archive actually expands to 484,629 bytes. No pickle loading or arbitrary archive
execution occurs.

Independent replay reconstructs four procedural-memory events and 37 skill
events, checks the accepted origin and exact promotion, and replays the origin
query and all 36 report queries. It recomputes the report with a separate Python
grouping implementation. `replay-result.json` is the retained independent verdict
and must match the portable result.

Hashes and local replay establish retained numerical consistency. They are not
signatures, independent physical execution witnessing or proof of a remote
checker's authority. The existing site design and production enrollment are
unchanged.
