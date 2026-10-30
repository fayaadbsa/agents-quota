#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing Agents Quota..."

# 1. Install icons
mkdir -p ~/.local/share/icons/hicolor/scalable/apps
cp "$DIR/icons/agents-quota.svg" ~/.local/share/icons/hicolor/scalable/apps/agents-quota.svg

python3 -c "
import os, gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

src = '$DIR/icons/agents-quota.png'
pix = GdkPixbuf.Pixbuf.new_from_file(src)
for sz in [16, 24, 32, 48, 64, 128, 256]:
    d = os.path.expanduser(f'~/.local/share/icons/hicolor/{sz}x{sz}/apps')
    os.makedirs(d, exist_ok=True)
    scaled = pix.scale_simple(sz, sz, GdkPixbuf.InterpType.BILINEAR)
    scaled.savev(os.path.join(d, 'agents-quota.png'), 'png', [], [])
" 2>/dev/null || true

gtk-update-icon-cache ~/.local/share/icons/hicolor 2>/dev/null || true

# 2. Install desktop application entry
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/agents-quota.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Agents Quota
GenericName=AI Quota Tracker
Comment=Track AI coding agents quota limits and session usage
Exec=$DIR/launch.sh
Icon=agents-quota
Terminal=false
Categories=Utility;Development;
StartupNotify=false
Keywords=claude;quota;limits;antigravity;codex;ai;9router;
StartupWMClass=quota-tracker-window
Actions=Refresh;Config;

[Desktop Action Refresh]
Name=Refresh Quotas Now
Exec=/usr/bin/python3 -c "import sys; sys.path.insert(0, '$DIR'); import quota_engine; quota_engine.get_all_accounts_data(force=True)"

[Desktop Action Config]
Name=Edit Accounts Configuration
Exec=xdg-open ~/.config/claude-limits/accounts.json
EOF

update-desktop-database ~/.local/share/applications/ 2>/dev/null || true

# 3. Install autostart entry
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/agents-quota.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Agents Quota
Comment=AI agents quota and limits indicator in tray
Exec=/usr/bin/python3 $DIR/indicator.py
Icon=agents-quota
Terminal=false
Categories=Utility;Development;
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF

echo "==> Starting Agents Quota..."
pkill -f "$DIR/indicator.py" 2>/dev/null || true
nohup /usr/bin/python3 "$DIR/indicator.py" >/dev/null 2>&1 &
disown

echo "==> Done! Agents Quota is now running and installed as an Ubuntu application."
