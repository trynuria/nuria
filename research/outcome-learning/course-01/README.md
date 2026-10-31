# Short-instruction exploration

This retained development course supplied BigCodeBench's `instruct_prompt` and
signature. It completed 24 training jobs, accepting 15. The baseline scored
10/16 on validation. Three GEPA proposals failed their training comparison and
no policy was promoted. No held-out model proposals were run in this course.

Execution failures exposed missing input-format information: the short prompt
for one plotting task omitted that its input was a formatted string. The full
upstream specification included that requirement. The subsequent course
corrected the request path for every method before fresh evaluation. Its
20/24 training result is not attributed to learning from this correction.

The files preserve both oracle preflights, environment corrections, completed
programs, policy proposals and original task sources. The first preflight's
compatibility problems were repaired without changing upstream assertions.
Remaining reference-program problems were retained, not resampled.

The [complete-specification course](../course-02) is the primary fresh-task
comparison. Provider-text evidence there includes the exploratory requests;
all calls used the same shared allowance.
