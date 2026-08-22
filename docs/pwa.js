(() => {
  const installBtn = document.getElementById('installPwaBtn');
  if (!installBtn) return;

  let deferredPrompt = null;

  const isStandalone = () =>
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true;

  const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent);

  const updateButton = () => {
    if (isStandalone()) {
      installBtn.hidden = true;
      return;
    }
    installBtn.hidden = !(deferredPrompt || isIos());
  };

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event;
    updateButton();
  });

  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    installBtn.hidden = true;
  });

  installBtn.addEventListener('click', async () => {
    if (deferredPrompt) {
      deferredPrompt.prompt();
      try { await deferredPrompt.userChoice; } catch (_) {}
      deferredPrompt = null;
      updateButton();
      return;
    }

    if (isIos()) {
      alert('iPhone/iPad에서는 Safari의 공유 버튼을 누른 뒤 “홈 화면에 추가”를 선택하세요.');
    }
  });

  if ('serviceWorker' in navigator && location.protocol === 'https:') {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('./sw.js', { scope: './' }).catch((error) => {
        console.warn('DTC service worker registration failed:', error);
      });
    });
  }

  updateButton();
})();
