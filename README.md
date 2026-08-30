# ControlPlane

A runtime supervisor for tool-augmented, multi-step AI agents.

**Live dashboard: [controlplane-supervisor.vercel.app](https://controlplane-supervisor.vercel.app)**

The hosted site reads the same run records that are committed to this
repository, so every number on it can be traced to a ledger you can open. It
never calls a model — the evidence was produced locally and checked in, which is
also why the deployment needs no API key.

Most responsible-AI tooling checks the **text** a model produces. That is the
easy half. The hard half is that an agent takes **actions**: it calls tools, it
writes to systems, it moves money, and one bad value at step 11 quietly shapes
every decision through step 57. By the time anything looks wrong, the question
is no longer "is this output safe" but "which step do we roll back to, and what
do we undo".

ControlPlane sits at the protocol boundary between an agent and its tools. It
intercepts every call, writes an append-only checkpoint, evaluates a library of
declarative invariants against the run so far, and — when one breaks — finds
the last step at which the run was still correct and recovers to it.

```
agent ──▶ [ intercept ] ──▶ tool
             │
             ├─ checkpoint  (append-only, hash-chained, PII redacted at write)
             ├─ assert      (inline: microseconds · async: the deep path)
             ├─ localize    (binary search over prefixes — exact, no model calls)
             └─ recover     (compensate, restore, re-plan with corrective note)
```

---

## Why localization is the interesting part

Detection is table stakes; plenty of systems can tell you something is wrong.
The expensive question on a long run is *where*.

An invariant here is a **pure function of a ledger prefix**. That single
constraint has a large consequence: if `holds(prefix(n))` can be evaluated for
any `n` without touching live tool state, and the invariant is **monotone**
(once false, false for every longer prefix), then the ledger is a sorted array
and finding the last good step is a **binary search** — `O(log N)` deterministic
evaluations, zero model calls, no judgement involved.

Monotonicity is a claim, so it is tested rather than asserted. Every invariant
declares a `monotone` flag, and `tests/test_monotonicity.py` generates
randomised adversarial ledgers, finds prefixes where each check fails, and
verifies it keeps failing. A wrong declaration would silently corrupt
localization while every "did it fire" test stayed green.

Non-monotone checks are allowed. They are just routed to a provenance-distance
fallback and labelled `estimated` rather than `exact`, so a softer answer is
never presented as a hard one.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # add your OpenRouter key
pytest -q                   # 127 tests, no network required
```

```bash
controlplane list                  # scenarios, invariants, fault catalogue
controlplane tiers                 # policy tiers and their latency budgets
controlplane run GS-1              # one scenario, narrated
controlplane ladder                # the full ablation grid, writes CLAIMS.md
controlplane serve                 # dashboard at http://127.0.0.1:8000
```

Every model call is disk-cached on a hash of the request and metered against a
hard USD ceiling (`CONTROLPLANE_USD_BUDGET`). A full 84-cell ladder costs a few
cents on the first pass and nothing on reruns.

---

## What is actually here

**Three workloads, one library.** A customer support assistant (external,
150 ms inline budget, moves money), an internal knowledge assistant (entitlement
boundaries), and a long-horizon underwriting tool (~55 steps, batch, 3 s). The
same twenty invariants run across all three. What differs is *placement*: a
check the interactive tier cannot afford inline is demoted to the deep path,
where it still fires — just later, and sometimes too late to prevent harm. That
trade-off is the product's actual thesis, and GS-4 / GS-4P exist to measure it
rather than describe it.

**Twenty invariants across eight classes** — binding, schema, precondition,
provenance, entitlement, progress, budget, safety — each declaring its
monotonicity, inline cost class, severity, and whether a violation implicates
the call's *arguments* or its *result*. That last flag matters more than it
sounds: when arguments were wrong, retrying unchanged repeats the fault, so the
plan is forbidden; when the *result* was wrong (a stale snapshot, a 502 body),
the same call is exactly what must be retried, and forbidding it strands the
agent. Getting this backwards turned a recoverable run into a stalled one, and
GS-4P is the regression test.

**An append-only, hash-chained ledger** with two views. The *physical log* keeps
every checkpoint ever written, including attempts that were later rolled back —
that is the audit trail, and nothing is ever removed from it. The *logical view*
is the sequence the agent is currently living in; a rollback truncates it. Edits,
deletions and reordering are all detectable by recomputing the chain.

**Tenant isolation at the store**, not by convention. There is no API that
returns another tenant's runs, and the "exists but not yours" error is
byte-identical to "does not exist" — otherwise the error message becomes an
existence oracle. Tested in `tests/test_ledger.py`.

**PII redacted at write time**, span-level, two tiers. Identity checks compare
hashes of the *pre-redaction* values, so binding invariants still work on data
the ledger no longer contains. Over-redaction is treated as a real failure and
tested for: redact `POL-100001` into a pincode placeholder and the binding
checks lose the identifier they exist to verify.

---

## Evidence

`CLAIMS.md` is **generated** from run records — never hand-edited. A claim with
no runs behind it prints as `UNSUPPORTED` rather than quietly disappearing.

The ablation ladder removes exactly one thing per rung, so each rung earns
exactly one claim:

| Rung | What it removes | What it tells you |
|---|---|---|
| `off` | the whole supervisor | the baseline, and where harm actually happens |
| `on` | nothing | full system |
| `detect_only` | recovery | how much comes from rolling back vs. noticing |
| `deterministic_only` | the LLM adjudicator | how much the cheap checks carry alone |

Current numbers, 7 scenarios × 3 seeds = 21 runs per condition:

| Condition | Task success | Detections | Exact L | Inline p95 | Harm |
|---|---|---|---|---|---|
| `off` | 71.4% | 0 | — | — | 6 runs |
| `on` | 100% | 22 | 100% | 0.27 ms | 0 runs |
| `detect_only` | 28.6% | 18 | 100% | 0.21 ms | 0 runs |
| `deterministic_only` | 100% | 22 | 100% | 0.26 ms | 0 runs |

Paired McNemar on off vs. on: helped 6, hurt 0, **p = 0.031**. Two results worth
stating plainly, because neither is flattering by default:

- **`detect_only` scores *worse* than `off`** — 28.6% against 71.4%. Blocking
  without recovery strands the agent mid-workflow: it is refused, escalates, and
  stops. Detection on its own is not merely less useful than the full system, it
  is worse than nothing for task completion. Recovery is what converts a guard
  into value.
- **`deterministic_only` exactly matches the full system.** On this suite the
  LLM adjudicator is not carrying the result; twenty deterministic invariants
  are. Good news for latency and cost, and a reason not to attribute the
  headline to the model-as-judge component.

Localization is scored against the **injected fault step**, and compared to the
alternatives on identical incidents replayed from saved ledgers (n = 54):

| Method | Exact | Within ±1 | Mean error | Model calls |
|---|---|---|---|---|
| binary search (ours) | **100%** | 100% | 0.0 | 0 (4.5 evaluations) |
| LLM reads the whole trace | 57.4% | 61.1% | 5.35 | 1 per incident |
| blame the alarm step | 22.2% | 61.1% | 5.70 | 0 |
| blame the last tool call | 22.2% | 61.1% | 5.70 | 0 |
| random | 16.7% | 33.3% | 4.94 | 0 |

Detection lag (Δdetect) has a median of 1.5 steps but a p90 of 42 — that spread
*is* the inline-versus-async trade-off, visible in one number.

One subtlety that took a scoring fix to get right: on a 57-step run the agent
sometimes makes its **own** unrelated mistake, and a guard catches it. Charging
that against a fault injected 30 steps earlier measures nothing. Each fault
declares which checks it can plausibly trip, so incidents split into
*traceable to an injection* (scored), *the agent's own error* (counted
separately, real, but with no injected origin to score against) and *false
alarm* (nothing was wrong). Before that fix, exact localization read 87% — an
understatement caused entirely by mis-attribution.

`CLAIMS.md` is authoritative and regenerates from the runs on disk.

**Guard liveness.** 18 of 20 invariants are *proven load-bearing* by the
sabotage suite: for each, there is a case showing the fault goes through when
that guard alone is removed. An unproven guard is listed as unvalidated rather
than assumed healthy, because an inactive check and a check with nothing to
catch look identical from outside. The two unproven ones are the non-monotone,
LLM-judged checks.

**Held-out faults.** F9 (PII injected mid-run) and F10 (protected-attribute
swap) are refused by the harness unless a holdout evaluation asks for them
explicitly. If the library only caught faults it was written against, it would
have memorised the catalogue rather than generalised, and only a holdout can
tell the difference.

---

## Honest limitations

**The environments are simulated.** Three mock tool estates with deterministic
ground truth. This is what makes exact scoring possible — you cannot measure
localization error without knowing the true fault step — but it is not evidence
about a production estate with flaky networks, ambiguous schemas and tools whose
reversibility nobody documented. The reversibility classes here are *declared*
in a manifest. In a real deployment, obtaining that manifest honestly is
probably harder than everything in this repository.

**n is small, and it is exactly large enough.** Seven scenarios across three
seeds gives 21 runs per condition, and the paired McNemar clears significance at
p = 0.031 — on the strength of 6 discordant pairs. That is a real result and a
thin one; it would not survive one scenario behaving differently. Bootstrap CIs
accompany every headline number for the same reason. The honest reading is
"consistent, directionally strong, and under-powered", not "proven".

**Recoverability@L is 68%, not 100%.** Three of the incidents escalate to a
human instead of recovering. That is usually the *correct* outcome — an
irreversible refund with no compensator should not be silently retried — but the
metric does not distinguish "could not recover" from "correctly refused to", and
it should.

**One agent architecture, mostly one model.** A ReAct-style loop and a
phase-graph variant, run against a small number of cheap OpenRouter backends.
Portability across model families is a matrix the harness supports and this
repository has only lightly exercised.

**Monotonicity is verified by property testing, not proof.** Randomised
adversarial ledgers with coverage assertions — every monotone invariant must
actually fire during the test or the suite fails as untested. That is much
stronger than trusting the flag, and much weaker than a proof.

**The false-alarm picture is thin.** GS-7 is the only clean scenario, so
intervention regret rests on three runs. It reports zero false alarms and zero
interventions, which is the right answer on a clean run and almost no evidence.
Over-flagging is the failure mode most likely to get a system like this switched
off in production, and one clean scenario cannot characterise it.

**Recovery assumes compensators are honest.** When a tool declares itself
compensable, the engine believes it. A compensator that silently half-succeeds
would produce a confident, wrong "restored" verdict. World-state hashes before
and after are recorded so the mismatch is at least *visible*, but the system
cannot currently prove a compensation worked.

**No real regulatory mapping.** The policy tiers are shaped like something that
could carry a real governance regime (per-workload, versioned, config-reloadable
with an audit trail behind each decision). They are not mapped to any actual
regulation, and doing that properly is a legal exercise, not an engineering one.

---

## Design decisions worth arguing with

**Invariants are pure functions of a prefix.** This is restrictive — no live
tool state, no model internals — and it is what buys exact localization and
hours-later replay. Every convenience given up here paid for that.

**Detection is deterministic first, model-judged only at the edges.** An
LLM-as-judge on the critical path costs latency and money and cannot be
sabotage-tested the way a deterministic check can. The model is used for
adjudicating non-monotone checks, and the `deterministic_only` rung exists
specifically to show how little the headline depends on it.

**The supervisor never reads model internals.** Only message text and tool
calls cross the boundary — no logprobs, no attention, no vendor-specific
signals. Enterprises consume models via API; a design that needs internals is a
design that does not deploy.

**Fault injection targets `(tool, occurrence)`, not absolute step numbers.**
Agents take different paths on different seeds, so "inject at step 11" targets
a different action every run and the ground truth stops meaning anything.

**The ledger deep-copies on write, and does not trust its callers.** Pure
functions of a prefix are only pure if the prefix cannot change after it is
written. An earlier version let the supervisor pass its live budget object by
shallow copy, so a counter mutated at step 40 appeared inside the checkpoint for
step 3 — the audit chain broke loudly, which is how it was found, but the real
damage was silent: invariants evaluating an early prefix were seeing state from
the future, which is precisely the assumption binary search rests on. Enforced
at the store rather than at every call site, and regression-tested by mutating a
live object after appending it.

**Observe-only means genuinely silent.** With the supervisor off, the deep path
does not run and no incidents are recorded. An earlier version left async
checking on, which contaminated the baseline arm of the very comparison the
ladder exists to make.

---

## Layout

```
src/controlplane/
  types.py         checkpoint, verdict, violation, localization, incident
  ledger.py        append-only hash-chained store, physical + logical views
  pii.py           span-level detection and redaction, two tiers
  invariants/      20 checks in 8 classes + declarative YAML loader
  localize.py      binary search, provenance fallback, three-layer RCA
  recover.py       compensation, state restoration, corrective notes
  supervisor.py    interception, policy tiers, inline/async budget split
  policy/          named versioned tiers, assigned per workload
  envs/            three mock tool estates + fault injector
  agent.py         ReAct and phase-graph loops driven through the supervisor
  harness/         runner, metrics, baselines, ablation ladder, CLAIMS.md
  dashboard/       FastAPI + a dependency-free front end
tests/             monotonicity properties, sabotage suite, ledger, PII
```

## Dashboard

Hosted at [controlplane-supervisor.vercel.app](https://controlplane-supervisor.vercel.app),
or `controlplane serve` locally — incident feed with localization error against injected
ground truth; run timelines that keep rolled-back attempts visible because the
audit trail is append-only; a localization view that **re-runs the binary search
live** against the saved ledger so the probe sequence is checkable rather than
asserted; and the guard-liveness attestation.
