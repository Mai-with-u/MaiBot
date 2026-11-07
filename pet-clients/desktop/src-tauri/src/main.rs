#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod native;
mod windows;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};
use tauri::{Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tokio::sync::oneshot;
use windows::{PetWindowController, RecordsWindowController};

type Reply = Result<Value, String>;
type Sender = oneshot::Sender<Reply>;
const BRIDGE: &str = include_str!("../../renderer/bridge.js");
const TRANSPORT: &str = include_str!("../../renderer/transport.js");

#[derive(Clone)]
struct Auth {
    label: String,
    server: String,
    nonce: String,
    bots: Vec<Value>,
    connected: bool,
}

#[derive(Clone)]
struct Session {
    generation: String,
    key: String,
    options: Value,
    native: Arc<native::NativeCapabilities>,
    frontend_ready: bool,
}

struct Pending {
    target: String,
    sender: Sender,
}

struct State {
    busy: bool,
    settings: Value,
    directory: PathBuf,
    auth: Option<Auth>,
    discovery: Option<Sender>,
    session: Option<Session>,
    pet: Option<Arc<PetWindowController>>,
    records: Option<Arc<RecordsWindowController>>,
    pending: HashMap<String, Pending>,
}

type Shared = Mutex<State>;
struct ConnectionChange(tauri::AppHandle);
impl Drop for ConnectionChange {
    fn drop(&mut self) {
        self.0.state::<Shared>().lock().unwrap().busy = false;
    }
}

#[cfg(windows)]
async fn show_proxy_challenges(window: &WebviewWindow) -> Result<(), String> {
    use webview2_com::{
        BasicAuthenticationRequestedEventHandler, Microsoft::Web::WebView2::Win32::ICoreWebView2_10,
    };
    use windows_core::Interface;
    let show = window.clone();
    let (sender, receiver) = oneshot::channel();
    window
        .with_webview(move |webview| {
            // Only reveal the browser's own challenge dialog; credentials never enter Rust.
            let result = (|| -> windows_core::Result<()> {
                unsafe {
                    let core: ICoreWebView2_10 = webview.controller().CoreWebView2()?.cast()?;
                    let handler =
                        BasicAuthenticationRequestedEventHandler::create(Box::new(move |_, _| {
                            if let Err(e) = show.show() {
                                report(e);
                            }
                            Ok(())
                        }));
                    let mut token = 0;
                    core.add_BasicAuthenticationRequested(&handler, &mut token)?;
                }
                Ok(())
            })()
            .map_err(error);
            let _ = sender.send(result);
        })
        .map_err(error)?;
    receiver.await.map_err(error)?
}
fn id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}
fn error(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn report(e: impl std::fmt::Display) {
    eprintln!("{e}");
}
fn hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn normalize_server(value: &str) -> Result<String, String> {
    if value.contains('\\') {
        return Err("服务器地址不能包含反斜杠".into());
    }
    let url = tauri::Url::parse(value.trim()).map_err(|_| "服务器地址无效")?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("请填写不含密码、查询参数或锚点的 HTTP(S) 地址".into());
    }
    Ok(url.as_str().trim_end_matches('/').to_string())
}

fn local(window: &WebviewWindow, role: &str) -> Result<(), String> {
    let url = window.url().map_err(error)?;
    let expected = match role {
        "login" => "/login/login.html",
        "pet" => "/pet/index.html",
        "records" => "/pet/exploration.html",
        _ => return Err("未知窗口".into()),
    };
    let trusted = url.scheme() == "tauri" && url.host_str() == Some("localhost")
        || url.scheme() == "http" && url.host_str() == Some("tauri.localhost");
    if window.label() != role || !trusted || url.path() != expected {
        return Err("此页面无权调用该接口".into());
    }
    Ok(())
}

fn local_window(
    app: &tauri::AppHandle,
    role: &str,
    title: &str,
    width: f64,
    height: f64,
) -> Result<WebviewWindow, String> {
    let path = match role {
        "login" => "login/login.html",
        "pet" => "pet/index.html",
        _ => "pet/exploration.html",
    };
    let mut builder = WebviewWindowBuilder::new(app, role, WebviewUrl::App(path.into()))
        .title(title)
        .inner_size(width, height)
        .initialization_script(BRIDGE)
        .data_directory(
            app.state::<Shared>()
                .lock()
                .unwrap()
                .directory
                .join("local-webview"),
        )
        .background_throttling(tauri::utils::config::BackgroundThrottlingPolicy::Disabled)
        .visible(false)
        .on_navigation(move |url| {
            (url.scheme() == "tauri" && url.host_str() == Some("localhost")
                || url.scheme() == "http" && url.host_str() == Some("tauri.localhost"))
                && url.path() == format!("/{path}")
        })
        .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny);
    builder = builder
        .on_permission_request(|_, _| tauri::webview::PermissionResponse::Deny)
        .on_download(|_, _| false);
    if role == "pet" {
        builder = builder
            .transparent(true)
            .decorations(false)
            .always_on_top(true)
            .skip_taskbar(true)
            .shadow(false)
            .resizable(false);
    } else if role == "records" {
        builder = builder.decorations(false).min_inner_size(680.0, 480.0);
    } else {
        builder = builder.resizable(false);
    }
    builder.build().map_err(error)
}

