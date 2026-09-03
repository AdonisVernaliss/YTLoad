(() => {
  let theme = 'dark';
  let language = 'en';
  try {
    if (localStorage.getItem('ytload-theme') === 'light') theme = 'light';
    const saved = localStorage.getItem('ytload-language');
    if (['en', 'de', 'ru'].includes(saved)) language = saved;
  } catch { }
  document.documentElement.dataset.theme = theme;
  document.documentElement.lang = language;
})();
