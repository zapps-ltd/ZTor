"""Screen reader support.

Speech goes to NVDA through its controller client DLL, or to JAWS through its
FreedomSci.JawsApi COM object (needs the comtypes package, or pywin32), in that
order. When neither reader is running, announcements fall back to the status
bar text set by the main window, so nothing is ever lost silently.
"""

import ctypes
import os
import sys
import time

from . import APP_NAME

PROBE_SECONDS = 5.0


def _jaws_window_present():
    try:
        return bool(ctypes.windll.user32.FindWindowW("JFWUI2", None))
    except Exception:
        return False


def high_contrast():
    """True when Windows high contrast is on, so custom colours stay off."""
    class HIGHCONTRAST(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint),
                    ("lpszDefaultScheme", ctypes.c_wchar_p)]
    try:
        info = HIGHCONTRAST()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.SystemParametersInfoW(
                0x0042, info.cbSize, ctypes.byref(info), 0):
            return bool(info.dwFlags & 1)
    except Exception:
        pass
    return False


class Announcer(object):
    """Speaks short status messages without stealing keyboard focus."""

    def __init__(self):
        self.enabled = True
        self._lib = None
        self._jaws = None
        self._dll_path = None
        self._reason = "not loaded"
        self._last_probe = 0.0

    # ------------------------------------------------------------------ NVDA
    def _candidate_paths(self):
        names = ("nvdaControllerClient64.dll", "nvdaControllerClient32.dll",
                 "nvdaControllerClient.dll")
        roots = [
            os.path.dirname(os.path.abspath(__file__)),
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            sys.prefix,
            os.path.join(sys.prefix, "DLLs"),
        ]
        appdata = os.environ.get("APPDATA", "")
        python_dir = os.path.join(appdata, "Python")
        if os.path.isdir(python_dir):
            for entry in sorted(os.listdir(python_dir)):
                roots.append(os.path.join(python_dir, entry, "site-packages",
                                          "accessible_output2", "lib"))
        for root in roots:
            for name in names:
                candidate = os.path.join(root, name)
                if os.path.isfile(candidate):
                    yield candidate

    def _load_nvda(self):
        for path in self._candidate_paths():
            try:
                lib = ctypes.WinDLL(path)
                lib.nvdaController_testIfRunning.restype = ctypes.c_long
                lib.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
                lib.nvdaController_speakText.restype = ctypes.c_long
                lib.nvdaController_cancelSpeech.restype = ctypes.c_long
            except Exception as exc:  # pragma: no cover - depends on host
                self._reason = "NVDA client load failed: %s" % exc
                continue
            try:
                running = lib.nvdaController_testIfRunning()
            except Exception:
                running = -1
            if running == 0:
                self._lib = lib
                self._dll_path = path
                self._reason = "NVDA controller client at %s" % path
                return True
            self._reason = "NVDA not running (code %s)" % running
        return False

    # ------------------------------------------------------------------ JAWS
    def _load_jaws(self):
        if not _jaws_window_present():
            return False
        api = None
        try:
            import comtypes.client
            api = comtypes.client.CreateObject("FreedomSci.JawsApi")
        except Exception:
            api = None
        if api is None:
            try:
                import win32com.client
                api = win32com.client.Dispatch("FreedomSci.JawsApi")
            except Exception:
                api = None
        if api is None:
            self._reason = ("JAWS is running but comtypes is not installed; "
                            "run build.bat to add it")
            return False
        self._jaws = api
        self._reason = "JAWS API"
        return True

    # ------------------------------------------------------------- setup
    def load(self, force=False):
        """Find a running reader. Probing is limited to once every few seconds."""
        if self._lib is not None or self._jaws is not None:
            return True
        now = time.monotonic()
        if not force and now - self._last_probe < PROBE_SECONDS:
            return False
        self._last_probe = now
        if self._load_nvda():
            return True
        return self._load_jaws()

    @property
    def backend(self):
        return self._reason

    def screen_reader_active(self):
        """True when NVDA or JAWS is running, used to skip duplicate toasts."""
        if self.load():
            return True
        return _jaws_window_present()

    # ------------------------------------------------------------- speaking
    def speak(self, text):
        if not text or not self.enabled:
            return False
        if not self.load():
            return False
        if self._lib is not None:
            try:
                if self._lib.nvdaController_speakText(text) == 0:
                    return True
            except Exception:
                pass
            # NVDA quit since it was found; look again next time.
            self._lib = None
        if self._jaws is not None:
            try:
                self._jaws.SayString(text, False)
                return True
            except Exception:
                self._jaws = None
        return False

    def silence(self):
        if self._lib is not None:
            try:
                self._lib.nvdaController_cancelSpeech()
            except Exception:
                self._lib = None
        if self._jaws is not None:
            try:
                self._jaws.StopSpeech()
            except Exception:
                self._jaws = None


_announcer = None


def announcer():
    global _announcer
    if _announcer is None:
        _announcer = Announcer()
    return _announcer


def appdata_dir():
    """The portable data folder, app\\data. The name is kept for old callers."""
    from .paths import data_dir
    return data_dir()


def sub_dir(name):
    path = os.path.join(appdata_dir(), name)
    os.makedirs(path, exist_ok=True)
    return path