fn show_login(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("login") {
        window.show().map_err(error)?;
        window.set_focus().map_err(error)?;
        return Ok(());
    }
    let window = local_window(app, "login", "连接桌宠 · MaiBot", 400.0, 300.0)?;
    let handle = app.clone();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::CloseRequested { .. })
            && handle.state::<Shared>().lock().unwrap().session.is_none()
        {
            handle.exit(0);
        }
    });
    window.show().map_err(error)
}

async fn save_settings(app: &tauri::AppHandle) -> Result<(), String> {
    static WRITES: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());
    let _guard = WRITES.lock().await;
    let (path, content) = {
        let state = app.state::<Shared>();
        let state = state.lock().unwrap();
        (
            state.directory.join("settings.json"),
            serde_json::to_vec_pretty(&state.settings).map_err(error)?,
        )
    };
    // 写盘放到线程池；所有设置写入由一个异步锁串行化，避免拖动与切换同时覆盖临时文件。
    tauri::async_runtime::spawn_blocking(move || {
        let temp = path.with_extension("json.tmp");
        std::fs::write(&temp, content).map_err(error)?;
        std::fs::rename(temp, path).map_err(error)
    })
    .await
    .map_err(error)?
}

fn transport_eval(app: &tauri::AppHandle, method: &str, args: Value) -> Result<(), String> {
    let auth = app
        .state::<Shared>()
        .lock()
        .unwrap()
        .auth
        .as_ref()
        .ok_or("请先通过 WebUI 登录")?
        .clone();
    transport_for(app, &auth, method, args)
}

fn transport_for(
    app: &tauri::AppHandle,
    auth: &Auth,
    method: &str,
    args: Value,
) -> Result<(), String> {
    let window = app
        .get_webview_window(&auth.label)
        .ok_or("认证窗口已关闭")?;
    window
        .eval(format!(
            "if(window.__petTransport?.nonce==={}) window.__petTransport[{method:?}](...{args});",
            json!(auth.nonce)
        ))
        .map_err(error)
}

async fn wait_reply(
    app: &tauri::AppHandle,
    request: String,
    receiver: oneshot::Receiver<Reply>,
    seconds: u64,
) -> Reply {
    let result = tokio::time::timeout(Duration::from_secs(seconds), receiver).await;
    app.state::<Shared>()
        .lock()
        .unwrap()
        .pending
        .remove(&request);
    match result {
        Ok(Ok(value)) => value,
        Ok(Err(_)) => Err("连接已切换，请确认操作结果".into()),
        Err(_) => Err("请求超时，结果尚未确认；请刷新后再操作".into()),
    }
}

async fn page(app: &tauri::AppHandle, role: &str, command: Value) -> Reply {
    let window = app.get_webview_window(role).ok_or("桌宠页面已关闭")?;
    let request = id();
    let (sender, receiver) = oneshot::channel();
    app.state::<Shared>().lock().unwrap().pending.insert(
        request.clone(),
        Pending {
            target: role.into(),
            sender,
        },
    );
    let script = format!(
        "window.__desktopPetDispatch({});",
        json!({"id":request,"command":command})
    );
    if let Err(e) = window.eval(script) {
        app.state::<Shared>()
            .lock()
            .unwrap()
            .pending
            .remove(&request);
        return Err(error(e));
    }
    wait_reply(app, request, receiver, 15).await
}

async fn request(app: &tauri::AppHandle, method: &str, path: &str, payload: Value) -> Reply {
    let request = id();
    let (sender, receiver) = oneshot::channel();
    let auth = {
        let state = app.state::<Shared>();
        let mut state = state.lock().unwrap();
        let auth = state
            .auth
            .as_ref()
            .filter(|auth| auth.connected)
            .ok_or("桌宠未连接，请等待连接恢复")?
            .clone();
        let label = auth.label.clone();
        if state.pending.len() >= 128 {
            return Err("桌宠请求过多".into());
        }
        state.pending.insert(
            request.clone(),
            Pending {
                target: label,
                sender,
            },
        );
        auth
    };
    if let Err(e) = transport_for(
        app,
        &auth,
        "request",
        json!([request, method, path, payload]),
    ) {
        app.state::<Shared>()
            .lock()
            .unwrap()
            .pending
            .remove(&request);
        return Err(e);
    }
    wait_reply(app, request, receiver, 65).await
}

