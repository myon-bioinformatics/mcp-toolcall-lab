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
advertises all five tool descriptions. The expected tool and argument values are
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
  --char-budget 600 2048 --token-budget 256 512 \
  --model-id MODEL --model-revision IMMUTABLE_REV --runtime-revision COMMIT \
  --output test-results/budget-quality-live.json
```

That is 20 × 2 × 2 = 80 planned trials (some may be infeasible before inference).
Use `/tokenize` measurements to choose budgets that include a verbatim baseline
and pressure points. The fixture prompts are roughly 580–800 characters, depending
on language and tier; 600 is intentionally infeasible for some tasks. Token
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
PYTHONPATH=src python -m pytest -x -q tests/test_budget_compression.py tests/test_char_compress.py
```

Tests cover independent oracle/catalogue handling, hard budgets, protected
negation/bounds, declared optional loss, type-sensitive tool arguments,
abstention/errors, and full-denominator accounting. Real model operation is
separate from these offline controls.
