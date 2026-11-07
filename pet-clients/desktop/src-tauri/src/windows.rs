//! Native window placement and pointer handling. Desktop coordinates are physical
//! pixels; renderer rectangles and viewport dimensions are CSS/DIP coordinates.
//! macOS desktop pixels are normalized to the primary monitor's backing scale,
//! matching Tao's global cursor API even when the window is on another monitor.
//! GTK/X11 uses its root window's scale for the same reason.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};
#[cfg(any(target_os = "macos", target_os = "linux"))]
use tauri::{LogicalPosition, LogicalSize};
use tauri::{Monitor, WebviewWindow, WindowEvent};
#[cfg(any(not(any(target_os = "macos", target_os = "linux")), test))]
use tauri::{PhysicalPosition, PhysicalSize};
use uuid::Uuid;

const PET_WIDTH: f64 = 260.0;
type WindowResult<T> = Result<T, String>;
type HitRect = [f64; 5];

/// The bottom-center of the pet viewport, in physical desktop pixels.
/// Persist separately from Electron's former DIP-based anchor coordinates.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
pub struct Anchor {
    pub x: f64,
    pub y: f64,
}

pub struct DragEnd {
    pub anchor: Anchor,
    pub event: Value,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct Area {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    scale: f64,
}

impl Area {
    fn from_monitor(monitor: &Monitor, _desktop_scale: f64) -> Self {
        let area = monitor.work_area();
        let result = Self {
            x: area.position.x as f64,
            y: area.position.y as f64,
            width: area.size.width as f64,
            height: area.size.height as f64,
            scale: monitor.scale_factor(),
        };
        #[cfg(any(target_os = "macos", target_os = "linux"))]
        return Self {
            x: result.x / result.scale * _desktop_scale,
            y: result.y / result.scale * _desktop_scale,
            width: result.width / result.scale * _desktop_scale,
            height: result.height / result.scale * _desktop_scale,
            scale: _desktop_scale,
        };
        #[cfg(not(any(target_os = "macos", target_os = "linux")))]
        result
    }
}

impl Area {
    fn contains(self, point: Anchor) -> bool {
        point.x >= self.x
            && point.x <= self.x + self.width
            && point.y >= self.y
            && point.y <= self.y + self.height
    }

    fn distance(self, point: Anchor) -> f64 {
        let dx = (self.x - point.x)
            .max(0.0)
            .max(point.x - self.x - self.width);
        let dy = (self.y - point.y)
            .max(0.0)
            .max(point.y - self.y - self.height);
        dx * dx + dy * dy
    }

    fn clamp(self, anchor: Anchor) -> Anchor {
        let half_width = (PET_WIDTH * self.scale / 2.0).min(self.width / 2.0);
        let visible_height = (180.0 * self.scale).min(self.height);
        Anchor {
            x: anchor
                .x
                .clamp(self.x + half_width, self.x + self.width - half_width),
            y: anchor
                .y
                .clamp(self.y + visible_height, self.y + self.height),
        }
    }
}

fn lock<T>(value: &Mutex<T>) -> WindowResult<MutexGuard<'_, T>> {
    value.lock().map_err(|_| "窗口状态锁已损坏".to_string())
}

fn native<T>(value: tauri::Result<T>) -> WindowResult<T> {
    value.map_err(|error| error.to_string())
}

fn valid_scale(scale: f64) -> WindowResult<()> {
    if !scale.is_finite() || !(0.1..=16.0).contains(&scale) {
        return Err("无效窗口缩放比例".into());
    }
    Ok(())
}

