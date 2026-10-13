from pathlib import Path

from raioxia.parsing.llms_txt import parse_llms_txt

FIXTURES = Path(__file__).parent / "fixtures" / "llms"


def test_valid_file():
    doc = parse_llms_txt((FIXTURES / "valid.txt").read_text(encoding="utf-8"))
    assert doc.title == "Padaria Pão Quente" and doc.title_is_first
    assert doc.summary.startswith("Padaria artesanal")
    assert doc.sections == {"Páginas principais": 2, "Optional": 1}
    assert [link.url for link in doc.links] == [
        "https://site.example/cardapio", "/encomendas", "https://site.example/historia",
    ]
    assert doc.issues == []


def test_incomplete_file():
    doc = parse_llms_txt((FIXTURES / "incomplete.txt").read_text(encoding="utf-8"))
    assert doc.issues == ["title_not_first", "missing_summary", "no_link_sections"]
    assert doc.other_links == ["https://site.example/cardapio"]


def test_missing_title_and_multiple_titles():
    assert "missing_title" in parse_llms_txt("## Só seção\n- [a](/a)").issues
    assert "multiple_titles" in parse_llms_txt("# A\n> r\n# B\n## S\n- [a](/a)").issues


def test_headings_inside_code_fences_are_ignored():
    doc = parse_llms_txt("# Real\n> resumo\n```\n# not a title\n## not a section\n```\n## S\n- [a](/a)")
    assert doc.h1_count == 1 and list(doc.sections) == ["S"] and doc.issues == []


def test_link_with_title_and_bullet_variants():
    doc = parse_llms_txt('# T\n> r\n## S\n* [A](/a "tooltip"): x\n+ [B]( /b )')
    assert [link.url for link in doc.links] == ["/a", "/b"]


def test_bom_and_blank_lines_before_title():
    doc = parse_llms_txt("﻿\n\n# T\n\n> resumo\n## S\n- [a](/a)")
    assert doc.title_is_first and doc.summary == "resumo"
