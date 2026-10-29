package com.guptamanas8.sketchboard;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Shows the live sketch board served by the phone running tv.sh's remote.
 * The phone sends its board URL once (adb am start --es url ...); after that
 * the app opens it straight from the home screen.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "board";
    private static final String KEY_URL = "url";

    private WebView web;
    private String boardUrl;
    private final Handler retry = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false); // game sounds play without a key press
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return true; // the board never navigates away
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showHelp("Can't reach your phone at " + boardUrl
                            + ". Make sure the remote is running in Termux and both are on the same Wi-Fi. Retrying…");
                    retry.removeCallbacksAndMessages(null);
                    retry.postDelayed(MainActivity.this::load, 5000);
                }
            }
        });
        setContentView(web);
        handle(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handle(intent);
    }

    private void handle(Intent intent) {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String sent = intent == null ? null : intent.getStringExtra(KEY_URL);
        if (isBoardUrl(sent)) {
            prefs.edit().putString(KEY_URL, sent).apply();
        }
        boardUrl = prefs.getString(KEY_URL, null);
        load();
    }

    private void load() {
        if (boardUrl == null) {
            showHelp("Open the TV remote on your phone, go to Sketch and tap \"Show board on TV\".");
        } else {
            web.loadUrl(boardUrl);
        }
    }

    /** Only http://<private-network-address>:<port>/board, so other apps can't point it elsewhere. */
    static boolean isBoardUrl(String url) {
        if (url == null) return false;
        Uri u = Uri.parse(url);
        String host = u.getHost();
        return "http".equals(u.getScheme()) && "/board".equals(u.getPath()) && host != null
                && (host.startsWith("192.168.") || host.startsWith("10.")
                || host.matches("172\\.(1[6-9]|2[0-9]|3[01])\\..*"));
    }

    private void showHelp(String message) {
        String html = "<html><body style='margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
                + "background:#14161a;color:#eef1f5;font:4vh system-ui,sans-serif;text-align:center;padding:0 10vw'>"
                + "<div><div style='font-size:7vh;font-weight:700;margin-bottom:3vh'>Sketch Board</div>"
                + android.text.TextUtils.htmlEncode(message) + "</div></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_ENTER) {
            retry.removeCallbacksAndMessages(null);
            load(); // OK on the remote reloads the board
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        retry.removeCallbacksAndMessages(null);
        web.destroy();
        super.onDestroy();
    }
}
