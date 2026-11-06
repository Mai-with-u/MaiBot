//! Local desktop capabilities. OS calls, image codecs and hashing stay off the async runtime.

use std::borrow::Cow;
use std::io::{self, Cursor};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use arboard::{Clipboard, ImageData};
use base64::{engine::general_purpose::STANDARD, Engine};
use chrono::{SecondsFormat, Utc};
use image::codecs::png::{PngDecoder, PngEncoder};
use image::{
    DynamicImage, ExtendedColorType, ImageDecoder, ImageEncoder, Limits, RgbImage, RgbaImage,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio::process::Command;

const MAX_EDGE: u32 = 1920;
const MAX_CAPTURE_PIXELS: u64 = 16_000_000;
const MAX_CLIPBOARD_PIXELS: u64 = 4_000_000;
const MAX_CLIPBOARD_BASE64: usize = 1_000_000;
const MAX_PROCESS_OUTPUT: usize = 2 * 1024 * 1024;
const SAMPLE_WIDTH: u32 = 320;
const SAMPLE_HEIGHT: u32 = 180;
const PNG_SIGNATURE: &[u8; 8] = b"\x89PNG\r\n\x1a\n";

#[cfg(any(windows, test))]
const WINDOWS_PROCESSES: &str = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; \
    $ErrorActionPreference='Stop'; \
    @(Get-Process | Where-Object { $_.Id -ne $PID } | Select-Object ProcessName,MainWindowTitle) | ConvertTo-Json -Compress";

struct NativeState {
    allow_screen_read: bool,
    capture_busy: AtomicBool,
    previous: Mutex<Option<ScreenSample>>,
    // Linux clipboard data is served by the owner. Keep its instance alive until disconnect/exit.
    clipboard: Mutex<Option<Clipboard>>,
}

#[derive(Clone)]
pub struct NativeCapabilities {
    state: Arc<NativeState>,
}

#[derive(Clone)]
struct ScreenSample {
    width: u32,
    height: u32,
    pixels: Vec<u8>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct SampleOptions {
    threshold: f64,
    reset: bool,
}

struct CaptureLease(Arc<NativeState>);

impl Drop for CaptureLease {
    fn drop(&mut self) {
        self.0.capture_busy.store(false, Ordering::Release);
    }
}

impl NativeCapabilities {
    pub fn new(allow_screen_read: bool) -> Self {
        Self {
            state: Arc::new(NativeState {
                allow_screen_read,
                capture_busy: AtomicBool::new(false),
                previous: Mutex::new(None),
                clipboard: Mutex::new(None),
            }),
        }
    }

    pub async fn execute(&self, command: &Value) -> Result<Value, String> {
        match command.get("type").and_then(Value::as_str) {
            Some("capture_screen") => self.capture(None).await,
            Some("sample_screen") => self.capture(Some(parse_sample_options(command)?)).await,
            Some("applications") => {
                let output = query_applications().await?;
                tokio::task::spawn_blocking(move || parse_applications(&output, cfg!(windows)))
                    .await
                    .map_err(|error| format!("应用解析任务失败：{error}"))?
            }
            _ => Err("不支持的本机能力指令".into()),
        }
    }

    async fn capture(&self, options: Option<SampleOptions>) -> Result<Value, String> {
        require_local_screen_permission(self.state.allow_screen_read)?;
        if self
            .state
            .capture_busy
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err("正在采集桌面，请稍后重试".into());
        }
        // The lease lives in the worker: cancelling the async caller must not release it early.
        let lease = CaptureLease(Arc::clone(&self.state));
        tokio::task::spawn_blocking(move || {
            let state = &lease.0;
            require_os_screen_permission(true)?;
            let mut previous = state
                .previous
                .lock()
                .map_err(|_| "屏幕采样状态不可用".to_string())?;
            if options.is_some_and(|options| options.reset) {
                *previous = None;
            }
            let image = capture_primary_screen()?;
            let captured_at = Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true);
            require_os_screen_permission(false)?;
            let (result, sample) = process_capture(
                image,
                &captured_at,
                options.and_then(|_| previous.as_ref()),
                options.map(|options| options.threshold),
            )?;
            require_os_screen_permission(false)?;
            if options.is_some() && sample.is_some() {
                *previous = sample;
            }
            Ok(result)
        })
        .await
        .map_err(|error| format!("桌面采集任务失败：{error}"))?
    }

    pub async fn copy_panel_content(&self, kind: &str, content: &str) -> Result<(), String> {
        validate_clipboard_request(kind, content)?;
        let kind = kind.to_owned();
        let content = content.to_owned();
        let state = Arc::clone(&self.state);
        tokio::task::spawn_blocking(move || {
            // Decode first. Invalid images must never replace the user's current clipboard.
            let image = if kind == "image" {
                Some(decode_clipboard_image(&content)?)
            } else {
                None
            };
            let mut owner = state
                .clipboard
                .lock()
                .map_err(|_| "剪贴板状态不可用".to_string())?;
            if owner.is_none() {
                *owner =
                    Some(Clipboard::new().map_err(|error| format!("无法打开剪贴板：{error}"))?);
            }
            let clipboard = owner.as_mut().ok_or("剪贴板未初始化")?;
            if let Some(image) = image {
                clipboard
                    .set_image(ImageData {
                        width: image.width() as usize,
                        height: image.height() as usize,
                        bytes: Cow::Owned(image.into_raw()),
                    })
                    .map_err(|error| format!("复制图片失败：{error}"))
            } else {
                clipboard
                    .set_text(content)
                    .map_err(|error| format!("复制文本失败：{error}"))
            }
        })
        .await
        .map_err(|error| format!("剪贴板任务失败：{error}"))?
    }
}

