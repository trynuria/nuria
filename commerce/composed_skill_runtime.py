"""Explicit four-audit text authority over the existing durable local runtime.

This is a separate provider and source. It cannot resume a numeric episode,
enable financial execution, or import tools from a task or checkpoint. Final
inputs and labels remain with the buyer; the session receives diagnostic inputs
and exactly the answers to its four recorded requests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from commerce.active_skill_runtime import ActiveSkillRuntime
from commerce.tasks import active_verifier_ceiling, digest

COMPOSED_PROVIDER = "local-composed-records-v1"
COMPOSED_SOURCE = "nuria:composed-record-repair-v1"
COMPOSED_POLICY = "composed_fixed4"


def validate_composed_authority(rule, provider, source_id):
    """A marker cannot broaden the reviewed language or four-answer policy."""
    if (
        type(rule) is not dict
        or rule.get("type") != "transform_active"
        or rule.get("policy") != COMPOSED_POLICY
        or rule.get("source_id") != COMPOSED_SOURCE
        or source_id != COMPOSED_SOURCE
        or provider != COMPOSED_PROVIDER
        or type(rule.get("config")) is not dict
        or rule["config"].get("version") != "safe-text-v1"
        or type(rule.get("maximum_probes")) is not int
        or rule["maximum_probes"] != 4
        or type(rule.get("minimum_evidence")) is not int
        or rule["minimum_evidence"] != 4
    ):
        raise ValueError(
            "Composed text requires its explicit source and four-audit policy"
        )


class ComposedSkillRuntime(ActiveSkillRuntime):
    """Keep reservation, recovery and buyer admission behavior unchanged."""

    def validate(self, row):
        task = row["contract"]
        if (
            self.adapter.config["mode"] != "active_composed"
            or self.adapter.id != COMPOSED_PROVIDER
            or self.adapter.config["source_id"] != COMPOSED_SOURCE
            or task["provider"] != self.adapter.id
            or task["kind"] != "compute"
            or task["maximum_micro_usd"] != 0
            or task.get("outcome")
        ):
            raise ValueError("Composed runtime has no authority for this task")
        validate_composed_authority(
            task["acceptance"], self.adapter.id, self.adapter.config["source_id"]
        )

    def _terms(self, row):
        import cognition.composed_skills as session
        import cognition.text_composition as solver

        self.validate(row)
        return digest(
            {
                "schema": "nuria.composed-skill-runtime.v1",
                "base_terms": super()._terms(row),
                "provider": self.adapter.id,
                "source_id": self.adapter.config["source_id"],
                "mode": "active_composed",
                "policy": COMPOSED_POLICY,
                "solver_id": session.SOLVER_ID,
                "implementation": {
                    module.__name__: hashlib.sha256(
                        Path(module.__file__).read_bytes()
                    ).hexdigest()
                    for module in (session, solver)
                }
                | {
                    "commerce.composed_skill_runtime": hashlib.sha256(
                        Path(__file__).read_bytes()
                    ).hexdigest()
                },
            }
        )

    def _session(self, row, *, state=None, library_state=None):
        from cognition.composed_skills import ComposedSkillSession

        self.validate(row)
        rule = row["contract"]["acceptance"]
        # No final evaluation row, diagnostic output vector or task recipe is
        # passed to the learner. Four answers arrive through the buyer interface.
        return ComposedSkillSession(
            self.adapter.config["source_id"],
            self.adapter.dsl_config,
            list(rule["diagnostic_inputs"]),
            library_state=library_state,
            maximum_work=rule["maximum_work"]
            - active_verifier_ceiling(self.adapter.dsl_config),
            maximum_probes=4,
            synthesis_work=rule["synthesis_work"],
            minimum_evidence=4,
            policy="audited_retention",
            state=state,
        )
