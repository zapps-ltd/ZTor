"""A speed history graph and a progress strip, drawn as pictures.

Both are static bitmaps, which Windows never puts in the Tab order, so they
can not trap the keyboard, and they are not native progress bars, so screen
readers do not beep. The same numbers are always available as text: the
summary box above the graph, the Speak Speed Summary command, and the list.
"""

import collections

import wx

from . import format as fmt


def is_dark(colour):
    try:
        lum = 0.299 * colour.Red() + 0.587 * colour.Green() + \
            0.114 * colour.Blue()
    except Exception:
        return False
    return lum < 128


def _span_text(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return "%d seconds" % seconds
    return "%d minutes" % round(seconds / 60.0)


class SpeedHistory(object):
    """The last few minutes of session download and upload rates."""

    def __init__(self, size=300):
        self.samples = collections.deque(maxlen=size)
        self.seconds_per_sample = 1.0

    def add(self, down, up):
        self.samples.append((max(0, int(down or 0)), max(0, int(up or 0))))

    def peak(self):
        if not self.samples:
            return 0
        return max(max(d, u) for d, u in self.samples)

    def summary(self):
        if not self.samples:
            return "No speed readings yet."
        downs = [d for d, _u in self.samples]
        ups = [u for _d, u in self.samples]
        span = _span_text(len(self.samples) * self.seconds_per_sample)

        def part(name, values):
            now = fmt.human_speed(values[-1]) or "0 B/s"
            avg = fmt.human_speed(sum(values) / float(len(values))) or "0 B/s"
            peak = fmt.human_speed(max(values)) or "0 B/s"
            return "%s %s now, average %s, peak %s." % (name, now, avg, peak)

        return "%s %s Over the last %s." % (part("Download", downs),
                                            part("Upload", ups), span)


def _nice_ceiling(value):
    if value <= 0:
        return 1024
    step = 1024
    while step * 10 < value:
        step *= 10
    for factor in (1, 2, 5, 10):
        if step * factor >= value:
            return step * factor
    return value


class _Drawn(wx.StaticBitmap):
    """A picture redrawn to fill its space. Never takes keyboard focus."""

    def __init__(self, parent, min_size, name):
        wx.StaticBitmap.__init__(self, parent, bitmap=wx.Bitmap(4, 4),
                                 size=min_size)
        self.SetMinSize(min_size)
        self.SetName(name)
        self._last_size = (0, 0)
        self.Bind(wx.EVT_SIZE, self._on_size)

    def _on_size(self, event):
        event.Skip()
        size = tuple(self.GetClientSize())
        if size != self._last_size:
            wx.CallAfter(self.redraw)

    def redraw(self):
        try:
            width, height = self.GetClientSize()
        except RuntimeError:
            return
        if width < 4 or height < 4:
            return
        self._last_size = (width, height)
        background = self.GetParent().GetBackgroundColour()
        bitmap = wx.Bitmap(width, height)
        dc = wx.MemoryDC(bitmap)
        dc.SetBackground(wx.Brush(background))
        dc.Clear()
        gc = wx.GraphicsContext.Create(dc)
        if gc is not None:
            self.paint(gc, width, height, is_dark(background))
            gc.Flush()
            del gc
        dc.SelectObject(wx.NullBitmap)
        self.SetBitmap(bitmap)

    def paint(self, gc, width, height, dark):
        raise NotImplementedError


class SpeedGraph(_Drawn):
    """Download and upload lines over time."""

    def __init__(self, parent, history):
        self.history = history
        _Drawn.__init__(self, parent, (200, 120), "Speed graph")

    def paint(self, gc, width, height, dark):
        left, right, top, bottom = 70, 10, 10, 26
        plot_w = max(10, width - left - right)
        plot_h = max(10, height - top - bottom)
        text_colour = wx.Colour(220, 220, 220) if dark else wx.Colour(60, 60, 60)
        grid_colour = wx.Colour(80, 80, 80) if dark else wx.Colour(215, 215, 215)
        down_colour = wx.Colour(110, 210, 130) if dark else wx.Colour(20, 140, 60)
        up_colour = wx.Colour(120, 175, 255) if dark else wx.Colour(30, 100, 200)
        gc.SetFont(self.GetFont(), text_colour)

        top_value = _nice_ceiling(self.history.peak())
        gc.SetPen(wx.Pen(grid_colour, 1))
        for step in range(5):
            y = top + plot_h * step / 4.0
            gc.StrokeLine(left, y, left + plot_w, y)
            label = fmt.human_speed(top_value * (4 - step) / 4.0) or "0 B/s"
            tw, th = gc.GetTextExtent(label)
            gc.DrawText(label, left - tw - 6, y - th / 2.0)

        samples = list(self.history.samples)
        capacity = self.history.samples.maxlen or len(samples) or 1
        if len(samples) >= 2:
            for index, colour in ((0, down_colour), (1, up_colour)):
                path = gc.CreatePath()
                for n, sample in enumerate(samples):
                    x = left + plot_w * (capacity - len(samples) + n) / \
                        float(max(1, capacity - 1))
                    y = top + plot_h - plot_h * sample[index] / float(top_value)
                    if n == 0:
                        path.MoveToPoint(x, y)
                    else:
                        path.AddLineToPoint(x, y)
                gc.SetPen(wx.Pen(colour, 2))
                gc.StrokePath(path)

        legend_y = top + plot_h + 6
        x = left
        for name, colour in (("Download", down_colour), ("Upload", up_colour)):
            gc.SetPen(wx.Pen(colour, 3))
            gc.StrokeLine(x, legend_y + 7, x + 18, legend_y + 7)
            gc.DrawText(name, x + 24, legend_y)
            tw, _th = gc.GetTextExtent(name)
            x += tw + 50


class ProgressStrip(_Drawn):
    """A thin drawn progress bar for the selected torrent. It glides to a
    new value when animate is on, and jumps straight there when it is off."""

    FRAME_MS = 30

    def __init__(self, parent):
        self.value = 0.0        # where it is heading
        self.shown = 0.0        # what is drawn now
        self.label = ""
        self.animate = False
        _Drawn.__init__(self, parent, (100, 18),
                        "Selected torrent progress, for display only")
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_tick, self.timer)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)

    def set(self, fraction, label=""):
        fraction = max(0.0, min(1.0, float(fraction or 0.0)))
        if abs(fraction - self.value) < 0.0005 and label == self.label:
            return
        self.value = fraction
        self.label = label
        if self.animate and abs(fraction - self.shown) > 0.002:
            if not self.timer.IsRunning():
                self.timer.Start(self.FRAME_MS)
            return
        self.shown = fraction
        self.redraw()

    def _on_tick(self, event):
        gap = self.value - self.shown
        if abs(gap) < 0.002:
            self.shown = self.value
            self.timer.Stop()
        else:
            self.shown += gap * 0.3
        self.redraw()

    def _on_destroy(self, event):
        if event.GetEventObject() is self:
            self.timer.Stop()
        event.Skip()

    def paint(self, gc, width, height, dark):
        track = wx.Colour(70, 70, 70) if dark else wx.Colour(225, 225, 225)
        fill = wx.Colour(60, 170, 90) if dark else wx.Colour(40, 150, 70)
        gc.SetPen(wx.TRANSPARENT_PEN)
        gc.SetBrush(wx.Brush(track))
        gc.DrawRoundedRectangle(0, 2, width, height - 4, 4)
        if self.shown > 0:
            gc.SetBrush(wx.Brush(fill))
            gc.DrawRoundedRectangle(0, 2, max(6, width * self.shown),
                                    height - 4, 4)
        if self.label:
            gc.SetFont(self.GetFont(),
                       wx.Colour(255, 255, 255) if dark or self.shown > 0.5
                       else wx.Colour(30, 30, 30))
            tw, th = gc.GetTextExtent(self.label)
            gc.DrawText(self.label, (width - tw) / 2.0, (height - th) / 2.0)
