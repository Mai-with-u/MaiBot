param([switch]$DebugBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (!$DebugBuild -and !$env:MAIBOT_ANDROID_KEYSTORE) { throw 'Release APK requires MAIBOT_ANDROID_KEYSTORE and MAIBOT_ANDROID_STORE_PASSWORD; use -DebugBuild for a test APK.' }
Push-Location $projectRoot
try {
    $variant = if ($DebugBuild) { 'debug' } else { 'release' }
    $task = if ($DebugBuild) { ':app:assembleDebug' } else { ':app:assembleRelease' }
    & ./gradlew.bat $task --console=plain
    if ($LASTEXITCODE -ne 0) { throw 'Android build failed' }
    $source = Join-Path $projectRoot "app/build/outputs/apk/$variant/app-$variant.apk"
    $destination = Join-Path (Join-Path $projectRoot '../..') 'dist/android-pet-client'
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $suffix = if ($DebugBuild) { '-debug' } else { '' }
    $fileName = "maibot-android-pet-1.0.1$suffix.apk"
    Copy-Item -LiteralPath $source -Destination (Join-Path $destination $fileName)
    $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $destination "$fileName.sha256"), "$hash  $fileName`n")
    Get-Item (Join-Path $destination $fileName) | Select-Object FullName,Length
} finally { Pop-Location }
