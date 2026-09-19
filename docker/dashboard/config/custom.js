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
