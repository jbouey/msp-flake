# flake/modules/log-watcher.nix
# NixOS module for MSP Log Watcher Service
{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp-log-watcher;
  
  # Python environment with dependencies
  pythonEnv = pkgs.python311.withPackages (ps: with ps; [
    requests
    fastapi
    uvicorn
  ]);
  
  # The tailer script (embedded for simplicity, can be moved to separate file)
  tailerScript = pkgs.writeScript "infra-tailer" ''
    #!${pythonEnv}/bin/python
    import threading
    import uvicorn
    from fastapi import FastAPI
    import os, re, json, time, requests, pathlib
    import sys

    LOG_DIR = pathlib.Path("/var/log")
    MCP_URL = os.getenv("MCP_URL", "http://localhost:8000")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    ANOMALY = re.compile(r"(ERROR|CRITICAL|panic|failed|timeout)", re.I)

    print(f"[LOG-WATCHER] Starting MSP Log Watcher...")
    print(f"[LOG-WATCHER] MCP Server: {MCP_URL}")
    print(f"[LOG-WATCHER] Watching: {LOG_DIR}")
    print(f"[LOG-WATCHER] Log Level: {LOG_LEVEL}")

    app = FastAPI()

    @app.get("/status")
    def status():
        return {"ok": True, "service": "msp-log-watcher", "mcp_url": MCP_URL}

    @app.get("/health")
    def health():
        return {"status": "healthy", "watching": str(LOG_DIR)}

    # Start health endpoint in background thread
    def run_health_server():
        uvicorn.run(app, host="0.0.0.0", port=8080, log_level="error")
    
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    print("[LOG-WATCHER] Health endpoint started on :8080")

    def scan(line, current_file):
        if ANOMALY.search(line):
            payload = {
                "snippet": line.strip()[:2000],
                "meta": {
                    "hostname": os.uname()[1],
                    "logfile": str(current_file),
                    "timestamp": time.time()
                }
            }
            try:
                if LOG_LEVEL == "DEBUG":
                    print(f"[LOG-WATCHER] Found anomaly: {line[:50]}...")
                
                r = requests.post(f"{MCP_URL}/diagnose", json=payload, timeout=3)
                r.raise_for_status()
                
                response = r.json()
                action = response.get("action")
                
                if action:
                    print(f"[LOG-WATCHER] MCP suggested action: {action}")
                    remediate_payload = {"action": action, **payload}
                    r2 = requests.post(f"{MCP_URL}/remediate", json=remediate_payload, timeout=3)
                    r2.raise_for_status()
                    print(f"[LOG-WATCHER] Remediation triggered: {action}")
                    
            except requests.exceptions.ConnectionError:
                print(f"[LOG-WATCHER] Cannot connect to MCP at {MCP_URL}", file=sys.stderr)
            except requests.exceptions.Timeout:
                print(f"[LOG-WATCHER] MCP timeout", file=sys.stderr)
            except Exception as e:
                print(f"[LOG-WATCHER] Error: {e}", file=sys.stderr)

    # Track file positions to avoid re-reading
    file_positions = {}
    
    print("[LOG-WATCHER] Entering main loop...")
    while True:
        try:
            for log_file in LOG_DIR.glob("*.log"):
                try:
                    # Skip if file doesn't exist or isn't readable
                    if not log_file.exists() or not os.access(log_file, os.R_OK):
                        continue
                    
                    with log_file.open('r') as f:
                        # Get last position or go to end
                        last_pos = file_positions.get(str(log_file), 0)
                        
                        # Check if file was truncated
                        f.seek(0, os.SEEK_END)
                        file_size = f.tell()
                        
                        if file_size < last_pos:
                            # File was truncated, start from beginning
                            last_pos = 0
                        
                        f.seek(last_pos)
                        
                        # Read new lines
                        for line in f:
                            scan(line, log_file)
                        
                        # Update position
                        file_positions[str(log_file)] = f.tell()
                        
                except IOError as e:
                    if LOG_LEVEL == "DEBUG":
                        print(f"[LOG-WATCHER] Cannot read {log_file}: {e}")
                except Exception as e:
                    print(f"[LOG-WATCHER] Error processing {log_file}: {e}", file=sys.stderr)
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\n[LOG-WATCHER] Shutting down...")
            break
        except Exception as e:
            print(f"[LOG-WATCHER] Main loop error: {e}", file=sys.stderr)
            time.sleep(5)
  '';

  # Fluent Bit configuration
  fluentBitConfig = pkgs.writeText "fluent-bit.conf" ''
    [SERVICE]
       Flush        1
       Daemon       Off
       Log_Level    ${cfg.logLevel}

    [INPUT]
       Name         tail
       Path         /var/log/*.log
       Tag          syslog
       Parser       syslog

    [FILTER]
       Name         grep
       Match        *
       Regex        log  (ERROR|CRITICAL|panic|failed|timeout)

    [OUTPUT]
       Name         stdout
       Match        *
  '';

in {
  options.services.msp-log-watcher = {
    enable = mkEnableOption "MSP Log Watcher Service";
    
    mcpUrl = mkOption {
      type = types.str;
      default = "http://localhost:8000";
      description = "URL of the MCP server (e.g., http://192.168.1.100:8000)";
      example = "http://192.168.1.100:8000";
    };
    
    logLevel = mkOption {
      type = types.enum [ "DEBUG" "INFO" "WARNING" "ERROR" ];
      default = "INFO";
      description = "Log level for the watcher";
    };
    
    useFluentBit = mkOption {
      type = types.bool;
      default = false;
      description = "Use Fluent Bit for log collection (production mode)";
    };
    
    watchPaths = mkOption {
      type = types.listOf types.str;
      default = [ "/var/log" ];
      description = "Paths to watch for log files";
    };
    
    autoStart = mkOption {
      type = types.bool;
      default = true;
      description = "Automatically start the service on boot";
    };
  };

  config = mkIf cfg.enable {
    # Create systemd service
    systemd.services.msp-log-watcher = {
      description = "MSP Log Watcher for Remote Monitoring";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = mkIf cfg.autoStart [ "multi-user.target" ];
      
      environment = {
        MCP_URL = cfg.mcpUrl;
        LOG_LEVEL = cfg.logLevel;
        PYTHONUNBUFFERED = "1";
      };
      
      serviceConfig = {
        Type = "simple";
        ExecStart = if cfg.useFluentBit then
          "${pkgs.bash}/bin/bash -c '${tailerScript} & ${pkgs.fluent-bit}/bin/fluent-bit -c ${fluentBitConfig}'"
        else
          "${tailerScript}";
        
        Restart = "always";
        RestartSec = "10";
        
        # Security hardening
        DynamicUser = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectControlGroups = true;
        RestrictNamespaces = true;
        LockPersonality = true;
        MemoryDenyWriteExecute = true;
        RestrictRealtime = true;
        ProtectHome = true;
        
        # Allow reading logs
        ReadOnlyPaths = cfg.watchPaths;
        
        # Capabilities for network access
        AmbientCapabilities = "CAP_NET_BIND_SERVICE";
        
        # Logging
        StandardOutput = "journal";
        StandardError = "journal";
      };
      
      # Health check
      startLimitBurst = 3;
      startLimitIntervalSec = 60;
    };
    
    # Open firewall for health endpoint
    networking.firewall.allowedTCPPorts = [ 8080 ];
    
    # Create test log files if they don't exist (for testing)
    systemd.tmpfiles.rules = [
      "f /var/log/test.log 0644 root root - # MSP test log"
      "f /var/log/app.log 0644 root root - # MSP app log"
      "f /var/log/system.log 0644 root root - # MSP system log"
    ];
    
    # Add convenient aliases for testing
    environment.systemPackages = with pkgs; [
      (writeShellScriptBin "msp-test-error" ''
        echo "ERROR: Test error from MSP system at $(date)" | sudo tee -a /var/log/test.log
        echo "Sent test error to /var/log/test.log"
      '')
      (writeShellScriptBin "msp-test-critical" ''
        echo "CRITICAL: Database connection failed at $(date)" | sudo tee -a /var/log/app.log
        echo "Sent critical error to /var/log/app.log"
      '')
      (writeShellScriptBin "msp-watcher-status" ''
        echo "=== MSP Log Watcher Status ==="
        systemctl status msp-log-watcher --no-pager
        echo ""
        echo "=== Health Check ==="
        curl -s http://localhost:8080/status | ${pkgs.jq}/bin/jq .
      '')
      (writeShellScriptBin "msp-watcher-logs" ''
        journalctl -u msp-log-watcher -f
      '')
    ];
  };
}