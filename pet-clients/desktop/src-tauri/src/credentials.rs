use serde::{de::DeserializeOwned, Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fs, io::ErrorKind, path::PathBuf};
use tauri::Url;
use win32::Win32::{
    Foundation::{LocalFree, HLOCAL},
    Security::Cryptography::{
        CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    },
};
use windows_core::PCWSTR;

#[derive(Clone, Deserialize, Serialize)]
pub struct BasicLogin {
    pub username: String,
    pub password: String,
    pub challenge: String,
}

pub struct Store {
    directory: PathBuf,
    scope: [u8; 32],
}

pub fn can_remember(url: &Url) -> bool {
    url.scheme() == "https" || matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"))
}

impl Store {
    pub fn new(directory: PathBuf, server: &str) -> Self {
        Self {
            directory,
            scope: Sha256::digest(server.as_bytes()).into(),
        }
    }

    pub fn load<T: DeserializeOwned>(&self, kind: &str) -> Result<Option<T>, String> {
        let ciphertext = match fs::read(self.directory.join(kind)) {
            Ok(bytes) => bytes,
            Err(e) if e.kind() == ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(e.to_string()),
        };
        let data = self.crypt(&ciphertext, false)?;
        serde_json::from_slice(&data)
            .map(Some)
            .map_err(|_| "本机登录凭据格式无效".into())
    }

    pub fn save<T: Serialize>(&self, kind: &str, value: &T) -> Result<(), String> {
        let data = serde_json::to_vec(value).map_err(|_| "无法编码本机登录凭据")?;
        let ciphertext = self.crypt(&data, true)?;
        fs::create_dir_all(&self.directory).map_err(|e| e.to_string())?;
        let temp = self.directory.join(super::id());
        let result =
            fs::write(&temp, ciphertext).and_then(|_| fs::rename(&temp, self.directory.join(kind)));
        if result.is_err() {
            let _ = fs::remove_file(temp);
        }
        result.map_err(|e| e.to_string())
    }

    pub fn forget(&self, kind: &str) -> Result<(), String> {
        match fs::remove_file(self.directory.join(kind)) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == ErrorKind::NotFound => Ok(()),
            Err(e) => Err(e.to_string()),
        }
    }

    fn crypt(&self, data: &[u8], protect: bool) -> Result<Vec<u8>, String> {
        let blob = |slice: &[u8]| CRYPT_INTEGER_BLOB {
            cbData: slice.len() as u32,
            pbData: slice.as_ptr().cast_mut(),
        };
        let input = blob(data);
        let entropy = blob(&self.scope);
        let mut output = CRYPT_INTEGER_BLOB::default();
        // DPAPI defaults to the current user's Windows profile (not the whole machine).
        unsafe {
            if protect {
                CryptProtectData(
                    &input,
                    PCWSTR::null(),
                    Some(&entropy),
                    None,
                    None,
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut output,
                )
            } else {
                CryptUnprotectData(
                    &input,
                    None,
                    Some(&entropy),
                    None,
                    None,
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut output,
                )
            }
            .map_err(|e| e.to_string())?;
            let result = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
            let _ = LocalFree(Some(HLOCAL(output.pbData.cast())));
            Ok(result)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_user_can_decrypt_but_another_server_cannot() {
        let directory = std::env::temp_dir().join(super::super::id());
        let store = Store::new(directory.clone(), "https://one.example/pet");
        let token = "example-secret-do-not-store-in-plaintext".to_string();
        store.save("webui", &token).unwrap();
        assert_eq!(store.load::<String>("webui").unwrap(), Some(token.clone()));
        let raw = fs::read(directory.join("webui")).unwrap();
        assert!(!raw
            .windows(token.len())
            .any(|chunk| chunk == token.as_bytes()));
        assert!(Store::new(directory.clone(), "https://two.example/pet")
            .load::<String>("webui")
            .is_err());
        store.forget("webui").unwrap();
        fs::remove_dir(directory).unwrap();
    }
}
