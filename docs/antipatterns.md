# MCP tool-call lab anti-patterns

This document is the human-readable anti-pattern catalog for the lab.

The machine-readable/runtime sources remain:

- `src/mcp_toolcall_lab/antipatterns.py` — classifier constants and runtime classification.
- `fixtures/antipatterns/catalog.yaml` — stable runtime IDs and short operational notes.
- `test-results/antipatterns.jsonl` — append-only observations produced by smoke runs.

This Markdown file adds the broader design and maintenance lessons learned across
this repository and sibling projects. Stable runtime IDs must stay synchronized
with the Python/YAML sources above.

## Runtime anti-pattern IDs

| ID | Meaning | Typical cause | Preferred response |
| --- | --- | --- | --- |
| `SELECTOR_MISS` | Composer/input selector not found | frontend markup drift or page never reached chat | verify the real DOM and keep selectors centralized |
| `AUTH_BLOCKED` | Login/registration never reached a chat session | auth flags, onboarding, first-run flow | make account/bootstrap state explicit in the smoke |
| `SEND_NOT_CLICKED` | Input exists but Send was not triggered | disabled button, overlay, empty composer | distinguish interaction failure from later MCP failure |
| `TIMEOUT` | Send happened but assistant response never appeared | OpenAI mock hang, product stall, downstream timeout | inspect timeline/log layers before blaming MCP |
| `MCP_PICKER_OFF` | Chat request contains no tools | MCP picker/config not attached to the turn | assert advertised tools at the OpenAI request boundary |
| `MCP_NOT_CALLED` | Tools may be advertised but no MCP `tools/call` appears | model/mock did not choose a tool or product dropped the call | separate “tool advertised” from “tool invoked” |
| `MCP_ERROR` | MCP `tools/call` ran with error outcome | wrong tool name, bad args, timeout | preserve wire args and error outcome in JSONL |
| `UI_NO_RESULT` | MCP succeeded but expected content is absent in UI | rendering/translation/envelope mismatch | inspect actual product output structure, not only raw MCP result |
| `MCP_UNREACHABLE` | Client/stub cannot complete MCP handshake/call | DNS, healthcheck, process/network failure | isolate reachability from model/tool-selection logic |
| `CPU_LLM_UNREACHABLE` | CPU model service health endpoint fails | container/startup/network failure | classify service availability separately from inference quality |
| `CPU_LLM_COMPLETION_FAILED` | Health works but completion fails | OOM, bad args, inference/runtime regression | distinguish “process up” from “inference usable” |
| `CPU_LLM_GGUF_FETCH_FAILED` | GGUF discovery/download/verification failed | CDN/API access, missing asset, checksum mismatch | keep artifact acquisition separate from runtime inference failures |

## Design anti-patterns

These are broader rules that may not map one-to-one to a runtime classifier ID.