fn require_local_screen_permission(allowed: bool) -> Result<(), String> {
    if allowed {
        Ok(())
    } else {
        Err("客户端未授权读取桌面，请在连接设置中开启".into())
    }
}

#[cfg(target_os = "macos")]
fn require_os_screen_permission(allow_prompt: bool) -> Result<(), String> {
    #[link(name = "CoreGraphics", kind = "framework")]
    unsafe extern "C" {
        fn CGPreflightScreenCaptureAccess() -> bool;
        fn CGRequestScreenCaptureAccess() -> bool;
    }
    // These parameterless CoreGraphics APIs are available since macOS 10.15. Request only after
    // the user's in-app opt-in; never return partial/blank captures when TCC has denied access.
    let granted = unsafe {
        CGPreflightScreenCaptureAccess()
            || (allow_prompt && CGRequestScreenCaptureAccess() && CGPreflightScreenCaptureAccess())
    };
    if granted {
        Ok(())
    } else {
        Err("macOS 屏幕录制权限未授权，请在系统设置的隐私与安全性中允许本应用，然后重新启动".into())
    }
}

#[cfg(not(target_os = "macos"))]
fn require_os_screen_permission(_allow_prompt: bool) -> Result<(), String> {
    Ok(())
}

fn parse_sample_options(command: &Value) -> Result<SampleOptions, String> {
    let invalid = || "屏幕变化阈值必须在 0.001～1 之间，reset 必须为布尔值".to_string();
    let threshold = command
        .get("threshold")
        .and_then(Value::as_f64)
        .ok_or_else(invalid)?;
    if !threshold.is_finite() || !(0.001..=1.0).contains(&threshold) {
        return Err(invalid());
    }
    let reset = match command.get("reset") {
        None => false,
        Some(value) => value.as_bool().ok_or_else(invalid)?,
    };
    Ok(SampleOptions { threshold, reset })
}

fn check_dimensions(width: u32, height: u32, limit: u64) -> Result<(), String> {
    if width == 0 || height == 0 || u64::from(width) * u64::from(height) > limit {
        Err("图片尺寸无效或超过限制".into())
    } else {
        Ok(())
    }
}

