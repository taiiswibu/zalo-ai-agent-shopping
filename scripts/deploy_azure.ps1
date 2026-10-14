param(
    [Parameter(Mandatory=$true)][string]$Image,
    [string]$Region = "southeastasia",
    [string]$ResourceGroup = "rg-zalo-agent-student",
    [string]$Environment = "cae-zalo-agent-student",
    [string]$AppName = "zalo-agent-free-demo",
    [switch]$Deploy
)
$ErrorActionPreference = "Stop"
Write-Host "=== Azure for Students: PRE-FLIGHT ==="
$sub = az account show --query '{name:name,id:id}' -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Hãy chạy az login trước." }
Write-Host "Subscription: $($sub.name), ID: $($sub.id)"
if ($sub.name -notmatch 'Student') { throw "Không phải subscription Azure for Students. Dừng để tránh thanh toán." }
if ($Image -notmatch '^ghcr\.io/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+:[a-zA-Z0-9._-]+$') { throw "Image phải là GHCR public: ghcr.io/owner/repo:latest" }
foreach ($key in @('DATABASE_URL','BOT_TOKEN','WEBHOOK_SECRET','ADMIN_TOKEN')) {
    $value = [Environment]::GetEnvironmentVariable($key)
    if (-not $value -or $value.StartsWith('replace-')) { throw "Thiếu biến môi trường $key trong PowerShell" }
}
if ($env:DATABASE_URL -notmatch '^postgresql\+psycopg://') { throw "Cloud cần PostgreSQL external, không dùng SQLite" }
if ($env:WEBHOOK_SECRET.Length -lt 8 -or $env:ADMIN_TOKEN.Length -lt 16) { throw "Secret quá ngắn" }
Write-Host "Region=$Region, RG=$ResourceGroup, Env=$Environment, App=$AppName"
Write-Host "Giới hạn: 0-1 replicas, 0.25 vCPU, 0.5 GiB; logs destination=none."
Write-Warning "Free tier không bảo đảm $0 nếu vượt quota; hãy kiểm tra credit và ngân sách."
if (-not $Deploy) {
    Write-Host "DRY RUN: chưa tạo tài nguyên. Thêm -Deploy sau khi kiểm tra Azure credit."
    exit 0
}
az extension add --name containerapp --upgrade
if ($LASTEXITCODE -ne 0) { throw "Cannot install containerapp extension" }
az group create -n $ResourceGroup -l $Region --tags project=zalo-agent environment=student
if ($LASTEXITCODE -ne 0) { throw "Resource group failed" }
az containerapp env create -n $Environment -g $ResourceGroup -l $Region --logs-destination none
if ($LASTEXITCODE -ne 0) { throw "Container Apps environment failed" }
az containerapp create -n $AppName -g $ResourceGroup --environment $Environment --image $Image `
  --ingress external --target-port 8000 --transport auto `
  --cpu 0.25 --memory 0.5Gi --min-replicas 0 --max-replicas 1 `
  --secrets "db-url=$env:DATABASE_URL" "bot-token=$env:BOT_TOKEN" "webhook-secret=$env:WEBHOOK_SECRET" "admin-token=$env:ADMIN_TOKEN" `
  --env-vars "DATABASE_URL=secretref:db-url" "BOT_TOKEN=secretref:bot-token" "WEBHOOK_SECRET=secretref:webhook-secret" "ADMIN_TOKEN=secretref:admin-token" "BOT_MODE=live" "LLM_PROVIDER=none" "SEED_DEMO=true" "CONTAINER_APP_NAME=$AppName"
if ($LASTEXITCODE -ne 0) { throw "Container App create failed" }
$fqdn = az containerapp show -n $AppName -g $ResourceGroup --query 'properties.configuration.ingress.fqdn' -o tsv
Write-Host "Deployment created. Test: https://$fqdn/healthz"
Write-Host "Zalo webhook: https://$fqdn/webhooks/zalo"
Write-Host "To delete cloud resources: az group delete -n $ResourceGroup --yes --no-wait"
