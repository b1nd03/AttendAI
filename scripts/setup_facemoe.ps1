$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$revision = 'f0972d487ed4fe20bce701840e4a3ee2da2f4c4e'
$expectedHash = '57087ca1e108ff5e6f59e13295e76e65f6d96e35111690d2c88fc9a474d6363c'
$checkpoint = 'models/facemoe/briar.pt'
$partial = "$checkpoint.partial"
$url = 'https://huggingface.co/kartiknarayan/FaceMoE/resolve/17296be68de1cba76da667cb3a158494c780edb8/swin4m_exp_3_k_2_briar_full/model.pt?download=true'

if (!(Test-Path .venv/Scripts/python.exe)) { throw 'Run setup.ps1 for AttendAI first.' }
if (!(Test-Path .venv-facemoe/Scripts/python.exe)) {
    & .venv/Scripts/python.exe -m venv .venv-facemoe
    if ($LASTEXITCODE) { throw 'Could not create the isolated pilot environment.' }
}
& .venv-facemoe/Scripts/python.exe -m pip install --no-deps 'torch==2.8.0' 'torchvision==0.23.0' --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE) { throw 'Could not install the CUDA pilot runtime.' }
& .venv-facemoe/Scripts/python.exe -m pip install 'timm==1.0.22' 'numpy==2.2.6' 'opencv-python-headless==4.12.0.88' 'cryptography==46.0.3' 'pillow==12.0.0'
if ($LASTEXITCODE) { throw 'Could not install pilot dependencies.' }
& .venv-facemoe/Scripts/python.exe -m pip check
if ($LASTEXITCODE) { throw 'Pilot dependencies did not resolve cleanly.' }

if (!(Test-Path models/facemoe-upstream)) {
    git clone https://github.com/Kartik-3004/FaceMoE models/facemoe-upstream
    if ($LASTEXITCODE) { throw 'Could not download the FaceMoE source.' }
}
git -C models/facemoe-upstream checkout --detach $revision
if ($LASTEXITCODE) { throw 'Could not select the pinned FaceMoE revision.' }
if (git -C models/facemoe-upstream status --porcelain --untracked-files=no) { throw 'The pinned FaceMoE source has local changes.' }

if (!(Test-Path $checkpoint)) {
    New-Item -ItemType Directory -Force models/facemoe | Out-Null
    if (!(Test-Path $partial) -or (Get-Item $partial).Length -lt 1856658185) {
        curl.exe --fail --location --retry 3 --continue-at - --output $partial $url
        if ($LASTEXITCODE) { throw 'FaceMoE download interrupted. Rerun this script to resume.' }
    }
    if ((Get-FileHash -LiteralPath $partial).Hash -ne $expectedHash) { throw 'The FaceMoE download did not match the published SHA-256.' }
    Move-Item -LiteralPath $partial -Destination $checkpoint
}
if ((Get-FileHash -LiteralPath $checkpoint).Hash -ne $expectedHash) { throw 'The FaceMoE checkpoint did not match the published SHA-256.' }
& .venv-facemoe/Scripts/python.exe -c "import torch; assert torch.cuda.is_available(), 'CUDA is not available'; print(torch.cuda.get_device_name(0))"
if ($LASTEXITCODE) { throw 'PyTorch could not initialize CUDA for the pilot.' }
