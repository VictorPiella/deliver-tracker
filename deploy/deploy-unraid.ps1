<#
.SYNOPSIS
    Construye la imagen en Windows y la despliega en Unraid por SSH.

.DESCRIPTION
    Sin registro de imágenes de por medio: build local -> docker save -> scp ->
    docker load -> docker compose up -d. Unraid no compila nada.

    La imagen se construye para linux/amd64 explícitamente. Si algún día mueves
    esto a un Unraid sobre ARM, cambia -Platform.

.PARAMETER UnraidHost
    IP o hostname de Unraid. Ej: 192.168.1.10 o tower.local

.PARAMETER User
    Usuario SSH de Unraid. Por defecto root (lo habitual en Unraid).

.PARAMETER RemoteDir
    Carpeta en Unraid donde vive el docker-compose.yml del proyecto.

.PARAMETER Tag
    Tag de la imagen. Debe coincidir con el `image:` de docker-compose.unraid.yml.

.PARAMETER SkipCompose
    Sólo carga la imagen; no toca el compose remoto.

.EXAMPLE
    .\deploy\deploy-unraid.ps1 -UnraidHost 192.168.1.10

.EXAMPLE
    .\deploy\deploy-unraid.ps1 -UnraidHost tower.local -RemoteDir /mnt/user/appdata/deliver-tracker/compose
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$UnraidHost,

    [string]$User = "root",

    [string]$RemoteDir = "/boot/config/plugins/compose.manager/projects/deliver-tracker",

    [string]$Tag = "deliver-tracker:latest",

    [switch]$SkipCompose
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$OutDir   = Join-Path $PSScriptRoot "out"
$TarPath  = Join-Path $OutDir "deliver-tracker.tar"
$Remote   = "$User@$UnraidHost"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# --- Comprobaciones previas -------------------------------------------------
Step "Comprobando requisitos"
docker info --format '{{.ServerVersion}}' *> $null
if (-not $?) { Fail "Docker no responde. Arranca Docker Desktop y reintenta." }

& ssh -o BatchMode=yes -o ConnectTimeout=8 $Remote "echo ok" *> $null
if (-not $?) {
    Fail @"
No se pudo conectar por SSH a $Remote sin contraseña.
Copia tu clave publica a Unraid primero:
  type `$env:USERPROFILE\.ssh\id_ed25519.pub | ssh $Remote "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
Y en Unraid asegurate de que la clave persiste a reinicios (Settings -> Management Access, o /boot/config/ssh/).
"@
}
Write-Host "    Docker OK, SSH a $Remote OK"

# --- Build ------------------------------------------------------------------
Step "Construyendo $Tag (target prod, linux/amd64)"
Push-Location $RepoRoot
try {
    docker build --platform linux/amd64 --target prod -t $Tag .
    if ($LASTEXITCODE -ne 0) { Fail "El build fallo." }
} finally {
    Pop-Location
}

$size = docker image inspect $Tag --format '{{.Size}}'
Write-Host ("    Imagen construida: {0:N0} MB" -f ($size / 1MB))

# --- Exportar ---------------------------------------------------------------
Step "Exportando imagen a $TarPath"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
docker save -o $TarPath $Tag
if ($LASTEXITCODE -ne 0) { Fail "docker save fallo." }
Write-Host ("    {0:N0} MB en disco" -f ((Get-Item $TarPath).Length / 1MB))

# --- Copiar y cargar --------------------------------------------------------
Step "Copiando a Unraid y cargando la imagen"
& ssh $Remote "mkdir -p $RemoteDir"
if (-not $?) { Fail "No se pudo crear $RemoteDir en Unraid." }

# /tmp de Unraid vive en RAM. Un tar de ~200 MB cabe de sobra, y se borra solo
# al reiniciar, pero lo limpiamos igualmente al terminar.
& scp $TarPath "${Remote}:/tmp/deliver-tracker.tar"
if (-not $?) { Fail "scp fallo." }

& ssh $Remote "docker load -i /tmp/deliver-tracker.tar && rm -f /tmp/deliver-tracker.tar"
if (-not $?) { Fail "docker load fallo en Unraid." }

# --- Compose ----------------------------------------------------------------
if ($SkipCompose) {
    Step "Imagen cargada. -SkipCompose activo: no se toca el compose remoto."
    exit 0
}

Step "Actualizando docker-compose.yml en Unraid"
$ComposeSrc = Join-Path $PSScriptRoot "docker-compose.unraid.yml"

# Si ya hay un compose remoto (con tus secretos ya editados: FLASK_SECRET_KEY,
# credenciales MQTT...), no lo pisamos. Sobrescribirlo en cada despliegue
# borraria esa configuracion sin avisar.
$exists = & ssh $Remote "test -f $RemoteDir/docker-compose.yml && echo yes || echo no"
if ($exists.Trim() -eq "yes") {
    Write-Host "    Ya existe un docker-compose.yml remoto; se respeta tal cual." -ForegroundColor Yellow
    Write-Host "    Si quieres reemplazarlo:  scp deploy\docker-compose.unraid.yml ${Remote}:$RemoteDir/docker-compose.yml"
} else {
    & scp $ComposeSrc "${Remote}:$RemoteDir/docker-compose.yml"
    if (-not $?) { Fail "No se pudo copiar el compose." }
    Write-Host "    Compose copiado. EDITA FLASK_SECRET_KEY antes de exponerlo." -ForegroundColor Yellow
}

Step "Levantando el container"
& ssh $Remote "cd $RemoteDir && docker compose up -d"
if (-not $?) { Fail "docker compose up fallo en Unraid." }

Step "Estado"
& ssh $Remote "cd $RemoteDir && docker compose ps"

Write-Host "`nListo. Panel en http://${UnraidHost}:5000" -ForegroundColor Green
Write-Host "Logs:  ssh $Remote 'cd $RemoteDir && docker compose logs -f'"
