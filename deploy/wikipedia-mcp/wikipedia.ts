/** MediaWiki plaintext extract + heading split, matching wikipedia_tool.py. */

export const DEFAULT_LANG = "en";
export const WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php";
export const USER_AGENT =
  "mcp-toolcall-lab-wikipedia/0.1 (https://github.com/myon-bioinformatics/mcp-toolcall-lab; lab demo, not production traffic)";
export const TIMEOUT_MS = 10_000;
export const FIXTURE_ENV = "MCP_TOOLCALL_LAB_WIKIPEDIA_FIXTURE";
/** MediaWiki-like project codes: en, zh-yue, simple. Rejects host injection. */
export const LANG_RE = /^[a-z0-9-]{2,24}$/i;

const WIKI_HEADING_RE = /^(=+)\s*(.+?)\s*=+\s*$/;
const ATX_HEADING_RE = /^(#{1,6})\s+(.+?)\s*#*\s*$/;

export class WikipediaFetchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WikipediaFetchError";
  }
}

export type HeadingRow = { heading: string; level: number };

export type WikipediaArticle = {
  canonical_title: string;
  lang: string;
  extract: string;
};

export type Section = {
  level: number;
  title: string;
  slug: string;
  body: string;
};

type CacheEntry = {
  article: WikipediaArticle;
  expiresAt: number;
  aliases: Set<string>;
};

function cacheKey(lang: string, title: string): string {
  return `${lang}\0${title.trim().toLowerCase()}`;
}

export function normalizeLang(lang: string | undefined): string {
  const cleaned = (lang || "").trim();
  return assertAllowedLang(cleaned || DEFAULT_LANG);
}

export function assertAllowedLang(lang: string): string {
  if (!LANG_RE.test(lang)) {
    throw new WikipediaFetchError(`invalid wikipedia language code '${lang}'`);
  }
  return lang.toLowerCase();
}

/**
 * Build the MediaWiki action API URL and refuse anything that is not a
 * `*.wikipedia.org` host (no userinfo, no open-redirect / host injection).
 */
export function wikipediaApiUrl(lang: string): URL {
  const safeLang = assertAllowedLang(lang);
  const url = new URL(`https://${safeLang}.wikipedia.org/w/api.php`);
  assertSafeWikipediaApiUrl(url);
  return url;
}

export function assertSafeWikipediaApiUrl(url: URL): void {
  if (url.protocol !== "https:") {
    throw new WikipediaFetchError(`refusing non-https wikipedia URL '${url.href}'`);
  }
  if (url.username || url.password) {
    throw new WikipediaFetchError(`refusing wikipedia URL with userinfo '${url.href}'`);
  }
  const host = url.hostname.toLowerCase();
  if (!host.endsWith(".wikipedia.org") || host === "wikipedia.org") {
    throw new WikipediaFetchError(`refusing unexpected wikipedia host '${url.hostname}'`);
  }
  // Single project subdomain only — not wikipedia.org.evil.com or nested hosts.
  if (!/^[a-z0-9-]{2,24}\.wikipedia\.org$/i.test(host)) {
    throw new WikipediaFetchError(`refusing unexpected wikipedia host '${url.hostname}'`);
  }
  if (url.pathname !== "/w/api.php") {
    throw new WikipediaFetchError(`refusing unexpected wikipedia path '${url.pathname}'`);
  }
}

