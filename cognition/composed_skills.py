"""Four buyer-directed audits with the versioned composition learner.

This policy uses the same source-bound, recoverable diagnostic protocol as the
original active route. It makes no neural selection or optimal-probe claim. The
reviewed runtime must bind this module and its solver identity before restoring
a checkpoint, and must use a separate provider/source from earlier studies.
"""

from cognition.active_skills import ActiveSkillSession
from cognition.skill_language import execute_checked, normalize_config
from cognition.text_composition import SEARCH_VERSION, CompositionalTextLibrary

SOLVER_ID = SEARCH_VERSION + ":four-audits"


class ComposedSkillSession(ActiveSkillSession):
    """A fixed policy selected by development evidence, not extra authority."""

    def __init__(
        self,
        source_id,
        config,
        inputs,
        library_state=None,
        *,
        maximum_work=100000,
        synthesis_work=50000,
        maximum_probes=4,
        minimum_evidence=4,
        policy="audited_retention",
        state=None,
    ):
        if (
            not isinstance(config, dict)
            or config.get("version") != "safe-text-v1"
            or maximum_probes != 4
            or minimum_evidence != 4
            or policy != "audited_retention"
        ):
            raise ValueError("Composed execution requires the exact four-audit policy")
        super().__init__(
            source_id,
            config,
            inputs,
            library_state=library_state,
            maximum_work=maximum_work,
            synthesis_work=synthesis_work,
            maximum_probes=4,
            minimum_evidence=4,
            policy="audited_retention",
            state=state,
            library_factory=CompositionalTextLibrary,
            executor=execute_checked,
            config_validator=normalize_config,
        )
