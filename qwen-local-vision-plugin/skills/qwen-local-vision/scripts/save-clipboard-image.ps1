<#
.SYNOPSIS
  Save the image currently on the Windows clipboard to a PNG file.
.DESCRIPTION
  Lets Codex read a screenshot or a copied picture without the chat supporting
  image attachments: copy the image, run this, then feed the printed path to
  vision_describe.py.
.EXAMPLE
  powershell -NoProfile -STA -File save-clipboard-image.ps1
.EXAMPLE
  powershell -NoProfile -STA -File save-clipboard-image.ps1 -OutPath "C:\tmp\shot.png"
#>
param(
    [string]$OutPath
)

$ErrorActionPreference = 'Stop'

# System.Windows.Forms.Clipboard requires a single-threaded apartment.
if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') {
    $psExe = Join-Path $PSHOME 'powershell.exe'
    if (Test-Path -LiteralPath $psExe) {
        $forward = @('-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
        if ($OutPath) { $forward += @('-OutPath', $OutPath) }
        & $psExe @forward
        exit $LASTEXITCODE
    }
    Write-Error 'Clipboard access needs an STA thread; re-run with: powershell -STA -File save-clipboard-image.ps1'
    exit 4
}

if (-not $OutPath) {
    $OutPath = Join-Path $env:TEMP ('clip-{0:yyyyMMdd-HHmmss}.png' -f (Get-Date))
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if (-not [System.Windows.Forms.Clipboard]::ContainsImage()) {
    # Explorer "copy file" puts a file list on the clipboard instead of a bitmap.
    $imageExt = @('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff')
    $files = @()
    if ([System.Windows.Forms.Clipboard]::ContainsFileDropList()) {
        $files = @([System.Windows.Forms.Clipboard]::GetFileDropList() |
                   Where-Object { $imageExt -contains ([System.IO.Path]::GetExtension($_).ToLower()) })
    }
    if ($files.Count -gt 0) {
        $f = Get-Item -LiteralPath $files[0]
        [pscustomobject]@{ ok = $true; path = $f.FullName; bytes = $f.Length; source = 'file-drop' } |
            ConvertTo-Json -Compress
        exit 0
    }
    $hasText = [System.Windows.Forms.Clipboard]::ContainsText()
    [pscustomobject]@{
        ok    = $false
        error = if ($hasText) { 'clipboard holds text, not an image' } else { 'clipboard does not contain an image' }
    } | ConvertTo-Json -Compress
    exit 2
}

$image = [System.Windows.Forms.Clipboard]::GetImage()
if ($null -eq $image) {
    [pscustomobject]@{ ok = $false; error = 'GetImage() returned null' } | ConvertTo-Json -Compress
    exit 3
}

try {
    $width = $image.Width
    $height = $image.Height
    $parent = Split-Path -Parent $OutPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $image.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
}
finally {
    $image.Dispose()
}

$file = Get-Item -LiteralPath $OutPath
[pscustomobject]@{
    ok     = $true
    path   = $file.FullName
    bytes  = $file.Length
    width  = $width
    height = $height
} | ConvertTo-Json -Compress
