# Outcome-trained search research

This directory retains exploratory development for an opt-in spiking search
policy. **There is no passed qualification or deployed policy replacement.**
The existing application, recurrent circuit, histories and financial authority
are unchanged by these experiments.

The [implementation notes](../../docs/neural-routing-loop.md) describe the
transactional choice/outcome bridge and its remaining integration gates. The
[development summary](development/summary.json) includes failed budget routing,
fixed search profiles, neural selection and a database-plan feasibility probe.
In the two-profile comparison a conventional rule accepted 355/427 tasks, versus
327/427 for the spiking policy. The negative result remains visible.

## Retained development evidence

- [First archive](development/archive-manifest-01.json): all 64 files from the
  completed budget/profile and SQLite probes, including superseded attempts.
- [Benchmark archive](development/archive-manifest-02.json): parser checks,
  development specifications, source identities and the still-sealed reserved
  benchmark inventory.

Each manifest distinguishes original byte hashes from published byte hashes.
Absolute local paths and one implementation-ownership annotation are normalized
where indicated. Numerical results are unchanged. Historical Python scripts are
retained as `.py.txt` snapshots rather than supported executable entry points.
Some inventory snapshots reference historical inputs outside this archive;
these snapshots are evidence, not a claim of one-command full reproduction.

The initial budget attempt omitted common audit charges. The first SQLite
attempt accidentally used redundant join predicates. Their corrected successors
are separately retained; the earlier artifacts must not support an efficiency
claim. Interpreter work, CPU time, accepted deliveries and wrong proposals are
different measurements.

## Upstream data and prior art

The 43 Duet development specifications and derived checks under
`development/eval-design-01/` originate from the pinned Duet source recorded in
the manifests. They retain the upstream [GPL-3.0 license](development/eval-design-01/duet-LICENSE).
The independently written project parser and runtime code are separate from
those benchmark materials. Original constraints, specification-derived checks,
unsupported tasks and unscorable tasks remain distinct.

The 162 reserved specification bodies have not been downloaded or inspected in
this development record. A source/selection freeze and declared evaluation are
required before opening them. Development success cannot substitute for that
test.

Learned prioritization of executed intermediate programs is established prior
art. [BUSTLE](https://github.com/google-research/google-research/tree/master/bustle)
and [its paper](https://arxiv.org/abs/2007.14381) motivate a further experiment;
an independently implemented spiking ranker would not make the general approach
new. Likewise, [Bao](https://arxiv.org/abs/2004.03814) already studies learned
database-plan selection. The SQLite pilot was stopped because a small histogram
rule nearly matched its oracle, leaving little practical neural headroom.
