#!/usr/bin/env python3
import os
import time
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango

import quota_engine

CARD_W, CARD_H = 600, 560
LOGO_PX = 36

CSS_STYLE = """
window.quota-tracker-window { background-color: transparent; }

.main-container {
    background-color: #12131c;
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 18px;
    padding: 20px 22px;
}
.header-title { font-size: 19px; font-weight: bold; color: #f8fafc; }
.header-subtitle { font-size: 12px; color: #94a3b8; }

.icon-btn {
    background: transparent; border: none; box-shadow: none;
    color: #94a3b8; font-size: 16px; padding: 4px 8px; border-radius: 8px;
}
.icon-btn:hover { background-color: rgba(255, 255, 255, 0.1); color: #ffffff; }

.account-card {
    background-color: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    padding: 16px 18px;
    margin-bottom: 12px;
}
.account-card.muted { opacity: 0.6; }
.logo-fallback {
    font-size: 13px; font-weight: bold; color: #c084fc;
    background-color: rgba(168, 85, 247, 0.15);
    border-radius: 8px;
}
.acc-name { font-size: 15px; font-weight: bold; color: #f1f5f9; }
.acc-label { font-size: 12px; color: #64748b; }
.tray-label { font-size: 11px; font-weight: bold; color: #94a3b8; }

.credit-banner {
    background-color: rgba(59, 130, 246, 0.12);
    border: 1px solid rgba(59, 130, 246, 0.25);
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 11px;
    color: #93c5fd;
}

.quota-name { font-size: 12px; font-weight: bold; color: #cbd5e1; }
.quota-meta { font-size: 11px; color: #64748b; }
.quota-pct { font-size: 13px; font-weight: bold; }
.quota-pct.ok { color: #10b981; }
.quota-pct.warn { color: #f59e0b; }
.quota-pct.danger { color: #ef4444; }
.quota-pct.unknown { color: #64748b; }

progressbar trough { min-height: 8px; border-radius: 4px; background-color: rgba(255, 255, 255, 0.08); border: none; }
progressbar progress { min-height: 8px; border-radius: 4px; border: none; }
progressbar.ok progress { background-color: #10b981; }
progressbar.warn progress { background-color: #f59e0b; }
progressbar.danger progress { background-color: #ef4444; }
progressbar.unknown progress { background-color: #334155; }

switch:checked { background-color: #2563eb; border-color: #3b82f6; }
"""


def _cls(widget, *names):
    ctx = widget.get_style_context()
    for n in names:
        ctx.add_class(n)
    return widget


