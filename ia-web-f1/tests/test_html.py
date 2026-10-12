from raioxia.parsing.html import HtmlDoc


def test_title_lang_and_meta():
    doc = HtmlDoc(
        '<html lang="pt-BR"><head><title>\n  Padaria   X </title>'
        '<META NAME="Description" content=" Pães  frescos "></head></html>'
    )
    assert doc.title == "Padaria X"
    assert doc.lang == "pt-BR"
    assert doc.meta("description") == "Pães frescos"


def test_missing_and_empty_values_are_none():
    doc = HtmlDoc('<html lang=""><head><title>  </title><meta name="description" content=""></head></html>')
    assert doc.title is None and doc.lang is None and doc.meta("description") is None
    assert HtmlDoc("not html at all").title is None
