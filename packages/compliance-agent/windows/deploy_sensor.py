#!/usr/bin/env python3
"""
Deploy OsirisCare Sensor to Windows servers via WinRM.

One-time deployment - sensor then runs independently.
Uses existing WinRM credentials from appliance configuration.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

try:
    import winrm
except ImportError:
    print("Error: pywinrm not installed. Run: pip install pywinrm")
    sys.exit(1)


def get_sensor_script() -> str:
    """Read the sensor PowerShell script."""
    sensor_path = Path(__file__).parent / "OsirisSensor.ps1"
    if not sensor_path.exists():
        raise FileNotFoundError(f"Sensor script not found: {sensor_path}")
    return sensor_path.read_text(encoding="utf-8")


def create_winrm_session(
    host: str,
    port: int,
    username: str,
    password: str,
    use_ssl: bool = False,
    domain: Optional[str] = None
) -> winrm.Session:
    """Create WinRM session to target host."""
    protocol = "https" if use_ssl else "http"
    endpoint = f"{protocol}://{host}:{port}/wsman"

    # Handle domain prefix
    if domain and "\\" not in username and "@" not in username:
        auth_user = f"{domain}\\{username}"
    else:
        auth_user = username

    return winrm.Session(
        endpoint,
        auth=(auth_user, password),
        transport="ntlm",
        server_cert_validation="ignore" if use_ssl else None
    )


def deploy_sensor(
    target_host: str,
    target_port: int,
    username: str,
    password: str,
    appliance_ip: str,
    appliance_port: int = 8080,
    use_ssl: bool = False,
    domain: Optional[str] = None,
    dry_run: bool = False
) -> bool:
    """Deploy sensor to a Windows server."""

    print(f"Deploying sensor to {target_host}...")

    if dry_run:
        print("  [DRY RUN] Would deploy sensor")
        return True

    # Read sensor script
    try:
        sensor_script = get_sensor_script()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return False

    # Connect via WinRM
    try:
        session = create_winrm_session(
            target_host, target_port, username, password, use_ssl, domain
        )
    except Exception as e:
        print(f"Error connecting to {target_host}: {e}")
        return False

    # 1. Create directory
    print("  Creating C:\\OsirisCare directory...")
    result = session.run_ps(
        'New-Item -Path "C:\\OsirisCare" -ItemType Directory -Force'
    )
    if result.status_code != 0:
        print(f"  Error: Failed to create directory: {result.std_err.decode()}")
        return False

    # 2. Write sensor script using here-string
    print("  Writing sensor script...")

    # Split script into chunks to avoid command line limits
    script_lines = sensor_script.split('\n')
    chunk_size = 50
    chunks = [script_lines[i:i + chunk_size]
              for i in range(0, len(script_lines), chunk_size)]

    # Clear file first
    result = session.run_ps(
        'if (Test-Path "C:\\OsirisCare\\OsirisSensor.ps1") { '
        'Remove-Item "C:\\OsirisCare\\OsirisSensor.ps1" -Force }'
    )

    for i, chunk in enumerate(chunks):
        chunk_text = '\n'.join(chunk)
        # Escape for PowerShell
        chunk_text = chunk_text.replace("'", "''")
        chunk_text = chunk_text.replace("$", "`$")

        write_cmd = f"""
        @'
{chunk_text}
'@ | Add-Content -Path 'C:\\OsirisCare\\OsirisSensor.ps1' -Encoding UTF8
        """

        result = session.run_ps(write_cmd)
        if result.status_code != 0:
            # Try alternative method
            write_cmd2 = f"""
            [System.IO.File]::AppendAllText(
                'C:\\OsirisCare\\OsirisSensor.ps1',
                @'
{chunk_text}
'@ + "`n",
                [System.Text.Encoding]::UTF8
            )
            """
            result = session.run_ps(write_cmd2)
            if result.status_code != 0:
                print(f"  Error: Failed to write script chunk {i}: {result.std_err.decode()}")
                return False

        print(f"  Progress: {min((i + 1) * chunk_size, len(script_lines))}/{len(script_lines)} lines")

    # 3. Create scheduled task
    print("  Creating scheduled task...")
    task_cmd = f'''
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\\OsirisCare\\OsirisSensor.ps1 -ApplianceIP {appliance_ip} -AppliancePort {appliance_port}"

    $trigger = New-ScheduledTaskTrigger -AtStartup

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -RestartCount 999 `
        -ExecutionTimeLimit (New-TimeSpan -Days 365) `
        -StartWhenAvailable

    $principal = New-ScheduledTaskPrincipal `
        -UserId "SYSTEM" `
        -LogonType ServiceAccount `
        -RunLevel Highest

    # Remove existing task if present
    Unregister-ScheduledTask -TaskName "OsirisCare Sensor" -Confirm:$false -ErrorAction SilentlyContinue

    # Register new task
    Register-ScheduledTask `
        -TaskName "OsirisCare Sensor" `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force

    # Start immediately
    Start-ScheduledTask -TaskName "OsirisCare Sensor"

    # Return status
    $task = Get-ScheduledTask -TaskName "OsirisCare Sensor"
    $task.State
    '''

    result = session.run_ps(task_cmd)
    if result.status_code != 0:
        print(f"  Error: Failed to create task: {result.std_err.decode()}")
        return False

    task_state = result.std_out.decode().strip()
    print(f"  Task state: {task_state}")

    print(f"✓ Sensor deployed to {target_host}")
    print(f"  Script: C:\\OsirisCare\\OsirisSensor.ps1")
    print(f"  Task: OsirisCare Sensor (Running as SYSTEM)")
    print(f"  Reporting to: http://{appliance_ip}:{appliance_port}")

    return True


def remove_sensor(
    target_host: str,
    target_port: int,
    username: str,
    password: str,
    use_ssl: bool = False,
    domain: Optional[str] = None,
    dry_run: bool = False
) -> bool:
    """Remove sensor from a Windows server."""

    print(f"Removing sensor from {target_host}...")

    if dry_run:
        print("  [DRY RUN] Would remove sensor")
        return True

    try:
        session = create_winrm_session(
            target_host, target_port, username, password, use_ssl, domain
        )
    except Exception as e:
        print(f"Error connecting to {target_host}: {e}")
        return False

    # Stop and remove task
    print("  Stopping and removing scheduled task...")
    result = session.run_ps('''
        Stop-ScheduledTask -TaskName "OsirisCare Sensor" -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName "OsirisCare Sensor" -Confirm:$false -ErrorAction SilentlyContinue
    ''')

    # Remove directory
    print("  Removing C:\\OsirisCare directory...")
    result = session.run_ps(
        'Remove-Item -Path "C:\\OsirisCare" -Recurse -Force -ErrorAction SilentlyContinue'
    )

    print(f"✓ Sensor removed from {target_host}")
    return True


def check_sensor_status(
    target_host: str,
    target_port: int,
    username: str,
    password: str,
    use_ssl: bool = False,
    domain: Optional[str] = None
) -> dict:
    """Check sensor status on a Windows server."""

    print(f"Checking sensor status on {target_host}...")

    try:
        session = create_winrm_session(
            target_host, target_port, username, password, use_ssl, domain
        )
    except Exception as e:
        print(f"Error connecting to {target_host}: {e}")
        return {"error": str(e)}

    result = session.run_ps('''
        $task = Get-ScheduledTask -TaskName "OsirisCare Sensor" -ErrorAction SilentlyContinue
        $status = if (Test-Path "C:\\OsirisCare\\status.json") {
            Get-Content "C:\\OsirisCare\\status.json" -Raw | ConvertFrom-Json
        } else { $null }

        $log = if (Test-Path "C:\\OsirisCare\\sensor.log") {
            Get-Content "C:\\OsirisCare\\sensor.log" -Tail 5
        } else { @() }

        @{
            TaskExists = $null -ne $task
            TaskState = if ($task) { $task.State.ToString() } else { "NotFound" }
            StatusFile = $null -ne $status
            LastCheck = if ($status) { $status.Timestamp } else { $null }
            DriftCount = if ($status) { $status.DriftCount } else { 0 }
            Compliant = if ($status) { $status.Compliant } else { $null }
            SensorVersion = if ($status) { $status.SensorVersion } else { $null }
            RecentLogs = $log
        } | ConvertTo-Json -Depth 3
    ''')

    if result.status_code != 0:
        print(f"  Error: {result.std_err.decode()}")
        return {"error": result.std_err.decode()}

    try:
        status = json.loads(result.std_out.decode())
    except json.JSONDecodeError:
        status = {"raw_output": result.std_out.decode()}

    print(f"\nSensor status on {target_host}:")
    print(f"  Task: {status.get('TaskState', 'Unknown')}")
    print(f"  Status file: {'Yes' if status.get('StatusFile') else 'No'}")

    if status.get('StatusFile'):
        print(f"  Sensor version: {status.get('SensorVersion', 'Unknown')}")
        print(f"  Last check: {status.get('LastCheck', 'Unknown')}")
        print(f"  Drift count: {status.get('DriftCount', 0)}")
        print(f"  Compliant: {status.get('Compliant', 'Unknown')}")

    if status.get('RecentLogs'):
        print("\n  Recent logs:")
        for log in status.get('RecentLogs', []):
            print(f"    {log}")

    return status


def bulk_deploy(
    hosts_file: str,
    appliance_ip: str,
    appliance_port: int = 8080,
    dry_run: bool = False
) -> dict:
    """Deploy sensor to multiple hosts from a JSON file.

    Expected file format:
    [
        {"host": "DC01", "port": 5985, "username": "admin", "password": "pass", "domain": "CONTOSO"},
        ...
    ]
    """

    with open(hosts_file) as f:
        hosts = json.load(f)

    results = {"success": [], "failed": []}

    for host_config in hosts:
        try:
            success = deploy_sensor(
                target_host=host_config["host"],
                target_port=host_config.get("port", 5985),
                username=host_config["username"],
                password=host_config["password"],
                appliance_ip=appliance_ip,
                appliance_port=appliance_port,
                use_ssl=host_config.get("use_ssl", False),
                domain=host_config.get("domain"),
                dry_run=dry_run
            )

            if success:
                results["success"].append(host_config["host"])
            else:
                results["failed"].append(host_config["host"])

        except Exception as e:
            print(f"Error deploying to {host_config['host']}: {e}")
            results["failed"].append(host_config["host"])

    print(f"\n=== Bulk Deploy Summary ===")
    print(f"Success: {len(results['success'])} hosts")
    print(f"Failed: {len(results['failed'])} hosts")

    if results["failed"]:
        print(f"Failed hosts: {', '.join(results['failed'])}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Deploy OsirisCare Sensor to Windows servers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Deploy to single host
  python deploy_sensor.py deploy --host DC01 --username admin --password secret

  # Check sensor status
  python deploy_sensor.py status --host DC01 --username admin --password secret

  # Remove sensor
  python deploy_sensor.py remove --host DC01 --username admin --password secret

  # Bulk deploy from file
  python deploy_sensor.py bulk-deploy --hosts-file servers.json
        """
    )

    parser.add_argument(
        "action",
        choices=["deploy", "remove", "status", "bulk-deploy"],
        help="Action to perform"
    )

    parser.add_argument("--host", help="Target Windows host")
    parser.add_argument("--port", type=int, default=5985, help="WinRM port (default: 5985)")
    parser.add_argument("--username", help="WinRM username")
    parser.add_argument("--password", help="WinRM password")
    parser.add_argument("--domain", help="Windows domain")
    parser.add_argument("--ssl", action="store_true", help="Use HTTPS for WinRM")

    parser.add_argument(
        "--appliance-ip",
        default="192.168.88.246",
        help="NixOS appliance IP (default: 192.168.88.246)"
    )
    parser.add_argument(
        "--appliance-port",
        type=int,
        default=8080,
        help="Appliance sensor API port (default: 8080)"
    )

    parser.add_argument("--hosts-file", help="JSON file with host configurations for bulk deploy")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (don't make changes)")

    args = parser.parse_args()

    # Validate arguments
    if args.action in ["deploy", "remove", "status"]:
        if not args.host:
            parser.error(f"--host is required for {args.action}")
        if not args.username:
            parser.error(f"--username is required for {args.action}")
        if not args.password:
            parser.error(f"--password is required for {args.action}")

    if args.action == "bulk-deploy":
        if not args.hosts_file:
            parser.error("--hosts-file is required for bulk-deploy")

    # Execute action
    if args.action == "deploy":
        success = deploy_sensor(
            args.host, args.port, args.username, args.password,
            args.appliance_ip, args.appliance_port, args.ssl, args.domain,
            args.dry_run
        )
        sys.exit(0 if success else 1)

    elif args.action == "remove":
        success = remove_sensor(
            args.host, args.port, args.username, args.password,
            args.ssl, args.domain, args.dry_run
        )
        sys.exit(0 if success else 1)

    elif args.action == "status":
        status = check_sensor_status(
            args.host, args.port, args.username, args.password,
            args.ssl, args.domain
        )
        sys.exit(0 if not status.get("error") else 1)

    elif args.action == "bulk-deploy":
        results = bulk_deploy(
            args.hosts_file, args.appliance_ip, args.appliance_port, args.dry_run
        )
        sys.exit(0 if not results["failed"] else 1)


if __name__ == "__main__":
    main()
