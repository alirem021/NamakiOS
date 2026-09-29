param([Parameter(ValueFromRemainingArguments=$true)][string[]]$LauncherArgs)
$ErrorActionPreference = 'Stop'
try {
    if ($env:NAMAKI_RUNTIME_CACHE) { $runtimeRoot = $env:NAMAKI_RUNTIME_CACHE }
    else { $runtimeRoot = Join-Path $env:LOCALAPPDATA 'NamakiOS/runtime' }
    $pythonPath = $null
    $cachedPython = Join-Path $runtimeRoot 'windows-x64/python/python.exe'
    if (Test-Path -LiteralPath $cachedPython) { $pythonPath = $cachedPython }
    foreach ($name in @('python', 'py')) {
        if ($pythonPath) { break }
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $candidateArgs = if ($name -eq 'py') { @('-3') } else { @() }
        $candidateArgs += @('-c', 'import sys; print(sys.executable) if sys.version_info >= (3,10) else sys.exit(1)')
        try { $candidate = & $command.Source @candidateArgs 2>$null }
        catch { continue }
        if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-Path -LiteralPath ([string]$candidate))) { $pythonPath = [string]$candidate }
    }
    if (-not $pythonPath) {
        if ($env:PROCESSOR_ARCHITECTURE -notin @('AMD64', 'ARM64') -and $env:PROCESSOR_ARCHITEW6432 -ne 'AMD64') { throw 'Automatic setup supports 64-bit Windows.' }
        $source = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-sources.json') -Raw | ConvertFrom-Json).'windows-x64'.python
        $runtimeFolder = Join-Path $runtimeRoot 'windows-x64'
        New-Item -ItemType Directory -Path $runtimeFolder -Force | Out-Null
        $archive = Join-Path $runtimeFolder 'python.tar.gz'
        $partial = "$archive.partial"
        Write-Host 'Python was not found. Downloading a private Python runtime...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        if (-not (Test-Path -LiteralPath $archive)) {
            Invoke-WebRequest -Uri $source.url -OutFile $partial -UseBasicParsing
            if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $source.sha256) { throw 'Python download checksum mismatch. Please run again.' }
            Move-Item -LiteralPath $partial -Destination $archive -Force
        }
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $source.sha256) { throw 'Cached Python checksum mismatch.' }
        $destination = Join-Path $runtimeFolder 'python'
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        & tar.exe -xzf $archive -C $destination --strip-components 1
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract Python.' }
        $pythonPath = Join-Path $destination 'python.exe'
    }
    & $pythonPath (Join-Path $PSScriptRoot 'bootstrap.py') @LauncherArgs
    exit $LASTEXITCODE
} catch {
    Write-Host "NamakiOS setup: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Check your Internet connection and run again. No manual dependency installation is needed.'
    exit 1
}
