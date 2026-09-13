from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_package_metadata_uses_flower_brand_and_keeps_cli_aliases() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    scripts = project["scripts"]

    assert project["name"] == "flower-chat-agent"
    assert "flower" in project["description"].casefold()
    assert scripts["flower-agent"] == scripts["nba-agent"]
    assert scripts["flower-agent-eval"] == scripts["nba-agent-eval"]

    frontend = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert frontend["name"] == "flower-agent-e2e"
    assert "种花 Agent" in frontend["description"]


def test_container_defaults_are_flower_branded_and_loopback_only() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "USER floweragent" in dockerfile
    assert "USER nbaagent" not in dockerfile
    assert "flower-agent:" in compose
    assert '"127.0.0.1:8000:8000"' in compose
    assert "profiles: [nba-compat]" in compose
    # The image delegates listener selection to Settings rather than baking a
    # public interface into the Docker command. Compose may use the container
    # interface for forwarding, but publication on the host stays loopback.
    assert 'CMD ["python", "-m", "apps.api.src.main"]' in dockerfile
    assert '"--host", "0.0.0.0"' not in dockerfile
    assert "BIND_HOST: 0.0.0.0" in compose


def test_readme_presents_the_flower_product_first() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# 种花 Agent\n")
    assert "北阳台适合种什么花" in readme
    assert "uvicorn apps.api.src.main:app --host 127.0.0.1" in readme
