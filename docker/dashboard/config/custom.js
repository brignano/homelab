// Tell the design tokens which theme Homepage settled on.
//
// The two decide light/dark by different means and neither can see the other.
// tokens.css keys its dark values off `prefers-color-scheme` and an explicit
// `[data-theme]` opt-out; Homepage ignores the OS entirely and puts `light` or
// `dark` on <html> from its `theme:` setting and the toggle in its header,
// remembered in localStorage.
//
// Left alone, the two disagree the moment they differ — a phone in light mode
// on a dashboard pinned to dark gets the light ramp painted on a dark page,
// which is unreadable rather than merely wrong. Mirroring Homepage's class onto
// the attribute the tokens already understand keeps one source of truth, and
// costs one MutationObserver. There is no styling in here: this file loads
// after hydration, so anything visual would land as a flash.
(() => {
  const html = document.documentElement;

  const sync = () => {
    const theme = html.classList.contains("dark") ? "dark" : "light";
    if (html.dataset.theme !== theme) {
      html.dataset.theme = theme;
    }
  };

  sync();
  new MutationObserver(sync).observe(html, { attributes: true, attributeFilter: ["class"] });
})();

// Declare the icons Homepage cannot.
//
// Setting `favicon:` makes Homepage emit exactly two tags from the one path:
// `rel="icon"` and `rel="apple-touch-icon"`. That path is the SVG, which is
// right for the tab and wrong for iOS — it will not take an SVG for a home
// screen icon, and substitutes a screenshot of the page.
//
// So the apple-touch link is repointed at the raster, and the fallbacks a
// browser wants when it declines an SVG are added beside it: a plain 32px PNG
// (no ICO container to parse, no inline <style> to evaluate) and the .ico.
// Order is the same as `life`'s markup, for the same reason — browsers that
// understand `type="image/svg+xml"` take the SVG, everything else falls
// through.
//
// Safari reads the live DOM when someone taps Add to Home Screen, so patching
// these after hydration is honoured. The root paths it probes *without*
// reading any markup are handled in docker/proxy/Caddyfile, not here.
(() => {
  const head = document.head;

  const link = (rel, href, attrs = {}) => {
    const el = document.createElement("link");
    el.rel = rel;
    el.href = href;
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    head.appendChild(el);
  };

  const apple = head.querySelector('link[rel="apple-touch-icon"]');
  if (apple) {
    apple.href = "/icons/apple-touch-icon.png";
  } else {
    link("apple-touch-icon", "/icons/apple-touch-icon.png", { sizes: "180x180" });
  }

  if (!head.querySelector('link[rel="icon"][type="image/png"]')) {
    link("icon", "/icons/favicon-32.png", { type: "image/png", sizes: "32x32" });
  }
  if (!head.querySelector('link[rel="icon"][href$=".ico"]')) {
    link("icon", "/icons/favicon.ico", { sizes: "32x32" });
  }
})();
