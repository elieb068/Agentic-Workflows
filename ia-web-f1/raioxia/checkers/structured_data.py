"""Check 6: JSON-LD structured data on the home page (raw HTML, as crawlers see it)."""

import json
from collections.abc import Iterator
from typing import Any

from raioxia.checkers import register
from raioxia.checkers.base import AnalysisContext, Checker
from raioxia.models import CheckResult, Status
from raioxia.net.fetcher import FetchError
from raioxia.parsing.html import HtmlDoc

# "Who the business is". LocalBusiness has hundreds of subtypes; these are the common ones.
ORGANIZATION_TYPES = {
    "Organization", "Corporation", "OnlineStore", "OnlineBusiness", "NGO", "EducationalOrganization",
    "MedicalOrganization", "NewsMediaOrganization", "SportsOrganization", "LocalBusiness",
    "AnimalShelter", "AutomotiveBusiness", "AutoRepair", "AutoDealer", "ChildCare", "DryCleaningOrLaundry",
    "EmergencyService", "EmploymentAgency", "EntertainmentBusiness", "FinancialService", "AccountingService",
    "InsuranceAgency", "FoodEstablishment", "Bakery", "BarOrPub", "CafeOrCoffeeShop", "FastFoodRestaurant",
    "IceCreamShop", "Restaurant", "Winery", "GovernmentOffice", "HealthAndBeautyBusiness", "BeautySalon",
    "DaySpa", "HairSalon", "HealthClub", "NailSalon", "TattooParlor", "HomeAndConstructionBusiness",
    "Electrician", "GeneralContractor", "HVACBusiness", "HousePainter", "Locksmith", "MovingCompany",
    "Plumber", "RoofingContractor", "InternetCafe", "LegalService", "Attorney", "Notary", "Library",
    "LodgingBusiness", "Hotel", "Hostel", "Motel", "BedAndBreakfast", "MedicalBusiness", "Dentist",
    "MedicalClinic", "Optician", "Pharmacy", "Physician", "VeterinaryCare", "ProfessionalService",
    "RealEstateAgent", "SelfStorage", "ShoppingCenter", "SportsActivityLocation", "Store", "BikeStore",
    "BookStore", "ClothingStore", "ComputerStore", "ConvenienceStore", "ElectronicsStore", "Florist",
    "FurnitureStore", "GardenStore", "GroceryStore", "HardwareStore", "HomeGoodsStore", "JewelryStore",
    "LiquorStore", "PetStore", "ShoeStore", "SportingGoodsStore", "ToyStore", "TravelAgency",
}
OTHER_RELEVANT_TYPES = {"Product", "FAQPage"}


def _short_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1] or None


def iter_types(node: Any) -> Iterator[str]:
    """Every @type in a JSON-LD document, including nested nodes and @graph."""
    if isinstance(node, list):
        for item in node:
            yield from iter_types(item)
    elif isinstance(node, dict):
        raw = node.get("@type")
        for value in raw if isinstance(raw, list) else [raw]:
            short = _short_type(value)
            if short:
                yield short
        for key, value in node.items():
            if key != "@type" and isinstance(value, dict | list):
                yield from iter_types(value)


def _unwrap(text: str) -> str:
    text = text.strip()
    for opener, closer in (("<!--", "-->"), ("//<![CDATA[", "//]]>"), ("<![CDATA[", "]]>")):
        if text.startswith(opener) and text.endswith(closer):
            text = text[len(opener) : -len(closer)].strip()
    return text


def parse_blocks(blocks: list[str]) -> list[dict]:
    parsed = []
    for raw in blocks:
        try:
            data = json.loads(_unwrap(raw))
        except json.JSONDecodeError as exc:
            parsed.append({"valid": False, "error": f"{exc.msg} (linha {exc.lineno}, coluna {exc.colno})", "types": []})
            continue
        parsed.append({"valid": True, "types": sorted(set(iter_types(data)))})
    return parsed


@register
class StructuredDataChecker(Checker):
    id = "structured_data"
    titulo = "Dados estruturados (JSON-LD)"

    async def run(self, ctx: AnalysisContext) -> CheckResult:
        try:
            home = await ctx.home()
        except FetchError as exc:
            return self.result(
                Status.UNKNOWN,
                "Não conseguimos abrir sua página inicial para verificar este item.",
                evidencia={"fetch_error": exc.kind},
            )
        if home.status_code >= 400:
            return self.result(
                Status.UNKNOWN,
                "Sua página inicial respondeu com erro, então não deu para verificar este item.",
                evidencia={"status_code": home.status_code},
            )

        doc = HtmlDoc(home.text)
        blocks = parse_blocks(doc.jsonld_blocks())
        types = sorted({t for b in blocks for t in b["types"]})
        organization = sorted(set(types) & ORGANIZATION_TYPES)
        relevant = organization + sorted(set(types) & OTHER_RELEVANT_TYPES)
        invalid = [b for b in blocks if not b["valid"]]
        microdata = sorted({t for t in (_short_type(m) for m in doc.microdata_types()) if t})
        evidence = {
            "blocks": blocks,
            "types": types,
            "relevant_types": relevant,
            "microdata_types": microdata,
        }
        how_to_add = (
            "Adicione um bloco JSON-LD do tipo Organization (ou LocalBusiness, se você atende numa cidade) com "
            "nome, endereço, telefone e site. No WordPress, plugins como Yoast ou Rank Math fazem isso. Depois, "
            "confira no teste gratuito do Google: search.google.com/test/rich-results"
        )

        if invalid:
            valid_note = f" Os blocos válidos trazem: {', '.join(types)}." if types else ""
            return self.result(
                Status.ERROR,
                f"Encontramos {len(invalid)} bloco(s) de dados estruturados com erro de formatação. Os robôs "
                f"descartam blocos com erro, então essas informações não chegam à IA.{valid_note}",
                "Corrija o bloco com erro: o teste gratuito do Google (search.google.com/test/rich-results) "
                "aponta a linha exata. Erros comuns são vírgula sobrando no fim de uma lista e aspas faltando.",
                evidence,
            )

        if organization:
            return self.result(
                Status.OK,
                f"Seu site tem dados estruturados (JSON-LD) que dizem à IA, de forma organizada, quem você é "
                f"({', '.join(relevant)}). Isso ajuda a IA a citar nome, endereço e contato sem adivinhar.",
                evidencia=evidence,
            )

        if relevant:  # Product / FAQPage, but nothing about the business itself
            return self.result(
                Status.OK,
                f"Seu site tem dados estruturados (JSON-LD) do tipo {', '.join(relevant)}, que ajudam a IA a "
                "entender o seu conteúdo.",
                "Dica: acrescente também um bloco Organization ou LocalBusiness com nome, endereço e contato "
                "da empresa.",
                evidence,
            )

        if types:
            return self.result(
                Status.WARNING,
                f"Seu site tem dados estruturados, mas só dos tipos {', '.join(types)}. Falta o principal: "
                "dizer quem é a empresa (nome, endereço, telefone), o que ajuda a IA a recomendar você.",
                how_to_add,
                evidence,
            )

        microdata_note = (
            f" Encontramos dados no formato antigo microdata ({', '.join(microdata)}); a IA lê melhor o JSON-LD."
            if microdata
            else ""
        )
        return self.result(
            Status.WARNING,
            "Seu site não tem dados estruturados (JSON-LD). São informações organizadas, escondidas no código, "
            "que dizem a buscadores e IAs o nome da empresa, endereço, telefone, horário e produtos, sem "
            f"precisar adivinhar.{microdata_note}",
            how_to_add,
            evidence,
        )
