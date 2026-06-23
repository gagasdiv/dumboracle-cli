# Create a virtual environment and install dependencies (Windows / PowerShell).
#   PS> .\setup.ps1
$ErrorActionPreference = "Stop"

$python = "python"
Write-Host "Creating virtual environment in .venv ..."
& $python -m venv .venv

$venvPython = Join-Path ".venv" "Scripts\python.exe"
Write-Host "Upgrading pip ..."
& $venvPython -m pip install --upgrade pip --quiet

Write-Host "Installing requirements ..."
& $venvPython -m pip install -r requirements.txt

if (-not (Test-Path "connections.yaml")) {
    Copy-Item "connections.example.yaml" "connections.yaml"
    Write-Host "Created connections.yaml from the example - edit it with your databases."
}

Write-Host ""
Write-Host "Done. Activate with:   .\.venv\Scripts\Activate.ps1"
Write-Host "Then run:              python -m dumboracle"
