# Deployment Guide for Nova Poshta AI Waybill Bot (Linux / Systemd)

This guide provides step-by-step commands to deploy the **Nova Poshta AI Waybill Generator Bot** on an Ubuntu/Debian Linux server and configure it as a systemd background service that automatically launches on server boot and restarts on crashes.

---

## 1. System Preparation & Dependency Installation

Run the following commands on your remote server via SSH:

```bash
# Update package list and install Python 3, venv, git, and build tools
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
```

---

## 2. Clone Repository & Setup Virtual Environment

```bash
# Navigate to deployment directory (e.g. /opt)
cd /opt

# Clone the repository from GitHub
sudo git clone https://github.com/rashevskyv/npsender_bot.git
cd npsender_bot

# Create Python virtual environment
sudo python3 -m venv venv

# Install dependencies in venv
sudo ./venv/bin/pip install --upgrade pip
sudo ./venv/bin/pip install -r requirements.txt
```

---

## 3. Create `.env` Configuration File

Create your `.env` file in `/opt/npsender_bot/.env`:

```bash
sudo nano /opt/npsender_bot/.env
```

Paste your production credentials into `.env`:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
NOVA_POSHTA_API_KEY=your_nova_poshta_api_key_here
AI_PROVIDER=openai
AI_API_KEY=your_ai_api_key_here
# SENDER_COUNTERPARTY_REF=...
# SENDER_CONTACT_REF=...
# SENDER_CITY_REF=...
# SENDER_ADDRESS_REF=...
# SENDER_PHONE=...
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

---

## 4. Setup Systemd Service (Auto-Start on Boot & Auto-Restart)

Create the systemd service file:

```bash
sudo nano /etc/systemd/system/npsender_bot.service
```

Paste the following service configuration:

```ini
[Unit]
Description=Nova Poshta AI Waybill Bot Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/npsender_bot
ExecStart=/opt/npsender_bot/venv/bin/python -m src.bot.main
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

---

## 5. Enable & Start Systemd Service

```bash
# Reload systemd daemon
sudo systemctl daemon-reload

# Enable service to start automatically on system boot
sudo systemctl enable npsender_bot.service

# Start the bot service immediately
sudo systemctl start npsender_bot.service

# Check live service status
sudo systemctl status npsender_bot.service
```

---

## 6. Useful Maintenance Commands

- **View Live Logs**:
  ```bash
  sudo journalctl -u npsender_bot.service -f
  ```

- **Restart Bot Service**:
  ```bash
  sudo systemctl restart npsender_bot.service
  ```

- **Stop Bot Service**:
  ```bash
  sudo systemctl stop npsender_bot.service
  ```

- **Update Bot Code from GitHub**:
  ```bash
  cd /opt/npsender_bot
  sudo git pull origin main
  sudo ./venv/bin/pip install -r requirements.txt
  sudo systemctl restart npsender_bot.service
  ```
