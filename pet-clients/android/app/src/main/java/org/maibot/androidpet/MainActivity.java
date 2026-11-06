package org.maibot.androidpet;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.widget.*;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private PetRuntime runtime;
    private LinearLayout root, form;
    private FrameLayout browser;
    private EditText address;
    private TextView status;
    private Spinner bots;
    private Button start;
    private String botSignature = "";
    private AlertDialog challengeDialog;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        runtime = PetRuntime.get(this);
        root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(Color.rgb(255, 248, 236));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        form = new LinearLayout(this); form.setOrientation(LinearLayout.VERTICAL); form.setPadding(24, 20, 24, 12);
        TextView title = new TextView(this); title.setText("麦麦桌宠"); title.setTextSize(25); form.addView(title);
        address = new EditText(this); address.setSingleLine(true); address.setHint("https://你的 WebUI 域名");
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setText(runtime.prefs.getString("server", "")); form.addView(address);
        Button connect = new Button(this); connect.setText("通过 WebUI 登录");
        connect.setOnClickListener(v -> { try { runtime.connect(address.getText().toString()); } catch (Exception error) { error(error); } }); form.addView(connect);
        status = new TextView(this); status.setTextSize(14); status.setPadding(0, 8, 0, 8); form.addView(status);
        bots = new Spinner(this); form.addView(bots);
        start = new Button(this); start.setText("启动悬浮桌宠"); start.setOnClickListener(v -> startPet()); form.addView(start);
        Button stop = new Button(this); stop.setText("停止桌宠"); stop.setOnClickListener(v -> runtime.stop()); form.addView(stop);
        root.addView(form);
        browser = new FrameLayout(this); root.addView(browser, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
    }
    @Override protected void onResume() {
        super.onResume(); runtime.activity = this; render();
        if (runtime.challenge != null) showChallenge();
        if (runtime.auth != null && !runtime.authenticated && runtime.auth.getUrl() != null && !runtime.auth.getUrl().endsWith("/auth")) runtime.auth.loadUrl(runtime.server + "/auth");
    }
    @Override protected void onPause() { if (runtime.activity == this) runtime.activity = null; super.onPause(); }
    @Override protected void onDestroy() {
        if (runtime.auth != null && runtime.auth.getParent() == browser) runtime.auth.detach();
        if (challengeDialog != null) challengeDialog.dismiss();
        super.onDestroy();
    }
    void render() {
        if (isFinishing() || root == null) return;
        status.setText(runtime.text);
        start.setEnabled(runtime.authenticated);
        bots.setVisibility(runtime.authenticated ? View.VISIBLE : View.GONE);
        String signature = runtime.bots.toString();
        if (!signature.equals(botSignature)) {
            botSignature = signature;
            String[] names = new String[runtime.bots.length()];
            int selection = 0;
            String saved = runtime.prefs.getString("bot:" + runtime.server, "");
            for (int i = 0; i < names.length; i++) {
                JSONObject bot = runtime.bots.optJSONObject(i); names[i] = bot.optString("name");
                if (saved.equals(bot.optString("bot_id"))) selection = i;
            }
            bots.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, names));
            bots.setSelection(selection);
        }
        if (runtime.auth != null && !runtime.authenticated) {
            if (runtime.auth.getParent() != browser) {
                runtime.auth.detach(); browser.removeAllViews(); browser.addView(runtime.auth, new FrameLayout.LayoutParams(-1, -1));
            }
            runtime.auth.setAlpha(1); browser.setVisibility(View.VISIBLE);
        } else {
            browser.setVisibility(View.GONE);
            if (runtime.service != null) runtime.service.attachTransport();
        }
    }
    private void startPet() {
        try {
            if (runtime.service != null) { runtime.service.setVisible(true); moveTaskToBack(true); return; }
            if (!Settings.canDrawOverlays(this)) {
                new AlertDialog.Builder(this).setTitle("允许悬浮显示").setMessage("桌宠需要“显示在其他应用上层”权限。授权后回到这里，点击启动。")
                    .setPositiveButton("去授权", (dialog, which) -> startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + getPackageName()))))
                    .setNegativeButton("取消", null).show(); return;
            }
            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                && !runtime.prefs.getBoolean("notificationAsked", false)) {
                runtime.prefs.edit().putBoolean("notificationAsked", true).apply();
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 7); return;
            }
            JSONObject bot = runtime.bots.optJSONObject(bots.getSelectedItemPosition());
            if (bot == null) throw new IllegalStateException("当前没有可选机器人");
            runtime.select(bot.getString("bot_id"));
            startForegroundService(new Intent(this, PetService.class));
            moveTaskToBack(true);
        } catch (Exception error) { error(error); }
    }
    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == 7) startPet();
    }
    void showChallenge() {
        if (runtime.challenge == null || isFinishing() || challengeDialog != null && challengeDialog.isShowing()) return;
        android.webkit.HttpAuthHandler challenge = runtime.challenge;
        LinearLayout fields = new LinearLayout(this); fields.setOrientation(LinearLayout.VERTICAL); fields.setPadding(32, 16, 32, 0);
        EditText user = new EditText(this); user.setSingleLine(); user.setHint("代理用户名"); fields.addView(user);
        EditText password = new EditText(this); password.setSingleLine(); password.setHint("代理密码");
        password.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD); fields.addView(password);
        challengeDialog = new AlertDialog.Builder(this).setTitle("代理认证 · " + runtime.challengeHost).setMessage(runtime.challengeRealm).setView(fields)
            .setPositiveButton("登录", (dialog, which) -> {
                if (runtime.challenge == challenge) { runtime.challenge = null; challenge.proceed(user.getText().toString(), password.getText().toString()); }
            }).setNegativeButton("取消", (dialog, which) -> { challenge.cancel(); if (runtime.challenge == challenge) runtime.challenge = null; })
            .setOnCancelListener(dialog -> { challenge.cancel(); if (runtime.challenge == challenge) runtime.challenge = null; }).create();
        challengeDialog.show();
    }
    private void error(Exception error) { new AlertDialog.Builder(this).setMessage(error.getMessage()).setPositiveButton("知道了", null).show(); }
}
