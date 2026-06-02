#!/bin/bash
# MIA GEKDI Auth Server — Full Setup (run as root)
set -e
echo "=== MIA GEKDI Auth Server Setup ==="

# System deps
apt-get update -q
apt-get install -y nginx openssl curl

# Node.js 20
if ! command -v node &>/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
echo "Node: $(node --version)"

# System user
id mia-auth &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin mia-auth

# App directory
mkdir -p /opt/mia-auth
cp server.js /opt/mia-auth/server.js
cp package.json /opt/mia-auth/package.json
cd /opt/mia-auth && npm install --production --no-audit
chown -R mia-auth:mia-auth /opt/mia-auth

# TLS certificate (self-signed, 10 years)
mkdir -p /etc/ssl/mia-auth
openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /etc/ssl/mia-auth/cert.key \
  -out    /etc/ssl/mia-auth/cert.crt \
  -subj   "/CN=62.238.37.129" \
  -addext "subjectAltName=IP:62.238.37.129" 2>/dev/null
chmod 600 /etc/ssl/mia-auth/cert.key
echo "TLS cert ✓"

# .env with auto-generated secrets
if [ ! -f /opt/mia-auth/.env ]; then
  JWT_S=$(node -e "console.log(require('crypto').randomBytes(48).toString('base64url'))")
  AUTH_H=$(node -e "require('bcryptjs').hash('004577',12).then(h=>process.stdout.write(h))")
  printf "PORT=3000\nALLOWED_ORIGINS=https://secrett-archive.com\nJWT_SECRET=%s\nAUTH_HASH=%s\n" "$JWT_S" "$AUTH_H" > /opt/mia-auth/.env
  chmod 600 /opt/mia-auth/.env
  chown mia-auth:mia-auth /opt/mia-auth/.env
  echo ".env created ✓"
fi

# nginx
cp nginx.conf /etc/nginx/sites-available/mia-auth
ln -sf /etc/nginx/sites-available/mia-auth /etc/nginx/sites-enabled/mia-auth
rm -f /etc/nginx/sites-enabled/default
nginx -t && echo "nginx config valid ✓"

# Firewall
if command -v ufw &>/dev/null; then
  ufw allow 22/tcp; ufw allow 80/tcp; ufw allow 443/tcp; ufw --force enable
  echo "UFW ✓"
fi

# systemd
cp mia-auth.service /etc/systemd/system/mia-auth.service
systemctl daemon-reload
systemctl enable mia-auth; systemctl start mia-auth
systemctl enable nginx; systemctl restart nginx
sleep 2

echo "=== Done ==="
curl -sk https://62.238.37.129/health | python3 -m json.tool 2>/dev/null || echo "Starting..."
