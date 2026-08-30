# CLAIMS

Generated from run records by `controlplane.harness.experiment.write_claims`.
Do not hand-edit: every number below is read back out of `runs/*.json`, and a
claim with no runs behind it is printed as UNSUPPORTED rather than dropped.

- runs: **84**
- scenarios: GS-1, GS-1L, GS-2, GS-3, GS-4, GS-4P, GS-7
- conditions: off, on, on+detect_only, on+deterministic_only
- backends: primary
- total spend: $0.0087

## Claims

### C1 — SUPPORTED

Faults in tool-using agent runs are detected, and detection is attributed to a named invariant rather than to a general-purpose model opinion.

```json
22
```

### C2 — SUPPORTED

For monotone invariants, the last good step is recovered exactly, in O(log N) deterministic evaluations and zero model calls.

```json
100.0
```

### C3 — SUPPORTED

Exact localization beats what you would otherwise do: alarm-step blame, last-tool-call blame, and an LLM reading the whole trace.

```json
{
  "detected_at": {
    "n": 54,
    "exact_step_pct": 22.22,
    "exact_step_ci": [
      0.1111,
      0.3333
    ],
    "mean_abs_error": 5.704,
    "within_1_pct": 61.11,
    "mean_calls": 0.0
  },
  "last_tool_call": {
    "n": 54,
    "exact_step_pct": 22.22,
    "exact_step_ci": [
      0.1111,
      0.3333
    ],
    "mean_abs_error": 5.704,
    "within_1_pct": 61.11,
    "mean_calls": 0.0
  },
  "random": {
    "n": 54,
    "exact_step_pct": 16.67,
    "exact_step_ci": [
      0.0741,
      0.2778
    ],
    "mean_abs_error": 4.944,
    "within_1_pct": 33.33,
    "mean_calls": 0.0
  },
  "llm_whole_trace": {
    "n": 54,
    "exact_step_pct": 57.41,
    "exact_step_ci": [
      0.4444,
      0.7037
    ],
    "mean_abs_error": 5.352,
    "within_1_pct": 61.11,
    "mean_calls": 1.0
  }
}
```

### C4 — SUPPORTED

Rolling back to the reported step produces a correct outcome, rather than merely a correct diagnosis.

```json
68.18
```

### C5 — SUPPORTED

The supervisor improves task success on a paired off/on design, and the improvement is tested rather than asserted.

```json
0.03125
```

### C6 — SUPPORTED

On clean runs the system mostly leaves the agent alone; the false-alarm rate is reported whether or not it flatters the design.

```json
{
  "n": 3,
  "false_alarms": 0,
  "false_alarms_per_100_steps": 0.0,
  "interventions": 0,
  "task_success_pct": 100.0
}
```

### C7 — SUPPORTED

Inline checking fits the tier's latency budget.

```json
0.2694
```

### C8 — SUPPORTED

The audit trail is tamper-evident and replays identically.

```json
{
  "chain_intact_all": true,
  "replay_identical_all": true
}
```

## Condition summary

