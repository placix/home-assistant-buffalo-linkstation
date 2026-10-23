[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = $PSScriptRoot
$projectFile = Join-Path $repositoryRoot "pyproject.toml"
$requirementsFile = Join-Path $repositoryRoot "requirements_test.txt"
$venvPath = Join-Path $repositoryRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$dependencyStamp = Join-Path $venvPath ".buffalo-dependencies.sha256"

function Write-Step {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[Buffalo LinkStation] $Message"
}

function Get-PythonVersion {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    $arguments = @($PrefixArguments) + @(
        "-c",
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    )
    $output = & $Executable @arguments 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $output) {
        return $null
    }
    try {
        return [version]($output | Select-Object -Last 1)
    }
    catch {
        return $null
    }
}

function Test-CompatibleVersion {
    param(
        [Parameter(Mandatory)][version]$Version,
        [Parameter(Mandatory)][version]$MinimumVersion
    )
    return $Version -ge $MinimumVersion
}

try {
    if (-not (Test-Path -LiteralPath $projectFile -PathType Leaf)) {
        throw "pyproject.toml was not found at $projectFile."
    }
    if (-not (Test-Path -LiteralPath $requirementsFile -PathType Leaf)) {
        throw "requirements_test.txt was not found at $requirementsFile."
    }

    $projectText = Get-Content -LiteralPath $projectFile -Raw
    $versionMatch = [regex]::Match(
        $projectText,
        'requires-python\s*=\s*">=\s*(\d+)\.(\d+)(?:\.\d+)?"'
    )
    if (-not $versionMatch.Success) {
        throw "setup.ps1 supports a simple >=X.Y requires-python value in pyproject.toml."
    }
    $minimumVersion = [version](
        "$($versionMatch.Groups[1].Value).$($versionMatch.Groups[2].Value)"
    )

    $createdVenv = $false
    if (Test-Path -LiteralPath $venvPath) {
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "The existing .venv is broken: Scripts\python.exe is missing. Remove .venv and run setup.ps1 again."
        }
        $venvVersion = Get-PythonVersion -Executable $venvPython
        if ($null -eq $venvVersion) {
            throw "The existing .venv is broken: its Python executable could not be started. Remove .venv and run setup.ps1 again."
        }
        if (-not (Test-CompatibleVersion $venvVersion $minimumVersion)) {
            throw "The existing .venv uses Python $venvVersion, but this project requires Python $minimumVersion or newer. Remove .venv and run setup.ps1 with a compatible Python installed."
        }
        Write-Step "Reusing .venv with Python $venvVersion."
    }
    else {
        $pythonCommand = $null
        $pythonArguments = @()

        if (Get-Command py -ErrorAction SilentlyContinue) {
            $candidateVersion = Get-PythonVersion -Executable "py" -PrefixArguments @("-3")
            if ($null -ne $candidateVersion -and (Test-CompatibleVersion $candidateVersion $minimumVersion)) {
                $pythonCommand = "py"
                $pythonArguments = @("-3")
            }
        }

        if ($null -eq $pythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
            $candidateVersion = Get-PythonVersion -Executable "python"
            if ($null -ne $candidateVersion -and (Test-CompatibleVersion $candidateVersion $minimumVersion)) {
                $pythonCommand = "python"
            }
        }

        if ($null -eq $pythonCommand) {
            throw "No compatible Python was found. Install Python $minimumVersion or newer, including the Windows Python launcher, and run setup.ps1 again."
        }

        Write-Step "Creating .venv with $pythonCommand (Python $candidateVersion)."
        $createArguments = @($pythonArguments) + @("-m", "venv", $venvPath)
        & $pythonCommand @createArguments
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            throw "Python failed to create .venv."
        }
        $createdVenv = $true
    }

    $dependencyFiles = @($projectFile, $requirementsFile)
    $dependencyFingerprint = ($dependencyFiles | ForEach-Object {
        (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash
    }) -join ""
    $installedFingerprint = if (Test-Path -LiteralPath $dependencyStamp) {
        (Get-Content -LiteralPath $dependencyStamp -Raw).Trim()
    }
    else {
        ""
    }

    if ($createdVenv -or $installedFingerprint -ne $dependencyFingerprint) {
        Write-Step "Upgrading pip in .venv."
        & $venvPython -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) {
            throw "pip could not be upgraded."
        }

        Write-Step "Installing capture and Windows-compatible development dependencies."
        & $venvPython -m pip install -r $requirementsFile
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency installation failed."
        }
        Set-Content -LiteralPath $dependencyStamp -Value $dependencyFingerprint -Encoding ascii
    }
    else {
        Write-Step "Dependency declarations are unchanged; installation is already current."
    }

    Write-Step "Validating the API client and capture dependencies."
    Push-Location $repositoryRoot
    try {
        & $venvPython -c "import aiohttp; from custom_components.buffalo_linkstation.api import BuffaloLinkStationApi; print('API client import passed.')"
        if ($LASTEXITCODE -ne 0) {
            throw "The Buffalo API client or aiohttp could not be imported."
        }
        & $venvPython tools\capture_readonly.py --help
        if ($LASTEXITCODE -ne 0) {
            throw "The capture tool help check failed."
        }
    }
    finally {
        Pop-Location
    }

    Write-Step "Setup completed successfully. No NAS requests were sent."
}
catch {
    Write-Error "Setup failed: $($_.Exception.Message)"
    exit 1
}