fn capture_primary_screen() -> Result<RgbaImage, String> {
    let monitors = xcap::Monitor::all().map_err(|error| format!("无法读取显示器信息：{error}"))?;
    let mut primary = None;
    for monitor in monitors {
        if monitor
            .is_primary()
            .map_err(|error| format!("无法确定主屏：{error}"))?
        {
            primary = Some(monitor);
            break;
        }
    }
    let monitor = primary.ok_or("无法确定主屏截图来源；当前桌面环境可能未提供主屏标识")?;
    check_dimensions(
        monitor
            .width()
            .map_err(|error| format!("无法读取主屏宽度：{error}"))?,
        monitor
            .height()
            .map_err(|error| format!("无法读取主屏高度：{error}"))?,
        MAX_CAPTURE_PIXELS,
    )?;
    let image = monitor
        .capture_image()
        .map_err(|error| format!("无法读取主屏画面，请检查系统屏幕录制权限：{error}"))?;
    check_dimensions(image.width(), image.height(), MAX_CAPTURE_PIXELS)?;
    Ok(image)
}

fn scaled_size(width: u32, height: u32) -> (u32, u32) {
    let ratio = (f64::from(MAX_EDGE) / f64::from(width.max(height))).min(1.0);
    (
        (f64::from(width) * ratio).round().max(1.0) as u32,
        (f64::from(height) * ratio).round().max(1.0) as u32,
    )
}

/// Match the original client's bilinear RGB sampling, independent of OS bitmap layout.
fn resize_rgb(source: &RgbImage, width: u32, height: u32) -> RgbImage {
    RgbImage::from_fn(width, height, |x, y| {
        let sx = ((f64::from(x) + 0.5) * f64::from(source.width()) / f64::from(width) - 0.5)
            .clamp(0.0, f64::from(source.width() - 1));
        let sy = ((f64::from(y) + 0.5) * f64::from(source.height()) / f64::from(height) - 0.5)
            .clamp(0.0, f64::from(source.height() - 1));
        let left = sx.floor() as u32;
        let top = sy.floor() as u32;
        let right = (left + 1).min(source.width() - 1);
        let bottom = (top + 1).min(source.height() - 1);
        let fx = sx - f64::from(left);
        let fy = sy - f64::from(top);
        let mut result = [0; 3];
        for (channel, byte) in result.iter_mut().enumerate() {
            *byte = ((f64::from(source[(left, top)][channel]) * (1.0 - fx)
                + f64::from(source[(right, top)][channel]) * fx)
                * (1.0 - fy)
                + (f64::from(source[(left, bottom)][channel]) * (1.0 - fx)
                    + f64::from(source[(right, bottom)][channel]) * fx)
                    * fy)
                .round() as u8;
        }
        image::Rgb(result)
    })
}

fn screen_change_ratio(previous: &ScreenSample, current: &ScreenSample) -> Result<f64, String> {
    if previous.width != current.width || previous.height != current.height {
        return Ok(1.0);
    }
    let expected = (SAMPLE_WIDTH * SAMPLE_HEIGHT * 3) as usize;
    if previous.pixels.len() != expected || current.pixels.len() != expected {
        return Err("屏幕采样状态损坏".into());
    }
    let changed = previous
        .pixels
        .chunks_exact(3)
        .zip(current.pixels.chunks_exact(3))
        .filter(|(before, after)| {
            // Use luminance of RGB differences; ignore per-pixel changes below 20/255.
            (0.299 * f64::from(before[0].abs_diff(after[0]))
                + 0.587 * f64::from(before[1].abs_diff(after[1]))
                + 0.114 * f64::from(before[2].abs_diff(after[2])))
            .round()
                >= 20.0
        })
        .count();
    Ok(changed as f64 / f64::from(SAMPLE_WIDTH * SAMPLE_HEIGHT))
}

