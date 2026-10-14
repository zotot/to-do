"""Classement des opérations par catégorie de dépense, à partir de mots-clés du libellé."""

from __future__ import annotations

import re
import unicodedata

from .modeles import Transaction

REVENUS = "Revenus"
AUTRES = "Autres"
VIREMENTS_EMIS = "Virements émis"
EPARGNE = "Épargne"

# L'ordre compte : la première catégorie qui correspond l'emporte
# (ex. « AMAZON PRIME » avant « AMAZON », « ASSURANCE VIE » avant « ASSURANCE »).
REGLES_PAR_DEFAUT: list[tuple[str, list[str]]] = [
    (EPARGNE, ["LIVRET", "LIVRET A", "LDDS", "EPARGNE", "PEA", "ASSURANCE VIE"]),
    ("Logement", ["LOYER", "FONCIA", "NEXITY", "CITYA", "SYNDIC", "COPROPRIETE", "HLM", "OPAC"]),
    ("Énergie & eau", [
        "EDF", "ENGIE", "TOTALENERGIES", "TOTAL ENERGIES", "EKWATEUR", "VATTENFALL", "ENI",
        "GAZ", "VEOLIA", "SAUR", "SUEZ", "EAU",
    ]),
    ("Télécom & internet", ["FREE", "FREE MOBILE", "ORANGE", "SFR", "BOUYGUES", "SOSH", "B AND YOU"]),
    ("Abonnements & loisirs", [
        "NETFLIX", "SPOTIFY", "DEEZER", "DISNEY", "CANAL", "AMAZON PRIME", "PRIME VIDEO", "APPLE COM",
        "ITUNES", "YOUTUBE", "GOOGLE PLAY", "PLAYSTATION", "STEAM", "XBOX", "NINTENDO", "SALLE DE SPORT",
        "FITNESS", "BASIC FIT", "CINEMA", "UGC", "PATHE",
    ]),
    # Compte professionnel : abonnements logiciels (après les loisirs : « MICROSOFT XBOX » reste un loisir).
    ("Logiciels & services pro", [
        "OVH", "IONOS", "GANDI", "O2SWITCH", "GOOGLE WORKSPACE", "GSUITE", "MICROSOFT", "OFFICE 365", "ADOBE",
        "NOTION", "SLACK", "ZOOM", "CANVA", "DROPBOX", "SHOPIFY", "WIX", "SQUARESPACE", "AWS", "AMAZON WEB SERVICES",
        "PENNYLANE", "INDY", "TIIME", "QUICKBOOKS", "SAGE", "LINKEDIN", "CALENDLY", "DOCUSIGN", "YOUSIGN",
    ]),
    ("Restaurants & sorties", [
        "RESTAURANT", "RESTO", "MCDONALD", "MCDONALDS", "MC DONALD", "BURGER KING", "KFC", "QUICK",
        "SUBWAY", "DOMINOS", "PIZZA", "UBER EATS", "UBEREATS", "DELIVEROO", "JUST EAT", "BRASSERIE",
        "CAFE", "BAR", "STARBUCKS",
    ]),
    ("Alimentation", [
        "CARREFOUR", "LECLERC", "AUCHAN", "INTERMARCHE", "LIDL", "ALDI", "MONOPRIX", "FRANPRIX",
        "SUPER U", "HYPER U", "SYSTEME U", "U EXPRESS", "CORA", "PICARD", "BIOCOOP", "GRAND FRAIS",
        "NETTO", "CASINO", "BOULANGERIE", "BOUCHERIE", "PRIMEUR",
    ]),
    ("Transport", [
        "SNCF", "RATP", "NAVIGO", "UBER", "BLABLACAR", "TOTAL", "ESSO", "SHELL", "BP", "AVIA",
        "CARBURANT", "STATION", "AUTOROUTE", "VINCI", "SANEF", "APRR", "PARKING", "INDIGO",
    ]),
    ("Santé", [
        "PHARMACIE", "PHARMA", "MEDECIN", "DOCTEUR", "DENTISTE", "OPTICIEN", "LABORATOIRE", "KINE",
        "HOPITAL", "CLINIQUE", "DOCTOLIB", "CPAM",
    ]),
    # Compte professionnel : cotisations sociales et retraite des indépendants (avant « Assurances »).
    ("Charges sociales", [
        "URSSAF", "CIPAV", "CARMF", "CARPIMKO", "CAVEC", "CAVP", "CARCDSF", "CNBF", "SSI", "MSA", "IRCANTEC",
        "RETRAITE", "PREVOYANCE", "MADELIN", "AG2R", "MALAKOFF", "HUMANIS", "KLESIA", "PRO BTP",
    ]),
    ("Assurances", [
        "ASSURANCE", "ASSURANCES", "MAIF", "MACIF", "MAAF", "MATMUT", "GMF", "AXA", "ALLIANZ",
        "GROUPAMA", "MUTUELLE", "HARMONIE", "MGEN", "ALAN",
    ]),
    ("Crédits", ["ECHEANCE PRET", "PRET", "CETELEM", "COFIDIS", "SOFINCO", "FRANFINANCE", "ONEY"]),
    ("Impôts & taxes", [
        "DGFIP", "IMPOT", "IMPOTS", "TRESOR PUBLIC", "FINANCES PUBLIQUES", "TAXE", "AMENDE", "ANTAI",
        "TVA", "CFE", "CVAE", "SIE",
    ]),
    ("Famille & éducation", ["CRECHE", "ECOLE", "CANTINE", "PAJEMPLOI", "GARDERIE"]),
    ("Frais bancaires", [
        "FRAIS", "COTISATION", "COMMISSION", "COMMISSIONS", "AGIOS", "OFFRE GROUPEE", "TENUE DE COMPTE",
        "MONETIQUE", "LOCATION TPE", "FRAIS TPE", "INTERETS DEBITEURS",
    ]),
    ("Shopping", [
        "AMAZON", "CDISCOUNT", "FNAC", "DARTY", "BOULANGER", "IKEA", "LEROY MERLIN", "CASTORAMA", "ZARA",
        "KIABI", "DECATHLON", "ACTION", "PRIMARK", "VINTED", "ZALANDO", "SEPHORA", "ALIEXPRESS", "SHEIN",
    ]),
    ("Retraits d'espèces", ["RETRAIT", "DAB"]),
]