fn send_event(app: &tauri::AppHandle, event: Value) -> Reply {
    let auth = app
        .state::<Shared>()
        .lock()
        .unwrap()
        .auth
        .as_ref()
        .filter(|auth| auth.connected)
        .cloned()
        .ok_or("桌宠未连接服务器")?;
    transport_for(app, &auth, "sendEvent", json!([event]))?;
    Ok(Value::Null)
}

fn authenticated_window(window: &WebviewWindow, nonce: &str) -> Result<Auth, String> {
    let url = window.url().map_err(error)?;
    let state = window.state::<Shared>();
    let state = state.lock().unwrap();
    let auth = state.auth.as_ref().ok_or("没有进行中的认证")?;
    let base = tauri::Url::parse(&auth.server).map_err(error)?;
    let root = base.path().trim_end_matches('/');
    if window.label() != auth.label
        || nonce != auth.nonce
        || url.origin() != base.origin()
        || !root.is_empty() && url.path() != root && !url.path().starts_with(&format!("{root}/"))
    {
        return Err("认证来源不匹配".into());
    }
    Ok(auth.clone())
}

async fn discover(app: &tauri::AppHandle, server: String) -> Reply {
    let server = normalize_server(&server)?;
    let (sender, receiver) = oneshot::channel();
    let label = format!("auth-{}", id());
    let nonce = id();
    let previous = {
        let state = app.state::<Shared>();
        let mut state = state.lock().unwrap();
        if state.session.is_some() {
            return Err("请先切换连接".into());
        }
        if let Some(pending) = state.discovery.take() {
            let _ = pending.send(Err("登录已取消".into()));
        }
        let old = state.auth.take();
        state.discovery = Some(sender);
        state.auth = Some(Auth {
            label: label.clone(),
            nonce: nonce.clone(),
            server: server.clone(),
            bots: vec![],
            connected: false,
        });
        old
    };
    if let Some(previous) = previous {
        if let Some(window) = app.get_webview_window(&previous.label) {
            window.destroy().map_err(error)?;
        }
    }
    let url = tauri::Url::parse(&(server.clone() + "/auth")).map_err(error)?;
    let origin = url.origin().ascii_serialization();
    // 远端只获得一个传输回调；文件、剪贴板、屏幕和通用桌宠 IPC 均不授权。
    app.add_capability(
        json!({"identifier":label,"windows":[label],"local":false,
        "remote":{"urls":[format!("{origin}/*")]},"permissions":["allow-transport-message"]})
        .to_string(),
    )
    .map_err(error)?;
    let directory = app
        .state::<Shared>()
        .lock()
        .unwrap()
        .directory
        .join("web-sessions")
        .join(format!("{:x}", Sha256::digest(server.as_bytes())));
    let create_directory = directory.clone();
    tauri::async_runtime::spawn_blocking(move || std::fs::create_dir_all(create_directory))
        .await
        .map_err(error)?
        .map_err(error)?;
    let script = format!(
        "window.__petTransportConfig={};\n{TRANSPORT}",
        json!({"nonce":nonce,"server":server})
    );
    let mut builder = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .title("登录 WebUI · MaiBot")
        .inner_size(920.0, 720.0)
        .data_directory(directory)
        .background_throttling(tauri::utils::config::BackgroundThrottlingPolicy::Disabled)
        .initialization_script(&script)
        .on_navigation(|url| matches!(url.scheme(), "http" | "https"))
        .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
        .on_permission_request(|_, _| tauri::webview::PermissionResponse::Deny)
        .on_download(|_, _| false);
    // WKWebView 的持久存储由系统管理，Cookie 保持同源隔离。
    #[cfg(target_os = "macos")]
    {
        let digest = Sha256::digest(server.as_bytes());
        let mut store = [0_u8; 16];
        store.copy_from_slice(&digest[..16]);
        builder = builder.data_store_identifier(store);
    }
    let loaded_script = script.clone();
    builder = builder.on_page_load(move |window, payload| {
        if matches!(payload.event(), tauri::webview::PageLoadEvent::Finished) {
            if let Err(e) = window.eval(&loaded_script) {
                report(e);
            }
        }
    });
    let window = match builder.build() {
        Ok(window) => window,
        Err(e) => {
            app.state::<Shared>().lock().unwrap().discovery.take();
            return Err(error(e));
        }
    };
    let handle = app.clone();
    let auth_label = label.clone();
    #[cfg(windows)]
    show_proxy_challenges(&window).await?;
    window.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            if let Some(window) = handle.get_webview_window(&auth_label) {
                let _ = window.hide();
            }
            let state = handle.state::<Shared>();
            let mut state = state.lock().unwrap();
            if let Some(sender) = state.discovery.take() {
                let _ = sender.send(Err("已取消 WebUI 登录".into()));
            }
        }
    });
    // 用户登录可以慢于网络超时；关闭认证窗口可随时取消。
    match tokio::time::timeout(Duration::from_secs(600), receiver).await {
        Ok(Ok(result)) => result,
        _ => {
            app.state::<Shared>().lock().unwrap().discovery.take();
            Err("WebUI 登录已超时，请重新连接".into())
        }
    }
}

