package org.maibot.androidpet;

import android.annotation.SuppressLint;
import android.content.Context;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.webkit.*;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.json.JSONObject;

/** Only a top-level, origin-bound MessagePort can call the native adapter. */
final class PetWebView extends WebView {
    static final String LOCAL = "https://appassets.androidplatform.net";
    final String role;
    final PetRuntime runtime;
    private WebMessagePort channel;
    private int document;

    @SuppressLint("SetJavaScriptEnabled")
    PetWebView(Context context, PetRuntime runtime, String role) {
        super(context);
        this.runtime = runtime;
        this.role = role;
        setBackgroundColor(Color.TRANSPARENT);
        setOverScrollMode(OVER_SCROLL_NEVER);
        getSettings().setJavaScriptEnabled(true);
        getSettings().setDomStorageEnabled(true);
        getSettings().setAllowFileAccess(false);
        getSettings().setAllowContentAccess(false);
        getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        getSettings().setTextZoom(100);
        getSettings().setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptThirdPartyCookies(this, false);
        setWebChromeClient(new WebChromeClient() {
            @Override public void onPermissionRequest(PermissionRequest request) { request.deny(); }
            @Override public boolean onConsoleMessage(ConsoleMessage message) {
                if (BuildConfig.DEBUG) android.util.Log.d("MaiBotWeb", role + ": " + message.message());
                return true;
            }
        });
        setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                if (role.equals("auth")) return null;
                Uri url = request.getUrl();
                if (!LOCAL.equals(origin(url))) return blocked();
                String path = url.getPath();
                if (path == null || path.contains("..") || !path.matches("/(pet|android)/[A-Za-z0-9_./-]+")) return blocked();
                try {
                    if (path.endsWith(".woff2")) return new WebResourceResponse("font/woff2", null, getContext().getAssets().open(path.substring(1)));
                    byte[] bytes = runtime.asset(path.substring(1)).getBytes(StandardCharsets.UTF_8);
                    String mime = path.endsWith(".html") ? "text/html" : path.endsWith(".css") ? "text/css" : "application/javascript";
                    Map<String, String> headers = new java.util.HashMap<>();
                    headers.put("Content-Security-Policy", "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https: http:; font-src 'self'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'");
                    headers.put("Cache-Control", "no-store");
                    return new WebResourceResponse(mime, "UTF-8", 200, "OK", headers, new ByteArrayInputStream(bytes));
                } catch (Exception error) { return blocked(); }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if (role.equals("auth") && runtime.trusted(url)) return false;
                // An external redirect never inherits a native port or a local page's privileges.
                if (request.isForMainFrame() && request.hasGesture()) runtime.openExternal(url.toString());
                return true;
            }
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                document++;
                if (channel != null) { channel.close(); channel = null; }
            }
            @Override public void onPageFinished(WebView view, String url) {
                if (role.equals("auth") ? !runtime.trusted(Uri.parse(url)) : !LOCAL.equals(origin(Uri.parse(url)))) return;
                int revision = document;
                String source = role.equals("auth") ? runtime.asset("android/channel.js")
                    + "\nwindow.__petTransportConfig=" + J.obj("server", runtime.server, "nonce", runtime.nonce) + ";\n"
                    + runtime.asset("android/transport.js") : "void 0";
                evaluateJavascript(source, ignored -> {
                    if (revision != document || channel != null) return;
                    WebMessagePort[] ports = createWebMessageChannel();
                    channel = ports[0];
                    channel.setWebMessageCallback(new WebMessagePort.WebMessageCallback() {
                        @Override public void onMessage(WebMessagePort port, WebMessage message) {
                            if (revision != document || channel != port) return;
                            String id = "";
                            try {
                                String data = message.getData();
                                if (data == null || data.length() > 65 * 1024 * 1024) throw new IllegalArgumentException("消息过大");
                                JSONObject call = new JSONObject(data);
                                id = call.getString("id");
                                final String replyId = id;
                                runtime.receive(PetWebView.this, call.getString("command"), call.optJSONObject("payload"), (result, error) -> {
                                    if (revision == document && channel == port)
                                        port.postMessage(new WebMessage(J.obj("id", replyId, "result", result, "error", error).toString()));
                                });
                            } catch (Exception error) {
                                port.postMessage(new WebMessage(J.obj("id", id, "error", error.getMessage()).toString()));
                            }
                        }
                    });
                    postWebMessage(new WebMessage("maibot-native-channel", new WebMessagePort[]{ports[1]}), Uri.parse(origin(Uri.parse(url))));
                });
            }
            @Override public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) {
                if (role.equals("auth") && runtime.trusted(Uri.parse(view.getUrl()))) runtime.basicAuth(handler, host, realm);
                else handler.cancel();
            }
            @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel(); runtime.status("HTTPS 证书无效，请修复服务器证书");
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) runtime.status("页面加载失败：" + error.getDescription());
            }
            @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                runtime.status("系统已回收网页进程，请重新连接桌宠");
                runtime.stop(); return true;
            }
        });
    }

    static String origin(Uri uri) {
        String scheme = uri.getScheme(), host = uri.getHost();
        if (scheme == null || host == null) return "";
        int port = uri.getPort();
        return scheme.toLowerCase(java.util.Locale.ROOT) + "://" + host.toLowerCase(java.util.Locale.ROOT)
            + (port == -1 || port == 443 && scheme.equals("https") || port == 80 && scheme.equals("http") ? "" : ":" + port);
    }
    static WebResourceResponse blocked() { return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", java.util.Collections.emptyMap(), new ByteArrayInputStream(new byte[0])); }
    void detach() { if (getParent() instanceof android.view.ViewGroup parent) parent.removeView(this); }
    @Override public void destroy() { document++; if (channel != null) channel.close(); channel = null; detach(); super.destroy(); }
}
