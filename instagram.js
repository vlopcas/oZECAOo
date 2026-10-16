(function () {
  'use strict';

  const embeds = [];
  const instagramOrigin = 'https://www.instagram.com';

  document.querySelectorAll('[data-instagram-embed]').forEach(function (container) {
    const frame = container.querySelector('iframe[data-src]');
    if (!frame) return;

    let url;
    try {
      url = new URL(frame.dataset.src, document.baseURI);
    } catch (_) {
      return;
    }

    // Load only the official public profile embed, never an arbitrary frame URL.
    if (url.origin !== instagramOrigin || !/^\/[A-Za-z0-9._]+\/embed\/$/.test(url.pathname) || url.search || url.hash) return;

    const embed = { container: container, frame: frame, url: url.href, started: false, timer: null, resumed: false };
    embeds.push(embed);
    container.hidden = false;
    frame.tabIndex = -1;
    frame.addEventListener('load', function () {
      if (!embed.started || embed.resumed || !container.classList.contains('instagram-unavailable')) return;
      // Hidden cross-origin frames can stop rendering before they measure.
      // Give a late document one render window; load alone is not success.
      embed.resumed = true;
      container.classList.remove('instagram-unavailable');
      container.setAttribute('aria-busy', 'true');
      waitForMeasurement(embed, 4000);
    });
  });

  if (!embeds.length) return;

  function waitForMeasurement(embed, delay) {
    window.clearTimeout(embed.timer);
    embed.timer = window.setTimeout(function () {
      // A loaded document can still be an empty or blocked Instagram frame.
      // Keep it measurable so a late successful response can restore the embed.
      embed.container.classList.add('instagram-unavailable');
      embed.container.removeAttribute('aria-busy');
      embed.frame.tabIndex = -1;
      embed.timer = null;
    }, delay);
  }

  function loadEmbed(embed) {
    if (embed.started) return;
    embed.started = true;
    embed.container.setAttribute('aria-busy', 'true');
    waitForMeasurement(embed, 12000);
    embed.frame.src = embed.url;
  }

  // Instagram's own embed SDK uses MEASURE messages to publish frame height.
  // Accept only that value, from this exact official frame; never apply styles
  // or executable content received through postMessage.
  window.addEventListener('message', function (event) {
    if (event.origin !== instagramOrigin) return;
    const embed = embeds.find(function (candidate) {
      return candidate.started && candidate.frame.contentWindow === event.source;
    });
    if (!embed) return;

    let message = event.data;
    if (typeof message === 'string') {
      if (message.length > 4096) return;
      try {
        message = JSON.parse(message);
      } catch (_) {
        return;
      }
    }

    if (!message || message.type !== 'MEASURE' || !message.details) return;
    const measuredHeight = message.details.height;
    if (typeof measuredHeight !== 'number' || !Number.isFinite(measuredHeight) || measuredHeight < 240 || measuredHeight > 2000) return;

    window.clearTimeout(embed.timer);
    embed.timer = null;
    embed.container.classList.remove('instagram-unavailable');
    embed.frame.removeAttribute('tabindex');
    const height = Math.ceil(measuredHeight);
    embed.frame.height = String(height);
    embed.frame.style.height = height + 'px';
    embed.container.removeAttribute('aria-busy');
  });

  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        const embed = embeds.find(function (candidate) {
          return candidate.container === entry.target;
        });
        if (embed) loadEmbed(embed);
        observer.unobserve(entry.target);
      });
    }, { rootMargin: '320px 0px', threshold: 0 });
    embeds.forEach(function (embed) { observer.observe(embed.container); });
  } else {
    // Native iframe loading="lazy" remains the fallback for older browsers.
    embeds.forEach(loadEmbed);
  }
})();