async fn connect(app: &tauri::AppHandle, input: Value) -> Reply {
    let server = normalize_server(input["server"].as_str().ok_or("缺少服务器地址")?)?;
    let bot_id = input["botId"]
        .as_str()
        .filter(|v| hex(v, 32))
        .ok_or("无效机器人 ID")?;
    let allow_screen = input["allowScreenRead"]
        .as_bool()
        .ok_or("缺少屏幕授权选择")?;
    let key = format!("{:x}", Sha256::digest(format!("{server}\n{bot_id}")));
    let (name, anchor) = {
        let state = app.state::<Shared>();
        let mut state = state.lock().unwrap();
        if state.session.is_some() || state.busy {
            return Err("桌宠已连接".into());
        }
        let auth = state
            .auth
            .as_ref()
            .filter(|auth| auth.server == server)
            .ok_or("请先通过 WebUI 登录")?;
        let bot = auth
            .bots
            .iter()
            .find(|bot| bot["bot_id"] == bot_id)
            .ok_or("请重新读取机器人列表")?;
        let name = bot["name"].as_str().ok_or("机器人名称无效")?.to_string();
        let profile = &mut state.settings["clients"][&key];
        if profile.is_null() {
            *profile = json!({"clientId":id()});
        }
        let client_id = profile["clientId"]
            .as_str()
            .ok_or("客户端配置无效")?
            .to_string();
        let anchor = if profile["anchor"].is_null() {
            None
        } else {
            Some(serde_json::from_value(profile["anchor"].clone()).map_err(error)?)
        };
        state.settings["server"] = json!(server);
        state.settings["botId"] = json!(bot_id);
        state.settings["botName"] = json!(name);
        state.session = Some(Session {
            generation: id(),
            key,
            options: json!({"botId":bot_id,"clientId":client_id,"allowScreenRead":allow_screen}),
            native: Arc::new(native::NativeCapabilities::new(allow_screen)),
            frontend_ready: false,
        });
        state.busy = true;
        (name, anchor)
    };
    let _change = ConnectionChange(app.clone());
    let result = async {
        save_settings(app).await?;
        let window = local_window(app, "pet", &format!("麦麦 · {name}"), 260.0, 900.0)?;
        let controller = PetWindowController::new(window.clone(), anchor)?;
        app.state::<Shared>().lock().unwrap().pet = Some(controller);
        let handle = app.clone();
        window.on_window_event(move |event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. })
                && handle.state::<Shared>().lock().unwrap().session.is_some()
            {
                handle.exit(0);
            }
        });
        window.show().map_err(error)?;
        if let Some(login) = app.get_webview_window("login") {
            login.destroy().map_err(error)?;
        }
        Ok(json!(true))
    }
    .await;
    if result.is_err() {
        {
            let state = app.state::<Shared>();
            let mut state = state.lock().unwrap();
            state.session = None;
            if let Some(controller) = state.pet.take() {
                controller.stop();
            }
        }
        if let Some(window) = app.get_webview_window("pet") {
            let _ = window.destroy();
        }
    }
    result
}

#[tauri::command]
async fn login_call(window: WebviewWindow, method: String, args: Vec<Value>) -> Reply {
    local(&window, "login")?;
    let app = window.app_handle();
    match method.as_str() {
        "profile" => {
            let state = app.state::<Shared>();
            let state = state.lock().unwrap();
            Ok(
                json!({"server":state.settings["server"],"botId":state.settings["botId"],"botName":state.settings["botName"]}),
            )
        }
        "fit" => {
            let height = number(&args, 0)?.clamp(240.0, 560.0);
            let scale = number(&args, 1)?;
            if !(0.1..=16.0).contains(&scale) {
                return Err("无效窗口缩放比例".into());
            }
            window
                .set_size(tauri::PhysicalSize::new(
                    (400.0 * scale).round() as u32,
                    (height * scale).ceil() as u32,
                ))
                .map_err(error)?;
            Ok(Value::Null)
        }
        "discover" => discover(app, string(&args, 0)?.into()).await,
        "connect" => connect(app, args.first().ok_or("缺少连接信息")?.clone()).await,
        "cancel" => {
            if let Some(sender) = app.state::<Shared>().lock().unwrap().discovery.take() {
                let _ = sender.send(Err("已取消登录".into()));
            }
            Ok(Value::Null)
        }
        _ => Err("未知登录操作".into()),
    }
}

