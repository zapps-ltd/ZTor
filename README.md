# ZTor

ZTor is a portable BitTorrent client for Windows, written in Python with wxPython and libtorrent. It is built so that blind and sighted people can use it equally well: every command is reachable from the keyboard, screen readers hear what happens, and the look of the window stays clear for people who see it. Everything it stores stays in one folder, so it can live on a USB stick.

Free software under AGPL-3.0-or-later. Source: https://github.com/zapps-ltd/ZTor

## Why it exists

Many BitTorrent clients are hard or impossible to use with a screen reader: unlabeled lists, progress bars that beep, dialogs that trap the keyboard. ZTor starts from the other end. The window is made of standard controls, tab order and focus are treated as part of correctness, and nothing important is shown only by color or by a picture. The torrent engine runs inside the program, so there is no separate service to install or keep in step.

## Features

### Adding and managing torrents

- Add .torrent files, magnet links and web addresses, one or many at a time, or add them paused.
- Drag and drop .torrent files onto the list.
- Import all torrent files from a folder, and export the selected torrent files.
- Watch a folder: new .torrent files that appear in it are added by themselves.
- Choose the save folder for each torrent, or ask every time.
- Optional separate folders for unfinished downloads and for finished downloads.
- Start, pause, force start, resume all and pause all.
- Force recheck, force reannounce and update trackers.
- Rename a torrent, move its data folder, save the torrent file under another name, and open the containing folder.
- Remove a torrent and keep its data, or remove it and delete the data. Both can ask for confirmation.
- Torrents and their progress are restored after a restart, using a stored copy of the torrent file and the resume data, even if the original file is gone.
- Labels to group torrents, and a filter tree with status filters (all, active, downloading, seeding, completed, paused, error, inactive) and label filters. A filter box finds torrents by text.
- Copy the magnet link, info hash, name, save path or all details of a torrent.

### Files, trackers and peers

- Choose which files of a torrent to download and set each file's priority (skip, normal, high), also while adding.
- Sequential download and super seeding, per torrent.
- Add trackers and web seeds, and see each tracker with its tier and status.
- Connect to a peer by address, and list connected peers with client, flags and speeds.
- Per torrent switches for DHT, peer exchange and local peer discovery.
- Optional list of public trackers added to new torrents that are not private. The list is downloaded in the background and cached; a built-in list is used if the download fails. You can add your own extra trackers.
- Ask every tracker for peers, not only the first one.

### Queueing, speed and seeding

- Queue management with a limit on active downloads and active seeds. Move a torrent to the top, up, down or to the bottom of the queue, or add new torrents at the top.
- Torrent priority: low, normal, high.
- Global download and upload limits, and separate limits for a single torrent.
- Alternative speed limits that can be switched on by hand or by a schedule.
- Speed profiles: low resources, balanced and fast. Option to prefer TCP over uTP. Maximum connections setting.
- Seeding goals by share ratio or by time, with the action to take when a goal is reached (pause or remove).
- Statistics window, and a spoken summary of the current, average and peak speeds.

### Network and security

- Listening port: fixed, or chosen automatically.
- DHT, local peer discovery, UPnP and NAT-PMP port mapping, each can be switched off. DHT state is saved between runs.
- Protocol encryption: prefer plaintext and allow encryption, require encrypted peers, or plaintext only.
- Proxy support: SOCKS5, SOCKS4, HTTP and HTTP with authentication, for peer connections and for trackers.
- Bind to one network address, for example a VPN adapter, so nothing is sent if the VPN drops.
- IP filter lists in eMule .dat or PeerGuardian .p2p format, plain or gzip.

### Feeds, search and creating torrents

- RSS and Atom feeds with download rules. Rules act only on articles that arrive after a feed was first checked, so adding a feed never starts a flood of downloads. Older articles can be downloaded by hand.
- Search for torrents on the Internet Archive, whose items are free to share. If you set the Torznab address and API key of a Jackett or Prowlarr you run, ZTor can search those indexers too.
- Create a torrent from a file or a folder: piece size, trackers, web seeds, comment, private flag, and optional start of seeding.

### Web remote

- A small password protected web page to see, start, pause, remove and add (by magnet link) torrents from a browser or phone.
- Off by default. It needs a password, which is stored only as a salted hash. It answers only this computer unless you allow other devices on your network. Pages are plain HTML forms with no scripts. Sessions use a strict cookie and a per session token, and repeated wrong passwords lock the address out for a minute.

### The window

- A torrent list with 41 selectable columns, sorting, and a column dialog to show, hide, reorder and size them.
- A details area with tabs: Torrents (properties), Files, Peers, Trackers, Speed, Log and Graph.
- A speed history graph and a progress strip for the selected torrent, drawn as pictures.
- Toolbar, status colors in the list, optional action buttons under the list, light or dark appearance (or follow Windows), gentle animations, and a touch mode with larger rows and buttons.
- Tray icon, start minimized to the tray, and pop up notices when no screen reader is running.
- Optional Windows integration: open magnet links and .torrent files with ZTor. It is off by default and writes only to the current user's registry keys.
- Only one copy runs per folder; starting it again brings the running copy to the front and hands it the torrent files or magnet links you gave.
- Automatic cleanup of the leftover cache and temporary files that ZTor itself made. Downloaded data is never touched.

## Accessibility

