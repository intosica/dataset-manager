from __future__ import annotations

import os

from gi.repository import Adw, GLib, Gtk

from ..core import capabilities
from ..core.duplicates import HASH_BITS
from ..core.organizer import OrganizeOptions, organize_library
from .duplicate_review import DuplicateReview
from .widgets import CaptionEditor, ModelPicker, TaskPanel, hint

DIRECTIONS = [("Enlarge and shrink", "both"), ("Enlarge only", "up"), ("Shrink only", "down")]
DUP_MODES = ["Review the groups first", "Move copies to the Trash automatically"]
CAPTION_MODES = [("Add where missing", "add"), ("Append missing tags to existing captions", "append"),
                 ("Overwrite all captions", "overwrite")]


def _step(title: str, subtitle: str, enabled: bool) -> Adw.ExpanderRow:
    """A step with its own on/off switch; its settings are inside the expander."""
    row = Adw.ExpanderRow(title=title, subtitle=subtitle, show_enable_switch=True, enable_expansion=enabled,
                          expanded=enabled)
    return row


def _spin(title, lo, hi, step, value, subtitle="") -> Adw.SpinRow:
    row = Adw.SpinRow.new_with_range(lo, hi, step)
    row.set_title(title)
    row.set_subtitle(subtitle)
    row.set_value(value)
    return row


