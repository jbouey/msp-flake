#!/bin/bash

# Usage: ./start-demo.sh [--check-only]
#   --check-only: Validate prerequisites without starting Docker services

# Parse command line arguments
SKIP_DOCKER_CHECK=false
if [ "$1" == "--check-only" ]; then
    SKIP_DOCKER_CHECK=true
elif [ "$1" == "--help" ] || [ "$1" == "-h" ]; then
    echo "Usage: $0 [--check-only]"
    echo ""
    echo "Start the MSP HIPAA Compliance Platform demo environment"
    echo ""
    echo "Options:"
    echo "  --check-only    Validate prerequisites without starting services"
    echo "  -h, --help      Show this help message"
    echo ""
    exit 0
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "════════════════════════════════════════════════════════════════"
echo "  MSP HIPAA Compliance Platform - Demo Startup"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Error tracking
ERRORS=()
WARNINGS=()

# Function to add error
add_error() {
    ERRORS+=("$1")
}

# Function to add warning
add_warning() {
    WARNINGS+=("$1")
}

# Function to check command exists
check_command() {
    if ! command -v "$1" &> /dev/null; then
        add_error "$1 is not installed. Please install it first."
        return 1
    fi
    echo -e "${GREEN}✅${NC} $1 found"
    return 0
}

# Function to check file exists
check_file() {
    if [ ! -f "$1" ]; then
        add_error "Required file missing: $1"
        return 1
    fi
    echo -e "${GREEN}✅${NC} Found: $1"
    return 0
}

# Function to check directory exists or create it
ensure_directory() {
    if [ ! -d "$1" ]; then
        mkdir -p "$1" 2>/dev/null || {
            add_error "Cannot create directory: $1 (permission denied)"
            return 1
        }
        echo -e "${BLUE}📁${NC} Created: $1"
    else
        echo -e "${GREEN}✅${NC} Found: $1"
    fi
    return 0
}

echo "🔍 Checking prerequisites..."
echo ""

# Check for required commands
echo "Checking required applications..."
check_command docker
check_command docker-compose
check_command curl
check_command python3

# Check if Docker daemon is running
if [ "$SKIP_DOCKER_CHECK" = false ]; then
    if ! docker info &> /dev/null; then
        add_error "Docker daemon is not running. Please start Docker."
    fi
else
    echo -e "${YELLOW}⏭️  Skipping Docker check (--check-only mode)${NC}"
fi

# Check Python version
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]; }; then
        add_warning "Python 3.8+ recommended (found $PYTHON_VERSION)"
    fi
fi

echo ""
echo "🔍 Checking required files..."
echo ""

# Check for required configuration files
check_file "docker-compose.yml"
check_file "prometheus/prometheus.yml"
check_file "grafana/provisioning/datasources/prometheus.yml"
check_file "grafana/provisioning/dashboards/default.yml"
check_file "grafana/dashboards/msp-compliance-dashboard.json"
check_file "mcp-server/demo-cli.py"
check_file "mcp-server/metrics_exporter.py"

# Check if demo-cli.py is executable
if [ -f "mcp-server/demo-cli.py" ]; then
    if [ ! -x "mcp-server/demo-cli.py" ]; then
        chmod +x mcp-server/demo-cli.py 2>/dev/null || {
            add_warning "Could not make demo-cli.py executable"
        }
    fi
fi

echo ""
echo "🔍 Checking Python dependencies..."
echo ""

# Check for Python packages (optional but helpful)
if command -v python3 &> /dev/null; then
    if ! python3 -c "import requests" &> /dev/null; then
        add_warning "Python 'requests' module not found (demo-cli.py may not work)"
        echo -e "${YELLOW}   Install with: pip3 install requests${NC}"
    fi

    if ! python3 -c "import prometheus_client" &> /dev/null; then
        add_warning "Python 'prometheus_client' module not found (metrics exporter may not work)"
        echo -e "${YELLOW}   Install with: pip3 install prometheus-client${NC}"
    fi
fi

echo ""

