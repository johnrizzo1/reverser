#!/usr/bin/env bash
# Configure nftables to restrict Incus container egress.
# Containers on incusbr0 can only reach api.anthropic.com:443 and DNS on the gateway.
set -euo pipefail

BRIDGE_SUBNET="10.175.12.0/24"
GATEWAY_IP="10.175.12.1"

# Resolve Anthropic API IP(s)
echo "Resolving api.anthropic.com..."
API_IPS=$(getent ahostsv4 api.anthropic.com | awk '{print $1}' | sort -u)
if [ -z "$API_IPS" ]; then
    echo "ERROR: Could not resolve api.anthropic.com" >&2
    exit 1
fi

echo "Anthropic API IPs: $API_IPS"

# Build the nftables allow rules for each API IP
ALLOW_RULES=""
for ip in $API_IPS; do
    ALLOW_RULES="${ALLOW_RULES}
        iifname \"incusbr0\" ip saddr $BRIDGE_SUBNET ip daddr $ip tcp dport 443 accept"
done

# Apply nftables rules
echo "Applying firewall rules..."
nft -f - <<EOF
table inet reverser_harness {
    chain forward {
        type filter hook forward priority 0; policy accept;

        # Allow return traffic for established connections
        iifname "incusbr0" ct state established,related accept

        # Allow DNS to gateway (for hostname resolution inside container)
        iifname "incusbr0" ip saddr $BRIDGE_SUBNET ip daddr $GATEWAY_IP udp dport 53 accept
        iifname "incusbr0" ip saddr $BRIDGE_SUBNET ip daddr $GATEWAY_IP tcp dport 53 accept

        # Allow HTTPS to Anthropic API only
        $ALLOW_RULES

        # Drop all other outbound traffic from containers
        iifname "incusbr0" ip saddr $BRIDGE_SUBNET drop
    }
}
EOF

echo "Firewall configured. Containers on incusbr0 can only reach:"
echo "  - $GATEWAY_IP:53 (DNS)"
for ip in $API_IPS; do
    echo "  - $ip:443 (api.anthropic.com)"
done
echo "All other outbound traffic is dropped."
