# Raspberry Pi Zero 2W Smart Desk Dashboard

Pygame framebuffer dashboard for a 1024x600 HDMI display, with a Flask backend that caches school, calendar, weather, Spotify, market, news and AFL data.

## Files

- `app.py` — Flask API and background data workers
- `ui.py` — Pygame frontend and GPIO button handling
- `config.example.json` — configuration template; copy to `config.json`
- `dashboard-backend.service` / `dashboard-ui.service` — systemd units
- `requirements.txt` — Python dependencies

## Setup

```bash
sudo apt update
sudo apt install -y python3-pip python3-pygame
cd /home/pi/dashboard
python3 -m pip install -r requirements.txt
cp config.example.json config.json
nano config.json
```

Put `BebasNeue-Regular.ttf` beside `ui.py` if you want the specified font. The UI falls back to DejaVu Sans if the font is absent.

Do **not** commit `config.json` or `spotify_token.json`. They are ignored by Git because they contain credentials/tokens.

## Spotify

Run the Spotify OAuth flow once on a machine where the Spotify callback can be completed, then place the resulting `spotify_token.json` on the Pi. The Pi does not need to store the client secret in Git.

## Run

```bash
python3 app.py
```

In another terminal:

```bash
python3 ui.py
```

For boot-time operation:

```bash
sudo cp dashboard-backend.service dashboard-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dashboard-backend.service
sudo systemctl enable --now dashboard-ui.service
```

The physical button is BCM GPIO 17, active LOW with the internal pull-up. A short press changes pages; on page 3, a long press opens/closes the fixture menu.
