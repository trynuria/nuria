"""Persistent useful-work missions, frozen checks, and reviewed tool retention."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import zipfile
from contextlib import ExitStack, closing
from pathlib import Path

from agency.learning import VERSION as LEARNING_VERSION
from agency.learning import ExperienceMemory, bounded, procedure
from agency.model import BridgeModel, Gateway, PersistentAgent, working_context
from agency.policy import JUDGE, REVIEW, SYSTEM
from agency.sandbox import ShellWorkspace, fetch_public, files_under
from scripts.check_publication import RULES
from studio.store import Store, atomic, digest, now
from studio.worker import BudgetExhausted, Client


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def public_text(text):
    if any(re.search(pattern, text) for _, pattern in RULES):
        raise ValueError(
            "Artifact contains material excluded by the publication policy"
        )
    return text


class Mission:
    def __init__(self, root, shell, gateway, public, library, memory=None):
        self.root, self.shell, self.gateway = Path(root), shell, gateway
        self.public, self.library = Path(public), Path(library)
        self.memory = memory
        self.path = self.root / "mission.json"
        self.data = read(
            self.path,
            {
                "id": self.root.name,
                "status": "exploring",
                "started_at": now(),
                "events": [],
            },
        )

    def save(self):
        self.data["updated_at"] = now()
        atomic(self.path, self.data)

    def get_template_vars(self, **kwargs):
        return kwargs

    def serialize(self):
        return {"mission_id": self.root.name}

    def checker(self, code, label):
        receipt = self.root / "executions" / label / "result.json"
        if receipt.exists():
            return read(receipt)
        directory = receipt.parent
        if directory.exists():
            # A checker cannot mutate /work. Interrupted checks can safely rerun.
            directory.rename(
                directory.with_name(
                    label + "-interrupted-" + str(len(list(directory.parent.iterdir())))
                )
            )
        return self.shell.execute(code, label, checker=True)

    def execution_evidence(self, artifacts):
        """Give the reviewer actual receipts bound to the submitted file bytes."""
        identity = digest(artifacts)
        matches = []
        for path in sorted((self.root / "actions").glob("*.json")):
            action = read(path)
            result = action.get("result", {})
            if (
                action.get("state") == "completed"
                and action.get("action", {}).get("kind") == "exec"
                and result.get("workspace_sha256") == identity
            ):
                matches.append(
                    {
                        "action_id": action["action"]["action_id"],
                        "command": action["action"].get("command"),
                        "result": result,
                    }
                )
        receipts, size = [], 0
        for record in reversed(matches):
            encoded = len(json.dumps(record).encode())
            if size + encoded > 12000 or len(receipts) >= 4:
                continue
            receipts.insert(0, record)
            size += encoded
        return {
            "workspace_sha256": identity,
            "receipts": receipts,
            "matching_receipts": len(matches),
            "omitted_receipts": len(matches) - len(receipts),
            "scope": "Recorded commands and outputs on these exact workspace bytes. Agent-authored tests remain distinct from frozen independent acceptance. Older or different-source receipts confer no verification credit.",
        }

    def goal(self, action):
        if self.data.get("goal"):
            return {
                "accepted": True,
                "criteria": self.data["criteria"],
                "goal": self.data["goal"],
            }
        if not any(e["kind"] in ("exec", "fetch") for e in self.data["events"]):
            return {
                "accepted": False,
                "reason": "Inspect actual evidence before proposing a goal",
            }
        if files_under(self.shell.work):
            raise ValueError("Goal must be frozen before implementation")
        review_path = self.root / "reviews" / (action["action_id"] + "-goal.json")
        review = read(review_path)
        if review is None:
            review = self.gateway.decision(
                action["action_id"] + "-goal",
                JUDGE,
                {
                    "proposal": action,
                    "observed_trajectory": working_context(
                        read(self.root / "observations" / "trajectory.json", []), 25000
                    ),
                },
                5000,
            )
            atomic(review_path, review)
        code = review.get("checks")
        if (
            review.get("approved") is not True
            or not isinstance(code, str)
            or len(code.encode()) > 18000
        ):
            return {
                "accepted": False,
                "reason": review.get("reason", "Missing bounded independent checks"),
            }
        try:
            compile(code, "<frozen-acceptance>", "exec")
        except SyntaxError:
            return {
                "accepted": False,
                "reason": "Independent checker did not compile; propose a tractable complete interface",
            }
        negative = self.checker(code, action["action_id"] + "-before")
        if negative["exit_code"] == 0 or negative["timed_out"]:
            return {
                "accepted": False,
                "reason": "Independent checks failed the before-implementation qualification",
            }
        atomic(
            self.root / "acceptance.json",
            {
                "checks": code,
                "sha256": hashlib.sha256(code.encode()).hexdigest(),
                "before": negative,
            },
        )
        self.data.update(
            status="building",
            goal={k: v for k, v in action.items() if k not in ("kind", "action_id")},
            criteria=review.get("criteria", []),
        )
        self.save()
        atomic(
            self.root / "observations" / "goal.json",
            {"goal": self.data["goal"], "criteria": self.data["criteria"]},
        )
        return {
            "accepted": True,
            "criteria": self.data["criteria"],
            "reason": review.get("reason"),
            "instruction": "Implement the proposed interface in /work; frozen checks will run on submission.",
        }

    def submit(self, action):
        if not self.data.get("goal"):
            return {
                "accepted": False,
                "reason": "A goal must be accepted before submission",
            }
        found = files_under(self.shell.work)
        if {"delivery.json", "NURIA-DELIVERY.json"} & found.keys():
            raise ValueError(
                "Delivery metadata filenames are reserved; rename the generated file"
            )
        names = action.get("artifacts", [])
        if (
            not isinstance(names, list)
            or not names
            or any(not isinstance(n, str) or n not in found for n in names)
        ):
            raise ValueError("List the existing normal workspace files to deliver")
        # Review every workspace source: no unreviewed helper can influence checks.
        sources = {n: public_text(p.read_text()) for n, p in found.items()}
        artifacts = {
            n: hashlib.sha256(p.read_bytes()).hexdigest() for n, p in found.items()
        }
        if len(json.dumps(sources)) > 60000:
            raise ValueError(
                "Complete review input exceeds 60 KB; remove generated data/caches and keep this delivery focused"
            )
        acceptance = read(self.root / "acceptance.json")
        code = acceptance["checks"]
        if hashlib.sha256(code.encode()).hexdigest() != acceptance["sha256"]:
            raise ValueError("Frozen acceptance identity changed")
        result = self.checker(code, action["action_id"] + "-after")
        if result["exit_code"] != 0 or result["timed_out"]:
            return {
                "accepted": False,
                "reason": "Frozen acceptance checks failed; repair the implementation",
                "checks": result,
            }
        review_path = self.root / "reviews" / (action["action_id"] + "-delivery.json")
        review = read(review_path)
        if review is None:
            review = self.gateway.decision(
                action["action_id"] + "-delivery",
                REVIEW,
                {
                    "goal": self.data["goal"],
                    "criteria": self.data["criteria"],
                    "submission": action,
                    "sources": sources,
                    "acceptance": acceptance,
                    "after": result,
                    "execution_evidence": self.execution_evidence(artifacts),
                    "learning_draft": self.data.get("learning_draft"),
                    "lesson_applications": self.memory.applications(self.root.name)
                    if self.memory
                    else [],
                    "applied_lessons": [
                        self.memory.get(ident)
                        for ident in dict.fromkeys(
                            u["id"] for u in self.memory.applications(self.root.name)
                        )
                    ]
                    if self.memory
                    else [],
                },
                2200,
            )
            atomic(review_path, review)
        if self.memory:
            self.memory.ingest(self.root)
        if review.get("approved") is not True:
            return {"accepted": False, "review": review}
        # Deliver the exact complete reviewed workspace; exclude nothing used by tests.
        record = {
            "id": self.root.name,
            "goal": self.data["goal"],
            "criteria": self.data["criteria"],
            "usage": action.get("usage"),
            "claims": action.get("claims", []),
            "review": review,
            "before": acceptance["before"],
            "after": result,
            "acceptance_sha256": acceptance["sha256"],
            "artifacts": artifacts,
            "accepted_at": now(),
            "download": f"/studio/deliveries/{self.root.name}.zip",
        }
        target = self.library / self.root.name
        target.mkdir(exist_ok=True)
        for name, source in found.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        atomic(target / "delivery.json", record)
        self.public.mkdir(parents=True, exist_ok=True)
        temporary = self.public / "agency-artifact.zip.tmp"
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as bundle:
            for name, source in found.items():
                bundle.write(source, name)
            bundle.writestr("NURIA-DELIVERY.json", json.dumps(record, indent=2))
        os.replace(temporary, self.public / "agency-artifact.zip")
        (self.public / "agency-artifact.zip").chmod(0o644)
        archive = self.public / "deliveries" / (self.root.name + ".zip")
        archive.parent.mkdir(exist_ok=True)
        shutil.copyfile(
            self.public / "agency-artifact.zip", archive.with_suffix(".zip.tmp")
        )
        os.replace(archive.with_suffix(".zip.tmp"), archive)
        archive.chmod(0o644)
        self.data.update(status="accepted", delivery=record)
        self.save()
        if self.memory:
            self.memory.ingest(self.root)
        return {"terminal": True, "status": "accepted", "delivery": record}

    def execute(self, action, cwd=""):
        identifier = action.get("action_id", "")
        if not re.fullmatch(r"act-[0-9]{3}-[a-f0-9]{12}", identifier):
            raise ValueError("Host action identity is missing")
        path = self.root / "actions" / (identifier + ".json")
        previous = read(path)
        if previous and previous["state"] == "completed":
            return previous["result"]
        kind = action.get("kind")
        if previous and kind in ("exec", "fetch"):
            result = {
                "interrupted": True,
                "reason": "The previous action may have executed before restart. Inspect /work and its full outputs; it was not blindly repeated.",
            }
        else:
            atomic(path, {"state": "running", "action": action, "started_at": now()})
            try:
                if "working_state" in action:
                    state = action["working_state"]
                    if (
                        not isinstance(state, dict)
                        or len(json.dumps(state).encode()) > 3500
                    ):
                        raise ValueError(
                            "Working state must be a bounded structured plan"
                        )
                    if set(state) - {"facts", "steps", "open_questions", "next_test"}:
                        raise ValueError("Unknown working-state field")
                    self.data["working_state"] = state
                    atomic(self.root / "observations" / "working-state.json", state)
                    atomic(self.root / "plans" / (identifier + ".json"), state)
                    self.save()
                if "procedure" in action and kind == "submit" and self.memory:
                    draft = procedure(action["procedure"])
                    if (
                        draft.get("revises")
                        and self.memory.get(draft["revises"]) is None
                    ):
                        raise ValueError("Revision must reference a retained lesson")
                    self.data["learning_draft"] = draft
                    atomic(
                        self.root / "learning-drafts" / (identifier + ".json"),
                        self.data["learning_draft"],
                    )
                    self.save()
                if self.memory and action.get("uses"):
                    self.memory.declare_use(self.root.name, identifier, action["uses"])
                if kind == "exec":
                    result = self.shell.execute(
                        action.get("command"),
                        identifier,
                        writable=self.data.get("status") == "building",
                    )
                    result["workspace_sha256"] = digest(
                        {
                            name: hashlib.sha256(path.read_bytes()).hexdigest()
                            for name, path in files_under(self.shell.work).items()
                        }
                    )
                elif kind == "fetch":
                    full = fetch_public(action.get("url"))
                    atomic(self.root / "observations" / (identifier + ".json"), full)
                    result = {
                        **full,
                        "text": full.get("text", "")[:10000],
                        "full_output": "/observations/" + identifier + ".json",
                    }
                elif kind == "goal":
                    result = self.goal(action)
                elif kind == "submit":
                    result = self.submit(action)
                elif kind == "recall" and self.memory:
                    result = {
                        "lessons": self.memory.search(
                            bounded(action.get("query"), 5000)
                        )
                    }
                    atomic(
                        self.root / "observations" / (identifier + "-memory.json"),
                        result,
                    )
                elif kind == "learn" and self.memory:
                    draft = procedure(action.get("procedure"))
                    if (
                        draft.get("revises")
                        and self.memory.get(draft["revises"]) is None
                    ):
                        raise ValueError("Revision must reference a retained lesson")
                    self.data["learning_draft"] = draft
                    atomic(
                        self.root / "learning-drafts" / (identifier + ".json"), draft
                    )
                    result = {
                        "saved": True,
                        "status": "provisional",
                        "instruction": "Independent delivery review must approve this method before retention",
                    }
                elif kind == "abandon":
                    self.data.update(
                        status="abandoned",
                        reason=action.get("reason", "No reason supplied"),
                    )
                    result = {"terminal": True, "status": "abandoned"}
                elif kind == "resume":
                    target = action.get("mission", "")
                    if (
                        self.data.get("goal")
                        or not re.fullmatch(r"mission-[0-9]{4,}", target)
                        or target == self.root.name
                    ):
                        raise ValueError(
                            "Resume a retained unfinished mission before selecting a new goal"
                        )
                    previous_mission = read(
                        self.root.parent / target / "mission.json", {}
                    )
                    if previous_mission.get("status") not in (
                        "abandoned",
                        "unresolved",
                    ):
                        raise ValueError(
                            "Only retained abandoned or unresolved work can be reopened"
                        )
                    self.data.update(
                        status="revisit",
                        reason=action.get("reason"),
                        resume_mission=target,
                    )
                    result = {
                        "terminal": True,
                        "status": "revisit",
                        "resume_mission": target,
                    }
                else:
                    result = {
                        "error": "Use exec, fetch, goal, submit, abandon, resume, recall or learn with the required fields"
                    }
            except (ValueError, OSError, UnicodeError) as error:
                result = {"error": type(error).__name__ + ": " + str(error)[:500]}
        atomic(
            path,
            {
                "state": "completed",
                "action": action,
                "result": result,
                "finished_at": now(),
            },
        )
        self.data["events"].append(
            {
                "at": now(),
                "kind": kind,
                "reason": str(action.get("reason", action.get("title", kind)))[:500],
                "action_id": identifier,
                "exit_code": result.get("exit_code"),
                "accepted": result.get("accepted"),
                "error": result.get("error"),
            }
        )
        self.save()
        return result


def run(args):
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "runner.lock").open("w") as lock, ExitStack() as resources:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        store, client = Store(args.studio_root), Client(args.gateway, args.token_file)
        public, library = Path(args.studio_root) / "public", root / "library"
        library.mkdir(exist_ok=True)
        memory = resources.enter_context(
            closing(ExperienceMemory(library / "learning"))
        )
        for prior in sorted((root / "missions").glob("mission-*")):
            memory.ingest(prior)
        state = read(root / "state.json", {"next_mission": 1, "active": None})

        def publish(phase, mission=None, detail=None):
            state.update(
                phase=phase,
                updated_at=now(),
                budget=client.budget(),
                learning_version=LEARNING_VERSION,
            )
            atomic(root / "state.json", state)
            data = (
                mission.data
                if mission
                else read(
                    root
                    / "missions"
                    / (state.get("active") or state.get("last_mission") or "none")
                    / "mission.json",
                    {},
                )
            )
            title = data.get("goal", {}).get(
                "title", "Choosing a useful problem from real evidence"
            )
            summary = f"{title}. {detail or data.get('status', phase)}. Runs on Cherry; original shared allowance applies."
            store.put(
                "meta",
                "research",
                {
                    "name": "Nuria research agent",
                    "mode": "agency",
                    "phase": phase,
                    "updated_at": state["updated_at"],
                    "summary": summary,
                    "mission": state["active"],
                    "artifact_available": (public / "agency-artifact.zip").is_file(),
                    "title": title,
                    "activity": data.get("events", [])[-5:],
                    "criteria": data.get("criteria", []),
                },
            )
            report = {
                "runtime": "mini-swe-agent 2.4.6 with Nuria gateway, persistent offline tools and independent acceptance contexts",
                "state": state,
                "current": data,
                "history": read(library / "memory.json", []),
                "learning": memory.export(),
                "scope": "Autonomous software work inside a bounded tool environment. Model-written checks and reviews can be wrong; no AGI, scientific novelty or general superiority claim. Accepted artifacts are downloads and sandbox-reusable tools, not automatic production code changes.",
            }
            atomic(public / "agency-results.json", report)
            (public / "agency-results.json").chmod(0o644)
            print(
                json.dumps(
                    {
                        "phase": phase,
                        "mission": state["active"],
                        "title": title,
                        "remaining_micro_usd": state["budget"]["remaining_micro_usd"],
                    }
                ),
                flush=True,
            )

        budget = client.budget()
        if (
            state.get("phase") == "budget_paused"
            and state.get("learning_version") == LEARNING_VERSION
            and budget["remaining_micro_usd"]
            <= state.get("budget", {}).get("remaining_micro_usd", 0)
        ):
            publish(
                "budget_paused",
                detail="Checkpoint retained at the original allowance; no new inference requested",
            )
            return
        if (
            budget["blocked"]
            or budget["remaining_micro_usd"] <= 0
            or budget.get("remaining_requests", 0) <= 0
        ):
            publish("budget_paused", detail="Original inference allowance unavailable")
            return
        if not state["active"]:
            if budget["remaining_micro_usd"] < 120000:
                publish(
                    "budget_paused",
                    detail="Remaining allowance is below the reserve for a new complete mission",
                )
                return
            state["active"] = f"mission-{state['next_mission']:04d}"
            state["next_mission"] += 1
            atomic(root / "state.json", state)
        work = root / "missions" / state["active"]
        shell = ShellWorkspace(
            work,
            args.source,
            library,
            bwrap=args.bwrap,
            dependencies=args.dependencies,
            node=args.node,
        )
        gateway = Gateway(store, client, work)
        mission = Mission(work, shell, gateway, public, library, memory=memory)
        mission.save()
        agent = PersistentAgent(
            BridgeModel(gateway, mission, memory),
            mission,
            system_template=SYSTEM,
            instance_template="{{ task }}",
            cost_limit=0,
            output_path=work / "agent.json",
        )
        if not agent.restore():
            current = Path(args.studio_root) / "public" / "status.json"
            if current.exists():
                shutil.copyfile(current, shell.observations / "public-status.json")
            agent.add_messages(
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "mission": state["active"],
                            "instruction": "Inspect /repo, /observations and /library. Choose the next worthwhile goal yourself, demonstrate the problem, and deliver useful work. Read prior outcomes first. Do not spend many actions browsing; prefer a focused complete intervention.",
                            "remaining_shared_usd": budget["remaining_micro_usd"]
                            / 1000000,
                            "outcomes": read(library / "memory.json", [])[-5:],
                        }
                    ),
                },
            )
        environment = {
            "command_file_limit_mib": 8,
            "checker_temporary_file_limit_mib": 128,
            "wall_seconds_per_tool": 25,
            "cpu_seconds_per_process": 15,
            "service_memory_mib": 1024,
            "resume_action": "Before selecting a new goal, you may use {kind: resume, mission: mission-NNNN, reason: what changed} to reopen a retained abandoned/unresolved mission from outcome memory. Its files and frozen tests are restored; no regeneration or weakened acceptance.",
            "note": "Checker fixtures have a separate larger disposable-file allowance. Acceptance code and criteria remain frozen. Runtime changes do not reset mission history or inference accounting.",
        }
        environment_path = shell.observations / "runtime.json"
        if state.get("resume_from") and agent.messages[-1]["role"] == "exit":
            mission.data.setdefault("resumptions", []).append(
                {
                    "at": now(),
                    "from": state["resume_from"],
                    "prior_status": mission.data["status"],
                    "prior_reason": mission.data.get("reason"),
                }
            )
            mission.data["status"] = (
                "building" if mission.data.get("goal") else "exploring"
            )
            mission.data["attempt_start_call"] = agent.n_calls
            mission.save()
            agent.add_messages(
                {
                    "role": "user",
                    "content": "Your later mission elected to resume this retained work. Workspace and acceptance checks are unchanged. Inspect the current runtime and prior failure, then continue from the saved files.",
                }
            )
            state.pop("resume_from")
            atomic(root / "state.json", state)
        if (
            agent.messages[-1]["role"] == "user"
            and read(environment_path) != environment
        ):
            atomic(environment_path, environment)
            agent.add_messages(
                {
                    "role": "user",
                    "content": "Current execution environment: "
                    + json.dumps(environment),
                }
            )
        try:
            for _ in range(args.steps):
                publish("researching", mission)
                if (
                    agent.n_calls - mission.data.get("attempt_start_call", 0) >= 28
                    and agent.messages[-1]["role"] != "assistant"
                ):
                    mission.data.update(
                        status="unresolved",
                        reason="Per-mission effort limit reached; work and failures retained",
                    )
                    mission.save()
                    agent.add_messages(
                        {"role": "exit", "content": "Mission effort limit"}
                    )
                agent.resume_step()
                if agent.messages[-1]["role"] == "exit":
                    memory.ingest(work)
                    outcomes = read(library / "memory.json", [])
                    outcome = {
                        "id": mission.data["id"],
                        "status": mission.data["status"],
                        "goal": mission.data.get("goal"),
                        "lesson": mission.data.get("delivery", {})
                        .get("review", {})
                        .get("lesson", mission.data.get("reason", "Unresolved")),
                        "delivery": mission.data.get("delivery"),
                    }
                    outcomes = [m for m in outcomes if m["id"] != outcome["id"]] + [
                        outcome
                    ]
                    atomic(library / "memory.json", outcomes)
                    state["last_mission"] = mission.data["id"]
                    state["active"] = mission.data.get("resume_mission")
                    if state["active"]:
                        state["resume_from"] = mission.data["id"]
                    publish(
                        "research_waiting",
                        mission,
                        "Outcome retained; next useful goal will be selected on Cherry",
                    )
                    return
            publish(
                "research_waiting",
                mission,
                "Workspace checkpointed; Cherry resumes this mission automatically",
            )
        except BudgetExhausted:
            publish(
                "budget_paused",
                mission,
                "Next request exceeds the original allowance; checkpoint retained",
            )
        except Exception:
            publish(
                "needs_attention",
                mission,
                "Execution interrupted; durable requests and actions are retained for safe recovery",
            )
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--studio-root", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--gateway", default="http://127.0.0.1:4041")
    parser.add_argument("--bwrap", default="/opt/nuria/studio-bin/bwrap")
    parser.add_argument("--node", default="/opt/nuria/node")
    parser.add_argument(
        "--dependencies", nargs="*", default=["/opt/nuria/research-deps"]
    )
    parser.add_argument("--steps", type=int, default=5)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
