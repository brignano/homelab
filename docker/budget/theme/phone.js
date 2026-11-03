// Long-press a Reports card on a phone to open its report (homelab#337).
//
// Caddy adds this script and a style rule to Actual's page. Below Actual's
// phone breakpoint (730px) the rule makes each dashboard card look-only, so a
// tap while scrolling no longer opens the report. Holding a card still for
// HOLD_MS opens it: this presses the card's own button, which is Actual's
// normal navigation. Taps, scrolls and desktop widths are left alone.
(() => {
  const HOLD_MS = 450;
  const MOVE_PX = 10;
  const phone = window.matchMedia("(max-width: 729px)");
  let timer = null;
  let fired = false;
  let startX = 0;
  let startY = 0;

  const cancel = () => {
    clearTimeout(timer);
    timer = null;
  };

  document.addEventListener(
    "touchstart",
    (e) => {
      cancel();
      fired = false;
      if (!phone.matches || e.touches.length !== 1) return;
      const target = e.target instanceof Element ? e.target : null;
      const overview = target?.closest('[data-testid="reports-overview"]');
      if (!overview) return;
      // Each card is one grid item holding a single button around the card.
      const button = target.closest(".react-grid-item")?.querySelector("button");
      if (!button) return;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      timer = setTimeout(() => {
        timer = null;
        fired = true;
        navigator.vibrate?.(10);
        button.click();
      }, HOLD_MS);
    },
    { passive: true },
  );

  document.addEventListener(
    "touchmove",
    (e) => {
      if (!timer) return;
      const t = e.touches[0];
      if (Math.abs(t.clientX - startX) > MOVE_PX || Math.abs(t.clientY - startY) > MOVE_PX) cancel();
    },
    { passive: true },
  );
  // After a long-press has opened the report, the finger lifting must not
  // also click whatever is now under it (iOS sends a click on release).
  document.addEventListener(
    "touchend",
    (e) => {
      cancel();
      if (fired) {
        fired = false;
        e.preventDefault();
      }
    },
    { passive: false },
  );
  document.addEventListener("touchcancel", cancel, { passive: true });
})();
