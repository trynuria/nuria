# Evaluation

Nuria has working mechanisms. That does not establish why the complete neural architecture is useful. This report keeps unsuccessful comparisons alongside useful component results.

## Correction to the first neural tasks

The original spike-feature readout achieved 92.5% accuracy on an alternating stream and 88.75% on a persistent stream. Those streams usually flip or repeat the previous side. A one-parameter predictor fitted to the same training transitions matches both accuracies and has substantially lower Brier error:

| Task        | Neural Brier ↓ | Learned repeat Brier ↓ | Accuracy, both |
| ----------- | -------------- | ---------------------- | -------------- |
| Alternation | 0.1601         | 0.0703                 | 92.5%          |
| Persistence | 0.1729         | 0.0999                 | 88.75%         |

The baseline estimates `(training repeats + 1) / (training transitions + 2)` and uses the last observed side. Neither future labels nor a known generating probability are provided to it. `scripts/evaluate_forecasts.py` reproduces this comparison from the exact original generator and 160/80 split. Removing every neural feature was a weak comparator. These tasks establish signal transmission and readout learning, not neural superiority.

## The forecast component

The deployed forecast council learns which of seven specialists to trust: frequency, repeat probability, amount context, fee-rate context, joint context, contextual logistic regression and the neural readout. Each source owns separate parameters, pending predictions and metrics. Stored forecasts are scored before the new label trains any specialist. Conditional counts forget with a fixed factor of 0.999 per observed training step. Attention updates discounted log weights with factor 0.995 and squared-error learning rate 2, plus a 1% fixed share. Public encodings and logistic parameters are in `cognition/forecast.py`.

This is an adaptive statistical component alongside the spiking circuit. Its memory is conditional outcome statistics; its attention selects forecasters. Those mechanisms are distinct from neural episodic replay, workspace broadcast and synaptic STDP.

### Development and validation

The first comparison used seeds 101–110. Its council beat repeat/frequency and joint tables, but lost to contextual logistic regression on the switch task: 0.1423 versus 0.1302 Brier. That development result remains in [forecast-development.json](forecast-development.json).

The council then added that existing logistic model as a specialist. Validation uses new seeds 201–220. The protocol was written and hashed before that run; this is a local protocol record, not an independently preregistered study. Each seed generates 2,001 inputs, yielding 2,000 next-side opportunities. Every model receives the same first 500 opportunities as unscored warmup and the same 1,500 scored outcomes. Scored data may train models only after their forecast is evaluated. The frozen ablation receives only warmup learning.

Amount and fee context tasks assign repeat/flip probabilities to 16 recurring context bins. The switch task changes which context controls the transition halfway through the stream. The unpredictable control has independent 50/50 outcomes. All models see side, amount and fee; they never see task names, context rules or future labels. The logistic comparator receives one-hot encodings of the same contexts, avoiding a deliberately weak linear raw-feature baseline.

### Results on fresh seeds

Mean Brier error across 20 paired seeds; lower is better:

| Task           | Council    | Repeat     | Joint table | Context logistic | Best relevant table |
| -------------- | ---------- | ---------- | ----------- | ---------------- | ------------------- |
| Amount context | 0.0905     | 0.2502     | 0.1349      | 0.0942           | **0.0903**          |
| Fee context    | 0.0901     | 0.2503     | 0.1343      | 0.0937           | **0.0898**          |
| Context switch | **0.1285** | 0.2503     | 0.1886      | 0.1297           | 0.1828              |
| Unpredictable  | 0.2502     | **0.2502** | 0.2677      | 0.2733           | 0.2537              |

The council beats logistic regression on all 20 seeds for each structured task. Its switch-task gain over logistic is small, approximately 0.0012 Brier. The table given the relevant context is slightly better on the static tasks. The council does not reliably beat repeat/frequency on unpredictable data. Rounded ties do not imply identical predictions. Full precision, per-seed wins and paired standard errors are retained in [forecast-evaluation.json](forecast-evaluation.json).

### Mechanism ablations

On the switch task:

| Branch                        | Mean Brier ↓ | Paired losses to council |
| ----------------------------- | ------------ | ------------------------ |
| Council                       | **0.1285**   | —                        |
| No conditional outcome memory | 0.2502       | 20/20                    |
| Uniform forecast attention    | 0.1878       | 20/20                    |
| Learning frozen after warmup  | 0.2911       | 20/20                    |