fn string(args: &[Value], index: usize) -> Result<&str, String> {
    args.get(index)
        .and_then(Value::as_str)
        .ok_or_else(|| "参数必须是字符串".into())
}
fn number(args: &[Value], index: usize) -> Result<f64, String> {
    args.get(index)
        .and_then(Value::as_f64)
        .filter(|v| v.is_finite())
        .ok_or_else(|| "参数必须是有限数字".into())
}

async fn todo(app: &tauri::AppHandle, action: &str, mut fields: Value) -> Reply {
    fields["action"] = json!(action);
    if action != "list" {
        fields["request_id"] = json!(id());
    }
    request(app, "POST", "/todos", fields).await
}

fn open_records(app: &tauri::AppHandle) -> Reply {
    if let Some(window) = app.get_webview_window("records") {
        window.unminimize().map_err(error)?;
        window.show().map_err(error)?;
        return Ok(Value::Null);
    }
    let window = local_window(app, "records", "探索记录 · MaiBot", 1040.0, 740.0)?;
    let controller = RecordsWindowController::new(window.clone())?;
    app.state::<Shared>().lock().unwrap().records = Some(controller);
    window.show().map_err(error)?;
    Ok(Value::Null)
}

#[tauri::command]
async fn pet_call(window: WebviewWindow, method: String, args: Vec<Value>) -> Reply {
    let role = window.label().to_string();
    local(&window, &role)?;
    let app = window.app_handle();
    if args.len() > 5 {
        return Err("参数过多".into());
    }
    if role == "records" {
        let controller = app
            .state::<Shared>()
            .lock()
            .unwrap()
            .records
            .clone()
            .ok_or("记录窗口已关闭")?;
        return match method.as_str() {
            "refresh" => send_event(
                app,
                json!({"type":"exploration_tasks","task_id":args.first(),"request_id":format!("records:{}",string(&args,1)?)}),
            ),
            "window_control" => controller.control(string(&args, 0)?).map(|v| json!(v)),
            "window_gesture" => {
                controller.gesture(string(&args, 0)?)?;
                Ok(Value::Null)
            }
            "window_gesture_end" => {
                controller.end();
                Ok(Value::Null)
            }
            _ => Err("记录窗口无权调用该操作".into()),
        };
    }
    if role != "pet" {
        return Err("此窗口无权调用桌宠接口".into());
    }
    let (session, controller) = {
        let state = app.state::<Shared>();
        let state = state.lock().unwrap();
        (
            state.session.clone().ok_or("桌宠未连接")?,
            state.pet.clone().ok_or("桌宠窗口未就绪")?,
        )
    };
    match method.as_str() {
        "ready" => {
            let auth = {
                let state = app.state::<Shared>();
                let mut state = state.lock().unwrap();
                let current = state
                    .session
                    .as_mut()
                    .filter(|value| value.generation == session.generation)
                    .ok_or("桌宠连接已切换")?;
                current.frontend_ready = true;
                state.auth.clone().ok_or("桌宠认证窗口已关闭")?
            };
            transport_for(app, &auth, "start", json!([session.options]))?;
            Ok(json!(true))
        }
        "send" => {
            let request_id = string(&args, 0)?;
            if !hex(request_id, 32) {
                return Err("无效请求 ID".into());
            }
            let result = request(
                app,
                "POST",
                &format!("/chat/{request_id}"),
                json!({"text":args.get(1),"images":args.get(2)}),
            )
            .await?;
            let _ = page(
                app,
                "pet",
                json!({"type":"accepted","request_id":request_id}),
            )
            .await;
            Ok(result)
        }
        "todos" => {
            let completed = match args.first() {
                None => false,
                Some(v) => v.as_bool().ok_or("无效待办筛选")?,
            };
            let result = todo(app, "list", json!({"include_completed":completed})).await?;
            let mut items = result["items"]
                .as_array()
                .ok_or("待办列表格式无效")?
                .clone();
            if completed {
                items.retain(|v| v["status"] == "completed");
                items.sort_by(|a, b| {
                    a["completed_at"]
                        .as_f64()
                        .partial_cmp(&b["completed_at"].as_f64())
                        .unwrap_or(std::cmp::Ordering::Equal)
                });
            }
            Ok(json!(items))
        }
        "todo_image" => {
            let image = string(&args, 0)?;
            if !hex(image, 64) {
                return Err("无效图片 ID".into());
            }
            Ok(
                request(app, "GET", &format!("/todos/images/{image}"), Value::Null).await?
                    ["data_url"]
                    .clone(),
            )
        }
        "add_todo" => Ok(todo(
            app,
            "add",
            json!({"text":args.first(),"options":args.get(1)}),
        )
        .await?["item"]
            .clone()),
        "update_todo" => Ok(todo(
            app,
            "update",
            json!({"todo_id":args.first(),"text":args.get(1),"options":args.get(2)}),
        )
        .await?["item"]
            .clone()),
        "done_todo" => todo(app, "complete", json!({"todo_id":args.first()})).await,
        "open_exploration_records" => open_records(app),
        "exploration_tasks" => send_event(
            app,
            json!({"type":"exploration_tasks","task_id":args.first(),"request_id":args.get(1)}),
        ),
        "explore" => send_event(app, json!({"type":"explore","goal":args.first()})),
        "fit" => {
            controller.fit(number(&args, 0)?, number(&args, 1)?)?;
            Ok(Value::Null)
        }
        "region" => {
            controller.region(
                serde_json::from_value(args.first().ok_or("缺少窗口区域")?.clone())
                    .map_err(error)?,
                number(&args, 1)?,
            )?;
            Ok(Value::Null)
        }
        "drag_begin" => {
            controller.drag_begin()?;
            Ok(Value::Null)
        }
        "drag_move" => {
            if let Some(event) = controller.drag_move()? {
                send_event(app, event)?;
            }
            Ok(Value::Null)
        }
        "drag_end" => {
            if let Some(end) = controller.drag_end()? {
                app.state::<Shared>().lock().unwrap().settings["clients"][&session.key]["anchor"] =
                    serde_json::to_value(end.anchor).map_err(error)?;
                save_settings(app).await?;
                send_event(app, end.event)?;
            }
            Ok(Value::Null)
        }
        "clicked" => send_event(app, json!({"type":"clicked"})),
        "screen_action" => send_event(app, json!({"type":"screen_action","action":args.first()})),
        "refresh_buttons" => send_event(app, json!({"type":"buttons_refresh"})),
        "click_button" => send_event(
            app,
            json!({"type":"button_click","plugin_id":args.first(),"button_id":args.get(1),"action_id":args.get(2)}),
        ),
        "copy_panel_content" => {
            session
                .native
                .copy_panel_content(string(&args, 0)?, string(&args, 1)?)
                .await?;
            Ok(Value::Null)
        }
        "exit_client" => {
            app.exit(0);
            Ok(Value::Null)
        }
        _ => Err("未知桌宠操作".into()),
    }
}