MOTS_REVENUS = ["SALAIRE", "PAIE", "CAF", "POLE EMPLOI", "FRANCE TRAVAIL", "PENSION", "RETRAITE", "PRIME ACTIVITE"]


def simplifier(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]", " ", texte).split())


def _motif(mots: list[str]) -> re.Pattern:
    alternatives = "|".join(re.escape(simplifier(m)) for m in mots if simplifier(m))
    return re.compile(rf"\b(?:{alternatives})\b")


class Categoriseur:
    """Associe une catégorie à chaque opération.

    `regles_perso` (catégorie -> mots-clés) passe avant les règles par défaut : c'est là que
    l'utilisateur ajoute ses propres marchands (« Animaux » : ["VETERINAIRE", "ZOOPLUS"]).
    """

    def __init__(self, regles_perso: dict[str, list[str]] | None = None):
        regles = list((regles_perso or {}).items()) + REGLES_PAR_DEFAUT
        self._regles = [(cat, _motif(mots)) for cat, mots in regles if mots]
        self._revenus = _motif(MOTS_REVENUS)

    def categorie_du_libelle(self, libelle: str) -> str | None:
        texte = simplifier(libelle)
        for categorie, motif in self._regles:
            if motif.search(texte):
                return categorie
        return None

    def categoriser(self, t: Transaction) -> str:
        texte = simplifier(t.libelle)
        if t.montant > 0:
            # Un virement reçu ou un salaire est un revenu, même si l'employeur s'appelle « Orange ».
            if self._revenus.search(texte) or re.match(r"VIR(EMENT)?\b.*\bRECU\b", texte):
                return REVENUS
            # Sinon, un crédit dans une catégorie de dépense est un remboursement (CPAM, retour Amazon…).
            return self.categorie_du_libelle(t.libelle) or REVENUS
        categorie = self.categorie_du_libelle(t.libelle)
        if categorie:
            return categorie
        if re.match(r"VIR", texte):
            return VIREMENTS_EMIS
        return AUTRES
