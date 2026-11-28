use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::{collections::HashSet, fs, io::Read, path::PathBuf, sync::Mutex, time::SystemTime};

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
        self.remember_with_limit(scope, skin, MAX_CACHE_BYTES)
    }

    fn remember_with_limit(
        &self,
        scope: &str,
        skin: &Value,
        cache_limit: u64,
    ) -> Result<(), String> {
        let bytes = serde_json::to_vec(skin).map_err(super::error)?;
        if bytes.len() as u64 > MAX_MANIFEST_BYTES {
            return Err("皮肤清单过大".into());
        }
        let _guard = self.disk.lock().map_err(|_| "皮肤缓存锁不可用")?;
        let name = format!("{scope}.json");
        self.write_file(&name, &bytes).map_err(super::error)?;
        let path = self.directory.join(name);
        let protected = skin["resources"]
            .as_object()
            .into_iter()
            .flat_map(|resources| resources.values())
            .filter_map(Value::as_str)
            .filter(|digest| super::hex(digest, 64))
            .map(|digest| self.directory.join(format!("{scope}-{digest}")))
            .collect();
        self.enforce_limit(cache_limit, &protected, Some(&path))
            .map_err(super::error)
    }

    pub fn import_inline(&self, scope: &str, skin: &Value) -> Result<Value, String> {
        self.import_inline_with_limit(scope, skin, MAX_CACHE_BYTES)
    }

    fn import_inline_with_limit(
        &self,
        scope: &str,
        skin: &Value,
        cache_limit: u64,
    ) -> Result<Value, String> {
        let mut clean = Map::new();
        let mut total = 0;
        let mut assets = Vec::new();
        let mut protected = HashSet::new();
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
            protected.insert(self.directory.join(format!("{scope}-{digest}")));
            assets.push((name.clone(), digest, bytes));
        }
        for (name, digest, bytes) in assets {
            let path = self.directory.join(format!("{scope}-{digest}"));
            if !fs::metadata(&path).is_ok_and(|meta| meta.len() == bytes.len() as u64)
                || !fs::read(&path).is_ok_and(|cached| cached == bytes)
            {
                self.write_with_limit(scope, &digest, &bytes, cache_limit, &protected)
                    .map_err(super::error)?;
            }
            clean.insert(name, Value::String(digest));
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
        self.write_with_limit(scope, digest, bytes, MAX_CACHE_BYTES, &HashSet::new())
    }

    fn write_with_limit(
        &self,
        scope: &str,
        digest: &str,
        bytes: &[u8],
        cache_limit: u64,
        protected: &HashSet<PathBuf>,
    ) -> std::io::Result<()> {
        let name = format!("{scope}-{digest}");
        self.write_file(&name, bytes)?;
        let path = self.directory.join(name);
        self.enforce_limit(cache_limit, protected, Some(&path))
    }

    fn enforce_limit(
        &self,
        cache_limit: u64,
        protected: &HashSet<PathBuf>,
        keep: Option<&PathBuf>,
    ) -> std::io::Result<()> {
        let mut total = 0;
        let mut older = Vec::new();
        for entry in fs::read_dir(&self.directory)?.filter_map(Result::ok) {
            let filename = entry.file_name();
            let Some(key) = filename.to_str() else {
                continue;
            };
            if !(key
                .strip_suffix(".json")
                .is_some_and(|scope| super::hex(scope, 64))
                || key
                    .split_once('-')
                    .is_some_and(|(scope, digest)| super::hex(scope, 64) && super::hex(digest, 64)))
            {
                continue;
            }
            let Ok(meta) = entry.metadata() else {
                continue;
            };
            if meta.is_file() {
                total += meta.len();
                let path = entry.path();
                if keep != Some(&path) && !protected.contains(&path) {
                    older.push((
                        meta.modified().unwrap_or(SystemTime::UNIX_EPOCH),
                        meta.len(),
                        path,
                    ));
                }
            }
        }
        if total > cache_limit {
            older.sort_unstable();
            for (_, len, path) in older {
                if fs::remove_file(path).is_ok() {
                    total -= len;
                }
                if total <= cache_limit {
                    break;
                }
            }
        }
        if total > cache_limit {
            return Err(std::io::Error::new(
                std::io::ErrorKind::Other,
                "skin cache exceeds its size limit",
            ));
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

    #[test]
    fn inline_import_keeps_every_asset_referenced_by_the_skin() {
        let directory = std::env::temp_dir().join(super::super::id());
        let scope = "a".repeat(64);
        let first = b"\x89PNG\r\n\x1a\n1";
        let second = b"\x89PNG\r\n\x1a\n2";
        let first_digest = format!("{:x}", Sha256::digest(first));
        let second_digest = format!("{:x}", Sha256::digest(second));
        fs::create_dir_all(&directory).unwrap();
        let first_path = directory.join(format!("{scope}-{first_digest}"));
        fs::write(&first_path, first).unwrap();
        fs::OpenOptions::new()
            .write(true)
            .open(&first_path)
            .unwrap()
            .set_modified(SystemTime::UNIX_EPOCH)
            .unwrap();
        fs::write(directory.join(format!("{scope}-{}", "b".repeat(64))), b"x").unwrap();

        let skin = serde_json::json!({
            "resources": {
                "first": format!("data:image/png;base64,{}", STANDARD.encode(first)),
                "second": format!("data:image/png;base64,{}", STANDARD.encode(second)),
            }
        });
        let cache = SkinCache::new(directory.clone());
        let resources = cache.import_inline_with_limit(&scope, &skin, 18).unwrap();

        assert_eq!(resources["first"], first_digest);
        assert_eq!(resources["second"], second_digest);
        assert!(directory.join(format!("{scope}-{first_digest}")).exists());
        assert!(directory.join(format!("{scope}-{second_digest}")).exists());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn remember_enforces_the_cache_limit_including_the_manifest() {
        let directory = std::env::temp_dir().join(super::super::id());
        let scope = "a".repeat(64);
        let old_asset = directory.join(format!("{scope}-{}", "b".repeat(64)));
        let first_digest = "c".repeat(64);
        let first_asset = directory.join(format!("{scope}-{first_digest}"));
        let manifest = serde_json::json!({"id": "skin", "resources": {"first": first_digest}});
        let cache_limit = serde_json::to_vec(&manifest).unwrap().len() as u64 + 1;
        fs::create_dir_all(&directory).unwrap();
        fs::write(&first_asset, b"x").unwrap();
        fs::OpenOptions::new()
            .write(true)
            .open(&first_asset)
            .unwrap()
            .set_modified(SystemTime::UNIX_EPOCH)
            .unwrap();
        fs::write(&old_asset, b"12345").unwrap();

        let cache = SkinCache::new(directory.clone());
        cache
            .remember_with_limit(&scope, &manifest, cache_limit)
            .unwrap();

        assert!(!old_asset.exists());
        assert!(first_asset.exists());
        assert_eq!(cache.last_skin(&scope), Some(manifest));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn inline_import_failure_leaves_inline_resources_available() {
        let directory = std::env::temp_dir().join(super::super::id());
        fs::write(&directory, b"not a directory").unwrap();
        let scope = "a".repeat(64);
        let skin = serde_json::json!({
            "resources": {
                "image": format!("data:image/png;base64,{}", STANDARD.encode(b"\x89PNG\r\n\x1a\n")),
            }
        });

        assert!(SkinCache::new(directory.clone())
            .import_inline_with_limit(&scope, &skin, MAX_CACHE_BYTES)
            .is_err());
        assert!(skin["resources"]["image"]
            .as_str()
            .unwrap()
            .starts_with("data:image/png;base64,"));
        fs::remove_file(directory).unwrap();
    }
}