#[tauri::command]
fn page_result(
    window: WebviewWindow,
    id: String,
    result: Value,
    error: Option<String>,
) -> Result<(), String> {
    local(&window, window.label())?;
    let state = window.state::<Shared>();
    let mut state = state.lock().unwrap();
    if state
        .pending
        .get(&id)
        .is_some_and(|pending| pending.target == window.label())
    {
        let pending = state.pending.remove(&id).unwrap();
        let _ = pending.sender.send(match error {
            Some(e) => Err(e.chars().take(2000).collect()),
            None => Ok(result),
        });
    }
    Ok(())
}

#[tauri::command]
async fn page_open_external(window: WebviewWindow, value: String) -> Result<(), String> {
    local(&window, window.label())?;
    if value.len() > 8192 {
        return Err("链接过长".into());
    }
    let url = tauri::Url::parse(&value).map_err(error)?;
    if !matches!(url.scheme(), "http" | "https")
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err("只允许 HTTP(S) 链接".into());
    }
    tauri::async_runtime::spawn_blocking(move || open::that_detached(value).map_err(error))
        .await
        .map_err(error)?
}

async fn execute(app: &tauri::AppHandle, session: Session, nonce: &str, command: Value) -> Reply {
    {
        let state = app.state::<Shared>();
        let state = state.lock().unwrap();
        if !state
            .auth
            .as_ref()
            .is_some_and(|v| v.nonce == nonce && v.connected)
            || !state
                .session
                .as_ref()
                .is_some_and(|v| v.generation == session.generation)
        {
            return Err("连接已变化，取消旧指令".into());
        }
    }
    match command["type"].as_str().ok_or("缺少指令类型")? {
        "capture_screen" | "sample_screen" | "applications" => {
            session.native.execute(&command).await
        }
        "show" => {
            app.get_webview_window("pet")
                .ok_or("桌宠已关闭")?
                .show()
                .map_err(error)?;
            page(app, "pet", json!({"type":"visibility","visible":true})).await
        }
        "hide" => {
            let window = app.get_webview_window("pet").ok_or("桌宠已关闭")?;
            let controller = app
                .state::<Shared>()
                .lock()
                .unwrap()
                .pet
                .clone()
                .ok_or("桌宠已关闭")?;
            if let Some(end) = controller.drag_end()? {
                app.state::<Shared>().lock().unwrap().settings["clients"][&session.key]["anchor"] =
                    serde_json::to_value(end.anchor).map_err(error)?;
                save_settings(app).await?;
                if !app
                    .state::<Shared>()
                    .lock()
                    .unwrap()
                    .session
                    .as_ref()
                    .is_some_and(|value| value.generation == session.generation)
                {
                    return Err("桌宠连接已变化，取消旧指令".into());
                }
                send_event(app, end.event)?;
            }
            window.hide().map_err(error)?;
            page(app, "pet", json!({"type":"visibility","visible":false})).await
        }
        "exploration_tasks"
            if command["request_id"]
                .as_str()
                .is_some_and(|id| id.starts_with("records:")) =>
        {
            if app.get_webview_window("records").is_some() {
                page(app, "records", command).await
            } else {
                Ok(json!({}))
            }
        }
        "skin" | "animation" | "reply" | "notice" | "thinking_caption" | "thinking" | "idle"
        | "failed" | "accepted" | "buttons" | "button_result" | "screen_state"
        | "exploration_state" | "exploration_result" | "exploration_tasks" | "todo_refresh" => {
            page(app, "pet", command).await
        }
        _ => Err("不支持的服务器指令".into()),
    }
}

