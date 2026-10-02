"""`salary_options` — the configurable salary ladder (issue #337).

The ladder used to be a hardcoded legacy set in the Web Dashboard (`3K以下` … `50K以上`)
that matched nothing the Boss 直聘 app actually displays, while the domain baseline in
`FilterConfig` and the provisioner's default saved search both named `"5万元以上"` — a
tier that exists in neither ladder. A default search therefore applied a filter the
device could not satisfy.

These tests pin the two halves of the fix: the ladder is *configurable* (the realm reads
it), and everything that must name a real tier *does*.
"""

import pytest

from boss_agent import config_realm
from boss_agent.broker.provisioner import DEFAULT_INITIAL_SEARCHES
from boss_agent.models import FilterConfig

#: The invalid tier that shipped as the domain baseline. Asserted against literally so a
#: regression is legible in the failure rather than looking like an ordinary value diff.
LEGACY_INVALID_TIER = "5万元以上"


def test_the_default_ladder_matches_the_current_app_tiers() -> None:
    assert config_realm.salary_options({}) == [
        "15K以下",
        "15-25K",
        "25-35K",
        "35-45K",
        "45K以上",
    ]


def test_the_realm_key_overrides_the_shipped_ladder() -> None:
    """The point of the key: an operator's ladder wins over the built-in floor."""
    configured = ["3K以下", "3-5K", "5K以上"]
    assert config_realm.salary_options({"salary_options": configured}) == configured


def test_a_malformed_ladder_degrades_to_the_baseline() -> None:
    """A hand-edited YAML file must not leave the search strategy with no options.

    The realm's file format is schema-less, so an empty list, a bare scalar and a list
    of non-strings are all reachable by a typo. Each degrades rather than raising.
    """
    baseline = config_realm.DEFAULT_SALARY_OPTIONS

    # An empty list is never a useful answer: fall back instead of rendering an empty
    # dropdown that silently saves "".
    assert config_realm.salary_options({"salary_options": []}) == baseline
    assert config_realm.salary_options({"salary_options": None}) == baseline
    assert config_realm.salary_options({"salary_options": {}}) == baseline

    # Blanks are dropped and duplicates collapse, but order is preserved — the ladder is
    # a presented list, not a set.
    assert config_realm.salary_options(
        {"salary_options": ["20-30K", "  ", "10-20K", "20-30K", 42, "35K以上"]}
    ) == ["20-30K", "10-20K", "35K以上"]

    # A bare scalar is tolerated the same way a numeric value is coerced.
    assert config_realm.salary_options({"salary_options": "10-20K, 20-30K"}) == [
        "10-20K",
        "20-30K",
    ]


def test_the_accessor_reads_the_chain() -> None:
    """It resolves through the realm, not a private copy — a tmp-YAML seam, like every
    other config test."""
    example = config_realm.Path(__file__).parents[2] / "config" / "settings.example.yaml"
    assert config_realm.salary_options(config_path=example) == (
        config_realm.DEFAULT_SALARY_OPTIONS
    )


def test_the_filter_config_default_names_a_configured_tier() -> None:
    """`FilterConfig.salary` must be a tier the dialog can actually select."""
    options = config_realm.salary_options({})

    cfg = FilterConfig()
    assert cfg.salary != LEGACY_INVALID_TIER
    assert cfg.salary in options, (
        f"FilterConfig.salary={cfg.salary!r} is not one of the configured tiers {options!r}"
    )


@pytest.mark.parametrize("name", sorted(DEFAULT_INITIAL_SEARCHES))
def test_every_provisioned_default_search_names_a_configured_tier(name: str) -> None:
    """The default saved searches are seeded into PocketBase on first boot, so an
    invalid tier here persists into the operator's database rather than being visible as
    a broken dropdown."""
    options = config_realm.salary_options({})
    salary = DEFAULT_INITIAL_SEARCHES[name]["filter"]["salary"]

    assert salary != LEGACY_INVALID_TIER
    assert salary in options, (
        f"provisioned search {name!r} filters on {salary!r}, which is not one of {options!r}"
    )
