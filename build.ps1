<#
  LoaAuctionCalc 빌드 스크립트

  사용:  powershell -ExecutionPolicy Bypass -File .\build.ps1
  옵션:  -NoSelfTest   빌드 후 --selftest 실행을 건너뜀

  1) .venv 가상환경 생성(없을 때만, Python 3.11 이상)
  2) requirements-dev.txt 설치
  3) PyInstaller --onefile --windowed → dist\LoaAuctionCalc.exe
     fixtures\sample_<이름>_<가격>.(webp|png|jpg) 이미지가 있으면 번들에 포함
     (이미지가 없어도 빌드는 성공하고, --selftest는 계산만 검사)
  4) dist\LoaAuctionCalc.exe --selftest 실행
#>
[CmdletBinding()]
param(
    [switch]$NoSelfTest
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Set-Location -LiteralPath $Root

function Invoke-Native {
    # 네이티브 프로그램이 stderr에 경고를 써도 스크립트가 멈추지 않게 하고, 종료 코드로만 판단한다.
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    $ErrorActionPreference = 'Continue'
    # stderr 줄이 PowerShell 오류 레코드로 표시되지 않도록 문자열로 바꿔 출력한다.
    & $Exe @Arguments 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ }
    } | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "명령 실패 (exit $LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

function Find-Python {
    # Python 3.11 이상인 인터프리터를 찾는다: py 런처(-3) → python 순서.
    $candidates = @(
        @{ Exe = 'py'; Pre = @('-3') },
        @{ Exe = 'python'; Pre = @() }
    )
    $ErrorActionPreference = 'Continue'
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $checkArgs = @($c.Pre) + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)')
        & $c.Exe @checkArgs 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $c }
    }
    return $null
}

# 1) 가상환경
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host '[1/4] 가상환경(.venv) 생성'
    $Python = Find-Python
    if (-not $Python) { throw 'Python 3.11 이상을 찾을 수 없습니다. https://www.python.org 에서 설치하세요.' }
    # 배열은 반드시 스플래팅(@변수)으로 넘겨야 인자가 하나로 합쳐지지 않는다.
    $VenvArgs = @($Python.Pre) + @('-m', 'venv', (Join-Path $Root '.venv'))
    Invoke-Native $Python.Exe @VenvArgs
} else {
    Write-Host '[1/4] 기존 가상환경(.venv) 사용'
}
Invoke-Native $VenvPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'

# 2) 의존성
Write-Host '[2/4] 의존성 설치'
Invoke-Native $VenvPython -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'requirements-dev.txt')

# 3) PyInstaller
Write-Host '[3/4] PyInstaller 빌드'
$AddData = @()
$FixtureDir = Join-Path $Root 'fixtures'
if (Test-Path -LiteralPath $FixtureDir) {
    Get-ChildItem -LiteralPath $FixtureDir -File |
        Where-Object { $_.Name -match '^sample_.+_\d+\.(webp|png|jpg)$' } |
        ForEach-Object {
            Write-Host "  fixture 포함: $($_.Name)"
            $AddData += @('--add-data', "$($_.FullName);fixtures")
        }
}
if ($AddData.Count -eq 0) {
    Write-Host '  fixtures 이미지 없음: OCR 샘플 없이 빌드합니다 (--selftest는 계산만 검사).'
}

# exe 아이콘: 저장소에 이미지 파일을 두지 않고 코드(loa_auction\icon.py)로 그려 만든다.
$IconPath = Join-Path $Root 'build\icon\LoaAuctionCalc.ico'
Invoke-Native $VenvPython -m loa_auction.icon $IconPath

$PyInstallerArgs = @(
    '-m', 'PyInstaller',
    '--noconfirm', '--clean',
    '--onefile', '--windowed',
    '--name', 'LoaAuctionCalc',
    '--icon', $IconPath,
    '--distpath', (Join-Path $Root 'dist'),
    '--workpath', (Join-Path $Root 'build'),
    '--specpath', (Join-Path $Root 'build'),
    '--paths', $Root,
    '--collect-submodules', 'winrt',
    '--log-level', 'WARN'
) + $AddData + @((Join-Path $Root 'loa_auction\__main__.py'))
Invoke-Native $VenvPython @PyInstallerArgs

$Exe = Join-Path $Root 'dist\LoaAuctionCalc.exe'
if (-not (Test-Path -LiteralPath $Exe)) { throw "빌드 결과물이 없습니다: $Exe" }
$SizeMb = [math]::Round((Get-Item -LiteralPath $Exe).Length / 1MB, 1)
Write-Host "  생성됨: $Exe ($SizeMb MB)"

# 4) 자체 검사
if ($NoSelfTest) {
    Write-Host '[4/4] 자체 검사 건너뜀'
    exit 0
}
Write-Host '[4/4] dist\LoaAuctionCalc.exe --selftest'
$LogPath = Join-Path $Root 'dist\selftest.log'
if (Test-Path -LiteralPath $LogPath) { Remove-Item -LiteralPath $LogPath -Force }  # 이전 결과를 보여 주지 않도록
# --windowed exe는 PowerShell이 기다리지 않으므로 Start-Process -Wait로 종료 코드를 받는다.
$proc = Start-Process -FilePath $Exe -ArgumentList '--selftest' -Wait -PassThru
if (Test-Path -LiteralPath $LogPath) { Get-Content -LiteralPath $LogPath -Encoding UTF8 | ForEach-Object { Write-Host "  $_" } }
if ($proc.ExitCode -ne 0) { throw "자체 검사 실패 (exit $($proc.ExitCode))" }
Write-Host '빌드 및 자체 검사 완료'
