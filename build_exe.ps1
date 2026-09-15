$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$venvRoot = Join-Path $projectRoot ".venv-build"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$oldBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        python -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw "Paketleme ortamı oluşturulamadı." }
    }

    & $venvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip güncellenemedi." }

    & $venvPython -m pip install --disable-pip-version-check -r requirements.txt -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw "Paketleme bağımlılıkları kurulamadı." }

    # Playwright tarayıcısını site-packages altına koyar; PyInstaller bu sayede
    # Chromium'u tek dosyalık EXE'nin içine dahil eder.
    $env:PLAYWRIGHT_BROWSERS_PATH = "0"
    & $venvPython -m playwright install chromium --no-shell
    if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium indirilemedi." }

    & $venvPython -m PyInstaller `
        --noconfirm `
        --onefile `
        --windowed `
        --name "OBS-Ders-Kayit" `
        --distpath (Join-Path $projectRoot "dist") `
        --workpath (Join-Path $projectRoot "build\pyinstaller") `
        --specpath (Join-Path $projectRoot "build") `
        bot2.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller paketi oluşturamadı." }

    Write-Host "EXE hazır: $(Join-Path $projectRoot 'dist\OBS-Ders-Kayit.exe')"
}
finally {
    if ($null -eq $oldBrowserPath) {
        Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PLAYWRIGHT_BROWSERS_PATH = $oldBrowserPath
    }
    Pop-Location
}