fn desktop_scale(_window: &WebviewWindow, _native_scale: f64) -> WindowResult<f64> {
    #[cfg(target_os = "macos")]
    return Ok(native(_window.primary_monitor())?
        .ok_or("没有可用显示器")?
        .scale_factor());
    #[cfg(target_os = "linux")]
    {
        fn root_scale() -> WindowResult<f64> {
            use gtk::gdk::prelude::*;
            gtk::gdk::Display::default()
                .map(|display| display.default_group().scale_factor() as f64)
                .ok_or_else(|| "没有可用的 X11 显示连接".into())
        }
        // Window commands normally run on workers, but never wait on the GTK thread itself.
        if gtk::is_initialized_main_thread() {
            return root_scale();
        }
        let (sender, receiver) = std::sync::mpsc::sync_channel(1);
        native(_window.run_on_main_thread(move || {
            let _ = sender.send(root_scale());
        }))?;
        return receiver.recv().map_err(|error| error.to_string())?;
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    Ok(_native_scale)
}

fn desktop_point(point: Anchor, _native_scale: f64, _desktop_scale: f64) -> Anchor {
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    return Anchor {
        x: point.x / _native_scale * _desktop_scale,
        y: point.y / _native_scale * _desktop_scale,
    };
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    point
}

fn validate_rects(rects: &[HitRect]) -> WindowResult<()> {
    if rects.len() > 256
        || rects.iter().any(|rect| {
            rect.iter()
                .any(|number| !number.is_finite() || number.abs() > 10_000.0)
                || rect[2] < 0.0
                || rect[3] < 0.0
                || rect[4] < 0.0
        })
    {
        return Err("无效窗口区域".into());
    }
    Ok(())
}

#[cfg(any(not(windows), test))]
fn contains(rect: HitRect, point: Anchor) -> bool {
    let [x, y, width, height, corner] = rect;
    if width <= 0.0
        || height <= 0.0
        || point.x < x
        || point.x > x + width
        || point.y < y
        || point.y > y + height
    {
        return false;
    }
    let radius = corner.min(width / 2.0).min(height / 2.0);
    let dx = (x + radius - point.x)
        .max(0.0)
        .max(point.x - (x + width - radius));
    let dy = (y + radius - point.y)
        .max(0.0)
        .max(point.y - (y + height - radius));
    dx * dx + dy * dy <= radius * radius
}

#[cfg(any(not(windows), test))]
fn pointer_relative_to_window(cursor: Anchor, origin: Anchor, css_scale: f64) -> Anchor {
    Anchor {
        x: (cursor.x - origin.x) / css_scale,
        y: (cursor.y - origin.y) / css_scale,
    }
}

#[cfg(any(not(any(target_os = "macos", target_os = "linux")), test))]
fn placement(
    anchor: Anchor,
    height: f64,
    scale: f64,
) -> (PhysicalPosition<i32>, PhysicalSize<u32>) {
    (
        PhysicalPosition::new(
            (anchor.x - PET_WIDTH * scale / 2.0).round() as i32,
            (anchor.y - height * scale).round() as i32,
        ),
        PhysicalSize::new(
            (PET_WIDTH * scale).round() as u32,
            (height * scale).round() as u32,
        ),
    )
}

#[derive(Clone)]
struct Drag {
    id: String,
    offset: Anchor,
    moved: bool,
}

impl Drag {
    fn anchor(&self, cursor: Anchor, scale: f64) -> Anchor {
        // Keep the same logical grip point when the window crosses monitors with different DPI.
        Anchor {
            x: cursor.x + self.offset.x * scale,
            y: cursor.y + self.offset.y * scale,
        }
    }
}

struct PetState {
    anchor: Anchor,
    height: f64,
    // Native desktop units per CSS pixel, including WebView accessibility/text scaling.
    scale: f64,
    desktop_scale: f64,
    renderer_zoom: f64,
    rects: Vec<HitRect>,
    monitors: Vec<Area>,
    drag: Option<Drag>,
    #[cfg(not(windows))]
    ignored: Option<bool>,
}

pub struct PetWindowController {
    window: WebviewWindow,
    state: Mutex<PetState>,
    dirty: AtomicBool,
    stopped: AtomicBool,
}

impl PetWindowController {
    /// Call after constructing a transparent, undecorated, always-on-top window.
    pub fn new(window: WebviewWindow, anchor: Option<Anchor>) -> WindowResult<Arc<Self>> {
        let primary = native(window.primary_monitor())?.ok_or("没有可用显示器")?;
        let scale = desktop_scale(&window, primary.scale_factor())?;
        let primary_area = Area::from_monitor(&primary, scale);
        let monitors = native(window.available_monitors())?;
        let areas: Vec<Area> = monitors
            .iter()
            .map(|monitor| Area::from_monitor(monitor, scale))
            .collect();
        let saved = anchor
            .filter(|point| point.x.is_finite() && point.y.is_finite())
            .filter(|point| {
                areas.iter().any(|area| {
                    area.contains(Anchor {
                        x: point.x,
                        y: point.y - 1.0,
                    })
                })
            });
        let anchor = saved.unwrap_or(Anchor {
            x: primary_area.x + primary_area.width - 138.0 * primary_area.scale,
            y: primary_area.y + primary_area.height,
        });
        let area = nearest_area(&areas, anchor).unwrap_or(primary_area);
        let height = (area.height / area.scale).clamp(460.0, 900.0);
        let controller = Arc::new(Self {
            window: window.clone(),
            state: Mutex::new(PetState {
                anchor: area.clamp(anchor),
                height,
                scale: area.scale,
                desktop_scale: area.scale,
                renderer_zoom: 1.0,
                rects: vec![[47.0, height - 155.0, 166.0, 139.0, 42.0]],
                monitors: areas,
                drag: None,
                #[cfg(not(windows))]
                ignored: None,
            }),
            dirty: AtomicBool::new(false),
            stopped: AtomicBool::new(false),
        });
        controller.place()?;
        #[cfg(windows)]
        controller.apply_region()?;
        controller.update_pointer()?;
        let weak = Arc::downgrade(&controller);
        window.on_window_event(move |event| {
            if let Some(controller) = weak.upgrade() {
                match event {
                    WindowEvent::Destroyed => controller.stop(),
                    WindowEvent::ScaleFactorChanged { .. } => {
                        controller.dirty.store(true, Ordering::Release)
                    }
                    _ => {}
                }
            }
        });
        let weak = Arc::downgrade(&controller);
        thread::Builder::new()
            .name("desktop-pet-pointer".into())
            .spawn(move || {
                let mut monitor_check = Instant::now();
                loop {
                    let Some(controller) = weak.upgrade() else {
                        break;
                    };
                    if controller.stopped.load(Ordering::Acquire) {
                        break;
                    }
                    let result = (|| {
                        if monitor_check.elapsed() >= Duration::from_secs(1) {
                            controller.keep_on_screen()?;
                            monitor_check = Instant::now();
                        }
                        if controller.dirty.swap(false, Ordering::AcqRel) {
                            controller.sync_scale()?;
                            controller.place()?;
                            #[cfg(windows)]
                            controller.apply_region()?;
                        }
                        controller.update_pointer()
                    })();
                    if let Err(error) = result {
                        if !controller.stopped.load(Ordering::Acquire) {
                            eprintln!("桌宠窗口处理失败: {error}");
                            // Expose the failure and retain access to the window instead of leaving it unclickable.
                            #[cfg(not(windows))]
                            let _ = controller.window.set_ignore_cursor_events(false);
                        }
                        controller.stop();
                        break;
                    }
                    drop(controller); // The sleeping worker must not keep a closed controller alive.
                    thread::sleep(Duration::from_millis(32));
                }
            })
            .map_err(|error| error.to_string())?;
        Ok(controller)
    }

    fn active(&self) -> WindowResult<()> {
        if self.stopped.load(Ordering::Acquire) {
            Err("桌宠窗口已关闭或停止".into())
        } else {
            Ok(())
        }
    }

    fn cursor(&self) -> WindowResult<Anchor> {
        let cursor = native(self.window.cursor_position())?;
        Ok(Anchor {
            x: cursor.x,
            y: cursor.y,
        })
    }

    fn place(&self) -> WindowResult<()> {
        let (anchor, height, scale, _desktop_scale, _zoom) = {
            let state = lock(&self.state)?;
            (
                state.anchor,
                state.height,
                state.scale,
                state.desktop_scale,
                state.renderer_zoom,
            )
        };
        #[cfg(any(target_os = "macos", target_os = "linux"))]
        {
            // Cocoa/GTK global positions are logical; passing physical coordinates at the destination
            // monitor's scale would make the global origin jump when crossing a DPI boundary.
            native(self.window.set_position(LogicalPosition::new(
                anchor.x / _desktop_scale - PET_WIDTH * _zoom / 2.0,
                anchor.y / _desktop_scale - height * _zoom,
            )))?;
            return native(
                self.window
                    .set_size(LogicalSize::new(PET_WIDTH * _zoom, height * _zoom)),
            );
        }
        #[cfg(not(any(target_os = "macos", target_os = "linux")))]
        let (position, size) = placement(anchor, height, scale);
        #[cfg(not(any(target_os = "macos", target_os = "linux")))]
        {
            native(self.window.set_position(position))?;
            native(self.window.set_size(size))
        }
    }

    fn sync_scale(&self) -> WindowResult<()> {
        let desktop_scale = desktop_scale(&self.window, native(self.window.scale_factor())?)?;
        let cursor = self.cursor()?;
        let mut state = lock(&self.state)?;
        let scale = desktop_scale * state.renderer_zoom;
        if state.scale != scale {
            #[cfg(any(target_os = "macos", target_os = "linux"))]
            {
                state.anchor.x *= desktop_scale / state.desktop_scale;
                state.anchor.y *= desktop_scale / state.desktop_scale;
            }
            if let Some(drag) = &state.drag {
                if drag.moved {
                    state.anchor = drag.anchor(cursor, scale);
                }
            }
            state.scale = scale;
        }
        state.desktop_scale = desktop_scale;
        Ok(())
    }

    fn keep_on_screen(&self) -> WindowResult<()> {
        let scale = desktop_scale(&self.window, native(self.window.scale_factor())?)?;
        let areas: Vec<Area> = native(self.window.available_monitors())?
            .iter()
            .map(|monitor| Area::from_monitor(monitor, scale))
            .collect();
        let mut state = lock(&self.state)?;
        if state.monitors == areas {
            return Ok(());
        }
        if state.drag.is_none() {
            let mut area = nearest_area(&areas, state.anchor).ok_or("没有可用显示器")?;
            area.scale *= state.renderer_zoom;
            let anchor = area.clamp(state.anchor);
            if anchor != state.anchor {
                state.anchor = anchor;
                self.dirty.store(true, Ordering::Release);
            }
        }
        // Preserve freely placed anchors during ordinary use; clamp only when the display layout changes.
        state.monitors = areas;
        Ok(())
    }

    fn update_pointer(&self) -> WindowResult<()> {
        // Windows uses a stable native region. Toggling IGNORE_CURSOR_EVENT also changes
        // WS_EX_LAYERED in Tao, which can invalidate WebView2's transparent composition.
        #[cfg(windows)]
        {
            return Ok(());
        }
        #[cfg(not(windows))]
        {
            let cursor = self.cursor()?;
            let position = native(self.window.inner_position())?;
            let native_scale = native(self.window.scale_factor())?;
            let scale = desktop_scale(&self.window, native_scale)?;
            let position = desktop_point(
                Anchor {
                    x: position.x as f64,
                    y: position.y as f64,
                },
                native_scale,
                scale,
            );
            let ignore = {
                let state = lock(&self.state)?;
                let css_scale = scale * state.renderer_zoom;
                let point = pointer_relative_to_window(cursor, position, css_scale);
                let ignore =
                    state.drag.is_none() && !state.rects.iter().any(|rect| contains(*rect, point));
                if state.ignored == Some(ignore) {
                    return Ok(());
                }
                ignore
            };
            // All window calls happen outside the mutex; their event-loop dispatch cannot deadlock commands.
            native(self.window.set_ignore_cursor_events(ignore))?;
            lock(&self.state)?.ignored = Some(ignore);
            Ok(())
        }
    }

    pub fn fit(&self, scale: f64, height: f64) -> WindowResult<()> {
        self.active()?;
        valid_scale(scale)?;
        if !height.is_finite() {
            return Err("无效窗口高度".into());
        }
        let native_scale = native(self.window.scale_factor())?;
        {
            let mut state = lock(&self.state)?;
            state.height = height.clamp(460.0, 900.0).round();
            // WebView2's DPR also includes Windows text size. Using only monitor DPI
            // would shrink a 260 CSS-pixel viewport (for example to 215 at 121% text size).
            state.renderer_zoom = scale / native_scale;
            if state.drag.is_none() {
                if let Some(mut area) = nearest_area(&state.monitors, state.anchor) {
                    area.scale *= state.renderer_zoom;
                    state.anchor = area.clamp(state.anchor);
                }
            }
        }
        // The page awaits fit before measuring hit rectangles. Queue native placement before
        // replying, otherwise its first region calculation can use the old viewport height.
        self.sync_scale()?;
        self.place()?;
        #[cfg(windows)]
        self.apply_region()?;
        #[cfg(debug_assertions)]
        eprintln!(
            "桌宠窗口缩放: renderer_dpr={scale}, native_dpi={native_scale}, inner_size={:?}",
            native(self.window.inner_size())?
        );
        Ok(())
    }

    pub fn region(&self, rects: Vec<HitRect>, scale: f64) -> WindowResult<()> {
        self.active()?;
        valid_scale(scale)?;
        validate_rects(&rects)?;
        let native_scale = native(self.window.scale_factor())?;
        let mut state = lock(&self.state)?;
        state.rects = rects;
        let zoom = scale / native_scale;
        if state.renderer_zoom != zoom {
            state.renderer_zoom = zoom;
            self.dirty.store(true, Ordering::Release);
        }
        drop(state);
        #[cfg(windows)]
        self.apply_region()?;
        // Monitor changes use the native DPI; renderer changes supply their actual CSS pixel ratio.
        Ok(())
    }

    #[cfg(windows)]
    fn apply_region(&self) -> WindowResult<()> {
        let (rects, scale) = {
            let state = lock(&self.state)?;
            (state.rects.clone(), state.scale)
        };
        let window = self.window.clone();
        let (sender, receiver) = std::sync::mpsc::sync_channel(1);
        native(self.window.run_on_main_thread(move || {
            use win32::Win32::Graphics::Gdi::{
                CombineRgn, CreateRectRgn, CreateRoundRectRgn, DeleteObject, SetWindowRgn, RGN_OR,
            };
            let result = (|| {
                let hwnd = win32::Win32::Foundation::HWND(native(window.hwnd())?.0);
                unsafe {
                    let combined = CreateRectRgn(0, 0, 0, 0);
                    if combined.0.is_null() {
                        return Err("无法创建桌宠窗口区域".into());
                    }
                    for [x, y, width, height, corner] in rects {
                        if width == 0.0 || height == 0.0 {
                            continue;
                        }
                        let diameter = (corner.min(width / 2.0).min(height / 2.0) * 2.0 * scale)
                            .round() as i32;
                        let region = CreateRoundRectRgn(
                            (x * scale).floor() as i32,
                            (y * scale).floor() as i32,
                            ((x + width) * scale).ceil() as i32,
                            ((y + height) * scale).ceil() as i32,
                            diameter,
                            diameter,
                        );
                        if region.0.is_null() {
                            let _ = DeleteObject(combined.into());
                            return Err("无法创建桌宠交互区域".into());
                        }
                        let merged = CombineRgn(Some(combined), Some(combined), Some(region), RGN_OR);
                        let _ = DeleteObject(region.into());
                        if merged.0 == 0 {
                            let _ = DeleteObject(combined.into());
                            return Err("无法合并桌宠交互区域".into());
                        }
                    }
                    // Success transfers ownership to Windows; on failure we must release the HRGN.
                    if SetWindowRgn(hwnd, Some(combined), true) == 0 {
                        let _ = DeleteObject(combined.into());
                        return Err("无法应用桌宠窗口区域".into());
                    }
                }
                Ok(())
            })();
            let _ = sender.send(result);
        }))?;
        receiver.recv().map_err(|error| error.to_string())?
    }

    pub fn drag_begin(&self) -> WindowResult<()> {
        self.active()?;
        let cursor = self.cursor()?;
        let scale = desktop_scale(&self.window, native(self.window.scale_factor())?)?;
        let mut state = lock(&self.state)?;
        if state.drag.is_some() {
            return Ok(());
        }
        state.drag = Some(Drag {
            id: Uuid::new_v4().simple().to_string(),
            moved: false,
            offset: Anchor {
                x: (state.anchor.x - cursor.x) / (scale * state.renderer_zoom),
                y: (state.anchor.y - cursor.y) / (scale * state.renderer_zoom),
            },
        });
        Ok(())
    }

    pub fn drag_move(&self) -> WindowResult<Option<Value>> {
        self.active()?;
        let cursor = self.cursor()?;
        let native_scale = native(self.window.scale_factor())?;
        let desktop_scale = desktop_scale(&self.window, native_scale)?;
        let mut state = lock(&self.state)?;
        let scale = desktop_scale * state.renderer_zoom;
        let renderer_dpr = native_scale * state.renderer_zoom;
        let Some(drag) = state.drag.as_mut() else {
            return Ok(None);
        };
        let anchor = drag.anchor(cursor, scale);
        let event = (!drag.moved).then(|| json!({ "type": "drag_started", "gesture_id": drag.id,
            "offset_x": drag.offset.x * renderer_dpr, "offset_y": drag.offset.y * renderer_dpr, "scale": renderer_dpr }));
        drag.moved = true;
        state.anchor = anchor;
        state.scale = scale;
        self.dirty.store(true, Ordering::Release);
        Ok(event)
    }

    pub fn drag_end(&self) -> WindowResult<Option<DragEnd>> {
        self.active()?;
        let mut state = lock(&self.state)?;
        let Some(drag) = state.drag.take() else {
            return Ok(None);
        };
        if !drag.moved {
            return Ok(None);
        }
        Ok(Some(DragEnd {
            anchor: state.anchor,
            event: json!({ "type": "drag_ended", "gesture_id": drag.id }),
        }))
    }

    pub fn stop(&self) {
        self.stopped.store(true, Ordering::Release);
    }
}

fn nearest_area(areas: &[Area], anchor: Anchor) -> Option<Area> {
    // The anchor lies exactly on the bottom edge: look one physical pixel inside its screen.
    let point = Anchor {
        x: anchor.x,
        y: anchor.y - 1.0,
    };
    areas
        .iter()
        .copied()
        .min_by(|left, right| left.distance(point).total_cmp(&right.distance(point)))
}

#[derive(Clone, Copy)]
struct Bounds {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

#[derive(Clone)]
struct Resize {
    edge: String,
    cursor: Anchor,
    bounds: Bounds,
}

fn resized(gesture: &Resize, cursor: Anchor, scale: f64) -> Bounds {
    let Bounds {
        mut x,
        mut y,
        mut width,
        mut height,
    } = gesture.bounds;
    let dx = cursor.x - gesture.cursor.x;
    let dy = cursor.y - gesture.cursor.y;
    if gesture.edge.contains('e') {
        width = (width + dx).max(680.0 * scale);
    }
    if gesture.edge.contains('s') {
        height = (height + dy).max(480.0 * scale);
    }
    if gesture.edge.contains('w') {
        width = (width - dx).max(680.0 * scale);
        x = gesture.bounds.x + gesture.bounds.width - width;
    }
    if gesture.edge.contains('n') {
        height = (height - dy).max(480.0 * scale);
        y = gesture.bounds.y + gesture.bounds.height - height;
    }
    Bounds {
        x,
        y,
        width,
        height,
    }
}

pub struct RecordsWindowController {
    window: WebviewWindow,
    gesture: Mutex<Option<Resize>>,
    stopped: AtomicBool,
}

impl RecordsWindowController {
    pub fn new(window: WebviewWindow) -> WindowResult<Arc<Self>> {
        let controller = Arc::new(Self {
            window: window.clone(),
            gesture: Mutex::new(None),
            stopped: AtomicBool::new(false),
        });
        let weak = Arc::downgrade(&controller);
        window.on_window_event(move |event| {
            if let Some(controller) = weak.upgrade() {
                match event {
                    WindowEvent::Destroyed => controller.stopped.store(true, Ordering::Release),
                    WindowEvent::Focused(false) => {
                        controller.end();
                    }
                    _ => {}
                }
            }
        });
        let weak = Arc::downgrade(&controller);
        thread::Builder::new()
            .name("desktop-pet-records-resize".into())
            .spawn(move || loop {
                let Some(controller) = weak.upgrade() else {
                    break;
                };
                if controller.stopped.load(Ordering::Acquire) {
                    break;
                }
                if let Err(error) = controller.resize_step() {
                    if !controller.stopped.load(Ordering::Acquire) {
                        eprintln!("记录窗口缩放失败: {error}");
                    }
                    controller.end();
                }
                drop(controller);
                thread::sleep(Duration::from_millis(16));
            })
            .map_err(|error| error.to_string())?;
        Ok(controller)
    }

    pub fn control(&self, action: &str) -> WindowResult<bool> {
        match action {
            "close" => {
                self.end();
                native(self.window.close())?;
                return Ok(false);
            }
            "minimize" => {
                self.end();
                native(self.window.minimize())?;
            }
            "maximize" => {
                self.end();
                if native(self.window.is_maximized())? {
                    native(self.window.unmaximize())?;
                } else {
                    native(self.window.maximize())?;
                }
            }
            "state" => {}
            _ => return Err("未知窗口操作".into()),
        }
        native(self.window.is_maximized())
    }

    pub fn gesture(&self, edge: &str) -> WindowResult<()> {
        self.end();
        if edge == "move" {
            return native(self.window.start_dragging());
        }
        if !["n", "s", "w", "e", "nw", "ne", "sw", "se"].contains(&edge) {
            return Err("未知缩放方向".into());
        }
        if native(self.window.is_maximized())? {
            return Ok(());
        }
        let cursor = native(self.window.cursor_position())?;
        let position = native(self.window.outer_position())?;
        let size = native(self.window.outer_size())?;
        let native_scale = native(self.window.scale_factor())?;
        let scale = desktop_scale(&self.window, native_scale)?;
        let position = desktop_point(
            Anchor {
                x: position.x as f64,
                y: position.y as f64,
            },
            native_scale,
            scale,
        );
        let size = desktop_point(
            Anchor {
                x: size.width as f64,
                y: size.height as f64,
            },
            native_scale,
            scale,
        );
        *lock(&self.gesture)? = Some(Resize {
            edge: edge.into(),
            cursor: Anchor {
                x: cursor.x,
                y: cursor.y,
            },
            bounds: Bounds {
                x: position.x,
                y: position.y,
                width: size.x,
                height: size.y,
            },
        });
        Ok(())
    }

    fn resize_step(&self) -> WindowResult<()> {
        let Some(gesture) = lock(&self.gesture)?.clone() else {
            return Ok(());
        };
        let cursor = native(self.window.cursor_position())?;
        let scale = desktop_scale(&self.window, native(self.window.scale_factor())?)?;
        let bounds = resized(
            &gesture,
            Anchor {
                x: cursor.x,
                y: cursor.y,
            },
            scale,
        );
        #[cfg(any(target_os = "macos", target_os = "linux"))]
        {
            native(
                self.window
                    .set_position(LogicalPosition::new(bounds.x / scale, bounds.y / scale)),
            )?;
            return native(self.window.set_size(LogicalSize::new(
                bounds.width / scale,
                bounds.height / scale,
            )));
        }
        #[cfg(not(any(target_os = "macos", target_os = "linux")))]
        {
            native(self.window.set_position(PhysicalPosition::new(
                bounds.x.round() as i32,
                bounds.y.round() as i32,
            )))?;
            native(self.window.set_size(PhysicalSize::new(
                bounds.width.round() as u32,
                bounds.height.round() as u32,
            )))
        }
    }

    pub fn end(&self) {
        match self.gesture.lock() {
            Ok(mut gesture) => *gesture = None,
            Err(error) => eprintln!("记录窗口状态锁已损坏: {error}"),
        }
    }
}
