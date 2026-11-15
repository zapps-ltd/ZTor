"""Icons drawn at runtime with anti aliasing. No image files are shipped.

Each drawing works on a 24 unit grid and is scaled to the requested size, on a
fully transparent bitmap so it looks right on light and dark toolbars.
"""

import math

import wx


COLORS = {
    "app": (30, 100, 190),
    "down": (25, 140, 70),
    "up": (35, 115, 210),
    "paused": (130, 130, 130),
    "error": (200, 45, 45),
    "orange": (235, 130, 20),
    "gray": (110, 110, 110),
    "teal": (0, 140, 150),
    "white": (255, 255, 255),
}

TOOL_KINDS = ("add_file", "add_link", "search", "create", "start", "pause",
              "remove", "queue_up", "queue_down", "alt_speed", "rss", "prefs")

# The torrent list image list, in this order; see MainFrame.get_item_image.
STATE_KINDS = ("paused", "down", "up", "error", "queued", "checking",
               "finished", "metadata")

# Other icons: filters, Preferences pages, the action bar.
EXTRA_KINDS = ("all", "tag", "folder", "clock", "lock", "phone", "display",
               "access", "globe")


def _colour(name, alpha=255):
    r, g, b = COLORS.get(name, (90, 90, 90))
    return wx.Colour(r, g, b, alpha)


def _fill(gc, name):
    gc.SetPen(wx.TRANSPARENT_PEN)
    gc.SetBrush(wx.Brush(_colour(name)))


def _line(gc, name, width):
    gc.SetBrush(wx.TRANSPARENT_BRUSH)
    pen = wx.Pen(_colour(name), max(1, int(round(width))))
    pen.SetCap(wx.CAP_ROUND)
    pen.SetJoin(wx.JOIN_ROUND)
    gc.SetPen(pen)


def _polygon(gc, points, s):
    path = gc.CreatePath()
    x, y = points[0]
    path.MoveToPoint(x * s, y * s)
    for x, y in points[1:]:
        path.AddLineToPoint(x * s, y * s)
    path.CloseSubpath()
    # Fills with the brush and outlines with the pen; _fill sets no pen.
    gc.DrawPath(path)