```json
{
  "off": {
    "n": 21,
    "task_success_pct": 71.43,
    "task_success_ci": [
      0.5238,
      0.9048
    ],
    "detections": 0,
    "attributable_detections": 0,
    "spontaneous_detections": 0,
    "localization": {
      "n": 0,
      "exact_step_pct": 0.0,
      "exact_step_ci": [
        0.0,
        0.0
      ],
      "within_1_pct": 0.0,
      "within_1_ci": [
        0.0,
        0.0
      ],
      "mean_abs_error": null,
      "mean_evaluations": null,
      "mean_wall_ms": null,
      "max_wall_ms": null,
      "quality_mix": {}
    },
    "delta_detect": {
      "median": null,
      "p90": null,
      "max": null,
      "ci": [
        0.0,
        0.0
      ]
    },
    "recoverability_at_L_pct": null,
    "escalations": 0,
    "intervention_regret_pct": 0.0,
    "false_alarms_per_100_steps": 0.0,
    "inline_ms_p95": 0.0,
    "cost": {
      "mean_tokens": 104973.1,
      "mean_usd": 0.0,
      "total_usd": 0.0
    },
    "integrity": {
      "chain_intact_all": true,
      "replay_identical_all": true
    },
    "clean_runs": {
      "n": 3,
      "false_alarms": 0,
      "false_alarms_per_100_steps": 0.0,
      "interventions": 0,
      "task_success_pct": 100.0
    }
  },
  "on": {
    "n": 21,
    "task_success_pct": 100.0,
    "task_success_ci": [
      1.0,
      1.0
    ],
    "detections": 22,
    "attributable_detections": 18,
    "spontaneous_detections": 4,
    "localization": {
      "n": 18,
      "exact_step_pct": 100.0,
      "exact_step_ci": [
        1.0,
        1.0
      ],
      "within_1_pct": 100.0,
      "within_1_ci": [
        1.0,
        1.0
      ],
      "mean_abs_error": 0.0,
      "mean_evaluations": 4.5,
      "mean_wall_ms": 0.0289,
      "max_wall_ms": 0.0665,
      "quality_mix": {
        "exact": 18
      }
    },
    "delta_detect": {
      "median": 1.5,
      "p90": 42.0,
      "max": 43.0,
      "ci": [
        1.1111,
        15.0556
      ]
    },
    "recoverability_at_L_pct": 68.18,
    "escalations": 3,
    "intervention_regret_pct": 0.0,
    "false_alarms_per_100_steps": 0.0,
    "inline_ms_p95": 0.2694,
    "cost": {
      "mean_tokens": 173567.5,
      "mean_usd": 0.0,
      "total_usd": 0.0
    },
    "integrity": {
      "chain_intact_all": true,
      "replay_identical_all": true
    },
    "clean_runs": {
      "n": 3,
      "false_alarms": 0,
      "false_alarms_per_100_steps": 0.0,
      "interventions": 0,
      "task_success_pct": 100.0
    }
  },
  "on+detect_only": {
    "n": 21,
    "task_success_pct": 28.57,
    "task_success_ci": [
      0.0952,
      0.4762
    ],
    "detections": 18,
    "attributable_detections": 18,
    "spontaneous_detections": 0,
    "localization": {
      "n": 18,
      "exact_step_pct": 100.0,
      "exact_step_ci": [
        1.0,
        1.0
      ],
      "within_1_pct": 100.0,
      "within_1_ci": [
        1.0,
        1.0
      ],
      "mean_abs_error": 0.0,
      "mean_evaluations": 4.5,
      "mean_wall_ms": 0.0276,
      "max_wall_ms": 0.0669,
      "quality_mix": {
        "exact": 18
      }
    },
    "delta_detect": {
      "median": 1.5,
      "p90": 42.0,
      "max": 43.0,
      "ci": [
        1.1111,
        15.0556
      ]
    },
    "recoverability_at_L_pct": 83.33,
    "escalations": 3,
    "intervention_regret_pct": 0.0,
    "false_alarms_per_100_steps": 0.0,
    "inline_ms_p95": 0.2055,
    "cost": {
      "mean_tokens": 56746.0,
      "mean_usd": 0.0,
      "total_usd": 0.0
    },
    "integrity": {
      "chain_intact_all": true,
      "replay_identical_all": true
    },
    "clean_runs": {
      "n": 3,
      "false_alarms": 0,
      "false_alarms_per_100_steps": 0.0,
      "interventions": 0,
      "task_success_pct": 100.0
    }
  },
  "on+deterministic_only": {
    "n": 21,
    "task_success_pct": 100.0,
    "task_success_ci": [
      1.0,
      1.0
    ],
    "detections": 22,
    "attributable_detections": 18,
    "spontaneous_detections": 4,
    "localization": {
      "n": 18,
      "exact_step_pct": 100.0,
      "exact_step_ci": [
        1.0,
        1.0
      ],
      "within_1_pct": 100.0,
      "within_1_ci": [
        1.0,
        1.0
      ],
      "mean_abs_error": 0.0,
      "mean_evaluations": 4.5,
      "mean_wall_ms": 0.0284,
      "max_wall_ms": 0.0778,
      "quality_mix": {
        "exact": 18
      }
    },
    "delta_detect": {
      "median": 1.5,
      "p90": 42.0,
      "max": 43.0,
      "ci": [
        1.1111,
        15.0556
      ]
    },
    "recoverability_at_L_pct": 68.18,
    "escalations": 3,
    "intervention_regret_pct": 0.0,
    "false_alarms_per_100_steps": 0.0,
    "inline_ms_p95": 0.263,
    "cost": {
      "mean_tokens": 173567.5,
      "mean_usd": 0.0,
      "total_usd": 0.0
    },
    "integrity": {
      "chain_intact_all": true,
      "replay_identical_all": true
    },
    "clean_runs": {
      "n": 3,
      "false_alarms": 0,
      "false_alarms_per_100_steps": 0.0,
      "interventions": 0,
      "task_success_pct": 100.0
    }
  }
}
```

