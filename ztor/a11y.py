"""Keep check boxes readable when Windows dark mode is on.

In dark mode wxWidgets draws check boxes itself, so Windows reports them to
screen readers as plain buttons with no checked state. Here each check box
gets an accessible object that reports the check box role and its checked,
focused and unavailable states again, and announces state changes. Light mode
keeps the native control untouched.
"""

import wx

from .graph import is_dark

ACC_SELF = getattr(wx, "ACC_SELF", 0)


def touch_available():
    """True when the computer has a touch screen."""
    try:
        import ctypes
        return ctypes.windll.user32.GetSystemMetrics(95) > 0  # SM_MAXIMUMTOUCHES
    except Exception:
        return False


def touch_mode(config):
    """0 automatic, 1 on, 2 off."""
    try:
        mode = int(config.get("touch_mode", 0) or 0)
    except (TypeError, ValueError):
        mode = 0
    if mode == 1:
        return True
    if mode == 2:
        return False
    return touch_available()


def windows_animations():
    """False when Windows animation effects are turned off."""
    try:
        import ctypes
        value = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(
                0x1042, 0, ctypes.byref(value), 0):  # client area animation
            return bool(value.value)
    except Exception:
        pass
    return True


def motion_enabled(config):
    """Animations only when the user wants them, Windows allows them, and no
    screen reader is running, so speech is never competing with movement."""
    if not config.get("animations", True) or not windows_animations():
        return False
    try:
        from .accessibility import announcer
        return not announcer().screen_reader_active()
    except Exception:
        return True


def dark_mode(window):
    try:
        if wx.SystemSettings.GetAppearance().IsDark():
            return True
    except Exception:
        pass
    try:
        return is_dark(window.GetBackgroundColour())
    except Exception:
        return False


if hasattr(wx, "Accessible"):
    class CheckBoxAccessible(wx.Accessible):
        def __init__(self, box):
            wx.Accessible.__init__(self, box)
            self.box = box

        def GetRole(self, child_id):
            if child_id != ACC_SELF:
                return (wx.ACC_NOT_IMPLEMENTED, 0)
            return (wx.ACC_OK, wx.ROLE_SYSTEM_CHECKBUTTON)

        def GetState(self, child_id):
            if child_id != ACC_SELF:
                return (wx.ACC_NOT_IMPLEMENTED, 0)
            state = wx.ACC_STATE_SYSTEM_FOCUSABLE
            try:
                if self.box.IsChecked():
                    state |= wx.ACC_STATE_SYSTEM_CHECKED
                if wx.Window.FindFocus() is self.box:
                    state |= wx.ACC_STATE_SYSTEM_FOCUSED
                if not self.box.IsEnabled():
                    state |= wx.ACC_STATE_SYSTEM_UNAVAILABLE
                if not self.box.IsShownOnScreen():
                    state |= wx.ACC_STATE_SYSTEM_INVISIBLE
            except RuntimeError:
                pass
            return (wx.ACC_OK, state)

        def GetName(self, child_id):
            if child_id != ACC_SELF:
                return (wx.ACC_NOT_IMPLEMENTED, "")
            try:
                return (wx.ACC_OK, self.box.GetLabelText())
            except RuntimeError:
                return (wx.ACC_NOT_IMPLEMENTED, "")

        def GetDefaultAction(self, child_id):
            if child_id != ACC_SELF:
                return (wx.ACC_NOT_IMPLEMENTED, "")
            try:
                return (wx.ACC_OK, "Uncheck" if self.box.IsChecked() else "Check")
            except RuntimeError:
                return (wx.ACC_NOT_IMPLEMENTED, "")
else:
    CheckBoxAccessible = None


def _walk(window):
    for child in window.GetChildren():
        yield child
        for grandchild in _walk(child):
            yield grandchild


def _on_toggle(event):
    box = event.GetEventObject()
    try:
        wx.Accessible.NotifyEvent(wx.ACC_EVENT_OBJECT_STATECHANGE, box,
                                  wx.OBJID_CLIENT, ACC_SELF)
    except Exception:
        pass
    event.Skip()


def fix_check_boxes(root, force=False):
    """Give every check box under root its state back. Returns how many."""
    if CheckBoxAccessible is None:
        return 0
    if not force and not dark_mode(root):
        return 0
    count = 0
    for child in _walk(root):
        if not isinstance(child, wx.CheckBox) or \
                getattr(child, "_mt_accessible", None) is not None:
            continue
        try:
            accessible = CheckBoxAccessible(child)
            child.SetAccessible(accessible)
            child._mt_accessible = accessible
            child.Bind(wx.EVT_CHECKBOX, _on_toggle)
            count += 1
        except Exception:
            continue
    return count


def menu_letter(label):
    """The access letter of a menu label, upper case, or "" if it has none.

    "S&top" gives "T". A doubled "&&" is a plain ampersand, not a letter.
    """
    text = label.split("\t", 1)[0]
    index = 0
    while index < len(text) - 1:
        if text[index] == "&":
            following = text[index + 1]
            if following == "&":
                index += 2
                continue
            return following.upper() if following.strip() else ""
        index += 1
    return ""


def add_menu_letters(menu):
    """Show each item's access letter after its name, as Start  S.

    Windows draws the text after a tab at the right edge of the menu, and
    NVDA and JAWS read it after the name, the way Ctrl+S is read for menu bar
    commands. Pressing the letter still works through the access key. Items
    that already show a shortcut keep it. Submenus are done too.
    """
    for item in menu.GetMenuItems():
        if item.IsSeparator():
            continue
        label = item.GetItemLabel()
        if "\t" not in label:
            letter = menu_letter(label)
            if letter:
                item.SetItemLabel(label + "\t" + letter)
        submenu = item.GetSubMenu()
        if submenu is not None:
            add_menu_letters(submenu)
    return menu
