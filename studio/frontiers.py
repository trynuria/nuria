"""A diverse creative portfolio, with explicit exploration and improvement."""

FRONTIERS = (
    {
        "id": "visual",
        "name": "Visual instruments",
        "challenge": "Invent a visual instrument that turns a person's choices into a striking, useful image they can export as SVG. Think impossible typography, living diagrams, kinetic posters or new drawing constraints. Give the person authorship and a result worth keeping.",
    },
    {
        "id": "game",
        "name": "Games with new rules",
        "challenge": "Invent a small, immediately playable game with an unusual rule the player can discover through action. Create tension, a clear goal, a readable win/loss condition, and a reason to try again. The mechanic should be explainable in one sentence and surprising in use.",
    },
    {
        "id": "useful",
        "name": "Useful little machines",
        "challenge": "Invent a genuinely useful local tool: a constraint-solving planner, unusual comparison instrument, layout optimizer, idea recombiner or decision aid. Let people enter or change real inputs, inspect a concrete result, and keep it. Demonstrate utility without claiming real-world outcomes you have not measured.",
    },
    {
        "id": "world",
        "name": "Worlds to interfere with",
        "challenge": "Invent an explorable world with a surprising causal rule. Let visitors intervene, observe second-order consequences, undo or compare an alternate history. Prefer an unusual physical, social or ecological mechanism over generic particles. Make its fictional nature clear in one brief sentence.",
    },
    {
        "id": "story",
        "name": "Story machines",
        "challenge": "Invent a participatory story machine, playable comic, spatial poem or character system whose rules create surprising changes in meaning. Visitors must change something consequential and get a coherent, shareable artifact or ending. Avoid generic random quote generators.",
    },
    {
        "id": "sound",
        "name": "Rhythm and motion",
        "challenge": "Invent a rhythm, movement or sound-composition instrument with a new interaction. Web Audio may begin only after a user gesture; provide a visual equivalent and an exportable score. Help visitors make a deliberate composition, not just watch random noise.",
    },
)

ARTIFACT = """Build a complete offline browser invention in index.html, style.css,
app.js and engine.js. Use native HTML/CSS/SVG/canvas/Web Audio; no dependencies,
network requests, external assets, frames or forms. Use system fonts. Load
engine.js before app.js. At 360px and 1280px it must be legible and usable without
horizontal overflow. Use labeled keyboard-accessible controls. Create a strong
visual identity appropriate to THIS invention, not another dark audit dashboard.
Put technical tests/provenance in a small optional details panel, not the main flow.
Make a visitor's first action and its payoff obvious. Use domain-appropriate
controls; Energy/Curiosity sliders and animation are NOT required. Export must
actually work if promised, with a selectable text fallback. No localStorage is
required: creations run in an opaque-origin sandbox. Catch optional storage errors.

engine.js assigns globalThis.NuriaEngine = {create(seed, source), act(state, action),
metrics(state), runTests()}. The engine has no DOM. create returns a JSON-serializable
state; act applies an explicit action and returns state (mutation allowed).
Seed random behavior deterministically. metrics returns at least one finite numeric
measurement meaningful to the work. The frozen engine_actions and alternate_actions
in the contract run at several seeds: replay must agree and different actions must
produce a different outcome. Include at least THREE meaningful, named logic tests in
runTests(): return {passed:boolean,results:[{name,pass:boolean,...measuredOutputs}]}.
The host executes them, including failures, with a four-second bound. Test an edge
case and a round trip or reset; do not write self-fulfilling assertions.

The frozen browser journey is exercised in Chromium at desktop and phone widths.
Its controls and result selector must exist and be visible. Clicking/filling must
change the rendered result. A requested download must yield nonempty bytes.
The browser reports JavaScript errors, missing resources, overflow, before/after
DOM observations and actual interaction results. These findings are returned for
repair. The host also retains screenshots. Do not claim a browser pass yourself.
Expose a clear primary control using data-nuria-primary and a meaningful result
region using data-nuria-result. Match the selectors declared by the concept.
Keep total editable source compact, ideally below 40 KB. Implement the whole useful
experience first, then improve the weakest behavior from the actual feedback.
Host-managed inputs.json, contract.json, toolkit.js and toolkit-bridge.js are never
editable. A supplied toolkit is optional unless the concept explicitly chose it;
use its actual methods and load the bridge before your engine. A fresh mechanism
is encouraged. Source neural measurements are optional inspiration, not a required
theme, data dependency or claim about intelligence."""


