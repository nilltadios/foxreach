# Installs the foxreach program for this user and lets Firefox-based browsers start it. Windows.
#
# Run:    powershell -ExecutionPolicy Bypass -File install.ps1   (any arguments go to `foxreach install`)
# Does:   downloads foxreach for this machine from the latest GitHub release, checks it against
#         the release's SHA256SUMS, puts it in %LOCALAPPDATA%\Programs\foxreach, adds that folder to
#         this user's PATH, and runs `foxreach install`.
# Undo:   foxreach uninstall, then delete %LOCALAPPDATA%\Programs\foxreach
# The checksum shows the download is complete, not who built it. To check that, see
# "Checking a release" in README.md.
$ErrorActionPreference = "Stop"

$Repo = "nilltadios/foxreach"
$Base = if ($env:FOXREACH_RELEASE_URL) { $env:FOXREACH_RELEASE_URL } else { "https://github.com/$Repo/releases/latest/download" }
$Dest = if ($env:FOXREACH_BIN_DIR) { $env:FOXREACH_BIN_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\foxreach" }

# A 32-bit PowerShell on a 64-bit Windows says x86; the machine itself is in PROCESSOR_ARCHITEW6432.
$machine = if ($env:FOXREACH_ARCH) { $env:FOXREACH_ARCH }
  elseif ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 }
  else { $env:PROCESSOR_ARCHITECTURE }
switch -Regex ($machine) {
  "^(AMD64|x64)$" { $file = "foxreach-windows-x64.exe" }
  "^(ARM64|arm64)$" { $file = "foxreach-windows-arm64.exe" }
  default { Write-Error "No foxreach build for $machine."; exit 1 }
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("foxreach-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  Write-Host "Downloading $file from $Base"
  $ProgressPreference = "SilentlyContinue"  # the progress bar makes Invoke-WebRequest many times slower
  Invoke-WebRequest -UseBasicParsing -Uri "$Base/$file" -OutFile (Join-Path $tmp $file)
  Invoke-WebRequest -UseBasicParsing -Uri "$Base/SHA256SUMS" -OutFile (Join-Path $tmp "SHA256SUMS")
  $want = $null
  foreach ($line in Get-Content (Join-Path $tmp "SHA256SUMS")) {
    $parts = $line.Trim() -split "\s+"
    if ($parts.Count -ge 2 -and $parts[1] -eq $file) { $want = $parts[0].ToLower() }
  }
  $got = (Get-FileHash -Algorithm SHA256 (Join-Path $tmp $file)).Hash.ToLower()
  if (-not $want -or $want -ne $got) {
    Write-Error "Checksum mismatch for ${file}: expected $(if ($want) { $want } else { 'nothing listed' }), got $got. Nothing was installed."
    exit 1
  }

  # Before anything is replaced: a program Windows will not start (Smart App Control, another
  # processor) must not take the place of one that works.
  $new = Join-Path $tmp $file
  $version = $null
  $why = $null
  try {
    $version = & $new version
    if ($LASTEXITCODE -ne 0) { $version = $null; $why = "exit code $LASTEXITCODE" }
  } catch {
    # The inner exception holds Windows' own words; the outer one adds PowerShell's position text.
    $why = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
  }
  if (-not $version) {
    Write-Error "$file does not start on this machine ($why). Smart App Control may block programs that are not signed. Nothing was installed."
    exit 1
  }

  New-Item -ItemType Directory -Force -Path $Dest | Out-Null
  $target = Join-Path $Dest "foxreach.exe"
  # A running foxreach (an agent, a browser's host) cannot be replaced or deleted, but it can be
  # moved aside. Such leftovers from earlier updates are deleted once they have closed.
  if (Test-Path $target) {
    Get-ChildItem -Path $Dest -Filter "foxreach.exe.previous-*" | Remove-Item -Force -ErrorAction SilentlyContinue
    if (Test-Path "$target.previous") {
      try { Remove-Item -Force "$target.previous" }
      catch {
        try { Move-Item -Force "$target.previous" "$target.previous-$([DateTimeOffset]::Now.ToUnixTimeMilliseconds())" }
        catch {
          Write-Error "$target.previous is in use and cannot be moved aside: close the programs using it (agents, browsers), then run this again. Nothing was installed."
          exit 1
        }
      }
    }
    Move-Item -Force $target "$target.previous"
  }
  Move-Item $new $target
  Write-Host "Installed $target ($version)"

  # The user's Path as stored, %USERPROFILE% and all, and of the same type (usually REG_EXPAND_SZ):
  # [Environment]::GetEnvironmentVariable would expand it and SetEnvironmentVariable store REG_SZ.
  $envKey = Get-Item -Path "HKCU:\Environment"
  $userPath = $envKey.GetValue("Path", "", "DoNotExpandEnvironmentNames")
  $kind = if ($envKey.GetValueNames() -contains "Path") { $envKey.GetValueKind("Path") } else { "ExpandString" }
  $entries = $userPath -split ";" | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd("\") }
  if (-not ($entries -contains $Dest.TrimEnd("\"))) {
    Set-ItemProperty -Path "HKCU:\Environment" -Name "Path" -Type $kind -Value ((($userPath.TrimEnd(";")), $Dest | Where-Object { $_ }) -join ";")
    # Tell running programs (Explorer, so new terminals) the environment changed: setting a user
    # variable this way broadcasts it.
    [Environment]::SetEnvironmentVariable("FOXREACH_INSTALLING", "1", "User")
    [Environment]::SetEnvironmentVariable("FOXREACH_INSTALLING", $null, "User")
    Write-Host "Added $Dest to your PATH (new terminals see it)."
  }
  & $target install @args
  exit $LASTEXITCODE
} finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
