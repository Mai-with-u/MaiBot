use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::{fs, io::Read, path::PathBuf, sync::Mutex, time::SystemTime};

const MAX_ASSET_BYTES: u64 = 32 * 1024 * 1024;
const MAX_CACHE_BYTES: u64 = 256 * 1024 * 1024;
const MAX_MANIFEST_BYTES: u64 = 4 * 1024 * 1024;

pub struct SkinCache {
    directory: PathBuf,
    disk: Mutex<()>,
}

impl SkinCache {
    pub fn new(directory: PathBuf) -> Self {
        Self {
            directory,
            disk: Mutex::new(()),
        }
    }

    pub fn last_skin(&self, scope: &str) -> Option<Value> {
        let file = fs::File::open(self.directory.join(format!("{scope}.json"))).ok()?;
        let mut bytes = Vec::new();
        file.take(MAX_MANIFEST_BYTES + 1)
            .read_to_end(&mut bytes)
            .ok()?;
        if bytes.len() as u64 > MAX_MANIFEST_BYTES {
            return None;
        }
        let skin: Value = serde_json::from_slice(&bytes).ok()?;
        skin["id"].as_str()?;
        Some(skin)
    }

    pub fn remember(&self, scope: &str, skin: &Value) -> Result<(), String> {
        let bytes = serde_json::to_vec(skin).map_err(super::error)?;
        if bytes.len() as u64 > MAX_MANIFEST_BYTES {
            return Err("皮肤清单过大".into());
        }
        let _guard = self.disk.lock().map_err(|_| "皮肤缓存锁不可用")?;
        self.write_file(&format!("{scope}.json"), &bytes)
            .map_err(super::error)
    }

    pub fn import_inline(&self, scope: &str, skin: &Value) -> Result<Value, String> {
        let mut clean = Map::new();
        let mut total = 0;
        let _guard = self.disk.lock().map_err(|_| "皮肤缓存锁不可用")?;
        for (name, data) in skin["resources"].as_object().ok_or("皮肤资源清单无效")? {
            let url = data.as_str().ok_or("皮肤资源无效")?;
            let (format, bytes) = Self::decode_url(url)?;
            total += bytes.len() as u64;
            if total > MAX_ASSET_BYTES {
                return Err("皮肤资源超过大小限制".into());
            }
            let digest = format!("{:x}", Sha256::digest(&bytes));
            if Self::mime(&bytes).is_none_or(|mime| format != format!("data:{mime};base64")) {
                return Err("皮肤资源格式与内容不一致".into());
            }
            let path = self.directory.join(format!("{scope}-{digest}"));
            if !fs::metadata(&path).is_ok_and(|meta| meta.len() == bytes.len() as u64)
                || !fs::read(&path).is_ok_and(|cached| cached == bytes)
            {
                self.write(scope, &digest, &bytes).map_err(super::error)?;
            }
            clean.insert(name.clone(), Value::String(digest));
        }
        Ok(Value::Object(clean))
    }

    pub fn read(&self, scope: &str, digest: &str, prefetch: bool) -> Option<String> {
        let _guard = self.disk.lock().ok()?;
        let file = fs::File::open(self.directory.join(format!("{scope}-{digest}"))).ok()?;
        let mut bytes = Vec::new();
        (&file)
            .take(MAX_ASSET_BYTES + 1)
            .read_to_end(&mut bytes)
            .ok()?;
        let mime = Self::validate(&bytes, digest)?;
        let _ = file.set_modified(SystemTime::now());
        Some(if prefetch {
            String::new()
        } else {
            format!("data:{mime};base64,{}", STANDARD.encode(bytes))
        })
    }

    pub fn save(&self, scope: &str, digest: &str, url: &str) -> Result<(), String> {
        let (format, bytes) = Self::decode_url(url)?;
        let mime = Self::validate(&bytes, digest).ok_or("皮肤资源内容与清单不一致")?;
        if format != format!("data:{mime};base64") {
            return Err("皮肤资源格式与内容不一致".into());
        }
        let _guard = self.disk.lock().map_err(|_| "皮肤缓存锁不可用")?;
        if let Err(error) = self.write(scope, digest, &bytes) {
            // A full or read-only disk must not prevent the downloaded skin from displaying.
            super::report(format!("无法保存皮肤缓存：{error}"));
        }
        Ok(())
    }

