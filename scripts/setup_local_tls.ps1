[CmdletBinding()]
param(
    # Defaults to the IPv4 of the interface that owns the default gateway, matching
    # peach.mdns.lan_ipv4. Pass -Address when a full-tunnel proxy owns that route.
    [string]$Address = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } |
        Select-Object -First 1).IPv4Address.IPAddress,
    [string]$DnsName = 'peach.local',
    [string]$OutDir = '',
    [switch]$TrustCurrentUser,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Resolve-PeachTlsDirectory {
    param(
        [string]$RequestedDirectory = '',
        [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
    )
    if ($RequestedDirectory) { return $RequestedDirectory }

    # Reuse the settings authority for environment, discovery and directory overrides.
    $TlsPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $TlsPython -PathType Leaf)) {
        $TlsCommonGit = (& git -C $ProjectRoot rev-parse --path-format=absolute --git-common-dir).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Cannot locate the Peach project Python.' }
        $TlsPython = Join-Path (Split-Path -Parent $TlsCommonGit) '.venv\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $TlsPython -PathType Leaf)) {
        throw "Project Python not found: $TlsPython"
    }

    $TlsHelper = Join-Path $ProjectRoot 'scripts\resolve_tls_directory.py'
    $TlsDirectory = & $TlsPython -X utf8 $TlsHelper
    if ($LASTEXITCODE -ne 0 -or -not $TlsDirectory) {
        throw 'Cannot resolve the configured Peach TLS directory.'
    }
    return $TlsDirectory.Trim()
}

$OutDir = Resolve-PeachTlsDirectory -RequestedDirectory $OutDir
$openssl = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $openssl) {
    $bundled = 'C:\Program Files\Git\usr\bin\openssl.exe'
    if (Test-Path -LiteralPath $bundled -PathType Leaf) { $openssl = $bundled }
}
if (-not $openssl) { throw 'OpenSSL not found. Install it or use the Git for Windows bundle.' }

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$caKey = Join-Path $OutDir 'peach-local-ca.key'
$caCert = Join-Path $OutDir 'peach-local-ca.crt'
$serverKey = Join-Path $OutDir 'peach.key'
$serverCert = Join-Path $OutDir 'peach.crt'
$request = Join-Path $OutDir 'peach.csr'
$extensions = Join-Path $OutDir 'peach-san.cnf'
$previousCaThumbprint = $null

if ($Force -and $TrustCurrentUser -and (Test-Path -LiteralPath $caCert -PathType Leaf)) {
    $previousCaThumbprint = ((& $openssl x509 -in $caCert -noout -fingerprint -sha1) -replace '^.*=', '') -replace ':', ''
}

$targets = @($caKey, $caCert, $serverKey, $serverCert, $request, $extensions)
if (-not $Force -and ($targets | Where-Object { Test-Path -LiteralPath $_ })) {
    throw 'TLS files already exist. Re-run with -Force only when rotating the local CA.'
}

@"
[server]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:$DnsName,IP:$Address
"@ | Set-Content -LiteralPath $extensions -Encoding ascii

& $openssl genrsa -out $caKey 3072
if ($LASTEXITCODE) { throw 'Failed to create local CA key.' }
& $openssl req -x509 -new -sha256 -key $caKey -out $caCert -days 3650 `
    -subj '/CN=Peach Local CA/O=Peach' `
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' `
    -addext 'keyUsage=critical,keyCertSign,cRLSign' `
    -addext 'subjectKeyIdentifier=hash'
if ($LASTEXITCODE) { throw 'Failed to create local CA certificate.' }
& $openssl genrsa -out $serverKey 2048
if ($LASTEXITCODE) { throw 'Failed to create server key.' }
& $openssl req -new -sha256 -key $serverKey -out $request -subj "/CN=$DnsName/O=Peach"
if ($LASTEXITCODE) { throw 'Failed to create server request.' }
& $openssl x509 -req -sha256 -in $request -CA $caCert -CAkey $caKey -CAcreateserial `
    -out $serverCert -days 397 -extfile $extensions -extensions server
if ($LASTEXITCODE) { throw 'Failed to sign server certificate.' }

& $openssl verify -CAfile $caCert $serverCert | Out-Null
if ($LASTEXITCODE) { throw 'Generated certificate chain did not verify.' }

if ($TrustCurrentUser) {
    if ($previousCaThumbprint) {
        & certutil.exe -user -delstore Root $previousCaThumbprint | Out-Null
    }
    & certutil.exe -user -addstore Root $caCert | Out-Null
    if ($LASTEXITCODE) { throw 'Failed to trust Peach Local CA for the current Windows user.' }
}

$fingerprint = (& $openssl x509 -in $serverCert -noout -fingerprint -sha256) -replace '^.*=', ''
[pscustomobject]@{
    Certificate = $serverCert
    PrivateKey = $serverKey
    LocalCA = $caCert
    TrustedCurrentUser = [bool]$TrustCurrentUser
    Names = "$DnsName, $Address"
    SHA256Fingerprint = $fingerprint
}
