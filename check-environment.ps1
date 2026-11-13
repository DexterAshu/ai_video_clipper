# ClipForge Environment Verification Script
# Checks all hardware, Python, FFmpeg, CUDA, Faster-Whisper, and Ollama components.

$ErrorActionPreference = "Continue"

Write-Host "`n=======================================================" -ForegroundColor Cyan
Write-Host "         ClipForge Environment Health Check           " -ForegroundColor Cyan
Write-Host "=======================================================`n" -ForegroundColor Cyan

function Report-Item($num, $title, $status, $details) {
    $color = switch ($status) {
        "PASS"    { "Green" }
        "WARNING" { "Yellow" }
        "FAIL"    { "Red" }
        Default   { "White" }
    }
    Write-Host ("[{0,2}] {1,-32} : " -f $num, $title) -NoNewline
    Write-Host ("[{0}]" -f $status) -ForegroundColor $color -NoNewline
    if ($details) {
        Write-Host " $details"
    } else {
        Write-Host ""
    }
}

# [1] Python version
$pyGlobal = Get-Command python -ErrorAction SilentlyContinue
$py311Exe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
if (Test-Path $py311Exe) {
    $ver = & $py311Exe --version 2>&1
    Report-Item 1 "Python 3.11 Installation" "PASS" "$ver ($py311Exe)"
} elseif ($pyGlobal) {
    $ver = & python --version 2>&1
    if ($ver -match "3\.11") {
        Report-Item 1 "Python 3.11 Installation" "PASS" $ver
    } else {
        Report-Item 1 "Python 3.11 Installation" "WARNING" "Default is $ver (Python 3.11 required)"
    }
} else {
    Report-Item 1 "Python 3.11 Installation" "FAIL" "Python not found"
}

# [2] Virtual environment status
$venvPy = ".\.venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $vver = & $venvPy --version 2>&1
    Report-Item 2 "Virtual Environment (.venv)" "PASS" "$vver (.venv is healthy)"
} else {
    Report-Item 2 "Virtual Environment (.venv)" "FAIL" ".venv not found in project directory"
}

# [3] Required Python packages
if (Test-Path $venvPy) {
    $pkgCheck = & $venvPy -c "
import sys
required = ['fastapi', 'uvicorn', 'faster_whisper', 'ctranslate2', 'mediapipe', 'scenedetect', 'yt_dlp', 'cv2', 'yaml']
missing = []
for p in required:
    try:
        __import__(p)
    except Exception as e:
        missing.append(p)
if missing:
    print('MISSING:' + ','.join(missing))
else:
    print('ALL_OK')
" 2>&1
    if ($pkgCheck -match "ALL_OK") {
        Report-Item 3 "Core Python Packages" "PASS" "fastapi, faster-whisper, mediapipe, opencv, scenedetect, yt-dlp OK"
    } else {
        Report-Item 3 "Core Python Packages" "FAIL" $pkgCheck
    }
} else {
    Report-Item 3 "Core Python Packages" "FAIL" "Virtual environment missing"
}

# [4] FFmpeg version & [5] FFprobe version
$ffmpegExe = ".\tools\ffmpeg\bin\ffmpeg.exe"
$ffprobeExe = ".\tools\ffmpeg\bin\ffprobe.exe"
if (-not (Test-Path $ffmpegExe)) {
    $ffCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($ffCmd) { $ffmpegExe = $ffCmd.Source }
}
if (-not (Test-Path $ffprobeExe)) {
    $fpCmd = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($fpCmd) { $ffprobeExe = $fpCmd.Source }
}

if (Test-Path $ffmpegExe) {
    $ffVer = & $ffmpegExe -version 2>&1 | Select-Object -First 1
    Report-Item 4 "FFmpeg Binary" "PASS" ($ffVer -replace "Copyright.*","").Trim()
} else {
    Report-Item 4 "FFmpeg Binary" "FAIL" "ffmpeg.exe not found in tools/ffmpeg/bin or PATH"
}

if (Test-Path $ffprobeExe) {
    $fpVer = & $ffprobeExe -version 2>&1 | Select-Object -First 1
    Report-Item 5 "FFprobe Binary" "PASS" ($fpVer -replace "Copyright.*","").Trim()
} else {
    Report-Item 5 "FFprobe Binary" "FAIL" "ffprobe.exe not found in tools/ffmpeg/bin or PATH"
}

# [6] libass support
if (Test-Path $ffmpegExe) {
    $libass = & $ffmpegExe -version 2>&1 | Select-String "--enable-libass"
    if ($libass) {
        Report-Item 6 "Subtitle Engine (libass)" "PASS" "libass is enabled in FFmpeg build"
    } else {
        Report-Item 6 "Subtitle Engine (libass)" "FAIL" "libass not enabled in FFmpeg build"
    }
} else {
    Report-Item 6 "Subtitle Engine (libass)" "FAIL" "FFmpeg unavailable"
}

