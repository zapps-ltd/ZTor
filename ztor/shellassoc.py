"""Windows integration: open magnet links and .torrent files with ZTor.

Everything goes under HKEY_CURRENT_USER, so no administrator rights are
needed and other Windows accounts are not touched. It is off by default and
only written when the user turns it on, to keep the app portable.

Windows 10 and 11 keep the user's own choice of default app in a protected
UserChoice key that programs may not write. When another app holds that
choice, ZTor registers itself as a candidate and opens the Windows
Default apps page so the user can pick it there.
"""

import os
import sys

from . import APP_NAME

PROGID_TORRENT = "ZTor.torrent"
PROGID_MAGNET = "ZTor.magnet"
CLASSES = r"Software\Classes"
SOFTWARE = r"Software\ZTor"
REGISTERED = r"Software\RegisteredApplications"
SETTINGS_URI = "ms-settings:defaultapps?registeredAppUser=" + APP_NAME


def _winreg():
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def available():
    return sys.platform == "win32" and _winreg() is not None


def launcher():
    """(program, script) that starts ZTor. script is None when built."""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable), None
    program = os.path.abspath(sys.executable)
    folder, name = os.path.split(program)
    if name.lower() == "python.exe":
        windowed = os.path.join(folder, "pythonw.exe")
        if os.path.exists(windowed):
            program = windowed
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return program, os.path.join(root, "main.py")


def open_command(program=None, script=None):
    """The command line Windows runs, with %1 for the file or link."""
    if program is None:
        program, script = launcher()
    if script:
        return '"%s" "%s" "%%1"' % (program, script)
    return '"%s" "%%1"' % program


def icon_value(program=None):
    if program is None:
        program = launcher()[0]
    return '"%s",0' % program


def _set(winreg, path, name, value):
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0,
                             winreg.KEY_WRITE)
    try:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    finally:
        winreg.CloseKey(key)


def _get(winreg, path, name=""):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return None
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None
    finally:
        winreg.CloseKey(key)


def _delete_value(winreg, path, name):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                             winreg.KEY_SET_VALUE)
    except OSError:
        return
    try:
        winreg.DeleteValue(key, name)
    except OSError:
        pass
    finally:
        winreg.CloseKey(key)


def _delete_tree(winreg, path):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                             winreg.KEY_READ)
    except OSError:
        return
    try:
        children = []
        index = 0
        while True:
            try:
                children.append(winreg.EnumKey(key, index))
            except OSError:
                break
            index += 1
    finally:
        winreg.CloseKey(key)
    for child in children:
        _delete_tree(winreg, path + "\\" + child)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _notify():
    """Tell Explorer the associations changed."""
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


def register(classes=CLASSES, software=SOFTWARE, registered=REGISTERED,
             command=None, icon=None):
    """Write the associations. Returns (ok, message)."""
    winreg = _winreg()
    if winreg is None:
        return False, "Windows integration is only available on Windows."
    command = command or open_command()
    icon = icon or icon_value()
    try:
        # File type for .torrent.
        prog = classes + "\\" + PROGID_TORRENT
        _set(winreg, prog, "", "Torrent file")
        _set(winreg, prog + r"\DefaultIcon", "", icon)
        _set(winreg, prog + r"\shell", "", "open")
        _set(winreg, prog + r"\shell\open\command", "", command)
        ext = classes + r"\.torrent"
        _set(winreg, ext, "", PROGID_TORRENT)
        _set(winreg, ext, "Content Type", "application/x-bittorrent")
        _set(winreg, ext + r"\OpenWithProgids", PROGID_TORRENT, "")
        # Link type for magnet.
        for path, name in ((classes + "\\" + PROGID_MAGNET, "ZTor magnet link"),
                           (classes + r"\magnet", "URL:Magnet link")):
            _set(winreg, path, "", name)
            _set(winreg, path, "URL Protocol", "")
            _set(winreg, path + r"\DefaultIcon", "", icon)
            _set(winreg, path + r"\shell", "", "open")
            _set(winreg, path + r"\shell\open\command", "", command)
        # Capabilities, so ZTor is listed in Windows Default apps.
        caps = software + r"\Capabilities"
        _set(winreg, caps, "ApplicationName", APP_NAME)
        _set(winreg, caps, "ApplicationDescription",
             "Accessible portable BitTorrent client")
        _set(winreg, caps, "ApplicationIcon", icon)
        _set(winreg, caps + r"\FileAssociations", ".torrent", PROGID_TORRENT)
        _set(winreg, caps + r"\URLAssociations", "magnet", PROGID_MAGNET)
        _set(winreg, registered, APP_NAME, caps)
    except OSError as exc:
        return False, "Could not register with Windows: %s" % exc
    _notify()
    return True, "ZTor is registered for magnet links and .torrent files."


