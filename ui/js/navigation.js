/* One header across the demo and company onboarding. */
(function () {
  SO.renderNavigation = function () {
    const company = location.pathname.startsWith('/company/');
    const here = location.pathname.split('/').pop();
    const nav = document.querySelector('.nav');
    const brand = document.querySelector('.mark');
    if (brand) brand.href = '/start.html';
    if (!nav) return;
    const links = [
      ['/queue.html?track=stage&present=1', 'Review queue', !company && here === 'queue.html'],
      ['/playbook.html?client=A&track=stage&period=2026-05&present=1', 'Learned policies', !company && here === 'playbook.html'],
      ['/real.html?track=stage&present=1', 'Benchmark', here === 'real.html'],
      ['/company/index.html?track=main', 'Your company', company]
    ];
    nav.innerHTML = links.map(([url, label, active]) => `<a href="${url}"${active ? ' aria-current="page"' : ''}>${label}</a>`).join('');
    const more = document.createElement('details'); more.className = 'demo-more';
    more.innerHTML = '<summary>More</summary><div>' + [
      ['/index.html?track=stage&present=1', 'Experiment'],
      ['/index.html?track=stage&present=1#control', 'Bank change'],
      ['/close.html?track=stage&period=2026-05&present=1', 'Close'],
      ['/audit.html?track=stage&present=1', 'Audit'],
      ['/results.html?track=stage&present=1', 'Results'],
      ['/presenter.html', 'Presenter guide'],
      ['/recorded-demo.html', 'Recorded fallback'],
      ['/start.html', 'Choose demo or company setup']
    ].map(([url,label])=>`<a href="${url}">${label}</a>`).join('') + '</div>';
    nav.append(more);
  };
})();
