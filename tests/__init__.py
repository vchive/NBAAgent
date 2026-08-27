"""Test package marker.

Keeping the shared test root as a package prevents duplicate module names in
different test layers (for example unit.test_news and contract.test_news)
from colliding during pytest collection.
"""
