"""
Versioning module for Civilization Knowledge Layer.
Handles version tracking, diffs, and staleness detection.
"""
from .version_manager import VersionManager, NodeVersion, VersionHistory
from .diff_engine import DiffEngine, NodeDiff, DiffType
from .staleness_detector import StalenessDetector, StalenessLevel, StalenessReport

__all__ = [
    "VersionManager",
    "NodeVersion",
    "VersionHistory",
    "DiffEngine",
    "NodeDiff",
    "DiffType",
    "StalenessDetector",
    "StalenessLevel",
    "StalenessReport",
]
