package com.morninginvest.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

public final class MainActivity extends Activity {
    private static final String PRIMARY_URL = "https://morninginv.web.app/";
    private static final String FALLBACK_URL = "https://demasia90.github.io/stock_search/";

    private WebView webView;
    private boolean fallbackAttempted = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(8, 10, 15));
        webView.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));
        setContentView(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setLoadsImagesAutomatically(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setUserAgentString(settings.getUserAgentString() + " MorningInvestAndroid/10.0.1");
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);

        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        webView.setWebViewClient(new MorningInvestWebViewClient());
        webView.loadUrl(PRIMARY_URL);
    }

    private boolean isInternalHost(String host) {
        if (host == null) return false;
        return host.equalsIgnoreCase("morninginv.web.app")
                || host.equalsIgnoreCase("morninginv.firebaseapp.com")
                || host.equalsIgnoreCase("demasia90.github.io");
    }

    private void openExternal(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (ActivityNotFoundException ignored) {
            // No compatible external app is installed; leave the current page intact.
        }
    }

    private void showOfflinePage() {
        String html = "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<style>body{font-family:sans-serif;background:#080a0f;color:#e5e7eb;display:flex;min-height:100vh;"
                + "align-items:center;justify-content:center;margin:0}.box{padding:28px;text-align:center;max-width:420px}"
                + "a{display:block;margin-top:12px;padding:12px;border:1px solid #374151;border-radius:10px;color:#fff;text-decoration:none}</style>"
                + "</head><body><div class='box'><h2>Morning Invest</h2><p>네트워크 또는 호스팅에 연결할 수 없습니다.</p>"
                + "<a href='" + PRIMARY_URL + "'>Firebase 다시 연결</a>"
                + "<a href='" + FALLBACK_URL + "'>GitHub Pages로 연결</a></div></body></html>";
        webView.loadDataWithBaseURL(PRIMARY_URL, html, "text/html", "UTF-8", null);
    }

    private final class MorningInvestWebViewClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri uri = request.getUrl();
            String scheme = uri.getScheme();
            if (("https".equalsIgnoreCase(scheme) || "http".equalsIgnoreCase(scheme)) && isInternalHost(uri.getHost())) {
                return false;
            }
            openExternal(uri);
            return true;
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            super.onReceivedError(view, request, error);
            if (!request.isForMainFrame()) return;

            String failedUrl = request.getUrl() == null ? "" : request.getUrl().toString();
            if (!fallbackAttempted && failedUrl.startsWith(PRIMARY_URL)) {
                fallbackAttempted = true;
                view.loadUrl(FALLBACK_URL);
                return;
            }
            showOfflinePage();
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.setWebViewClient(null);
            webView.removeAllViews();
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
