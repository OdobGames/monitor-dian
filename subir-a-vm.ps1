# Sube el monitor DIAN a una VM Ubuntu con systemd y lo instala.
#
#   powershell -ExecutionPolicy Bypass -File .\subir-a-vm.ps1
#   powershell -ExecutionPolicy Bypass -File .\subir-a-vm.ps1 -VmIp 1.2.3.4
#
# Los valores por defecto salen de vm-config.cmd. Solo copia el codigo y la
# configuracion; no sube el log ni las capturas.
param(
    [string]$VmIp,
    [string]$VmUser,
    [string]$VmKey,
    [switch]$SoloSubir           # copia los archivos pero no ejecuta el instalador
)

$ErrorActionPreference = "Stop"

# Lo que no venga por parametro se lee de vm-config.cmd, para no tener la IP
# escrita en dos sitios.
$config = Join-Path $PSScriptRoot "vm-config.cmd"
if (Test-Path $config) {
    foreach ($linea in Get-Content $config) {
        if ($linea -match '^\s*set\s+"(VM_IP|VM_USER|VM_KEY)=(.*)"\s*$') {
            $valor = $Matches[2] -replace '%USERPROFILE%', $env:USERPROFILE
            switch ($Matches[1]) {
                "VM_IP"   { if (-not $VmIp)   { $VmIp   = $valor } }
                "VM_USER" { if (-not $VmUser) { $VmUser = $valor } }
                "VM_KEY"  { if (-not $VmKey)  { $VmKey  = $valor } }
            }
        }
    }
}

if (-not $VmIp -or $VmIp -like "CAMBIAR*") {
    throw "Falta la IP de la VM. Editala en vm-config.cmd o pasala con -VmIp"
}
if (-not $VmUser) { $VmUser = "ubuntu" }
if (-not $VmKey)  { $VmKey  = "$env:USERPROFILE\.ssh\dian.key" }
$origen = $PSScriptRoot
$destino = "$VmUser@$VmIp"
$stage = "~/dian-stage"

if (-not (Test-Path $VmKey)) { throw "No encuentro la llave SSH en $VmKey" }

Write-Host "==> preparando carpeta en la VM" -ForegroundColor Cyan
ssh -i $VmKey -o StrictHostKeyChecking=accept-new $destino "rm -rf $stage && mkdir -p $stage/vm"

Write-Host "==> copiando archivos" -ForegroundColor Cyan
$archivos = @(
    "check_dian_v5.py",
    "requirements.txt",
    "LEEME.md",
    "COMANDOS.md"
) | Where-Object { Test-Path (Join-Path $origen $_) }

foreach ($f in $archivos) {
    scp -i $VmKey (Join-Path $origen $f) "${destino}:$stage/"
    if ($LASTEXITCODE -ne 0) { throw "fallo copiando $f" }
}

$archivosVm = @("install.sh", "resumen.sh", "dian-monitor.service", "dian-monitor.timer", "dian.env.example")
foreach ($f in $archivosVm) {
    scp -i $VmKey (Join-Path $origen "vm\$f") "${destino}:$stage/vm/"
    if ($LASTEXITCODE -ne 0) { throw "fallo copiando vm\$f" }
}

# El .env real (con la clave) solo si existe; si no, el instalador usa el ejemplo.
$envReal = Join-Path $origen "vm\dian.env"
if (Test-Path $envReal) {
    scp -i $VmKey $envReal "${destino}:$stage/vm/"
}

# Windows guarda saltos de linea CRLF; bash no traga eso en los scripts.
ssh -i $VmKey $destino "sed -i 's/\r`$//' $stage/vm/*.sh $stage/vm/*.service $stage/vm/*.timer $stage/vm/*.env* $stage/*.py 2>/dev/null; chmod +x $stage/vm/install.sh"

if ($SoloSubir) {
    Write-Host "`nArchivos en $stage. Para instalar:" -ForegroundColor Yellow
    Write-Host "  ssh -i `"$VmKey`" $destino 'sudo bash $stage/vm/install.sh'"
    exit 0
}

Write-Host "==> instalando en la VM (esto baja Chromium, tarda unos minutos)" -ForegroundColor Cyan
ssh -i $VmKey $destino "sudo bash $stage/vm/install.sh"
if ($LASTEXITCODE -ne 0) { throw "el instalador fallo" }

Write-Host "`nListo." -ForegroundColor Green
