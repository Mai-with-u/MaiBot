# Runs INSIDE Windows Sandbox only. No host input automation is used.
$ErrorActionPreference = 'Stop'
$exchange = 'C:\MaiExchange'
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-JsonAtomic($path, $value) {
    [IO.File]::WriteAllText("$path.tmp", ($value | ConvertTo-Json -Depth 32 -Compress), $utf8)
    [IO.File]::Move("$path.tmp", $path)
}

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class MaiInput {
    static readonly System.Collections.Generic.HashSet<ushort> pressedKeys = new System.Collections.Generic.HashSet<ushort>();
    static readonly System.Collections.Generic.HashSet<uint> pressedButtons = new System.Collections.Generic.HashSet<uint>();
    [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT {
        public int dx, dy;
        public uint mouseData, dwFlags, time;
        public UIntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT {
        public ushort wVk, wScan;
        public uint dwFlags, time;
        public UIntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Explicit)] public struct UNION {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }
    [StructLayout(LayoutKind.Sequential)] public struct INPUT {
        public uint type;
        public UNION u;
    }
    [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint n, INPUT[] inputs, int size);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int count);
    static void Send(INPUT input) {
        if (SendInput(1, new INPUT[] { input }, Marshal.SizeOf(typeof(INPUT))) != 1)
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    }
    public static void Mouse(uint flags, int data) {
        INPUT i = new INPUT();
        i.u.mi.dwFlags = flags;
        i.u.mi.mouseData = unchecked((uint)data);
        Send(i);
        if (flags == 2 || flags == 8 || flags == 32) pressedButtons.Add(flags);
        if (flags == 4 || flags == 16 || flags == 64) pressedButtons.Remove(flags / 2);
    }
    public static void Key(ushort key, bool up) {
        INPUT i = new INPUT(); i.type = 1; i.u.ki.wVk = key;
        i.u.ki.dwFlags = up ? 2u : 0u;
        if (key == 91 || (key >= 33 && key <= 40) || key == 45 || key == 46) i.u.ki.dwFlags |= 1;
        Send(i);
        if (up) pressedKeys.Remove(key); else pressedKeys.Add(key);
    }
    public static void ReleaseAll() {
        foreach (ushort key in new System.Collections.Generic.List<ushort>(pressedKeys)) Key(key, true);
        foreach (uint button in new System.Collections.Generic.List<uint>(pressedButtons)) Mouse(button * 2, 0);
    }
    public static void Text(string text) {
        foreach (char c in text) {
            if (c == '\r') continue;
            if (c == '\n' || c == '\t') {
                ushort key = (ushort)(c == '\n' ? 13 : 9);
                Key(key, false); Key(key, true); continue;
            }
            INPUT i = new INPUT(); i.type = 1; i.u.ki.wScan = c;
            i.u.ki.dwFlags = 4; Send(i);
            i.u.ki.dwFlags = 6; Send(i);
        }
    }
}
'@
    [void][MaiInput]::SetProcessDPIAware()
    $settings = [IO.File]::ReadAllText('C:\MaiBootstrap\settings.json') | ConvertFrom-Json
    $script:frameWidth = 0
    $script:frameHeight = 0
    $script:activeId = ''
    $script:nextHeartbeat = [DateTime]::MinValue

    function Release-Input {
        # 只释放代理实际按住的键；盲发右键 up 或 Apps 键 up 本身会弹出菜单。
        [MaiInput]::ReleaseAll()
    }

    function Check-Control {
        if ([DateTime]::UtcNow -ge $script:nextHeartbeat) {
            [IO.File]::WriteAllText("$exchange\heartbeat", $script:activeId, $utf8)
            $script:nextHeartbeat = [DateTime]::UtcNow.AddSeconds(2)
        }
        foreach ($file in @(Get-ChildItem -LiteralPath $exchange -Filter '*.observe.json')) {
            $observationId = $file.Name.Replace('.observe.json', '')
            [IO.File]::Delete($file.FullName)
            try {
                $observed = Get-Frame
                $observed.id = $observationId; $observed.success = $true
            } catch {
                $observed = @{ id=$observationId; success=$false; error=($_ | Out-String) + $_.ScriptStackTrace }
            }
            Write-JsonAtomic "$exchange\$observationId.result.json" $observed
        }
        if ($script:activeId -and [IO.File]::Exists("$exchange\$script:activeId.cancel")) {
            Release-Input
            throw [OperationCanceledException]::new('Operation cancelled; already completed actions are not rolled back.')
        }
    }

    function Wait-Responsive([double]$seconds) {
        if ($seconds -lt 0) { throw 'Duration cannot be negative' }
        $deadline = [DateTime]::UtcNow.AddSeconds($seconds)
        while ([DateTime]::UtcNow -lt $deadline) {
            Check-Control
            $remaining = [Math]::Max(1, [Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalMilliseconds))
            Start-Sleep -Milliseconds ([int][Math]::Min(50, $remaining))
        }
    }

    function Get-Frame {
        $bounds = [Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = New-Object Drawing.Bitmap($bounds.Width, $bounds.Height)
        $graphics = [Drawing.Graphics]::FromImage($bitmap)
        $scaled = $null
        $canvas = $null
        $stream = New-Object IO.MemoryStream
        try {
            $graphics.CopyFromScreen($bounds.Location, [Drawing.Point]::Empty, $bounds.Size)
            $scale = 1.0
            if ($settings.screenshot_max_edge -gt 0) {
                $scale = [Math]::Min(1.0, $settings.screenshot_max_edge / [Math]::Max($bounds.Width, $bounds.Height))
            }
            $script:frameWidth = [int][Math]::Floor($bounds.Width * $scale)
            $script:frameHeight = [int][Math]::Floor($bounds.Height * $scale)
            $scaled = New-Object Drawing.Bitmap($script:frameWidth, $script:frameHeight)
            $canvas = [Drawing.Graphics]::FromImage($scaled)
            $canvas.DrawImage($bitmap, 0, 0, $script:frameWidth, $script:frameHeight)
            $codec = @([Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object MimeType -eq 'image/jpeg')[0]
            $encoding = New-Object Drawing.Imaging.EncoderParameters 1
            try {
                $encoding.Param[0] = New-Object Drawing.Imaging.EncoderParameter([Drawing.Imaging.Encoder]::Quality, [long]90)
                $scaled.Save($stream, $codec, $encoding)
            } finally { $encoding.Dispose() }
            return @{
                image = [Convert]::ToBase64String($stream.ToArray())
                mime_type = 'image/jpeg'
                width = $script:frameWidth; height = $script:frameHeight
                capture_id = [Guid]::NewGuid().ToString('N'); captured_at = [DateTime]::UtcNow.ToString('o')
                native_width = $bounds.Width; native_height = $bounds.Height
                active_window = Get-WindowTitle
            }
        } finally {
            if ($canvas) { $canvas.Dispose() }
            if ($scaled) { $scaled.Dispose() }
            $graphics.Dispose(); $bitmap.Dispose(); $stream.Dispose()
        }
    }

    function Get-WindowTitle {
        $title = New-Object Text.StringBuilder 1024
        [void][MaiInput]::GetWindowText([MaiInput]::GetForegroundWindow(), $title, $title.Capacity)
        return $title.ToString()
    }

    function Inspect-UI($request) {
        $depth = 2; $limit = 100
        if ($null -ne $request.depth) { $depth = [int]$request.depth }
        if ($null -ne $request.limit) { $limit = [int]$request.limit }
        $root = [Windows.Automation.AutomationElement]::FromHandle([MaiInput]::GetForegroundWindow())
        if ($null -eq $root) { throw 'No foreground window' }
        $queue = New-Object Collections.Queue
        $queue.Enqueue(@{ element=$root; depth=0 })
        $items = New-Object Collections.ArrayList
        $walker = [Windows.Automation.TreeWalker]::ControlViewWalker
        while ($queue.Count -gt 0 -and ($limit -eq 0 -or $items.Count -lt $limit)) {
            Check-Control
            $entry = $queue.Dequeue(); $element = $entry.element; $current = $element.Current
            $bounds = $current.BoundingRectangle
            [void]$items.Add(@{ runtime_id=($element.GetRuntimeId() -join '.'); name=$current.Name;
                automation_id=$current.AutomationId; type=$current.ControlType.ProgrammaticName;
                enabled=$current.IsEnabled; focused=$current.HasKeyboardFocus;
                bounds=@{ x=$bounds.X; y=$bounds.Y; width=$bounds.Width; height=$bounds.Height } })
            if ($entry.depth -lt $depth) {
                $child = $walker.GetFirstChild($element)
                while ($null -ne $child) { $queue.Enqueue(@{ element=$child; depth=$entry.depth+1 }); $child=$walker.GetNextSibling($child) }
            }
        }
        return @{ controls=@($items); more=($queue.Count -gt 0); active_window=(Get-WindowTitle) }
    }

    function Invoke-UI($request) {
        $wanted = @([string]$request.runtime_id -split '\.' | ForEach-Object { [int]$_ })
        $root = [Windows.Automation.AutomationElement]::FromHandle([MaiInput]::GetForegroundWindow())
        $condition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::RuntimeIdProperty, [int[]]$wanted)
        $element = $root.FindFirst([Windows.Automation.TreeScope]::Subtree, $condition)
        if ($null -eq $element) { throw 'UIA target no longer exists; inspect again' }
        switch ($request.operation) {
            'focus' { $element.SetFocus() }
            'invoke' { ([Windows.Automation.InvokePattern]$element.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)).Invoke() }
            'set_value' { ([Windows.Automation.ValuePattern]$element.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern)).SetValue([string]$request.value) }
            'select' { ([Windows.Automation.SelectionItemPattern]$element.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern)).Select() }
        }
        return @{ runtime_id=$request.runtime_id; operation=$request.operation }
    }

    function Invoke-Browser($request) {
        # CDP 仅连接沙盒内的浏览器，不开放宿主机浏览器或调试端口。
        $targets = Invoke-RestMethod 'http://127.0.0.1:9222/json/list' -UseBasicParsing
        if ($request.method -eq 'targets') { return @{ targets=$targets } }
        $pages = @($targets | Where-Object { $_.type -eq 'page' -and (-not $request.target -or $_.id -eq $request.target) })
        if ($pages.Count -eq 0) { throw 'No matching CDP page. Launch Edge with --remote-debugging-port=9222 --user-data-dir=C:\MaiWorkspace\browser' }
        $socket = New-Object Net.WebSockets.ClientWebSocket
        $token = [Threading.CancellationToken]::None
        try {
            $task = $socket.ConnectAsync([Uri]($pages[0].webSocketDebuggerUrl), $token)
            while (-not $task.IsCompleted) { Check-Control; Start-Sleep -Milliseconds 50 }; [void]$task.GetAwaiter().GetResult()
            $params = @{}; if ($null -ne $request.params) { $params = $request.params }
            $bytes = $utf8.GetBytes((@{ id=1; method=$request.method; params=$params } | ConvertTo-Json -Depth 32 -Compress))
            [void]$socket.SendAsync([ArraySegment[byte]]::new($bytes), [Net.WebSockets.WebSocketMessageType]::Text, $true, $token).GetAwaiter().GetResult()
            do {
                $stream = New-Object IO.MemoryStream
                try {
                    do {
                        $buffer = New-Object byte[] 16384
                        $task = $socket.ReceiveAsync([ArraySegment[byte]]::new($buffer), $token)
                        while (-not $task.IsCompleted) { Check-Control; Start-Sleep -Milliseconds 50 }
                        $received = $task.GetAwaiter().GetResult()
                        if ($received.MessageType -eq [Net.WebSockets.WebSocketMessageType]::Close) { throw 'CDP connection closed' }
                        $stream.Write($buffer, 0, $received.Count)
                    } until ($received.EndOfMessage)
                    $message = $utf8.GetString($stream.ToArray()) | ConvertFrom-Json
                } finally { $stream.Dispose() }
            } until ($message.id -eq 1)
            if ($message.error) { throw ($message.error | ConvertTo-Json -Compress) }
            return @{ target=$pages[0].id; result=$message.result }
        } finally { $socket.Dispose() }
    }

    function Move-Pointer($x, $y) {
        if ($script:frameWidth -eq 0 -or $x -lt 0 -or $y -lt 0 -or
            $x -ge $script:frameWidth -or $y -ge $script:frameHeight) {
            throw 'Pointer is outside the last screenshot'
        }
        $bounds = [Windows.Forms.Screen]::PrimaryScreen.Bounds
        $nativeX = $bounds.X + [int][Math]::Floor($x * $bounds.Width / $script:frameWidth)
        $nativeY = $bounds.Y + [int][Math]::Floor($y * $bounds.Height / $script:frameHeight)
        if (-not [MaiInput]::SetCursorPos($nativeX, $nativeY)) { throw 'SetCursorPos failed' }
    }

    function Require-Fields($request, [string[]]$names) {
        foreach ($name in $names) {
            if ($null -eq $request.$name) { throw "Missing action field: $name" }
        }
    }

    function Key-Code([string]$key) {
        $aliases = @{ CTRL=17; ALT=18; SHIFT=16; WIN=91; ENTER=13; ESC=27; BACKSPACE=8; PAGEUP=33; PAGEDOWN=34 }
        $key = $key.ToUpperInvariant()
        if ($aliases.ContainsKey($key)) { return [UInt16]$aliases[$key] }
        if ($key -match '^[A-Z0-9]$') { return [UInt16][char]$key }
        return [UInt16][Enum]::Parse([Windows.Forms.Keys], $key, $true)
    }

    function Button-Flags($request) {
        $button = 'left'
        if ($null -ne $request.button) { $button = [string]$request.button }
        switch ($button) {
            'left' { return @(2, 4) }
            'right' { return @(8, 16) }
            'middle' { return @(32, 64) }
            default { throw "Unknown mouse button: $button" }
        }
    }

    function Invoke-Shell($request) {
        Require-Fields $request @('command')
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = 'powershell.exe'
        $script = '$ErrorActionPreference = "Stop"; $ProgressPreference = "SilentlyContinue"; [Console]::OutputEncoding = [Text.Encoding]::UTF8; ' + [string]$request.command
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
        $info.Arguments = "-NoProfile -NonInteractive -EncodedCommand $encoded"
        $info.WorkingDirectory = 'C:\MaiWorkspace'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardOutputEncoding = [Text.Encoding]::UTF8
        $info.StandardErrorEncoding = [Text.Encoding]::UTF8
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $info
        try {
            [void]$process.Start()
            $stdout = $process.StandardOutput.ReadToEndAsync()
            $stderr = $process.StandardError.ReadToEndAsync()
            while (-not $process.HasExited) { Check-Control; Start-Sleep -Milliseconds 50 }
            return @{ exit_code=$process.ExitCode; stdout=$stdout.GetAwaiter().GetResult(); stderr=$stderr.GetAwaiter().GetResult() }
        } catch {
            if (-not $process.HasExited) {
                # 取消同步命令时终止它的进程树；此前由 open 启动的应用不属于此命令。
                & taskkill.exe /PID $process.Id /T /F | Out-Null
            }
            throw
        } finally { $process.Dispose() }
    }

    function Invoke-Action($request) {
        switch ($request.action) {
            'observe' { }
            'release' { Release-Input }
            'move' { Require-Fields $request @('x', 'y'); Move-Pointer $request.x $request.y }
            { $_ -in 'click', 'double_click' } {
                Require-Fields $request @('x', 'y')
                Move-Pointer $request.x $request.y
                $down, $up = Button-Flags $request
                $count = 1
                if ($request.action -eq 'double_click') { $count = 2 }
                for ($i = 0; $i -lt $count; $i++) {
                    try { [MaiInput]::Mouse($down, 0) }
                    finally { [MaiInput]::Mouse($up, 0) }
                    Start-Sleep -Milliseconds 80
                }
            }
            'drag' {
                Require-Fields $request @('x', 'y', 'end_x', 'end_y')
                Move-Pointer $request.x $request.y
                $duration = 0.5
                if ($null -ne $request.seconds) { $duration = [double]$request.seconds }
                if ($duration -lt 0) { throw 'Duration cannot be negative' }
                $steps = [Math]::Max(1, [int][Math]::Ceiling($duration * 60))
                $down, $up = Button-Flags $request
                try {
                    [MaiInput]::Mouse($down, 0)
                    for ($i = 1; $i -le $steps; $i++) {
                        Move-Pointer ($request.x + ($request.end_x - $request.x) * $i / $steps) `
                            ($request.y + ($request.end_y - $request.y) * $i / $steps)
                        if ($duration -gt 0) { Wait-Responsive ($duration / $steps) }
                    }
                } finally { [MaiInput]::Mouse($up, 0) }
            }
            'mouse_down' { $down, $up = Button-Flags $request; [MaiInput]::Mouse($down, 0) }
            'mouse_up' { $down, $up = Button-Flags $request; [MaiInput]::Mouse($up, 0) }
            'scroll' { Require-Fields $request @('delta'); [MaiInput]::Mouse(0x0800, [int]$request.delta * 120) }
            'key_down' { Require-Fields $request @('keys'); foreach ($key in $request.keys) { [MaiInput]::Key((Key-Code $key), $false) } }
            'key_up' { Require-Fields $request @('keys'); foreach ($key in $request.keys) { [MaiInput]::Key((Key-Code $key), $true) } }
            'keys' {
                Require-Fields $request @('keys')
                $pressed = New-Object 'Collections.Generic.List[UInt16]'
                try {
                    foreach ($key in $request.keys) {
                        $vk = Key-Code $key
                        $pressed.Add([UInt16]$vk)
                        [MaiInput]::Key([UInt16]$vk, $false)
                    }
                    if ($null -ne $request.seconds) { Wait-Responsive ([double]$request.seconds) }
                } finally {
                    for ($i = $pressed.Count - 1; $i -ge 0; $i--) { [MaiInput]::Key($pressed[$i], $true) }
                }
            }
            'type' {
                Require-Fields $request @('text')
                $text = [string]$request.text
                for ($offset = 0; $offset -lt $text.Length; $offset += 128) {
                    Check-Control
                    [MaiInput]::Text($text.Substring($offset, [Math]::Min(128, $text.Length - $offset)))
                }
            }
            'open' {
                Require-Fields $request @('path')
                $options = @{ FilePath=[string]$request.path; WorkingDirectory='C:\MaiWorkspace'; PassThru=$true }
                if ($request.arguments) { $options.ArgumentList = @($request.arguments) }
                $opened = Start-Process @options
                return @{ pid=$opened.Id }
            }
            'shell' { return Invoke-Shell $request }
            'inspect' { return Inspect-UI $request }
            'uia' { return Invoke-UI $request }
            'browser' { return Invoke-Browser $request }
            'wait' { Require-Fields $request @('seconds'); Wait-Responsive ([double]$request.seconds) }
            'wait_window' {
                $deadline = [DateTime]::UtcNow.AddSeconds([double]$request.seconds)
                while ((Get-WindowTitle) -notlike "*$($request.title)*") {
                    if ([DateTime]::UtcNow -ge $deadline) { throw "Window did not appear: $($request.title)" }
                    Wait-Responsive 0.1
                }
                return @{ active_window=(Get-WindowTitle) }
            }
            'wait_process' {
                $deadline = [DateTime]::UtcNow.AddSeconds([double]$request.seconds)
                while (Get-Process -Id ([int]$request.pid) -ErrorAction SilentlyContinue) {
                    if ([DateTime]::UtcNow -ge $deadline) { throw "Process is still running: $($request.pid)" }
                    Wait-Responsive 0.1
                }
                return @{ exited=$true; pid=$request.pid }
            }
            default { throw 'Unsupported desktop action' }
        }
        return @{}
    }

    foreach ($unfinished in @(Get-ChildItem -LiteralPath $exchange -Filter '*.running.json')) {
        $unfinishedId = $unfinished.Name.Replace('.running.json', '')
        if (-not [IO.File]::Exists("$exchange\$unfinishedId.result.json")) {
            Write-JsonAtomic "$exchange\$unfinishedId.result.json" @{ id=$unfinishedId; success=$false; status='failed';
                error='Guest agent restarted during this operation. Outcome is unknown; inspect the application before deciding what to do. Input was not replayed.' }
        }
    }
    Write-JsonAtomic "$exchange\ready.json" @{ success = $true; protocol=2 }
    while ($true) {
        Check-Control
        $requests = @(Get-ChildItem -LiteralPath $exchange -Filter '*.request.json' | Sort-Object Name)
        foreach ($file in $requests) {
            $id = $file.Name.Replace('.request.json', '')
            if ($id -notmatch '^[0-9a-f]{32}$') { continue }
            $outputs = @()
            try {
                $requestText = [IO.File]::ReadAllText($file.FullName, $utf8)
                # 持久认领后执行；失联后不得重放输入，running 文件也供重启后核对。
                [IO.File]::Move($file.FullName, "$exchange\$id.running.json")
                $script:activeId = $id
                $request = $requestText | ConvertFrom-Json
                if ($request.id -ne $id) { throw 'Request ID mismatch' }
                foreach ($action in $request.actions) {
                    Check-Control
                    $output = Invoke-Action $action
                    $outputs += @{ action=$action.action; result=$output }
                    if ($output.ContainsKey('exit_code') -and $output.exit_code -ne 0) {
                        throw "Shell exited with code $($output.exit_code): $($output.stderr)"
                    }
                }
                $result = @{ success=$true; id=$id; results=$outputs }
            } catch {
                $result = @{ success=$false; id=$id; results=$outputs; error=($_ | Out-String) + $_.ScriptStackTrace }
                if ($_.Exception -is [OperationCanceledException]) { $result.status = 'cancelled' }
            }
            $script:activeId = ''
            # 普通动作失败仍返回现场截图，让模型修正；不关闭桌面或伪装成功。
            try {
                $frame = Get-Frame
                foreach ($key in $frame.Keys) { $result[$key] = $frame[$key] }
            } catch {
                $result.success = $false
                $result.capture_error = ($_ | Out-String) + $_.ScriptStackTrace
            }
            Write-JsonAtomic "$exchange\$id.result.json" $result
        }
        Start-Sleep -Milliseconds 100
    }
} catch {
    [IO.File]::WriteAllText("$exchange\agent-error.txt", $_.Exception.ToString(), $utf8)
    if (-not [IO.File]::Exists("$exchange\ready.json")) {
        Write-JsonAtomic "$exchange\ready.json" @{ success=$false; error=$_.Exception.ToString() }
    }
    exit 1
}
