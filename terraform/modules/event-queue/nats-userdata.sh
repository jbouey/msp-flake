#!/bin/bash
# NATS Server installation and configuration
# For Amazon Linux 2023

set -euo pipefail

# Update system
dnf update -y

# Install dependencies
dnf install -y wget tar gzip

# Create NATS user
useradd -r -s /sbin/nologin nats || true

# Create directories
mkdir -p /opt/nats /var/lib/nats/jetstream /var/log/nats /etc/nats
chown -R nats:nats /var/lib/nats /var/log/nats

# Download and install NATS
NATS_VERSION="2.10.7"
wget -q https://github.com/nats-io/nats-server/releases/download/v$${NATS_VERSION}/nats-server-v$${NATS_VERSION}-linux-amd64.tar.gz
tar -xzf nats-server-v$${NATS_VERSION}-linux-amd64.tar.gz
mv nats-server-v$${NATS_VERSION}-linux-amd64/nats-server /usr/local/bin/
chmod +x /usr/local/bin/nats-server
rm -rf nats-server-v$${NATS_VERSION}-linux-amd64*

# Write configuration
cat > /etc/nats/nats-server.conf <<'NATSCONFIG'
${nats_config}
NATSCONFIG

chown nats:nats /etc/nats/nats-server.conf
chmod 640 /etc/nats/nats-server.conf

# Create systemd service
cat > /etc/systemd/system/nats.service <<'EOF'
[Unit]
Description=NATS Server
After=network.target

[Service]
Type=simple
User=nats
Group=nats
ExecStart=/usr/local/bin/nats-server -c /etc/nats/nats-server.conf
ExecReload=/bin/kill -s HUP $MAINPID
Restart=always
RestartSec=5
LimitNOFILE=65536

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/nats /var/log/nats
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
EOF

# Enable and start NATS
systemctl daemon-reload
systemctl enable nats
systemctl start nats

# Wait for NATS to start
sleep 5

# Check if NATS is running
if systemctl is-active --quiet nats; then
    echo "NATS server started successfully"
else
    echo "NATS server failed to start"
    journalctl -u nats -n 50
    exit 1
fi

# Install CloudWatch agent (for metrics)
wget -q https://s3.amazonaws.com/amazoncloudwatch-agent/amazon_linux/amd64/latest/amazon-cloudwatch-agent.rpm
rpm -U ./amazon-cloudwatch-agent.rpm
rm -f amazon-cloudwatch-agent.rpm

# Configure CloudWatch agent
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<'CWCONFIG'
{
  "metrics": {
    "namespace": "MSP/NATS",
    "metrics_collected": {
      "cpu": {
        "measurement": [
          {"name": "cpu_usage_idle", "rename": "CPU_IDLE", "unit": "Percent"},
          {"name": "cpu_usage_iowait", "rename": "CPU_IOWAIT", "unit": "Percent"}
        ],
        "totalcpu": false
      },
      "disk": {
        "measurement": [
          {"name": "used_percent", "rename": "DISK_USED", "unit": "Percent"}
        ],
        "resources": ["/", "/var/lib/nats"]
      },
      "mem": {
        "measurement": [
          {"name": "mem_used_percent", "rename": "MEM_USED", "unit": "Percent"}
        ]
      }
    }
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/nats/nats-server.log",
            "log_group_name": "/msp/nats",
            "log_stream_name": "{instance_id}"
          }
        ]
      }
    }
  }
}
CWCONFIG

# Start CloudWatch agent
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config \
    -m ec2 \
    -s \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

echo "NATS installation and configuration complete"
