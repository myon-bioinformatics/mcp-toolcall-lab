/*
 * Browser-side MediaWiki extract client for the published GitHub Pages
 * #wiki panel. Fetches https://{lang}.wikipedia.org/w/api.php with
 * origin=* (CORS). Not an MCP tool call, and not the local GET /wiki
 * stub form.
 *
 * Vanilla JS, no build step. write_pages() copies this file next to
 * index.html as pages-wiki.js. parseWikiSections / sectionView /
 * parseExtractPayload / buildApiUrl / loadArticle are the contracts
 * tests/test_pages_wiki.py exercises via node.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.mcpToolcallLabPagesWiki = api;
    if (typeof document !== "undefined") {
      api.autoMount(root);
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var DEFAULT_LANG = "en";
  var DEFAULT_TITLE = "Yokohama";
  var HEADING_RE = /^(=+)\s*(.+?)\s*=+\s*$/;
  var LANG_RE = /^[a-z]{2,12}(?:-[a-z0-9]+)*$/i;
  var MISSING_TITLE = "Enter a Wikipedia title.";

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normalizeLang(lang) {
    var cleaned = String(lang || "").trim();
    if (!cleaned) {
      return DEFAULT_LANG;
    }
    if (!LANG_RE.test(cleaned)) {
      return "";
    }
    return cleaned.toLowerCase();
  }

  function buildApiUrl(title, lang) {
    var safeLang = normalizeLang(lang);
    if (!safeLang) {
      return "";
    }
    var cleaned = String(title || "").trim();
    if (!cleaned) {
      return "";
    }
    var query =
      "action=query&prop=extracts&explaintext=1&exsectionformat=wiki" +
      "&redirects=1&format=json&origin=*&titles=" +
      encodeURIComponent(cleaned);
    return "https://" + safeLang + ".wikipedia.org/w/api.php?" + query;
  }

  function parseWikiSections(extract) {
    var lines = String(extract || "").split(/\r?\n/);
    var found = [];
    var i;
    for (i = 0; i < lines.length; i++) {
      var match = lines[i].match(HEADING_RE);
      if (match) {
        found.push({ index: i, level: match[1].length, title: match[2] });
      }
    }
    var sections = [];
    for (i = 0; i < found.length; i++) {
      var start = found[i].index;
      var end = i + 1 < found.length ? found[i + 1].index : lines.length;
      sections.push({
        title: found[i].title,
        level: found[i].level,
        body: lines.slice(start + 1, end).join("\n").replace(/^\s+|\s+$/g, ""),
      });
    }
    return sections;
  }

  function findSection(sections, heading) {
    var needle = String(heading || "").trim();
    if (!needle) {
      return null;
    }
    var folded = needle.toLowerCase();
    var i;
    var section;
    for (i = 0; i < sections.length; i++) {
      section = sections[i];
      if (section.title === needle || section.title.toLowerCase() === folded) {
        return section;
      }
    }
    for (i = 0; i < sections.length; i++) {
      section = sections[i];
      var title = section.title.toLowerCase();
      if (title.indexOf(folded) === 0 || folded.indexOf(title) === 0 || title.indexOf(folded) !== -1) {
        return section;
      }
    }
    return null;
  }

  function sectionView(extract, heading) {
    var text = String(extract || "");
    if (!String(heading || "").trim()) {
      return text;
    }
    var match = findSection(parseWikiSections(text), heading);
    return match ? match.body : text;
  }

  function noSuchArticle(title, lang) {
    return "no " + lang + ".wikipedia.org article named '" + title + "'";
  }

  function parseExtractPayload(payload, title, lang) {
    var safeLang = normalizeLang(lang) || DEFAULT_LANG;
    var cleaned = String(title || "").trim();
    if (!payload || typeof payload !== "object") {
      return { ok: false, error: "could not fetch '" + cleaned + "' from " + safeLang + ".wikipedia.org: bad JSON" };
    }
    var pages = (payload.query && payload.query.pages) || {};
    var keys = Object.keys(pages);
    if (!keys.length) {
      return { ok: false, error: noSuchArticle(cleaned, safeLang) };
    }
    var page = pages[keys[0]];
    if (!page || typeof page !== "object" || Object.prototype.hasOwnProperty.call(page, "missing")) {
      return { ok: false, error: noSuchArticle(cleaned, safeLang) };
    }
    var extract = page.extract == null ? "" : String(page.extract);
    return {
      ok: true,
      canonical_title: String(page.title || cleaned),
      lang: safeLang,
      extract: extract,
      sections: parseWikiSections(extract),
    };
  }

  function parseWikiHash(hash) {
    var raw = String(hash || "");
    if (raw.charAt(0) === "#") {
      raw = raw.slice(1);
    }
    var q = raw.indexOf("?");
    var query = q === -1 ? "" : raw.slice(q + 1);
    var title = "";
    var lang = "";
    var heading = "";
    if (query && typeof URLSearchParams === "function") {
      var params = new URLSearchParams(query);
      title = params.get("title") || "";
      lang = params.get("lang") || "";
      heading = params.get("heading") || "";
    } else if (query) {
      var parts = query.split("&");
      for (var i = 0; i < parts.length; i++) {
        var pair = parts[i].split("=");
        var key = decodeURIComponent((pair[0] || "").replace(/\+/g, " "));
        var value = decodeURIComponent((pair[1] || "").replace(/\+/g, " "));
        if (key === "title") title = value;
        if (key === "lang") lang = value;
        if (key === "heading") heading = value;
      }
    }
    return { title: title, lang: lang, heading: heading };
  }

  function wikiHash(query) {
    query = query || {};
    var parts = [];
    if (query.title) {
      parts.push("title=" + encodeURIComponent(query.title));
    }
    if (query.lang) {
      parts.push("lang=" + encodeURIComponent(query.lang));
    }
    if (query.heading) {
      parts.push("heading=" + encodeURIComponent(query.heading));
    }
    return parts.length ? "#wiki?" + parts.join("&") : "#wiki";
  }

  function viewState(article, heading) {
    if (!article || !article.ok) {
      return { body: "", headings: [], canonical_title: "", error: article && article.error };
    }
    return {
      body: sectionView(article.extract, heading),
      headings: (article.sections || parseWikiSections(article.extract)).map(function (section) {
        return section.title;
      }),
      canonical_title: article.canonical_title,
      error: "",
    };
  }

  function loadArticle(title, lang, fetchImpl) {
    var cleaned = String(title || "").trim();
    if (!cleaned) {
      return Promise.resolve({ ok: false, error: MISSING_TITLE });
    }
    var safeLang = normalizeLang(lang);
    if (!safeLang) {
      return Promise.resolve({ ok: false, error: "Unsupported language code." });
    }
    var url = buildApiUrl(cleaned, safeLang);
    var doFetch = fetchImpl || (typeof fetch === "function" ? fetch : null);
    if (!doFetch) {
      return Promise.resolve({
        ok: false,
        error: "could not fetch '" + cleaned + "' from " + safeLang + ".wikipedia.org: fetch is unavailable",
      });
    }
    return Promise.resolve()
      .then(function () {
        return doFetch(url);
      })
      .then(function (response) {
        if (!response || response.ok === false) {
          var status = response && response.status != null ? String(response.status) : "network";
          throw new Error("HTTP " + status);
        }
        if (typeof response.json === "function") {
          return response.json();
        }
        return response;
      })
      .then(function (payload) {
        return parseExtractPayload(payload, cleaned, safeLang);
      })
      .catch(function (err) {
        var detail = err && err.message ? err.message : String(err);
        return {
          ok: false,
          error: "could not fetch '" + cleaned + "' from " + safeLang + ".wikipedia.org: " + detail,
        };
      });
  }

  function createSession(fetchImpl) {
    var article = null;
    var heading = "";
    var fetches = 0;
    return {
      fetches: function () {
        return fetches;
      },
      article: function () {
        return article;
      },
      heading: function () {
        return heading;
      },
      load: function (title, lang) {
        fetches += 1;
        heading = "";
        return loadArticle(title, lang, fetchImpl).then(function (result) {
          article = result.ok ? result : null;
          return result;
        });
      },
      selectHeading: function (nextHeading) {
        heading = String(nextHeading || "");
        return viewState(article, heading);
      },
      view: function () {
        return viewState(article, heading);
      },
    };
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
      el.innerHTML = escapeHtml(text);
    }
  }

  function fillHeadings(select, headings, selected) {
    if (!select) {
      return;
    }
    select.innerHTML = "";
    var full = select.ownerDocument.createElement("option");
    full.value = "";
    full.textContent = "(full extract)";
    select.appendChild(full);
    for (var i = 0; i < headings.length; i++) {
      var option = select.ownerDocument.createElement("option");
      option.value = headings[i];
      option.textContent = headings[i];
      if (headings[i] === selected) {
        option.selected = true;
      }
      select.appendChild(option);
    }
    if (!selected) {
      full.selected = true;
    }
  }

  function ensureLangOption(select, lang) {
    if (!select || !lang) {
      return;
    }
    var options = select.options || [];
    for (var i = 0; i < options.length; i++) {
      if (options[i].value === lang) {
        select.value = lang;
        return;
      }
    }
    var option = select.ownerDocument.createElement("option");
    option.value = lang;
    option.textContent = lang;
    select.appendChild(option);
    select.value = lang;
  }

  function qs(root, testid) {
    if (!root || typeof root.querySelector !== "function") {
      return null;
    }
    return root.querySelector('[data-testid="' + testid + '"]');
  }

  function mount(root, options) {
    options = options || {};
    if (!root) {
      return null;
    }
    var loc = options.location || {};
    var hist = options.history;
    var session = createSession(options.fetch);
    var titleInput = qs(root, "pages-wiki-title");
    var langInput = qs(root, "pages-wiki-lang");
    var headingSelect = qs(root, "pages-wiki-heading");
    var form = qs(root, "pages-wiki-form");
    var errorEl = qs(root, "pages-wiki-error");
    var canonicalEl = qs(root, "pages-wiki-canonical");
    var extractEl = qs(root, "pages-wiki-extract");
    var noteEl = qs(root, "pages-wiki-extract-note");

    function syncHash(title, lang, heading) {
      if (!hist || typeof hist.replaceState !== "function" || !loc.pathname) {
        return;
      }
      var search = loc.search || "";
      try {
        hist.replaceState(null, "", loc.pathname + search + wikiHash({ title: title, lang: lang, heading: heading }));
      } catch (err) {
        return;
      }
    }

    function render(result, heading) {
      if (!result || !result.ok) {
        setText(errorEl, result && result.error ? result.error : MISSING_TITLE);
        setHidden(errorEl, false);
        setHidden(canonicalEl, true);
        setHidden(extractEl, true);
        setHidden(noteEl, true);
        fillHeadings(headingSelect, [], "");
        return;
      }
      var view = viewState(result, heading);
      setText(errorEl, "");
      setHidden(errorEl, true);
      if (canonicalEl) {
        setText(canonicalEl, "canonical title " + view.canonical_title);
        setHidden(canonicalEl, !view.canonical_title);
      }
      setHidden(noteEl, false);
      setText(extractEl, view.body);
      setHidden(extractEl, false);
      fillHeadings(headingSelect, view.headings, heading || "");
    }

    function currentTitle() {
      return titleInput ? String(titleInput.value || "").trim() : "";
    }

    function currentLang() {
      return langInput ? String(langInput.value || "").trim() : DEFAULT_LANG;
    }

    function onFetch() {
      var title = currentTitle();
      var lang = currentLang();
      if (!title) {
        render({ ok: false, error: MISSING_TITLE }, "");
        return Promise.resolve({ ok: false, error: MISSING_TITLE });
      }
      return session.load(title, lang).then(function (result) {
        render(result, "");
        if (result.ok) {
          syncHash(title, normalizeLang(lang) || DEFAULT_LANG, "");
        }
        return result;
      });
    }

    function onHeadingChange() {
      var heading = headingSelect ? headingSelect.value : "";
      var view = session.selectHeading(heading);
      if (!session.article()) {
        return view;
      }
      setText(extractEl, view.body);
      setHidden(extractEl, false);
      syncHash(currentTitle(), normalizeLang(currentLang()) || DEFAULT_LANG, heading);
      return view;
    }

    if (form && typeof form.addEventListener === "function") {
      form.addEventListener("submit", function (event) {
        if (event && typeof event.preventDefault === "function") {
          event.preventDefault();
        }
        onFetch();
      });
    }
    if (headingSelect && typeof headingSelect.addEventListener === "function") {
      headingSelect.addEventListener("change", onHeadingChange);
    }

    var fromHash = parseWikiHash(loc.hash || "");
    if (fromHash.title && titleInput) {
      titleInput.value = fromHash.title;
    }
    if (fromHash.lang && langInput) {
      ensureLangOption(langInput, normalizeLang(fromHash.lang) || fromHash.lang);
    }
    if (fromHash.title) {
      session.load(fromHash.title, fromHash.lang || currentLang()).then(function (result) {
        var heading = fromHash.heading || "";
        if (result.ok && heading) {
          session.selectHeading(heading);
        }
        render(result, heading);
      });
    }

    return {
      session: session,
      fetch: onFetch,
      selectHeading: onHeadingChange,
    };
  }

  function autoMount(win) {
    if (!win || !win.document) {
      return null;
    }
    var doc = win.document;
    var root = doc.querySelector('[data-testid="pages-wiki-app"]');
    if (!root) {
      return null;
    }
    var start = function () {
      return mount(root, { location: win.location, history: win.history, fetch: win.fetch });
    };
    if (doc.readyState === "loading" && typeof doc.addEventListener === "function") {
      doc.addEventListener("DOMContentLoaded", start);
      return null;
    }
    return start();
  }

  return {
    DEFAULT_LANG: DEFAULT_LANG,
    DEFAULT_TITLE: DEFAULT_TITLE,
    MISSING_TITLE: MISSING_TITLE,
    escapeHtml: escapeHtml,
    normalizeLang: normalizeLang,
    buildApiUrl: buildApiUrl,
    parseWikiSections: parseWikiSections,
    findSection: findSection,
    sectionView: sectionView,
    parseExtractPayload: parseExtractPayload,
    parseWikiHash: parseWikiHash,
    wikiHash: wikiHash,
    viewState: viewState,
    loadArticle: loadArticle,
    createSession: createSession,
    mount: mount,
    autoMount: autoMount,
  };
});
