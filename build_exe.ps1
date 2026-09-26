$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "Quant Learning Journal" `
    --distpath $ProjectRoot `
    --workpath (Join-Path $ProjectRoot "build\pyinstaller") `
    --specpath (Join-Path $ProjectRoot "build") `
    --add-data "$(Join-Path $ProjectRoot 'app\templates');app\templates" `
    --add-data "$(Join-Path $ProjectRoot 'app\static');app\static" `
    (Join-Path $ProjectRoot "launcher.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller không thể tạo file EXE."
}
Write-Host "Đã tạo: $(Join-Path $ProjectRoot 'Quant Learning Journal.exe')"