# Report errors and warnings
if [ ${#ERRORS[@]} -gt 0 ]; then
    echo -e "${RED}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}  ❌ ERRORS FOUND - Cannot start demo${NC}"
    echo -e "${RED}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    for error in "${ERRORS[@]}"; do
        echo -e "${RED}  ❌ $error${NC}"
    done
    echo ""
    echo "Please fix the above errors and try again."
    exit 1
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  ⚠️  WARNINGS${NC}"
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    for warning in "${WARNINGS[@]}"; do
        echo -e "${YELLOW}  ⚠️  $warning${NC}"
    done
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

echo -e "${GREEN}✅ All prerequisites satisfied${NC}"
echo ""

# Create required directories
echo "📁 Creating required directories..."
ensure_directory "/tmp/msp-demo-state"
ensure_directory "/tmp/msp-evidence-test"
ensure_directory "prometheus"
ensure_directory "grafana/provisioning/datasources"
ensure_directory "grafana/provisioning/dashboards"
ensure_directory "grafana/dashboards"

# Create incident tracking file
if ! touch /tmp/msp-demo-incidents.json 2>/dev/null; then
    add_error "Cannot create /tmp/msp-demo-incidents.json (permission denied)"
fi

# Final error check before proceeding
if [ ${#ERRORS[@]} -gt 0 ]; then
    echo -e "${RED}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}  ❌ ERRORS FOUND - Cannot start demo${NC}"
    echo -e "${RED}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    for error in "${ERRORS[@]}"; do
        echo -e "${RED}  ❌ $error${NC}"
    done
    echo ""
    echo "Please fix the above errors and try again."
    exit 1
fi

echo -e "${GREEN}✅ Directories and files ready${NC}"
echo ""

# Exit early if check-only mode
if [ "$SKIP_DOCKER_CHECK" = true ]; then
    echo ""
    echo -e "${GREEN}✅ All checks passed (check-only mode)${NC}"
    echo ""
    echo "Run without --check-only to start services."
    exit 0
fi

# Start services
echo "🚀 Starting services..."
if ! docker-compose up -d; then
    echo -e "${RED}❌ Failed to start services with docker-compose${NC}"
    echo "Check docker-compose.yml for errors or run 'docker-compose up' without -d to see logs"
    exit 1
fi

echo ""
echo "⏳ Waiting for services to be ready..."
sleep 10

# Check service health
echo ""
echo "🔍 Checking service health..."

# Check Prometheus
if curl -s http://localhost:9091/-/healthy > /dev/null 2>&1; then
    echo "  ✅ Prometheus: http://localhost:9091"
else
    echo "  ⚠️  Prometheus: Not responding yet (may take a moment)"
fi

# Check Grafana
if curl -s http://localhost:3000/api/health > /dev/null 2>&1; then
    echo "  ✅ Grafana: http://localhost:3000"
else
    echo "  ⚠️  Grafana: Not responding yet (may take a moment)"
fi

# Check MCP Server
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "  ✅ MCP Server: http://localhost:8000"
else
    echo "  ⚠️  MCP Server: Not responding yet (may take a moment)"
fi

# Check Metrics Exporter
if curl -s http://localhost:9090/metrics > /dev/null 2>&1; then
    echo "  ✅ Metrics Exporter: http://localhost:9090/metrics"
else
    echo "  ⚠️  Metrics Exporter: Not responding yet (may take a moment)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  🎉 Demo Environment Ready!"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "📊 DASHBOARDS:"
echo "   Grafana: http://localhost:3000"
echo "   Username: admin"
echo "   Password: admin"
echo ""
echo "🔧 SERVICES:"
echo "   MCP Server: http://localhost:8000"
echo "   Prometheus: http://localhost:9091"
echo "   Metrics: http://localhost:9090/metrics"
echo ""
echo "🎮 DEMO CLI:"
echo "   Trigger incidents:"
echo "     ./mcp-server/demo-cli.py break backup"
echo "     ./mcp-server/demo-cli.py break disk"
echo "     ./mcp-server/demo-cli.py break service nginx"
echo "     ./mcp-server/demo-cli.py break cert"
echo "     ./mcp-server/demo-cli.py break baseline"
echo ""
echo "   Check status:"
echo "     ./mcp-server/demo-cli.py status"
echo ""
echo "   Reset all:"
echo "     ./mcp-server/demo-cli.py reset"
echo ""
echo "📝 LOGS:"
echo "   View logs: docker-compose logs -f"
echo "   Stop: docker-compose down"
echo ""
echo "════════════════════════════════════════════════════════════════"
