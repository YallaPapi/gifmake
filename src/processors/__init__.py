"""Processors module for content analysis and categorization."""

from .content_categorizer import (
    categorize_subreddit,
    process_all,
    get_subreddits_by_category,
    get_categories_for_subreddit,
    CATEGORY_KEYWORDS
)

from .tier_classifier import (
    classify_subreddit,
    process_all as process_tiers,
    TIER_2_PATTERNS,
    TIER_3_PATTERNS,
)

from .account_profile import (
    AccountAttributes,
    RedditAccount,
    AccountProfile,
    ProfileManager,
    create_example_profiles,
)

from .config_builder import (
    build_unified_config,
    load_config,
    get_postable_subreddits,
    get_posting_info,
    filter_by_subscribers,
    get_subreddits_needing_flair,
    get_subreddits_by_title_format,
)

__all__ = [
    # Content categorizer
    'categorize_subreddit',
    'process_all',
    'get_subreddits_by_category',
    'get_categories_for_subreddit',
    'CATEGORY_KEYWORDS',
    # Tier classifier
    'classify_subreddit',
    'process_tiers',
    'TIER_2_PATTERNS',
    'TIER_3_PATTERNS',
    # Account profiles
    'AccountAttributes',
    'RedditAccount',
    'AccountProfile',
    'ProfileManager',
    'create_example_profiles',
    # Config builder
    'build_unified_config',
    'load_config',
    'get_postable_subreddits',
    'get_posting_info',
    'filter_by_subscribers',
    'get_subreddits_needing_flair',
    'get_subreddits_by_title_format',
]
