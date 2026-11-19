package cn.moontable.game;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.util.Log;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

// 安卓外壳负责本机 Python 服务、页面加载和系统语音输入，游戏规则在 Python 中统一处理。
public class MainActivity extends Activity implements RecognitionListener {
    private static final ExecutorService backend = Executors.newSingleThreadExecutor();
    private WebView web;
    private SpeechRecognizer recognizer;
    private String localUrl;
    private volatile boolean stopped = false;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.BLACK);
        getWindow().setNavigationBarColor(Color.BLACK);
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        TextView loading = new TextView(this);
        loading.setText("月下圆桌\n正在启动…");
        loading.setTextColor(Color.rgb(221, 199, 150));
        loading.setGravity(android.view.Gravity.CENTER);
        root.addView(loading, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                android.graphics.Insets keyboard = insets.getInsets(WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, keyboard.bottom));
                return insets;
            });
        }
        backend.execute(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                File assets = new File(getCacheDir(), "web");
                copyAssets("web", assets);
                int port = Python.getInstance().getModule("android_server")
                    .callAttr("start", getFilesDir().getAbsolutePath(), assets.getAbsolutePath()).toInt();
                localUrl = "http://127.0.0.1:" + port + "/";
                if (stopped) Python.getInstance().getModule("android_server").callAttr("pause");
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    web = new WebView(this);
                    web.setBackgroundColor(Color.BLACK);
                    web.getSettings().setJavaScriptEnabled(true);
                    web.getSettings().setDomStorageEnabled(true);
                    web.getSettings().setAllowFileAccess(false);
                    web.getSettings().setAllowContentAccess(false);
                    web.getSettings().setMediaPlaybackRequiresUserGesture(false);
                    web.addJavascriptInterface(new VoiceBridge(), "MoonAndroid");
                    web.setWebViewClient(new WebViewClient() {
                        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                            return !request.getUrl().toString().startsWith(localUrl);
                        }
                    });
                    root.removeAllViews();
                    root.addView(web, new FrameLayout.LayoutParams(-1, -1));
                    web.loadUrl(localUrl);
                });
            } catch (Exception error) {
                Log.e("MoonTable", "Backend startup failed", error);
                runOnUiThread(() -> loading.setText("本地服务启动失败，请关闭后重新打开。\n" + error.getClass().getSimpleName()));
            }
        });
    }

    private void copyAssets(String path, File destination) throws Exception {
        String[] children = getAssets().list(path);
        if (children != null && children.length > 0) {
            destination.mkdirs();
            for (String child : children) copyAssets(path + "/" + child, new File(destination, child));
        } else {
            try (InputStream input = getAssets().open(path); FileOutputStream output = new FileOutputStream(destination)) {
                byte[] buffer = new byte[16384];
                int length;
                while ((length = input.read(buffer)) != -1) output.write(buffer, 0, length);
            }
        }
    }

    private void voiceResult(String key, Object value) {
        if (web == null || isDestroyed()) return;
        JSONObject result = new JSONObject();
        try { result.put(key, value); } catch (Exception ignored) { return; }
        web.evaluateJavascript("window.moonVoice && window.moonVoice(" + result + ")", null);
    }

    private void startVoice() {
        if (stopped) return;
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 7);
            return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            voiceResult("error", "手机未安装语音识别服务，请启用系统语音服务或使用文字输入。");
            return;
        }
        if (recognizer == null) {
            recognizer = SpeechRecognizer.createSpeechRecognizer(this);
            recognizer.setRecognitionListener(this);
        }
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN");
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        recognizer.startListening(intent);
    }

    private void stopVoice() {
        if (recognizer != null) recognizer.stopListening();
        voiceResult("recording", false);
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(code, permissions, grants);
        if (code == 7) {
            if (grants.length > 0 && grants[0] == PackageManager.PERMISSION_GRANTED) startVoice();
            else voiceResult("error", "未开启麦克风权限，可继续使用文字输入。");
        }
    }

    public class VoiceBridge {
        // WebView 的桥接调用不在主线程；系统语音识别必须切回 UI 线程。
        @JavascriptInterface public void voice(String action) {
            runOnUiThread(() -> { if ("start".equals(action)) startVoice(); else stopVoice(); });
        }
    }

    @Override protected void onStart() {
        super.onStart();
        stopped = false;
    }

    @Override protected void onStop() {
        stopped = true;
        stopVoice();
        backend.execute(() -> {
            if (Python.isStarted()) Python.getInstance().getModule("android_server").callAttr("pause");
        });
        super.onStop();
    }

    @Override protected void onDestroy() {
        if (recognizer != null) recognizer.destroy();
        if (web != null) {
            web.removeJavascriptInterface("MoonAndroid");
            web.destroy();
        }
        super.onDestroy();
    }

    @Override public void onReadyForSpeech(Bundle params) { voiceResult("recording", true); }
    @Override public void onBeginningOfSpeech() {}
    @Override public void onRmsChanged(float rms) {}
    @Override public void onBufferReceived(byte[] data) {}
    @Override public void onEndOfSpeech() { voiceResult("recording", false); }
    @Override public void onError(int code) {
        voiceResult("recording", false);
        if (code != SpeechRecognizer.ERROR_NO_MATCH && code != SpeechRecognizer.ERROR_SPEECH_TIMEOUT)
            voiceResult("error", "语音识别未完成（" + code + "），请检查系统语音服务和网络。");
    }
    private void recognized(Bundle results) {
        ArrayList<String> words = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        if (words != null && !words.isEmpty()) voiceResult("text", words.get(0));
    }
    @Override public void onResults(Bundle results) { recognized(results); voiceResult("recording", false); }
    @Override public void onPartialResults(Bundle results) { recognized(results); }
    @Override public void onEvent(int type, Bundle params) {}
}
