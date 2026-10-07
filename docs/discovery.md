# Discovery lab

Nuria now runs a second kind of experiment: choosing actions when relevant information is missing, rewards arrive later, and previously useful behavior can stop working. The main 1,024-neuron history continues unchanged. The lab has its own 256-neuron Brian2 sensory circuit, genesis, journal, random state and checkpoint.

The unknown is **unknown to the learner inside a specified software world**. This is a behavioral experiment, not an assertion that a new form of consciousness has been discovered.

## The loop

Eight possible cues identify different contexts. Four choices have unknown success probabilities. A cue is presented, a distractor follows, and the controller must decide from the representation it retained. Some cues are hidden. The controller can pay virtual credits to observe the missing cue, provided its estimated value of that information exceeds the probe price. Only the chosen action's reward returns, two to five same-task decisions later. No reward is available before its due step.

The controller learns context/choice success and failure counts. It scores choices using estimated success, a bounded uncertainty bonus and the observed cost. Eight percent of decisions explore using seeded random choices. A surprise accumulator and discounted counts let old beliefs lose influence. It never receives the world's mapping, phase, unused outcomes or future reward labels as policy inputs. Audit records retain those fields separately so reviewers can check what happened.

The sensory circuit receives cue stimulation, recurrent excitation, inhibition and background noise. It receives **no action utility or reward-probability drive**. A learned cue decoder reads actual pooled spike counts and membrane values. Remembered spike representations carry information across the distractor. The decoder is taught visible sensory cue labels; the useful action for each cue must still be learned from delayed chosen-action rewards.

This establishes an explicit spike-to-representation-to-choice path. It does not make that path superior to remembering the cue directly, and it does not test the old circuit's STDP or spontaneous recurrent memory.

## Ten working capabilities

| Capability                    | What actually runs                                                                       |
| ----------------------------- | ---------------------------------------------------------------------------------------- |
| Partial observation           | Visible and occluded cues, followed by distractors                                       |
| Learned outcomes              | Contextual choice success/failure estimates                                              |
| Uncertainty-aware exploration | A variance bonus plus seeded exploration                                                 |
| Active sensing                | Optional cue probes chosen from estimated one-step information value                     |
| Cost accounting               | Arm and probe costs debit a bounded virtual balance                                      |
| Delayed credit                | Original beliefs and predictions are retained until the selected outcome is due          |
| Cue memory                    | Stored spike-derived representations; an independent no-memory branch                    |
| Continual adaptation          | Discounting and a bounded prediction-surprise detector                                   |
| Adaptive curriculum           | Tasks selected from observed net-reward deficits and a coverage bonus, without an oracle |
| Causal controls               | Matched worlds and exploration draws, isolated learners, public negative results         |

All decision actions and numerical scores are public. Explanations come from these recorded computations; no language model invents a thought process.

## Four environments

- **Association:** learn four-choice outcomes for eight visible cues.
- **Occlusion:** cues can be missing; extra information costs credits.
- **Reversal:** cue/choice associations change without notifying the controller.
- **Scarcity:** associations change and information prices vary.

Success probabilities are 0.88 for a world's currently useful choice and 0.12 for other choices. The mapping is seeded independently for each context and regime. Reversals occur every 320 same-task opportunities. Those rules describe the environment to reviewers; controllers do not receive the current mapping or phase.

Live curriculum selection uses actual settled net rewards, not hidden optimal rewards. The main circuit's fresh public firing rate adds at most 0.1 virtual credits to the current probe price. Its tick, source cursor and receipt head accompany each coupling. This is a bounded software link, not live trading or a link between the separate neural histories. Missing/stale upstream evidence produces no perturbation and is recorded as absent.

## Controls and information boundaries

Each task runs seven branches on the same world sequence and shared exploration draws:

- **Spike memory:** the full learned controller using retained neural representations.
- **Symbolic memory:** the same learned controller remembers the visible cue directly. This is a strong simpler alternative, not a deliberately weak baseline.
- **No cue memory:** the controller loses the original cue before decision, but may buy a fresh observation.
- **No probes:** retained memory remains; additional information cannot be purchased.
- **No adaptation:** outcome counts learn but neither decay nor react to the change detector.
- **Frozen learning:** outcome-model updates stop after 240 settled outcomes; sensory decoding still runs.
- **Random:** choices are random, with the same action costs and feedback delays.

Branches have separate outcome models, budgets and cue decoders. One branch's paid probe never teaches another branch's decoder. Actual cue/probe windows can be shared as matched sensory observations; information becomes available to a learner only if its own observation/probe permits it.

Net reward includes paid costs. Oracle regret is an audit measure against an ideal observer that knows the context and current mapping for free; it does not enter choice or curriculum code. Brier error scores available predictions, with its own denominator. Every scored choice is evaluated by its originating decision index, so delayed warmup rewards cannot leak into the validation endpoint.

## Validation

