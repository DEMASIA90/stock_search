(() => {
  const installBtn = document.getElementById('installPwaBtn');
  if (!installBtn) return;
  let deferredPrompt = null;
  const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
  const updateButton = () => { installBtn.hidden = isStandalone(); };
  window.addEventListener('beforeinstallprompt', (event) => { event.preventDefault(); deferredPrompt = event; updateButton(); });
  window.addEventListener('appinstalled', () => { deferredPrompt = null; installBtn.hidden = true; });
  installBtn.addEventListener('click', async () => {
    if (deferredPrompt) {
      deferredPrompt.prompt();
      try { await deferredPrompt.userChoice; } catch (_) {}
      deferredPrompt = null; updateButton(); return;
    }
    if (isIos()) alert('Safari의 공유 버튼 → “홈 화면에 추가”를 선택하세요.');
    else alert('브라우저 메뉴에서 “앱 설치” 또는 “홈 화면에 추가”를 선택하세요.');
  });
  if ('serviceWorker' in navigator && location.protocol === 'https:') {
    window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js', { scope:'./' }).catch(console.warn));
  }
  updateButton();
})();
