import {
  WikipediaExtractCache,
  WikipediaFetchError,
  assertSafeWikipediaApiUrl,
  loadWikipediaArticle,
  wikipediaApiUrl,
} from "./wikipedia.ts";

function assertEquals(actual: unknown, expected: unknown) {
  const same = actual === expected || JSON.stringify(actual) === JSON.stringify(expected);
  if (!same) {
    throw new Error(`Expected ${Deno.inspect(expected)}, got ${Deno.inspect(actual)}`);
  }
}

function assertThrows(fn: () => unknown, errorClass: new (...args: never[]) => Error, messageIncludes?: string) {
  try {
    fn();
  } catch (err) {
    if (!(err instanceof errorClass)) {
      throw new Error(`Expected ${errorClass.name}, got ${err}`);
    }
    if (messageIncludes && !String((err as Error).message).includes(messageIncludes)) {
      throw new Error(`Expected message to include ${JSON.stringify(messageIncludes)}, got ${JSON.stringify((err as Error).message)}`);
    }
    return;
  }
  throw new Error(`Expected ${errorClass.name} to be thrown`);
}

async function assertRejects(
  fn: () => Promise<unknown>,
  errorClass: new (...args: never[]) => Error,
  messageIncludes?: string,
) {
  try {
    await fn();
  } catch (err) {
    if (!(err instanceof errorClass)) {
      throw new Error(`Expected ${errorClass.name}, got ${err}`);
    }
    if (messageIncludes && !String((err as Error).message).includes(messageIncludes)) {
      throw new Error(`Expected message to include ${JSON.stringify(messageIncludes)}, got ${JSON.stringify((err as Error).message)}`);
    }
    return;
  }
  throw new Error(`Expected ${errorClass.name} to be rejected`);
}

const REJECT_LANGS = ["evil.com/", "evil.com", "#", "@", ".", "..", "/", "x", "en.wikipedia.org", ""];

Deno.test("wikipediaApiUrl accepts MediaWiki-like lang codes", () => {
  for (const lang of ["en", "EN", "zh", "zh-yue", "simple", "cbk-zam"]) {
    const url = wikipediaApiUrl(lang);
    assertEquals(url.protocol, "https:");
    assertEquals(url.hostname.endsWith(".wikipedia.org"), true);
    assertEquals(url.pathname, "/w/api.php");
  }
  assertEquals(wikipediaApiUrl("en").hostname, "en.wikipedia.org");
  assertEquals(wikipediaApiUrl("zh-yue").hostname, "zh-yue.wikipedia.org");
});

Deno.test("wikipediaApiUrl rejects host-injection lang before fetch", () => {
  for (const lang of REJECT_LANGS) {
    assertThrows(
      () => wikipediaApiUrl(lang),
      WikipediaFetchError,
      "invalid wikipedia language code",
    );
  }
});

Deno.test("assertSafeWikipediaApiUrl rejects open redirect / injected hosts", () => {
  const bad = [
    "https://evil.com/.wikipedia.org/w/api.php",
    "https://en.wikipedia.org.evil.com/w/api.php",
    "https://notwikipedia.org/w/api.php",
    "https://wikipedia.org/w/api.php",
    "http://en.wikipedia.org/w/api.php",
    "https://user@en.wikipedia.org/w/api.php",
    "https://upload.wikimedia.org/w/api.php",
  ];
  for (const href of bad) {
    assertThrows(() => assertSafeWikipediaApiUrl(new URL(href)), WikipediaFetchError);
  }
  assertSafeWikipediaApiUrl(new URL("https://en.wikipedia.org/w/api.php"));
});

Deno.test("loadWikipediaArticle rejects injected lang without calling fetch", async () => {
  const originalFetch = globalThis.fetch;
  let fetches = 0;
  globalThis.fetch = ((..._args: Parameters<typeof fetch>) => {
    fetches += 1;
    throw new Error("fetch must not run for invalid lang");
  }) as typeof fetch;
  try {
    for (const lang of ["evil.com/", "evil.com", "#", "@"]) {
      await assertRejects(
        () => loadWikipediaArticle("Yokohama", lang),
        WikipediaFetchError,
        "invalid wikipedia language code",
      );
    }
    assertEquals(fetches, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

Deno.test("getOrLoad single-flights the same cache key", async () => {
  const cache = new WikipediaExtractCache();
  let loads = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const loader = async () => {
    loads += 1;
    await gate;
    return {
      article: { canonical_title: "Yokohama", lang: "en", extract: "lead" },
      aliases: ["Yokohama"],
    };
  };
  const first = cache.getOrLoad("Yokohama", "en", loader);
  const second = cache.getOrLoad("Yokohama", "en", loader);
  release();
  const [a, b] = await Promise.all([first, second]);
  assertEquals(loads, 1);
  assertEquals(a.extract, "lead");
  assertEquals(b.extract, "lead");
});

Deno.test("getOrLoad lets distinct keys overlap", async () => {
  const cache = new WikipediaExtractCache();
  let inFlight = 0;
  let maxInFlight = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const loader = (title: string) =>
    async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await gate;
      inFlight -= 1;
      return {
        article: { canonical_title: title, lang: "en", extract: title },
        aliases: [title],
      };
    };
  const tokyo = cache.getOrLoad("Tokyo", "en", loader("Tokyo"));
  const osaka = cache.getOrLoad("Osaka", "en", loader("Osaka"));
  assertEquals(maxInFlight, 2);
  release();
  const articles = await Promise.all([tokyo, osaka]);
  assertEquals(articles.map((row) => row.canonical_title).sort(), ["Osaka", "Tokyo"]);
});
