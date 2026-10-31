"""Source-bound policy learning from enrolled work and independent outcomes.

GEPA is optional. A separate validation cohort selects the policy; ties retain
the original. Artifacts are never activated by the optimizer itself.
"""

from __future__ import annotations

from cognition.outcome_worker import BASE_POLICY, identity, run_work


def learn_policy(
    reasoner,
    executor,
    store,
    *,
    training,
    validation,
    experience,
    base_policy=BASE_POLICY,
    max_evaluations=72,
    max_proposals=2,
):
    """Qualify a finite policy artifact without modifying the model's weights.

    The client must enforce a shared resource allowance. Specifications and
    host checkers are enrolled before optimization. No benchmark, provider
    discovery or background timer is built into this interface.
    """
    train_ids = {x["task_id"] for x in training}
    validation_ids = {x["task_id"] for x in validation}
    if (
        not train_ids
        or not validation_ids
        or train_ids & validation_ids
        or len(train_ids) != len(training)
        or len(validation_ids) != len(validation)
    ):
        raise ValueError("Distinct nonempty qualification cohorts are required")
    if not len(validation) <= max_evaluations <= 200 or not 0 <= max_proposals <= 8:
        raise ValueError("Policy optimization exceeds its finite enrollment")

    from gepa.optimize_anything import (
        EngineConfig,
        GEPAConfig,
        ReflectionConfig,
        optimize_anything,
    )

    def evaluate(policy, example):
        job = (
            "learn-"
            + identity([policy, example["task_id"], example["source_sha256"]])[:24]
        )
        result = run_work(
            reasoner,
            executor,
            store,
            example,
            job_id=job,
            policy=policy,
            experience=experience,
        )
        return float(result["accepted"] is True), {
            "specification": result["prompt"],
            "program": result["code"],
            "independent_outcome": result["checker"],
        }

    def reflect(prompt):
        messages = (
            prompt
            if isinstance(prompt, list)
            else [{"role": "user", "content": str(prompt)}]
        )
        return reasoner.complete(messages, max_tokens=2400, seed=0)["content"]

    result = optimize_anything(
        seed_candidate=base_policy,
        evaluator=evaluate,
        dataset=training,
        valset=validation,
        objective="Improve reliable execution on varied fresh Python work. Derive transferable implementation and checking strategies from actual outcomes. Do not memorize task IDs or bypass checks.",
        config=GEPAConfig(
            engine=EngineConfig(
                max_metric_calls=max_evaluations,
                max_candidate_proposals=max_proposals,
                parallel=False,
                use_cloudpickle=False,
                seed=0,
                candidate_selection_strategy="current_best",
                acceptance_criterion="improvement_or_equal",
            ),
            reflection=ReflectionConfig(
                reflection_lm=reflect, reflection_minibatch_size=6
            ),
        ),
    )
    artifact = {
        "version": "nuria.outcome-policy.v1",
        "policy": result.best_candidate,
        "policy_sha256": identity(result.best_candidate),
        "base_policy_sha256": identity(base_policy),
        "experience_sha256": identity(experience),
        "enrollment": {
            "training": [
                {k: x[k] for k in ("task_id", "source_sha256")} for x in training
            ],
            "validation": [
                {k: x[k] for k in ("task_id", "source_sha256")} for x in validation
            ],
        },
        "validation_scores": result.val_aggregate_scores,
        "metric_calls": result.total_metric_calls,
        "model": reasoner.configuration,
        "journal": store.verify(),
        "lineage": result.to_dict(),
        "activated": False,
        "optimizer": {"package": "gepa", "version": "0.1.4", "max_evaluations": max_evaluations,
                      "max_proposals": max_proposals, "reflection_minibatch": 6,
                      "candidate_screen": "improvement_or_equal", "validation_tie": "retain_original"},
    }
    artifact["artifact_sha256"] = identity(artifact)
    return artifact


def qualified_policy(artifact):
    """Check an explicitly supplied artifact's integrity, not its generality."""
    body = {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    if artifact.get("version") != "nuria.outcome-policy.v1" or artifact.get(
        "artifact_sha256"
    ) != identity(body):
        raise ValueError("Policy artifact identity differs")
    if artifact["policy_sha256"] != identity(artifact["policy"]):
        raise ValueError("Policy text differs from its qualification record")
    return artifact["policy"]
