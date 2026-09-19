/*
 * Same-origin hash router for the published GitHub Pages index.
 * Switches the home report and the #wiki induction panel. Not a live
 * Wikipedia form, and not the local stub-demo.js heading pulldown.
 *
 * Vanilla JS, no build step. write_pages() copies this file next to
 * index.html as pages-hash.js. viewFromLocation / applyView are the
 * contracts tests/test_stub_pages.py exercises via node.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.mcpToolcallLabPagesView = api;
    if (typeof document !== "undefined") {
      api.bind(root);
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var HOME = "home";
  var WIKI = "wiki";

  function fragmentName(hash) {
    var raw = String(hash || "");
    if (raw.charAt(0) === "#") {
      raw = raw.slice(1);
    }
    return raw.split("?")[0];
  }

  function queryHasWikiView(search) {
    var raw = String(search || "");
    if (!raw) {
      return false;
    }
    if (typeof URLSearchParams === "function") {
      var params = new URLSearchParams(raw.charAt(0) === "?" ? raw.slice(1) : raw);
      return params.get("view") === WIKI;
    }
    var q = raw.charAt(0) === "?" ? raw.slice(1) : raw;
    var parts = q.split("&");
    for (var i = 0; i < parts.length; i++) {
      var pair = parts[i].split("=");
      var key = decodeURIComponent(pair[0] || "").replace(/\+/g, " ");
      var value = decodeURIComponent((pair[1] || "").replace(/\+/g, " "));
      if (key === "view" && value === WIKI) {
        return true;
      }
    }
    return false;
  }

  function viewFromLocation(hash, search) {
    if (fragmentName(hash) === WIKI || queryHasWikiView(search)) {
      return WIKI;
    }
    return HOME;
  }

  function applyView(doc, view) {
    if (!doc || typeof doc.querySelectorAll !== "function") {
      return view;
    }
    var panels = doc.querySelectorAll("[data-pages-view]");
    for (var i = 0; i < panels.length; i++) {
      var panel = panels[i];
      var match = panel.getAttribute("data-pages-view") === view;
      if (match) {
        panel.removeAttribute("hidden");
      } else {
        panel.setAttribute("hidden", "");
      }
    }
    var navs = doc.querySelectorAll("[data-pages-nav]");
    for (var j = 0; j < navs.length; j++) {
      var link = navs[j];
      if (link.getAttribute("data-pages-nav") === view) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    }
    return view;
  }

  function normalizeWikiHash(win, view) {
    if (view !== WIKI || !win || !win.location || !win.history || !win.history.replaceState) {
      return;
    }
    if (fragmentName(win.location.hash) === WIKI) {
      return;
    }
    try {
      var url = new URL(win.location.href);
      url.hash = WIKI;
      if (url.searchParams.get("view") === WIKI) {
        url.searchParams.delete("view");
      }
      win.history.replaceState(null, "", url.pathname + url.search + url.hash);
    } catch (err) {
      return;
    }
  }

  function syncFromLocation(win) {
    var loc = win && win.location ? win.location : { hash: "", search: "" };
    var view = viewFromLocation(loc.hash, loc.search);
    applyView(win && win.document, view);
    normalizeWikiHash(win, view);
    return view;
  }

  function bind(win) {
    if (!win || !win.document) {
      return;
    }
    var onChange = function () {
      syncFromLocation(win);
    };
    if (typeof win.addEventListener === "function") {
      win.addEventListener("hashchange", onChange);
    }
    if (win.document.readyState === "loading" && typeof win.document.addEventListener === "function") {
      win.document.addEventListener("DOMContentLoaded", onChange);
    } else {
      onChange();
    }
  }

  return {
    HOME: HOME,
    WIKI: WIKI,
    viewFromLocation: viewFromLocation,
    applyView: applyView,
    syncFromLocation: syncFromLocation,
    bind: bind,
  };
});
