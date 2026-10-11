<#
Pixel Office - Claude Code hook client, PowerShell twin of pixel_hook.py (CLOUD.md 7.5).
Windows PowerShell 5.1+ (built into Windows 10/11), also pwsh 7 on macOS/Linux.

  powershell -NoProfile -ExecutionPolicy Bypass -File pixel_hook.ps1 install -Server https://<host> -Room <roomId> -Key <writeKey> -Name "Timur"
  powershell -NoProfile -ExecutionPolicy Bypass -File pixel_hook.ps1 install -Server http://127.0.0.1:8765 -Name "Timur"
  ... pixel_hook.ps1 uninstall | test | doctor
  ... pixel_hook.ps1 status -Session build -State working -Tool Build -Label "Derleniyor"
  ... pixel_hook.ps1 hook     # what Claude Code runs: hook JSON on stdin, always exit 0, no output

Same mapping table, same state-file format and the same privacy allow-list as pixel_hook.py: only
hook_event_name, session_id (hashed), agent_id (hashed), agent_type (built-in names only), cwd (basename or
alias), source, tool_name, is_interrupt, notification_type and error (allow-listed enum) are ever read.
This file is pure ASCII on purpose (PowerShell 5.1 reads BOM-less files in the ANSI code page); the Turkish
labels are built from [char] codes.
#>
param(
    [Parameter(Position = 0)][string]$Command = 'hook',
    [string]$Server,
    [string]$Room,
    [string]$Key,
    [string]$Name,
    [int]$Color = -1,
    [string]$SettingsPath,
    [ValidateSet('exec', 'shell')][string]$CommandStyle = 'exec',
    [switch]$NoTest,
    [string]$Session,
    [string]$State,
    [string]$Tool,
    [string]$Label,
    [string]$Project,
    [string]$Owner,
    [string]$Source = 'cli',
    [switch]$Final,
    [double]$Seconds = 20
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$VERSION = '2.0.0'
$SOURCE_CC = 'claude-code'
$MARKER = 'pixel_hook'
$DEBOUNCE = 1.0
$KEEPALIVE = 300.0
$TOOL_MIN_INTERVAL = 30.0
$TOOL_MIN_INTERVAL_SUB = 60.0
$POST_TIMEOUT = 4
$END_TIMEOUT = 3
$LOG_MAX = 65536
$STATE_PRUNE_AGE = 7 * 86400

# --- fixed Turkish labels (ASCII source, see header) ---
$c_i = [string][char]0x0131   # dotless i
$c_I = [string][char]0x0130   # capital I with dot
$c_g = [string][char]0x011F   # g breve
$c_s = [string][char]0x015F   # s cedilla
$c_O = [string][char]0x00D6   # O umlaut
$L_ASK = 'Soru soruyor'
$L_PLAN = "Plan onay$c_i bekliyor"
$L_PERMISSION = "${c_I}zin bekliyor"
$L_ANSWER = "Yan${c_i}t bekliyor"
$L_INTERRUPTED = 'Durduruldu'
$L_COMPACT = "Ba${c_g}lam s${c_i}k${c_i}${c_s}t${c_i}r${c_i}l${c_i}yor"
$L_API_ERROR = "API hatas$c_i"
$L_SUBAGENT = 'Alt-ajan'
$L_HIDDEN_PROJECT = 'Gizli proje'
$L_CUSTOM_AGENT = "${c_O}zel ajan"
$L_SETUP_TEST = 'Kurulum testi'

$BUILTIN_AGENT_TYPES = @('general-purpose', 'Explore', 'Plan', 'statusline-setup', 'claude-code-guide', 'claude')
$STOP_FAILURE_ERRORS = @('rate_limit', 'authentication_failed', 'billing_error', 'invalid_request', 'server_error',
    'max_output_tokens', 'overloaded', 'unknown')
$KNOWN_EVENTS = @('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PermissionRequest', 'PostToolUse',
    'PostToolUseFailure', 'Notification', 'PreCompact', 'Stop', 'StopFailure', 'SubagentStart', 'SubagentStop',
    'SessionEnd')
$MATCHER_EVENTS = @('PreToolUse', 'PermissionRequest', 'PostToolUse', 'PostToolUseFailure')
$STATES = @('working', 'thinking', 'waiting_for_user', 'idle', 'sleeping', 'offline')

$IsWin = ($env:OS -eq 'Windows_NT')
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ------------------------------------------------------------------------------------------------
# JSON helpers (dictionaries everywhere, so 5.1 and 7 behave the same)
# ------------------------------------------------------------------------------------------------

function ConvertFrom-JsonAny([string]$text) {
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        return (ConvertFrom-Json -InputObject $text -AsHashtable)
    }
    # Windows PowerShell 5.1: ConvertFrom-Json is limited to 2 MB and returns PSCustomObjects.
    Add-Type -AssemblyName System.Web.Extensions
    $ser = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $ser.MaxJsonLength = [int]::MaxValue
    $ser.RecursionLimit = 1000
    return $ser.DeserializeObject($text)
}

function ConvertTo-JsonText($obj, [switch]$Pretty) {
    if ($Pretty) { return (ConvertTo-Json -InputObject $obj -Depth 64) }
    return (ConvertTo-Json -InputObject $obj -Depth 64 -Compress)
}

function Test-Key($d, [string]$k) {
    # IDictionary.Contains is an explicit interface member on Dictionary<,> (not callable in 5.1)
    if (-not ($d -is [System.Collections.IDictionary])) { return $false }
    foreach ($x in $d.Keys) { if ($x -ceq $k) { return $true } }
    return $false
}

