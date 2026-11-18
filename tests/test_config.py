from pathlib import Path

import pytest

from beatpub.config import ConfigError, find_root, load_config, parse_config

ROOT = Path(__file__).resolve().parent.parent


def test_the_owners_config_loads():
    config = load_config(ROOT)
    assert config.artist and config.privacy_default in ("private", "unlisted", "public")
    assert config.client_secret_path == ROOT / "client_secret.json"
    assert config.beats_dir == ROOT / "beats"


def test_defaults():
    config = parse_config({"artist": "CapryBeats"}, ROOT)
    assert config.privacy_default == "private" and config.audit_passed is False
    assert config.category_id == "10" and config.shorts.enabled and config.shorts.publish_offset_hours == 2
    assert config.warnings == ()


def test_unknown_keys_and_unaudited_public_warn():
    config = parse_config({"artist": "A", "privacy_default": "public", "colour": "red",
                           "shorts": {"lenght": 3}, "category_id": 10}, ROOT)
    text = " | ".join(config.warnings)
    assert "colour" in text and "shorts.lenght" in text and "audit" in text
    assert config.category_id == "10"


@pytest.mark.parametrize("data", [
    None, [], {}, {"artist": ""}, {"artist": "A", "privacy_default": "secret"},
    {"artist": "A", "audit_passed": "yes"}, {"artist": "A", "shorts": "on"},
    {"artist": "A", "shorts": {"publish_offset_hours": -1}}, {"artist": "A", "category_id": "music"},
])
def test_invalid_config(data):
    with pytest.raises(ConfigError):
        parse_config(data, ROOT)


def test_find_root(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BEATPUB_HOME", raising=False)
    (tmp_path / "config.yaml").write_text("artist: A\n", encoding="utf-8")
    deep = tmp_path / "beats" / "Používateľ beat"
    deep.mkdir(parents=True)
    assert find_root(deep) == tmp_path.resolve()
    monkeypatch.setenv("BEATPUB_HOME", str(tmp_path))
    assert find_root(ROOT) == tmp_path.resolve()
    monkeypatch.setenv("BEATPUB_HOME", str(deep))
    with pytest.raises(ConfigError):
        find_root()