## Localization vs baselines

Same incidents, same scoring code, replayed from saved ledgers.

```json
{
  "incidents": 54,
  "ours": {
    "n": 54,
    "exact_step_pct": 100.0,
    "exact_step_ci": [
      1.0,
      1.0
    ],
    "mean_abs_error": 0.0,
    "within_1_pct": 100.0,
    "mean_calls": 4.5
  },
  "baselines": {
    "detected_at": {
      "n": 54,
      "exact_step_pct": 22.22,
      "exact_step_ci": [
        0.1111,
        0.3333
      ],
      "mean_abs_error": 5.704,
      "within_1_pct": 61.11,
      "mean_calls": 0.0
    },
    "last_tool_call": {
      "n": 54,
      "exact_step_pct": 22.22,
      "exact_step_ci": [
        0.1111,
        0.3333
      ],
      "mean_abs_error": 5.704,
      "within_1_pct": 61.11,
      "mean_calls": 0.0
    },
    "random": {
      "n": 54,
      "exact_step_pct": 16.67,
      "exact_step_ci": [
        0.0741,
        0.2778
      ],
      "mean_abs_error": 4.944,
      "within_1_pct": 33.33,
      "mean_calls": 0.0
    },
    "llm_whole_trace": {
      "n": 54,
      "exact_step_pct": 57.41,
      "exact_step_ci": [
        0.4444,
        0.7037
      ],
      "mean_abs_error": 5.352,
      "within_1_pct": 61.11,
      "mean_calls": 1.0
    }
  }
}
```

## Run ids