function Get-Prop($o, [string]$name) {
    if ($null -eq $o) { return $null }
    if ($o -is [System.Collections.IDictionary]) {
        if (Test-Key $o $name) { return $o[$name] }
        return $null
    }
    $p = $o.PSObject.Properties[$name]
    if ($p) { return $p.Value }
    return $null
}

function Test-Str($v) { return ($v -is [string] -and $v.Length -gt 0) }
function Test-Int($v) { return (($v -is [int] -or $v -is [long]) -and -not ($v -is [bool])) }

function Read-JsonFile([string]$path) {
    try {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
        $t = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
        if (-not $t.Trim()) { return $null }
        return (ConvertFrom-JsonAny $t)
    } catch { return $null }
}

function Write-TextAtomic([string]$path, [string]$text) {
    $dir = Split-Path -Parent $path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $tmp = Join-Path $dir ('.' + (Split-Path -Leaf $path) + '.' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.tmp')
    [System.IO.File]::WriteAllText($tmp, $text, $Utf8NoBom)
    if (Test-Path -LiteralPath $path) {
        try { [System.IO.File]::Replace($tmp, $path, $null) } catch { Move-Item -LiteralPath $tmp -Destination $path -Force }
    } else {
        Move-Item -LiteralPath $tmp -Destination $path -Force
    }
}

function Get-Sha([string]$s) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $b = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($s))
    } finally { $sha.Dispose() }
    return (-join ($b | ForEach-Object { $_.ToString('x2') }))
}

function Get-Now { return ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0) }

# ------------------------------------------------------------------------------------------------
# Pure mapping (CLOUD.md 7.2, 7.4) - must match map_event() in pixel_hook.py
# ------------------------------------------------------------------------------------------------

function Get-HashId([string]$salt, $raw) {
    $r = ''
    if ($raw -is [string]) { $r = $raw }
    return (Get-Sha ($salt + $r)).Substring(0, 20)
}

function Get-Clean([string]$s, [int]$limit) {
    $t = $s -replace '[\x00-\x1f\x7f-\x9f]', ''
    $t = ($t -replace '\s+', ' ').Trim()
    if ($t.Length -gt $limit) { $t = $t.Substring(0, $limit) }
    return $t.Trim()
}

function Split-AnyPath([string]$p) { return @($p -split '[\\/]+' | Where-Object { $_ -ne '' }) }

