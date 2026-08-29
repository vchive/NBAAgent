"""Public, fixture and controlled web-search provider adapters."""

from .ddg_adapter import DuckDuckGoAdapter
from .search_augmented_provider import SearchAugmentedProvider

__all__ = ["DuckDuckGoAdapter", "SearchAugmentedProvider"]
