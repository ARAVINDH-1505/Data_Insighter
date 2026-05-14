param(
    [string]$BaseUrl = 'http://127.0.0.1:5000',
    [string[]]$Routes = @('/','/login','/register'),
    [string[]]$AuthenticatedRoutes = @('/analysis','/dashboard','/upload'),
    [string]$OutputDir,
    [switch]$SkipServerStart,
    [string]$BrowserPath,
    [string]$Username,
    [string]$Password
)

$ErrorActionPreference = 'Stop'

function Get-PreferredBrowserPath {
    $candidates = @(
        'C:\Program Files\Google\Chrome\Application\chrome.exe',
        'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw 'No supported local browser was found. Install Chrome or Edge, or pass -BrowserPath explicitly.'
}

function Wait-ForLocalUrl {
    param(
        [string]$Url,
        [int]$Attempts = 30
    )

    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            return $response.StatusCode
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    throw "Local app did not respond at $Url"
}

function Invoke-BrowserCommand {
    param(
        [string]$Executable,
        [string[]]$Arguments
    )

    $tempKey = [Guid]::NewGuid().ToString('N')
    $stdoutPath = Join-Path ([System.IO.Path]::GetTempPath()) ("di_browser_stdout_{0}.log" -f $tempKey)
    $stderrPath = Join-Path ([System.IO.Path]::GetTempPath()) ("di_browser_stderr_{0}.log" -f $tempKey)

    try {
        $quotedArguments = $Arguments | ForEach-Object {
            if ($_ -match '\s') {
                '"' + ($_ -replace '"', '\"') + '"'
            } else {
                $_
            }
        }

        $process = Start-Process `
            -FilePath $Executable `
            -ArgumentList $quotedArguments `
            -Wait `
            -PassThru `
            -NoNewWindow `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath

        $stdout = if (Test-Path $stdoutPath) { Get-Content -Path $stdoutPath -Raw } else { '' }
        $stderr = if (Test-Path $stderrPath) { Get-Content -Path $stderrPath -Raw } else { '' }

        if ($process.ExitCode -ne 0) {
            throw "Browser command failed with exit code $($process.ExitCode). $stderr"
        }

        return [pscustomobject]@{
            StdOut = $stdout
            StdErr = $stderr
        }
    } finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Get-RouteName {
    param([string]$Route)

    if ([string]::IsNullOrWhiteSpace($Route) -or $Route -eq '/') {
        return 'home'
    }

    return (($Route -replace '^[\\/]+', '') -replace '[^a-zA-Z0-9_-]+', '_')
}

function Get-CsrfTokenFromHtml {
    param([string]$Html)

    $match = [regex]::Match($Html, 'name="_csrf_token"\s+value="([^"]+)"')
    if ($match.Success) {
        return $match.Groups[1].Value
    }

    throw 'Could not locate CSRF token in the login form.'
}

$repo = Split-Path -Parent $PSScriptRoot
$outputRoot = Join-Path $repo 'output\ui_verification'
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'

if (-not $OutputDir) {
    $OutputDir = Join-Path $outputRoot $timestamp
}

if (-not $BrowserPath) {
    $BrowserPath = Get-PreferredBrowserPath
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

if (-not $SkipServerStart) {
    try {
        Invoke-WebRequest -Uri $BaseUrl -UseBasicParsing -TimeoutSec 3 | Out-Null
    } catch {
        & (Join-Path $PSScriptRoot 'start_local_server.ps1')
    }
}

$statusCode = Wait-ForLocalUrl -Url $BaseUrl

$profileRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("data_insighter_ui_verify_{0}" -f $timestamp)
New-Item -ItemType Directory -Force -Path $profileRoot | Out-Null

$results = @()
$authenticatedResults = @()

foreach ($route in $Routes) {
    $routeName = Get-RouteName -Route $route
    $fullUrl = '{0}{1}' -f $BaseUrl.TrimEnd('/'), ($(if ($route.StartsWith('/')) { $route } else { "/$route" }))
    if ($route -eq '/') {
        $fullUrl = $BaseUrl
    }

    $screenshotPath = Join-Path $OutputDir ("{0}.png" -f $routeName)
    $htmlPath = Join-Path $OutputDir ("{0}.html" -f $routeName)

    $shotArgs = @(
        '--headless=new',
        '--disable-gpu',
        '--hide-scrollbars',
        '--allow-insecure-localhost',
        '--disable-extensions',
        '--disable-component-extensions-with-background-pages',
        '--disable-background-networking',
        '--disable-default-apps',
        '--disable-sync',
        '--window-size=1440,2200',
        '--virtual-time-budget=5000',
        "--user-data-dir=$profileRoot",
        "--screenshot=$screenshotPath",
        $fullUrl
    )
    Invoke-BrowserCommand -Executable $BrowserPath -Arguments $shotArgs | Out-Null

    $domArgs = @(
        '--headless=new',
        '--disable-gpu',
        '--allow-insecure-localhost',
        '--disable-extensions',
        '--disable-component-extensions-with-background-pages',
        '--disable-background-networking',
        '--disable-default-apps',
        '--disable-sync',
        '--virtual-time-budget=5000',
        "--user-data-dir=$profileRoot",
        '--dump-dom',
        $fullUrl
    )
    $domResult = Invoke-BrowserCommand -Executable $BrowserPath -Arguments $domArgs
    Set-Content -Path $htmlPath -Value $domResult.StdOut -Encoding UTF8

    if (-not (Test-Path $screenshotPath)) {
        throw "Screenshot was not created for $fullUrl"
    }

    $results += [pscustomobject]@{
        route = $route
        url = $fullUrl
        screenshot = $screenshotPath
        dom_dump = $htmlPath
    }
}

if ($Username -and $Password) {
    $webSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $loginPage = Invoke-WebRequest -Uri "$BaseUrl/login" -WebSession $webSession -UseBasicParsing
    $csrfToken = Get-CsrfTokenFromHtml -Html $loginPage.Content
    $loginResponse = Invoke-WebRequest -Uri "$BaseUrl/login" -Method Post -WebSession $webSession -UseBasicParsing -Body @{
        username = $Username
        password = $Password
        _csrf_token = $csrfToken
    }

    foreach ($route in $AuthenticatedRoutes) {
        $routeName = "auth_" + (Get-RouteName -Route $route)
        $fullUrl = '{0}{1}' -f $BaseUrl.TrimEnd('/'), ($(if ($route.StartsWith('/')) { $route } else { "/$route" }))
        $htmlPath = Join-Path $OutputDir ("{0}.html" -f $routeName)

        try {
            $response = Invoke-WebRequest -Uri $fullUrl -WebSession $webSession -UseBasicParsing
            Set-Content -Path $htmlPath -Value $response.Content -Encoding UTF8
            $authenticatedResults += [pscustomobject]@{
                route = $route
                url = $fullUrl
                status_code = $response.StatusCode
                dom_dump = $htmlPath
                authenticated = $true
            }
        } catch {
            $authenticatedResults += [pscustomobject]@{
                route = $route
                url = $fullUrl
                status_code = $_.Exception.Response.StatusCode.value__
                dom_dump = $htmlPath
                authenticated = $true
                error = $_.Exception.Message
            }
        }
    }
}

$summaryPath = Join-Path $OutputDir 'verification_summary.json'
$summaryPayload = [pscustomobject]@{
    public_routes = $results
    authenticated_routes = $authenticatedResults
}
$summaryPayload | ConvertTo-Json -Depth 5 | Set-Content -Path $summaryPath -Encoding UTF8

$reportPath = Join-Path $OutputDir 'verification_report.md'
$reportLines = @(
    '# Local UI Verification',
    '',
    "Base URL: $BaseUrl",
    '',
    '## Public routes'
)

foreach ($result in $results) {
    $reportLines += "- $($result.route) -> screenshot: $($result.screenshot) / DOM: $($result.dom_dump)"
}

if ($authenticatedResults.Count) {
    $reportLines += ''
    $reportLines += '## Authenticated routes'
    foreach ($result in $authenticatedResults) {
        $status = if ($result.status_code) { $result.status_code } else { 'error' }
        $detail = if ($result.error) { " / error: $($result.error)" } else { '' }
        $reportLines += "- $($result.route) -> status: $status / DOM: $($result.dom_dump)$detail"
    }
}

Set-Content -Path $reportPath -Value ($reportLines -join [Environment]::NewLine) -Encoding UTF8

Write-Host "Local UI verification completed."
Write-Host "HTTP status:"
Write-Host "  $statusCode"
Write-Host "Browser:"
Write-Host "  $BrowserPath"
Write-Host "Artifacts:"
Write-Host "  $OutputDir"
Write-Host "Summary:"
Write-Host "  $summaryPath"
Write-Host "Report:"
Write-Host "  $reportPath"
