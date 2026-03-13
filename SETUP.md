# Sauna Session Tracker — Setup Guide

## Prerequisites

- **Windows 10/11 mini PC** with Python 3.11+ installed
- **Aqara Presence Sensor FP2** (mmWave, Wi-Fi)
- Your Wi-Fi network name and password

---

## 1. Quick Start

```
cd HomeAutomation
run.bat
```

This will:
1. Create a Python virtual environment
2. Install dependencies (FastAPI, uvicorn, aiosqlite)
3. Start the server on `http://0.0.0.0:8000`

Open `http://<your-pc-ip>:8000` from any device on your network.

---

## 2. Set Up the Aqara FP2 Sensor

### 2a. Add the sensor to your Wi-Fi

1. **Download the Aqara Home app** on your phone (iOS / Android).
2. Create an Aqara account and sign in.
3. Tap **"+"** → **Presence Sensor FP2**.
4. Plug in the FP2 via USB-C. The LED will blink.
5. Follow the in-app instructions to connect it to your **2.4 GHz Wi-Fi**
   network (the FP2 does not support 5 GHz).
6. Once paired, the FP2 shows up in the Aqara app with live presence data.

### 2b. Expose the sensor to this app

The FP2 supports **HomeKit** and **Matter** natively but not direct HTTP
webhooks. The recommended bridge is **Home Assistant**:

1. **Install Home Assistant** on the same Windows PC
   ([instructions](https://www.home-assistant.io/installation/windows/)).
   The easiest method is to run it inside **VirtualBox** or **Docker Desktop**.
2. In Home Assistant, go to **Settings → Devices & Services → Add Integration**
   and add **HomeKit Controller**. It will auto-discover the FP2.
3. Accept the pairing code (shown on the FP2 or in the Aqara app).
4. The FP2 will appear as a binary sensor: `binary_sensor.fp2_presence`.

### 2c. Create a Home Assistant automation

Go to **Settings → Automations → Create Automation** and add two automations:

**Automation 1 — Presence ON**

```yaml
alias: "Sauna Presence ON"
trigger:
  - platform: state
    entity_id: binary_sensor.fp2_presence
    to: "on"
action:
  - service: rest_command.sauna_enter
```

**Automation 2 — Presence OFF**

```yaml
alias: "Sauna Presence OFF"
trigger:
  - platform: state
    entity_id: binary_sensor.fp2_presence
    to: "off"
action:
  - service: rest_command.sauna_leave
```

Add these REST commands to your `configuration.yaml`:

```yaml
rest_command:
  sauna_enter:
    url: "http://<WINDOWS-PC-IP>:8000/api/presence"
    method: POST
    content_type: "application/json"
    payload: '{"presence": true}'
  sauna_leave:
    url: "http://<WINDOWS-PC-IP>:8000/api/presence"
    method: POST
    content_type: "application/json"
    payload: '{"presence": false}'
```

Replace `<WINDOWS-PC-IP>` with your mini PC's local IP (e.g. `192.168.1.50`).
Restart Home Assistant to apply.

### Alternative: Without Home Assistant

If you don't want Home Assistant, you can use the **simulate** buttons in the
web UI to manually start/stop the timer, or write a small script that polls the
Aqara cloud API (requires an Aqara developer account).

---

## 3. Access from Outside Your Network

### Option A: Cloudflare Tunnel (recommended, free)

1. Create a free [Cloudflare](https://dash.cloudflare.com/) account.
2. Add a domain (or use a free one via Cloudflare).
3. Install `cloudflared` on the Windows PC:
   ```
   winget install Cloudflare.cloudflared
   ```
4. Authenticate:
   ```
   cloudflared tunnel login
   ```
5. Create a tunnel:
   ```
   cloudflared tunnel create sauna
   ```
6. Route traffic:
   ```
   cloudflared tunnel route dns sauna sauna.yourdomain.com
   ```
7. Run the tunnel:
   ```
   cloudflared tunnel --url http://localhost:8000 run sauna
   ```

Now `https://sauna.yourdomain.com` is accessible from anywhere.

To auto-start the tunnel, install it as a Windows service:
```
cloudflared service install
```

### Option B: ngrok (quick and easy)

1. [Download ngrok](https://ngrok.com/download) and sign up for a free account.
2. Authenticate: `ngrok config add-authtoken <YOUR_TOKEN>`
3. Run: `ngrok http 8000`
4. Use the generated URL (e.g. `https://abc123.ngrok-free.app`).

Note: The free ngrok URL changes every restart unless you pay for a fixed domain.

---

## 4. Auto-Start on Windows Boot

Run `install_service.bat` **as Administrator**. This creates a Windows
Scheduled Task called `SaunaTracker` that launches the server on every logon.

To remove it later:
```
schtasks /delete /tn "SaunaTracker" /f
```

If you also set up a Cloudflare Tunnel, install that as a service too
(`cloudflared service install`) so both start automatically.

---

## 5. API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Web UI |
| `/api/state` | GET | Current session state (JSON) |
| `/api/presence` | POST | Webhook: `{"presence": true/false}` |
| `/api/sessions` | GET | Past sessions (`?limit=50&offset=0`) |
| `/api/sessions/{id}` | GET | Single session detail |
| `/api/sessions/{id}` | DELETE | Delete a session |
| `/api/simulate/enter` | POST | Simulate entering sauna |
| `/api/simulate/leave` | POST | Simulate leaving sauna |
| `/ws` | WebSocket | Live state updates (1 Hz) |

---

## 6. Configuration

Environment variables (optional):

| Variable | Default | Description |
|---|---|---|
| `SAUNA_DB` | `sauna.db` | Path to SQLite database file |
| `SAUNA_BREAK_TIMEOUT` | `600` | Seconds before a break auto-closes the session |

---

## 7. Testing Without the Sensor

Use the **Simulate Enter / Simulate Leave** buttons in the web UI, or call the
API directly:

```bash
curl -X POST http://localhost:8000/api/simulate/enter
curl -X POST http://localhost:8000/api/simulate/leave
```
