# Agents Quota

A lightweight system tray indicator and popup widget for tracking AI coding agent quotas (Claude Code, Antigravity, OpenAI Codex, 9Router) on Linux / GNOME and any AppIndicator-compatible desktop.

---

## Supported Environments & Providers

### Supported Desktop Environments & OS
- **OS**: Linux (Ubuntu, Debian, Fedora, Arch, etc.)
- **Display Servers**: Wayland and X11
- **Desktop Environments**:
  - GNOME (Ubuntu default panel, Dash to Panel, or AppIndicator extension)
  - KDE Plasma (via StatusNotifierItem / AppIndicator)
  - XFCE, MATE, Cinnamon, and other desktops with system tray / notification area support

### Supported Agents & Providers
- **Claude Code**:
  - Direct OAuth / official credentials (`~/.claude/.credentials.json`)
  - Multi-profile environments (`~/.claude-profiles/profile*`)
  - Remote router endpoints with dual-instance tracking:
    - **Claude Dev** (amber `D` badge in tray)
    - **Claude Stag** (purple `S` badge in tray)
  - 5-hour session quota, weekly (7-day) limit, and live reset countdowns
- **Antigravity**:
  - Live session & weekly quotas (Gemini & Claude/GPT windows)
  - Multi-connection auto-detection via local 9Router instance
- **OpenAI Codex**:
  - 5-hour session quota & weekly limit
  - Available reset credits counter
- **9Router**:
  - Local gateway (`localhost:20128`) auto-auth via machine ID and CLI secret
  - Remote endpoints (Dev & Staging gateways)

---

## Features

- **Dynamic Tray Indicator**: Real-time rings and compact percentage / countdown counters rendered directly into the system panel.
- **Visual Badges**: Distinguishes multiple instances of the same provider (e.g. `D` for Dev, `S` for Staging).
- **Popup Mini-Card**: Modern dark liquid-glass card showing detailed progress bars, percentage remaining, and exact reset timers.
- **Per-Account Tray Toggles**: Choose exactly which accounts appear on your panel and which stay in the details popup.
- **Activity-Aware Polling**: Checks local transcripts and database activity before making network requests to conserve API rate limits.
- **One-Click Hotkey Toggle**: Easily bindable to a custom shortcut key to show/hide the details popup instantly.

---

## Requirements & Dependencies

On Ubuntu / Debian:
```bash
sudo apt update
sudo apt install python3 python3-gi python3-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
```

On Fedora:
```bash
sudo dnf install python3 python3-gobject cairo-gobject gtk3 libayatana-appindicator-gtk3
```

On Arch Linux:
```bash
sudo pacman -S python python-gobject python-cairo gtk3 libayatana-appindicator
```

---

## Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/fayaadbsa/agents-quota.git ~/programming/agents-quota
   cd ~/programming/agents-quota
   ```

2. **Run manually**:
   ```bash
   python3 indicator.py &
   ```

3. **Toggle Details Popup via Shortcut**:
   You can bind `toggle.sh` to a GNOME custom keyboard shortcut (e.g. `Super + U`):
   ```bash
   /path/to/agents-quota/toggle.sh
   ```

4. **Autostart on Login**:
   Create `~/.config/autostart/agents-quota.desktop`:
   ```ini
   [Desktop Entry]
   Type=Application
   Name=Agents Quota
   Comment=AI agents quota and limits indicator in tray
   Exec=python3 /home/YOUR_USER/programming/agents-quota/indicator.py
   Icon=/home/YOUR_USER/programming/agents-quota/icons/claude.png
   Terminal=false
   Categories=Utility;System;
   StartupNotify=false
   X-GNOME-Autostart-enabled=true
   ```

---

## Configuration

Configuration is stored in `~/.config/claude-limits/accounts.json` (created automatically on first launch).

You can customize:
- `fetch_gap_seconds`: Minimum seconds between network fetches after model activity (default: `300`).
- Remote URLs & API keys for multi-environment gateways.
- `show_in_tray`: Boolean per account to toggle display on the panel.

---

## Credits & Acknowledgments

This project is inspired by and originally based on [claude-limits-plasmoid](https://github.com/Nael-Nathanael/claude-limits-plasmoid) by [Nael Nathanael](https://github.com/Nael-Nathanael), which pioneered Claude Code limit tracking for KDE Plasma.

This version was ported to GNOME / GTK3 (via Ayatana AppIndicator) and expanded to support multi-agent ecosystems (Claude Dev & Stag split, Antigravity, OpenAI Codex, and 9Router integration).

---

## License

MIT License. See [LICENSE](LICENSE) for details.
