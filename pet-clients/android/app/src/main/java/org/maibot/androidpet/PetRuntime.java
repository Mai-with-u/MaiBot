package org.maibot.androidpet;

import android.content.*;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.webkit.CookieManager;
import android.webkit.HttpAuthHandler;
import android.webkit.WebView;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import org.json.*;

/** Main-thread session owner. Remote pages can publish transport messages only. */
final class PetRuntime {
    interface Reply { void finish(Object value, String error); }
    private static PetRuntime instance;
    static PetRuntime get(Context context) { if (instance == null) instance = new PetRuntime(context.getApplicationContext()); return instance; }
    final Context context;
    final Handler handler = new Handler(Looper.getMainLooper());
    final SharedPreferences prefs;
    final Map<String, Reply> pending = new HashMap<>();
    final Map<String, String> pageCommands = new HashMap<>();
    PetWebView auth, pet, records;
    MainActivity activity;
    PetService service;
    JSONArray bots = new JSONArray();
    String server = "", nonce = "", selected = "", text = "填写 WebUI 地址后连接";
    boolean authenticated, petReady, connected;
    HttpAuthHandler challenge;
    String challengeHost = "", challengeRealm = "";

    private PetRuntime(Context context) {
        this.context = context;
        prefs = context.getSharedPreferences("pet", Context.MODE_PRIVATE);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
    }
    String asset(String name) {
        try (var input = context.getAssets().open(name); var output = new java.io.ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192]; int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        }
        catch (Exception error) { throw new IllegalStateException("缺少桌宠资源：" + name, error); }
    }
    boolean trusted(Uri url) {
        if (server.isEmpty()) return false;
        Uri base = Uri.parse(server);
        String prefix = base.getPath() == null ? "" : base.getPath();
        String path = url.getPath() == null ? "" : url.getPath();
        return PetWebView.origin(base).equals(PetWebView.origin(url)) && (prefix.isEmpty() || path.equals(prefix) || path.startsWith(prefix + "/"));
    }
    void connect(String address) {
        Uri url = Uri.parse(address.trim());
        if (!("https".equals(url.getScheme()) || "http".equals(url.getScheme())) || url.getHost() == null
            || url.getUserInfo() != null || url.getQuery() != null || url.getFragment() != null
            || PetWebView.LOCAL.equals(PetWebView.origin(url))) throw new IllegalArgumentException("请输入完整的 HTTP(S) WebUI 地址，不要包含账号、密码或查询参数");
        stop();
        server = url.toString().replaceAll("/+$", ""); nonce = J.id();
        prefs.edit().putString("server", server).apply();
        auth = new PetWebView(context, this, "auth");
        status("请在 WebUI 中完成登录");
        auth.loadUrl(server + "/auth");
        refreshActivity();
    }
    void select(String botId) {
        if (!authenticated || !botId.matches("[a-f0-9]{32}")) throw new IllegalArgumentException("请先登录并选择机器人");
        boolean found = false;
        for (int i = 0; i < bots.length(); i++) if (bots.optJSONObject(i).optString("bot_id").equals(botId)) found = true;
        if (!found) throw new IllegalArgumentException("机器人不在当前登录列表中");
        selected = botId;
        prefs.edit().putString("bot:" + server, botId).apply();
    }
    JSONObject options() {
        String key = "client:" + server + ":" + selected;
        String client = prefs.getString(key, "");
        if (client.isEmpty()) { client = J.id(); prefs.edit().putString(key, client).apply(); }
        return J.obj("botId", selected, "clientId", client, "allowScreenRead", false);
    }
    void startTransport() { if (authenticated && petReady && !selected.isEmpty()) transport("start", J.arr(options())); }
    void transport(String method, JSONArray args) {
        if (auth == null) return;
        // The nonce prevents a queued invocation from targeting a replacement document/session.
        auth.evaluateJavascript("if(window.__petTransport?.nonce===" + JSONObject.quote(nonce) + ")window.__petTransport."
            + method + "(..." + args + ");", null);
    }
    void status(String value) { text = value; refreshActivity(); if (service != null) service.updateNotification(value); }
    void refreshActivity() { if (activity != null) activity.render(); }
    void basicAuth(HttpAuthHandler handler, String host, String realm) {
        if (challenge != null) challenge.cancel();
        challenge = handler; challengeHost = host; challengeRealm = realm;
        status("代理需要认证，请打开麦麦桌宠登录");
        if (activity != null) activity.showChallenge();
    }
    void openExternal(String value) {
        Uri uri = Uri.parse(value);
        if (!"https".equals(uri.getScheme()) && !"http".equals(uri.getScheme())) throw new IllegalArgumentException("不支持的链接类型");
        context.startActivity(new Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    }
    void receive(PetWebView source, String command, JSONObject payload, Reply reply) throws Exception {
        if (payload == null) throw new IllegalArgumentException("消息格式无效");
        if (source.role.equals("auth")) {
            if (source != auth || !trusted(Uri.parse(source.getUrl())) || !command.equals("transport_message") || !nonce.equals(payload.optString("nonce")))
                throw new SecurityException("远端页面无权调用本机能力");
            remote(payload.getJSONObject("payload")); reply.finish(null, null); return;
        }
        if (source != pet && source != records) throw new SecurityException("窗口已关闭");
        switch (command) {
            case "request": {
                if (!connected) throw new IllegalStateException("桌宠未连接，请等待连接恢复");
                String id = J.id();
                pending.put(id, reply);
                handler.postDelayed(() -> { Reply timedOut = pending.remove(id); if (timedOut != null) timedOut.finish(null, "请求超时，请刷新确认结果"); }, 60000);
                transport("request", J.arr(id, payload.getString("method"), payload.getString("path"), payload.opt("payload"))); return;
            }
            case "event": transport("sendEvent", J.arr(payload)); break;
            case "page_result": {
                String id = payload.getString("id");
                if (source.role.equals(pageCommands.remove(id))) transport("commandResult", J.arr(id, payload.opt("result"), payload.opt("error")));
                break;
            }
            case "page_open_external": openExternal(payload.getString("value")); break;
            case "focus": if (source == pet && service != null) service.focus(payload.optBoolean("editing")); break;
            case "pet_call": nativeCall(source, payload.getString("method"), payload.getJSONArray("args"), reply); return;
            default: throw new SecurityException("不支持的本机操作");
        }
        reply.finish(null, null);
    }
    void nativeCall(PetWebView source, String method, JSONArray args, Reply reply) throws Exception {
        Object result = null;
        if (source == records) {
            switch (method) {
                case "refresh": transport("sendEvent", J.arr(J.obj("type", "exploration_tasks", "task_id", args.opt(0), "request_id", "records:" + args.get(1)))); break;
                case "window_control":
                    if (args.optString(0).equals("close")) { if (RecordsActivity.current != null) RecordsActivity.current.finish(); }
                    result = false; break;
                case "window_gesture": case "window_gesture_end": break;
                default: throw new SecurityException("记录页面无权调用该操作");
            }
            reply.finish(result, null); return;
        }
        switch (method) {
            case "ready": petReady = true; startTransport(); result = true; break;
            case "fit": if (service != null) service.fit(args.getDouble(1)); break;
            case "region": if (service != null) service.region(args.getJSONArray(0)); break;
            case "drag_begin": if (service != null) service.dragBegin(); break;
            case "drag_move": if (service != null) service.dragMove(); break;
            case "drag_end": if (service != null) service.dragEnd(); break;
            case "open_exploration_records": context.startActivity(new Intent(context, RecordsActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); break;
            case "copy_panel_content":
                if (!args.getString(0).equals("text")) throw new IllegalArgumentException("Android 暂只支持复制文字");
                context.getSystemService(ClipboardManager.class).setPrimaryClip(ClipData.newPlainText("麦麦", args.getString(1))); break;
            case "exit_client": handler.post(this::stop); break;
            default: throw new IllegalArgumentException("Android 不支持该操作：" + method);
        }
        reply.finish(result, null);
    }
    private void remote(JSONObject payload) throws Exception {
        switch (payload.getString("type")) {
            case "ready": connected = false; break;
            case "authenticated":
                if (!Uri.parse(auth.getUrl()).getPath().equals(Uri.parse(server + "/api/host/desktop-pet/session").getPath())) {
                    auth.loadUrl(server + "/api/host/desktop-pet/session"); return;
                }
                bots = payload.getJSONArray("bots"); authenticated = true;
                CookieManager.getInstance().flush();
                status("已登录，选择机器人后启动悬浮桌宠");
                startTransport(); break;
            case "auth_required":
                authenticated = false; connected = false;
                status(payload.optString("reason", "请重新登录"));
                if (activity != null && !auth.getUrl().equals(server + "/auth")) auth.loadUrl(server + "/auth");
                break;
            case "state":
                connected = payload.getBoolean("connected"); status(payload.optString("text"));
                if (petReady) dispatch(pet, "state:" + J.id(), J.obj("type", "connection", "connected", connected, "text", text), false);
                break;
            case "response": {
                Reply pendingReply = pending.remove(payload.getString("id"));
                if (pendingReply != null) pendingReply.finish(payload.opt("data"), payload.optBoolean("ok") ? null : payload.optString("error", "请求失败"));
                break;
            }
            case "command": {
                String id = payload.getString("id"); JSONObject action = payload.getJSONObject("command");
                String type = action.getString("type");
                if (type.equals("capture_screen") || type.equals("sample_screen") || type.equals("applications")) {
                    transport("commandResult", J.arr(id, null, "Android 版未启用屏幕读取或应用监测")); return;
                }
                if (!petReady || service == null) { transport("commandResult", J.arr(id, null, "悬浮桌宠未启动")); return; }
                if (type.equals("hide") || type.equals("show")) {
                    boolean show = type.equals("show"); service.setVisible(show); action = J.obj("type", "visibility", "visible", show);
                }
                PetWebView target = type.equals("exploration_tasks") && action.optString("request_id").startsWith("records:") ? records : pet;
                if (target == null) { transport("commandResult", J.arr(id, new JSONObject(), null)); return; }
                dispatch(target, id, action, true); break;
            }
            default: throw new IllegalArgumentException("未知传输消息");
        }
    }
    void dispatch(PetWebView target, String id, JSONObject command, boolean ack) {
        if (ack) {
            pageCommands.put(id, target.role);
            String generation = nonce;
            handler.postDelayed(() -> {
                if (nonce.equals(generation) && pageCommands.remove(id) != null) transport("commandResult", J.arr(id, null, "桌宠页面响应超时"));
            }, 15000);
        }
        target.evaluateJavascript("window.__desktopPetDispatch(" + J.obj("id", id, "command", command) + ");", null);
    }
    void stop() {
        transport("stop", new JSONArray());
        nonce = J.id(); authenticated = false; connected = false; petReady = false; selected = ""; bots = new JSONArray();
        for (Reply reply : pending.values()) reply.finish(null, "连接已关闭");
        pending.clear(); pageCommands.clear();
        if (challenge != null) challenge.cancel(); challenge = null;
        if (service != null) { PetService old = service; service = null; old.shutdown(); }
        if (pet != null) pet.destroy(); pet = null;
        if (records != null) records.destroy(); records = null;
        if (auth != null) auth.destroy(); auth = null;
        if (RecordsActivity.current != null) RecordsActivity.current.finish();
        status("已断开连接");
    }
}