    fn decode_url(url: &str) -> Result<(&str, Vec<u8>), String> {
        let (format, encoded) = url.split_once(',').ok_or("无效皮肤资源")?;
        if encoded.len() as u64 > MAX_ASSET_BYTES.div_ceil(3) * 4 {
            return Err("皮肤资源超过大小限制".into());
        }
        Ok((format, STANDARD.decode(encoded).map_err(super::error)?))
    }

    fn validate(bytes: &[u8], digest: &str) -> Option<&'static str> {
        if bytes.len() as u64 > MAX_ASSET_BYTES || format!("{:x}", Sha256::digest(bytes)) != digest
        {
            return None;
        }
        Self::mime(bytes)
    }

    fn mime(bytes: &[u8]) -> Option<&'static str> {
        if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
            Some("image/png")
        } else if bytes.starts_with(b"RIFF") && bytes.get(8..12) == Some(b"WEBP") {
            Some("image/webp")
        } else {
            None
        }
    }

    fn write_file(&self, name: &str, bytes: &[u8]) -> std::io::Result<()> {
        fs::create_dir_all(&self.directory)?;
        let temp = self.directory.join(super::id());
        let result =
            fs::write(&temp, bytes).and_then(|_| fs::rename(&temp, self.directory.join(name)));
        if result.is_err() {
            let _ = fs::remove_file(temp);
        }
        result
    }

    fn write(&self, scope: &str, digest: &str, bytes: &[u8]) -> std::io::Result<()> {
        let name = format!("{scope}-{digest}");
        self.write_file(&name, bytes)?;

        let mut total = bytes.len() as u64;
        let mut older = Vec::new();
        for entry in fs::read_dir(&self.directory)?.filter_map(Result::ok) {
            let filename = entry.file_name();
            let Some(key) = filename.to_str() else {
                continue;
            };
            if key == name
                || !(key
                    .strip_suffix(".json")
                    .is_some_and(|scope| super::hex(scope, 64))
                    || key.split_once('-').is_some_and(|(scope, digest)| {
                        super::hex(scope, 64) && super::hex(digest, 64)
                    }))
            {
                continue;
            }
            let Ok(meta) = entry.metadata() else {
                continue;
            };
            if meta.is_file() {
                total += meta.len();
                older.push((
                    meta.modified().unwrap_or(SystemTime::UNIX_EPOCH),
                    meta.len(),
                    entry.path(),
                ));
            }
        }
        if total > MAX_CACHE_BYTES {
            older.sort_unstable();
            for (_, len, path) in older {
                if fs::remove_file(path).is_ok() {
                    total -= len;
                }
                if total <= MAX_CACHE_BYTES {
                    break;
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disk_cache_persists_isolates_scopes_and_rejects_corruption() {
        let directory = std::env::temp_dir().join(super::super::id());
        let scope = "a".repeat(64);
        let bytes = b"\x89PNG\r\n\x1a\n";
        let digest = format!("{:x}", Sha256::digest(bytes));
        let url = format!("data:image/png;base64,{}", STANDARD.encode(bytes));
        SkinCache::new(directory.clone())
            .save(&scope, &digest, &url)
            .unwrap();
        let cache = SkinCache::new(directory.clone());
        assert_eq!(cache.read(&scope, &digest, false), Some(url.clone()));
        assert_eq!(cache.read(&scope, &digest, true), Some(String::new()));
        assert_eq!(cache.read(&"b".repeat(64), &digest, false), None);
        assert!(cache.save(&scope, &"c".repeat(64), &url).is_err());
        let file = directory.join(format!("{scope}-{digest}"));
        fs::write(&file, b"corrupt").unwrap();
        assert_eq!(cache.read(&scope, &digest, false), None);
        fs::remove_file(file).unwrap();
        fs::remove_dir(directory).unwrap();
    }
}