function Get-NormPath([string]$p) {
    $lead = ''
    if ($p.Length -gt 0 -and ($p[0] -eq '/' -or $p[0] -eq '\')) { $lead = '/' }
    return $lead + ((Split-AnyPath $p) -join '/')
}

function Get-ProjectFor($cwd, $cfg) {
    if ((Get-Prop $cfg 'hide_projects') -eq $true) { return $L_HIDDEN_PROJECT }
    if (-not (Test-Str $cwd)) { return $null }
    $aliases = Get-Prop $cfg 'project_aliases'
    if ($null -ne $aliases) {
        $pairs = @()
        if ($aliases -is [System.Collections.IDictionary]) {
            foreach ($k in $aliases.Keys) { $pairs += , @($k, $aliases[$k]) }
        } else {
            foreach ($pp in $aliases.PSObject.Properties) { $pairs += , @($pp.Name, $pp.Value) }
        }
        $here = Get-NormPath $cwd
        $ci = $IsWin -or ($here.Length -ge 2 -and $here.Substring(0, [Math]::Min(3, $here.Length)).Contains(':'))
        if ($ci) { $here = $here.ToLowerInvariant() }
        $best = $null; $bestLen = -1
        foreach ($pair in $pairs) {
            $k = $pair[0]; $alias = $pair[1]
            if (-not (Test-Str $k) -or -not ($alias -is [string])) { continue }
            $kn = Get-NormPath $k
            $kci = $IsWin -or ($kn.Length -ge 2 -and $kn.Substring(0, [Math]::Min(3, $kn.Length)).Contains(':'))
            if ($kci) { $kn = $kn.ToLowerInvariant() }
            if ($here -ceq $kn -or $here.StartsWith($kn.TrimEnd('/') + '/', [StringComparison]::Ordinal)) {
                if ($kn.Length -gt $bestLen) { $best = $alias; $bestLen = $kn.Length }
            }
        }
        if ($null -ne $best) {
            $a = Get-Clean (($best -replace '/', '-') -replace '\\', '-') 32
            if ($a) { return $a }
            return $null
        }
    }
    $parts = @(Split-AnyPath $cwd)
    if ($parts.Count -eq 0) { return $null }
    $base = Get-Clean $parts[$parts.Count - 1] 32
    if (-not $base -or $base.EndsWith(':')) { return $null }
    return $base
}

function Get-ToolFor($name, $cfg) {
    if (-not (Test-Str $name)) { return $null }
    if ($name.StartsWith('mcp__', [StringComparison]::Ordinal)) {
        if ((Get-Prop $cfg 'send_mcp_names') -ne $true) { return 'MCP' }
        if ($name.Length -gt 32) { $name = $name.Substring(0, 32) }
    }
    if ($name -cmatch '^[A-Za-z0-9_.:-]{1,32}$') { return $name }
    return $null
}

function Get-AgentTypeFor($v) {
    if (-not (Test-Str $v)) { return $null }
    if ($BUILTIN_AGENT_TYPES -ccontains $v) { return $v }
    return $L_CUSTOM_AGENT
}

function Get-RecordIds($ev, $cfg) {
    $salt = [string](Get-Prop $cfg 'salt')
    $s = Get-HashId $salt (Get-Prop $ev 'session_id')
    $raw = Get-Prop $ev 'agent_id'
    $sub = $null
    if (Test-Str $raw) { $sub = Get-HashId $salt $raw }
    return , @($s, $sub)
}

function New-Update($ev, $cfg, [string]$session, $sub, [string]$state, $tool, $label, $atype) {
    $d = [ordered]@{ session = $session }
    if ($sub) { $d['sub'] = $sub }
    $owner = Get-Prop $cfg 'name'
    if (-not (Test-Str $owner)) { $owner = Get-Prop $cfg 'owner' }
    if (Test-Str $owner) { if ($owner.Length -gt 24) { $owner = $owner.Substring(0, 24) }; $d['owner'] = $owner }
    $oid = Get-Prop $cfg 'ownerId'
    if ((Test-Str $oid) -and $oid -cmatch '^[A-Za-z0-9_-]{4,32}$') { $d['ownerId'] = $oid }
    $proj = Get-ProjectFor (Get-Prop $ev 'cwd') $cfg
    if ($proj) { $d['project'] = $proj }
    $d['state'] = $state
    if ($tool) { $d['tool'] = $tool }
    if ($label) { $d['label'] = $label }
    if ($atype) { $d['agentType'] = $atype }
    $d['source'] = $SOURCE_CC
    $col = Get-Prop $cfg 'color'
    if ((Test-Int $col) -and $col -ge 0 -and $col -le 15) { $d['color'] = [int]$col }
    return $d
}

function Get-MappedUpdates($ev, $cfg, $last) {
    # Returns one ordered dictionary, or $null when nothing is sent (the hook path never yields more than
    # one update per event; returning a scalar avoids PowerShell's array unrolling). Pure: no I/O.
    if ($null -eq $ev -or $null -eq $cfg) { return $null }
    if ((Get-Prop $cfg 'enabled') -eq $false) { return $null }
    $name = Get-Prop $ev 'hook_event_name'
    if (-not ($name -is [string]) -or -not ($KNOWN_EVENTS -ccontains $name)) { return $null }
    $ids = Get-RecordIds $ev $cfg
    $session = $ids[0]; $sub = $ids[1]
    if ($name -ceq 'SessionEnd') { $sub = $null }
    $atype = $null
    if ($sub) { $atype = Get-AgentTypeFor (Get-Prop $ev 'agent_type') }
    $u = $null
    switch -CaseSensitive ($name) {
        'SessionStart' { if ((Get-Prop $ev 'source') -cne 'compact') { $u = @('idle', $null, $null) } }
        'UserPromptSubmit' { $u = @('thinking', $null, $null) }
        'PreToolUse' {
            $raw = Get-Prop $ev 'tool_name'
            $t = Get-ToolFor $raw $cfg
            if ($raw -ceq 'AskUserQuestion') { $u = @('waiting_for_user', $t, $L_ASK) }
            elseif ($raw -ceq 'ExitPlanMode') { $u = @('waiting_for_user', $t, $L_PLAN) }
            else { $u = @('working', $t, $null) }
        }
        'PermissionRequest' { $u = @('waiting_for_user', (Get-ToolFor (Get-Prop $ev 'tool_name') $cfg), $L_PERMISSION) }
        'PostToolUse' { if ($last -ceq 'waiting_for_user') { $u = @('thinking', $null, $null) } }
        'PostToolUseFailure' {
            $intr = Get-Prop $ev 'is_interrupt'
            if ($intr -is [bool] -and $intr) { $u = @('idle', $null, $L_INTERRUPTED) }
            elseif ($last -ceq 'waiting_for_user') { $u = @('thinking', $null, $null) }
        }
        'Notification' {
            $nt = Get-Prop $ev 'notification_type'
            $wl = $null
            if ($nt -ceq 'permission_prompt') { $wl = $L_PERMISSION }
            elseif ($nt -ceq 'elicitation_dialog' -or $nt -ceq 'agent_needs_input') { $wl = $L_ANSWER }
            if ($wl) { if ($last -cne 'waiting_for_user') { $u = @('waiting_for_user', $null, $wl) } }
            elseif ($nt -ceq 'idle_prompt') { $u = @('idle', $null, $null) }
        }
        'PreCompact' { $u = @('thinking', $null, $L_COMPACT) }
        'Stop' { $u = @('idle', $null, $null) }
        'StopFailure' {
            $err = Get-Prop $ev 'error'
            $lbl = $L_API_ERROR
            if (($err -is [string]) -and ($STOP_FAILURE_ERRORS -ccontains $err)) { $lbl = $L_API_ERROR + ': ' + $err }
            $u = @('idle', $null, $lbl)
        }
        'SubagentStart' { if ($sub) { $u = @('working', $null, $L_SUBAGENT) } }
        'SubagentStop' { if ($sub) { $u = @('sleeping', $null, $null) } }
        'SessionEnd' { $u = @('offline', $null, $null) }
    }
    if ($null -eq $u) { return $null }
    return (New-Update $ev $cfg $session $sub $u[0] $u[1] $u[2] $atype)
}

# ------------------------------------------------------------------------------------------------
# Paths, config, log, HTTP
# ------------------------------------------------------------------------------------------------

function Get-OfficeHome {
    if ($env:PIXEL_OFFICE_HOME) { return $env:PIXEL_OFFICE_HOME }
    $h = $env:USERPROFILE
    if (-not $h) { $h = $HOME }
    return (Join-Path $h '.pixel-office')
}

function Get-ClaudeSettingsPath([string]$override) {
    if ($override) { return $override }
    if ($env:PIXEL_OFFICE_CLAUDE_SETTINGS) { return $env:PIXEL_OFFICE_CLAUDE_SETTINGS }
    $h = $env:USERPROFILE
    if (-not $h) { $h = $HOME }
    return (Join-Path (Join-Path $h '.claude') 'settings.json')
}

function Get-Config([string]$oh) {
    $c = Read-JsonFile (Join-Path $oh 'config.json')
    if ($c -is [System.Collections.IDictionary]) { return $c }
    return $null
}

function Write-HookLog([string]$oh, [string]$text) {
    try {
        $p = Join-Path $oh 'hook.log'
        if ((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p).Length -gt $LOG_MAX)) {
            Move-Item -LiteralPath $p -Destination ($p + '.1') -Force
        }
        $line = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss') + ' ' + $text + "`n"
        [System.IO.File]::AppendAllText($p, $line, $Utf8NoBom)
    } catch { }
}

function Get-StatusUrl($cfg) {
    $base = [string](Get-Prop $cfg 'server')
    if (-not $base) { $base = 'http://127.0.0.1:8765' }
    $url = $base.TrimEnd('/') + '/api/status'
    $r = Get-Prop $cfg 'room'
    if (Test-Str $r) { $url += '?room=' + [Uri]::EscapeDataString($r) }
    return $url
}

function Send-Updates($cfg, $updates, [int]$timeout = 4, [int]$retries = 1) {
    # Returns the HTTP status, 0 on network error. Never throws.
    try {
        $list = @($updates)
        if ($list.Count -eq 0) { return 0 }
        if ($list.Count -eq 1) { $body = $list[0] } else { $body = [ordered]@{ agents = @($list | Select-Object -First 10) } }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes((ConvertTo-JsonText $body))
        $headers = @{ 'User-Agent' = "pixel-hook-ps/$VERSION" }
        $k = Get-Prop $cfg 'key'
        if (Test-Str $k) { $headers['Authorization'] = 'Bearer ' + $k }
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        } catch { }
        $url = Get-StatusUrl $cfg
    } catch { return 0 }
    for ($attempt = 0; $attempt -le $retries; $attempt++) {
        $st = 0
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri $url -Method Post -Body $bytes `
                -ContentType 'application/json; charset=utf-8' -Headers $headers -TimeoutSec $timeout
            return [int]$r.StatusCode
        } catch {
            try { $st = [int]$_.Exception.Response.StatusCode } catch { $st = 0 }
        }
        if (($st -eq 0 -or $st -ge 500) -and $attempt -lt $retries) { Start-Sleep -Seconds 1; continue }
        return $st
    }
    return 0
}

# ------------------------------------------------------------------------------------------------
# Stateful runner (CLOUD.md 7.3) - same state files as pixel_hook.py
# ------------------------------------------------------------------------------------------------

function Test-IgnoredCwd($cwd) {
    if (-not (Test-Str $cwd)) { return $false }
    try {
        $p = [System.IO.Path]::GetFullPath($cwd)
        for ($i = 0; $i -lt 64; $i++) {
            if (Test-Path -LiteralPath (Join-Path $p '.pixel-office-ignore')) { return $true }
            $parent = [System.IO.Path]::GetDirectoryName($p)
            if (-not $parent -or $parent -eq $p) { break }
            $p = $parent
        }
    } catch { }
    return $false
}

function Get-RecPath([string]$stateDir, $cfg, [string]$session, $sub) {
    $s = ''
    if ($sub) { $s = $sub }
    $rid = (Get-Sha ([string](Get-Prop $cfg 'salt') + $session + $s)).Substring(0, 16)
    return (Join-Path $stateDir ($rid + '.json'))
}

function Read-Rec([string]$stateDir, $cfg, [string]$session, $sub) {
    $r = Read-JsonFile (Get-RecPath $stateDir $cfg $session $sub)
    $rec = [ordered]@{ seq = 0; last = $null; pending = $null; since = $null; started = $null; tc = 0 }
    if ($r -is [System.Collections.IDictionary]) { foreach ($k in @($r.Keys)) { $rec[$k] = $r[$k] } }
    $rec['s'] = $session
    $rec['sub'] = $sub
    return $rec
}

function Save-Rec([string]$stateDir, $cfg, $rec) {
    Write-TextAtomic (Get-RecPath $stateDir $cfg $rec['s'] $rec['sub']) (ConvertTo-JsonText $rec)
}

function Get-EffectiveLast($rec) {
    $p = $rec['pending']
    if ($null -ne $p -and (Test-Str (Get-Prop $p 'state'))) { return (Get-Prop $p 'state') }
    $l = $rec['last']
    if ($null -ne $l) { return (Get-Prop $l 'st') }
    return $null
}

function Get-Lock {
    try {
        $m = New-Object System.Threading.Mutex($false, 'PixelOfficeHookState')
        try { [void]$m.WaitOne(3000) } catch [System.Threading.AbandonedMutexException] { }
        return $m
    } catch { return $null }
}
function Release-Lock($m) { if ($m) { try { $m.ReleaseMutex() } catch { }; try { $m.Dispose() } catch { } } }

function Invoke-HookEvent($ev, $cfg, [string]$oh) {
    $name = Get-Prop $ev 'hook_event_name'
    if (-not ($name -is [string]) -or -not ($KNOWN_EVENTS -ccontains $name)) { return 'none' }
    if ((Get-Prop $cfg 'enabled') -eq $false) { return 'none' }
    if (Test-IgnoredCwd (Get-Prop $ev 'cwd')) { return 'none' }
    $stateDir = Join-Path $oh 'state'
    if (-not (Test-Path -LiteralPath $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
    $ids = Get-RecordIds $ev $cfg
    $session = $ids[0]; $sub = $ids[1]
    $isEnd = ($name -ceq 'SessionEnd')
    if ($isEnd) { $sub = $null }
    $now = Get-Now
    $stateChange = $false
    $lock = Get-Lock
    try {
        $rec = Read-Rec $stateDir $cfg $session $sub
        if ($name -ceq 'PreToolUse') { $rec['tc'] = [long]$rec['tc'] + 1 }
        $u = Get-MappedUpdates $ev $cfg (Get-EffectiveLast $rec)
        if ($null -eq $u) {
            if ($name -ceq 'PreToolUse') { Save-Rec $stateDir $cfg $rec }
            return 'none'
        }
        if ($sub) {
            if (Test-Key $u 'agentType') { $rec['atype'] = $u['agentType'] }
            elseif (Test-Str $rec['atype']) { $u['agentType'] = $rec['atype'] }
        }
        $prev = Get-EffectiveLast $rec
        if ($null -eq $rec['since'] -or $prev -cne $u['state']) { $rec['since'] = $now }
        if ($null -eq $rec['started']) { $rec['started'] = $now }
        $u['stateAgeMs'] = [long][Math]::Max(0, ($now - [double]$rec['since']) * 1000)
        $u['startedAgeMs'] = [long][Math]::Max(0, ($now - [double]$rec['started']) * 1000)
        $u['toolCount'] = [long]$rec['tc']
        $last = $rec['last']
        $lt = 0.0
        if ($null -ne $last) { $lt = [double](Get-Prop $last 't') }
        if ($null -ne $last -and -not $isEnd -and ((Get-Prop $last 'st') -ceq $u['state']) -and
            ((Get-Prop $last 'tool') -ceq $u['tool']) -and ((Get-Prop $last 'lbl') -ceq $u['label']) -and
            ((Get-Prop $last 'proj') -ceq $u['project']) -and ($now - $lt -lt $KEEPALIVE)) {
            $rec['pending'] = $null
            Save-Rec $stateDir $cfg $rec
            return 'dedupe'
        }
        $rec['seq'] = [long]$rec['seq'] + 1
        $rec['pending'] = $u
        $mySeq = [long]$rec['seq']
        $lastSt = $null
        if ($null -ne $last) { $lastSt = Get-Prop $last 'st' }
        $stateChange = ($null -eq $last -or $lastSt -cne $u['state'] -or $lastSt -ceq 'waiting_for_user' -or
            $u['state'] -ceq 'waiting_for_user' -or $lastSt -ceq 'offline' -or $u['state'] -ceq 'offline')
        if (-not $stateChange) {
            $interval = $TOOL_MIN_INTERVAL
            if ($sub) { $interval = $TOOL_MIN_INTERVAL_SUB }
            if ($now - $lt -lt $interval) { Save-Rec $stateDir $cfg $rec; return 'throttled' }
        }
        Save-Rec $stateDir $cfg $rec
    } finally { Release-Lock $lock }

    $send = New-Object System.Collections.ArrayList
    [void]$send.Add($u)
    $extraSubs = @()
    if ($isEnd) {
        foreach ($f in @(Get-ChildItem -LiteralPath $stateDir -Filter '*.json' -ErrorAction SilentlyContinue)) {
            $r = Read-JsonFile $f.FullName
            if (-not ($r -is [System.Collections.IDictionary])) { continue }
            if ((Get-Prop $r 's') -cne $session -or -not (Test-Str (Get-Prop $r 'sub'))) { continue }
            $l = Get-Prop $r 'last'
            $lst = Get-Prop $l 'st'
            if ($null -ne $lst -and $lst -cne 'offline') { $extraSubs += (Get-Prop $r 'sub') }
        }
        $extraSubs = @($extraSubs | Sort-Object | Select-Object -First 9)
        foreach ($s in $extraSubs) {
            $x = [ordered]@{ session = $session; sub = $s }
            if ((Test-Key $u 'owner')) { $x['owner'] = $u['owner'] }
            if ((Test-Key $u 'ownerId')) { $x['ownerId'] = $u['ownerId'] }
            $x['state'] = 'offline'; $x['source'] = $SOURCE_CC
            [void]$send.Add($x)
        }
    } elseif ($stateChange) {
        Start-Sleep -Milliseconds ([int]($DEBOUNCE * 1000))
        $lock = Get-Lock
        try {
            $cur = Read-Rec $stateDir $cfg $session $sub
            if ([long]$cur['seq'] -ne $mySeq) { return 'superseded' }
        } finally { Release-Lock $lock }
    }

    if ($isEnd) { $status = Send-Updates $cfg $send.ToArray() $END_TIMEOUT 0 }
    else { $status = Send-Updates $cfg $send.ToArray() $POST_TIMEOUT 1 }
    $shown = 'neterr'
    if ($status) { $shown = [string]$status }
    Write-HookLog $oh ($name + ' ' + $shown)
    if ($isEnd) {
        foreach ($f in @(Get-ChildItem -LiteralPath $stateDir -Filter '*.json' -ErrorAction SilentlyContinue)) {
            $r = Read-JsonFile $f.FullName
            if ((Get-Prop $r 's') -ceq $session) { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue }
        }
        try {
            $cut = (Get-Date).AddSeconds(-$STATE_PRUNE_AGE)
            Get-ChildItem -LiteralPath $stateDir -Filter '*.json' -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -lt $cut } | Remove-Item -Force -ErrorAction SilentlyContinue
        } catch { }
        return "sent:$status"
    }
    if ($status -ge 200 -and $status -lt 300) {
        $lock = Get-Lock
        try {
            $cur = Read-Rec $stateDir $cfg $session $sub
            $cur['last'] = [ordered]@{ st = $u['state']; tool = $u['tool']; lbl = $u['label']; proj = $u['project']; t = (Get-Now) }
            if ([long]$cur['seq'] -eq $mySeq) { $cur['pending'] = $null }
            Save-Rec $stateDir $cfg $cur
        } finally { Release-Lock $lock }
    }
    return "sent:$status"
}

function Invoke-Hook {
    try {
        $reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [System.Text.Encoding]::UTF8)
        $raw = $reader.ReadToEnd()
        if ($env:PIXEL_OFFICE_DISABLE -eq '1') { return }
        $oh = Get-OfficeHome
        $cfg = Get-Config $oh
        if ($null -eq $cfg -or (Get-Prop $cfg 'enabled') -eq $false) { return }
        if (-not $raw -or $raw.Length -gt 64MB) { return }
        $ev = ConvertFrom-JsonAny $raw
        $raw = $null
        if (-not ($ev -is [System.Collections.IDictionary])) { return }
        [void](Invoke-HookEvent $ev $cfg $oh)
    } catch { }
}

# ------------------------------------------------------------------------------------------------
# settings.json merge (CLOUD.md 7.5.1)
# ------------------------------------------------------------------------------------------------

function Test-OurHook($h) {
    if ($null -eq $h) { return $false }
    $parts = @(Get-Prop $h 'command')
    $a = Get-Prop $h 'args'
    if ($null -ne $a -and -not ($a -is [string])) { $parts += @($a) }
    foreach ($p in $parts) { if (($p -is [string]) -and $p.Contains($MARKER)) { return $true } }
    return $false
}

function Remove-OurHooks($settings) {
    $removed = 0
    $hooks = Get-Prop $settings 'hooks'
    if (-not ($hooks -is [System.Collections.IDictionary])) { return 0 }
    foreach ($ev in @($hooks.Keys)) {
        $groups = $hooks[$ev]
        if ($null -eq $groups -or $groups -is [string] -or $groups -is [System.Collections.IDictionary]) { continue }
        $newGroups = New-Object System.Collections.ArrayList
        foreach ($g in @($groups)) {
            $hs = Get-Prop $g 'hooks'
            $ours = $false
            if ($null -ne $hs -and -not ($hs -is [string])) { foreach ($h in @($hs)) { if (Test-OurHook $h) { $ours = $true } } }
            if ($ours) {
                $kept = @(@($hs) | Where-Object { -not (Test-OurHook $_) })
                $removed += @($hs).Count - $kept.Count
                if ($kept.Count -gt 0) { $g['hooks'] = $kept; [void]$newGroups.Add($g) }
            } else { [void]$newGroups.Add($g) }
        }
        if ($newGroups.Count -gt 0) { $hooks[$ev] = $newGroups.ToArray() } else { [void]$hooks.Remove($ev) }
    }
    if ($hooks.Count -eq 0) { [void]$settings.Remove('hooks') }
    return $removed
}

function New-HookEntries([string]$cmd, $hookArgs) {
    $entries = [ordered]@{}
    foreach ($ev in $KNOWN_EVENTS) {
        $h = [ordered]@{ type = 'command'; command = $cmd }
        if ($null -ne $hookArgs) { $h['args'] = @($hookArgs) }
        if ($ev -ceq 'SessionEnd') { $h['timeout'] = 5 } else { $h['async'] = $true }
        $g = [ordered]@{}
        if ($MATCHER_EVENTS -ccontains $ev) { $g['matcher'] = '*' }
        $g['hooks'] = @($h)
        $entries[$ev] = @($g)
    }
    return $entries
}

function Read-Settings([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return [ordered]@{} }
    $t = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
    if (-not $t.Trim()) { return [ordered]@{} }
    try { $d = ConvertFrom-JsonAny $t } catch { throw "$path is not valid JSON. Nothing was changed." }
    if (-not ($d -is [System.Collections.IDictionary])) { throw "$path is not a JSON object. Nothing was changed." }
    return $d
}

function Backup-Settings([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $bak = $path + '.pixel-backup'
    if (-not (Test-Path -LiteralPath $bak)) { Copy-Item -LiteralPath $path -Destination $bak }
    return $bak
}

function Install-Hooks([string]$path, [string]$cmd, $hookArgs) {
    $data = Read-Settings $path
    $bak = Backup-Settings $path
    $hooksVal = Get-Prop $data 'hooks'
    if ($null -ne $hooksVal -and -not ($hooksVal -is [System.Collections.IDictionary])) { throw '"hooks" in settings.json is not an object' }
    [void](Remove-OurHooks $data)
    if (-not (Test-Key $data 'hooks')) { $data['hooks'] = [ordered]@{} }
    $hooks = $data['hooks']
    $entries = New-HookEntries $cmd $hookArgs
    foreach ($ev in $entries.Keys) {
        if ((Test-Key $hooks $ev)) { $hooks[$ev] = @(@($hooks[$ev]) + @($entries[$ev])) }
        else { $hooks[$ev] = $entries[$ev] }
    }
    Write-TextAtomic $path ((ConvertTo-JsonText $data -Pretty) + "`n")
    return $bak
}

# ------------------------------------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------------------------------------

function Get-Mask($k) { if (-not (Test-Str $k)) { return '(yok)' }; if ($k.Length -gt 8) { return $k.Substring(0, 4) + '...' + $k.Substring($k.Length - 2) }; return '****' }

function New-OwnerId {
    $b = New-Object byte[] 9
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return 'o_' + ([Convert]::ToBase64String($b).Replace('+', '-').Replace('/', '_').TrimEnd('='))
}
function New-Salt {
    $b = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return (-join ($b | ForEach-Object { $_.ToString('x2') }))
}

function Get-PwshCommand {
    if ($IsWin) { return 'powershell.exe' }
    try { return (Get-Process -Id $PID).Path } catch { return 'pwsh' }
}

function Invoke-Install {
    if (-not ($Server -match '^https?://[^/\s]+')) { [Console]::Error.WriteLine('Hata: -Server bir http(s) adresi olmali'); exit 2 }
    if ($Room -and -not ($Room -cmatch '^[a-z2-7]{26}$')) { [Console]::Error.WriteLine('Hata: -Room 26 karakterlik oda kimligi olmali'); exit 2 }
    if ($Room -and -not $Key) { [Console]::Error.WriteLine('Hata: -Room ile -Key gerekli'); exit 2 }
    if (-not $Name -or -not $Name.Trim() -or $Name.Trim().Length -gt 24) { [Console]::Error.WriteLine('Hata: -Name 1-24 karakter olmali'); exit 2 }
    $oh = Get-OfficeHome
    if (-not (Test-Path -LiteralPath $oh)) { New-Item -ItemType Directory -Path $oh -Force | Out-Null }
    $cfg = Get-Config $oh
    if ($null -eq $cfg) { $cfg = [ordered]@{} }
    $cfg['server'] = $Server.TrimEnd('/')
    $cfg['room'] = $(if ($Room) { $Room } else { $null })
    $cfg['key'] = $(if ($Key) { $Key } else { $null })
    $cfg['name'] = $Name.Trim()
    $cfg['version'] = $VERSION
    if ($Color -ge 0 -and $Color -le 15) { $cfg['color'] = $Color }
    $oid = Get-Prop $cfg 'ownerId'
    if (-not ((Test-Str $oid) -and $oid -cmatch '^[A-Za-z0-9_-]{4,32}$')) { $cfg['ownerId'] = New-OwnerId }
    $salt = Get-Prop $cfg 'salt'
    if (-not ((Test-Str $salt) -and $salt.Length -ge 16)) { $cfg['salt'] = New-Salt }
    if (-not (Test-Key $cfg 'project_aliases')) { $cfg['project_aliases'] = [ordered]@{} }
    if (-not (Test-Key $cfg 'hide_projects')) { $cfg['hide_projects'] = $false }
    if (-not (Test-Key $cfg 'send_mcp_names')) { $cfg['send_mcp_names'] = $false }
    if (-not (Test-Key $cfg 'enabled')) { $cfg['enabled'] = $true }
    $cfg['client'] = 'powershell'
    $dst = Join-Path $oh 'pixel_hook.ps1'
    $self = $PSCommandPath
    if ($self -and ([System.IO.Path]::GetFullPath($self) -ne [System.IO.Path]::GetFullPath($dst))) {
        Copy-Item -LiteralPath $self -Destination $dst -Force
    }
    Write-TextAtomic (Join-Path $oh 'config.json') (ConvertTo-JsonText $cfg -Pretty)
    if (-not $IsWin) { try { & chmod 600 (Join-Path $oh 'config.json') } catch { } }
    $cmd = Get-PwshCommand
    $hookArgs = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $dst, 'hook')
    if ($CommandStyle -eq 'shell') {
        $cmd = '"' + $cmd + '" ' + (($hookArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' ')
        $hookArgs = $null
    }
    $sp = Get-ClaudeSettingsPath $SettingsPath
    try { $bak = Install-Hooks $sp $cmd $hookArgs } catch { [Console]::Error.WriteLine(('Hata: ' + $_.Exception.Message)); exit 1 }
    Write-Output 'Pixel Office kancasi kuruldu.'
    Write-Output ('  Yapilandirma: ' + (Join-Path $oh 'config.json'))
    if ($bak) { Write-Output ("  Claude ayarlari: $sp (yedek: $bak)") } else { Write-Output "  Claude ayarlari: $sp" }
    if (-not $NoTest) { [void](Invoke-Test) }
    Write-Output 'Acik Claude Code oturumlarini yeniden baslatin.'
}

function Invoke-Uninstall {
    $sp = Get-ClaudeSettingsPath $SettingsPath
    if (-not (Test-Path -LiteralPath $sp)) { Write-Output "0 kanca kaldirildi ($sp yok)."; return }
    try {
        $data = Read-Settings $sp
        [void](Backup-Settings $sp)
        $n = Remove-OurHooks $data
        if ($n -gt 0) { Write-TextAtomic $sp ((ConvertTo-JsonText $data -Pretty) + "`n") }
    } catch { [Console]::Error.WriteLine(('Hata: ' + $_.Exception.Message)); exit 1 }
    Write-Output "$n Pixel Office kancasi kaldirildi ($sp). Diger kancalara dokunulmadi."
}

function Invoke-Test {
    $cfg = Get-Config (Get-OfficeHome)
    if ($null -eq $cfg) { [Console]::Error.WriteLine("Hata: once 'install' calistirin."); return 1 }
    $u = [ordered]@{ session = 'setup-test'; owner = [string](Get-Prop $cfg 'name'); state = 'idle'; label = $L_SETUP_TEST; source = $SOURCE_CC }
    if (Test-Str (Get-Prop $cfg 'ownerId')) { $u['ownerId'] = Get-Prop $cfg 'ownerId' }
    $st = Send-Updates $cfg @($u)
    if ($st -ge 200 -and $st -lt 300) {
        [Console]::Out.WriteLine("Test basarili: ofiste 'Kurulum testi' karakteri gorunmeli (20 sn sonra cikar).")
        try {
            $argList = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '_offline-later')
            if ($IsWin) { Start-Process -FilePath (Get-PwshCommand) -ArgumentList $argList -WindowStyle Hidden | Out-Null }
            else { Start-Process -FilePath (Get-PwshCommand) -ArgumentList $argList | Out-Null }
        } catch { }
        return 0
    }
    [Console]::Error.WriteLine("Test basarisiz (HTTP $st). 'doctor' ile kontrol edin.")
    return 1
}