#[tauri::command]
async fn transport_message(
    window: WebviewWindow,
    nonce: String,
    payload: Value,
) -> Result<(), String> {
    let auth = authenticated_window(&window, &nonce)?;
    if payload.to_string().len() > 65 * 1024 * 1024 {
        return Err("传输消息过大".into());
    }
    let app = window.app_handle();
    match payload["type"].as_str().ok_or("消息缺少类型")? {
        "ready" => {
            app.state::<Shared>()
                .lock()
                .unwrap()
                .auth
                .as_mut()
                .unwrap()
                .connected = false;
        }
        "authenticated" => {
            let bots = payload["bots"].as_array().ok_or("机器人列表无效")?;
            if bots.len() > 1000
                || bots.iter().any(|bot| {
                    !bot["bot_id"].as_str().is_some_and(|v| hex(v, 32))
                        || !bot["name"].as_str().is_some_and(|v| v.len() <= 800)
                })
            {
                return Err("机器人列表无效".into());
            }
            let session_page =
                tauri::Url::parse(&(auth.server.clone() + "/api/host/desktop-pet/session"))
                    .map_err(error)?;
            if window.url().map_err(error)?.path() != session_page.path() {
                // 认证后卸载完整 WebUI，仅保留同源轻量文档承载浏览器认证的 HTTP/WSS。
                window.navigate(session_page).map_err(error)?;
                return Ok(());
            }
            let options = {
                let state = app.state::<Shared>();
                let mut state = state.lock().unwrap();
                state.auth.as_mut().unwrap().bots = bots.clone();
                if let Some(sender) = state.discovery.take() {
                    let _ = sender.send(Ok(json!(bots)));
                }
                state
                    .session
                    .as_ref()
                    .filter(|s| s.frontend_ready)
                    .map(|s| s.options.clone())
            };
            window.hide().map_err(error)?;
            if let Some(options) = options {
                transport_eval(app, "start", json!([options]))?;
            }
        }
        "auth_required" => {
            app.state::<Shared>()
                .lock()
                .unwrap()
                .auth
                .as_mut()
                .unwrap()
                .connected = false;
            window.show().map_err(error)?;
            // 重新打开同一个登录页，沿用认证缓存；不清 Cookie，也不把密码转交主进程。
            let url = tauri::Url::parse(&(auth.server + "/auth")).map_err(error)?;
            if window.url().map_err(error)?.path() != url.path() {
                window.navigate(url).map_err(error)?;
            }
        }
        "state" => {
            let connected = payload["connected"].as_bool().ok_or("状态无效")?;
            app.state::<Shared>()
                .lock()
                .unwrap()
                .auth
                .as_mut()
                .unwrap()
                .connected = connected;
            if app
                .state::<Shared>()
                .lock()
                .unwrap()
                .session
                .as_ref()
                .is_some_and(|s| s.frontend_ready)
            {
                let handle = app.clone();
                let text = payload["text"].clone();
                tauri::async_runtime::spawn(async move {
                    let _ = page(
                        &handle,
                        "pet",
                        json!({"type":"connection","connected":connected,"text":text}),
                    )
                    .await;
                });
            }
        }
        "response" => {
            let request = payload["id"].as_str().ok_or("缺少请求 ID")?;
            let state = app.state::<Shared>();
            let mut state = state.lock().unwrap();
            if state
                .pending
                .get(request)
                .is_some_and(|pending| pending.target == auth.label)
            {
                let pending = state.pending.remove(request).unwrap();
                let result = if payload["ok"] == true {
                    Ok(payload["data"].clone())
                } else {
                    Err(payload["error"]
                        .as_str()
                        .unwrap_or("远端请求失败")
                        .chars()
                        .take(2000)
                        .collect())
                };
                let _ = pending.sender.send(result);
            }
        }
        "command" => {
            let session = app
                .state::<Shared>()
                .lock()
                .unwrap()
                .session
                .clone()
                .ok_or("桌宠已断开")?;
            let request = payload["id"]
                .as_str()
                .filter(|v| v.len() <= 128)
                .ok_or("指令 ID 无效")?
                .to_string();
            let command = payload["command"].clone();
            let handle = app.clone();
            tauri::async_runtime::spawn(async move {
                let generation = session.generation.clone();
                let result = execute(&handle, session, &nonce, command).await;
                let current = handle
                    .state::<Shared>()
                    .lock()
                    .unwrap()
                    .auth
                    .as_ref()
                    .is_some_and(|v| v.nonce == nonce);
                let session_current = handle
                    .state::<Shared>()
                    .lock()
                    .unwrap()
                    .session
                    .as_ref()
                    .is_some_and(|v| v.generation == generation);
                if current && session_current {
                    let args = match result {
                        Ok(value) => json!([request, value, null]),
                        Err(e) => json!([request, null, e]),
                    };
                    if let Err(e) = transport_eval(&handle, "commandResult", args) {
                        report(e);
                    }
                }
            });
        }
        _ => return Err("未知传输消息".into()),
    }
    Ok(())
}

