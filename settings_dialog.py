"""GTK settings window for the indicator.

Runs in-process (same GTK main loop as the indicator). On *Save* it writes
``settings.json`` via :func:`settings.save_settings` and calls back into the
indicator so the change applies immediately — no daemon restart. Alerts are
not edited here (they stay in the JSON); the *Edit JSON* button opens the
raw file for that.

Only the widgets the GUI manages are collected on save; the rest of the
:class:`~settings.Settings` (notably ``alerts``) is carried over untouched.
"""

from __future__ import annotations

import dataclasses
from typing import Callable

from gi.repository import GLib, Gtk

import topbar
from settings import (
    SCHEMA_VERSION,
    Settings,
    TopbarSettings,
    VALID_CLAUDE_TOPBAR_METRICS,
    sanitize_separator,
    save_settings,
)
from strings import SUPPORTED_LANGS, t

# Representative numbers used only to render the live preview.
_SAMPLE_CLAUDE = {
    "status": "ok",
    "data": {
        "five_hour": {"utilization": 24},
        "seven_day": {"utilization": 5},
        "seven_day_sonnet": {"utilization": 12},
    },
}


class SettingsDialog(Gtk.Window):
    def __init__(
        self,
        settings: Settings,
        *,
        on_save: Callable[[], None],
        on_close: Callable[[], None],
        on_edit_json: Callable[[], None],
        on_update: Callable[[], None] | None = None,
        on_uninstall: Callable[[bool], None] | None = None,
        update_info: object | None = None,
    ) -> None:
        super().__init__(title=t("dlg_title"))
        self.settings = settings
        self._on_save = on_save
        self._on_close = on_close
        self._on_edit_json = on_edit_json
        self._on_update = on_update
        self._on_uninstall = on_uninstall
        # Duck-typed: an updates.UpdateInfo (current / latest / available).
        self._update_info = update_info

        self.set_border_width(12)
        self.set_default_size(440, -1)
        self.set_resizable(False)
        self.connect("destroy", lambda _w: self._on_close())

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(outer)

        notebook = Gtk.Notebook()
        outer.pack_start(notebook, True, True, 0)
        notebook.append_page(
            self._build_general_tab(), Gtk.Label(label=t("dlg_tab_general"))
        )
        notebook.append_page(
            self._build_topbar_tab(), Gtk.Label(label=t("dlg_tab_topbar"))
        )
        notebook.append_page(
            self._build_accounts_tab(), Gtk.Label(label=t("dlg_tab_accounts"))
        )
        notebook.append_page(
            self._build_maintenance_tab(),
            Gtk.Label(label=t("dlg_tab_maintenance")),
        )

        outer.pack_start(self._build_preview(), False, False, 0)
        outer.pack_start(self._build_footer(), False, False, 0)
        outer.pack_start(self._build_buttons(), False, False, 0)

        self._update_preview()

    # ------------------------------------------------------------ tab: general

    def _build_general_tab(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.set_border_width(12)
        row = 0

        grid.attach(self._label(t("dlg_lang")), 0, row, 1, 1)
        self.cmb_lang = Gtk.ComboBoxText()
        for code in SUPPORTED_LANGS:
            self.cmb_lang.append(code, t(f"dlg_lang_{code}"))
        active = self.settings.lang if self.settings.lang in SUPPORTED_LANGS else "fr"
        self.cmb_lang.set_active_id(active)
        grid.attach(self.cmb_lang, 1, row, 1, 1)
        row += 1

        grid.attach(self._label(t("dlg_poll")), 0, row, 1, 1)
        self.spin_poll = Gtk.SpinButton.new_with_range(10, 3600, 10)
        self.spin_poll.set_value(self.settings.poll_seconds)
        grid.attach(self.spin_poll, 1, row, 1, 1)
        row += 1

        grid.attach(self._label(t("dlg_thresholds")), 0, row, 1, 1)
        self.ent_thresholds = Gtk.Entry()
        self.ent_thresholds.set_text(
            ", ".join(str(x) for x in self.settings.builtin_thresholds)
        )
        grid.attach(self.ent_thresholds, 1, row, 1, 1)
        row += 1

        grid.attach(self._dim(t("dlg_thresholds_hint")), 1, row, 1, 1)
        return grid

    # ------------------------------------------------------------- tab: top-bar

    def _build_topbar_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)
        tb = self.settings.topbar

        # Claude segment + metric picks.
        self.chk_show_claude = self._check(t("dlg_show_claude"), tb.show_claude)
        box.pack_start(self.chk_show_claude, False, False, 0)
        self.claude_metric_checks = self._metric_checks(
            VALID_CLAUDE_TOPBAR_METRICS, tb.claude_metrics, box
        )

        box.pack_start(Gtk.Separator(), False, False, 4)

        # Format toggles.
        self.chk_prefix = self._check(
            t("dlg_provider_prefix"), tb.show_provider_prefix
        )
        box.pack_start(self.chk_prefix, False, False, 0)
        self.chk_alert_prefix = self._check(
            t("dlg_alert_prefix"), tb.show_alert_prefix
        )
        box.pack_start(self.chk_alert_prefix, False, False, 0)
        self.chk_metric_labels = self._check(
            t("dlg_metric_labels"), tb.metric_labels
        )
        box.pack_start(self.chk_metric_labels, False, False, 0)
        self.chk_compact = self._check(t("dlg_compact"), tb.compact)
        box.pack_start(self.chk_compact, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        row = 0
        grid.attach(self._label(t("dlg_metric_separator")), 0, row, 1, 1)
        self.ent_msep = Gtk.Entry(text=tb.metric_separator)
        self.ent_msep.set_max_length(8)
        self.ent_msep.connect("changed", self._on_change)
        grid.attach(self.ent_msep, 1, row, 1, 1)
        row += 1

        grid.attach(self._label(t("dlg_percent_decimals")), 0, row, 1, 1)
        self.spin_decimals = Gtk.SpinButton.new_with_range(0, 2, 1)
        self.spin_decimals.set_value(tb.percent_decimals)
        self.spin_decimals.connect("value-changed", self._on_change)
        grid.attach(self.spin_decimals, 1, row, 1, 1)
        box.pack_start(grid, False, False, 0)

        return box

    # ------------------------------------------------------------ tab: accounts

    def _build_accounts_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)
        self.chk_accounts_enabled = self._check(
            t("dlg_accounts_enabled"), self.settings.accounts_enabled
        )
        box.pack_start(self.chk_accounts_enabled, False, False, 0)
        box.pack_start(
            self._dim(t("dlg_accounts_enabled_hint")), False, False, 0
        )
        box.pack_start(Gtk.Separator(), False, False, 4)
        self.chk_account_switch = self._check(
            t("dlg_account_switch_enabled"), self.settings.account_switch_enabled
        )
        box.pack_start(self.chk_account_switch, False, False, 0)
        box.pack_start(
            self._dim(t("dlg_account_switch_hint")), False, False, 0
        )
        return box

    # ------------------------------------------------------------ tab: maintenance

    def _ui_current(self) -> str:
        info = self._update_info
        return getattr(info, "current", None) or "?"

    def _ui_latest(self) -> str:
        info = self._update_info
        return getattr(info, "latest", None) or "?"

    def _ui_update_available(self) -> bool:
        return bool(getattr(self._update_info, "available", False))

    def _build_maintenance_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)

        box.pack_start(
            self._label(t("dlg_version", ver=self._ui_current())), False, False, 0
        )

        if self._ui_update_available():
            box.pack_start(
                self._label(t("dlg_update_available", ver=self._ui_latest())),
                False,
                False,
                0,
            )
            btn_update = Gtk.Button(label=t("dlg_update_btn"))
            btn_update.get_style_context().add_class("suggested-action")
            btn_update.set_halign(Gtk.Align.START)
            btn_update.connect("clicked", lambda _b: self._trigger_update())
            box.pack_start(btn_update, False, False, 0)
        else:
            box.pack_start(
                self._dim(t("dlg_update_uptodate")), False, False, 0
            )

        self.chk_update_check = self._check(
            t("dlg_update_check_enabled"), self.settings.update_check_enabled
        )
        box.pack_start(self.chk_update_check, False, False, 0)
        box.pack_start(
            self._dim(t("dlg_update_check_hint")), False, False, 0
        )

        box.pack_start(Gtk.Separator(), False, False, 6)

        btn_uninstall = Gtk.Button(label=t("dlg_uninstall_btn"))
        btn_uninstall.get_style_context().add_class("destructive-action")
        btn_uninstall.set_halign(Gtk.Align.START)
        btn_uninstall.connect("clicked", lambda _b: self._on_uninstall_clicked())
        box.pack_start(btn_uninstall, False, False, 0)
        return box

    # ----------------------------------------------------------------- footer

    def _build_footer(self) -> Gtk.Widget:
        """Small always-visible row at the bottom: update status."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if self._ui_update_available():
            btn = Gtk.Button(
                label=t("dlg_footer_update", ver=self._ui_latest())
            )
            btn.get_style_context().add_class("suggested-action")
            btn.connect("clicked", lambda _b: self._trigger_update())
            box.pack_start(btn, False, False, 0)
        else:
            box.pack_start(
                self._dim(t("dlg_footer_uptodate", ver=self._ui_current())),
                False,
                False,
                0,
            )
        return box

    def _trigger_update(self) -> None:
        """Close the window, then hand off to the daemon's update flow."""
        cb = self._on_update
        self.destroy()
        if cb is not None:
            cb()

    def _on_uninstall_clicked(self) -> None:
        if self._on_uninstall is None:
            return
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=t("dlg_uninstall_confirm_title"),
        )
        dlg.format_secondary_text(t("dlg_uninstall_confirm_body"))
        purge_chk = Gtk.CheckButton(label=t("dlg_uninstall_purge_check"))
        dlg.get_content_area().pack_start(purge_chk, False, False, 6)
        purge_chk.show()
        resp = dlg.run()
        purge = purge_chk.get_active()
        dlg.destroy()
        if resp == Gtk.ResponseType.OK:
            cb = self._on_uninstall
            self.destroy()
            cb(purge)

    # --------------------------------------------------------------- preview

    def _build_preview(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.pack_start(self._label(t("dlg_preview")), False, False, 0)
        self.preview = Gtk.Label()
        self.preview.set_selectable(True)
        self.preview.set_halign(Gtk.Align.START)
        box.pack_start(self.preview, False, False, 0)
        return box

    def _build_buttons(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_json = Gtk.Button(label=t("dlg_edit_json"))
        btn_json.connect("clicked", lambda _b: self._on_edit_json())
        box.pack_start(btn_json, False, False, 0)

        btn_cancel = Gtk.Button(label=t("dlg_cancel"))
        btn_cancel.connect("clicked", lambda _b: self.destroy())
        box.pack_end(btn_cancel, False, False, 0)

        btn_save = Gtk.Button(label=t("dlg_save"))
        btn_save.get_style_context().add_class("suggested-action")
        btn_save.connect("clicked", self._handle_save)
        box.pack_end(btn_save, False, False, 0)
        return box

    # ---------------------------------------------------------------- widgets

    @staticmethod
    def _label(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_halign(Gtk.Align.START)
        return lbl

    @staticmethod
    def _dim(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_line_wrap(True)
        lbl.get_style_context().add_class("dim-label")
        return lbl

    def _check(self, text: str, active: bool) -> Gtk.CheckButton:
        chk = Gtk.CheckButton(label=text)
        chk.set_active(active)
        chk.connect("toggled", self._on_change)
        return chk

    def _metric_checks(
        self,
        valid: tuple[str, ...],
        selected: tuple[str, ...],
        parent: Gtk.Box,
    ) -> dict[str, Gtk.CheckButton]:
        """Build indented per-metric checkboxes and return them keyed by id."""
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        inner.set_margin_start(22)
        checks: dict[str, Gtk.CheckButton] = {}
        for metric in valid:
            chk = self._check(t(f"dlg_metric_{metric}"), metric in selected)
            checks[metric] = chk
            inner.pack_start(chk, False, False, 0)
        parent.pack_start(inner, False, False, 0)
        return checks

    # ----------------------------------------------------------------- logic

    def _on_change(self, *_args: object) -> None:
        self._update_preview()

    def _collect_topbar(self) -> TopbarSettings:
        claude_metrics = tuple(
            m for m, chk in self.claude_metric_checks.items() if chk.get_active()
        )
        return TopbarSettings(
            show_claude=self.chk_show_claude.get_active(),
            claude_metrics=claude_metrics,
            show_provider_prefix=self.chk_prefix.get_active(),
            show_alert_prefix=self.chk_alert_prefix.get_active(),
            metric_labels=self.chk_metric_labels.get_active(),
            compact=self.chk_compact.get_active(),
            # Mirror the loader's sanitization so the preview matches what
            # actually gets saved (non-ASCII is stripped; empty → default).
            metric_separator=sanitize_separator(self.ent_msep.get_text(), " . "),
            percent_decimals=int(self.spin_decimals.get_value()),
        )

    def _collect_thresholds(self) -> tuple[int, ...]:
        out: list[int] = []
        for part in self.ent_thresholds.get_text().split(","):
            part = part.strip()
            if not part:
                continue
            try:
                v = int(part)
            except ValueError:
                continue
            if 1 <= v <= 100 and v not in out:
                out.append(v)
        return tuple(out) if out else self.settings.builtin_thresholds

    def _collect_settings(self) -> Settings:
        # ``dataclasses.replace`` carries over fields the GUI doesn't manage
        # (alerts), so they survive a save untouched.
        return dataclasses.replace(
            self.settings,
            schema_version=SCHEMA_VERSION,
            lang=self.cmb_lang.get_active_id() or "fr",
            poll_seconds=int(self.spin_poll.get_value()),
            builtin_thresholds=self._collect_thresholds(),
            update_check_enabled=self.chk_update_check.get_active(),
            accounts_enabled=self.chk_accounts_enabled.get_active(),
            account_switch_enabled=self.chk_account_switch.get_active(),
            topbar=self._collect_topbar(),
        )

    def _update_preview(self) -> None:
        tb = self._collect_topbar()
        body, _ = topbar.compose_label(_SAMPLE_CLAUDE, tb, has_alerts=False)
        if not (body and body.strip()):
            text = t("dlg_preview_icon_only")
        else:
            text = body
        self.preview.set_markup(
            "<tt>" + GLib.markup_escape_text(text) + "</tt>"
        )

    def _handle_save(self, _btn: Gtk.Button) -> None:
        new_settings = self._collect_settings()
        try:
            save_settings(new_settings)
        except OSError as e:
            self._error(t("dlg_save_failed_title"), str(e))
            return
        # Close first (clears the indicator's window ref via the destroy
        # signal), then apply — so a hiccup in apply can't strand a window
        # the user can no longer reopen.
        self.destroy()
        self._on_save()

    def _error(self, title: str, detail: str) -> None:
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dlg.format_secondary_text(detail)
        dlg.run()
        dlg.destroy()