export function slugify(title: string): string {
  return title.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

export function wikiHeadingsToAtx(text: string): string {
  return text.split(/\r?\n/).map((line) => {
    const match = line.match(WIKI_HEADING_RE);
    if (!match) return line;
    return `${"#".repeat(match[1].length)} ${match[2]}`;
  }).join("\n");
}

export function parseSections(markdown: string): Section[] {
  const lines = markdown.split(/\r?\n/);
  const found: Array<{ index: number; level: number; title: string }> = [];
  for (let i = 0; i < lines.length; i++) {
    const match = lines[i].match(ATX_HEADING_RE);
    if (match) {
      found.push({ index: i, level: match[1].length, title: match[2].trim() });
    }
  }
  const sections: Section[] = [];
  for (let idx = 0; idx < found.length; idx++) {
    const start = found[idx].index;
    const end = idx + 1 < found.length ? found[idx + 1].index : lines.length;
    const body = lines.slice(start + 1, end).join("\n").trim();
    const title = found[idx].title;
    sections.push({
      level: found[idx].level,
      title,
      slug: slugify(title),
      body,
    });
  }
  return sections;
}

export function lookupHeading(
  query: string,
  sections: Section[],
  fuzzy = true,
): Section | null {
  let needle = query.trim();
  if (needle.startsWith("#")) needle = needle.replace(/^#+/, "").trim();
  if (!needle) return null;
  const slug = slugify(needle);
  const folded = needle.toLowerCase();
  for (const section of sections) {
    if (
      section.title === needle ||
      section.title.toLowerCase() === folded ||
      section.slug === slug
    ) {
      return section;
    }
  }
  if (!fuzzy) return null;
  for (const section of sections) {
    const title = section.title.toLowerCase();
    if (title.startsWith(folded) || folded.startsWith(title) || title.includes(folded)) {
      return section;
    }
  }
  return null;
}

export function articleSections(article: WikipediaArticle): Section[] {
  const atx = `# ${article.canonical_title}\n\n${wikiHeadingsToAtx(article.extract)}`;
  return parseSections(atx);
}

export function articleHeadings(article: WikipediaArticle): HeadingRow[] {
  return articleSections(article).map((section) => ({
    heading: section.title,
    level: section.level,
  }));
}

export function articleResult(article: WikipediaArticle) {
  return {
    canonical_title: article.canonical_title,
    lang: article.lang,
    extract: article.extract,
    headings: articleHeadings(article),
  };
}

function payloadCoversTitle(payload: Record<string, unknown>, title: string): boolean {
  const requested = title.trim().toLowerCase();
  if (!requested) return false;
  const query = (payload.query || {}) as Record<string, unknown>;
  const pages = (query.pages || {}) as Record<string, Record<string, unknown>>;
  for (const page of Object.values(pages)) {
    if (String(page.title || "").trim().toLowerCase() === requested) return true;
  }
  const redirects = (query.redirects || []) as Array<Record<string, unknown>>;
  for (const redirect of redirects) {
    if (String(redirect.from || "").trim().toLowerCase() === requested) return true;
    if (String(redirect.to || "").trim().toLowerCase() === requested) return true;
  }
  return false;
}

function parseExtractPayload(
  payload: Record<string, unknown>,
  title: string,
  lang: string,
): WikipediaArticle {
  const query = (payload.query || {}) as Record<string, unknown>;
  const pages = (query.pages || {}) as Record<string, Record<string, unknown>>;
  const page = Object.values(pages)[0];
  if (!page || "missing" in page) {
    throw new WikipediaFetchError(`no ${lang}.wikipedia.org article named '${title}'`);
  }
  return {
    canonical_title: String(page.title || title),
    lang,
    extract: String(page.extract || ""),
  };
}

function redirectAliases(payload: Record<string, unknown>): string[] {
  const query = (payload.query || {}) as Record<string, unknown>;
  const redirects = (query.redirects || []) as Array<Record<string, unknown>>;
  const aliases: string[] = [];
  for (const redirect of redirects) {
    const source = String(redirect.from || "").trim();
    if (source) aliases.push(source);
  }
  return aliases;
}

async function readFixturePayload(
  path: string,
  title: string,
  lang: string,
): Promise<Record<string, unknown>> {
  let payload: unknown;
  try {
    payload = JSON.parse(await Deno.readTextFile(path));
  } catch (err) {
    throw new WikipediaFetchError(`could not read wikipedia fixture ${path}: ${err}`);
  }
  if (!payload || typeof payload !== "object" || !payloadCoversTitle(payload as Record<string, unknown>, title)) {
    throw new WikipediaFetchError(`no ${lang}.wikipedia.org article named '${title}'`);
  }
  return payload as Record<string, unknown>;
}

async function fetchExtractPayload(title: string, lang: string): Promise<Record<string, unknown>> {
  const api = wikipediaApiUrl(lang);
  const fixture = Deno.env.get(FIXTURE_ENV)?.trim();
  if (fixture) {
    return await readFixturePayload(fixture, title, lang);
  }
  const query = new URLSearchParams({
    action: "query",
    format: "json",
    prop: "extracts",
    explaintext: "1",
    exsectionformat: "wiki",
    redirects: "1",
    titles: title,
  });
  const url = new URL(api.href);
  url.search = query.toString();
  assertSafeWikipediaApiUrl(url);
  try {
    const response = await fetch(url, {
      headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok) {
      throw new WikipediaFetchError(
        `could not fetch '${title}' from ${lang}.wikipedia.org: HTTP ${response.status}`,
      );
    }
    const payload = await response.json();
    if (!payload || typeof payload !== "object") {
      throw new WikipediaFetchError(`could not fetch '${title}' from ${lang}.wikipedia.org: bad JSON`);
    }
    return payload as Record<string, unknown>;
  } catch (err) {
    if (err instanceof WikipediaFetchError) throw err;
    throw new WikipediaFetchError(`could not fetch '${title}' from ${lang}.wikipedia.org: ${err}`);
  }
}

export class WikipediaExtractCache {
  private readonly maxsize: number;
  private readonly ttlMs: number;
  private readonly order: string[] = [];
  private readonly entries = new Map<string, CacheEntry>();
  private readonly alias = new Map<string, string>();
  private readonly inflight = new Map<string, Promise<WikipediaArticle>>();

  constructor(maxsize = 16, ttlSeconds = 300) {
    this.maxsize = Math.max(1, maxsize);
    this.ttlMs = Math.max(0, ttlSeconds) * 1000;
  }

  get(title: string, lang: string): WikipediaArticle | undefined {
    const key = cacheKey(lang, title);
    const canonical = this.alias.get(key);
    if (!canonical) return undefined;
    const entry = this.entries.get(canonical);
    if (!entry) {
      this.alias.delete(key);
      return undefined;
    }
    if (entry.expiresAt <= Date.now()) {
      this.purge(canonical);
      return undefined;
    }
    this.touch(canonical);
    return entry.article;
  }

  put(article: WikipediaArticle, aliases: string[] = []): WikipediaArticle {
    const lang = article.lang;
    const canonical = cacheKey(lang, article.canonical_title);
    const aliasKeys = new Set<string>([canonical]);
    for (const title of [article.canonical_title, ...aliases]) {
      if (title && title.trim()) aliasKeys.add(cacheKey(lang, title));
    }
    const existing = this.entries.get(canonical);
    if (existing && existing.expiresAt > Date.now()) {
      for (const alias of aliasKeys) {
        existing.aliases.add(alias);
        this.alias.set(alias, canonical);
      }
      this.touch(canonical);
      return existing.article;
    }
    this.evictIfNeeded(canonical);
    const entry: CacheEntry = {
      article,
      expiresAt: Date.now() + this.ttlMs,
      aliases: aliasKeys,
    };
    this.entries.set(canonical, entry);
    if (!this.order.includes(canonical)) this.order.push(canonical);
    for (const alias of aliasKeys) this.alias.set(alias, canonical);
    return article;
  }

  private touch(canonical: string) {
    const idx = this.order.indexOf(canonical);
    if (idx >= 0) this.order.splice(idx, 1);
    this.order.push(canonical);
  }

  private purge(canonical: string) {
    const entry = this.entries.get(canonical);
    this.entries.delete(canonical);
    const idx = this.order.indexOf(canonical);
    if (idx >= 0) this.order.splice(idx, 1);
    if (!entry) return;
    for (const alias of entry.aliases) {
      if (this.alias.get(alias) === canonical) this.alias.delete(alias);
    }
  }

  private evictIfNeeded(incomingCanonical: string) {
    while (this.order.length >= this.maxsize && this.order[0] !== incomingCanonical) {
      this.purge(this.order[0]);
    }
  }

  /**
   * Same-key cache misses single-flight via a Map of in-flight Promises
   * (parity with Python WikipediaExtractCache._inflight). Distinct keys load
   * concurrently. The loader runs without blocking other keys.
   */
  async getOrLoad(
    title: string,
    lang: string,
    loader: () => Promise<{ article: WikipediaArticle; aliases?: string[] }>,
  ): Promise<WikipediaArticle> {
    const hit = this.get(title, lang);
    if (hit) return hit;
    const key = cacheKey(lang, title);
    const existing = this.inflight.get(key);
    if (existing) return existing;
    const pending = (async () => {
      const { article, aliases = [] } = await loader();
      return this.put(article, aliases);
    })();
    this.inflight.set(key, pending);
    try {
      return await pending;
    } finally {
      if (this.inflight.get(key) === pending) this.inflight.delete(key);
    }
  }
}

const cache = new WikipediaExtractCache();

export async function loadWikipediaArticle(
  title: string,
  lang = DEFAULT_LANG,
): Promise<WikipediaArticle> {
  const cleaned = title.trim();
  const safeLang = normalizeLang(lang);
  if (!cleaned) {
    throw new WikipediaFetchError(`no ${safeLang}.wikipedia.org article named '${title}'`);
  }
  return await cache.getOrLoad(cleaned, safeLang, async () => {
    const payload = await fetchExtractPayload(cleaned, safeLang);
    const article = parseExtractPayload(payload, cleaned, safeLang);
    return {
      article,
      aliases: [cleaned, article.canonical_title, ...redirectAliases(payload)],
    };
  });
}

export async function fetchWikipediaArticle(title: string, lang = DEFAULT_LANG) {
  const article = await loadWikipediaArticle(title, lang);
  return articleResult(article);
}

export async function fetchWikipediaSection(
  title: string,
  heading = "",
  lang = DEFAULT_LANG,
) {
  const article = await loadWikipediaArticle(title, lang);
  const sections = articleSections(article);
  if (!heading.trim()) {
    return sections.map((section) => ({ heading: section.title }));
  }
  const match = lookupHeading(heading, sections, true);
  if (!match) return [];
  return [
    {
      heading: match.title,
      heading_markdown: `${"#".repeat(match.level)} ${match.title}`,
      body: match.body,
    },
  ];
}
