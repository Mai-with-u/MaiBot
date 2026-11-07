package org.maibot.androidpet;

import android.app.Activity;
import android.os.Bundle;

public final class RecordsActivity extends Activity {
    static RecordsActivity current;
    private PetRuntime runtime;
    private PetWebView page;
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        runtime = PetRuntime.get(this);
        if (runtime.service == null) { finish(); return; }
        current = this;
        page = new PetWebView(this, runtime, "records"); runtime.records = page;
        android.widget.FrameLayout frame = new android.widget.FrameLayout(this);
        frame.addView(page, new android.widget.FrameLayout.LayoutParams(-1, -1));
        frame.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(0, insets.getSystemWindowInsetTop(), 0, insets.getSystemWindowInsetBottom()); return insets;
        });
        setContentView(frame);
        page.loadUrl(PetWebView.LOCAL + "/pet/exploration.html");
    }
    @Override protected void onResume() { super.onResume(); if (runtime != null && runtime.service != null) runtime.service.setVisible(false); }
    @Override protected void onPause() { if (runtime != null && runtime.service != null) runtime.service.setVisible(true); super.onPause(); }
    @Override protected void onDestroy() {
        if (current == this) current = null;
        if (runtime != null && runtime.records == page) { runtime.records = null; page.destroy(); }
        super.onDestroy();
    }
}
