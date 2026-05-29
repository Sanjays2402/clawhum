"""Library loaders: local filesystem + optional Spotify preview clips."""
from .local import LocalLibrary, scan_directory
from .spotify import SpotifyLibrary, SpotifyAuth

__all__ = ["LocalLibrary", "scan_directory", "SpotifyLibrary", "SpotifyAuth"]
