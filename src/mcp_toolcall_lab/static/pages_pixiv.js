/*
 * Browser-side pixiv Encyclopedia client for the published GitHub Pages
 * #pixiv panel. This is the History -> Source -> Extract workflow, not a
 * search box: dic.pixiv.net does not send permissive CORS headers, so a
 * static host like Pages can never fetch it directly (that dead end is
 * retired -- see tests/test_pages_pixiv.py's retirement regression tests).
 *
 * Instead this panel generates/validates the real dic.pixiv.net URLs
 * (article, history, revision-source) so a visitor can open them in a new
 * tab, then normalizes source text pasted back from Pixiv's own
 * "原文表示" (view source) page into a stable structured Extract
 * (title / reading / overview / headings / body). Everything below runs
 * against the pasted text only -- there is no network fetch of any kind.
 *
 * Vanilla JS, no build step. write_pages() copies this file next to
 * index.html as pages-pixiv.js. The exported functions are the contracts
 * tests/test_pages_pixiv.py exercises via node.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.mcpToolcallLabPagesPixiv = api;
    if (typeof document !== "undefined") {
      api.autoMount(root);
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var PIXIV_ORIGIN = "https://dic.pixiv.net";
  var MISSING_TITLE = "Enter a Pixiv Encyclopedia title.";
  var MISSING_SOURCE = "Paste the source text from Pixiv's 原文表示 (view source) page.";
  var INVALID_SOURCE_URL =
    "That does not look like a dic.pixiv.net revision-source URL. Expected " +
    "https://dic.pixiv.net/history/<title>/<numeric revision id>/source.";
  var EMPTY_SOURCE = "Pasted Pixiv source had no readable text.";

  var HISTORY_SOURCE_RE = /^https:\/\/dic\.pixiv\.net\/history\/([^/?#]+)\/(\d+)\/source\/?(?:[?#].*)?$/i;

  function buildArticleUrl(title) {
    var cleaned = String(title || "").trim();
    if (!cleaned) {
      return "";
    }
    return PIXIV_ORIGIN + "/a/" + encodeURIComponent(cleaned);
  }

  function buildHistoryUrl(title) {
    var cleaned = String(title || "").trim();
    if (!cleaned) {
      return "";
    }
    return PIXIV_ORIGIN + "/history/" + encodeURIComponent(cleaned);
  }

  function buildSourceUrl(title, revisionId) {
    var cleaned = String(title || "").trim();
    var revision = String(revisionId || "").trim();
    if (!cleaned || !/^[0-9]+$/.test(revision)) {
      return "";
    }
    return PIXIV_ORIGIN + "/history/" + encodeURIComponent(cleaned) + "/" + revision + "/source";
  }

  // Recognizes/validates a pasted revision-source URL: numeric revision id
  // required, non-pixiv or malformed URLs rejected, title may be raw Unicode
  // or percent-encoded (both are valid in a browser-copied URL).
  function parseSourceUrl(url) {
    var raw = String(url || "").trim();
    if (!raw) {
      return { ok: false, error: INVALID_SOURCE_URL };
    }
    var match = HISTORY_SOURCE_RE.exec(raw);
    if (!match) {
      return { ok: false, error: INVALID_SOURCE_URL };
    }
    var title;
    try {
      title = decodeURIComponent(match[1]);
    } catch (err) {
      return { ok: false, error: INVALID_SOURCE_URL };
    }
    if (!title.trim()) {
      return { ok: false, error: INVALID_SOURCE_URL };
    }
    return { ok: true, title: title, revisionId: match[2], url: raw };
  }

  // Curl-like plain text, not a reproduction of pixiv's page layout.
  function stripHtml(rawHtml) {
    var text = String(rawHtml || "")
      .replace(/<script[\s\S]*?<\/script>/gi, "")
      .replace(/<style[\s\S]*?<\/style>/gi, "")
      .replace(/<[^>]+>/g, "\n")
      .replace(/&nbsp;/gi, " ")
      .replace(/&amp;/gi, "&")
      .replace(/&lt;/gi, "<")
      .replace(/&gt;/gi, ">")
      .replace(/&quot;/gi, '"')
      .replace(/&#0*39;/gi, "'");
    return text
      .split(/\r?\n+/)
      .map(function (line) {
        return line.replace(/[ \t]+/g, " ").trim();
      })
      .filter(function (line) {
        return line.length > 0;
      })
      .join("\n");
  }

  function normalizeLines(text) {
    return stripHtml(text).split("\n").filter(function (line) {
      return line.length > 0;
    });
  }

  // Mirrors the h1..h6 -> "#".repeat(level) heading model
  // pixiv_dictionary_tool.py gets from vendor/markdown.py's html_to_markdown(),
  // so Extract owns pixiv-specific acquisition rather than a second rich
  // Markdown parser. Keeps each match's position so extractSource() can find
  // the prose lying between a heading and the next one.
  function scanHeadings(rawHtml) {
    var matches = [];
    var re = /<h([1-6])[^>]*>([\s\S]*?)<\/h\1>/gi;
    var match;
    while ((match = re.exec(String(rawHtml || "")))) {
      var text = stripHtml(match[2]).replace(/\s+/g, " ").trim();
      if (text) {
        matches.push({
          level: Number(match[1]),
          heading: text,
          start: match.index,
          end: match.index + match[0].length,
        });
      }
    }
    return matches;
  }

  function extractHeadingsFromHtml(rawHtml) {
    return scanHeadings(rawHtml).map(function (m) {
      return { level: m.level, heading: m.heading };
    });
  }

  // Pixiv Encyclopedia article titles are frequently followed by a
  // parenthesized reading, e.g. "テスト記事（てすときじ）".
  function splitReading(line) {
    var cleaned = String(line || "").trim();
    var match = /^(.*?)[(（]([^()（）]+)[)）]\s*$/.exec(cleaned);
    if (!match) {
      return { title: cleaned, reading: "" };
    }
    var title = match[1].trim();
    var reading = match[2].trim();
    if (!title || !reading) {
      return { title: cleaned, reading: "" };
    }
    return { title: title, reading: reading };
  }

  // Normalizes pasted Pixiv source/plain text into a stable structured
  // extract: title / reading / overview / headings / body. HTML input (the
  // 原文表示 page's markup, or a saved copy of it) yields real headings via
  // its own <h1>..<h6> tags, the same signal html_to_markdown() reads
  // server-side. Plain pasted text has no reliable heading markers, so it
  // only yields title/reading/body -- headings/overview stay empty rather
  // than guessing at pixiv's undocumented wiki markup.
  function extractSource(input) {
    input = input || {};
    var rawText = String(input.sourceText || "").trim();
    if (!rawText) {
      return { ok: false, error: MISSING_SOURCE };
    }

    var looksHtml = /<[a-z!][\s\S]*>/i.test(rawText);
    var matches = looksHtml ? scanHeadings(rawText) : [];
    var headings = matches.map(function (m) {
      return { level: m.level, heading: m.heading };
    });
    var bodyLines = looksHtml ? normalizeLines(rawText) : normalizeLines(rawText.replace(/[ \t]+/g, " "));
    if (!bodyLines.length) {
      return { ok: false, error: EMPTY_SOURCE };
    }

    var titleMatch = matches.length ? matches[0] : null;
    var split = splitReading((titleMatch && titleMatch.heading) || bodyLines[0]);
    var title = String(input.title || "").trim() || split.title;
    var reading = split.reading;

    // Overview is the prose lying between the title heading and whatever
    // heading comes next (or end of input) -- not a rendered-layout scrape.
    var overview = "";
    if (titleMatch) {
      var nextStart = matches.length > 1 ? matches[1].start : rawText.length;
      overview = normalizeLines(rawText.slice(titleMatch.end, nextStart)).join(" ").trim();
    }

    return {
      ok: true,
      title: title,
      reading: reading,
      overview: overview,
      headings: headings,
      body: bodyLines.join("\n"),
    };
  }

  function qs(node, testid) {
    if (!node || typeof node.querySelector !== "function") {
      return null;
    }
    return node.querySelector('[data-testid="' + testid + '"]');
  }

  function setHidden(el, hidden) {
    if (!el) {
      return;
    }
    if (hidden) {
      el.setAttribute("hidden", "");
    } else {
      el.removeAttribute("hidden");
    }
  }

  function setText(el, text) {
    if (!el) {
      return;
    }
    if ("textContent" in el) {
      el.textContent = text;
    } else {
      el.innerHTML = String(text);
    }
  }

  function formatExtract(result) {
    var lines = ["Title: " + result.title];
    if (result.reading) {
      lines.push("Reading: " + result.reading);
    }
    if (result.revisionId) {
      lines.push("Revision: " + result.revisionId);
    }
    if (result.overview) {
      lines.push("", "Overview:", result.overview);
    }
    if (result.headings && result.headings.length) {
      lines.push("", "Headings:");
      result.headings.forEach(function (heading) {
        lines.push("  ".repeat(Math.max(0, heading.level - 1)) + "- " + heading.heading);
      });
    }
    lines.push("", "Body:", result.body);
    return lines.join("\n");
  }

  function renderError(els, message) {
    setText(els.status, "");
    setText(els.error, message || MISSING_TITLE);
    setHidden(els.error, false);
    setHidden(els.output, true);
  }

  function render(els, result) {
    if (!result || !result.ok) {
      renderError(els, result && result.error);
      return;
    }
    setText(els.error, "");
    setHidden(els.error, true);
    setText(els.status, "Extracted locally from pasted source -- no network request was made.");
    setText(els.output, formatExtract(result));
    setHidden(els.output, false);
  }

  function mount(root, options) {
    options = options || {};
    if (!root) {
      return null;
    }
    var els = {
      title: qs(root, "pages-pixiv-title"),
      openArticle: qs(root, "pages-pixiv-open-article"),
      openHistory: qs(root, "pages-pixiv-open-history"),
      form: qs(root, "pages-pixiv-source-form"),
      sourceUrl: qs(root, "pages-pixiv-source-url"),
      sourceText: qs(root, "pages-pixiv-source-text"),
      status: qs(root, "pages-pixiv-status"),
      error: qs(root, "pages-pixiv-error"),
      output: qs(root, "pages-pixiv-extract"),
    };
    if (!els.form) {
      return null;
    }
    var win = options.window || (typeof window !== "undefined" ? window : null);

    function currentTitle() {
      return String((els.title && els.title.value) || "").trim();
    }

    function openInNewTab(url) {
      if (!url) {
        renderError(els, MISSING_TITLE);
        return;
      }
      if (win && typeof win.open === "function") {
        win.open(url, "_blank", "noopener");
      }
    }

    if (els.openArticle) {
      els.openArticle.addEventListener("click", function () {
        openInNewTab(buildArticleUrl(currentTitle()));
      });
    }
    if (els.openHistory) {
      els.openHistory.addEventListener("click", function () {
        openInNewTab(buildHistoryUrl(currentTitle()));
      });
    }

    els.form.addEventListener("submit", function (event) {
      if (event && typeof event.preventDefault === "function") {
        event.preventDefault();
      }
      var sourceUrlValue = String((els.sourceUrl && els.sourceUrl.value) || "").trim();
      var meta = { title: currentTitle(), revisionId: "" };
      if (sourceUrlValue) {
        var parsed = parseSourceUrl(sourceUrlValue);
        if (!parsed.ok) {
          renderError(els, parsed.error);
          return;
        }
        meta.title = parsed.title;
        meta.revisionId = parsed.revisionId;
      }
      var result = extractSource({
        title: meta.title,
        sourceText: (els.sourceText && els.sourceText.value) || "",
      });
      if (result.ok && meta.revisionId) {
        result.revisionId = meta.revisionId;
      }
      render(els, result);
    });

    return {
      extract: extractSource,
      parseSourceUrl: parseSourceUrl,
      render: render.bind(null, els),
    };
  }

  function autoMount(win) {
    if (!win || !win.document) {
      return null;
    }
    var doc = win.document;
    var root = doc.querySelector('[data-testid="pages-pixiv-app"]');
    if (!root) {
      return null;
    }
    var start = function () {
      return mount(root, { window: win });
    };
    if (doc.readyState === "loading" && typeof doc.addEventListener === "function") {
      doc.addEventListener("DOMContentLoaded", start);
      return null;
    }
    return start();
  }

  return {
    MISSING_TITLE: MISSING_TITLE,
    MISSING_SOURCE: MISSING_SOURCE,
    INVALID_SOURCE_URL: INVALID_SOURCE_URL,
    EMPTY_SOURCE: EMPTY_SOURCE,
    buildArticleUrl: buildArticleUrl,
    buildHistoryUrl: buildHistoryUrl,
    buildSourceUrl: buildSourceUrl,
    parseSourceUrl: parseSourceUrl,
    stripHtml: stripHtml,
    extractHeadingsFromHtml: extractHeadingsFromHtml,
    extractSource: extractSource,
    mount: mount,
    autoMount: autoMount,
  };
});
