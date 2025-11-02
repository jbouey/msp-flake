#!/bin/bash
set -e

echo "════════════════════════════════════════════════════════════════"
echo "  MSP HIPAA Compliance Platform - Demo Startup"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker first."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ docker-compose not found. Please install docker-compose first."
    exit 1
fi

echo "✅ Docker found"
echo ""

# Create required directories
echo "📁 Creating required directories..."
mkdir -p /tmp/msp-demo-state
mkdir -p /var/lib/msp/evidence
mkdir -p prometheus
mkdir -p grafana/provisioning/datasources
mkdir -p grafana/provisioning/dashboards
mkdir -p grafana/dashboards

touch /tmp/msp-demo-incidents.json

echo "✅ Directories created"
echo ""

# Start services
echo "🚀 Starting services..."
docker-compose up -d

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
