"""
Account Profile System

Defines account personas with attributes and flair mappings for Reddit posting.
Each profile represents a "persona" (e.g., "Jenny, 24, redhead, petite") with:
- Physical attributes for content matching
- Title templates for different subreddit requirements
- Flair mappings per subreddit
- Content tags for filtering which subs to post to
- Reddit account metadata (karma, age, verification status)
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class PersonaInterests:
    """General interests, hobbies, and personality for realistic warmup activity."""
    location: str = ""                # city/state e.g. "Charlotte, NC"
    hobbies: List[str] = field(default_factory=list)     # cooking, yoga, hiking, gaming...
    interests: List[str] = field(default_factory=list)   # fashion, travel, music, true_crime...
    personality_traits: List[str] = field(default_factory=list)  # bubbly, sarcastic, chill...
    favorite_subs: List[str] = field(default_factory=list)  # explicit SFW subs to browse
    comment_style: str = "casual"     # casual, enthusiastic, minimal


@dataclass
class AccountAttributes:
    """Physical attributes of an account persona."""
    age: int
    gender: str = "F"
    hair_color: str = "brunette"  # redhead, blonde, brunette, black
    body_type: str = "average"    # petite, thick, athletic, average
    ethnicity: str = "white"      # white, asian, latina, ebony, mixed
    bust_size: str = "medium"     # small, medium, large

    def matches_tags(self, tags: List[str]) -> bool:
        """Check if attributes match any of the given tags."""
        attribute_values = [
            self.hair_color,
            self.body_type,
            self.ethnicity,
            self.bust_size,
            f"{self.age}",
        ]
        # Add age-based tags
        if self.age >= 35:
            attribute_values.append("milf")
        if self.age >= 40:
            attribute_values.append("mature")
        if self.age <= 25:
            attribute_values.append("young")
        if self.age <= 22:
            attribute_values.append("teen")

        return any(tag.lower() in attribute_values for tag in tags)


@dataclass
class RedditAccount:
    """Reddit account metadata."""
    username: str
    age_days: int = 0
    karma: int = 0
    verified_subreddits: List[str] = field(default_factory=list)

    def is_verified_for(self, subreddit: str) -> bool:
        """Check if account is verified for a specific subreddit."""
        return subreddit.lower() in [s.lower() for s in self.verified_subreddits]

    def meets_requirements(self, min_age_days: int = 0, min_karma: int = 0) -> bool:
        """Check if account meets age and karma requirements."""
        return self.age_days >= min_age_days and self.karma >= min_karma


@dataclass
class AccountProfile:
    """
    Complete account profile representing a posting persona.

    Example usage:
        profile = manager.get_profile("jenny_24")
        title = profile.get_title("Playing with myself", "age_gender")
        flair = profile.get_flair("gonewild")
    """
    profile_id: str
    display_name: str
    attributes: AccountAttributes
    adspower_id: str = ""
    persona: PersonaInterests = field(default_factory=PersonaInterests)
    title_templates: Dict[str, str] = field(default_factory=lambda: {"default": "{title}"})
    flair_mappings: Dict[str, Optional[str]] = field(default_factory=dict)
    content_tags: List[str] = field(default_factory=list)
    reddit_account: RedditAccount = field(default_factory=lambda: RedditAccount(username=""))
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    creator: str = ""  # Content bank folder name (e.g., "creator_mae")
    proxy_group: str = ""  # Proxy group name (e.g., "proxy_1")

    def get_title(self, base_title: str, template_name: str = "default") -> str:
        """
        Generate title using a template.

        Available placeholders:
        - {title}: The base title
        - {age}: Profile age
        - {gender}: Profile gender

        Args:
            base_title: The base title text
            template_name: Name of the template to use (from title_templates)

        Returns:
            Formatted title string
        """
        template = self.title_templates.get(template_name, "{title}")
        try:
            return template.format(
                title=base_title,
                age=self.attributes.age,
                gender=self.attributes.gender
            )
        except KeyError as e:
            # If template has unknown placeholders, return base title
            print(f"Warning: Unknown placeholder in template '{template_name}': {e}")
            return base_title

    def get_flair(self, subreddit: str) -> Optional[str]:
        """
        Get flair text for a specific subreddit.

        Args:
            subreddit: Subreddit name (without r/ prefix)

        Returns:
            Flair text or None if no flair mapped
        """
        # Try exact match first, then case-insensitive
        if subreddit in self.flair_mappings:
            return self.flair_mappings[subreddit]

        for sub, flair in self.flair_mappings.items():
            if sub.lower() == subreddit.lower():
                return flair

        return None

    def has_flair_for(self, subreddit: str) -> bool:
        """Check if a flair mapping exists for the subreddit."""
        return self.get_flair(subreddit) is not None

    def can_post_tier(self, tier: int) -> bool:
        """
        Check if account meets tier requirements.

        Tiers:
        - Tier 1: No requirements (anyone can post)
        - Tier 2: Account age >= 30 days, karma >= 100
        - Tier 3: Must be verified for the subreddit

        Args:
            tier: Tier level (1, 2, or 3)

        Returns:
            True if account meets tier requirements
        """
        if tier == 1:
            return True
        elif tier == 2:
            return self.reddit_account.meets_requirements(min_age_days=30, min_karma=100)
        elif tier == 3:
            # Tier 3 requires verification - check if verified for any sub
            return len(self.reddit_account.verified_subreddits) > 0
        return False

    def can_post_to(self, subreddit: str, tier: int) -> bool:
        """
        Check if account can post to a specific subreddit.

        Args:
            subreddit: Subreddit name
            tier: Tier level of the subreddit

        Returns:
            True if account can post
        """
        if tier == 3:
            return self.reddit_account.is_verified_for(subreddit)
        return self.can_post_tier(tier)

    def matches_content(self, required_tags: List[str]) -> bool:
        """
        Check if profile's content tags match required tags.

        Args:
            required_tags: List of tags required by a subreddit

        Returns:
            True if any content tag matches
        """
        if not required_tags:
            return True

        profile_tags_lower = [t.lower() for t in self.content_tags]
        required_lower = [t.lower() for t in required_tags]

        return any(tag in profile_tags_lower for tag in required_lower)


class ProfileManager:
    """
    Manager for loading, saving, and querying account profiles.

    Usage:
        manager = ProfileManager("config/account_profiles.json")
        profile = manager.get_profile("jenny_24")
        all_ids = manager.list_profiles()
    """

    def __init__(self, config_path: str = "config/account_profiles.json"):
        self.config_path = Path(config_path)
        self.profiles: Dict[str, AccountProfile] = {}
        self._load()

    def _load(self):
        """Load profiles from JSON file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for profile_id, profile_data in data.get("profiles", {}).items():
                        self.profiles[profile_id] = self._dict_to_profile(profile_data)
                print(f"Loaded {len(self.profiles)} profiles from {self.config_path}")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error loading profiles: {e}")
                self.profiles = {}
        else:
            print(f"Profile config not found: {self.config_path}")

    def _dict_to_profile(self, data: Dict[str, Any]) -> AccountProfile:
        """Convert dictionary to AccountProfile object."""
        persona_data = data.get("persona", {})
        return AccountProfile(
            profile_id=data["profile_id"],
            adspower_id=data.get("adspower_id", ""),
            display_name=data["display_name"],
            attributes=AccountAttributes(**data.get("attributes", {})),
            persona=PersonaInterests(**persona_data) if persona_data else PersonaInterests(),
            title_templates=data.get("title_templates", {"default": "{title}"}),
            flair_mappings=data.get("flair_mappings", {}),
            content_tags=data.get("content_tags", []),
            reddit_account=RedditAccount(**data.get("reddit_account", {"username": ""})),
            created_at=data.get("created_at", datetime.now().isoformat()),
            creator=data.get("creator", ""),
            proxy_group=data.get("proxy_group", ""),
        )

    def _profile_to_dict(self, profile: AccountProfile) -> Dict[str, Any]:
        """Convert AccountProfile to dictionary for JSON serialization."""
        return {
            "profile_id": profile.profile_id,
            "adspower_id": profile.adspower_id,
            "display_name": profile.display_name,
            "attributes": asdict(profile.attributes),
            "persona": asdict(profile.persona),
            "title_templates": profile.title_templates,
            "flair_mappings": profile.flair_mappings,
            "content_tags": profile.content_tags,
            "reddit_account": asdict(profile.reddit_account),
            "created_at": profile.created_at,
            "creator": profile.creator,
            "proxy_group": profile.proxy_group,
        }

    def save(self):
        """Save all profiles to JSON file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"profiles": {}}
        for profile_id, profile in self.profiles.items():
            data["profiles"][profile_id] = self._profile_to_dict(profile)

        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        print(f"Saved {len(self.profiles)} profiles to {self.config_path}")

    def add_profile(self, profile: AccountProfile) -> None:
        """Add or update a profile."""
        self.profiles[profile.profile_id] = profile
        self.save()

    def remove_profile(self, profile_id: str) -> bool:
        """Remove a profile by ID. Returns True if removed."""
        if profile_id in self.profiles:
            del self.profiles[profile_id]
            self.save()
            return True
        return False

    def get_profile(self, profile_id: str) -> Optional[AccountProfile]:
        """Get a profile by ID."""
        return self.profiles.get(profile_id)

    def list_profiles(self) -> List[str]:
        """List all profile IDs."""
        return list(self.profiles.keys())

    def get_all_profiles(self) -> List[AccountProfile]:
        """Get all profile objects."""
        return list(self.profiles.values())

    def find_profiles_for_subreddit(
        self,
        subreddit: str,
        tier: int,
        required_tags: Optional[List[str]] = None
    ) -> List[AccountProfile]:
        """
        Find profiles that can post to a subreddit.

        Args:
            subreddit: Subreddit name
            tier: Subreddit tier level
            required_tags: Optional content tags required

        Returns:
            List of matching profiles
        """
        matching = []
        for profile in self.profiles.values():
            # Check if can post (meets tier requirements)
            if not profile.can_post_to(subreddit, tier):
                continue

            # Check content tag match if required
            if required_tags and not profile.matches_content(required_tags):
                continue

            matching.append(profile)

        return matching

    def find_profiles_by_tag(self, tag: str) -> List[AccountProfile]:
        """Find profiles that have a specific content tag."""
        tag_lower = tag.lower()
        return [
            p for p in self.profiles.values()
            if tag_lower in [t.lower() for t in p.content_tags]
        ]


def create_example_profiles() -> str:
    """
    Create example profile config file with sample data.

    Returns:
        Path to created config file
    """
    example_data = {
        "profiles": {
            "jenny_24": {
                "profile_id": "jenny_24",
                "display_name": "Jenny",
                "attributes": {
                    "age": 24,
                    "gender": "F",
                    "hair_color": "redhead",
                    "body_type": "petite",
                    "ethnicity": "white",
                    "bust_size": "small"
                },
                "title_templates": {
                    "default": "{title}",
                    "age_gender": "[{age}F] {title}",
                    "simple_gender": "[F] {title}",
                    "emoji": "{title}"
                },
                "flair_mappings": {
                    "gonewild": "OC",
                    "petitegonewild": "Verified",
                    "realgirls": "OC",
                    "redheads": None
                },
                "content_tags": ["petite", "redhead", "amateur", "young", "small-tits"],
                "reddit_account": {
                    "username": "jenny_plays_24",
                    "age_days": 45,
                    "karma": 1500,
                    "verified_subreddits": ["gonewild", "petitegonewild"]
                },
                "created_at": "2024-01-15T10:30:00"
            },
            "maria_40": {
                "profile_id": "maria_40",
                "display_name": "Maria",
                "attributes": {
                    "age": 40,
                    "gender": "F",
                    "hair_color": "brunette",
                    "body_type": "thick",
                    "ethnicity": "latina",
                    "bust_size": "large"
                },
                "title_templates": {
                    "default": "{title}",
                    "age_gender": "[{age}F] {title}",
                    "milf_style": "MILF {title}",
                    "latina_style": "Latina MILF - {title}"
                },
                "flair_mappings": {
                    "gonewildcurvy": "OC",
                    "latinas": "Verified Latina",
                    "milf": "OC",
                    "thick": None
                },
                "content_tags": ["milf", "thick", "latina", "boobs", "curvy", "mature"],
                "reddit_account": {
                    "username": "maria_mature",
                    "age_days": 90,
                    "karma": 5000,
                    "verified_subreddits": ["gonewildcurvy", "latinas"]
                },
                "created_at": "2024-01-10T08:00:00"
            }
        }
    }

    config_path = Path("config/account_profiles.json")
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(example_data, f, indent=2)

    print(f"Created example profiles at {config_path.absolute()}")
    print(f"Profiles: {list(example_data['profiles'].keys())}")

    return str(config_path)


def main():
    """Main entry point for testing the profile system."""
    print("=" * 60)
    print("ACCOUNT PROFILE SYSTEM TEST")
    print("=" * 60)

    # Create example profiles
    config_path = create_example_profiles()

    # Load and test
    print("\n--- Loading Profiles ---")
    manager = ProfileManager(config_path)

    print(f"\nAvailable profiles: {manager.list_profiles()}")

    for profile_id in manager.list_profiles():
        profile = manager.get_profile(profile_id)
        if not profile:
            continue

        print(f"\n{'=' * 40}")
        print(f"Profile: {profile.display_name} ({profile.profile_id})")
        print(f"{'=' * 40}")
        print(f"  Age: {profile.attributes.age}")
        print(f"  Body type: {profile.attributes.body_type}")
        print(f"  Hair: {profile.attributes.hair_color}")
        print(f"  Ethnicity: {profile.attributes.ethnicity}")
        print(f"  Content tags: {profile.content_tags}")
        print(f"  Reddit account: @{profile.reddit_account.username}")
        print(f"  Account age: {profile.reddit_account.age_days} days")
        print(f"  Karma: {profile.reddit_account.karma}")
        print(f"  Verified subs: {profile.reddit_account.verified_subreddits}")

        print(f"\n  --- Tier Eligibility ---")
        print(f"  Can post Tier 1: {profile.can_post_tier(1)}")
        print(f"  Can post Tier 2: {profile.can_post_tier(2)}")
        print(f"  Can post Tier 3: {profile.can_post_tier(3)}")

        print(f"\n  --- Title Examples ---")
        base_title = "Playing with myself after work"
        for template_name in profile.title_templates.keys():
            formatted = profile.get_title(base_title, template_name)
            print(f"  {template_name}: {formatted}")

        print(f"\n  --- Flair Mappings ---")
        for sub, flair in profile.flair_mappings.items():
            print(f"  r/{sub}: {flair if flair else '(no flair)'}")

    # Test finding profiles for subreddits
    print("\n" + "=" * 60)
    print("PROFILE MATCHING TEST")
    print("=" * 60)

    test_cases = [
        ("gonewild", 3, ["amateur"]),
        ("petitegonewild", 3, ["petite"]),
        ("milf", 1, ["milf"]),
        ("latinas", 2, ["latina"]),
    ]

    for subreddit, tier, tags in test_cases:
        matching = manager.find_profiles_for_subreddit(subreddit, tier, tags)
        print(f"\nr/{subreddit} (Tier {tier}, tags: {tags}):")
        if matching:
            for p in matching:
                print(f"  - {p.display_name} ({p.profile_id})")
        else:
            print("  (no matching profiles)")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
