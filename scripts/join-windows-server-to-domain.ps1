# Domain Join Script for Windows Server
# Joins Windows Server to northvalley.local domain
# Run as Administrator

param(
    [string]$DomainName = "northvalley.local",
    [string]$DomainController = "192.168.88.250",
    [string]$DomainAdmin = "NORTHVALLEY\Administrator",
    [string]$DomainAdminPassword = "NorthValley2024!",
    [string]$NewComputerName = $null  # Leave null to keep current name
)

Write-Host "=== Windows Server Domain Join Script ===" -ForegroundColor Cyan
Write-Host "Domain: $DomainName" -ForegroundColor Yellow
Write-Host "Domain Controller: $DomainController" -ForegroundColor Yellow
Write-Host ""

# Check if already domain-joined
$ComputerSystem = Get-WmiObject Win32_ComputerSystem
if ($ComputerSystem.PartOfDomain) {
    Write-Host "WARNING: Computer is already joined to domain: $($ComputerSystem.Domain)" -ForegroundColor Red
    Write-Host "Current computer name: $($ComputerSystem.Name)" -ForegroundColor Yellow
    $response = Read-Host "Do you want to rejoin? (yes/no)"
    if ($response -ne "yes") {
        Write-Host "Exiting..." -ForegroundColor Yellow
        exit 0
    }
}

# Step 1: Configure DNS to point to Domain Controller
Write-Host "[1/5] Configuring DNS..." -ForegroundColor Green
try {
    $Adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
    foreach ($Adapter in $Adapters) {
        Write-Host "  Setting DNS on $($Adapter.Name) to $DomainController" -ForegroundColor Gray
        Set-DnsClientServerAddress -InterfaceIndex $Adapter.ifIndex -ServerAddresses $DomainController
    }
    Write-Host "  DNS configured successfully" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Failed to configure DNS: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Step 2: Test connectivity to Domain Controller
Write-Host "[2/5] Testing connectivity to Domain Controller..." -ForegroundColor Green
try {
    $PingResult = Test-Connection -ComputerName $DomainController -Count 2 -Quiet
    if ($PingResult) {
        Write-Host "  Domain Controller is reachable" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: Cannot ping Domain Controller" -ForegroundColor Yellow
        Write-Host "  Continuing anyway..." -ForegroundColor Yellow
    }
} catch {
    Write-Host "  WARNING: Connectivity test failed: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Step 3: Test DNS resolution
Write-Host "[3/5] Testing DNS resolution..." -ForegroundColor Green
try {
    $DCName = "NVDC01.$DomainName"
    $DNSResult = Resolve-DnsName -Name $DCName -ErrorAction SilentlyContinue
    if ($DNSResult) {
        Write-Host "  DNS resolution successful: $DCName -> $($DNSResult[0].IPAddress)" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: DNS resolution failed for $DCName" -ForegroundColor Yellow
        Write-Host "  Using IP address directly: $DomainController" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  WARNING: DNS test failed: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Step 4: Create credential object
Write-Host "[4/5] Preparing domain credentials..." -ForegroundColor Green
try {
    $SecurePassword = ConvertTo-SecureString $DomainAdminPassword -AsPlainText -Force
    $Credential = New-Object System.Management.Automation.PSCredential($DomainAdmin, $SecurePassword)
    Write-Host "  Credentials prepared" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Failed to create credential object: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Step 5: Join domain
Write-Host "[5/5] Joining domain..." -ForegroundColor Green
try {
    $JoinParams = @{
        DomainName = $DomainName
        Credential = $Credential
        Force = $true
        ErrorAction = "Stop"
    }
    
    if ($NewComputerName) {
        $JoinParams['NewName'] = $NewComputerName
        Write-Host "  Joining domain as: $NewComputerName" -ForegroundColor Yellow
    } else {
        Write-Host "  Joining domain with current name: $($ComputerSystem.Name)" -ForegroundColor Yellow
    }
    
    Add-Computer @JoinParams
    Write-Host "  Domain join successful!" -ForegroundColor Green
    Write-Host ""
    Write-Host "=== SUCCESS ===" -ForegroundColor Green
    Write-Host "Computer will restart in 30 seconds to complete domain join." -ForegroundColor Yellow
    Write-Host "After restart, you can log in with domain credentials:" -ForegroundColor Yellow
    Write-Host "  Username: $DomainAdmin" -ForegroundColor Cyan
    Write-Host "  Password: [your password]" -ForegroundColor Cyan
    Write-Host ""
    
    # Restart computer
    $Restart = Read-Host "Restart now? (yes/no)"
    if ($Restart -eq "yes") {
        Write-Host "Restarting in 10 seconds..." -ForegroundColor Yellow
        Start-Sleep -Seconds 10
        Restart-Computer -Force
    } else {
        Write-Host "Please restart manually to complete domain join." -ForegroundColor Yellow
    }
    
} catch {
    Write-Host "  ERROR: Domain join failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Common issues:" -ForegroundColor Yellow
    Write-Host "  1. Cannot reach Domain Controller - check network/DNS" -ForegroundColor Gray
    Write-Host "  2. Invalid credentials - verify username/password" -ForegroundColor Gray
    Write-Host "  3. Computer name conflict - try different name" -ForegroundColor Gray
    Write-Host "  4. Domain controller not responding - check DC status" -ForegroundColor Gray
    exit 1
}
