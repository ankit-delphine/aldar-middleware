#!/bin/bash
# Prometheus Entrypoint Script - Substitutes environment variables in config

CONFIG_FILE="/etc/prometheus/prometheus.yml"
TEMP_FILE="/tmp/prometheus.yml.tmp"

# Replace placeholder with actual environment variable
if [ -n "$AZURE_PROMETHEUS_PASSWORD" ]; then
    sed "s/PLACEHOLDER_WORKSPACE_KEY/$AZURE_PROMETHEUS_PASSWORD/g" "$CONFIG_FILE" > "$TEMP_FILE"
    mv "$TEMP_FILE" "$CONFIG_FILE"
    echo "✓ Azure Prometheus credentials injected"
else
    echo "⚠ Warning: AZURE_PROMETHEUS_PASSWORD not set - remote write will fail"
fi

# Start Prometheus
exec /bin/prometheus "$@"