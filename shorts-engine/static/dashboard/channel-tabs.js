(function () {
  function navigateSameTab(event) {
    const link = event.target.closest && event.target.closest('a.channel-tab');
    if (!link) return;
    const href = (link.getAttribute('href') || '').trim();
    if (!href || href === '#') return;
    event.preventDefault();
    event.stopPropagation();
    window.location.assign(href);
  }

  document.addEventListener('click', navigateSameTab, true);
})();