Development failures remain in [discovery-development.json](discovery-development.json). The original decoder spread confidence too broadly and paid for unnecessary information. After correcting its sensitivity and matching exploration draws, a frozen protocol was written and hashed before running fresh world seeds 201–212.

Each of four tasks has 1,200 decision opportunities per seed. The first 240 decisions are warmup. All 960 later decisions are scored after their delayed outcomes settle. The evaluation uses 16 actual Brian2 feature windows for each of eight cues plus a blank cue, selected for replay by seed. It does not simulate a fresh circuit window for every evaluated choice. The live worker does.

Primary endpoint: paired mean net virtual reward. Results, per-seed trials, source hashes and the feature evidence hash are in [discovery-evaluation.json](discovery-evaluation.json). The superiority gate requires a positive lower conservative paired interval against symbolic memory on **every** task. A failed gate remains a failed gate. Paired intervals use a conservative 2.6 standard-error multiplier for 12 seeds; they do not establish a population-wide guarantee or independent replication.

Mean net virtual reward across 12 fresh world seeds; higher is better:

| Task        | Spike memory | Symbolic memory | No memory | No probes | No adaptation | Frozen learning | Random |
| ----------- | ------------ | --------------- | --------- | --------- | ------------- | --------------- | ------ |
| Association | 0.8070       | 0.8070          | 0.3805    | 0.8070    | 0.8070        | 0.8070          | 0.2853 |
| Occlusion   | 0.7473       | 0.7481          | 0.4426    | 0.6568    | 0.7481        | 0.7431          | 0.2847 |
| Reversal    | 0.6260       | 0.6245          | 0.3641    | 0.5269    | 0.3609        | 0.2597          | 0.2885 |
| Scarcity    | 0.5863       | 0.5866          | 0.3694    | 0.5270    | 0.3871        | 0.2848          | 0.2861 |

Cue-memory removal lowers reward on all four tasks (12/12 paired wins each). Costed probes help on occlusion, reversal and scarcity; their conservative paired lower bounds are positive. Adaptation and continued outcome learning help on reversal and scarcity, with 12/12 paired wins each. They do not improve the stable association task.

**Neural superiority gate: failed.** Spike memory equals the symbolic alternative on association and differs by less than 0.0015 mean reward on the other tasks. Its conservative paired lower bounds against symbolic memory are not positive. The useful effects are from cue retention, active sensing and adapting outcome models; this study does not establish a benefit from neural dynamics or plastic synapses.

The validation scores 46,080 post-warmup world opportunities across seven branches (322,560 scored choices). Its protocol hash is `b47e96911dffa78b8d10be71fd98ed764179acdce38eb7124e59776fbe2f2fb2`. [Recorded spike evidence](discovery-features.json) contains the counts, relative spike times, pooled membrane values and features used in replay. Independent circuit seeds and replication remain pending.

## Evidence and continuation

`GET /api/discovery` returns bounded cached evidence. More than 15 seconds without a fresh cache returns unavailable. The worker commits the decision journal, all learners, pending delayed rewards, decoder state, curriculum visits, complete Brian2 network and random state together. Missing or inconsistent checkpoint evidence refuses continuation. One writer lock prevents duplicate workers.

The worker runs on the dedicated Nuria server with a separate unprivileged user, no network address families beyond Unix sockets, a 50% CPU ceiling, 512 MB memory limit and private state. It reads the main circuit's public cache; it cannot write either established organism's state or access their private databases. Its consistent SQLite snapshot is included in the existing encrypted backup path.

## What remains open

The live curriculum can expose previously unencountered combinations; it remains four designed task families with a published controller. New senses, social agents, multi-step learned plans, harder memory delays and independent replication are next experiments, not implemented claims. A separate neural-circuit/plasticity ablation is still required to attribute gains to recurrent dynamics or STDP.

Credits are virtual. The creator-fee wallet, mint, funded spending wallet and isolated independently limited signer are not configured. Eventually an information probe could buy a specified external computation with a verifiable outcome and a published budget. That requires connecting the prepared fee rails, not renaming these virtual credits as SOL.

## Reproduce

```sh
python -m scripts.evaluate_discovery .test-state/fresh-discovery-validation
python -m unittest discover -s tests -p test_discovery.py -v
```

Use a fresh destination; prior evidence is retained. The code calls no RPC and sends no transactions. Start `cognition.lab_worker` only with dedicated lab data/public paths, never either existing circuit's paths.

Change detection and costed exploration are informed by the [piecewise-stationary bandit literature](https://www.jmlr.org/beta/papers/v23/20-1384.html) and [Bayesian curiosity research](https://arxiv.org/abs/1911.08701). The implemented discounted-count/surprise heuristic is simpler and claims none of those algorithms' theoretical guarantees. Brian2's [store/restore interface](https://brian2.readthedocs.io/en/stable/reference/brian2.core.magic.restore.html) preserves the circuit's random state for continuation.
