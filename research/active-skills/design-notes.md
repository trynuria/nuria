# Active skill acquisition: protocol draft

This directory contains a source inventory, protocol and development evidence.
The [frozen protocol](protocol.json) defines the pending reserved qualification;
the [development reports](development/index.json) preserve failed approaches and
their source snapshots. Reserved access requires the protocol to be published
and its commit verified before specification retrieval.

## Independent external source

The [Microsoft PROSE benchmark suite](https://github.com/microsoft/prose-benchmarks/tree/8e3349ae8575c2615926b76170b287b6dddb5a73)
is pinned to revision `8e3349ae8575c2615926b76170b287b6dddb5a73`.
Only its README, license, complete tree and 354 transformation-task metadata
files were retrieved for the inventory. Metadata bytes were checked against
their pinned Git blob hashes and separately hashed with SHA-256.

The universe contains 354 transformation tasks. A rule fixed without running a
solver selects 183: the Microsoft CC BY-SA 4.0 license group, with metadata
features limited to substring, concatenation, conditional, multicolumn, casing
and numeric transformations. Eligibility does not imply that the language can
solve a task. Unsupported tasks stay in the reported denominator.

All 346 tasks in that license group are marked synthetic. The eight tasks marked
source-derived use other license sections and remain excluded pending separate
review. This is an independently authored benchmark, not evidence of customer
traffic or a real-world deployment. The source README requests avoiding data
redistribution because generated strings can accidentally resemble personal
data. This directory publishes metadata, provenance and commitments rather than
benchmark bodies.

The entire Currency, Column and Email families are development-only: 16 eligible
tasks. The other 167 eligible tasks form the proposed reserved qualification set.
Three development forms were inspected when defining the language. All sixteen
development tasks were subsequently evaluated; reserved targets remained unopened
at protocol freeze:

| Task            | Original examples | Required behavior                                                                                                   |
| --------------- | ----------------: | ------------------------------------------------------------------------------------------------------------------- |
| Currency.000003 |                20 | Remove a leading currency character while preserving the remaining numeric text, including decimal trailing zeroes. |
| Column.000001   |                10 | Map a special input to a constant; otherwise remove part of an input prefix and concatenate the remainder.          |
| Email.000008    |                 3 | Select the first of three input columns and append a constant domain.                                               |

These expose useful generic needs: bounded string extraction, field selection,
concatenation and simple conditional behavior. The present decimal formatter
cannot preserve arbitrary numeric text such as trailing zeroes. Three examples
also cannot support a fabricated 24-row evaluation. The proposed external split
uses the first two original rows as the diagnostic pool and all remaining rows
for scoring, with exact original outputs.

## Capability and comparison

An input-only request supplies no recipe, format number or selected skill hash.
The controller evaluates retained candidates, identifies disagreements, chooses
diagnostic labels, synthesizes when existing skills do not suffice, and abstains
if a finite evidence budget leaves uncertainty unresolved. The production policy
is conventional candidate elimination. It is the strong baseline, not a claim
that a neural controller outperforms that same algorithm.

Compare fixed-example retention, active disagreement selection, random probes and
a frozen library under the same grammar, solver and work allowance. All probes,
candidate executions, failed searches, buyer checks and state costs count.
Report label counts, work, CPU, exact correctness and coverage separately. Asking
for fewer labels is useful only if correctness and useful coverage survive.

Input syntax cannot identify arbitrary hidden meaning. A familiar numeric shape
does not establish a unit or business rule. An audit label is the default, and
agreement among retained candidates is only agreement within that finite set.
Final scoring outputs must not be supplied to search; any aggregate buyer
feedback used for later tasks must be explicitly registered as prequential
learning and available identically to every method.

No reserved task specification was retrieved or executed before the protocol
freeze. Current access and execution receipts must be reported separately.
