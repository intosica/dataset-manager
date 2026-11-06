"""Imageboard results: thumbnail grid, viewer, and the save actions that use the same ImageSink as the grabber."""
from __future__ import annotations

import os
import tempfile

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from ..core.imaging import save_image
from ..core.sink import ImageSink
from ..core.sources import boards
from ..core.tasks import TaskContext
from .image_menu import attach_image_menu
from .widgets import run_background


def _square(path: str, size: int):
    """Centre-crop to a square so every tile has the same size (a Gtk.Picture would follow each thumbnail's own)."""
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    side = min(pb.get_width(), pb.get_height())
    pb = pb.new_subpixbuf((pb.get_width() - side) // 2, (pb.get_height() - side) // 2, side, side)
    return pb.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)


class BoardTile(Gtk.FlowBoxChild):
    def __init__(self, post: boards.BoardPost, state, on_view, on_save, on_save_as):
        super().__init__(css_classes=["card"], halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.post = post
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=False, width_request=176,
                          height_request=176, css_classes=["thumb"])
        if post.thumb_file and os.path.isfile(post.thumb_file):
            try:
                pic.set_pixbuf(_square(post.thumb_file, 176))
            except GLib.Error:
                pass
        score = Gtk.Label(label=f"★ {post.score}" if post.score is not None else "", halign=Gtk.Align.START,
                          valign=Gtk.Align.START, css_classes=["osd", "caption", "score-badge"],
                          visible=post.score is not None)
        overlay = Gtk.Overlay(child=pic, overflow=Gtk.Overflow.HIDDEN)
        overlay.add_overlay(score)
        label = Gtk.Label(label=f"#{post.post_id} · {len(post.tags)} tags", xalign=0,
                          ellipsize=Pango.EllipsizeMode.END, max_width_chars=16, css_classes=["caption"],
                          margin_start=4, margin_end=4)
        box.append(overlay)
        box.append(label)
        self.set_child(box)
        self.set_tooltip_text(", ".join(post.tags[:60]))
        self.set_cursor_from_name("pointer")

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("released", lambda *_a: on_view(post))
        self.add_controller(click)

        attach_image_menu(self, lambda: post.thumb_file, state, extras=lambda: [
            ("View", lambda: on_view(post)), ("Save to collection", lambda: on_save(post)),
            ("Save As…", lambda: on_save_as(post))])


class BoardViewer(Adw.Window):
    def __init__(self, post: boards.BoardPost, state, on_save, on_save_as, parent=None):
        subtitle = " · ".join(x for x in (
            post.rating.title() if post.rating else "", f"score {post.score}" if post.score is not None else "",
            f"{len(post.tags)} tags" if post.tags else "") if x)
        super().__init__(transient_for=parent, modal=True, title=f"{post.board.title()} #{post.post_id}",
                         default_width=1400, default_height=1000)
        self.post, self.state, self.on_save, self.on_save_as = post, state, on_save, on_save_as
        toolbar = Adw.ToolbarView()
        bar = Adw.HeaderBar()
        bar.set_title_widget(Adw.WindowTitle(title=f"{post.board.title()} #{post.post_id}", subtitle=subtitle))
        save = Gtk.Button(label="Save")
        save.connect("clicked", lambda _b: on_save(post))
        save_as = Gtk.Button(label="Save As…")
        save_as.connect("clicked", lambda _b: on_save_as(post))
        bar.pack_end(save_as)
        bar.pack_end(save)
        toolbar.add_top_bar(bar)

        self.stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.spinner = Adw.StatusPage(title="Loading image…", icon_name="image-loading-symbolic")
        self.spinner.set_child(Gtk.Spinner())
        # Fit the whole image to the dialog: CONTAIN + expanding picture, no scrollbars around the image.
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                   hexpand=True, vexpand=True)
        self.tags = Gtk.Label(label=", ".join(post.tags), wrap=True, selectable=True, xalign=0,
                              margin_start=12, margin_end=12, margin_bottom=8)
        tags_scroll = Gtk.ScrolledWindow(child=self.tags, min_content_height=56, max_content_height=112,
                                         hexpand=True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, vexpand=True)
        body.append(self.stack)
        body.append(tags_scroll)
        self.stack.add_named(self.spinner, "loading")
        self.stack.add_named(self.picture, "image")
        self.stack.set_visible_child_name("loading")
        toolbar.set_content(body)
        self.set_content(toolbar)
        self._closed, self._data = False, b""
        self.connect("close-request", self._on_close)
        attach_image_menu(self.picture, self._temp_file, state)
        state.open_in_tool.connect(lambda *_a: None if self._closed else self.close())
        self._fetch()

    def _temp_file(self) -> str | None:
        """The viewed image as a file, so it can be sent to the Aesthetic Scorer / Quick Caption."""
        if not self._data:
            return None
        d = os.path.join(tempfile.gettempdir(), "dataset-manager-viewed")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{self.post.board}_{self.post.post_id}.jpg")
        with open(path, "wb") as f:
            f.write(self._data)
        return path

    def _on_close(self, *_a):
        self._closed = True
        return False

    def _fetch(self) -> None:
        ctx = TaskContext(log=lambda m: GLib.idle_add(self.spinner.set_description, m))
        run_background(lambda: boards.fetch_image(self.post, self.state.settings, ctx), self._loaded, self._failed)

    def _loaded(self, data: bytes) -> None:
        if self._closed:
            return
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except Exception as exc:
            self._failed(str(exc))
            return
        self._data = data
        self.picture.set_paintable(texture)
        self.stack.set_visible_child_name("image")

    def _failed(self, err: str) -> None:
        if self._closed:
            return
        self.spinner.set_title("Could not load the image")
        self.spinner.set_description(str(err)[:500])
        self.spinner.set_icon_name("dialog-warning-symbolic")


