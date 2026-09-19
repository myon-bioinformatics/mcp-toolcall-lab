/*
 * Static, client-side re-implementation of stub_front.py's heading -> body
 * lookup (parse_sections / slugify / lookup_heading / classify_prompt).
 * No server, no MCP: this is what GitHub Pages *can* run by itself.
 *
 * Corpus data (title/slug/body only -- no chat_id, no MCP arguments, no
 * logs) is fetched from stub-demo-data.json, generated at Pages-build time
 * from the same fixtures/stub_front/*.md the real stub server reads, so
 * there is one source of truth for corpus content.
 *
 * MCP_PATTERNS below mirrors stub_front.py's MCP_PATTERNS *tokens and tool
 * names only* -- there is no MCP client here, so a matching prompt is
 * labelled, never answered with a fabricated result. tests/test_stub_pages.py
 * checks this list stays in sync with the Python source.
 *
 * Vanilla JS, no build step, no dependencies -- same rule as the rest of
 * this repo's "necessary minimum" stub.
 */
(function () {
  "use strict";

  var MCP_PATTERNS = [
    { tokens: ["station", "駅", "find_stations"], tool: "find_stations" },
    { tokens: ["price", "transaction", "価格", "find_transaction"], tool: "find_transaction_prices" },
    { tokens: ["yokohama", "横浜", "municipalit", "市区町村", "find_municipalities"], tool: "find_municipalities" },
  ];

  function slugify(title) {
    return String(title)
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  // Mirrors lookup_heading() in stub_front.py: exact title/slug match first,
  // then (if fuzzy) a starts-with/contains pass. Not CommonMark-aware; ATX
  // titles only, same as the Python side.
  function lookupHeading(query, sections, fuzzy) {
    var needle = String(query).trim();
    if (needle.charAt(0) === "#") {
      needle = needle.replace(/^#+/, "").trim();
    }
    if (!needle) return null;
    var slug = slugify(needle);
    var folded = needle.toLowerCase();
    var i, section;
    for (i = 0; i < sections.length; i++) {
      section = sections[i];
      if (section.title === needle || section.title.toLowerCase() === folded || section.slug === slug) {
        return section;
      }
    }
    if (!fuzzy) return null;
    for (i = 0; i < sections.length; i++) {
      section = sections[i];
      var title = section.title.toLowerCase();
      if (title.indexOf(folded) === 0 || folded.indexOf(title) === 0 || title.indexOf(folded) !== -1) {
        return section;
      }
    }
    return null;
  }

  // Mirrors classify_prompt() in stub_front.py. The "mcp" branch never calls
  // a server -- it only names which tool the real stub would have tried,
  // same honesty rule as the rest of this Pages report.
  function classifyPrompt(prompt, sections) {
    var text = String(prompt).trim();
    var forcedHeading = text.charAt(0) === "#";
    var exact = lookupHeading(text, sections, false);
    if (exact) return { kind: "heading", section: exact };
    var lowered = text.toLowerCase();
    if (!forcedHeading) {
      for (var i = 0; i < MCP_PATTERNS.length; i++) {
        var pattern = MCP_PATTERNS[i];
        for (var j = 0; j < pattern.tokens.length; j++) {
          if (lowered.indexOf(pattern.tokens[j]) !== -1) {
            return { kind: "mcp", tool: pattern.tool };
          }
        }
      }
    }
    var fuzzy = lookupHeading(text, sections, true);
    if (fuzzy) return { kind: "heading", section: fuzzy };
    return { kind: "miss" };
  }

  function reply(prompt, sections) {
    var classified = classifyPrompt(prompt, sections);
    if (classified.kind === "heading") {
      return { case: "HEADING_HIT", text: classified.section.body || "(no body under this heading)" };
    }
    if (classified.kind === "mcp") {
      return {
        case: "MCP_PATTERN",
        text:
          "This looks like an MCP tool-call prompt for `" +
          classified.tool +
          "`. This static Pages demo has no MCP server behind it, so it will not fabricate a result " +
          "-- run the real round-trip via `docker compose -f docker/stub-pages/docker-compose.yml up --build`, " +
          "or trigger the `stub-pages` GitHub Actions workflow (see the last verified run below).",
      };
    }
    var titles = sections.slice(0, 12).map(function (s) {
      return s.title;
    });
    return {
      case: "HEADING_MISS",
      text: "No heading matched. Known: " + (titles.join(", ") || "(empty corpus)"),
    };
  }

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        node.setAttribute(key, attrs[key]);
      });
    }
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function mount(root, corpusUrl, ids) {
    var sections = [];
    var status = el("p", { class: "stub-demo-status" }, "Loading corpus…");
    var thread = el("div", { class: "stub-demo-thread" });
    var form = el("form", { class: "stub-demo-form" });
    var textarea = el("textarea", {
      id: ids.input,
      "data-testid": ids.inputTestId,
      rows: "2",
      placeholder: 'Try "Yokohama", "Find municipalities", or "# Tracing"',
    });
    var button = el("button", { id: ids.send, "data-testid": ids.sendTestId, type: "submit" }, "Send");
    form.appendChild(textarea);
    form.appendChild(button);
    root.appendChild(status);
    root.appendChild(thread);
    root.appendChild(form);

    function addTurn(prompt, result) {
      var turn = el("div", { class: "stub-demo-turn", "data-case": result.case });
      turn.appendChild(el("div", { class: "stub-demo-user" }, "you: " + prompt));
      var assistant = el("div", { id: ids.response, class: "stub-demo-assistant" });
      assistant.textContent = "stub (" + result.case + "): " + result.text;
      turn.appendChild(assistant);
      thread.appendChild(turn);
      thread.scrollTop = thread.scrollHeight;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var prompt = textarea.value.trim();
      if (!prompt) return;
      addTurn(prompt, reply(prompt, sections));
      textarea.value = "";
    });

    fetch(corpusUrl)
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        sections = Array.isArray(data) ? data : [];
        status.textContent = sections.length
          ? "Corpus loaded (" + sections.length + " headings). No MCP or Docker running here — heading lookup only."
          : "Corpus is empty.";
      })
      .catch(function (err) {
        status.textContent = "Could not load stub-demo-data.json: " + err.message;
      });
  }

  window.mcpToolcallLabStubDemo = { mount: mount, classifyPrompt: classifyPrompt, lookupHeading: lookupHeading, slugify: slugify };
})();