The first ablation retains working frequency and repeat models; it removes conditional tables and contextual logistic learning. The second keeps all specialists learning but gives them equal selection weights. The third freezes conditional counts, their aging clock, logistic weights and attention after warmup. These interventions establish a contribution on this component task, not universal usefulness.

The neural specialist receives **0.5 throughout this fast component evaluation**. No random vectors are presented as spike features. The recorded `no_neural_expert` branch removes that neutral probability proxy; it is **not a neural-circuit ablation** and cannot demonstrate a benefit from neurons. Real spike features are supplied by the separate production readout.

## Recorded-input diagnostics

The experiment action no longer earns reward for a predefined random-feature fixture. It compares fresh forecast learners against repeat and contextual logistic models on up to 512 recent same-source recorded inputs. The first third is warmup; later inputs are scored chronologically before updating. It reports the actual first/last input IDs, counts, losses, expert attention and result hash. Insufficient observations are unavailable. Negative improvement is retained and feeds the measured job reward.

A retrospective diagnostic is not independent future validation, a randomized intervention or hypothesis discovery. Historical payloads cannot reconstruct the old neural predictor, so every diagnostic branch excludes its specialist.

## Actual synaptic ablation

A separate Brian2 run compares normal training STDP with training STDP disabled from the start. Both branches use the same fixed circuit seed, matched event stream, 160 training opportunities and 80 held-out outcomes. Readout learning and synaptic updates are frozen during held-out evaluation. Three data seeds are published; they are not three independent circuit seeds.

| Task        | Training STDP enabled, mean Brier ↓ | Training STDP disabled | Enabled branch wins |
| ----------- | ----------------------------------- | ---------------------- | ------------------- |
| Alternation | 0.163974                            | 0.164600               | 2/3                 |
| Persistence | 0.237479                            | 0.237416               | 2/3                 |

The effects are small and mixed. Persistence is slightly worse on average with STDP, and one persistence seed produces error above the 0.25 chance predictor in both branches. These runs establish no robust neural-plasticity advantage. They also do not test the usefulness of reward modulation, episodic neural replay or workspace broadcasting. [All matched trials](synaptic-evaluation.json) remain available, including the poor result.

## Remaining gates

The separate [acquisition evaluation](acquisition.md) measures whether information is worth its virtual cost against five matched alternatives. Its successful reversal comparison and failed stable-task comparisons are both retained. It uses no neural features and cannot establish whole-organism or paid-resource benefit.

- A task where the complete organism beats sensible alternatives, with causal gains from synaptic plasticity, episodic neural memory and workspace broadcast separately established.
- Learned planning rather than the current explicit habitat path planner and 65% utility / 35% neural action arbitration.
- Live finalized trades from a verified protocol source, verified creator-fee attribution and a funded, independently limited signer. Live economic execution remains disabled.
- Independent receipt witnesses or chain anchors. Current hashes establish internal consistency, not independently attested truth.
- A full-day provider-to-browser load test and independently replicated capability results. Actual encrypted-archive checkpoint recovery now passes; production failover and recovery time remain untested. See [capacity and recovery](capacity.md).

## Reproduction

```sh
python -m scripts.evaluate_forecasts .test-state/new-forecast-run
python -m scripts.benchmark_cognition .test-state/new-neural-run --neural-only --paired-synaptic-ablation
```

Use a new directory; existing evidence is retained. The first command evaluates the production statistical component. The second uses actual Brian2 spikes, a fixed circuit seed and three data seeds, comparing normal training STDP against disabled training STDP with both held-out branches frozen. Neither command calls RPC, sends transactions or touches the running history.

Expert weighting is informed by [prediction with expert advice for the Brier game](https://www.jmlr.org/papers/v10/vovk09a.html). Nuria's discounted fixed-share heuristic is not an implementation of that paper's theorem and claims no corresponding guarantee. Neural plasticity uses Brian2's [explicit pre/post synaptic mechanisms](https://brian2.readthedocs.io/en/stable/examples/synapses.STDP.html).

## Hidden-world decision experiment

The separate Discovery lab now tests costed sensing, remembered neural representations, delayed feedback and continual adaptation against seven paired branches.

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

Read [the full specification](discovery.md) and [all decision validation trials](discovery-evaluation.json). This experiment has its own circuit and history and does not establish efficacy of the main circuit’s STDP, workspace or episodic neural replay.
