# iso/local-status.nix
# Local status page served by nginx on port 80
# Provides appliance health info accessible on local network

{ config, pkgs, lib, ... }:

let
  # Status page HTML template
  statusPageHtml = pkgs.writeText "status.html" ''
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <meta http-equiv="refresh" content="30">
      <title>MSP Compliance Appliance</title>
      <style>
        :root {
          --bg: #0a0a0a;
          --surface: #1a1a1a;
          --border: #333;
          --text: #e0e0e0;
          --text-muted: #888;
          --green: #22c55e;
          --yellow: #eab308;
          --red: #ef4444;
          --blue: #3b82f6;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: var(--bg);
          color: var(--text);
          min-height: 100vh;
          padding: 2rem;
        }
        .container { max-width: 800px; margin: 0 auto; }
        .header {
          display: flex;
          align-items: center;
          gap: 1rem;
          margin-bottom: 2rem;
          padding-bottom: 1rem;
          border-bottom: 1px solid var(--border);
        }
        .logo {
          width: 48px;
          height: 48px;
          background: var(--blue);
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: bold;
          font-size: 1.5rem;
        }
        h1 { font-size: 1.5rem; font-weight: 600; }
        .subtitle { color: var(--text-muted); font-size: 0.875rem; }
        .card {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 1.5rem;
          margin-bottom: 1rem;
        }
        .card-title {
          font-size: 0.875rem;
          color: var(--text-muted);
          text-transform: uppercase;
          letter-spacing: 0.05em;
          margin-bottom: 1rem;
        }
        .status-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 0.75rem 0;
          border-bottom: 1px solid var(--border);
        }
        .status-row:last-child { border-bottom: none; }
        .status-label { font-weight: 500; }
        .status-value { color: var(--text-muted); }
        .badge {
          display: inline-flex;
          align-items: center;
          gap: 0.5rem;
          padding: 0.25rem 0.75rem;
          border-radius: 9999px;
          font-size: 0.875rem;
          font-weight: 500;
        }
        .badge-green { background: rgba(34, 197, 94, 0.2); color: var(--green); }
        .badge-yellow { background: rgba(234, 179, 8, 0.2); color: var(--yellow); }
        .badge-red { background: rgba(239, 68, 68, 0.2); color: var(--red); }
        .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: currentColor;
        }
        .portal-link {
          display: block;
          text-align: center;
          padding: 1rem;
          background: var(--blue);
          color: white;
          text-decoration: none;
          border-radius: 8px;
          font-weight: 500;
          margin-top: 2rem;
        }
        .portal-link:hover { opacity: 0.9; }
        .footer {
          text-align: center;
          color: var(--text-muted);
          font-size: 0.75rem;
          margin-top: 2rem;
        }
        #loading { text-align: center; padding: 2rem; color: var(--text-muted); }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <div class="logo">M</div>
          <div>
            <h1>Compliance Appliance</h1>
            <p class="subtitle" id="site-id">Loading...</p>
          </div>
        </div>

        <div id="loading">Loading status...</div>
        <div id="content" style="display: none;">
          <div class="card">
            <div class="card-title">Appliance Status</div>
            <div class="status-row">
              <span class="status-label">Status</span>
              <span id="status-badge" class="badge badge-green"><span class="dot"></span> Online</span>
            </div>
            <div class="status-row">
              <span class="status-label">Agent Version</span>
              <span class="status-value" id="agent-version">-</span>
            </div>
            <div class="status-row">
              <span class="status-label">Uptime</span>
              <span class="status-value" id="uptime">-</span>
            </div>
            <div class="status-row">
              <span class="status-label">Last Check-in</span>
              <span class="status-value" id="last-checkin">-</span>
            </div>
          </div>

          <div class="card">
            <div class="card-title">Compliance Summary</div>
            <div class="status-row">
              <span class="status-label">Controls Passing</span>
              <span id="controls-passing" class="badge badge-green">-/8</span>
            </div>
            <div class="status-row">
              <span class="status-label">Endpoint Drift</span>
              <span id="ctrl-endpoint" class="badge badge-green">-</span>
            </div>
            <div class="status-row">
              <span class="status-label">Patch Status</span>
              <span id="ctrl-patch" class="badge badge-green">-</span>
            </div>
            <div class="status-row">
              <span class="status-label">Backup Status</span>
              <span id="ctrl-backup" class="badge badge-green">-</span>
            </div>
            <div class="status-row">
              <span class="status-label">MFA Coverage</span>
              <span id="ctrl-mfa" class="badge badge-green">-</span>
            </div>
          </div>

          <div class="card">
            <div class="card-title">System Health</div>
            <div class="status-row">
              <span class="status-label">Memory Usage</span>
              <span class="status-value" id="memory">-</span>
            </div>
            <div class="status-row">
              <span class="status-label">Disk Usage</span>
              <span class="status-value" id="disk">-</span>
            </div>
            <div class="status-row">
              <span class="status-label">Time Sync</span>
              <span id="time-sync" class="badge badge-green">-</span>
            </div>
          </div>

          <a href="#" id="portal-link" class="portal-link">View Full Dashboard in Portal</a>
        </div>

        <div class="footer">
          OsirisCare Compliance Platform &bull; <span id="timestamp">-</span>
        </div>
      </div>

      <script>
        async function loadStatus() {
          try {
            const res = await fetch('/api/status');
            const data = await res.json();

            document.getElementById('loading').style.display = 'none';
            document.getElementById('content').style.display = 'block';

            document.getElementById('site-id').textContent = data.site_id || 'Unconfigured';
            document.getElementById('agent-version').textContent = data.agent_version || '-';
            document.getElementById('uptime').textContent = formatUptime(data.uptime_seconds);
            document.getElementById('last-checkin').textContent = data.last_checkin || '-';

            // Controls
            const passing = data.controls_passing || 0;
            const total = data.controls_total || 8;
            document.getElementById('controls-passing').textContent = passing + '/' + total;
            document.getElementById('controls-passing').className = 'badge ' +
              (passing === total ? 'badge-green' : passing >= 6 ? 'badge-yellow' : 'badge-red');

            setControlBadge('ctrl-endpoint', data.controls?.endpoint_drift);
            setControlBadge('ctrl-patch', data.controls?.patch_freshness);
            setControlBadge('ctrl-backup', data.controls?.backup_success);
            setControlBadge('ctrl-mfa', data.controls?.mfa_coverage);

            // System
            document.getElementById('memory').textContent = data.memory_usage || '-';
            document.getElementById('disk').textContent = data.disk_usage || '-';
            setControlBadge('time-sync', data.time_synced ? 'pass' : 'fail');

            // Portal link
            if (data.portal_url) {
              document.getElementById('portal-link').href = data.portal_url;
            }

            document.getElementById('timestamp').textContent = new Date().toLocaleString();
          } catch (e) {
            document.getElementById('loading').textContent = 'Error loading status: ' + e.message;
          }
        }

        function setControlBadge(id, status) {
          const el = document.getElementById(id);
          if (!el) return;
          if (status === 'pass') {
            el.textContent = 'Pass';
            el.className = 'badge badge-green';
          } else if (status === 'warn') {
            el.textContent = 'Warning';
            el.className = 'badge badge-yellow';
          } else {
            el.textContent = 'Fail';
            el.className = 'badge badge-red';
          }
        }

        function formatUptime(seconds) {
          if (!seconds) return '-';
          const days = Math.floor(seconds / 86400);
          const hours = Math.floor((seconds % 86400) / 3600);
          const mins = Math.floor((seconds % 3600) / 60);
          if (days > 0) return days + 'd ' + hours + 'h';
          if (hours > 0) return hours + 'h ' + mins + 'm';
          return mins + 'm';
        }

        loadStatus();
        setInterval(loadStatus, 30000);
      </script>
    </body>
    </html>
  '';

  # Status API script
  statusApiScript = pkgs.writeScript "status-api.py" ''
    #!${pkgs.python311}/bin/python3
    """
    Simple status API for the appliance local status page.
    Reads from compliance agent state and returns JSON.
    """

    import json
    import os
    import subprocess
    import time
    from datetime import datetime
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from pathlib import Path

    CONFIG_PATH = Path("/var/lib/msp/config.yaml")
    STATE_PATH = Path("/var/lib/compliance-agent/state.json")
    EVIDENCE_PATH = Path("/var/lib/compliance-agent/evidence")


    def get_uptime():
        """Get system uptime in seconds."""
        try:
            with open("/proc/uptime") as f:
                return int(float(f.read().split()[0]))
        except:
            return 0


    def get_memory_usage():
        """Get memory usage percentage."""
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            mem = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
            total = mem.get("MemTotal", 1)
            available = mem.get("MemAvailable", 0)
            used_pct = int((1 - available / total) * 100)
            return f"{used_pct}%"
        except:
            return "-"


    def get_disk_usage():
        """Get disk usage for /var/lib/msp."""
        try:
            result = subprocess.run(
                ["df", "-h", "/var/lib/msp"],
                capture_output=True,
                text=True
            )
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 5:
                    return parts[4]
        except:
            pass
        return "-"


    def check_time_sync():
        """Check if time is synchronized."""
        try:
            result = subprocess.run(
                ["chronyc", "tracking"],
                capture_output=True,
                text=True
            )
            return "Normal" in result.stdout
        except:
            return False


    def get_site_id():
        """Get site ID from config."""
        try:
            import yaml
            with open(CONFIG_PATH) as f:
                config = yaml.safe_load(f)
            return config.get("site_id", "unconfigured")
        except:
            return "unconfigured"


    def get_portal_url():
        """Get portal URL from config."""
        try:
            import yaml
            with open(CONFIG_PATH) as f:
                config = yaml.safe_load(f)
            site_id = config.get("site_id")
            token = config.get("portal_token")
            if site_id and token:
                return f"https://portal.osiriscare.net/site/{site_id}?token={token}"
        except:
            pass
        return None


    def get_agent_state():
        """Get compliance agent state."""
        try:
            if STATE_PATH.exists():
                with open(STATE_PATH) as f:
                    return json.load(f)
        except:
            pass
        return {}


    def get_latest_evidence():
        """Get latest evidence bundle results."""
        try:
            bundles = sorted(EVIDENCE_PATH.glob("*/bundle.json"), reverse=True)
            if bundles:
                with open(bundles[0]) as f:
                    return json.load(f)
        except:
            pass
        return {}


    def get_status():
        """Build status response."""
        state = get_agent_state()
        evidence = get_latest_evidence()

        controls = {}
        controls_passing = 0
        control_names = [
            "endpoint_drift", "patch_freshness", "backup_success", "mfa_coverage",
            "privileged_access", "git_protections", "secrets_hygiene", "storage_posture"
        ]

        for ctrl in control_names:
            status = state.get("controls", {}).get(ctrl, "unknown")
            controls[ctrl] = status
            if status == "pass":
                controls_passing += 1

        return {
            "site_id": get_site_id(),
            "agent_version": state.get("version", "0.2.0"),
            "uptime_seconds": get_uptime(),
            "last_checkin": state.get("last_checkin"),
            "controls_passing": controls_passing,
            "controls_total": 8,
            "controls": controls,
            "memory_usage": get_memory_usage(),
            "disk_usage": get_disk_usage(),
            "time_synced": check_time_sync(),
            "portal_url": get_portal_url(),
        }


    class StatusHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Suppress logging

        def do_GET(self):
            if self.path == "/api/status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(json.dumps(get_status()).encode())
            else:
                self.send_response(404)
                self.end_headers()


    if __name__ == "__main__":
        server = HTTPServer(("127.0.0.1", 8081), StatusHandler)
        print("Status API running on :8081")
        server.serve_forever()
  '';

