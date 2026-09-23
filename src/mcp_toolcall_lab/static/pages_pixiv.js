/*
 * Browser-side pixiv Encyclopedia client for the published GitHub Pages
 * #pixiv panel. Fetches the real https://dic.pixiv.net/a/{title} directly
 * from the browser -- the same upstream URL pixiv_dictionary_tool.py uses,
 * just executed client-side, mirroring how pages_wiki.js talks to the
 * MediaWiki API. Not an MCP tool call.
 *
 * Unlike MediaWiki's action API (origin=*), dic.pixiv.net is not known to
 * send permissive CORS headers, so a blocked fetch is a real possibility on
 * a static host. On failure this panel says so explicitly and prints the
 * local/MCP fallback command -- it never presents 127.0.0.1 as if this page
 * could reach it.
 *
 * Vanilla JS, no build step. write_pages() copies this file next to
 * index.html as pages-pixiv.js. buildArticleUrl / stripHtml / loadArticle
 * are the contracts tests/test_pages_pixiv.py exercises via node.
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

  var MISSING_TITLE = "Enter a Pixiv Encyclopedia title.";
  var NO_FETCH_ERROR = "this browser has no fetch() available.";
  var STATIC_HOST_NOTE =
    "GitHub Pages is a static host with no server-side proxy; this browser " +
    "request to dic.pixiv.net failed (likely blocked by CORS, since that " +
    "site does not opt in to cross-origin requests the way Wikipedia does).";

  function buildArticleUrl(title) {
    var cleaned = String(title || "").trim();
    if (!cleaned) {
      return "";
    }
    return "https://dic.pixiv.net/a/" + encodeURIComponent(cleaned);
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

  function loadArticle(title, fetchImpl) {
    var cleaned = String(title || "").trim();
    if (!cleaned) {
      return Promise.resolve({ ok: false, error: MISSING_TITLE });
    }
    var url = buildArticleUrl(cleaned);
    var doFetch = fetchImpl || (typeof fetch === "function" ? fetch : null);
    if (!doFetch) {
      return Promise.resolve({ ok: false, error: NO_FETCH_ERROR, url: url, staticHost: true });
    }
    return Promise.resolve()
      .then(function () {
        return doFetch(url, { mode: "cors" });
      })
      .then(function (response) {
        if (!response || response.ok === false) {
          var status = response && response.status != null ? String(response.status) : "network";
          throw new Error("HTTP " + status);
        }
        return response.text();
      })
      .then(function (rawHtml) {
        var text = stripHtml(rawHtml);
        if (!text) {
          return {
            ok: false,
            error: "pixiv Encyclopedia article '" + cleaned + "' returned no readable text",
            url: url,
          };
        }
        return { ok: true, title: cleaned, text: text, url: url };
      })
      .catch(function (err) {
        var detail = err && err.message ? err.message : String(err);
        return {
          ok: false,
          error: STATIC_HOST_NOTE + " (" + detail + ")",
          url: url,
          staticHost: true,
        };
      });
  }

  function qs(root, testid) {
    if (!root || typeof root.querySelector !== "function") {
      return null;
    }
    return root.querySelector('[data-testid="' + testid + '"]');
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

  function render(els, result) {
    if (!result || !result.ok) {
      setText(els.status, "");
      setText(els.error, (result && result.error) || MISSING_TITLE);
      setHidden(els.error, false);
      setHidden(els.output, true);
      setHidden(els.fallback, !(result && result.staticHost));
      return;
    }
    setText(els.error, "");
    setHidden(els.error, true);
    setHidden(els.fallback, true);
    setText(els.status, "Fetched " + result.url + " directly from your browser (no MCP call).");
    setText(els.output, result.text);
    setHidden(els.output, false);
  }

  function mount(root, options) {
    options = options || {};
    if (!root) {
      return null;
    }
    var form = qs(root, "pages-pixiv-form");
    if (!form) {
      return null;
    }
    var els = {
      input: qs(root, "pages-pixiv-title"),
      status: qs(root, "pages-pixiv-status"),
      error: qs(root, "pages-pixiv-error"),
      output: qs(root, "pages-pixiv-extract"),
      fallback: qs(root, "pages-pixiv-fallback"),
    };
    var doFetch = options.fetch;

    form.addEventListener("submit", function (event) {
      if (event && typeof event.preventDefault === "function") {
        event.preventDefault();
      }
      var title = String((els.input && els.input.value) || "").trim();
      if (!title) {
        render(els, { ok: false, error: MISSING_TITLE });
        return;
      }
      setText(els.status, "Fetching " + buildArticleUrl(title) + " …");
      setHidden(els.error, true);
      setHidden(els.output, true);
      setHidden(els.fallback, true);
      loadArticle(title, doFetch).then(function (result) {
        render(els, result);
      });
    });

    return { load: loadArticle, render: render.bind(null, els) };
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
      return mount(root, { fetch: win.fetch });
    };
    if (doc.readyState === "loading" && typeof doc.addEventListener === "function") {
      doc.addEventListener("DOMContentLoaded", start);
      return null;
    }
    return start();
  }

  return {
    MISSING_TITLE: MISSING_TITLE,
    NO_FETCH_ERROR: NO_FETCH_ERROR,
    STATIC_HOST_NOTE: STATIC_HOST_NOTE,
    buildArticleUrl: buildArticleUrl,
    stripHtml: stripHtml,
    loadArticle: loadArticle,
    mount: mount,
    autoMount: autoMount,
  };
});
