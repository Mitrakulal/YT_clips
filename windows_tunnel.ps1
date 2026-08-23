# ============================================================
#  YT_clips + Mattrlabs — Windows port-forward to Mac Mini
#  Run: double-click this file (needs OpenSSH, built into Win10/11)
# ============================================================

$MAC_USER = "inunity"
$MAC_IP   = "192.168.250.209"   # Mac mini LAN IP. Update if DHCP changes it.

Write-Host "=== Port-forward: $MAC_USER@$MAC_IP ===" -ForegroundColor Cyan
Write-Host "First time only: accept the fingerprint prompt + enter your Mac password."
Write-Host "Window stays open = tunnels alive. Close window or Ctrl+C = tunnels dead.`n"

# ---- Tunnels (Windows-port -> service on Mac's localhost) ----
ssh -N `
  -o ServerAliveInterval=30 `
  -o ServerAliveCountMax=3 `
  -o ExitOnForwardFailure=yes `
  -L 3001:127.0.0.1:3001 `   # chat router UI
  -L 8000:127.0.0.1:8000 `   # Mattrlabs RAG API
  -L 8080:127.0.0.1:8080 `   # llama.cpp (gemma models)
  -L 8787:127.0.0.1:8787 `   # gemma gateway (API key protected)
  -L 11434:127.0.0.1:11434 ` # Ollama API
  -L 8765:127.0.0.1:8765 `   # YT_clips studio dashboard
  "$MAC_USER@$MAC_IP"

if ($LASTEXITCODE -ne 0) {
  Write-Host "`nTunnel dropped or could not connect." -ForegroundColor Red
  Write-Host "- Is the Mac on and awake?"
  Write-Host "- Is Remote Login ON? (Mac: System Settings > General > Sharing > Remote Login)"
  Write-Host "- Is the IP still $MAC_IP? (check on Mac: ipconfig getifaddr en0)"
}
