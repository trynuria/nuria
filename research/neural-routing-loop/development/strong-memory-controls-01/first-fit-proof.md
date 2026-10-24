# First-fit equivalence

Fix a finite ordered history of deterministic, bounded, previously accepted
programs and the same nonempty disclosed input/output rows. Let a fitting program
mean that evaluating it on every disclosed input terminates without an error and
returns the corresponding disclosed output. Both scans visit history in decreasing
historical use count, with the same program-hash tie break.

For each visited candidate, a full-row scan accepts exactly when all rows match.
A first-mismatch scan also accepts exactly when all rows match: it can stop early
only after establishing a mismatch or an execution error, which already makes
the conjunction false. Each evaluator invocation has the same arguments and
deterministic semantics. This remains true if a later skipped row would have
raised an error: the candidate is already known not to fit. Therefore both scans
reject the same candidates before the first fitting candidate and return exactly
that same candidate. If no candidate fits, both exhaust the same history and
return no retained program. This is an unbounded selection result; it does not
claim identical finite-budget choices or fallback allowances.

Under the original retained-scan meter (one candidate charge, then only the
interpreter charges for executed rows), the short-circuit trace for each rejected
candidate is a prefix of the full-row trace. All charges are nonnegative. The
candidate charge is identical and the first fitting candidate executes every row
in both scans. Thus, until selection or history exhaustion, short-circuit total
work cannot exceed full-row work. Error counters can differ without changing
selection, because a skipped later error need not be observed.

The serving controls additionally pay candidate-AST serialization and actual
observed-output serialization. Static AST validation and template construction
are prepaid once per fold and reported separately. Consequently the semantic
argument applies to their retained first-fit ordering, while the original-meter
work inequality is tested only by the separately reported paired scans. A finite
work limit can truncate either scan before an otherwise fitting candidate, so no
unbounded parity claim is silently extended to the 50,000-work serving cap.

For field substitutions, canonical slots are assigned at each field reference's
first occurrence. All references to the same source field share a slot, including
conditional predicate fields and both branches. A binding assigns every slot an
integer in the actual common disclosed field range. Distinct source slots may
map to the same field. Non-field parameters and constants are unchanged. Static
enumeration over the declared maximum field count, followed by filtering on the
actual field count, yields the same ordered candidate stream as direct current
field-count enumeration. Complete-AST duplicates have the same referenced field
set, so deleting later duplicates before filtering preserves that first occurrence.
