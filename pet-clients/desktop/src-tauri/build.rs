fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "login_call",
            "pet_call",
            "page_result",
            "page_open_external",
            "transport_message",
        ]),
    ))
    .expect("failed to build desktop pet resources");
}
