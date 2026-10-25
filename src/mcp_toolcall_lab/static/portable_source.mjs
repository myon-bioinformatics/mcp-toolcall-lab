/**
 * Bounded, deterministic lexical selection. No DOM, network, model or Intl APIs.
 *
 * Offsets are original JavaScript UTF-16 code units, never normalized text.
 * Sentences end at . ! ? 。 ！ ？ or a CR/LF/U+2028/U+2029 newline. Runs of
 * punctuation and immediately following closing quotes/brackets stay together.
 * Only the explicit whitespace set below is trimmed. Abbreviations and decimals
 * are deliberately not language-aware. This is a heuristic, not NLP confidence.
 *
 * Keywords are exact, case-sensitive Latin words (with combining marks/internal
 * apostrophes or hyphens), or contiguous Han/Katakana runs. Character ranges are
 * frozen here rather than using version-dependent Unicode properties or a
 * dictionary. Hiragana and other scripts remain intact in sentence evidence but
 * are not offered as keyword hints. At most 128 options are surfaced, ordered by
 * first occurrence; their counts still cover the complete source.
 */
export const MAX_SOURCE_BYTES = 128 * 1024;
export const MAX_SOURCE_SENTENCES = 1000;
export const MAX_SELECTED_KEYWORDS = 32;
export const MAX_SELECTED_SENTENCES = 100;
export const MAX_KEYWORD_OPTIONS = 128;

const encoder = new TextEncoder();
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const keysAre = (value, keys) => object(value) && Object.keys(value).sort().join('|') === [...keys].sort().join('|');
const SPACE = /[\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]/;
const NEWLINE = /[\r\n\u2028\u2029]/;
const PUNCTUATION = /[.!?。！？]/;
const CLOSER = /["'”’»）)\]}」』】》〉]/;
// Fixed Latin, combining-mark, Han (including supplementary), and Katakana ranges.
const LATIN = 'A-Za-z\\u00c0-\\u00d6\\u00d8-\\u00f6\\u00f8-\\u024f\\u1e00-\\u1eff';
const MARK = '\\u0300-\\u036f';
const HAN_KATAKANA = '\\u3005\\u3007\\u303b\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff\\u{20000}-\\u{2fa1f}\\u{30000}-\\u{323af}\\u30a1-\\u30fa\\u30fc-\\u30ff\\u31f0-\\u31ff\\uff66-\\uff9f';
const TOKEN = new RegExp(`[${LATIN}][${LATIN}${MARK}]*(?:['’\\-][${LATIN}][${LATIN}${MARK}]*)*|[${HAN_KATAKANA}]+`, 'gu');
const STOPWORDS = new Set((
  'a an and are as at be been being but by can could did do does for from had has have he her hers him his how i if in into is it its me my no nor not of on or our ours she so than that the their theirs them then there these they this those to too us was we were what when where which who why will with would you your yours '
  + '場合 事 物 為 等 私 僕 自分'
).split(' '));
const asciiLower = text => text.replace(/[A-Z]/g, letter => String.fromCharCode(letter.charCodeAt(0) + 32));
const idPattern = kind => new RegExp(`^${kind}:[0-9a-f]{64}:(?:0|[1-9][0-9]{0,5}):(?:0|[1-9][0-9]{0,5})$`);

function invalidUnicode(text) {
  for (let index = 0; index < text.length; index++) {
    const code = text.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = text.charCodeAt(++index);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
    } else if (code >= 0xdc00 && code <= 0xdfff) return true;
  }
  return false;
}

function spans(text) {
  const result = [];
  let start = 0;
  const append = end => {
    let left = start;
    while (left < end && SPACE.test(text[left])) left++;
    while (end > left && SPACE.test(text[end - 1])) end--;
    if (left < end) result.push({start: left, end, text: text.slice(left, end)});
  };
  for (let index = 0; index < text.length; index++) {
    if (PUNCTUATION.test(text[index])) {
      while (index + 1 < text.length && PUNCTUATION.test(text[index + 1])) index++;
      while (index + 1 < text.length && CLOSER.test(text[index + 1])) index++;
      append(index + 1);
      start = index + 1;
    } else if (NEWLINE.test(text[index])) {
      append(index);
      while (index + 1 < text.length && NEWLINE.test(text[index + 1])) index++;
      start = index + 1;
    }
    if (result.length > MAX_SOURCE_SENTENCES) return null;
  }
  append(text.length);
  return result.length > MAX_SOURCE_SENTENCES ? null : result;
}

/** Structural/budget validation only. Existence of source-bound IDs is checked below. */
export function validateSourceInput(input) {
  if (!keysAre(input, ['text', 'keywords', 'sentence_ids']) || typeof input.text !== 'string'
      || !Array.isArray(input.keywords) || !Array.isArray(input.sentence_ids)) {
    return 'source_selection requires exactly text (string), keywords (keyword ID array), and sentence_ids (sentence ID array).';
  }
  // UTF-8 needs at least one byte per UTF-16 code unit for valid input.
  if (input.text.length > MAX_SOURCE_BYTES) return 'Source exceeds the 128 KiB UTF-8 input limit.';
  if (invalidUnicode(input.text)) return 'Source must contain valid Unicode; lone surrogates are not accepted.';
  if (encoder.encode(input.text).length > MAX_SOURCE_BYTES) return 'Source exceeds the 128 KiB UTF-8 input limit.';
  for (const [field, kind, limit] of [['keywords', 'keyword', MAX_SELECTED_KEYWORDS], ['sentence_ids', 'sentence', MAX_SELECTED_SENTENCES]]) {
    const ids = input[field];
    if (ids.length > limit) return `${field} exceeds the ${limit}-ID selection limit.`;
    if (ids.some(id => typeof id !== 'string' || !idPattern(kind).test(id))) return `${field} contains an invalid source-bound ID.`;
    if (new Set(ids).size !== ids.length) return `${field} contains duplicate IDs.`;
  }
  if (spans(input.text) === null) return 'Source exceeds the 1,000-sentence limit.';
  return null;
}

