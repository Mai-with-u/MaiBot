package org.maibot.androidpet;

import android.app.*;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.PixelFormat;
import android.graphics.Region;
import android.os.IBinder;
import android.provider.Settings;
import android.view.*;
import android.view.inputmethod.InputMethodManager;
import android.widget.FrameLayout;
import org.json.JSONArray;

public final class PetService extends Service {
    private PetRuntime runtime;
    private WindowManager windows;
    private FrameLayout frame;
    private WindowManager.LayoutParams params;
    private float density, rawX, rawY, downX, downY;
    private int fullHeight, clipTop, bottom, dragX, dragBottom;
    private int keyboardTop = Integer.MAX_VALUE;
    private boolean dragging, moved, closing;
    private String gesture;
    private final boolean touchRegions = android.os.Build.VERSION.SDK_INT >= 33;
    private static final String CHANNEL = "companion";

    @Override public void onCreate() {
        super.onCreate();
        runtime = PetRuntime.get(this);
        getSystemService(NotificationManager.class).createNotificationChannel(new NotificationChannel(CHANNEL, "悬浮桌宠", NotificationManager.IMPORTANCE_LOW));
        startForeground(1, notification("桌宠启动中"));
        if (!Settings.canDrawOverlays(this) || !runtime.authenticated || runtime.selected.isEmpty()) { stopSelf(); return; }
        runtime.service = this;
        windows = getSystemService(WindowManager.class);
        density = getResources().getDisplayMetrics().density;
        fullHeight = Math.min(dp(900), getResources().getDisplayMetrics().heightPixels);
        bottom = Math.max(dp(180), getResources().getDisplayMetrics().heightPixels - dp(80));
        frame = new FrameLayout(this); frame.setClipChildren(true);
        params = new WindowManager.LayoutParams(dp(260), dp(180), WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL | WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH,
            PixelFormat.TRANSLUCENT);
        // Android 13+ 可直接设置触摸区域；保持画布固定，避免窗口边界与 WebView
        // 重绘分帧提交，导致展开菜单时桌宠先跳动再回到原位。
        if (touchRegions) {
            params.height = fullHeight;
            // 系统按整个悬浮窗计算遮挡透明度；透明触摸区域也必须符合穿透上限。
            params.alpha = getSystemService(android.hardware.input.InputManager.class).getMaximumObscuringOpacityForTouch();
            params.flags |= WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS;
        }
        params.gravity = Gravity.TOP | Gravity.LEFT;
        params.x = Math.max(0, getResources().getDisplayMetrics().widthPixels - params.width);
        params.y = touchRegions ? bottom - params.height : Math.max(0, bottom - params.height);
        params.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE;
        runtime.pet = new PetWebView(this, runtime, "pet");
        FrameLayout.LayoutParams pageLayout = new FrameLayout.LayoutParams(dp(260), fullHeight);
        clipTop = fullHeight - params.height; pageLayout.topMargin = -clipTop;
        frame.addView(runtime.pet, pageLayout);
        frame.setOnApplyWindowInsetsListener((view, insets) -> {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                boolean visible = insets.isVisible(WindowInsets.Type.ime());
                int overlap = insets.getInsets(WindowInsets.Type.ime()).bottom;
                // Insets are relative to this floating window, not the whole display. Once it
                // moves clear of the keyboard the overlap becomes zero; retain the known top
                // until the IME is hidden, rather than oscillating between both positions.
                int nextTop = !visible ? Integer.MAX_VALUE : overlap > 0 ? params.y + params.height - overlap : keyboardTop;
                if (keyboardTop != nextTop) { keyboardTop = nextTop; place(); }
            }
            return insets;
        });
        if (android.os.Build.VERSION.SDK_INT < 30) {
            // Before typed IME insets, Android exposes the keyboard-free display rectangle.
            frame.getViewTreeObserver().addOnGlobalLayoutListener(() -> {
                android.graphics.Rect visible = new android.graphics.Rect();
                android.util.DisplayMetrics display = new android.util.DisplayMetrics();
                int[] location = new int[2];
                frame.getWindowVisibleDisplayFrame(visible); frame.getLocationOnScreen(location);
                windows.getDefaultDisplay().getRealMetrics(display);
                int nextTop = display.heightPixels - visible.bottom > dp(100)
                    ? visible.bottom - (location[1] - params.y) : Integer.MAX_VALUE;
                if (keyboardTop != nextTop) { keyboardTop = nextTop; place(); }
            });
        }
        frame.setOnTouchListener((view, event) -> { if (event.getActionMasked() == MotionEvent.ACTION_OUTSIDE) focus(false); return false; });
        runtime.pet.setOnTouchListener((view, event) -> {
            rawX = event.getRawX(); rawY = event.getRawY();
            if (event.getActionMasked() == MotionEvent.ACTION_DOWN) { downX = rawX; downY = rawY; }
            if (event.getActionMasked() == MotionEvent.ACTION_UP || event.getActionMasked() == MotionEvent.ACTION_CANCEL)
                runtime.pet.evaluateJavascript("document.activeElement.matches('input,textarea,[contenteditable=true]')", editing -> { if ("false".equals(editing)) focus(false); });
            return false;
        });
        runtime.pet.setOnKeyListener((view, code, event) -> {
            if (code == KeyEvent.KEYCODE_BACK && event.getAction() == KeyEvent.ACTION_UP) { focus(false); return true; } return false;
        });
        windows.addView(frame, params);
        attachTransport();
        runtime.pet.loadUrl(PetWebView.LOCAL + "/pet/index.html");
    }
    private int dp(double value) { return (int) Math.round(value * density); }
    void attachTransport() {
        if (frame == null || runtime.auth == null || !runtime.authenticated) return;
        if (runtime.auth.getParent() != frame) {
            runtime.auth.detach(); frame.addView(runtime.auth, 0, new FrameLayout.LayoutParams(1, 1));
        }
        runtime.auth.setAlpha(0);
    }
    void fit(double cssHeight) {
        if (!Double.isFinite(cssHeight) || cssHeight < 1 || cssHeight > 2000) throw new IllegalArgumentException("无效窗口高度");
        fullHeight = dp(Math.max(460, Math.min(900, cssHeight)));
        if (touchRegions) params.height = fullHeight;
        FrameLayout.LayoutParams layout = (FrameLayout.LayoutParams) runtime.pet.getLayoutParams();
        layout.height = fullHeight; clipTop = Math.max(0, fullHeight - params.height); layout.topMargin = -clipTop;
        runtime.pet.setLayoutParams(layout);
        if (touchRegions) place();
    }
    void region(JSONArray rects) throws Exception {
        if (rects.length() > 256) throw new IllegalArgumentException("交互区域过多");
        Region touchable = new Region();
        double first = fullHeight / density;
        for (int i = 0; i < rects.length(); i++) {
            JSONArray rect = rects.getJSONArray(i);
            if (rect.length() != 5) throw new IllegalArgumentException("交互区域无效");
            for (int j = 0; j < 5; j++) if (!Double.isFinite(rect.getDouble(j)) || Math.abs(rect.getDouble(j)) > 10000) throw new IllegalArgumentException("交互坐标无效");
            if (rect.getDouble(2) > 0 && rect.getDouble(3) > 0) {
                first = Math.min(first, rect.getDouble(1));
                touchable.op(dp(rect.getDouble(0)), dp(rect.getDouble(1)),
                    dp(rect.getDouble(0) + rect.getDouble(2)), dp(rect.getDouble(1) + rect.getDouble(3)), Region.Op.UNION);
            }
        }
        if (touchRegions) {
            frame.getRootSurfaceControl().setTouchableRegion(touchable);
            return;
        }
        int top = Math.max(0, Math.min(fullHeight - dp(1), dp(first)));
        if (top == clipTop) return;
        // Clip the unused upper canvas so the idle pet does not intercept the entire phone screen.
        // Native overlay bounds remain rectangular, with outside touches passed to the app underneath.
        clipTop = top;
        params.height = fullHeight - clipTop;
        FrameLayout.LayoutParams layout = (FrameLayout.LayoutParams) runtime.pet.getLayoutParams();
        layout.topMargin = -clipTop; runtime.pet.setLayoutParams(layout);
        place();
    }
    private void place() {
        int width = getResources().getDisplayMetrics().widthPixels, height = getResources().getDisplayMetrics().heightPixels;
        params.x = Math.max(0, Math.min(params.x, width - params.width));
        bottom = Math.max(Math.min(touchRegions ? dp(180) : params.height, height), Math.min(bottom, height));
        params.y = Math.min(bottom, keyboardTop) - params.height;
        if (!touchRegions) params.y = Math.max(0, params.y);
        if (frame != null && frame.isAttachedToWindow()) windows.updateViewLayout(frame, params);
    }
    void focus(boolean editing) {
        if (frame == null || !frame.isAttachedToWindow()) return;
        if (editing) params.flags &= ~WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
        else {
            params.flags |= WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
            getSystemService(InputMethodManager.class).hideSoftInputFromWindow(frame.getWindowToken(), 0);
        }
        windows.updateViewLayout(frame, params);
    }
    void dragBegin() { dragging = true; moved = false; dragX = params.x; dragBottom = bottom; gesture = J.id(); }
    void dragMove() {
        if (!dragging) return;
        if (!moved && Math.hypot(rawX - downX, rawY - downY) >= dp(4)) {
            moved = true; runtime.transport("sendEvent", J.arr(J.obj("type", "drag_started", "gesture_id", gesture)));
        }
        if (moved) { params.x = dragX + Math.round(rawX - downX); bottom = dragBottom + Math.round(rawY - downY); place(); }
    }
    void dragEnd() {
        if (dragging && moved) runtime.transport("sendEvent", J.arr(J.obj("type", "drag_ended", "gesture_id", gesture)));
        dragging = false;
    }
    void setVisible(boolean visible) { if (frame != null) frame.setVisibility(visible ? View.VISIBLE : View.GONE); }
    private Notification notification(String text) {
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 1, new Intent(this, PetService.class).setAction("stop"), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL).setSmallIcon(org.maibot.androidpet.R.drawable.pet_icon).setContentTitle("麦麦桌宠")
            .setContentText(text).setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true).addAction(new Notification.Action.Builder(null, "停止", stop).build()).build();
    }
    void updateNotification(String text) { if (!closing) getSystemService(NotificationManager.class).notify(1, notification(text)); }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && "stop".equals(intent.getAction())) runtime.stop();
        return START_NOT_STICKY;
    }
    @Override public void onConfigurationChanged(Configuration configuration) { super.onConfigurationChanged(configuration); if (frame != null) place(); }
    void shutdown() {
        closing = true;
        if (frame != null && frame.isAttachedToWindow()) windows.removeView(frame);
        frame = null; stopForeground(STOP_FOREGROUND_REMOVE); stopSelf();
    }
    @Override public void onDestroy() {
        if (runtime != null && runtime.service == this) runtime.stop();
        else if (!closing) shutdown();
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
