"""Service package: pure logic with no UI dependencies."""

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
from .kill_process import (
    GRACE_PERIOD,
    KillOutcome,
    KillRefused,
    KillResult,
    force_kill,
    is_alive,
    terminate,
    validate_target,
)
from .resource_search import (
    ResourceFilter,
    filter_resources,
    search_processes,
    search_resources,
)

__all__ = [
    "GRACE_PERIOD",
    "DeduplicationStats",
    "KillOutcome",
    "KillRefused",
    "KillResult",
    "ResourceFilter",
    "dedup_key",
    "deduplicate",
    "filter_category",
    "filter_files",
    "filter_listeners",
    "filter_network",
    "filter_resources",
    "filter_unix",
    "force_kill",
    "is_alive",
    "search_processes",
    "search_resources",
    "terminate",
    "validate_target",
]