fn change_connection(app: &tauri::AppHandle) -> Result<(), String> {
    {
        let state = app.state::<Shared>();
        let mut state = state.lock().unwrap();
        if state.busy {
            return Err("桌宠正在连接，请稍后再切换".into());
        }
        state.busy = true;
    }
    let _change = ConnectionChange(app.clone());
    let _ = transport_eval(app, "stop", json!([]));
    {
        let state = app.state::<Shared>();
        let mut state = state.lock().unwrap();
        state.session = None;
        state.pending.clear();
        if let Some(controller) = state.pet.take() {
            controller.stop();
        }
        if let Some(controller) = state.records.take() {
            controller.end();
        }
        if let Some(auth) = state.auth.as_mut() {
            auth.connected = false;
        }
    }
    for role in ["pet", "records"] {
        if let Some(window) = app.get_webview_window(role) {
            window.destroy().map_err(error)?;
        }
    }
    show_login(app)
}

fn main() {
    // 原生 Wayland 不支持桌宠依赖的全局定位，Linux 使用 X11/XWayland。
    #[cfg(target_os = "linux")]
    std::env::set_var("GDK_BACKEND", "x11");
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            login_call,
            pet_call,
            page_result,
            page_open_external,
            transport_message
        ])
        .setup(|app| {
            let directory = match std::env::var_os("MAIBOT_PET_DATA_DIR") {
                Some(path) => PathBuf::from(path),
                None => app.path().app_data_dir()?,
            };
            std::fs::create_dir_all(&directory)?;
            let settings = match std::fs::read(directory.join("settings.json")) {
                Ok(bytes) => {
                    let data: Value = serde_json::from_slice(&bytes)?;
                    if !data["clients"].is_object() {
                        return Err("桌宠设置格式无效".into());
                    }
                    data
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                    json!({"server":"","botId":"","botName":"","clients":{}})
                }
                Err(e) => return Err(e.into()),
            };
            app.manage(Mutex::new(State {
                busy: false,
                settings,
                directory,
                auth: None,
                discovery: None,
                session: None,
                pet: None,
                records: None,
                pending: HashMap::new(),
            }));
            use tauri::menu::{Menu, MenuItem};
            let show = MenuItem::with_id(app, "show", "显示桌宠", true, None::<&str>)?;
            let change =
                MenuItem::with_id(app, "change", "更换连接 / 屏幕授权", true, None::<&str>)?;
            let auth = MenuItem::with_id(app, "auth", "打开 WebUI 登录", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &change, &auth, &quit])?;
            tauri::tray::TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .tooltip("MaiBot 桌宠")
                .on_menu_event(|app, event| {
                    let result = match event.id.as_ref() {
                        "change" => change_connection(app),
                        "quit" => {
                            app.exit(0);
                            Ok(())
                        }
                        "auth" => {
                            let label = app
                                .state::<Shared>()
                                .lock()
                                .unwrap()
                                .auth
                                .as_ref()
                                .map(|v| v.label.clone());
                            if let Some(window) = label.and_then(|v| app.get_webview_window(&v)) {
                                window.show().map_err(error)
                            } else {
                                show_login(app)
                            }
                        }
                        _ => {
                            if let Some(window) = app.get_webview_window("pet") {
                                window.show().map_err(error)
                            } else {
                                show_login(app)
                            }
                        }
                    };
                    if let Err(e) = result {
                        report(e);
                    }
                })
                .build(app)?;
            show_login(app.handle()).map_err(std::io::Error::other)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("无法启动 MaiBot 桌宠");
}