function Invoke-OfflineLater {
    try {
        Start-Sleep -Seconds $Seconds
        $cfg = Get-Config (Get-OfficeHome)
        $u = [ordered]@{ session = 'setup-test'; owner = [string](Get-Prop $cfg 'name'); state = 'offline'; source = $SOURCE_CC }
        if (Test-Str (Get-Prop $cfg 'ownerId')) { $u['ownerId'] = Get-Prop $cfg 'ownerId' }
        [void](Send-Updates $cfg @($u))
    } catch { }
}

function Invoke-Status {
    $cfg = Get-Config (Get-OfficeHome)
    if ($null -eq $cfg) { $cfg = [ordered]@{} }
    if ($Server) { $cfg['server'] = $Server; $cfg['room'] = $Room; $cfg['key'] = $Key }
    if (-not ($Session -cmatch '^[A-Za-z0-9_-]{1,64}$')) { [Console]::Error.WriteLine('Hata: -Session gecersiz'); exit 2 }
    if (-not ($STATES -ccontains $State)) { [Console]::Error.WriteLine(('Hata: -State ' + ($STATES -join '|'))); exit 2 }
    $o = $Owner
    if (-not $o) { $o = Get-Prop $cfg 'name' }
    if (-not $o) { $o = $env:USERNAME }
    if (-not $o) { $o = $env:USER }
    if (-not $o) { $o = 'local' }
    $u = [ordered]@{ session = $Session; owner = $o; state = $State; source = $Source }
    if (-not $Owner -and (Test-Str (Get-Prop $cfg 'ownerId'))) { $u['ownerId'] = Get-Prop $cfg 'ownerId' }
    if ($Tool) { $u['tool'] = $Tool }
    if ($Label) { $u['label'] = $Label }
    if ($Project) { $u['project'] = $Project }
    if ($Final) { $u['final'] = $true }
    $st = Send-Updates $cfg @($u)
    if ($st -ge 200 -and $st -lt 300) { exit 0 }
    [Console]::Error.WriteLine("Gonderilemedi (HTTP $st)")
    exit 1
}