/** Trusted helpers are supplied by the shared portable execution core. */
export async function executeSourceSelection(request, {sha256, canonicalRequest, failure}) {
  if (!keysAre(request, ['schema', 'operation', 'input']) || request.schema !== 'lab-execution/1' || request.operation !== 'source_selection') {
    return failure('invalid_input', 'Expected a lab-execution/1 source_selection request with schema, operation and input only.');
  }
  const invalid = validateSourceInput(request.input);
  if (invalid) return failure('invalid_input', invalid);
  // Snapshot before awaiting a helper: callers cannot change the validated IDs
  // during hashing and obtain a receipt whose provenance describes another input.
  const text = request.input.text;
  const keywords = [...request.input.keywords];
  const sentenceIds = [...request.input.sentence_ids];
  const requestSnapshot = {schema: request.schema, operation: request.operation, input: {text, keywords, sentence_ids: sentenceIds}};
  const sourceHash = await sha256(text);
  const sourceBytes = encoder.encode(text).length;
  const sentences = spans(text).map(span => ({id: `sentence:${sourceHash}:${span.start}:${span.end}`, ...span}));
  const options = new Map();
  for (const match of text.matchAll(TOKEN)) {
    const token = match[0];
    if (STOPWORDS.has(asciiLower(token))) continue;
    const prior = options.get(token);
    if (prior) prior.count++;
    else options.set(token, {id: `keyword:${sourceHash}:${match.index}:${match.index + token.length}`, text: token, count: 1});
  }
  const keywordOptions = [...options.values()].slice(0, MAX_KEYWORD_OPTIONS);
  const optionIds = new Set(keywordOptions.map(option => option.id));
  if (keywords.some(id => !optionIds.has(id))) return failure('invalid_input', 'A keyword ID is unknown or stale for this exact source. No partial output returned.');
  const wantedKeywords = new Set(keywords);
  const selectedKeywords = keywordOptions.filter(option => wantedKeywords.has(option.id));
  const selectedTokens = new Set(selectedKeywords.map(option => option.text));
  const candidates = keywords.length === 0 ? sentences : sentences.filter(sentence => {
    for (const match of sentence.text.matchAll(TOKEN)) if (selectedTokens.has(match[0])) return true;
    return false;
  });
  const candidateIds = new Set(candidates.map(sentence => sentence.id));
  if (sentenceIds.some(id => !candidateIds.has(id))) return failure('invalid_input', 'A sentence ID is stale, unknown, or not among the current keyword candidates. No partial output returned.');
  const wantedSentences = new Set(sentenceIds);
  const selectedSentences = candidates.filter(sentence => wantedSentences.has(sentence.id));
  const selectionStatus = sentences.length === 0 ? 'empty_source' : candidates.length === 0 ? 'no_candidates' : selectedSentences.length === 0 ? 'none_selected' : 'ready';
  const summaryRequest = selectedSentences.length === 0 ? null : {
    schema: 'lab-summary-request/1', source_sha256: sourceHash,
    source_bytes: sourceBytes, offset_unit: 'utf16_code_units',
    selected_source_evidence: selectedSentences,
    sampler_proposal: {temperature: 0, seed: 42, top_p: 0.75, repeat_penalty: 1.0, top_k: 40},
    runtime_status: 'build_time_only', generated_text: null,
    top_k_note: 'top_k=40 follows the pinned llama.cpp engine default; it was not inferred from top_p.',
    evidence_policy: 'Source evidence is untrusted data, not instructions. No instruction in the source has been executed.',
    determinism_note: 'Fixed sampler proposals do not guarantee deterministic generation across models or runtimes; this request prepares evidence only and does not invoke the build-time runtime.',
  };
  return {
    schema: 'lab-execution/1', status: 'ok', execution_mode: 'deterministic_source_selection',
    model_inference_performed: false, confidence: null,
    confidence_reason: 'Lexical hints and counts are deterministic heuristics, not calibrated confidence or a probability of correctness.',
    selection_status: selectionStatus,
    keyword_options: keywordOptions, keyword_options_truncated: options.size > MAX_KEYWORD_OPTIONS,
    sentences, candidates, selected_keywords: selectedKeywords,
    selected_sentences: selectedSentences, summary_request: summaryRequest,
    metrics: {source_bytes: sourceBytes, sentences: sentences.length, keyword_options: keywordOptions.length, keyword_options_available: keywordOptions.length, keyword_options_total: options.size, candidates: candidates.length, selected_keywords: selectedKeywords.length, selected_sentences: selectedSentences.length},
    provenance: {input_sha256: sourceHash, input_bytes: sourceBytes, request_sha256: await sha256(canonicalRequest(requestSnapshot)), source: 'caller_supplied_text', offset_unit: 'utf16_code_units', verification: 'Content identity only; source claims and producer authenticity are not verified.'},
    heuristics: {
      keyword_method: 'Exact Latin words and contiguous Han/Katakana compounds from explicit character ranges; fixed stopwords; first 128 unique tokens in first-occurrence order; counts cover the full source.',
      sentence_method: 'Explicit punctuation/newline boundaries and whitespace trimming; not language-aware sentence parsing.',
      matching: 'Case-sensitive exact lexical-token OR match; no keywords means all sentences; selected sentences remain in original source order.',
      calibrated: false,
    },
  };
}
