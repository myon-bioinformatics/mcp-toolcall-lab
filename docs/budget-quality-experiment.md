# Budget versus task quality (opt-in experiment)

`budget_compression` extends the existing `char_compress` experiment with **separate
character and real-token constraints**. It permits declared losses of optional
explanations, politeness and preferred arguments, while preserving caller-specified
mandatory literal anchors. It does not silently truncate or claim that arbitrary
translation is lossless.

This is the first bounded tool-selection experiment under the [existing roadmap
#122](https://github.com/myon-bioinformatics/mcp-toolcall-lab/issues/122), not a new
production proxy, a learned language, or an implementation of weight compression.

## Corpus and independent oracle

`fixtures/budget_compression/cases.json` contains 20 fictional cases (10 English,
10 Japanese), across five tools: price lookup, reminder, retry policy, branch
archive and translation request. **No external action is executed.** Each case
advertises all five tool descriptions, argument types and optional markers. The expected tool and argument values are
read only by the scorer, never by `prompt_for` or candidate selection.

The translation cases test preservation of the *translation request*, not the
quality of a generated translation. Preferred tone/detail fields provide a small
soft-quality measure; this is not BLEU/COMET or a general semantic-fidelity score.

The mandatory anchors are caller-authored constraints: IDs, dates, numerical
bounds, negations and conditions. This is an **oracle-assisted protection policy**;
it does not learn to discover important information automatically. The brief and
skeleton candidates are also hand-authored fixture controls, not a general-purpose
summarizer or a learned emergent code.

Candidate order:

1. `verbatim`: full source; use sufficiently large budgets as the baseline.
2. `rules`: existing character compression; literal anchors can reject contractions
   that alter a required negation string.
3. `brief`: omit optional explanatory background and politeness modifiers.
4. `skeleton`: additionally omit the preferred tone/detail argument.

Each loss is named in `losses`. The first fitting candidate wins; no expected
answer or model output is used to choose it.

## What is budgeted

Character count is Python `len(str)` (Unicode code points), not bytes or a social
platform's weighted character algorithm. Token count comes from an injected real
tokenizer or an explicitly running llama-server `/tokenize`.

**The complete prompt text, including all advertised tool descriptions and output
instructions, is counted. Chat-template, BOS and transport-envelope overhead are
not counted.** Receipts label this `complete_prompt_text_without_chat_template`.
Do not interpret the limit as a full model-context or HTTP-request budget. UTF-8
bytes are recorded separately.

Both constraints must pass. If none of the protected candidates fits, status is
`infeasible`; it is not a fabricated answer. A missing tokenizer is
`tokenizer_unavailable`; a failing counter is `measurement_error`. A selected but
unexecuted case is `not_run`. All planned trials remain in `summary.all_trials`,
and the conservative overall success rate uses that denominator.

The model response must be a completed JSON `tool`/`arguments` object. The scorer
requires exact tool name and required argument values/types, rejects unknown
argument keys, and separately records preferred-argument quality. Outcomes distinguish
`success`, `wrong_action`, `abstained`, `invalid_output`, and `model_error`. Incomplete
HTTP answers (`finish_reason != stop`) are model errors, never ordinary successful
responses. Free-text explanations or markdown fences are intentionally invalid
under this structured-output task contract.

## Running

No models are downloaded implicitly; normal pytest is offline. For live execution,
start an explicitly installed pinned llama-server and invoke this in the same
network namespace:

```bash
PYTHONPATH=src python -m mcp_toolcall_lab.budget_compression \
  --cases fixtures/budget_compression/cases.json \
  --base-url http://127.0.0.1:8080 \
  --char-budget 850 1200 --token-budget 220 512 \
  --model-id MODEL --model-revision IMMUTABLE_REV --runtime-revision COMMIT \
  --output test-results/budget-quality-live.json
```

That is 20 × 2 × 2 = 80 planned trials (some may be infeasible before inference).
Use `/tokenize` measurements to choose budgets that include a verbatim baseline
and pressure points. The typed catalogue consumes part of every budget; choose thresholds from the
actual receipt, rather than assuming the source text alone is the prompt. Token
thresholds must be chosen from the actual target tokenizer.

Fixed generation settings are temperature 0, seed 42, and max output 128 tokens.
Record the server's thread/context/CPU settings alongside the receipt. No CPU/GPU
usage is inferred from a caller-supplied label. The receipt stores corpus SHA-256,
model/runtime revisions, budget, selected tier, missing anchors, measured metrics,
task result and optional-quality result for every trial.

An offline plan intentionally produces no quality claims:

```bash
PYTHONPATH=src python -m mcp_toolcall_lab.budget_compression \
  --cases fixtures/budget_compression/cases.json \
  --output test-results/budget-quality-plan.json
```

With default token budgets and no server, all trials are explicitly
`tokenizer_unavailable`.

## Validation

```bash
PYTHONPATH=src uv run pytest -x -q tests/test_budget_compression.py tests/test_char_compress.py
```

Tests cover independent oracle/catalogue handling, hard budgets, protected
negation/bounds, declared optional loss, type-sensitive tool arguments,
abstention/errors, and full-denominator accounting. Real model operation is
separate from these offline controls.

## Exploratory v1 measurement (2026-10-30)

The [original receipt](experiments/budget-quality/v1-exploratory.json) and
[provenance](experiments/budget-quality/v1-exploratory-provenance.json) preserve
80 trials using Qwen2.5-1.5B-Instruct Q4_K_M on native llama.cpp, CPU only,
two threads, context 4096, temperature 0 and seed 42. Model and runtime revisions,
weight SHA-256 and server invocation are in the provenance. The exact pre-typed
fixture and code are commit `76e66ef5d92f0f4f33b07898a9e8f41360f01c63`;
its corpus SHA-256 is in the receipt.

**This is an exploratory run with an underspecified argument catalogue.** It
advertised argument names but omitted types and explicit required/optional rules.
The scorer correctly rejected string-valued booleans, but this input-contract gap
makes the result unsuitable as a fair MCP schema comparison. The current fixture
adds types and required/optional rules before a separate follow-up measurement.
The versions must not be pooled as one experiment.

| Character cap | Token cap | Success / all planned | Wrong action | Abstained | Infeasible | Selected tiers | Preferred quality* |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 600 | 192 | 8 / 20 | 2 | 0 | 10 | 4 brief, 6 skeleton | 4 / 10 = 0.40 |
| 600 | 512 | 9 / 20 | 1 | 0 | 10 | 4 brief, 6 skeleton | 4 / 10 = 0.40 |
| 2048 | 192 | 8 / 20 | 4 | 8 | 0 | 20 verbatim | 12 / 12 = 1.00 |
| 2048 | 512 | 8 / 20 | 4 | 8 | 0 | 20 verbatim | 12 / 12 = 1.00 |

*Preferred quality is the mean among responses with a non-null quality score:
not all planned trials, and not limited to required-task successes. Abstentions
and infeasible trials have no quality score. Each case has one preferred argument.
Across all 80 planned trials, 33 succeeded, 11 returned a wrong action, 16
abstained and 20 were infeasible; no model errors occurred. All baseline
abstentions were Japanese-language cases. The verbatim prompt was 165–192 tokens,
so the 192-token threshold did not force compression in the high-character group.

The compressed feasible cases can be easier for the model because explanatory
text is removed, but these observations do not establish general improvement:
there is one tiny authored corpus, one small model and no repeated-trial estimate.
The identical selected prompts at the two 600-character conditions differed by
one success despite fixed seed; exact deterministic replay is not assumed.

## Typed-catalogue v2 measurement (2026-10-30)

The [typed receipt](experiments/budget-quality/v2-typed.json) and
[provenance](experiments/budget-quality/v2-typed-provenance.json) use the same
pinned model/runtime and CPU configuration as v1, with the independent typed
catalogue at commit `62bd6cef3e03133528e3cf23dfaafd2458d575c7`.
Corpus SHA-256: `3a34dd449a11099365dded08c9bfb2da4c7c1cc2a913ee2275546999fbb7c22e`.

| Character cap | Token cap | Success / all planned | Selected tiers | Preferred quality* |
| --- | --- | --- | --- | --- |
| 850 | 220 | 20 / 20 | 4 verbatim, 16 brief | 20 / 20 = 1.00 |
| 850 | 512 | 20 / 20 | 6 verbatim, 14 brief | 20 / 20 = 1.00 |
| 1200 | 220 | 20 / 20 | 14 verbatim, 6 brief | 20 / 20 = 1.00 |
| 1200 | 512 | 20 / 20 | 20 verbatim | 20 / 20 = 1.00 |

*The quality denominator is the number of responses with a non-null quality
score. All 80 typed standard trials succeeded, with no abstentions, wrong actions,
infeasible trials or model errors. The 1200/512 condition supplies a fully verbatim
baseline. A 220-token cap changes the selected tier even when the character cap
is generous, establishing that the token constraint is independently binding.
These four conditions remove background at most; they do not yet require losing
a preferred argument. The small authored corpus and one model do not establish
performance on unrestricted real MCP traffic or translation accuracy.

The [pressure receipt](experiments/budget-quality/v2-pressure.json) and
[provenance](experiments/budget-quality/v2-pressure-provenance.json) add a separate
800-character/512-token condition on the same typed corpus:

| Character cap | Token cap | Success / all planned | Infeasible | Selected tiers | Preferred quality* |
| --- | --- | --- | --- | --- | --- |
| 800 | 512 | 12 / 20 | 8 | 6 brief, 6 skeleton | 6 / 12 = 0.50 |

All six skeleton responses preserved the required tool and typed argument values
while omitting the preferred argument (each quality score 0/1). The six brief
responses retained their preferred argument (each 1/1). Eight tasks could not fit
the full typed catalogue plus a protected candidate; they remain `infeasible`
in the 20-trial denominator. There were no wrong actions or model errors.

The two typed receipts therefore cover **100 planned trials: 92 successes and
8 infeasible, with zero wrong actions, abstentions or model errors**. The pooled
92/100 figure describes these chosen five budget conditions, not a deployment
success-rate estimate. This first control demonstrates the intended tradeoff:
optional detail can be dropped while required task semantics remain correct for
feasible cases, and lower budgets can still make a task impossible.

To repeat the pressure condition, use the same command and provenance flags with
`--char-budget 800 --token-budget 512`. Keep its output separate from the four
standard conditions. No claim of a generally learned compressor follows from
hand-authored candidates, and dropping optional tone is not evidence about actual
translation fidelity.
