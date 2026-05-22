param(
    [switch]$Init,
    [switch]$Once,
    [switch]$CheckOnly,
    [int]$IntervalSeconds = 30,
    [int]$RetrySeconds = 10,
    [int]$MaxLoginAttempts = 3,
    [string]$PortalBase = "http://10.200.84.3",
    [string]$ConfigPath = (Join-Path $PSScriptRoot "campus_login.config.json"),
    [string]$LogPath = (Join-Path $PSScriptRoot "campus_auto_login.log")
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Output $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function New-QueryString {
    param([System.Collections.IDictionary]$Params)
    $pairs = foreach ($key in $Params.Keys) {
        "{0}={1}" -f [Uri]::EscapeDataString([string]$key), [Uri]::EscapeDataString([string]$Params[$key])
    }
    return ($pairs -join "&")
}

function ConvertFrom-Jsonp {
    param([string]$Content)
    $text = $Content.Trim()
    $match = [regex]::Match($text, "^[A-Za-z_$][A-Za-z0-9_$]*\((.*)\)\s*;?\s*$", [Text.RegularExpressions.RegexOptions]::Singleline)
    if ($match.Success) {
        return ($match.Groups[1].Value | ConvertFrom-Json)
    }
    if ($text.StartsWith("{")) {
        return ($text | ConvertFrom-Json)
    }
    throw "Response is not JSONP/JSON:$($text.Substring(0, [Math]::Min(120, $text.Length)))"
}

function Invoke-PortalJsonp {
    param(
        [string]$Path,
        [hashtable]$Params,
        [int]$TimeoutSec = 10
    )
    $callback = "dr{0}" -f (Get-Random -Minimum 100000 -Maximum 999999)
    $allParams = [ordered]@{
        "callback" = $callback
    }
    foreach ($key in $Params.Keys) {
        $allParams[$key] = $Params[$key]
    }
    if (-not $allParams.Contains("jsVersion")) {
        $allParams["jsVersion"] = "4.X"
    }
    $allParams["v"] = Get-Random -Minimum 500 -Maximum 10500
    if (-not $allParams.Contains("lang")) {
        $allParams["lang"] = "zh"
    }
    $base = $PortalBase.TrimEnd("/")
    $uri = "{0}{1}?{2}" -f $base, $Path, (New-QueryString $allParams)
    $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec $TimeoutSec -Headers @{
        "User-Agent" = "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login"
        "Accept" = "*/*"
    }
    return ConvertFrom-Jsonp $response.Content
}

function Initialize-Config {
    $configDir = Split-Path -Parent $ConfigPath
    if ($configDir -and -not (Test-Path -LiteralPath $configDir)) {
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
    }
    $username = Read-Host "Campus username"
    $password = Read-Host "Campus password" -AsSecureString
    $suffix = Read-Host "Service suffix(empty for default,@dx for telecom,@lt for unicom)"
    $config = [ordered]@{
        PortalBase = $PortalBase.TrimEnd("/")
        Username = $username
        Password = ($password | ConvertFrom-SecureString)
        ServiceSuffix = $suffix
        TerminalType = 1
    }
    $config | ConvertTo-Json | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
    Write-Log "Config created:$ConfigPath"
}

function Get-PlainPassword {
    param([string]$EncryptedPassword)
    $secure = $EncryptedPassword | ConvertTo-SecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Read-Config {
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        throw "Config file not found.Run first:powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Init"
    }
    $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($config.PortalBase) {
        $script:PortalBase = [string]$config.PortalBase
    }
    if (-not $config.Username -or -not $config.Password) {
        throw "Config file misses Username or Password.Run -Init again."
    }
    return $config
}

function Get-CampusStatus {
    try {
        $status = Invoke-PortalJsonp -Path "/drcom/chkstatus" -Params @{} -TimeoutSec 8
        return [pscustomobject]@{
            Reachable = $true
            Online = ([int]$status.result -eq 1)
            Raw = $status
            Error = $null
        }
    }
    catch {
        return [pscustomobject]@{
            Reachable = $false
            Online = $false
            Raw = $null
            Error = $_.Exception.Message
        }
    }
}

function Invoke-CampusLogin {
    param(
        [string]$Username,
        [string]$Password,
        [string]$ServiceSuffix,
        [int]$TerminalType = 1
    )
    $account = "{0}{1}" -f $Username, $ServiceSuffix
    $params = @{
        "DDDDD" = $account
        "upass" = $Password
        "0MKKey" = "123456"
        "R1" = "0"
        "R2" = ""
        "R3" = "0"
        "R6" = "0"
        "para" = "00"
        "v6ip" = ""
        "terminal_type" = $TerminalType
        "lang" = "zh"
    }
    return Invoke-PortalJsonp -Path "/drcom/login" -Params $params -TimeoutSec 12
}

function Invoke-LoginCycle {
    param([object]$Config)
    $status = Get-CampusStatus
    if (-not $status.Reachable) {
        Write-Log "Portal unreachable:$($status.Error)"
        return $false
    }
    if ($status.Online) {
        Write-Log "Already online.No login needed."
        return $true
    }
    Write-Log "Offline from portal status.Login will be attempted."
    if ($CheckOnly) {
        return $false
    }
    $plainPassword = Get-PlainPassword $Config.Password
    try {
        for ($i = 1; $i -le $MaxLoginAttempts; $i++) {
            $result = Invoke-CampusLogin -Username $Config.Username -Password $plainPassword -ServiceSuffix $Config.ServiceSuffix -TerminalType ([int]$Config.TerminalType)
            if ($result.result -eq 1 -or $result.result -eq "ok") {
                Write-Log "Login API returned success."
                Start-Sleep -Seconds 2
                $after = Get-CampusStatus
                if ($after.Online) {
                    Write-Log "Status recheck confirmed online."
                    return $true
                }
                Write-Log "Status recheck still offline after login."
            }
            else {
                $message = @($result.msg, $result.error_msg, $result.ErrorMsg, $result.ret_code, $result.result) | Where-Object { $_ } | Select-Object -First 1
                Write-Log "Login failed:$(if($message){$message}else{"unknown error"})"
            }
            if ($i -lt $MaxLoginAttempts) {
                Start-Sleep -Seconds $RetrySeconds
            }
        }
        return $false
    }
    finally {
        $plainPassword = $null
    }
}

if ($Init) {
    Initialize-Config
    if (-not $Once -and -not $CheckOnly) {
        return
    }
}

if ($CheckOnly) {
    $status = Get-CampusStatus
    if (-not $status.Reachable) {
        Write-Log "Portal unreachable:$($status.Error)"
        exit 2
    }
    if ($status.Online) {
        Write-Log "Already online."
        exit 0
    }
    Write-Log "Offline from portal status."
    exit 1
}

$config = Read-Config

if ($Once) {
    $ok = Invoke-LoginCycle -Config $config
    if ($ok) { exit 0 } else { exit 1 }
}

Write-Log "Campus portal monitor started.Interval=${IntervalSeconds}s."
while ($true) {
    Invoke-LoginCycle -Config $config | Out-Null
    Start-Sleep -Seconds $IntervalSeconds
}