fn process_capture(
    image: RgbaImage,
    captured_at: &str,
    previous: Option<&ScreenSample>,
    threshold: Option<f64>,
) -> Result<(Value, Option<ScreenSample>), String> {
    check_dimensions(image.width(), image.height(), MAX_CAPTURE_PIXELS)?;
    let (width, height) = scaled_size(image.width(), image.height());
    // xcap supplies RGBA, not Electron's premultiplied BGRA. Screenshots are opaque RGB.
    let rgb = DynamicImage::ImageRgba8(image).into_rgb8();
    let rgb = if rgb.width() == width && rgb.height() == height {
        rgb
    } else {
        resize_rgb(&rgb, width, height)
    };
    let sample = ScreenSample {
        width,
        height,
        pixels: resize_rgb(&rgb, SAMPLE_WIDTH, SAMPLE_HEIGHT).into_raw(),
    };
    if let (Some(previous), Some(threshold)) = (previous, threshold) {
        if screen_change_ratio(previous, &sample)? < threshold {
            return Ok((json!({ "unchanged": true }), None));
        }
    }
    let mut png = Vec::new();
    PngEncoder::new(&mut png)
        .write_image(rgb.as_raw(), width, height, ExtendedColorType::Rgb8)
        .map_err(|error| format!("截图编码失败：{error}"))?;
    let digest = format!("{:x}", Sha256::digest(&png));
    Ok((
        json!({
            "base64": STANDARD.encode(&png), "captured_at": captured_at,
            "width": width, "height": height, "digest": digest,
        }),
        Some(sample),
    ))
}

async fn read_limited(reader: impl AsyncRead + Unpin) -> io::Result<Vec<u8>> {
    let mut output = Vec::new();
    reader
        .take((MAX_PROCESS_OUTPUT + 1) as u64)
        .read_to_end(&mut output)
        .await?;
    if output.len() > MAX_PROCESS_OUTPUT {
        return Err(io::Error::other("应用查询输出超过 2 MiB 限制"));
    }
    Ok(output)
}

async fn query_applications() -> Result<String, String> {
    #[cfg(windows)]
    let mut command = {
        let mut command = Command::new("powershell.exe");
        command.args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            WINDOWS_PROCESSES,
        ]);
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW; no flashing console on Windows.
        command
    };
    #[cfg(not(windows))]
    let mut command = {
        let mut command = Command::new("/bin/ps");
        command.args(["-A", "-o", "comm="]);
        command
    };
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|error| format!("读取本机应用失败：{error}"))?;
    let stdout = child.stdout.take().ok_or("应用查询未提供标准输出")?;
    let stderr = child.stderr.take().ok_or("应用查询未提供错误输出")?;
    let result = tokio::time::timeout(Duration::from_secs(10), async {
        tokio::try_join!(child.wait(), read_limited(stdout), read_limited(stderr))
    })
    .await;
    match result {
        Ok(Ok((status, output, _))) if status.success() => {
            String::from_utf8(output).map_err(|error| format!("应用查询未返回有效 UTF-8：{error}"))
        }
        Ok(Ok((status, _, error))) => Err(format!(
            "读取本机应用失败（{status}）：{}",
            String::from_utf8_lossy(&error)
                .chars()
                .take(500)
                .collect::<String>()
        )),
        failed => {
            // Dropping the wait future does not kill a process. Explicitly terminate and reap it.
            let _ = child.start_kill();
            let _ = child.wait().await;
            match failed {
                Err(_) => Err("读取本机应用超时".into()),
                Ok(Err(error)) => Err(format!("读取本机应用失败：{error}")),
                Ok(Ok(_)) => unreachable!(),
            }
        }
    }
}

