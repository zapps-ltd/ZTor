# Third-party notices

ZTor is licensed under the GNU Affero General Public License, version 3 or (at your option) any later version (AGPL-3.0-or-later). Copyright (C) 2026 ZAPPS SOFTWARE LTD. It uses the third-party components below. Each keeps its own license. The licenses were checked against the packages installed for ZTor and against the upstream projects.

## Components that ZTor loads or ships

### libtorrent-rasterbar and its Python bindings

- Author: Arvid Norberg and contributors.
- Used for: the whole BitTorrent engine (version 2.1.1, from the libtorrent Python package).
- License: BSD-3-Clause (BSD 3-Clause "New" or "Revised" License). The package metadata says "BSD"; the upstream license text has three conditions.
- URL: https://libtorrent.org and https://github.com/arvidn/libtorrent
- Bundled parts inside libtorrent, under their own terms: the Python bindings under the Boost Software License 1.0; an ed25519 implementation by Orson Peters (permissive notice); SHA-1 by Steve Reid and SHA-256 adapted from LibTomCrypt (public domain); puff.c by Mark Adler (permissive, see its header). Upstream also carries a BSD-style routing header derived from Apple and Berkeley code; it is used for platforms other than Windows and is not part of the Windows build as far as we can tell.
- Notice to keep with copies: "Copyright (c) 2003-2020, Arvid Norberg. All rights reserved. Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met: Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution. Neither the name of the author nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission." The full text, with the disclaimer, is at https://github.com/arvidn/libtorrent/blob/RC_2_0/LICENSE and must accompany binary builds.

### Boost

- Author: the Boost community.
- Used for: C++ libraries compiled into libtorrent (and its Python bindings).
- License: BSL-1.0 (Boost Software License 1.0). Binary-only distribution needs no notice; source copies keep the license text.
- URL: https://www.boost.org

### OpenSSL

- Author: The OpenSSL Project authors.
- Used for: encryption and hashing inside libtorrent. The libtorrent module installed for ZTor is linked with OpenSSL 3.6.1.
- License: Apache-2.0 (Apache License 2.0). Copies must carry the license text and the copyright and attribution notices of OpenSSL.
- URL: https://www.openssl.org

### wxPython and wxWidgets

- Author: Robin Dunn, Scott Talbert and contributors (wxPython); Julian Smart, Vadim Zeitlin and the wxWidgets team (wxWidgets).
- Used for: the whole window, menus, lists, dialogs, tray icon and accessibility objects (wxPython 4.3.2, which contains wxWidgets 3.3).
- License: LGPL-2.0-or-later WITH WxWindows-exception-3.1 (the wxWindows Library Licence, version 3.1). The license file ships in the wxPython package (LICENSE.txt). The exception allows distributing binaries built with the library under your own terms.
- URL: https://wxpython.org and https://www.wxwidgets.org
- Also inside the wxPython package: Microsoft WebView2Loader.dll (BSD-3-Clause), the SIP runtime by Riverbank Computing (BSD-2-Clause), and libcairo (LGPL-2.1 or MPL-1.1). ZTor does not use the web view or drawing parts that need them, and does not modify them. wxPython also depends on NumPy (BSD-3-Clause), which ZTor itself does not import.

### comtypes

- Author: Thomas Heller and the comtypes developers.
- Used for: speech through JAWS (its scripting COM object). Optional; ZTor works without it and falls back to the status bar.
- License: MIT. Copies must keep this notice: "Copyright (c) 2006-2013, Thomas Heller. Copyright (c) 2014, Comtypes Developers. All rights reserved. Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software." The full text with the warranty disclaimer is in the file LICENSE.txt of the comtypes package.
- URL: https://github.com/enthought/comtypes

### Python

- Author: the Python Software Foundation and contributors.
- Used for: the language and runtime, also inside the built program.
- License: PSF-2.0 (Python Software Foundation License Version 2). Copies of the runtime keep its license text.
- URL: https://www.python.org

### PyInstaller (only the built program)

- Author: the PyInstaller development team.
- Used for: packing ZTor into ZTor.exe and its app folder. Its bootloader is embedded in the launcher.
- License: GPL-2.0-or-later WITH the PyInstaller bootloader exception, which allows the bootloader to be embedded in a program of any license. Its run-time hooks are Apache-2.0.
- URL: https://pyinstaller.org

## Optional run-time items that are not shipped

- NVDA controller client (nvdaControllerClient DLL), by NV Access, LGPL-2.1. ZTor loads it for speech only if it is already present on the computer, for example from a Python package. ZTor does not ship or copy it.
- pywin32, optional fallback for JAWS speech if comtypes is missing. PSF-2.0. Not shipped.
- JAWS (Freedom Scientific) and NVDA (NV Access) themselves are separate programs that the user installs.

## Data and services, nothing copied

- Public tracker list: by default ZTor downloads the list "trackers_best.txt" from the ngosang/trackerslist project (GPL-2.0 on that repository) at run time, and caches it. Only tracker addresses are read as data. No code or file of that project is part of ZTor, and the user can change the address or turn the feature off. A short built-in list of well known public tracker addresses is the fallback.
- DHT bootstrap nodes (libtorrent, BitTorrent, uTorrent, Transmission and Aelitis routers) are network addresses used to join the DHT network. Nothing is copied.
- Internet Archive search (archive.org) and an optional, user provided Jackett or Prowlarr (Torznab) address are online services that ZTor talks to over their public interfaces. Nothing from them is shipped.
- The classic BitTorrent and uTorrent column order and the names of some columns are followed for familiarity. Only a behaviour and common terms are followed; no code, text, image or sound of those programs, or of qBittorrent, Deluge or any other client, is used.
- Icons are drawn by ZTor's own code at run time, and the program icon is generated by ZTor's own script. No image, sound, word list or translation file from another project is shipped.

## Compatibility with the AGPL

ZTor is AGPL-3.0-or-later. Each component above is compatible with that license:

- BSD-3-Clause, BSL-1.0, MIT, PSF-2.0 and the permissive and public domain parts: compatible with the GPL family, no extra terms for ZTor.
- Apache-2.0 (OpenSSL 3): compatible with version 3 of the GPL family, and so with AGPL version 3. It would not be compatible with GPL version 2 only, which ZTor does not use.
- LGPL-2.0-or-later with the wxWindows exception (wxWidgets, wxPython): compatible. ZTor only uses the library through its public interface and may be distributed under its own terms. The "or later" allows combining under LGPL version 3 and GPL version 3.
- PyInstaller (GPL-2.0-or-later with bootloader exception): the exception allows the embedded bootloader in a program of any license, so the built ZTor.exe stays AGPL-3.0-or-later. Using PyInstaller as a build tool puts no terms on ZTor.
- The runtime tracker list (GPL-2.0 repository) is data fetched from the Internet and is not linked into or distributed with ZTor, so its license does not reach ZTor.
- No component found is incompatible.

Where a license asks that its text or copyright notice accompany copies (BSD-3-Clause for libtorrent, MIT for comtypes, Apache-2.0 for OpenSSL, the wxWindows licence, and PSF-2.0 for Python), a built copy of ZTor must keep those notices. The notices are given above, the full texts are in the installed packages and at the URLs listed, and the built program shows the credits in its About page.