- `GS-1-off-primary-s7-239c12`
- `GS-1-on-primary-s7-ab00aa`
- `GS-1-on+detect_only-primary-s7-033d71`
- `GS-1-on+deterministic_only-primary-s7-ff2bc4`
- `GS-1-off-primary-s11-3920f6`
- `GS-1-on-primary-s11-54c05b`
- `GS-1-on+detect_only-primary-s11-957d72`
- `GS-1-on+deterministic_only-primary-s11-71e2c0`
- `GS-1-off-primary-s23-9642cb`
- `GS-1-on-primary-s23-87e59d`
- `GS-1-on+detect_only-primary-s23-4fc37c`
- `GS-1-on+deterministic_only-primary-s23-e33e9f`
- `GS-1L-off-primary-s7-67fd32`
- `GS-1L-on-primary-s7-c99087`
- `GS-1L-on+detect_only-primary-s7-b6202d`
- `GS-1L-on+deterministic_only-primary-s7-b32793`
- `GS-1L-off-primary-s11-eca8d2`
- `GS-1L-on-primary-s11-e9279d`
- `GS-1L-on+detect_only-primary-s11-c41095`
- `GS-1L-on+deterministic_only-primary-s11-9d275e`
- `GS-1L-off-primary-s23-8d4cbb`
- `GS-1L-on-primary-s23-22791c`
- `GS-1L-on+detect_only-primary-s23-891ade`
- `GS-1L-on+deterministic_only-primary-s23-13ec1e`
- `GS-2-off-primary-s7-0ea58c`
- `GS-2-on-primary-s7-9b1e01`
- `GS-2-on+detect_only-primary-s7-cd5c89`
- `GS-2-on+deterministic_only-primary-s7-26612e`
- `GS-2-off-primary-s11-09626e`
- `GS-2-on-primary-s11-f38f4c`
- `GS-2-on+detect_only-primary-s11-1c9f21`
- `GS-2-on+deterministic_only-primary-s11-142e01`
- `GS-2-off-primary-s23-2fb205`
- `GS-2-on-primary-s23-a49c20`
- `GS-2-on+detect_only-primary-s23-754327`
- `GS-2-on+deterministic_only-primary-s23-4d6a59`
- `GS-3-off-primary-s7-5518be`
- `GS-3-on-primary-s7-7eccd8`
- `GS-3-on+detect_only-primary-s7-3393f3`
- `GS-3-on+deterministic_only-primary-s7-68496b`
- `GS-3-off-primary-s11-d4735d`
- `GS-3-on-primary-s11-2fdf14`
- `GS-3-on+detect_only-primary-s11-bc32d0`
- `GS-3-on+deterministic_only-primary-s11-0e9934`
- `GS-3-off-primary-s23-9f8256`
- `GS-3-on-primary-s23-ff6e22`
- `GS-3-on+detect_only-primary-s23-14da2a`
- `GS-3-on+deterministic_only-primary-s23-9e247c`
- `GS-4-off-primary-s7-dcd782`
- `GS-4-on-primary-s7-c31980`
- `GS-4-on+detect_only-primary-s7-1104eb`
- `GS-4-on+deterministic_only-primary-s7-23f39e`
- `GS-4-off-primary-s11-44ae9f`
- `GS-4-on-primary-s11-9bb8ae`
- `GS-4-on+detect_only-primary-s11-4bbdac`
- `GS-4-on+deterministic_only-primary-s11-2e9d2b`
- `GS-4-off-primary-s23-9fda6d`
- `GS-4-on-primary-s23-c44b2b`
- `GS-4-on+detect_only-primary-s23-0488b0`
- `GS-4-on+deterministic_only-primary-s23-bc701b`
- `GS-4P-off-primary-s7-7969da`
- `GS-4P-on-primary-s7-a8cb05`
- `GS-4P-on+detect_only-primary-s7-5e2dce`
- `GS-4P-on+deterministic_only-primary-s7-1fca6f`
- `GS-4P-off-primary-s11-36518c`
- `GS-4P-on-primary-s11-ebbf0d`
- `GS-4P-on+detect_only-primary-s11-b2fe44`
- `GS-4P-on+deterministic_only-primary-s11-69138a`
- `GS-4P-off-primary-s23-180b6c`
- `GS-4P-on-primary-s23-357d23`
- `GS-4P-on+detect_only-primary-s23-64fe41`
- `GS-4P-on+deterministic_only-primary-s23-262981`
- `GS-7-off-primary-s7-039f5f`
- `GS-7-on-primary-s7-9cf9c5`
- `GS-7-on+detect_only-primary-s7-d0000a`
- `GS-7-on+deterministic_only-primary-s7-f9b2db`
- `GS-7-off-primary-s11-a773cd`
- `GS-7-on-primary-s11-f4d4ec`
- `GS-7-on+detect_only-primary-s11-45de8c`
- `GS-7-on+deterministic_only-primary-s11-3a4464`
- `GS-7-off-primary-s23-f6dda5`
- `GS-7-on-primary-s23-8716f6`
- `GS-7-on+detect_only-primary-s23-8cf75a`
- `GS-7-on+deterministic_only-primary-s23-08df0d`
