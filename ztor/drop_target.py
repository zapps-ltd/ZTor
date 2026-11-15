"""Drop target so .torrent files and magnet links can be dragged onto the list."""

import os

import wx


class TorrentDropTarget(wx.FileDropTarget):
    def __init__(self, on_files):
        wx.FileDropTarget.__init__(self)
        self.on_files = on_files

    def OnDropFiles(self, x, y, filenames):
        self.on_files(list(filenames))
        return True
