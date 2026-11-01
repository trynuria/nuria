"""Independent report oracles and conventional controls, frozen before capture."""

from collections import Counter, deque


def expected(kind, data):
    if kind == "pull-requests":
        rows = data["pull_requests"]
        out = {
            "total": len(rows),
            "merged": [],
            "open": [],
            "closed_unmerged": [],
            "open_drafts": [],
            "evidence": "captured_public_pull_requests",
        }
        for row in rows:
            if row["merged_at"] is not None:
                out["merged"].append(row["number"])
            elif row["state"] == "open":
                out["open"].append(row["number"])
                if row["draft"] is True:
                    out["open_drafts"].append(row["number"])
            elif row["state"] == "closed":
                out["closed_unmerged"].append(row["number"])
        for name in ["merged", "open", "closed_unmerged", "open_drafts"]:
            out[name].sort()
        return out
    if kind == "commit-activity":
        unique = {row["sha"]: row for row in data["commits"]}
        days = sorted({row["committed_at"][:10] for row in unique.values()})
        return {
            "head": data["head"],
            "unique_commits": len(unique),
            "committer_days": [
                {
                    "day": day,
                    "commits": sum(
                        r["committed_at"][:10] == day for r in unique.values()
                    ),
                    "merges": sum(
                        r["committed_at"][:10] == day and len(r["parent_shas"]) > 1
                        for r in unique.values()
                    ),
                }
                for day in days
            ],
            "different_author_and_committer_times": sum(
                r["authored_at"] != r["committed_at"] for r in unique.values()
            ),
            "evidence": "captured_git_commit_metadata",
        }
    if kind == "component-ancestry":
        graph = {row["sha"]: row["parent_shas"] for row in data["commits"]}
        distance = {data["head"]: 0}
        # Repeated relaxation is independent of the conventional BFS control.
        for _ in range(len(graph) + 1):
            changed = False
            for child, parents in graph.items():
                if child not in distance:
                    continue
                for parent in parents:
                    proposal = distance[child] + 1
                    if proposal < distance.get(parent, float("inf")):
                        distance[parent] = proposal
                        changed = True
            if not changed:
                break
        missing = sorted(set(distance) - set(graph))
        components = []
        for name in sorted(data["reported_components"]):
            commit = data["reported_components"][name]
            reachable = (
                None
                if commit is None
                else True
                if commit in distance
                else None
                if missing
                else False
            )
            components.append(
                {
                    "component": name,
                    "reported_commit": commit,
                    "reachable": reachable,
                    "minimum_parent_hops": distance.get(commit),
                }
            )
        return {
            "head": data["head"],
            "graph_nodes": len(graph),
            "unobserved_parent_ids": missing,
            "components": components,
            "direct_service_health": "unknown",
            "evidence": "observed_git_ancestry_only",
        }
    if kind == "reported-ci":
        heads = {}
        for label, head in data["heads"].items():
            selected = None
            for row in sorted(
                data["workflow_runs"], key=lambda r: (r["updated_at"], r["id"])
            ):
                if row["head_sha"] == head:
                    selected = row
            status = "unknown"
            if selected and selected["status"] == "completed":
                if selected["conclusion"] == "success":
                    status = "passed"
                elif selected["conclusion"] in [
                    "failure",
                    "timed_out",
                    "action_required",
                ]:
                    status = "failed"
            heads[label] = {
                "head": head,
                "latest_observed_run_id": selected["id"] if selected else None,
                "reported_ci": status,
            }
        return {
            "heads": heads,
            "observed_runs": len(data["workflow_runs"]),
            "total_reported_runs": data["total_reported_runs"],
            "coverage": "observed_runs_only",
            "direct_service_health": "unknown",
        }
    raise ValueError("Unknown report profile")


def conventional(kind, data):
    if kind == "pull-requests":
        rows = data["pull_requests"]
        return {
            "total": len(rows),
            "merged": sorted(r["number"] for r in rows if r["merged_at"] is not None),
            "open": sorted(
                r["number"]
                for r in rows
                if r["merged_at"] is None and r["state"] == "open"
            ),
            "closed_unmerged": sorted(
                r["number"]
                for r in rows
                if r["merged_at"] is None and r["state"] == "closed"
            ),
            "open_drafts": sorted(
                r["number"]
                for r in rows
                if r["merged_at"] is None
                and r["state"] == "open"
                and r["draft"] is True
            ),
            "evidence": "captured_public_pull_requests",
        }
    if kind == "commit-activity":
        rows = list({r["sha"]: r for r in data["commits"]}.values())
        totals = Counter(r["committed_at"][:10] for r in rows)
        merges = Counter(
            r["committed_at"][:10] for r in rows if len(r["parent_shas"]) > 1
        )
        return {
            "head": data["head"],
            "unique_commits": len(rows),
            "committer_days": [
                {"day": day, "commits": totals[day], "merges": merges[day]}
                for day in sorted(totals)
            ],
            "different_author_and_committer_times": len(
                [r for r in rows if r["authored_at"] != r["committed_at"]]
            ),
            "evidence": "captured_git_commit_metadata",
        }
    if kind == "component-ancestry":
        graph = {r["sha"]: r["parent_shas"] for r in data["commits"]}
        distance = {data["head"]: 0}
        queue = deque([data["head"]])
        while queue:
            current = queue.popleft()
            for parent in graph.get(current, []):
                if parent not in distance:
                    distance[parent] = distance[current] + 1
                    queue.append(parent)
        missing = sorted(sha for sha in distance if sha not in graph)
        rows = [
            {
                "component": name,
                "reported_commit": sha,
                "reachable": None
                if sha is None
                else sha in distance
                if not missing or sha in distance
                else None,
                "minimum_parent_hops": distance.get(sha),
            }
            for name, sha in sorted(data["reported_components"].items())
        ]
        return {
            "head": data["head"],
            "graph_nodes": len(graph),
            "unobserved_parent_ids": missing,
            "components": rows,
            "direct_service_health": "unknown",
            "evidence": "observed_git_ancestry_only",
        }
    if kind == "reported-ci":
        heads = {}
        for label, head in data["heads"].items():
            matches = [r for r in data["workflow_runs"] if r["head_sha"] == head]
            latest = (
                max(matches, key=lambda r: (r["updated_at"], r["id"]))
                if matches
                else None
            )
            state = "unknown"
            if latest and latest["status"] == "completed":
                state = {
                    "success": "passed",
                    "failure": "failed",
                    "timed_out": "failed",
                    "action_required": "failed",
                }.get(latest["conclusion"], "unknown")
            heads[label] = {
                "head": head,
                "latest_observed_run_id": latest["id"] if latest else None,
                "reported_ci": state,
            }
        return {
            "heads": heads,
            "observed_runs": len(data["workflow_runs"]),
            "total_reported_runs": data["total_reported_runs"],
            "coverage": "observed_runs_only",
            "direct_service_health": "unknown",
        }
    raise ValueError("Unknown report profile")
