# Nuria

**Persistent agents, learning systems and verifiable model research.**

[Project](https://nuria.network/) · [Documentation](https://nuria.network/?page=docs) · [Research](research/) · [Weight editing results](https://github.com/scryintel/nuria-weight-space-proof) · [Capability map](docs/capability-map.md)

Nuria builds systems that carry state across time, learn from measured outcomes, and turn successful work into reusable capabilities. The codebase brings together persistent neural circuits, memory, software research agents, checked program execution and direct model-repair experiments.

The project publishes implementations alongside evaluation methods, comparison results and reproducible artifacts. Each research area has its own documented scope and operating status.

## What we are building

| Area                     | Implemented work                                                                                      | Explore                                                                                                   |
| ------------------------ | ----------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Persistent neural state  | Spiking circuits, episodic memory, outcome feedback, atomic journals and recoverable checkpoints      | [Architecture](docs/architecture.md), [cognition source](cognition/)                                      |
| Software research agents | Source inspection, executable missions, failure repair, artifact acceptance checks and retained tools | [Research runtime](agency/README.md), [program delivery](docs/program-delivery.md)                        |
| Reusable software skills | Accepted programs applied to later observations, bounded synthesis and checked composition            | [Program reuse](research/outcome-learning/retained-work-04/), [skill synthesis](research/guarded-skills/) |
| Model repair             | Direct weight editing with matched benchmark comparisons and verified checkpoint identities           | [Weight editing evidence](https://github.com/scryintel/nuria-weight-space-proof)                          |

## Weight editing research

Nuria independently reproduced Takehi and collaborators’ published edit on Qwen3.5-0.8B. Changing **130,188 stored weight values—roughly 0.016% of the model—with no gradient training** raised MATH-500 from **42/500 to 251/500 correct answers** in our matched research run.

The same edit improved fixed GSM8K and IFEval subsets. Combined math output tokens fell **39.6%**. The [public evidence package](https://github.com/scryintel/nuria-weight-space-proof) includes the exact model identities, paired outcomes, scoring receipts, attribution and methodology. Results use one sampled answer per question and apply to the evaluated research checkpoint.

## Research you can inspect

- **Persistent state and recovery:** neural state, inputs, memory and learning metadata commit together. Recovery restores the matching network and journal. [Implementation and architecture](docs/architecture.md#commit-and-recovery).
- **Checked tool reuse:** four accepted programs produced four accepted reports on later repository observations without another model request. [Transfer record and replay](research/outcome-learning/retained-work-04/README.md).
- **Skill composition:** a frozen generated-task comparison completed **438/576** exact jobs versus **337/576** for its same-language enumerative control. All **5,184** method-job records and the comparison conditions are published. [Complete study](research/guarded-skills/README.md#frozen-result).
- **Neural learning and memory:** the repository includes specialist perception, continual prediction, memory interventions and outcome-trained search experiments, with their controls and saved artifacts. [Neural perception](research/neural-digits/), [adaptive memory](research/adaptive-memory/), [verified memory](research/verified-memory/), [search policy](research/neural-routing-loop/).

Browse the [research directory](research/) for complete study records and the [capability map](docs/capability-map.md) for implementation and activation status.

## System and observatory

The neural runtime records incoming observations, advances the circuit, retrieves relevant experience, selects local actions and records their outcomes. Synapses, learned values, memory and the journal preserve their relationship across checkpoints.

The [observatory](https://nuria.network/) provides views of recorded neural activity, work, results and evidence. The [Invention Studio](agency/README.md) implements software research missions that produce checked artifacts and reusable tools.

Source-specific integrations retain provenance. Protocol ingestion and financial execution boundaries are documented in [architecture](docs/architecture.md) and [spending](docs/spending.md); financial execution is disabled until its documented activation requirements are met.

## Run offline

Use Python 3.14 with the pinned dependencies. This starts a separate local simulation.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p run/data run/public/engine
NURIA_DATA="$PWD/run/data" NURIA_PUBLIC="$PWD/run/public/engine" .venv/bin/python engine.py
```

See [architecture and recovery](docs/architecture.md), [evaluation](docs/evaluation.md), [capacity](docs/capacity.md) and the [research runtime](agency/README.md) for component-specific setup and checks. Production histories, credentials and deployment settings are separate from the offline example.

## Development

```sh
python -m pip install -r requirements.txt ruff==0.16.10
npm ci
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
npm run format:check
npm run check:frontend
npm run build
```

The test suite covers learning, journal integrity, recovery, source handling, execution boundaries and frontend behavior. Experimental results retain their original protocols, failures, controls and artifact identities in the linked research records.

Contact: [hello@nuria.network](mailto:hello@nuria.network).
