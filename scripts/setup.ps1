# Cài backend trên Windows (PowerShell).
#   .\scripts\setup.ps1                 # chỉ phần chung (đủ để chạy mock)
#   .\scripts\setup.ps1 sam3            # + PyTorch + gói sam3 chính thức của Meta
#   .\scripts\setup.ps1 transformers    # + PyTorch + SAM3 qua Hugging Face transformers
# Nếu PowerShell chặn script: Set-ExecutionPolicy -Scope Process Bypass
param([string]$Mode = "base")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$TorchIndex = if ($env:TORCH_INDEX) { $env:TORCH_INDEX } else { "https://download.pytorch.org/whl/cu126" }

if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

if ($Mode -eq "sam3" -or $Mode -eq "transformers") {
    pip install torch torchvision --index-url $TorchIndex
}
if ($Mode -eq "sam3") {
    New-Item -ItemType Directory -Force third_party | Out-Null
    if (-not (Test-Path "third_party\sam3")) { git clone https://github.com/facebookresearch/sam3.git third_party\sam3 }
    pip install -e third_party\sam3
    pip install "huggingface_hub[cli]"
} elseif ($Mode -eq "transformers") {
    pip install --upgrade transformers "huggingface_hub[cli]"
}

if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
Write-Host ""
Write-Host "Xong. Buoc tiep theo:"
if ($Mode -ne "base") {
    Write-Host "  1. Xin quyen tai model tai https://huggingface.co/facebook/sam3 roi chay:  hf auth login"
    Write-Host "  2. .\scripts\run.ps1"
} else {
    Write-Host '  Chay thu khong can model:  $env:SAM3_ENGINE="mock"; .\scripts\run.ps1'
}