class MaintenancePage(Gtk.Box):
    """Every step is optional; switched-off steps are skipped entirely."""

    def __init__(self, state):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        s = state.settings

        self.g_dup = _step("Remove duplicate images", "Finds look-alike images; copies go to the Trash", False)
        self.dup_thr = _spin("Similarity (0 = exact copies only)", 0, 20, 1, s.duplicate_threshold,
                             f"Differing bits out of {HASH_BITS}; 6 is recommended")
        self.dup_mode = Adw.ComboRow(title="Copies", model=Gtk.StringList.new(DUP_MODES),
                                     subtitle="Reviewing happens after the other steps; you pick what to remove")
        for w in (self.dup_thr, self.dup_mode):
            self.g_dup.add_row(w)

        self.sort = Adw.SwitchRow(title="Sort into Landscape / Portrait / Square folders", active=True)
        self.rename = Adw.SwitchRow(title="Clean file names", subtitle="lowercase_with_underscores", active=True)
        self.to_jpg = Adw.SwitchRow(title="Convert images to JPEG", active=True,
                                    subtitle="With sorting off files stay where they are. Captions follow their image; "
                                             "JPEG files are moved without re-encoding.")
        g_files = Adw.PreferencesGroup(title="Files and folders")
        for w in (self.sort, self.rename, self.to_jpg):
            g_files.add(w)

        self.g_resize = _step("Resize images", "Scales every image in the folder", False)
        self.target = _spin("Longer side (px)", 64, 8192, 64, s.target_dim)
        self.direction = Adw.ComboRow(title="Direction", model=Gtk.StringList.new([d[0] for d in DIRECTIONS]))
        self.direction.set_selected(max(0, [d[1] for d in DIRECTIONS].index(s.scale_direction)
                                        if s.scale_direction in [d[1] for d in DIRECTIONS] else 0))
        self.snap = Adw.SwitchRow(title="Round sizes to multiples of 64", active=s.snap_to_64)
        for w in (self.target, self.direction, self.snap, ModelPicker(state)):
            self.g_resize.add_row(w)

        note = "Images under 400 px are always flagged; flagged images go to a low_quality folder when sorting is on."
        if not capabilities.has("transformers"):
            note += " The aesthetic model needs: pip install transformers"
        self.g_low = _step("Flag low-quality images", note, False)
        self.strict = _spin("Strictness (%)", 20, 80, 1, 45)
        self.g_low.add_row(self.strict)

        self.g_cap = _step("Caption files (.txt)", "Create or update a caption next to every image", True)
        self.cap_mode = Adw.ComboRow(title="Existing captions", model=Gtk.StringList.new([m[0] for m in CAPTION_MODES]))
        self.g_cap.add_row(self.cap_mode)
        self.cap_editor = CaptionEditor()
        self.cap_editor.load(s.caption_spec())

        self.steps = steps = Adw.PreferencesGroup(title="Steps")
        for w in (self.g_dup, self.g_resize, self.g_low, self.g_cap):
            steps.add(w)

        self.run_btn = Gtk.Button(label="Run Maintenance")
        self.run_btn.add_css_class("suggested-action")
        self.run_btn.add_css_class("pill")
        self.cancel_btn = Gtk.Button(label="Cancel", sensitive=False)
        self.cancel_btn.add_css_class("pill")
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        buttons.append(self.cancel_btn)
        buttons.append(self.run_btn)
        self.panel = TaskPanel(min_height=110)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=18,
                          margin_start=12, margin_end=12)
        self.review = DuplicateReview(state, self.panel)
        for w in (steps, self.review, g_files, self.cap_editor):
            content.append(w)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    child=Adw.Clamp(maximum_size=860, child=content))
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=18, margin_end=18,
                         margin_bottom=12, margin_top=6)
        bottom.append(buttons)
        bottom.append(self.panel)
        self.append(Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True, resize_end_child=False,
                              shrink_start_child=False, shrink_end_child=False, start_child=scroll, end_child=bottom,
                              position=540))

        self.run_btn.connect("clicked", lambda _b: self.run())
        self.cancel_btn.connect("clicked", lambda _b: self.panel.cancel())
        self.panel.on_busy(lambda b: (self.run_btn.set_sensitive(not b), self.cancel_btn.set_sensitive(b)))
        state.settings_changed.connect(self._reload)
        for sig in (state.library_selected, state.library_changed):
            sig.connect(lambda *_a: self._update_scope())
        self._update_scope()

    def scope_dir(self) -> str:
        """The highlighted collection on the Library page, else the whole library."""
        sel, lib = self.state.selected_collection, self.state.library
        return sel if sel and os.path.isdir(sel) and os.path.abspath(sel).startswith(os.path.abspath(lib)) else lib

    def _update_scope(self) -> None:
        t = self.scope_dir()
        what = ("the whole library" if t == self.state.library else f"the collection “{os.path.basename(t)}”")
        self.steps.set_description(f"Runs on {what} (pick another on the Library page). "
                                   "Switched-off steps are skipped.")

    def _reload(self) -> None:
        if not self.panel.busy:
            self.cap_editor.load(self.state.settings.caption_spec())

    def run(self) -> None:
        if self.panel.busy:
            return
        s = self.state.settings
        spec = self.cap_editor.spec()
        s.caption_template, s.good_quality_tags = spec.template, spec.good_tags
        s.low_quality_tags, s.extra_tags = spec.low_tags, spec.extra_tags
        direction = DIRECTIONS[self.direction.get_selected()][1]
        s.target_dim, s.scale_direction = int(self.target.get_value()), direction
        s.snap_to_64, s.duplicate_threshold = self.snap.get_active(), int(self.dup_thr.get_value())
        self.state.save()
        opts = OrganizeOptions(
            remove_duplicates=self.g_dup.get_enable_expansion(), duplicate_threshold=int(self.dup_thr.get_value()),
            detect_low_quality=self.g_low.get_enable_expansion(), low_quality_strictness=self.strict.get_value() / 100,
            sort_by_orientation=self.sort.get_active(), clean_filenames=self.rename.get_active(),
            convert_to_jpg=self.to_jpg.get_active(), resize=self.g_resize.get_enable_expansion(),
            target_dim=int(self.target.get_value()), direction=direction, snap_to_64=self.snap.get_active(),
            caption_mode=CAPTION_MODES[self.cap_mode.get_selected()][1] if self.g_cap.get_enable_expansion() else "off",
            caption=spec)
        base, thr = self.scope_dir(), opts.duplicate_threshold
        review = self.g_dup.get_enable_expansion() and self.dup_mode.get_selected() == 0
        opts.remove_duplicates = self.g_dup.get_enable_expansion() and not review

        def done(_r):
            self.state.content_changed.emit()
            if review:
                GLib.idle_add(self.review.scan, base, thr)
        if opts.remove_duplicates or opts.per_image_steps():
            self.panel.run(lambda ctx: organize_library(base, opts, ctx), done, with_trash=True,
                           name="Organizing " + os.path.basename(base.rstrip("/")))
        elif review:
            self.review.scan(base, thr)
        else:
            self.panel.log("No steps selected.")
