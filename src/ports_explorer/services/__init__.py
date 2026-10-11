"""Service package: pure logic with no I/O and no UI dependencies."""

from .deduplication import (
    DeduplicationStats,
    dedup_key,
    deduplicate,
    filter_category,
    filter_files,
    filter_listeners,
    filter_network,
    filter_unix,
)
from .resource_search import (
    ResourceFilter,
    filter_resources,
    search_processes,
    search_resources,
)

__all__ = [
    "DeduplicationStats",
    "ResourceFilter",
    "dedup_key",
    "deduplicate",
    "filter_category",
    "filter_files",
    "filter_listeners",
    "filter_network",
    "filter_resources",
    "filter_unix",
    "search_processes",
    "search_resources",
]
