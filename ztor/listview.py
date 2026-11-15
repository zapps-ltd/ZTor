"""The torrent list control.

ListView virtual mode is used so that only the rows on screen are ever asked
for their text. Screen readers then read exactly what Windows asks for, with no
owner drawn cells and no hidden state.
"""

import wx


class TorrentList(wx.ListCtrl):
    def __init__(self, parent, owner, style=0):
        wx.ListCtrl.__init__(
            self, parent,
            style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.BORDER_SUNKEN | style)
        self.owner = owner

    # The three hooks wxWidgets calls for a virtual list.
    def OnGetItemText(self, item, column):
        return self.owner.get_item_text(item, column)

    def OnGetItemImage(self, item):
        return self.owner.get_item_image(item)

    def OnGetItemAttr(self, item):
        return self.owner.get_item_attr(item)

    # Keyboard support that some screen readers rely on.
    def supports_key(self, event):
        return True
