/*
 * Browser-side PokéAPI client for the published GitHub Pages #pokemon panel.
 * Two ways in, one result (the same rule as adapters/pokeapi.py): Fetch asks
 * https://pokeapi.co/api/v2/pokemon/<name> directly from the browser, and
 * Extract reads JSON you pasted from the browser's own view-source / raw tab
 * of that URL (no network). When the direct fetch is blocked or fails, the
 * paste path still works. Both go through parse(), and toMarkdown() writes the
 * same Markdown tables as the Python adapter (tests/test_stub_pages.py pins
 * them byte for byte against fixtures/pokeapi/ditto.json).
 *
 * Vanilla JS, no build step. write_pages() copies this file next to
 * index.html as pages-pokemon.js.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.mcpToolcallLabPagesPokemon = api;
    if (typeof document !== "undefined") {
      api.autoMount(root);
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var API = "https://pokeapi.co/api/v2/pokemon/";
  var LIMIT = 2 * 1024 * 1024;
  var NAME_RE = /^[a-z0-9][a-z0-9-]{0,39}$/;
  var MISSING_NAME = "Enter a Pokémon name (e.g. ditto) or number.";
  var MISSING_SOURCE = "Paste the JSON from the pokeapi.co page first.";
  var NOT_POKEMON = "not a PokéAPI pokemon response";

  function normalizeName(value) {
    var text = String(value === null || value === undefined ? "" : value).trim().toLowerCase();
    return NAME_RE.test(text) ? text : "";
  }

  function buildUrl(name) {
    var clean = normalizeName(name);
    return clean ? API + clean : "";
  }

  function byteLength(text) {
    return typeof TextEncoder === "function" ? new TextEncoder().encode(text).length : text.length;
  }

  function integer(value) {
    var n = Number(value);
    if (value === null || value === "" || typeof value === "boolean" || !Number.isInteger(n)) {
      throw new Error(NOT_POKEMON);
    }
    return n;
  }

  function bySlot(rows) {
    if (!Array.isArray(rows)) {
      throw new Error(NOT_POKEMON);
    }
    return rows.slice().sort(function (a, b) {
      return integer(a.slot) - integer(b.slot);
    });
  }

  /** Normalized record for PokéAPI pokemon JSON text: {ok, record} or {ok:false, error}. */
  function parse(text) {
    if (typeof text !== "string") {
      return { ok: false, error: "source is JSON text" };
    }
    if (byteLength(text) > LIMIT) {
      return { ok: false, error: "source exceeds 2 MiB" };
    }
    var data;
    try {
      data = JSON.parse(text);
    } catch (err) {
      return { ok: false, error: "source is not JSON" };
    }
    try {
      var stats = [];
      data.stats.forEach(function (row) {
        var key = String(row.stat.name);
        var value = integer(row.base_stat);
        for (var i = 0; i < stats.length; i++) {
          if (stats[i][0] === key) {
            stats[i][1] = value;
            return;
          }
        }
        stats.push([key, value]);
      });
      var record = {
        schema: "pokeapi-pokemon/1",
        id: integer(data.id),
        name: String(data.name),
        height_m: integer(data.height) / 10,
        weight_kg: integer(data.weight) / 10,
        base_experience: data.base_experience === undefined ? null : data.base_experience,
        types: bySlot(data.types).map(function (row) {
          return String(row.type.name);
        }),
        abilities: bySlot(data.abilities).map(function (row) {
          return { name: String(row.ability.name), hidden: Boolean(row.is_hidden) };
        }),
        stats: stats,
      };
      if (!NAME_RE.test(record.name) || !stats.length) {
        throw new Error(NOT_POKEMON);
      }
      return { ok: true, record: record };
    } catch (err) {
      return { ok: false, error: NOT_POKEMON };
    }
  }

  function number(value) {
    return Number.isInteger(value) ? value.toFixed(1) : String(value);
  }

  function table(headers, rows) {
    var out = "| " + headers.join(" | ") + " |\n| " + headers.map(function () { return "---"; }).join(" | ") + " |\n";
    rows.forEach(function (row) {
      out += "| " + row.join(" | ") + " |\n";
    });
    return out;
  }

  /** Same Markdown as adapters/pokeapi.py (vendored markdown.py table). */
  function toMarkdown(record) {
    return (
      "# " + record.name + " (#" + record.id + ")\n\n" +
      table(["type", "height m", "weight kg"], [[record.types.join(", "), number(record.height_m), number(record.weight_kg)]]) + "\n" +
      table(["stat", "base"], record.stats.map(function (row) { return [row[0], String(row[1])]; })) + "\n" +
      table(["ability", "hidden"], record.abilities.map(function (a) { return [a.name, a.hidden ? "yes" : ""]; }))
    );
  }

  /** One direct GET of the pokemon URL; resolves {ok, record} or {ok:false, error}. */
  function load(name, fetchImpl) {
    var clean = normalizeName(name);
    if (!clean) {
      return Promise.resolve({ ok: false, error: MISSING_NAME });
    }
    return Promise.resolve()
      .then(function () {
        return fetchImpl(API + clean, { headers: { Accept: "application/json" } });
      })
      .then(function (response) {
        if (response.status === 404) {
          return { ok: false, error: "no Pokémon named '" + clean + "' on pokeapi.co" };
        }
        if (!response.ok) {
          return { ok: false, error: "pokeapi.co answered HTTP " + response.status };
        }
        return response.text().then(parse);
      })
      .catch(function (err) {
        return {
          ok: false,
          error: "could not fetch '" + clean + "' from pokeapi.co (" + ((err && err.message) || "network error") +
            "). Open the JSON, copy it, and paste it below instead.",
        };
      });
  }

  function qs(node, testid) {
    return node && typeof node.querySelector === "function" ? node.querySelector('[data-testid="' + testid + '"]') : null;
  }

  function setHidden(el, hidden) {
    if (el) {
      if (hidden) {
        el.setAttribute("hidden", "");
      } else {
        el.removeAttribute("hidden");
      }
    }
  }

  function setText(el, text) {
    if (el) {
      el.textContent = text;
    }
  }

  function mount(rootEl, options) {
    options = options || {};
    var els = {
      name: qs(rootEl, "pages-pokemon-name"),
      open: qs(rootEl, "pages-pokemon-open"),
      fetch: qs(rootEl, "pages-pokemon-fetch"),
      form: qs(rootEl, "pages-pokemon-source-form"),
      text: qs(rootEl, "pages-pokemon-source-text"),
      status: qs(rootEl, "pages-pokemon-status"),
      error: qs(rootEl, "pages-pokemon-error"),
      output: qs(rootEl, "pages-pokemon-extract"),
    };
    if (!els.form) {
      return null;
    }
    var win = options.window || (typeof window !== "undefined" ? window : null);
    var fetchImpl = options.fetch || (win && typeof win.fetch === "function" ? win.fetch.bind(win) : null);

    function show(result, origin) {
      setHidden(els.error, result.ok);
      setHidden(els.output, !result.ok);
      setText(els.error, result.ok ? "" : result.error);
      setText(els.output, result.ok ? toMarkdown(result.record) : "");
      setText(els.status, result.ok ? result.record.name + " (#" + result.record.id + ") — " + origin : "");
      return result;
    }

    if (els.open) {
      els.open.addEventListener("click", function () {
        var url = buildUrl(els.name && els.name.value);
        if (!url) {
          return show({ ok: false, error: MISSING_NAME });
        }
        if (win && typeof win.open === "function") {
          win.open(url, "_blank", "noopener");
        }
        setText(els.status, "Opened " + url + " — copy its JSON and paste it below.");
        return null;
      });
    }
    if (els.fetch) {
      els.fetch.addEventListener("click", function () {
        if (!fetchImpl) {
          return show({ ok: false, error: "fetch is not available; paste the JSON instead" });
        }
        return load(els.name && els.name.value, fetchImpl).then(function (result) {
          return show(result, "fetched from pokeapi.co");
        });
      });
    }
    els.form.addEventListener("submit", function (event) {
      if (event && typeof event.preventDefault === "function") {
        event.preventDefault();
      }
      var text = String((els.text && els.text.value) || "").trim();
      return show(text ? parse(text) : { ok: false, error: MISSING_SOURCE }, "from pasted JSON");
    });
    return { show: show };
  }

  function autoMount(win) {
    if (!win || !win.document) {
      return null;
    }
    var doc = win.document;
    var rootEl = doc.querySelector('[data-testid="pages-pokemon-app"]');
    if (!rootEl) {
      return null;
    }
    var start = function () {
      return mount(rootEl, { window: win });
    };
    if (doc.readyState === "loading" && typeof doc.addEventListener === "function") {
      doc.addEventListener("DOMContentLoaded", start);
      return null;
    }
    return start();
  }

  return {
    API: API,
    MISSING_NAME: MISSING_NAME,
    MISSING_SOURCE: MISSING_SOURCE,
    normalizeName: normalizeName,
    buildUrl: buildUrl,
    parse: parse,
    toMarkdown: toMarkdown,
    load: load,
    mount: mount,
    autoMount: autoMount,
  };
});