def unregister(classes=CLASSES, software=SOFTWARE, registered=REGISTERED):
    """Remove only what ZTor wrote. Returns (ok, message)."""
    winreg = _winreg()
    if winreg is None:
        return False, "Windows integration is only available on Windows."
    ext = classes + r"\.torrent"
    if _get(winreg, ext) == PROGID_TORRENT:
        _delete_value(winreg, ext, "")
    _delete_value(winreg, ext + r"\OpenWithProgids", PROGID_TORRENT)
    magnet = classes + r"\magnet"
    if _is_ours(_get(winreg, magnet + r"\shell\open\command")):
        _delete_tree(winreg, magnet)
    _delete_tree(winreg, classes + "\\" + PROGID_TORRENT)
    _delete_tree(winreg, classes + "\\" + PROGID_MAGNET)
    _delete_value(winreg, registered, APP_NAME)
    _delete_tree(winreg, software)
    _notify()
    return True, "ZTor no longer opens magnet links and .torrent files."


def _is_ours(command):
    if not command:
        return False
    program, script = launcher()
    text = command.lower()
    target = (script or program).lower()
    return target in text


def registered_command(classes=CLASSES):
    winreg = _winreg()
    if winreg is None:
        return None
    return _get(winreg, classes + "\\" + PROGID_TORRENT + r"\shell\open\command")


def needs_refresh(classes=CLASSES):
    """True when the app folder moved since it was registered."""
    current = registered_command(classes)
    return current is not None and current != open_command()


def _assoc_command(name, protocol):
    """The command Windows really uses, UserChoice included."""
    try:
        import ctypes
        from ctypes import wintypes
        shlwapi = ctypes.WinDLL("shlwapi")
        shlwapi.AssocQueryStringW.argtypes = [
            ctypes.c_uint, ctypes.c_uint, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        size = wintypes.DWORD(2048)
        buffer = ctypes.create_unicode_buffer(size.value)
        flags = 0x1000 if protocol else 0  # ASSOCF_IS_PROTOCOL
        result = shlwapi.AssocQueryStringW(flags, 1, name, "open", buffer,
                                           ctypes.byref(size))
        return buffer.value if result == 0 else ""
    except Exception:
        return ""


def status():
    """{'torrent': bool, 'magnet': bool}: does Windows open them with us?"""
    return {"torrent": _is_ours(_assoc_command(".torrent", False)),
            "magnet": _is_ours(_assoc_command("magnet", True))}


def status_text(state):
    missing = [name for key, name in (("magnet", "magnet links"),
                                      ("torrent", ".torrent files"))
               if not state.get(key)]
    if not missing:
        return "Windows opens magnet links and .torrent files with ZTor."
    return ("Windows still opens %s with another app. Choose ZTor for "
            "them on the Windows Default apps page." % " and ".join(missing))


def open_default_apps():
    try:
        os.startfile(SETTINGS_URI)
        return True
    except Exception:
        try:
            os.startfile("ms-settings:defaultapps")
            return True
        except Exception:
            return False