| ID | Anti-pattern | Why it is harmful | Preferred contract |
| --- | --- | --- | --- |
| `FICTIONAL_TOOL_NAME` | Model invents a tool that was never advertised | Tool selection looks plausible while violating the actual schema contract | choose only from current `tools/list` / OpenAI `tools` |
| `RAW_SCHEMA_EQUALS_SERVER_ACCEPTED` | Treating schema-valid JSON as equivalent to a successful MCP call | server-side validation/coercion/runtime can still fail | record raw schema validity and server acceptance separately |
| `EMPTY_EQUALS_ERROR` | Empty successful result is classified as protocol failure | destroys the semantic distinction between “no rows” and “call failed” | keep empty success and error as separate outcomes |
| `SDK_ONLY_PROBE` | Validating behavior only through one MCP SDK | hides raw HTTP/session/framing behavior | keep curl, httpx, SDK, browser-fetch, and product UI lanes distinct |
| `PRODUCT_WRAPPER_ASSUMPTION` | Assuming low-level MCP/OpenAI result shape survives unchanged through a chat product | middleware may wrap, flatten, rename, or serialize content | inspect and normalize the actual end-to-end product shape |
| `SUBSTRING_ONLY_ASSERTION` | Test only checks that “Yokohama” or another fragment appears | malformed structural output can still pass | assert records/columns/tool IDs/DOM structure when structure matters |
| `TOOL_ADVERTISED_EQUALS_TOOL_CALLED` | Presence of `tools` in the request is treated as proof of MCP invocation | model or product may never issue a tool call | observe OpenAI request, assistant `tool_calls`, MCP `tools/call`, and tool-role follow-up separately |
| `CHAT_ID_CONFLATION` | Product conversation IDs, OpenAI IDs, MCP session IDs, and lab `chat_*` IDs are treated as the same thing | traces become impossible to join reliably | preserve each namespace and correlate explicitly |
| `MCP_SESSION_AS_CONVERSATION` | MCP session ID is treated as durable product conversation identity | transport/session lifetime differs from chat lifetime | use lab/product conversation pins separately from MCP session IDs |
| `HIDDEN_COERCION` | Post-coercion values are logged as if they were the original wire arguments | debugging loses what the client actually sent | preserve raw arguments before validation/coercion |
| `DUPLICATE_MOCK_IMPLEMENTATION` | LibreChat/OpenWebUI mocks evolve independently | tool schemas and behavior drift | generate standalone copies from one package source |
| `GENERATED_FILE_HAND_EDIT` | Editing generated standalone mock files directly | regeneration silently overwrites fixes | change package source, then regenerate |
| `GENERATED_ARTIFACT_NOT_REGENERATED` | An inlined package source changes but committed standalone artifacts are not regenerated | even docstrings/prose change byte-for-byte `render()` output, so generated-file parity CI fails despite runtime logic being correct | treat every `INLINE_MODULES` source change as generator input: regenerate both standalone artifacts and verify parity before commit |
| `VENDORED_MARKDOWN_DRIFT` | Vendored `markdown.py` changes without provenance checks | stub behavior diverges from sibling source | keep provenance/hash checks and intentional refreshes |
| `REGEX_AS_PROTOCOL_PARSER` | Regex is expanded into a full MCP/JSON/Markdown parser | edge cases become fragile and undocumented | use regex only for narrow, explicit fallback contracts |
| `LIVE_NETWORK_IN_DEFAULT_CI` | Default CI depends on Wikipedia/Hugging Face/other live services | unrelated outages make deterministic tests flaky | use fixtures by default; live paths belong in explicit/manual lanes |
| `REAL_PRODUCT_EQUALS_REFERENCE_STUB` | Stub success is treated as proof LibreChat/Open WebUI will behave identically | real products add auth, middleware, UI state, envelopes, and rendering differences | test stub and real products as separate layers |
| `UI_SELECTOR_DUPLICATION` | Each smoke test hard-codes its own selectors | frontend drift causes inconsistent failures | centralize selectors/provenance in `frontends.py` |
| `SILENT_OBSERVABILITY_GAP` | A failed stage produces no durable log/ID/timestamp | diagnosis devolves into guessing | write structured JSONL + timestamped container stderr/logs |
| `ONE_LAYER_DIAGNOSIS` | Failure is attributed from only UI, only MCP, or only OpenAI logs | the broken hop may be elsewhere | correlate a timeline across UI → OpenAI → MCP → tool result |
| `HTTP11_STREAM_WITHOUT_END_SIGNAL` | Streaming response omits length/chunking/connection close | clients hang indefinitely | signal response end explicitly; shared SSE helper owns this |
| `STALE_DOM_HANDLE` | Polling a captured node while the framework replaces it | visible state can be correct while test waits forever | re-query selectors during polling |
| `BROAD_EXCEPT_PASS` | Diagnostic/cleanup exceptions are swallowed | the evidence for the original failure disappears | log cleanup failures and preserve fixture ordering |
| `ARCHITECTURE_FROM_THIN_FIXTURE` | Design conclusions are drawn from one canned fixture | apparent success may not generalize | expand corpus and record provenance before broad claims |
| `ANTIPATTERN_WITHOUT_REGRESSION` | Failure is documented but not pinned by a test when testable | the same bug returns silently | promote observed failure to a regression/contract test where practical |

## Layer model

When diagnosing a failed chat-to-MCP turn, do not collapse the whole path into
“tool calling failed.” Check each boundary independently:

```text
UI interaction
  -> chat request emitted
  -> tools advertised
  -> assistant selected tool
  -> MCP initialize/tools/list
  -> MCP tools/call
  -> tool result returned
  -> product middleware normalized result
  -> tool-role follow-up
  -> assistant/UI rendered expected structure
```

A failure at one layer should not be reclassified as another merely because the
final UI lacks the expected text.

## Stable-ID rules

1. Runtime classifier IDs in this document must match
   `antipatterns.py` and `catalog.yaml`.
2. Do not rename an existing ID just for wording/style.
3. New IDs should describe a reusable failure class, not one specific test name.
4. JSONL observations are append-only evidence; the catalog is the dictionary.
5. When a failure is general but not appropriate for runtime classification,
   document it under **Design anti-patterns** instead of forcing it into the
   classifier.

## Lessons imported from sibling repositories

From `markdown`:

- make supported/unsupported boundaries explicit;
- do not let a narrow regex silently become a full parser;
- distinguish expected lossiness from unexplained divergence;
- do not make architecture decisions from a thin real-world corpus.

From `ascii_artist`:

- use stable IDs for recurring failure modes;
- keep unknown content unless a narrow rule says it is a wrapper/error;
- promote real CI failures into regression tests;
- preserve the smallest reusable core contract instead of coupling it to one
  consumer.

## Update checklist

When a new anti-pattern is discovered:

1. Decide whether it is runtime-classifiable or design-only.
2. Reuse an existing stable ID if the failure class already exists.
3. If runtime-classifiable, update Python + YAML + tests together.
4. Add the observed example or general lesson here.
5. Add/extend a regression test where practical.
6. Keep protocol logs and UI observations separate but correlatable.
