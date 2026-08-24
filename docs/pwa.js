(() => {
  if ('serviceWorker' in navigator && location.protocol === 'https:') {
    window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js', { scope:'./' }).catch(console.warn));
  }
  const installBtn = document.getElementById('installPwaBtn');
  if (!installBtn) return;
  let deferredPrompt = null;
  const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const updateButton = () => { installBtn.hidden = isStandalone(); };
  window.addEventListener('beforeinstallprompt', (event) => { event.preventDefault(); deferredPrompt = event; updateButton(); });
  window.addEventListener('appinstalled', () => { deferredPrompt = null; installBtn.hidden = true; });
  installBtn.addEventListener('click', async () => { if (deferredPrompt) { deferredPrompt.prompt(); try { await deferredPrompt.userChoice; } catch (_) {} deferredPrompt = null; updateButton(); } });
  updateButton();
})();