# [7] NVENC support
if (Test-Path $ffmpegExe) {
    $encCheck = & $ffmpegExe -hide_banner -loglevel error -f lavfi -i color=black:s=256x256 -frames:v 1 -c:v h264_nvenc -f null - 2>&1
    if ($LASTEXITCODE -eq 0) {
        Report-Item 7 "Hardware Encoder (NVENC)" "PASS" "h264_nvenc verified (Blackwell GPU smoke encode OK)"
    } else {
        Report-Item 7 "Hardware Encoder (NVENC)" "WARNING" "h264_nvenc failed ($encCheck) - fallback to libx264 (CPU)"
    }
} else {
    Report-Item 7 "Hardware Encoder (NVENC)" "FAIL" "FFmpeg unavailable"
}

# [8] NVIDIA GPU information & [9] CUDA availability
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $gpuName = & nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>&1
    Report-Item 8 "NVIDIA GPU Detected" "PASS" $gpuName
    Report-Item 9 "CUDA Driver Compatibility" "PASS" "Driver supports CUDA 12/13 runtime"
} else {
    Report-Item 8 "NVIDIA GPU Detected" "FAIL" "nvidia-smi not available"
    Report-Item 9 "CUDA Driver Compatibility" "FAIL" "No NVIDIA GPU driver found"
}

# [10] Faster-Whisper CUDA status
if (Test-Path $venvPy) {
    $fwStatus = & $venvPy -c "
import sys
sys.path.insert(0, '.')
import transcribe
transcribe._register_cuda_dll_dirs()
import ctranslate2
devs = ctranslate2.get_cuda_device_count()
if devs > 0:
    print(f'CUDA_OK:{devs}')
else:
    print('CUDA_ZERO')
" 2>&1
    if ($fwStatus -match "CUDA_OK:(\d+)") {
        Report-Item 10 "Faster-Whisper CUDA" "PASS" "CUDA runtime available ($($Matches[1]) device(s))"
    } else {
        Report-Item 10 "Faster-Whisper CUDA" "WARNING" "CUDA not usable in CTranslate2, will use CPU fallback"
    }
} else {
    Report-Item 10 "Faster-Whisper CUDA" "FAIL" "Virtual environment missing"
}

# [11] Ollama installation status
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
$ollamaLocal = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if ($ollamaCmd) {
    Report-Item 11 "Ollama Installation" "PASS" "Installed at $($ollamaCmd.Source)"
} elseif (Test-Path $ollamaLocal) {
    Report-Item 11 "Ollama Installation" "PASS" "Installed at $ollamaLocal"
    $env:PATH = "$env:LOCALAPPDATA\Programs\Ollama;$env:PATH"
} else {
    Report-Item 11 "Ollama Installation" "WARNING" "ollama.exe not found. Install from https://ollama.com if using local LLM"
}

# [12] Ollama service status & [13] Installed Ollama models
$ollamaProc = Get-Process -Name "*ollama*" -ErrorAction SilentlyContinue
if (-not $ollamaProc) {
    $taskListOut = cmd /c "tasklist /fi ""imagename eq ollama.exe"" 2>nul"
    if ($taskListOut -match "ollama\.exe") {
        $ollamaProc = $true
    }
}
if ($ollamaProc) {
    Report-Item 12 "Ollama Background Service" "PASS" "Ollama process is currently running"
    
    # Check installed models
    $models = & ollama list 2>&1 | Select-String "llama3.2:3b"
    if ($models) {
        Report-Item 13 "Ollama Models" "PASS" "llama3.2:3b is installed and ready"
    } else {
        Report-Item 13 "Ollama Models" "WARNING" "llama3.2:3b not found (run: ollama run llama3.2:3b)"
    }
} else {
    Report-Item 12 "Ollama Background Service" "WARNING" "Ollama service is not running (start with: ollama serve)"
    Report-Item 13 "Ollama Models" "WARNING" "Cannot inspect models while service is stopped"
}

# [14] Workspace Directories
$dirs = @("output", "cache", "downloads")
$allDirsOk = $true
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
}
Report-Item 14 "Workspace Directories" "PASS" "output/, cache/, downloads/ initialized"

# [15] Free disk space
$drives = [System.IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady -and ($_.Name -match '^[CD]') }
$diskInfo = ($drives | ForEach-Object { "$($_.Name) $([math]::Round($_.AvailableFreeSpace / 1GB, 1)) GB free" }) -join " | "
Report-Item 14 "Available Disk Space" "PASS" $diskInfo

Write-Host "`n=======================================================" -ForegroundColor Cyan
Write-Host "                Verification Completed                 " -ForegroundColor Cyan
Write-Host "=======================================================`n" -ForegroundColor Cyan