class BoardResultsView(Gtk.Box):
    """The results panel. It owns saving (the page owns the query and folder callbacks)."""

    def __init__(self, state, panel, folder_fn, opts_fn, on_page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.state, self.panel = state, panel
        self.folder_fn, self.opts_fn, self.on_page = folder_fn, opts_fn, on_page
        self._page = None
        self._query = None
        self._offsets: list[int] = []
        self._idx = -1

        bar = Gtk.Box(spacing=6, margin_top=4)
        self.heading = Gtk.Label(xalign=0, hexpand=True, css_classes=["heading"])
        self.prev = Gtk.Button(label="Previous", valign=Gtk.Align.CENTER)
        self.next = Gtk.Button(label="Next", valign=Gtk.Align.CENTER)
        self.save_visible = Gtk.Button(label="Save Visible", valign=Gtk.Align.CENTER)
        self.save_all = Gtk.Button(label="Save All", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        for w in (self.heading, self.prev, self.next, self.save_visible, self.save_all):
            bar.append(w)
        self.append(bar)
        self.notice = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"], visible=False)
        self.append(self.notice)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=8,
                                row_spacing=8, margin_top=4, margin_bottom=8, margin_start=4, margin_end=4,
                                valign=Gtk.Align.START)
        self.flow.set_max_children_per_line(6)
        self.flow.set_min_children_per_line(1)
        self.grid = self.flow
        self.empty = Adw.StatusPage(icon_name="image-x-generic-symbolic", title="Search an imageboard",
                                    description="Pick a board, add positive or negative tags, then press Search.")
        self.loading = Adw.StatusPage(title="Searching…", icon_name="system-search-symbolic")
        self.loading.set_child(Gtk.Spinner())
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.loading, "loading")
        self.stack.add_named(self.grid, "grid")
        self.stack.set_visible_child_name("empty")
        self.append(self.stack)

        self.prev.connect("clicked", lambda _b: self._go(-1))
        self.next.connect("clicked", lambda _b: self._go(+1))
        self.save_visible.connect("clicked", lambda _b: self._save_many(self._visible_posts(), "visible"))
        self.save_all.connect("clicked", lambda _b: self._save_all())
        self._set_buttons(False)

    # ---- lifecycle ----
    def has_page(self) -> bool:
        return self._page is not None

    def clear(self) -> None:
        self._page = self._query = None
        self._offsets, self._idx = [], -1
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
        self.stack.set_visible_child_name("empty")
        self.empty.set_title("Search an imageboard")
        self.empty.set_description("Pick a board, add positive or negative tags, then press Search.")
        self.heading.set_text("")
        self.notice.set_visible(False)
        self._set_buttons(False)

    def set_loading(self, text: str = "Searching…") -> None:
        self.loading.set_title(text)
        self.stack.set_visible_child_name("loading")
        self._set_buttons(False)

    def show_error(self, message: str) -> None:
        self._page = None
        self.empty.set_title("Search failed")
        self.empty.set_description(str(message)[:1200])
        self.stack.set_visible_child_name("empty")
        self.heading.set_text("")
        self.notice.set_visible(False)
        self._set_buttons(False)

    def show(self, page: boards.BoardPage, query: boards.BoardQuery) -> None:
        self._page, self._query = page, query
        if page.offset in self._offsets:
            self._idx = self._offsets.index(page.offset)
        else:
            del self._offsets[self._idx + 1:]
            self._offsets.append(page.offset)
            self._idx = len(self._offsets) - 1
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
        for post in page.results:
            self.flow.append(BoardTile(post, self.state, self._view, self._save_one, self._save_as))
        self.notice.set_label(page.notice)
        self.notice.set_visible(bool(page.notice))
        self.heading.set_text(f"{query.label} · {query.positive or query.archetype or 'all posts'} · page {self._idx + 1}")
        if page.results:
            self.stack.set_visible_child_name("grid")
        else:
            self.empty.set_title("No results")
            self.empty.set_description("Try fewer tags or filters, another rating, or a lower score.")
            self.stack.set_visible_child_name("empty")
        self._set_buttons(True)
        self.prev.set_sensitive(self._idx > 0)
        self.next.set_sensitive(page.next_offset is not None and self._limit_left() > 0)
        if not page.results:
            self.next.set_sensitive(False)

    def _set_buttons(self, on: bool) -> None:
        for w in (self.save_visible, self.save_all, self.prev, self.next):
            w.set_sensitive(on)

    def _limit_left(self) -> int:
        if not self._page or not self._query or not self._query.limit:
            return 1
        return max(0, self._query.limit - self._page.offset)

    def _visible_posts(self) -> list[boards.BoardPost]:
        if not self._page:
            return []
        posts = self._page.results
        if self._query and self._query.limit:
            posts = posts[:max(0, self._query.limit - self._page.offset)]
        return posts

    def _go(self, step: int) -> None:
        if not self._page or not self._query:
            return
        if step < 0:
            if self._idx <= 0:
                return
            self.on_page(self._offsets[self._idx - 1])
        elif self._page.next_offset is not None:
            self.on_page(self._page.next_offset)

    # ---- actions ----
    def _view(self, post: boards.BoardPost) -> None:
        viewer = BoardViewer(post, self.state, self._save_one, self._save_as, self.get_root())
        viewer.connect("map", lambda w: w.maximize())
        viewer.present()

    def _folder(self):
        folder = self.folder_fn()
        if not folder:
            self.panel.log("Choose a collection name inside the library first.")
        return folder

    def _save_one(self, post: boards.BoardPost) -> None:
        folder = self._folder()
        if not folder or not self._query:
            return
        save_dir, tag = folder
        opts, q, s = self.opts_fn(), self._query, self.state.settings

        def job(ctx):
            sink = ImageSink(save_dir, tag, opts)
            sink.prepare()
            try:
                status = boards.save_result(q, post, sink, s, ctx, opts.write_captions)
            finally:
                sink.flush()
            return status, sink.summary()

        def done(r):
            self.panel.log(f"{post.board} #{post.post_id}: {r[0]} — {r[1]}")

        self.panel.run(job, done, name="Save imageboard image")

    def _save_as(self, post: boards.BoardPost) -> None:
        if not self._query:
            return
        dlg = Gtk.FileDialog(title="Save image as")
        dlg.set_initial_name(f"{post.board}_{post.post_id}.jpg")
        if self.state.library:
            dlg.set_initial_folder(Gio.File.new_for_path(self.state.library))

        def chosen(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            if path and not os.path.splitext(path)[1]:
                path += ".jpg"
            if path:
                self._save_as_job(post, path)
        dlg.save(self.get_root(), None, chosen)

    def _save_as_job(self, post: boards.BoardPost, path: str) -> None:
        q, s, opts = self._query, self.state.settings, self.opts_fn()

        def job(ctx):
            data = boards.fetch_image(post, s, ctx)
            from io import BytesIO

            from PIL import Image
            with Image.open(BytesIO(data)) as im:
                img = im.convert("RGB")
            save_image(img, path)
            if opts.write_captions:
                text = boards.caption_for(q, post)
                if text:
                    with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as f:
                        f.write(text)
            return path

        self.panel.run(job, lambda p: self.panel.log(f"Saved {p}"), name="Save imageboard image as")

    def _save_many(self, posts, what: str) -> None:
        if not posts:
            self.panel.log("Nothing to save on this page.")
            return
        folder = self._folder()
        if not folder or not self._query:
            return
        save_dir, tag = folder
        q, s, opts = self._query, self.state.settings, self.opts_fn()

        def job(ctx):
            sink = ImageSink(save_dir, tag, opts)
            sink.prepare()
            try:
                boards.save_all(q, posts, sink, s, ctx, opts.write_captions)
            finally:
                sink.flush()
            return sink.summary()

        self.panel.run(job, lambda summary: self.panel.log(f"Saved {what}: {summary}"), name="Save imageboard")

    def _save_all(self) -> None:
        if not self._query:
            return
        folder = self._folder()
        if not folder:
            return
        save_dir, tag = folder
        q, s, opts = self._query, self.state.settings, self.opts_fn()

        def job(ctx):
            posts, offset = [], 0
            while True:
                if ctx.cancelled:
                    break
                page = boards.search_page(q, offset, s, ctx, thumbs=False)
                posts += page.results
                if q.limit and len(posts) >= q.limit:
                    break
                if page.next_offset is None or page.next_offset <= offset:
                    break
                offset = page.next_offset
            if q.limit:
                posts = posts[:q.limit]
            sink = ImageSink(save_dir, tag, opts)
            sink.prepare()
            try:
                boards.save_all(q, posts, sink, s, ctx, opts.write_captions)
            finally:
                sink.flush()
            return sink.summary()

        self.panel.run(job, lambda summary: self.panel.log(f"Saved all pages: {summary}"), name="Save all imageboard pages")