def _paint(kind, gc, size):
    s = size / 24.0
    if kind in ("down", "queue_down"):
        _fill(gc, "down" if kind == "down" else "up")
        _polygon(gc, [(12, 21), (3, 11), (8.5, 11), (8.5, 3), (15.5, 3),
                      (15.5, 11), (21, 11)], s)
    elif kind in ("up", "queue_up"):
        _fill(gc, "up")
        _polygon(gc, [(12, 3), (3, 13), (8.5, 13), (8.5, 21), (15.5, 21),
                      (15.5, 13), (21, 13)], s)
    elif kind in ("paused", "pause"):
        _fill(gc, "paused" if kind == "paused" else "orange")
        gc.DrawRoundedRectangle(5 * s, 4 * s, 5 * s, 16 * s, 1.5 * s)
        gc.DrawRoundedRectangle(14 * s, 4 * s, 5 * s, 16 * s, 1.5 * s)
    elif kind == "start":
        _fill(gc, "down")
        _polygon(gc, [(6, 3), (21, 12), (6, 21)], s)
    elif kind == "error":
        _fill(gc, "error")
        gc.DrawEllipse(1 * s, 1 * s, 22 * s, 22 * s)
        _line(gc, "white", 3 * s)
        gc.StrokeLine(8 * s, 8 * s, 16 * s, 16 * s)
        gc.StrokeLine(16 * s, 8 * s, 8 * s, 16 * s)
    elif kind == "remove":
        _line(gc, "error", 4 * s)
        gc.StrokeLine(5 * s, 5 * s, 19 * s, 19 * s)
        gc.StrokeLine(19 * s, 5 * s, 5 * s, 19 * s)
    elif kind == "add_file":
        gc.SetBrush(wx.Brush(_colour("white")))
        gc.SetPen(wx.Pen(_colour("gray"), max(1, int(round(1.5 * s)))))
        _polygon(gc, [(4, 2), (14, 2), (19, 7), (19, 22), (4, 22)], s)
        gc.StrokeLine(14 * s, 2 * s, 14 * s, 7 * s)
        gc.StrokeLine(14 * s, 7 * s, 19 * s, 7 * s)
        _fill(gc, "down")
        gc.DrawEllipse(11 * s, 11 * s, 13 * s, 13 * s)
        _line(gc, "white", 2.5 * s)
        gc.StrokeLine(17.5 * s, 14 * s, 17.5 * s, 21 * s)
        gc.StrokeLine(14 * s, 17.5 * s, 21 * s, 17.5 * s)
    elif kind == "add_link":
        _line(gc, "error", 4.5 * s)
        path = gc.CreatePath()
        path.MoveToPoint(6 * s, 6 * s)
        path.AddLineToPoint(6 * s, 12 * s)
        path.AddCurveToPoint(6 * s, 21 * s, 18 * s, 21 * s, 18 * s, 12 * s)
        path.AddLineToPoint(18 * s, 6 * s)
        gc.StrokePath(path)
        _fill(gc, "gray")
        gc.DrawRectangle(3.5 * s, 2 * s, 5 * s, 5 * s)
        gc.DrawRectangle(15.5 * s, 2 * s, 5 * s, 5 * s)
    elif kind == "search":
        _line(gc, "gray", 3 * s)
        gc.DrawEllipse(3 * s, 3 * s, 12 * s, 12 * s)
        _line(gc, "gray", 4 * s)
        gc.StrokeLine(13.5 * s, 13.5 * s, 20.5 * s, 20.5 * s)
    elif kind == "create":
        _fill(gc, "orange")
        gc.DrawRoundedRectangle(3 * s, 8 * s, 18 * s, 14 * s, 2 * s)
        gc.DrawRoundedRectangle(2 * s, 4 * s, 20 * s, 5 * s, 1.5 * s)
        _line(gc, "white", 2 * s)
        gc.StrokeLine(12 * s, 4 * s, 12 * s, 22 * s)
        gc.StrokeLine(2 * s, 9 * s, 22 * s, 9 * s)
    elif kind == "alt_speed":
        _line(gc, "teal", 2.5 * s)
        gc.DrawEllipse(2.5 * s, 2.5 * s, 19 * s, 19 * s)
        _line(gc, "teal", 2.5 * s)
        gc.StrokeLine(12 * s, 12 * s, 7 * s, 8 * s)
        _fill(gc, "teal")
        gc.DrawEllipse(10 * s, 10 * s, 4 * s, 4 * s)
    elif kind == "rss":
        _fill(gc, "orange")
        gc.DrawRoundedRectangle(1 * s, 1 * s, 22 * s, 22 * s, 4 * s)
        _fill(gc, "white")
        gc.DrawEllipse(4.5 * s, 15.5 * s, 4 * s, 4 * s)
        _line(gc, "white", 2.5 * s)
        for radius in (7.5, 12.5):
            path = gc.CreatePath()
            path.MoveToPoint(5.5 * s, (17.5 - radius) * s)
            path.AddCurveToPoint((5.5 + radius * 0.55) * s, (17.5 - radius) * s,
                                 (5.5 + radius) * s, (17.5 - radius * 0.55) * s,
                                 (5.5 + radius) * s, 17.5 * s)
            gc.StrokePath(path)
    elif kind == "prefs":
        _fill(gc, "gray")
        centre = 12 * s
        for tooth in range(8):
            angle = tooth * math.pi / 4.0
            gc.PushState()
            gc.Translate(centre, centre)
            gc.Rotate(angle)
            gc.DrawRectangle(-2 * s, -11 * s, 4 * s, 6 * s)
            gc.PopState()
        gc.DrawEllipse(4.5 * s, 4.5 * s, 15 * s, 15 * s)
        _fill(gc, "white")
        gc.DrawEllipse(9 * s, 9 * s, 6 * s, 6 * s)
    elif kind in ("queued", "clock"):
        _line(gc, "gray" if kind == "queued" else "teal", 2.5 * s)
        gc.DrawEllipse(2.5 * s, 2.5 * s, 19 * s, 19 * s)
        gc.StrokeLine(12 * s, 12 * s, 12 * s, 6.5 * s)
        gc.StrokeLine(12 * s, 12 * s, 16 * s, 14 * s)
    elif kind == "checking":
        _line(gc, "orange", 3 * s)
        path = gc.CreatePath()
        path.MoveToPoint(19 * s, 12 * s)
        path.AddCurveToPoint(19 * s, 21 * s, 5 * s, 21 * s, 5 * s, 12 * s)
        path.AddCurveToPoint(5 * s, 4 * s, 15 * s, 3 * s, 18 * s, 7 * s)
        gc.StrokePath(path)
        _fill(gc, "orange")
        _polygon(gc, [(20.5, 3), (20.5, 10), (14, 8)], s)
    elif kind == "finished":
        _fill(gc, "down")
        gc.DrawEllipse(1 * s, 1 * s, 22 * s, 22 * s)
        _line(gc, "white", 3 * s)
        path = gc.CreatePath()
        path.MoveToPoint(6.5 * s, 12.5 * s)
        path.AddLineToPoint(10.5 * s, 16.5 * s)
        path.AddLineToPoint(17.5 * s, 8 * s)
        gc.StrokePath(path)
    elif kind == "metadata":
        _line(gc, "gray", 2 * s)
        gc.DrawEllipse(2 * s, 2 * s, 20 * s, 20 * s)
        _fill(gc, "gray")
        for x in (6.5, 11, 15.5):
            gc.DrawEllipse(x * s, 10.5 * s, 3 * s, 3 * s)
    elif kind == "all":
        _fill(gc, "app")
        for y in (4, 10.5, 17):
            gc.DrawRoundedRectangle(3 * s, y * s, 18 * s, 3.5 * s, 1.5 * s)
    elif kind == "tag":
        _fill(gc, "teal")
        _polygon(gc, [(2, 4), (12, 4), (22, 13), (13, 22), (2, 12)], s)
        _fill(gc, "white")
        gc.DrawEllipse(5.5 * s, 7 * s, 3.5 * s, 3.5 * s)
    elif kind == "folder":
        _fill(gc, "orange")
        _polygon(gc, [(2, 5), (9, 5), (11, 7.5), (22, 7.5), (22, 20), (2, 20)], s)
        _fill(gc, "white")
        gc.DrawRectangle(2 * s, 10 * s, 20 * s, 1.2 * s)
    elif kind == "lock":
        _line(gc, "gray", 2.5 * s)
        gc.DrawRoundedRectangle(7 * s, 3 * s, 10 * s, 12 * s, 5 * s)
        _fill(gc, "orange")
        gc.DrawRoundedRectangle(4 * s, 10 * s, 16 * s, 12 * s, 2 * s)
    elif kind == "phone":
        _line(gc, "gray", 2.5 * s)
        gc.DrawRoundedRectangle(6 * s, 2 * s, 12 * s, 20 * s, 2.5 * s)
        _fill(gc, "gray")
        gc.DrawEllipse(10.5 * s, 17 * s, 3 * s, 3 * s)
    elif kind == "display":
        _line(gc, "app", 2.5 * s)
        gc.DrawRoundedRectangle(2.5 * s, 3.5 * s, 19 * s, 13 * s, 1.5 * s)
        gc.StrokeLine(12 * s, 17 * s, 12 * s, 20.5 * s)
        gc.StrokeLine(7 * s, 20.5 * s, 17 * s, 20.5 * s)
    elif kind == "access":
        _fill(gc, "app")
        gc.DrawEllipse(9.5 * s, 1.5 * s, 5 * s, 5 * s)
        _line(gc, "app", 2.5 * s)
        gc.StrokeLine(4 * s, 9 * s, 20 * s, 9 * s)
        gc.StrokeLine(12 * s, 9 * s, 12 * s, 15 * s)
        gc.StrokeLine(12 * s, 15 * s, 8 * s, 22 * s)
        gc.StrokeLine(12 * s, 15 * s, 16 * s, 22 * s)
    elif kind == "globe":
        _line(gc, "teal", 2 * s)
        gc.DrawEllipse(2 * s, 2 * s, 20 * s, 20 * s)
        gc.DrawEllipse(7.5 * s, 2 * s, 9 * s, 20 * s)
        gc.StrokeLine(2 * s, 12 * s, 22 * s, 12 * s)
    else:   # the application icon
        _fill(gc, "app")
        gc.DrawRoundedRectangle(1 * s, 1 * s, 22 * s, 22 * s, 5 * s)
        _fill(gc, "white")
        _polygon(gc, [(12, 18), (6, 11), (10, 11), (10, 4), (14, 4),
                      (14, 11), (18, 11)], s)
        gc.DrawRectangle(6 * s, 19 * s, 12 * s, 2 * s)


def _bitmap(kind, size=16):
    bitmap = wx.Bitmap.FromRGBA(size, size, 0, 0, 0, 0)
    dc = wx.MemoryDC(bitmap)
    gc = wx.GraphicsContext.Create(dc)
    if gc is not None:
        try:
            gc.SetAntialiasMode(wx.ANTIALIAS_DEFAULT)
        except Exception:
            pass
        _paint(kind, gc, float(size))
        gc.Flush()
        del gc
    dc.SelectObject(wx.NullBitmap)
    return bitmap


def tool_bitmap(kind, size=24):
    return _bitmap(kind, size)


def make_icon(kind):
    try:
        icon = wx.Icon()
        icon.CopyFromBitmap(_bitmap(kind, 32))
        return icon
    except Exception:
        return None


def image_list(kinds, size):
    """An image list of the given kinds, in order, at size pixels."""
    try:
        images = wx.ImageList(size, size, False, len(kinds))
        for kind in kinds:
            images.Add(_bitmap(kind, size))
        return images
    except Exception:
        return None


def make_image_list(size=16):
    """Torrent list icons, in STATE_KINDS order; see get_item_image."""
    return image_list(STATE_KINDS, size)
