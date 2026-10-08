# Capability map

Implementation status and evidence for all 51 capabilities. The categories describe the system; they are not levels of consciousness.

Active mechanisms are implemented. Evaluation identifies bounded experiments needing broader validation. Prepared mechanisms require live configuration. Research identifies an objective and its next gate.

## Neural dynamics

| Capability                          | Status | Evidence or next gate                                                        |
| ----------------------------------- | ------ | ---------------------------------------------------------------------------- |
| Persistent membrane dynamics        | Active | Neural variables and random state resume from a trusted checkpoint.          |
| Recurrent excitation and inhibition | Active | Six-population 1024-neuron circuit with complete connectivity in the worker. |
| Multi-timescale dynamics            | Active | Sensory, memory and association use different time constants.                |
| Spike-timing plasticity             | Active | Timing traces change bounded excitatory weights.                             |
| Eligibility-based feedback          | Active | Observed rewards modulate recently eligible synapses.                        |
| Homeostatic control                 | Active | Bounded bias feedback regulates population firing.                           |

## Inputs and prediction

| Capability                 | Status   | Evidence or next gate                                                                                                                            |
| -------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Finalized trade reader     | Prepared | Finalized protocol events are decoded, deduplicated and linked to individual neural effects. Coverage and unresolved retrievals remain explicit. |
| Individual event encoding  | Active   | Each input has its own sensory window and event-ID texture.                                                                                      |
| Idempotent source history  | Active   | Changed or reordered duplicate inputs refuse continuation.                                                                                       |
| Causal perturbation probes | Active   | Matched current-state input and no-input replay with RNG restoration.                                                                            |
| Neural feature extraction  | Active   | 64 spike-derived features feed the predictor and memory.                                                                                         |
| Prequential prediction     | Active   | Stored forecast is scored before its next observed outcome trains the readout.                                                                   |
| Source-isolated learning   | Active   | Source-specific forecast council learns transition memory and specialist weights; old neural readouts are retained separately.                   |
| Calibrated measurement     | Active   | Prequential Brier error and learned repeat baseline; expert losses and forecast attention are public.                                            |

## Memory, attention and actions

| Capability                        | Status | Evidence or next gate                                                                                                        |
| --------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------- |
| Persistent episodic memory        | Active | Inputs, feature vectors and importance survive restart.                                                                      |
| Similarity-based recall           | Active | Bounded context-specific candidates are scored by similarity and importance.                                                 |
| Replay actions                    | Active | Selected past inputs re-stimulate the neural circuit.                                                                        |
| Novelty signals                   | Active | Context visits reduce novelty rather than novelty being a narrative label.                                                   |
| Workspace competition             | Active | Three selected salience slots affect neural drive.                                                                           |
| Uncertainty-driven arbitration    | Active | Prediction uncertainty competes with surprise and resource pressure.                                                         |
| Published hybrid action selection | Active | 65% utility and 35% neural score, with a deterministic tie rule.                                                             |
| Learned action values             | Active | Observed rewards update future utility estimates.                                                                            |
| Measured action costs             | Active | Runtime updates cost estimates used in action selection.                                                                     |
| Resource regulation               | Active | Explicit model energy and fatigue affect activity and rest.                                                                  |
| Software embodiment               | Active | Actions change habitat position, visitation and virtual resource collection.                                                 |
| Autonomous local experiments      | Active | Retrospective chronological diagnostics compare forecast components on recorded same-source inputs; no hypothesis invention. |

## Measurement and continuity

| Capability                 | Status     | Evidence or next gate                                                                                                     |
| -------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------- |
| Actual-neuron evaluation   | Evaluation | Actual spike tasks do not beat the learned repeat predictor; a matched training-synapse ablation is published separately. |
| Public decision evidence   | Active     | Numerical selection, outcome and model projection hashes are published.                                                   |
| Atomic recovery            | Active     | Journal, learning, memory and neural checkpoint commit together.                                                          |
| Bounded read-only web path | Active     | Visitors read cached evidence and cannot trigger RPC, jobs or signing.                                                    |

## Fees, custody and purchases

| Capability                    | Status   | Evidence or next gate                                                                                                                                       |
| ----------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Treasury observation          | Prepared | Read-only observation separates balances, protocol accrual and verified payment receipts.                                                                   |
| Creator-fee attribution       | Prepared | Live Pump creator/mode checks and unsigned standard claims; asset-specific attribution, unsupported modes and automatic collection remain activation gates. |
| Durable spending reservations | Prepared | Durable SOL and USDC limits, reserve floor, unique decisions, ambiguous payment reconciliation and restart protection are tested; spending disabled.        |
| Unsigned single-transfer rail | Prepared | Only a fixed system-transfer message is prepared; no signing or broadcast.                                                                                  |
| Isolated autonomous signer    | Prepared | Isolated exact Solana USDC x402 signer adapter and guarded service installed; no production key or funded provider configured.                              |
| Paid job outcome verification | Prepared | Payment, delivery hashes and delayed paid-forecast outcomes are implemented and tested offline; a real provider and funded production test remain required. |

## Research and validation

| Capability                         | Status     | Evidence or next gate                                                                                                          |
| ---------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Independent public attestation     | Research   | Publish externally witnessed heads and optional chain anchors with a defined cost budget.                                      |
| Learned transition models          | Research   | Predict habitat transitions from experience and test against a held-out map.                                                   |
| Multi-step planning                | Research   | Compare learned planning against the current explicit local path planner.                                                      |
| Attention efficacy tests           | Evaluation | Learned forecast attention is tested across 20 seeds; the separate hand-designed workspace still needs a causal efficacy test. |
| Memory consolidation               | Research   | Distill older episodes into slower representation without destroying their evidence.                                           |
| Continual-learning retention       | Research   | Measure forgetting across task changes before enabling new objectives.                                                         |
| Adaptive goal weighting            | Research   | Learn arbitration weights under stable published safety and resource limits.                                                   |
| Uncertainty-aware interventions    | Research   | Select experiments by expected information gain and verify actual gains.                                                       |
| Causal model learning              | Research   | Learn intervention effects beyond the current matched perturbation report.                                                     |
| Counterfactual action evaluation   | Research   | Compare prospective actions without treating a chosen replay as original-history proof.                                        |
| Multi-modal perception             | Research   | Add authorized structured signals with provenance, rate limits and a useful behavioral objective.                              |
| Social interaction tasks           | Research   | Evaluate coordination with other independent entities in a bounded environment.                                                |
| Long-horizon resource planning     | Research   | Measure budget prediction and reserve preservation over changing cost regimes.                                                 |
| Independent capability replication | Research   | Reproduce outcomes from public code and fixed benchmarks on separate hardware.                                                 |
| Open-ended cumulative development  | Research   | Expand only with measured capability, retained continuity, explicit failure boundaries and independent evaluation.             |
