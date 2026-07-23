"""Folder watching and local file access (M04).

`filesystem` implements the SourceFileStore port with path-escape
prevention; `service` (the watchfiles loop) turns change events into
DetectChanges sweeps.
"""
