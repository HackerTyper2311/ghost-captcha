/**
 * Ghost Font CAPTCHA - drop-in widget script (no API key required).
 *
 * Usage:
 *
 *   <script src="https://your-host/api.js" async defer></script>
 *   <div class="ghost-captcha"></div>
 *
 * The script auto-discovers every element with the `ghost-captcha` class,
 * injects a sandboxed <iframe> pointing at the widget page, and listens
 * for a `postMessage` containing the verified token.  When a token arrives:
 *   1. Stores it on the container element (element.dataset.token).
 *   2. Injects/updates a hidden <input name="ghost-captcha-response"> in
 *      the nearest ancestor <form> so standard form submissions carry it.
 *   3. Invokes the optional `data-callback` function with the token.
 *
 * Supported data-* attributes:
 *   data-callback  (optional) - global function name invoked with the token.
 *   data-theme     (optional) - "dark" (default) or "light".
 *   data-language  (optional) - passed to the widget as `lang` query param.
 */
(function () {
  "use strict";

  var DEFAULT_SRC = (document.currentScript && document.currentScript.src) || "";
  // Derive the widget host from the script's own URL (strip /api.js).
  var HOST = DEFAULT_SRC.replace(/\/api\.js.*$/, "");
  if (!HOST) {
    HOST = window.location.origin;
  }

  var INPUT_NAME = "ghost-captcha-response";
  var RENDER_CLASS = "ghost-captcha";

  function resolveCallback(name) {
    if (!name) return null;
    var ref = window;
    name.split(".").forEach(function (part) {
      if (ref) ref = ref[part];
    });
    return typeof ref === "function" ? ref : null;
  }

  function nearestForm(el) {
    var node = el;
    while (node && node !== document.body) {
      if (node.tagName === "FORM") return node;
      node = node.parentElement;
    }
    return null;
  }

  function ensureHiddenInput(container) {
    var form = nearestForm(container);
    if (!form) return null;
    var existing = form.querySelector('input[name="' + INPUT_NAME + '"]');
    if (existing) return existing;
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = INPUT_NAME;
    form.appendChild(input);
    return input;
  }

  function renderWidget(target) {
    if (target.dataset.gcRendered === "1") return;
    var theme = target.getAttribute("data-theme") || "dark";
    var language = target.getAttribute("data-language") || "";
    var callbackName = target.getAttribute("data-callback") || "";

    var src =
      HOST +
      "/widget?origin=" +
      encodeURIComponent(window.location.origin) +
      "&theme=" +
      encodeURIComponent(theme) +
      (language ? "&lang=" + encodeURIComponent(language) : "");

    var iframe = document.createElement("iframe");
    iframe.src = src;
    iframe.title = "Ghost Font CAPTCHA";
    iframe.width = "100%";
    iframe.height = "540";
    iframe.frameBorder = "0";
    iframe.scrolling = "no";
    iframe.style.border = "0";
    iframe.style.width = "100%";
    iframe.style.height = "540px";
    iframe.style.display = "block";
    iframe.style.borderRadius = "12px";
    iframe.style.overflow = "hidden";
    // allow-same-origin lets the iframe talk to its own API without CORS.
    // The widget is still isolated from the parent page by the browser.
    iframe.setAttribute("sandbox", "allow-scripts allow-forms allow-popups allow-same-origin");
    iframe.setAttribute("allow", "autoplay; encrypted-media");

    target.innerHTML = "";
    target.style.display = "block";
    target.appendChild(iframe);
    target.dataset.gcRendered = "1";
    target.dataset.token = "";

    target.ghostCaptcha = {
      iframe: iframe,
      reset: function () {
        iframe.contentWindow.postMessage({ type: "ghost-captcha-reset" }, HOST);
      },
      getResponse: function () {
        return target.dataset.token || "";
      },
    };

    // Keep callback name reference for the message listener.
    target.dataset.gcCallback = callbackName;
  }

  // Receive the verified token from the iframe.
  window.addEventListener("message", function (event) {
    if (event.origin !== HOST) return;
    var data = event.data || {};
    if (data.type !== "ghost-captcha-token") return;

    var containers = document.querySelectorAll("." + RENDER_CLASS);
    var target = null;
    Array.prototype.forEach.call(containers, function (el) {
      var iframe = el.querySelector("iframe");
      if (iframe && iframe.contentWindow === event.source) target = el;
    });
    if (!target) return;

    target.dataset.token = data.token || "";
    var input = ensureHiddenInput(target);
    if (input) input.value = data.token || "";

    if (data.error) {
      target.dispatchEvent(new CustomEvent("ghost-captcha:error", { detail: data }));
    } else {
      target.dispatchEvent(new CustomEvent("ghost-captcha:success", { detail: { token: data.token } }));
      var cbName = target.getAttribute("data-callback") || target.dataset.gcCallback || "";
      var cb = resolveCallback(cbName);
      if (cb) cb(data.token);
    }
  });

  function scan() {
    var nodes = document.querySelectorAll("." + RENDER_CLASS);
    Array.prototype.forEach.call(nodes, renderWidget);
  }

  window.ghostCaptcha = {
    render: function (target, options) {
      var el = typeof target === "string" ? document.querySelector(target) : target;
      if (!el) return;
      if (options && options.callback) el.setAttribute("data-callback", options.callback);
      if (options && options.theme) el.setAttribute("data-theme", options.theme);
      renderWidget(el);
    },
    reset: function (target) {
      var el = typeof target === "string" ? document.querySelector(target) : target;
      if (el && el.ghostCaptcha) el.ghostCaptcha.reset();
    },
    getResponse: function (target) {
      var el = typeof target === "string" ? document.querySelector(target) : target;
      return el && el.ghostCaptcha ? el.ghostCaptcha.getResponse() : "";
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
})();
