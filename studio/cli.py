"""Private operator controls; invoke over the project's existing SSH access."""

import argparse
import json

from studio.store import Store, now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("status", "pause", "resume"):
        sub.add_parser(name)
    submit = sub.add_parser("submit")
    submit.add_argument("brief")
    feedback = sub.add_parser("feedback")
    feedback.add_argument("project")
    feedback.add_argument("comment")
    args = parser.parse_args()
    store = Store(args.root)
    if args.action == "status":
        print(
            json.dumps(
                {
                    "projects": [
                        {k: p[k] for k in ("id", "title", "status")}
                        for p in store.all("projects")
                    ],
                    "control": store.get("meta", "control"),
                },
                indent=2,
            )
        )
    elif args.action in ("pause", "resume"):
        store.put("meta", "control", {"paused": args.action == "pause", "at": now()})
        print(
            "Pause applies after the current bounded step."
            if args.action == "pause"
            else "Campaign resumed within its existing allowance."
        )
    elif args.action == "submit":
        if not 10 <= len(args.brief) <= 6000:
            raise ValueError("A brief must contain 10–6000 characters")
        queue = store.get("meta", "queue") or []
        if len(queue) >= 8:
            raise ValueError("The pending brief queue is full")
        queue.append({"brief": args.brief, "at": now()})
        store.put("meta", "queue", queue)
        print("Brief queued within the existing shared allowance.")
    else:
        project = store.get("projects", args.project)
        if not project or not 1 <= len(args.comment) <= 3000:
            raise ValueError("A known project and bounded feedback are required")
        store.save(project, "operator_feedback", args.comment)
        taste = store.get("meta", "taste") or {"interests": [], "lessons": []}
        taste["lessons"] = (
            taste["lessons"]
            + [{"project": args.project, "lesson": args.comment, "source": "operator"}]
        )[-12:]
        store.put("meta", "taste", taste)
        print("Feedback retained for future project choices.")
    store.db.close()


if __name__ == "__main__":
    main()
