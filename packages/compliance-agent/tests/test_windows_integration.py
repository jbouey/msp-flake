"""
Windows Server Integration Tests.

Run these tests against a real Windows Server to validate runbooks.

Setup:
    1. Start Windows Server VM (see docs/WINDOWS_TEST_SETUP.md)
    2. Configure WinRM on the VM
    3. Set environment variables:
       export WIN_TEST_HOST="192.168.56.10"
       export WIN_TEST_USER="Administrator"
       export WIN_TEST_PASS="your_password"
    4. Run: python tests/test_windows_integration.py

Or run with pytest:
    pytest tests/test_windows_integration.py -v -s
"""

import asyncio
import os
import socket
import sys
from datetime import datetime

import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def is_windows_vm_available() -> bool:
    """Check if Windows VM is reachable (quick socket check)."""
    host_env = os.environ.get('WIN_TEST_HOST', '192.168.56.10')

    # Parse host:port format (e.g., "127.0.0.1:55985")
    if ':' in host_env:
        host, port_str = host_env.rsplit(':', 1)
        port = int(port_str)
    else:
        host = host_env
        port = 5985  # WinRM HTTP port default

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)  # 2 second timeout
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except (socket.error, socket.timeout):
        return False


# Skip all tests in this module if Windows VM is not available
pytestmark = pytest.mark.skipif(
    not is_windows_vm_available(),
    reason="Windows VM not available (set WIN_TEST_HOST or start VM)"
)


def get_test_config():
    """Get Windows test configuration from environment.

    Returns:
        tuple: (host, port, user, password)
    """
    host_env = os.environ.get('WIN_TEST_HOST', '192.168.56.10')
    user = os.environ.get('WIN_TEST_USER', 'vagrant')
    password = os.environ.get('WIN_TEST_PASS', 'vagrant')

    # Parse host:port format (e.g., "127.0.0.1:55985")
    if ':' in host_env:
        host, port_str = host_env.rsplit(':', 1)
        port = int(port_str)
    else:
        host = host_env
        port = 5985  # WinRM HTTP port default

    return host, port, user, password


def check_winrm_installed():
    """Check if pywinrm is installed."""
    try:
        import winrm
        return True
    except ImportError:
        print("ERROR: pywinrm not installed")
        print("Run: pip install pywinrm")
        return False


async def test_basic_connection():
    """Test basic WinRM connection."""
    from compliance_agent.runbooks.windows.executor import WindowsExecutor, WindowsTarget

    host, port, user, password = get_test_config()
    print(f"\n[TEST] Basic Connection to {host}:{port}")
    print("-" * 50)

    target = WindowsTarget(
        hostname=host,
        port=port,
        username=user,
        password=password,
        use_ssl=False,
        transport='ntlm'
    )

    executor = WindowsExecutor([target])

    # Simple command
    result = await executor.execute_script(
        target,
        "$env:COMPUTERNAME",
        timeout=30
    )

    if result.success:
        hostname = result.output.get('std_out', '').strip()
        print(f"  ✓ Connected to: {hostname}")
        print(f"  ✓ Duration: {result.duration_seconds:.2f}s")
        return True
    else:
        print(f"  ✗ Failed: {result.error}")
        return False


async def test_health_check():
    """Test comprehensive health check."""
    from compliance_agent.runbooks.windows.executor import WindowsExecutor, WindowsTarget

    host, port, user, password = get_test_config()
    print(f"\n[TEST] Health Check")
    print("-" * 50)

    target = WindowsTarget(
        hostname=host,
        port=port,
        username=user,
        password=password,
        use_ssl=False,
        transport='ntlm'
    )

    executor = WindowsExecutor([target])
    health = await executor.check_target_health(target)

    if health.get('Healthy'):
        print(f"  ✓ Hostname: {health.get('Hostname')}")
        print(f"  ✓ OS: {health.get('OSVersion')}")
        print(f"  ✓ Uptime: {health.get('Uptime', 0):.1f} hours")
        print(f"  ✓ Last Boot: {health.get('LastBoot')}")
        return True
    else:
        print(f"  ✗ Health check failed: {health.get('Error')}")
        return False


async def _run_runbook_detection(runbook_id: str):
    """Run detection phase of a runbook (helper function)."""
    from compliance_agent.runbooks.windows.executor import WindowsExecutor, WindowsTarget
    from compliance_agent.runbooks.windows.runbooks import get_runbook

    host, port, user, password = get_test_config()

    runbook = get_runbook(runbook_id)
    if not runbook:
        print(f"  ✗ Runbook not found: {runbook_id}")
        return False

    print(f"\n[TEST] {runbook.name} (Detection)")
    print(f"  HIPAA: {', '.join(runbook.hipaa_controls)}")
    print("-" * 50)

    target = WindowsTarget(
        hostname=host,
        port=port,
        username=user,
        password=password,
        use_ssl=False,
        transport='ntlm'
    )

    executor = WindowsExecutor([target])

    results = await executor.run_runbook(
        target,
        runbook_id,
        phases=["detect"]
    )

    if not results:
        print(f"  ✗ No results returned")
        return False

    result = results[0]

    if result.success:
        parsed = result.output.get('parsed', {})
        drifted = parsed.get('Drifted', 'Unknown')

        print(f"  ✓ Detection completed in {result.duration_seconds:.2f}s")
        print(f"  ✓ Drifted: {drifted}")

        # Print detection details
        if parsed:
            for key, value in parsed.items():
                if key != 'Drifted':
                    print(f"    - {key}: {value}")

        return True
    else:
        print(f"  ✗ Detection failed: {result.error}")
        if result.output.get('std_err'):
            print(f"    stderr: {result.output['std_err'][:200]}")
        return False


async def test_all_detections():
    """Run detection for all runbooks."""
    from compliance_agent.runbooks.windows.runbooks import list_runbooks

    print("\n" + "=" * 60)
    print("RUNNING ALL RUNBOOK DETECTIONS")
    print("=" * 60)

    runbooks = list_runbooks()
    results = {}

    for rb_info in runbooks:
        rb_id = rb_info['id']  # Extract ID from runbook dict
        try:
            success = await _run_runbook_detection(rb_id)
            results[rb_id] = success
        except Exception as e:
            print(f"  ✗ Exception: {e}")
            results[rb_id] = False

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for rb_id, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"  {status}: {rb_id}")

    print(f"\nTotal: {passed}/{total} passed")

    return passed == total


async def run_interactive_test():
    """Interactive test menu."""
    print("\n" + "=" * 60)
    print("WINDOWS SERVER COMPLIANCE RUNBOOK TESTER")
    print("=" * 60)

    host, port, user, password = get_test_config()
    print(f"\nTarget: {user}@{host}:{port}")
    print(f"Password: {'*' * len(password)}")

    if not check_winrm_installed():
        return

    print("\nRunning tests...\n")

    # Test 1: Basic connection
    if not await test_basic_connection():
        print("\n✗ Basic connection failed. Check:")
        print("  - VM is running")
        print("  - WinRM is configured")
        print("  - Firewall allows port 5985")
        print("  - Credentials are correct")
        return

    # Test 2: Health check
    if not await test_health_check():
        print("\n✗ Health check failed")
        return

    # Test 3: All runbook detections
    await test_all_detections()


def main():
    """Main entry point."""
    asyncio.run(run_interactive_test())


if __name__ == "__main__":
    main()
