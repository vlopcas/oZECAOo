(function () {
  'use strict';

  const header = document.querySelector('.site-header');
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
})();
