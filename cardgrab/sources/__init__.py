"""Card-record sources. Each adapter turns some local artefact into CardRefs."""

from cardgrab.sources.base import CardSource, NullSource
from cardgrab.sources.manifest import ManifestSource
from cardgrab.sources.saved_page import SavedPageSource

__all__ = ["CardSource", "NullSource", "ManifestSource", "SavedPageSource"]
