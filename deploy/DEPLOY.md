# Deployment guide

## Prerequisites

- Ubuntu 22.04+ on the rtorrent host
- rtorrent ≥ 0.9.8 with SCGI enabled (rutorrent sets this up by default)
- `uv` installed for the service user
- Port 8768 open to the bot host (or a reverse-proxy in front)

---

## 1. Verify rtorrent SCGI

In `/etc/rtorrent/.rtorrent.rc` (or equivalent) you need:

```ini
scgi_port = 127.0.0.1:5000
```

Test it locally before going further:

```bash
python3 - <<'EOF'
import socket, struct
body = b'<?xml version="1.0"?><methodCall><methodName>system.listMethods</methodName><params/></methodCall>'
hdr  = b'CONTENT_LENGTH\x00' + str(len(body)).encode() + b'\x00SCGI\x001\x00'
ns   = str(len(hdr)).encode() + b':' + hdr + b','
s = socket.create_connection(('127.0.0.1', 5000))
s.sendall(ns + body)
print(s.recv(4096)[:200])
EOF
```

You should see an XML response listing rtorrent methods.

---

## 2. Create the service user and install directory

```bash
sudo useradd --system --shell /usr/sbin/nologin --home /home/movie --create-home movie
sudo mkdir -p /opt/rtorrent-mcp /etc/rtorrent-mcp
sudo chown movie:movie /opt/rtorrent-mcp
```

---

## 3. Install uv for the service user

```bash
sudo -u movie bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
# uv lands in /home/movie/.local/bin/uv — adjust ExecStart if different:
sudo -u movie bash -c 'which uv || echo "check PATH"'
```

---

## 4. Clone the repository

```bash
sudo -u movie git clone https://github.com/wildcar/rtorrent-mcp.git /opt/rtorrent-mcp
cd /opt/rtorrent-mcp
sudo -u movie /home/movie/.local/bin/uv sync --frozen
```

---

## 5. Create the environment file

```bash
sudo cp /opt/rtorrent-mcp/.env.example /etc/rtorrent-mcp/rtorrent-mcp.env
sudo chmod 640 /etc/rtorrent-mcp/rtorrent-mcp.env
sudo chown root:movie /etc/rtorrent-mcp/rtorrent-mcp.env
sudo nano /etc/rtorrent-mcp/rtorrent-mcp.env
```

Minimum required values:

```ini
RTORRENT_SCGI_URL=scgi://127.0.0.1:5000

RTORRENT_DOWNLOAD_DIR_MOVIES=/mnt/storage/Media/Video/Movie/
RTORRENT_DOWNLOAD_DIR_SERIES=/mnt/storage/Media/Video/Series/

# Generate a strong random token — the bot must use the same value
MCP_AUTH_TOKEN=<openssl rand -hex 32>

MCP_TRANSPORT=streamable-http
MCP_HTTP_HOST=0.0.0.0
MCP_HTTP_PORT=8768
```

Generate the token in one step:

```bash
echo "MCP_AUTH_TOKEN=$(openssl rand -hex 32)"
```

---

## 6. Install and start the systemd unit

```bash
sudo cp /opt/rtorrent-mcp/deploy/rtorrent-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rtorrent-mcp
sudo systemctl status rtorrent-mcp
```

Check logs:

```bash
sudo journalctl -u rtorrent-mcp -f
```

You should see a JSON line like:

```json
{"event": "rtorrent_mcp.starting", "transport": "streamable-http", "scgi_url": "scgi://127.0.0.1:5000", ...}
```

---

## 7. Smoke-test the MCP endpoint

From the rtorrent host itself:

```bash
curl -s -X POST http://127.0.0.1:8768/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_TOKEN>" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python3 -m json.tool
```

From the bot host (replace `<RTORRENT_HOST>`):

```bash
curl -s -X POST http://<RTORRENT_HOST>:8768/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_TOKEN>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python3 -m json.tool
```

Expected: JSON with `tools` array listing `add_torrent`, `list_downloads`, etc.

---

## 8. Configure the bot

On the **bot host**, add to `/etc/movie-handler/movie-handler-telegram.env`:

```ini
RTORRENT_MCP_URL=http://<RTORRENT_HOST>:8768/mcp
RTORRENT_MCP_AUTH_TOKEN=<same token as above>
```

Then restart the bot:

```bash
sudo systemctl restart movie-handler-telegram
sudo journalctl -u movie-handler-telegram -f
```

The bot log should show the rtorrent client connecting. When a user picks a torrent, the bot will push it directly to rtorrent and reply with:

> ✅ Поставил на закачку на сервере: **<name>**

If `RTORRENT_MCP_URL` is not set, the bot falls back to sending the `.torrent` file to the user.

---

## 9. Updates

```bash
cd /opt/rtorrent-mcp
sudo -u movie git pull
sudo -u movie /home/movie/.local/bin/uv sync --frozen
sudo systemctl restart rtorrent-mcp
```

---

## Firewall note

If the rtorrent host runs `ufw`:

```bash
# allow bot host only — replace with the actual bot IP
sudo ufw allow from <BOT_HOST_IP> to any port 8768 proto tcp
```

Never expose port 8768 to the world without the `MCP_AUTH_TOKEN` set.
