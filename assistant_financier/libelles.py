"""Normalisation des libellés bancaires.

Un même prélèvement change de libellé d'un mois à l'autre (date, référence de
mandat, n° d'échéance...). On en extrait une clé stable, par exemple :

    "PRLV SEPA FREE MOBILE FM-1234567 ECH/050126 ID EMETTEUR/FR12ZZZ" -> "FREE MOBILE"
    "CB NETFLIX.COM 05/01"                                           -> "NETFLIX COM"
"""

from __future__ import annotations

import re
import unicodedata

# Préfixes techniques ajoutés par les banques (ordre : du plus long au plus court).
PREFIXES = [
    r"PAIEMENT PAR CARTE( X?\d+)?",
    r"PAIEMENT CB",
    r"FACTURE CARTE( DU \d+)?( X?\d+)?",
    r"CARTE X?\d+",
    r"PRLV SEPA",
    r"PRELEVEMENT( SEPA)?( EUROPEEN)?",
    r"PRELEV(T)?",
    r"PRLV",
    r"ECHEANCE( PRET)?",
    r"VIR(EMENT)? SEPA (RECU|EMIS)",
    r"VIR(EMENT)? (RECU|EMIS|INST(ANTANE)?|PERMANENT)",
    r"VIREMENT( SEPA)?",
    r"VIR SEPA",
    r"VIR",
    r"CB",
    r"DE:",
    r"/DE",
]

# Tout ce qui suit ces marqueurs est de la référence technique.
COUPURES = [
    r"\bECH/", r"\bID EMETTEUR", r"\bMDT/", r"\bREF/", r"\bREF\b", r"\bRUM\b", r"\bMOTIF\b",
    r"\b/MOTIF", r"\bLIB/", r"\bNO ", r"\bN°", r"\bDATE ", r"\b/REF", r"\b/ID",
]

MOIS = (
    "JANVIER FEVRIER MARS AVRIL MAI JUIN JUILLET AOUT SEPTEMBRE OCTOBRE NOVEMBRE DECEMBRE "
    "JANV FEV FEVR AVR JUIL SEPT OCT NOV DEC "
    "JANUARY FEBRUARY MARCH APRIL JUNE JULY AUGUST SEPTEMBER OCTOBER NOVEMBER DECEMBER"
).split()

MOTS_VIDES = {"DE", "DU", "LA", "LE", "LES", "DES", "ET", "EUR", "EUROS", "SA", "SAS", "SARL", "FR", "PAR", "POUR"}


def normaliser(libelle: str) -> str:
    s = unicodedata.normalize("NFKD", libelle).encode("ascii", "ignore").decode().upper()
    s = re.sub(r"\s+", " ", s).strip()
    original = s

    for prefixe in PREFIXES:
        s = re.sub(rf"^{prefixe}\b\s*", "", s).strip()

    for coupure in COUPURES:
        m = re.search(coupure, s)
        if m and m.start() > 0:
            s = s[: m.start()]

    s = re.sub(r"\b\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?\b", " ", s)  # dates
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    mots = [
        m for m in s.split()
        if not any(c.isdigit() for c in m)  # références, n° de carte, montants
        and m not in MOIS
        and m not in MOTS_VIDES
        and len(m) > 2
    ]
    if not mots:
        # Libellé réduit à son préfixe (ex. « ECHEANCE PRET 00123 ») : on garde le préfixe.
        mots = [m for m in re.sub(r"[^A-Z ]", " ", original).split() if m not in MOTS_VIDES]
    return " ".join(mots[:4]) or original
