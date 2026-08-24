(() => {
  const SW_PATH = './sw.js';

  if ('serviceWorker' in navigator && location.protocol === 'https:') {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register(SW_PATH, { scope: './' }).catch((error) => {
        console.warn('[PWA] service worker registration failed:', error);
      });
    });
  }

  const installBtn = document.getElementById('installPwaBtn');
  if (!installBtn) return;

  let deferredPrompt = null;

  const isStandalone = () =>
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true;

  const isIos = () => /iphone|ipad|ipod/i.test(window.navigator.userAgent);

  const updateButton = () => {
    const installed = isStandalone();
    installBtn.hidden = installed;
    installBtn.classList.toggle('is-ready', Boolean(deferredPrompt));
    installBtn.title = deferredPrompt
      ? 'DTC를 앱으로 설치합니다.'
      : '설치 창이 뜨지 않으면 브라우저 메뉴의 앱 설치/홈 화면에 추가를 사용하세요.';
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
    if (isStandalone()) {
      installBtn.hidden = true;
      return;
    }

    if (deferredPrompt) {
      const prompt = deferredPrompt;
      deferredPrompt = null;
      try {
        await prompt.prompt();
        await prompt.userChoice;
      } catch (error) {
        console.warn('[PWA] install prompt failed:', error);
      }
      updateButton();
      return;
    }

    if (isIos()) {
      window.alert('iPhone/iPad에서는 Safari의 공유 버튼을 누른 뒤 “홈 화면에 추가”를 선택하세요.');
      return;
    }

    window.alert('설치 창을 바로 열 수 없는 상태입니다. Chrome 우측 상단 ⋮ 메뉴에서 “앱 설치” 또는 “홈 화면에 추가”를 선택하세요.');
  });

  updateButton();
})();
