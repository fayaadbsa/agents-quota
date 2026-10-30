#!/usr/bin/env python3
import sys
import os
import signal
import time
import subprocess
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, GLib, AyatanaAppIndicator3

import quota_engine
from popup_card import QuotaTrackerWindow

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.expanduser("~/.cache/claude-limits")
os.makedirs(CACHE_DIR, exist_ok=True)
TRAY_H = 32  # logical px; appindicator shows wide (>=1.5:1) images at native size, so this is the on-panel height


class QuotaTrackerTray:
    def __init__(self):
        self.card_window = None
        self.icon_toggle = False
        self.acc_menu_box = []

        # Initial placeholder icon
        default_app_icon = os.path.join(APP_DIR, "icons", "agents-quota.png")
        init_icon = os.path.join(CACHE_DIR, "tray_strip_a.png")
        quota_engine.draw_tray([], init_icon, h=TRAY_H)

        # Single tray indicator instance
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "agents-quota-indicator",
            init_icon if os.path.exists(init_icon) else default_app_icon,
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_icon_theme_path(CACHE_DIR)
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

        self.build_menu()
        self.update_tray_data()

        # Tick every minute: redraw countdowns + check local hit signals (network only after real model use)
        GLib.timeout_add_seconds(60, self.on_timer_refresh)

    def build_menu(self):
        self.menu = Gtk.Menu()

        self.item_header = Gtk.MenuItem(label="Quota Tracker")
        self.item_header.set_sensitive(False)
        self.menu.append(self.item_header)
        self.menu.append(Gtk.SeparatorMenuItem())
        # account rows get inserted here (index 2)
        self.menu.append(Gtk.SeparatorMenuItem())

        item_open = Gtk.MenuItem(label="Show details")
        item_open.connect("activate", self.trigger_open_card)
        self.menu.append(item_open)

        item_refresh = Gtk.MenuItem(label="Refresh now")
        item_refresh.connect("activate", lambda w: self.update_tray_data(force=True))
        self.menu.append(item_refresh)

        item_settings = Gtk.MenuItem(label="Settings")
        self.settings_menu = Gtk.Menu()
        item_settings.set_submenu(self.settings_menu)
        self.menu.append(item_settings)

        self.menu.append(Gtk.SeparatorMenuItem())
        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect("activate", lambda w: Gtk.main_quit())
        self.menu.append(item_quit)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

    def rebuild_settings_menu(self, data):
        for child in self.settings_menu.get_children():
            self.settings_menu.remove(child)

        title = Gtk.MenuItem(label="Show in panel")
        title.set_sensitive(False)
        self.settings_menu.append(title)
        for acc in data.get("accounts", []):
            item = Gtk.CheckMenuItem(label=acc["name"])
            item.set_active(acc.get("show_in_tray", False))  # set before connect: no spurious toggle
            item.connect("toggled", self.on_toggle_account, acc["id"])
            self.settings_menu.append(item)

        self.settings_menu.append(Gtk.SeparatorMenuItem())
        title = Gtk.MenuItem(label="After model use, refetch at most every")
        title.set_sensitive(False)
        self.settings_menu.append(title)
        group = None
        for minutes in (2, 5, 10, 15):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, f"{minutes} min")
            group = group or item
            item.set_active(data.get("fetch_gap") == minutes * 60)
            item.connect("toggled", self.on_set_gap, minutes * 60)
            self.settings_menu.append(item)

        self.settings_menu.append(Gtk.SeparatorMenuItem())
        item_cfg = Gtk.MenuItem(label="Edit config file...")
        item_cfg.connect("activate", self.on_open_config)
        self.settings_menu.append(item_cfg)
        self.settings_menu.show_all()

    def on_toggle_account(self, item, account_id):
        quota_engine.toggle_account_tray(account_id, item.get_active())
        GLib.idle_add(self.update_tray_data)

    def on_set_gap(self, item, seconds):
        if item.get_active():
            quota_engine.set_setting("fetch_gap_seconds", seconds)
            GLib.idle_add(self.update_tray_data)

    @staticmethod
    def quota_row_text(acc):
        seg = quota_engine.tray_segments({"accounts": [dict(acc, show_in_tray=True)]})
        if not seg:
            return acc["name"]
        parts = [quota_engine._pct_text(p) + (f" · {t}" if t else "") for p, t in seg[0]["rows"]]
        labels = ["5h", "week"]
        return f"{acc['name']}    " + "    ".join(f"{l} {v}" for l, v in zip(labels, parts))

    def update_tray_data(self, force=False):
        data = quota_engine.get_all_accounts_data(force=force)
        accounts = data.get("accounts", [])

        # 1. Account rows: one line each, panel-visible accounts only
        for item in self.acc_menu_box:
            self.menu.remove(item)
        self.acc_menu_box.clear()
        shown = [a for a in accounts if a.get("show_in_tray")] or accounts
        for idx, acc in enumerate(shown, start=2):
            item = Gtk.MenuItem(label=self.quota_row_text(acc))
            item.connect("activate", self.trigger_open_card)
            self.menu.insert(item, idx)
            self.acc_menu_box.append(item)

        mins_ago = int((time.time() - data.get("fetched_at", 0)) // 60)
        self.item_header.set_label(f"Quota Tracker · fetched {'just now' if mins_ago < 1 else f'{mins_ago}m ago'}")
        self.rebuild_settings_menu(data)
        self.menu.show_all()

        # 2. Render whole tray strip (logo + daily/weekly rings + 2-row text) as one image
        self.icon_toggle = not self.icon_toggle
        suffix = "_a" if self.icon_toggle else "_b"  # alternate filename to force GNOME icon reload
        icon_file = os.path.join(CACHE_DIR, f"tray_strip{suffix}.png")
        png_out = quota_engine.draw_tray(quota_engine.tray_segments(data), icon_file, h=TRAY_H)
        self.indicator.set_icon_full(png_out, "Agents Quota")

        if self.card_window and self.card_window.get_visible():
            # Reuse fetched data; refresh_view() would call back into update_tray_data (infinite loop)
            self.card_window.data = data
            self.card_window.render_cards()

    def on_timer_refresh(self):
        self.update_tray_data(force=False)
        return True

    def trigger_open_card(self, widget):
        GLib.idle_add(self.show_card_window)

    def show_card_window(self):
        if not self.card_window:
            self.card_window = QuotaTrackerWindow(
                on_tray_toggle_cb=lambda: self.update_tray_data(force=False)
            )

        self.card_window.refresh_view(force=False)
        self.card_window.position_bottom_right()
        self.card_window.show_all()
        self.card_window.present()
        self.card_window.set_keep_above(True)
        return False

    def toggle_card_window(self):
        if self.card_window and self.card_window.get_visible() and self.card_window.is_active():
            self.card_window.hide()
        else:
            self.show_card_window()
        return False

    def on_open_config(self, widget):
        cfg_path = os.path.expanduser("~/.config/claude-limits/accounts.json")
        for cmd in [["xdg-open", cfg_path], ["gnome-text-editor", cfg_path], ["gedit", cfg_path]]:
            try:
                subprocess.Popen(cmd)
                return
            except Exception:
                pass


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QuotaTrackerTray()
    signal.signal(signal.SIGUSR1, lambda s, f: GLib.idle_add(app.toggle_card_window))
    Gtk.main()


if __name__ == "__main__":
    main()
