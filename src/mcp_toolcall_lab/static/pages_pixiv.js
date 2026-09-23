/*
 * Static Pixiv panel controller. GitHub Pages cannot execute MCP; this keeps
 * the search/result UX explicit and points the same title at the local stub.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else if (typeof document !== "undefined") api.mount(document, root.location);
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  function localPath(title) {
    return "/pixiv?title=" + encodeURIComponent(String(title || "").trim());
  }
  function mount(doc, location) {
    var form = doc.querySelector("#pages-pixiv-form");
    if (!form) return;
    var input = doc.querySelector("#pages-pixiv-title");
    var status = doc.querySelector("[data-testid=pages-pixiv-status]");
    var output = doc.querySelector("[data-testid=pages-pixiv-extract]");
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var title = String(input && input.value || "").trim();
      if (!title) {
        status.textContent = "Enter a Pixiv Encyclopedia title.";
        output.hidden = true;
        return;
      }
      var path = localPath(title);
      status.textContent = "MCP execution is local-only. Start stub_front serve, then open " + path;
      output.textContent =
        "fetch_pixiv_dictionary_article\n" +
        JSON.stringify({title: title}, null, 2) + "\n\n" +
        "Local result URL: http://127.0.0.1:8765" + path;
      output.hidden = false;
    });
  }
  return { localPath: localPath, mount: mount };
});
