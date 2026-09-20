(function () {
  var KEY = 'scNlmTheme';
  function apply(theme) {
    if (theme === 'light') document.documentElement.setAttribute('data-theme', 'light');
    else document.documentElement.removeAttribute('data-theme');
    var b = document.getElementById('themeBtn');
    if (b) b.textContent = theme === 'light' ? '☀' : '🌙';
    try { localStorage.setItem(KEY, theme); } catch (e) {}
  }
  window.scTheme = {
    init: function () { apply(localStorage.getItem(KEY) || 'dark'); },
    toggle: function () {
      var cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
      apply(cur === 'light' ? 'dark' : 'light');
    }
  };
})();