class QuotaTrackerWindow(Gtk.Window):
    def __init__(self, on_close_cb=None, on_tray_toggle_cb=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.on_close_cb = on_close_cb
        self.on_tray_toggle_cb = on_tray_toggle_cb
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_name("quota-tracker-window")
        self.set_app_paintable(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS_STYLE.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.build_ui()
        self.connect("key-press-event", self.on_key_press)
        self.refresh_view(force=False)
        GLib.timeout_add_seconds(60, self.on_timer_refresh)

    def build_ui(self):
        main_box = _cls(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0), "main-container")
        main_box.set_size_request(CARD_W, CARD_H)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        head_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        head_text.pack_start(_cls(Gtk.Label(label="Quota Limits", xalign=0), "header-title"), False, False, 0)
        self.lbl_sub = _cls(Gtk.Label(xalign=0), "header-subtitle")
        head_text.pack_start(self.lbl_sub, False, False, 0)
        header.pack_start(head_text, True, True, 0)

        btn_close = _cls(Gtk.Button(label="✕", tooltip_text="Close (Esc)"), "icon-btn")
        btn_close.connect("clicked", lambda b: self.close_card())
        header.pack_end(btn_close, False, False, 0)
        btn_refresh = _cls(Gtk.Button(label="⟳", tooltip_text="Refresh now"), "icon-btn")
        btn_refresh.connect("clicked", lambda b: self.refresh_view(force=True))
        header.pack_end(btn_refresh, False, False, 0)
        main_box.pack_start(header, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(CARD_H - 80)
        scroll.set_margin_top(16)
        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.add(self.cards_box)
        main_box.pack_start(scroll, True, True, 0)

        self.add(main_box)

    def on_timer_refresh(self):
        if self.get_visible():
            self.refresh_view(force=False)
        return True

    def refresh_view(self, force=False):
        self.data = quota_engine.get_all_accounts_data(force=force)
        self.render_cards()
        if self.on_tray_toggle_cb:
            self.on_tray_toggle_cb()

    def render_cards(self):
        for child in self.cards_box.get_children():
            self.cards_box.remove(child)

        mins_ago = int((time.time() - self.data.get("fetched_at", 0)) // 60)
        accounts = self.data.get("accounts", [])
        shown = sum(1 for a in accounts if a.get("show_in_tray"))
        self.lbl_sub.set_label(
            f"{len(accounts)} accounts · {shown} in tray · fetched {'just now' if mins_ago < 1 else f'{mins_ago}m ago'}"
        )

        if not accounts:
            empty = _cls(Gtk.Label(label="No accounts found."), "header-subtitle")
            empty.set_margin_top(40)
            self.cards_box.pack_start(empty, False, False, 0)
        for acc in accounts:
            self.cards_box.pack_start(self.create_account_card(acc), False, False, 0)
        self.cards_box.show_all()

    @staticmethod
    def provider_logo(acc):
        path = quota_engine.PROVIDER_LOGOS.get(acc["provider"])
        if path and os.path.exists(path):
            return Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(path, LOGO_PX, LOGO_PX, True))
        lbl = _cls(Gtk.Label(label=acc.get("slug", "?")), "logo-fallback")
        lbl.set_size_request(LOGO_PX, LOGO_PX)
        return lbl

    def create_account_card(self, acc):
        in_tray = acc.get("show_in_tray", False)
        card = _cls(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12), "account-card")
        if not in_tray:
            _cls(card, "muted")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.pack_start(self.provider_logo(acc), False, False, 0)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        slug = f"  [{acc['slug']}]" if acc.get("slug") else ""
        name = _cls(Gtk.Label(label=f"{acc['name']}{slug}", xalign=0), "acc-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        titles.pack_start(name, False, False, 0)
        desc = _cls(Gtk.Label(label=acc["email"] or acc["account_label"] or acc["provider"], xalign=0), "acc-label")
        desc.set_ellipsize(Pango.EllipsizeMode.END)
        titles.pack_start(desc, False, False, 0)
        top.pack_start(titles, True, True, 0)

        tray_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, valign=Gtk.Align.CENTER)
        tray_box.pack_start(_cls(Gtk.Label(label="Show in tray"), "tray-label"), False, False, 0)
        switch = Gtk.Switch(active=in_tray, valign=Gtk.Align.CENTER)
        switch.connect("notify::active", self.on_tray_switch, acc["id"])
        tray_box.pack_start(switch, False, False, 0)
        top.pack_end(tray_box, False, False, 0)
        card.pack_start(top, False, False, 0)

        if acc.get("reset_credits_available") is not None:
            banner = _cls(Gtk.Label(
                label=f"⚡ Reset credits: {acc['reset_credits_available']}  ·  next 5h window auto-starts after reset",
                xalign=0,
            ), "credit-banner")
            card.pack_start(banner, False, False, 0)

        for q in acc.get("quotas", []):
            card.pack_start(self.create_quota_row(q), False, False, 0)
        return card

    @staticmethod
    def create_quota_row(q):
        total = float(q.get("total") or 100)
        used = q.get("used")
        status = q.get("badge_status", "unknown")
        if used is None:
            left_fraction = 0.0
            left_text = "—"
        else:
            left_fraction = 1.0 if total <= 0 else max(0.0, 1.0 - float(used) / total)
            left_text = f"{left_fraction * 100:.0f}% left"

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.pack_start(_cls(Gtk.Label(label=q.get("name", "quota"), xalign=0), "quota-name"), True, True, 0)
        head.pack_end(_cls(Gtk.Label(label=left_text), "quota-pct", status), False, False, 0)
        box.pack_start(head, False, False, 0)

        bar = _cls(Gtk.ProgressBar(fraction=left_fraction), status)
        box.pack_start(bar, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        used_str = q.get("used_str") or (f"{used:g} / {total:g}" if isinstance(used, (int, float)) else f"— / {total:g}")
        meta.pack_start(_cls(Gtk.Label(label=f"Used {used_str}", xalign=0), "quota-meta"), True, True, 0)
        reset_str = q.get("reset_countdown") or "N/A"
        meta.pack_end(_cls(Gtk.Label(label=f"Reset {reset_str}"), "quota-meta"), False, False, 0)
        box.pack_start(meta, False, False, 0)
        return box

    def on_tray_switch(self, switch, _pspec, account_id):
        quota_engine.toggle_account_tray(account_id, switch.get_active())
        # Defer: callback re-renders cards, which destroys this switch mid-signal
        GLib.idle_add(self.after_tray_toggle)

    def after_tray_toggle(self):
        if self.on_tray_toggle_cb:
            self.on_tray_toggle_cb()  # tray redraw also re-renders this window
        else:
            self.refresh_view(force=False)
        return False

    def position_bottom_right(self):
        display = Gdk.Display.get_default()
        monitor = None
        if display:
            monitor = display.get_primary_monitor() or (display.get_monitor(0) if display.get_n_monitors() > 0 else None)
        self.show_all()
        if monitor:
            geom = monitor.get_geometry()
            alloc = self.get_allocation()
            pos_x = geom.x + geom.width - max(alloc.width, CARD_W) - 24
            pos_y = geom.y + geom.height - max(alloc.height, CARD_H) - 70
            self.move(pos_x, pos_y)

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close_card()
            return True
        return False

    def close_card(self):
        self.hide()
        if self.on_close_cb:
            self.on_close_cb()


if __name__ == "__main__":
    win = QuotaTrackerWindow(on_close_cb=lambda: Gtk.main_quit())
    win.position_bottom_right()
    win.show_all()
    Gtk.main()