function Invoke-Doctor {
    $oh = Get-OfficeHome
    Write-Output "Pixel Office hook istemcisi (PowerShell) $VERSION"
    Write-Output "Klasor: $oh"
    $cfg = Get-Config $oh
    if ($null -eq $cfg) { Write-Output "config.json yok: once 'install' calistirin." }
    else {
        $shown = [ordered]@{}
        foreach ($k in $cfg.Keys) { $shown[$k] = $cfg[$k] }
        $shown['key'] = Get-Mask (Get-Prop $cfg 'key')
        $shown['salt'] = Get-Mask (Get-Prop $cfg 'salt')
        Write-Output ('Yapilandirma: ' + (ConvertTo-JsonText $shown))
    }
    $sp = Get-ClaudeSettingsPath $SettingsPath
    try {
        $data = Read-Settings $sp
        $hooks = Get-Prop $data 'hooks'
        $found = 0
        if ($hooks -is [System.Collections.IDictionary]) {
            foreach ($ev in $KNOWN_EVENTS) {
                if (-not (Test-Key $hooks $ev)) { continue }
                $ok = $false
                foreach ($g in @($hooks[$ev])) { foreach ($h in @(Get-Prop $g 'hooks')) { if (Test-OurHook $h) { $ok = $true } } }
                if ($ok) { $found++ }
            }
        }
        Write-Output ("settings.json: $sp - kancali olaylar: $found/" + $KNOWN_EVENTS.Count)
    } catch { Write-Output ("settings.json: " + $_.Exception.Message) }
    $log = Join-Path $oh 'hook.log'
    if (Test-Path -LiteralPath $log) { Write-Output 'Son gunluk satirlari:'; Get-Content -LiteralPath $log -Tail 20 | ForEach-Object { '  ' + $_ } }
    else { Write-Output 'Gunluk yok.' }
    $srv = Get-Prop $cfg 'server'
    if (Test-Str $srv) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri ($srv.TrimEnd('/') + '/api/health') -TimeoutSec 5
            Write-Output ("Sunucu: $srv -> HTTP " + [int]$r.StatusCode)
        } catch {
            $st = 0
            try { $st = [int]$_.Exception.Response.StatusCode } catch { }
            if ($st) { Write-Output "Sunucu: $srv -> HTTP $st" } else { Write-Output "Sunucu: $srv -> ulasilamiyor" }
        }
    }
}

function Invoke-Map {
    # Test helper: {"event":..., "cfg":..., "last":...} on stdin -> mapped updates JSON on stdout.
    $reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [System.Text.Encoding]::UTF8)
    $d = ConvertFrom-JsonAny ($reader.ReadToEnd())
    $u = Get-MappedUpdates (Get-Prop $d 'event') (Get-Prop $d 'cfg') (Get-Prop $d 'last')
    if ($null -eq $u) { [Console]::Out.Write("[]`n"); return }
    [Console]::Out.Write('[' + (ConvertTo-Json -InputObject $u -Depth 8 -Compress) + "]`n")
}

switch ($Command) {
    'hook' { Invoke-Hook; exit 0 }
    'install' { Invoke-Install; exit 0 }
    'uninstall' { Invoke-Uninstall; exit 0 }
    'test' { $r = Invoke-Test; exit $r }
    'status' { Invoke-Status }
    'doctor' { Invoke-Doctor; exit 0 }
    'map' { Invoke-Map; exit 0 }
    '_offline-later' { Invoke-OfflineLater; exit 0 }
    default { Write-Output 'Kullanim: pixel_hook.ps1 hook|install|uninstall|test|status|doctor'; exit 2 }
}