- Works with NVDA and JAWS. Speech goes to NVDA through its controller client when it is present, and to JAWS through its scripting interface (needs the comtypes package). When neither is running, messages appear in the status bar.
- Speech announcements can be turned down: notices for finished downloads and errors, and confirmations for menu actions can be switched separately.
- Every control has an accessible name. Check boxes stay readable as check boxes in Windows dark mode.
- Menus have access keys, and the main commands have shortcuts: Ctrl+O add a torrent file, Ctrl+N add a magnet link or address, Ctrl+Shift+N add paused, Ctrl+S start, Ctrl+P pause, Ctrl+F filter, Ctrl+E search, Ctrl+R RSS feeds, Ctrl+Shift+M create a torrent, Ctrl+Shift+C columns, Ctrl+Shift+T alternative speed limits, Ctrl+Shift+D read the selected torrent aloud, Ctrl+Shift+S speak the speed summary, Ctrl+comma Preferences, F2 rename, F3 speed limits for the selection, F5 refresh. In the list, Delete removes and Shift+Delete removes with the data.
- The context menu opens with the Application key, Shift+F10 or a right click.
- Read Torrent Aloud speaks a whole row, with every visible column.
- The graph and the progress strip are not in the Tab order and are not native progress bars, so screen readers do not beep. The same numbers are always available as text.
- Progress of long jobs, such as creating a torrent, is spoken at each quarter.
- Status colors switch off under Windows high contrast, and animations switch off while a screen reader runs.

## Privacy and security

ZTor has no account, no advertising, no telemetry and no update check. It sends nothing to zapps.ltd. It contacts the network only for these reasons:

- Talking to peers and trackers of the torrents you add. This is how BitTorrent works: other peers can see your IP address. Use a proxy or bind to a VPN address if that matters to you.
- DHT bootstrap nodes (dht.libtorrent.org, router.bittorrent.com, router.utorrent.com, dht.transmissionbt.com, dht.aelitis.com), only while DHT is on.
- The public tracker list, from raw.githubusercontent.com by default. It is fetched when ZTor starts and when you update trackers, as long as the option to add public trackers is on. You can switch the option off or change the address in Preferences.
- RSS feeds that you add yourself, at the interval you choose.
- Search: the Internet Archive when you search, and a Jackett or Prowlarr address only if you enter one.
- UPnP and NAT-PMP port mapping on your router, if enabled.

Everything is stored in the app folder; nothing is written elsewhere, except the optional Windows registry entries for opening magnet links and torrent files, which only exist if you turn that on. The web remote is off by default.

## Legal note

You are responsible for what you download, share and seed. Copy only what you have the right to copy. ZTor is a general purpose tool and ships no content, no torrent index and no list of torrents.

## Requirements

- Windows 10 or 11, 64 bit.
- Python 3.11 or newer (only to run from source). It was developed on Python 3.12.
- Python packages: libtorrent 2.1.1, wxPython 4.3.2, and comtypes 1.4.17 or newer (only needed for JAWS speech).

## Running

A build is one folder: ZTor.exe, the launcher, beside the folder app. Start ZTor.exe.

To run from source, install the packages and start the program from the project folder:

```
python -m pip install libtorrent==2.1.1 wxPython==4.3.2 comtypes
python main.py
```

`python -m ztor` does the same.

## Where files are kept

ZTor is portable. The app folder holds the runtime and four folders that are made on first start:

- data: settings, torrent list, RSS feeds, tracker list, DHT state, resume data and torrent file copies
- downloads: the default download folder
- cache: temporary files and the single instance lock
- logs: the log file

Paths inside it are stored relative to it, so the folder can move to another drive or a USB stick. From source, the same app folder sits beside main.py.

## Folder layout

- main.py: entry point.
- ztor/: the program. engine.py runs libtorrent on its own thread; main_window.py is the window, menus and tabs; dialogs.py holds Preferences and the other dialogs; store.py keeps settings and the torrent list; columns.py and row.py define the list; rss.py and rss_dialog.py are the feeds; search.py and tools_dialog.py are search and torrent creation; creator.py makes torrent files; webui.py is the web remote; tuning.py builds session settings; trackers.py, ipfilter.py, cleanup.py, instance.py, shellassoc.py and paths.py are the supporting parts; accessibility.py and a11y.py handle speech and accessible controls; graph.py and icons.py draw the graph and the icons; about_page.py builds the About page.

## Languages

The interface is currently English only.

## Reporting problems and contact

Please report bugs and ideas by email to support@zapps.ltd or through the contact page of https://zapps.ltd. Pull requests are not accepted. ZTor is made by Harith and Mesar, the zapps.ltd developers, for ZAPPS SOFTWARE LTD. Every change keeps accessibility in mind: keyboard use and screen reader output must never get worse.

## License

ZTor is free software, licensed under the GNU Affero General Public License, version 3 or (at your option) any later version (AGPL-3.0-or-later). The full text is in the file LICENSE. In short: you may use, study, change and share it, but every copy and every modified version, including one that other people use over a network, must stay under the same license, must come with its complete source code, and must keep the copyright notices and the credits below. Nobody can make a closed version of it. Copyright (C) 2026 ZAPPS SOFTWARE LTD.

## Third-party software and credits

ZTor stands on the work of others:

- libtorrent-rasterbar (Arvid Norberg and contributors): the BitTorrent engine. BSD-3-Clause. https://libtorrent.org
- Boost (used by libtorrent): Boost Software License 1.0. https://www.boost.org
- OpenSSL (linked into libtorrent): Apache-2.0. https://www.openssl.org
- wxPython (Robin Dunn and contributors) and wxWidgets: the window toolkit. LGPL-2.0-or-later with the wxWindows exception 3.1 (wxWindows Library Licence). https://wxpython.org
- comtypes (Thomas Heller and contributors): MIT. https://github.com/enthought/comtypes
- Python: PSF-2.0. https://www.python.org

Full details, license texts and notices are in the file THIRD_PARTY_NOTICES.md.
