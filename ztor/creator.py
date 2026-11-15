"""Make a .torrent file from a file or a folder. No wx here."""

import os

import libtorrent as lt

from . import __version__

PIECE_SIZES = [("Automatic", 0), ("256 KB", 256 * 1024), ("512 KB", 512 * 1024),
               ("1 MB", 1024 * 1024), ("2 MB", 2 * 1024 * 1024),
               ("4 MB", 4 * 1024 * 1024), ("8 MB", 8 * 1024 * 1024),
               ("16 MB", 16 * 1024 * 1024)]


class Cancelled(Exception):
    pass


def lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def create(path, trackers=(), web_seeds=(), comment="", private=False,
           piece_size=0, progress=None, cancelled=None):
    """Hash the content and return the bencoded torrent bytes.

    progress(done, total) is called as pieces are hashed; cancelled() returning
    True stops the work with Cancelled.
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise ValueError("the file or folder does not exist")
    storage = lt.file_storage()
    lt.add_files(storage, path)
    if storage.num_files() == 0:
        raise ValueError("there are no files to share there")
    torrent = lt.create_torrent(storage, int(piece_size or 0))
    for tier, url in enumerate(trackers):
        torrent.add_tracker(url, tier)
    for url in web_seeds:
        torrent.add_url_seed(url)
    if comment:
        torrent.set_comment(comment)
    torrent.set_creator("ZTor %s" % __version__)
    if private:
        torrent.set_priv(True)
    total = torrent.num_pieces()

    def on_piece(index):
        if cancelled is not None and cancelled():
            raise Cancelled()
        if progress is not None:
            progress(index + 1, total)

    try:
        lt.set_piece_hashes(torrent, os.path.dirname(path), on_piece)
    except Cancelled:
        raise
    except Exception:
        if cancelled is not None and cancelled():
            raise Cancelled()
        raise
    if cancelled is not None and cancelled():
        raise Cancelled()
    return lt.bencode(torrent.generate())
