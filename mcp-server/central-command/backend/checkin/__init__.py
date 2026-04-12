"""Checkin handler decomposition — extracted from sites.py for maintainability.

Each module handles a logical group of STEP blocks from the original handler.
All helpers operate on a shared CheckinContext and use explicit transaction
savepoints (NEVER bare queries — poisoned transactions cascade).
"""
