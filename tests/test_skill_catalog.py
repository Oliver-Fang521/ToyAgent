from pathlib import Path

import pytest


def test_skill_catalog_loads_only_registered_bounded_skill(tmp_path: Path):
    from gaia_agent.interactive.skill_catalog import SkillCatalog, SkillError

    directory = tmp_path / "pdf"
    directory.mkdir()
    (directory / "SKILL.md").write_text("---\nname: pdf\ndescription: Read PDFs\n---\n\nRead pages.", encoding="utf-8")
    catalog = SkillCatalog(tmp_path, {"pdf"})
    skill = catalog.load("pdf")
    assert skill.name == "pdf" and "Read pages." in skill.instructions
    with pytest.raises(SkillError):
        catalog.load("../other")
    with pytest.raises(SkillError):
        catalog.load("unknown")


def test_skill_catalog_rejects_symlinks_and_oversized_content(tmp_path: Path):
    from gaia_agent.interactive.skill_catalog import SkillCatalog, SkillError

    directory = tmp_path / "pdf"
    directory.mkdir()
    path = directory / "SKILL.md"
    path.write_text("x" * 17000)
    with pytest.raises(SkillError):
        SkillCatalog(tmp_path, {"pdf"}).load("pdf")
    path.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(SkillError):
        SkillCatalog(tmp_path, {"pdf"}).load("pdf")
