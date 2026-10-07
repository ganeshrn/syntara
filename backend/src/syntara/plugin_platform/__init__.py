"""Trusted persistence and resolution boundary for installed plugins.

This package deliberately has no router or registry downloader yet.  Its first
responsibility is to retain the immutable result of a successful install and
make that result the only source from which workflow execution can later
resolve an external action.
"""