def next_direction(projects):
    """Fill different areas first, then alternate exploration and useful repairs."""
    recent = [p for p in projects if p.get("contract_version") == 2]
    counts = {
        f["id"]: sum(p.get("frontier", {}).get("id") == f["id"] for p in recent)
        for f in FRONTIERS
    }
    frontier = min(
        FRONTIERS,
        key=lambda f: (
            counts[f["id"]],
            next(i for i, item in enumerate(FRONTIERS) if item == f),
        ),
    )
    completed = [p for p in recent if p["status"] == "complete"]
    # Three different explorations precede one focused improvement. No chain
    # can continually rewrite its own critique into the next assignment.
    if len(recent) % 4 == 3 and completed:
        eligible = [p for p in completed if p.get("mode") != "improve"]
        if eligible:
            parent = max(
                eligible,
                key=lambda p: (p.get("review", {}).get("usefulness", 0), p["id"]),
            )
            direction = next(
                f for f in FRONTIERS if f["id"] == parent["frontier"]["id"]
            )
            brief = (
                "Improve this proven direction with a visible user benefit, preserving the working core. "
                + parent.get("review", {}).get("next_brief", direction["challenge"])
            )
            return direction, "improve", brief[:5000], parent["id"]
    return (
        frontier,
        "explore",
        frontier["challenge"]
        + " Invent a new central mechanism and interaction. A renamed echo/bell/reserve simulator, a replay audit, or a cosmetic reskin does not count as a new direction.",
        None,
    )


def validate_concept(concept):
    if not isinstance(concept, dict) or any(
        not isinstance(concept.get(k), str) or not concept[k].strip()
        for k in ("title", "thesis", "interaction", "mechanism", "payoff")
    ):
        raise ValueError(
            "Every concept needs a title, thesis, interaction, mechanism and visitor payoff"
        )
    for field in ("engine_actions", "alternate_actions"):
        values = concept.get(field)
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 12
            or any(
                not isinstance(v, dict) or not isinstance(v.get("type"), str)
                for v in values
            )
        ):
            raise ValueError("Declare bounded, typed engine action sequences")
    if concept["engine_actions"] == concept["alternate_actions"]:
        raise ValueError("The alternate action sequence must differ")
    journey = concept.get("journey", {})
    steps = journey.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
        raise ValueError("Declare one to eight browser actions")
    for step in steps:
        if (
            step.get("action") not in ("click", "fill", "select", "press")
            or not isinstance(step.get("selector"), str)
            or not 1 <= len(step["selector"]) <= 160
        ):
            raise ValueError("Unsupported browser action or selector")
        if len(str(step.get("value", ""))) > 500:
            raise ValueError("Browser fixture input is too large")
    if (
        not isinstance(journey.get("result"), str)
        or not 1 <= len(journey["result"]) <= 160
    ):
        raise ValueError("Declare the visible result selector")
    if journey.get("download") and (
        not isinstance(journey["download"], str) or len(journey["download"]) > 160
    ):
        raise ValueError("Invalid export selector")


def portfolio(projects):
    result = []
    for frontier in FRONTIERS:
        entries = [
            p for p in projects if p.get("frontier", {}).get("id") == frontier["id"]
        ]
        completed = [p for p in entries if p["status"] == "complete"]
        champion = (
            max(
                completed,
                key=lambda p: (p.get("review", {}).get("portfolio_score", 0), p["id"]),
            )
            if completed
            else None
        )
        result.append(
            {
                "id": frontier["id"],
                "name": frontier["name"],
                "projects": len(entries),
                "completed": len(completed),
                "champion": champion["id"] if champion else None,
            }
        )
    return result