fn parse_applications(output: &str, windows: bool) -> Result<Value, String> {
    let items: Vec<Value> = if windows {
        let data: Value = serde_json::from_str(output.trim_start_matches('\u{feff}'))
            .map_err(|error| format!("应用查询返回了无效 JSON：{error}"))?;
        let entries = match data {
            Value::Array(entries) => entries,
            entry => vec![entry],
        };
        entries
            .iter()
            .map(|entry| {
                let process = entry.get("ProcessName").and_then(Value::as_str);
                let title = entry.get("MainWindowTitle").and_then(Value::as_str);
                match (process, title) {
                    (Some(process), Some(title)) => {
                        Ok(json!({ "process": process, "title": title }))
                    }
                    _ => Err("应用查询返回了无效的进程信息".to_string()),
                }
            })
            .collect::<Result<_, _>>()?
    } else {
        // /bin/ps reports executable names on macOS/Linux. Window titles are unavailable here.
        output
            .lines()
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .map(|name| json!({ "process": name.rsplit('/').next().unwrap_or(name), "title": "" }))
            .collect()
    };
    Ok(json!({ "items": items }))
}

fn validate_clipboard_request(kind: &str, content: &str) -> Result<(), String> {
    match kind {
        "text" if content.len() <= 64_000 && content.chars().count() <= 16_000 => Ok(()),
        "text" => Err("复制文本不能超过 16000 字".into()),
        "image" if content.len() <= MAX_CLIPBOARD_BASE64 => Ok(()),
        _ => Err("不支持的复制类型或内容过大".into()),
    }
}

fn decode_clipboard_image(content: &str) -> Result<RgbaImage, String> {
    if content.is_empty() || content.len() > MAX_CLIPBOARD_BASE64 || content.len() % 4 != 0 {
        return Err("无效的 PNG 图片或内容过大".into());
    }
    let png = STANDARD
        .decode(content)
        .map_err(|_| "面板只支持有效的 PNG 图片")?;
    if STANDARD.encode(&png) != content
        || png.len() < 33
        || &png[..8] != PNG_SIGNATURE
        || &png[12..16] != b"IHDR"
        || png[8..12] != 13_u32.to_be_bytes()
    {
        return Err("面板只支持有效的 PNG 图片".into());
    }
    let width = u32::from_be_bytes(png[16..20].try_into().map_err(|_| "PNG 宽度缺失")?);
    let height = u32::from_be_bytes(png[20..24].try_into().map_err(|_| "PNG 高度缺失")?);
    check_dimensions(width, height, MAX_CLIPBOARD_PIXELS)?;
    let mut offset = 8;
    let mut ended = false;
    let mut has_data = false;
    // Validate chunk boundaries and every CRC, including IEND/ancillary chunks a decoder may skip.
    while offset + 12 <= png.len() {
        let length = u32::from_be_bytes(
            png[offset..offset + 4]
                .try_into()
                .map_err(|_| "PNG 块长度缺失")?,
        ) as usize;
        let end = offset
            .checked_add(length)
            .and_then(|end| end.checked_add(12))
            .ok_or("PNG 块长度溢出")?;
        if end > png.len() || (&png[offset + 4..offset + 8] == b"IHDR" && offset != 8) {
            return Err("PNG 数据结构无效".into());
        }
        let crc = u32::from_be_bytes(png[end - 4..end].try_into().map_err(|_| "PNG 校验值缺失")?);
        if crc32fast::hash(&png[offset + 4..end - 4]) != crc {
            return Err("PNG 校验失败".into());
        }
        let kind = &png[offset + 4..offset + 8];
        has_data |= kind == b"IDAT";
        offset = end;
        if kind == b"IEND" {
            ended = length == 0 && end == png.len();
            break;
        }
    }
    if !ended || !has_data {
        return Err("PNG 图片不完整".into());
    }
    let mut limits = Limits::default();
    limits.max_image_width = Some(width);
    limits.max_image_height = Some(height);
    limits.max_alloc = Some(64 * 1024 * 1024);
    let decoder = PngDecoder::with_limits(Cursor::new(png), limits)
        .map_err(|error| format!("PNG 图片解码失败：{error}"))?;
    if decoder.dimensions() != (width, height) {
        return Err("PNG 图片尺寸不一致".into());
    }
    // Return only pixels, preserving alpha and discarding arbitrary PNG metadata.
    DynamicImage::from_decoder(decoder)
        .map(DynamicImage::into_rgba8)
        .map_err(|error| format!("PNG 图片解码失败：{error}"))
}
