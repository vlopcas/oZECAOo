(function () {
  'use strict';

  const header = document.querySelector('.site-header');
  const backToTop = document.querySelector('.back-to-top');
  const wordmark = document.querySelector('.wordmark');
  const sectionLinks = Array.from(header?.querySelectorAll('.header-nav a[href^="#"]') || [])
    .map(function (link) {
      let target;
      try {
        target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
      } catch (_) {
        return null;
      }
      return target ? { link, target } : null;
    })
    .filter(Boolean)
    .sort(function (first, second) {
      return first.target.compareDocumentPosition(second.target) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    });
  let scrollUpdatePending = false;
  let activeSectionLink;

  function updateActiveSection(scrollPosition) {
    const headerHeight = header?.getBoundingClientRect().height || 0;
    const activationLine = headerHeight + 24;
    let nextActiveLink = null;

    // Section starts are stable even when an embedded preview changes height.
    // A single line below the header avoids competing intersection ratios.
    for (const entry of sectionLinks) {
      if (entry.target.getBoundingClientRect().top > activationLine) break;
      nextActiveLink = entry.link;
    }

    const pageHeight = document.documentElement.scrollHeight;
    const atBottom = scrollPosition > 0 && scrollPosition + window.innerHeight >= pageHeight - 2;
    if (atBottom && sectionLinks.length) {
      nextActiveLink = sectionLinks[sectionLinks.length - 1].link;
    }

    if (nextActiveLink === activeSectionLink) return;
    for (const entry of sectionLinks) {
      if (entry.link === nextActiveLink) entry.link.setAttribute('aria-current', 'location');
      else entry.link.removeAttribute('aria-current');
    }
    activeSectionLink = nextActiveLink;
  }

  function updateScrollState() {
    scrollUpdatePending = false;
    const scrollPosition = window.scrollY;
    const showBackToTop = scrollPosition > Math.max(400, window.innerHeight * 0.75);
    header?.classList.toggle('header-scrolled', scrollPosition >= 16);
    updateActiveSection(scrollPosition);

    if (backToTop) {
      // Return focus to a visible control before the floating link disappears.
      if (!showBackToTop && backToTop.contains(document.activeElement)) {
        wordmark?.focus({ preventScroll: true });
      }
      backToTop.hidden = !showBackToTop;
    }
  }

  function scheduleScrollUpdate() {
    if (scrollUpdatePending) return;
    scrollUpdatePending = true;
    window.requestAnimationFrame(updateScrollState);
  }

  backToTop?.addEventListener('click', function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    // Keep the native anchor and CSS scroll behavior, including reduced motion.
    wordmark?.focus({ preventScroll: true });
  });

  window.addEventListener('scroll', scheduleScrollUpdate, { passive: true });
  window.addEventListener('resize', scheduleScrollUpdate, { passive: true });
  window.addEventListener('pageshow', scheduleScrollUpdate);
  window.addEventListener('hashchange', scheduleScrollUpdate);
  window.addEventListener('load', scheduleScrollUpdate);
  if (typeof ResizeObserver !== 'undefined') {
    const layoutObserver = new ResizeObserver(scheduleScrollUpdate);
    layoutObserver.observe(document.body);
    if (header) layoutObserver.observe(header);
    for (const entry of sectionLinks) layoutObserver.observe(entry.target);
  }
  updateScrollState();

  const toggle = header?.querySelector('.menu-toggle');
  const navigation = header?.querySelector('#header-navigation');
  if (!header || !toggle || !navigation) return;

  const mobile = window.matchMedia('(max-width: 720px)');
  let lastFocused = document.activeElement;

  document.addEventListener('focusin', function (event) {
    lastFocused = event.target;
  });

  document.addEventListener('focusout', function (event) {
    if (event.relatedTarget && event.relatedTarget !== document.body) {
      lastFocused = event.relatedTarget;
    } else if (event.target.getClientRects().length) {
      // An intentional blur of a visible element must not restore stale focus.
      lastFocused = null;
    }
    // A control hidden by a breakpoint may lose focus before its change event.
  });

  function setOpen(open, restoreFocus = false) {
    const expanded = open && mobile.matches;
    header.classList.toggle('menu-open', expanded);
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded ? 'Fechar menu' : 'Abrir menu');
    if (restoreFocus) toggle.focus();
    scheduleScrollUpdate();
  }

  toggle.addEventListener('click', function () {
    setOpen(toggle.getAttribute('aria-expanded') !== 'true');
  });

  navigation.addEventListener('click', function (event) {
    if (event.target.closest('a')) setOpen(false);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && header.classList.contains('menu-open')) {
      event.preventDefault();
      setOpen(false, true);
    }
  });

  document.addEventListener('click', function (event) {
    if (!navigation.contains(event.target) && !toggle.contains(event.target)) setOpen(false);
  });

  header.addEventListener('focusout', function (event) {
    if (event.relatedTarget && !header.contains(event.relatedTarget)) setOpen(false);
  });

  mobile.addEventListener('change', function () {
    const active = document.activeElement;
    const focused = active === document.body || active === document.documentElement ? lastFocused : active;
    const focusWasInNavigation = navigation.contains(focused);
    const focusWasOnToggle = focused === toggle;
    setOpen(false);
    if (mobile.matches && focusWasInNavigation) toggle.focus();
    else if (!mobile.matches && focusWasOnToggle) {
      navigation.querySelector('a[href]')?.focus();
    }
    lastFocused = document.activeElement;
  });

  setOpen(false);
  header.classList.add('navigation-ready');
  scheduleScrollUpdate();
})();