in
{
  # ============================================================================
  # Nginx - Serves status page and proxies to status API
  # ============================================================================
  services.nginx = {
    enable = true;

    # Optimize for low memory
    recommendedOptimisation = true;
    recommendedGzipSettings = true;

    virtualHosts."_" = {
      default = true;
      root = "/var/www/status";

      locations."/" = {
        index = "status.html";
        tryFiles = "$uri $uri/ /status.html";
      };

      locations."/api/status" = {
        proxyPass = "http://127.0.0.1:8081/api/status";
        extraConfig = ''
          proxy_read_timeout 5s;
          proxy_connect_timeout 2s;
        '';
      };
    };
  };

  # Create status page directory and file
  system.activationScripts.statusPage = ''
    mkdir -p /var/www/status
    cp ${statusPageHtml} /var/www/status/status.html
    chmod 644 /var/www/status/status.html
  '';

  # ============================================================================
  # Status API Service
  # ============================================================================
  systemd.services.msp-status-api = {
    description = "MSP Status API";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" "appliance-daemon.service" ];

    serviceConfig = {
      Type = "simple";
      ExecStart = "${statusApiScript}";
      Restart = "always";
      RestartSec = "5s";

      # Run as nobody - read-only access
      User = "nobody";
      Group = "nogroup";

      # Security hardening
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadOnlyPaths = [ "/var/lib/msp" "/var/lib/compliance-agent" ];
      NoNewPrivileges = true;
    };
  };
}
