// Tripwire step observer. Injected into every page by `tripwire watch`.
// Reports ground-truth user/agent actions over a CDP binding so the watcher
// can build the "Steps to reproduce" section. Typed VALUES are never read —
// only which element was interacted with.
(() => {
  if (window.__tripwireObserver) return;
  window.__tripwireObserver = true;

  const send = (msg) => {
    try {
      window.__tripwire_binding(JSON.stringify(msg));
    } catch (_) {}
  };

  const describe = (el) => {
    if (!el || !el.tagName) return "page";
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${el.id}`;
    const name = el.getAttribute("name");
    if (name) return `${tag}[name="${name}"]`;
    const aria = el.getAttribute("aria-label");
    if (aria) return `${tag} "${aria.slice(0, 40)}"`;
    const text = (el.innerText || "").trim().replace(/\s+/g, " ").slice(0, 40);
    if (text) return `${tag} "${text}"`;
    const cls = typeof el.className === "string" ? el.className.trim().split(/\s+/)[0] : "";
    return cls ? `${tag}.${cls}` : tag;
  };

  // Attribute the event to the interactive ancestor, not the leaf span/svg.
  const interesting = (el) =>
    (el && el.closest && el.closest("a,button,input,select,textarea,label,[role=button],[onclick]")) || el;

  document.addEventListener(
    "click",
    (e) => send({ kind: "click", target: describe(interesting(e.target)) }),
    { capture: true, passive: true }
  );

  document.addEventListener(
    "submit",
    (e) => send({ kind: "submit", target: describe(e.target) }),
    { capture: true, passive: true }
  );

  // Debounce typing into one step per field; the value itself is never read.
  let inputTimer = null;
  let inputTarget = null;
  const flushInput = () => {
    if (inputTarget) send({ kind: "input", target: inputTarget });
    inputTarget = null;
  };
  document.addEventListener(
    "input",
    (e) => {
      const target = describe(interesting(e.target));
      if (target !== inputTarget) flushInput();
      inputTarget = target;
      clearTimeout(inputTimer);
      inputTimer = setTimeout(flushInput, 800);
    },
    { capture: true, passive: true }
  );

  // SPA navigations; hard navigations reach the watcher via Page.frameNavigated.
  const nav = () => send({ kind: "navigate", url: location.href });
  for (const fn of ["pushState", "replaceState"]) {
    const orig = history[fn];
    history[fn] = function (...args) {
      const result = orig.apply(this, args);
      nav();
      return result;
    };
  }
  addEventListener("popstate", nav);
  addEventListener("hashchange", nav);
})();
