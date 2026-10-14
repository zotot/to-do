"""Lecture des relevés de compte (CSV et OFX) exportés depuis la banque."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from .modeles import InfoReleve, Transaction

FORMATS_DATE = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%Y%m%d")

# Noms de colonnes possibles selon les banques (comparés sans accents ni casse).
COLONNES_DATE = ("date operation", "date", "date de l'operation", "dateop", "date comptable", "date valeur")
COLONNES_LIBELLE = ("libelle", "libelle operation", "libelle simplifie", "description", "label", "intitule", "detail", "operation")
COLONNES_MONTANT = ("montant", "montant(euros)", "montant (eur)", "amount", "valeur")
COLONNES_DEBIT = ("debit", "debit euros", "debit (eur)", "montant debit")
COLONNES_CREDIT = ("credit", "credit euros", "credit (eur)", "montant credit")


def _simplifier(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", texte).strip().lower()


def parser_date(valeur: str) -> date:
    valeur = valeur.strip()
    for fmt in FORMATS_DATE:
        try:
            return datetime.strptime(valeur, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Date non reconnue : {valeur!r}")


def parser_montant(valeur: str) -> float | None:
    """Accepte « -1 234,56 », « 1.234,56 € », « -45.90 », « 12,00- » ..."""
    v = valeur.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    v = v.replace("€", "").replace("EUR", "").replace("+", "")
    if not v:
        return None
    negatif = v.startswith("-") or v.endswith("-") or (v.startswith("(") and v.endswith(")"))
    v = v.strip("-()")
    if "," in v and "." in v:
        # le dernier séparateur est le séparateur décimal
        if v.rfind(",") > v.rfind("."):
            v = v.replace(".", "").replace(",", ".")
        else:
            v = v.replace(",", "")
    else:
        v = v.replace(",", ".")
    montant = float(v)
    return -montant if negatif else montant


def _trouver(entetes: list[str], candidats: tuple[str, ...]) -> int | None:
    simples = [_simplifier(e) for e in entetes]
    for cand in candidats:
        if cand in simples:
            return simples.index(cand)
    for i, e in enumerate(simples):
        if any(e.startswith(c) for c in candidats):
            return i
    return None


def _lire_texte(chemin: Path) -> str:
    brut = chemin.read_bytes()
    for encodage in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return brut.decode(encodage)
        except UnicodeDecodeError:
            continue
    return brut.decode("utf-8", errors="replace")


def lire_csv(chemin: Path) -> list[Transaction]:
    texte = _lire_texte(chemin)
    lignes = texte.splitlines()

    # Certaines banques ajoutent des lignes d'en-tête (n° de compte, solde...)
    # avant le tableau : on cherche la première ligne qui ressemble à un en-tête.
    debut = 0
    for i, ligne in enumerate(lignes):
        s = _simplifier(ligne)
        if "date" in s and any(m in s for m in ("libelle", "description", "label", "intitule", "operation", "detail")):
            debut = i
            break
    contenu = "\n".join(lignes[debut:])
    try:
        dialecte = csv.Sniffer().sniff(contenu[:4096], delimiters=";,\t|")
        separateur = dialecte.delimiter
    except csv.Error:
        separateur = ";"

    lecteur = csv.reader(io.StringIO(contenu), delimiter=separateur)
    entetes = next(lecteur, [])
    i_date = _trouver(entetes, COLONNES_DATE)
    i_lib = _trouver(entetes, COLONNES_LIBELLE)
    i_mnt = _trouver(entetes, COLONNES_MONTANT)
    i_deb = _trouver(entetes, COLONNES_DEBIT)
    i_cre = _trouver(entetes, COLONNES_CREDIT)

    if i_date is None or i_lib is None or (i_mnt is None and i_deb is None and i_cre is None):
        raise ValueError(
            f"{chemin.name} : colonnes introuvables. En-têtes lus : {entetes}. "
            "Il faut une colonne date, une colonne libellé et une colonne montant (ou débit/crédit)."
        )

    transactions = []
    for ligne in lecteur:
        if not ligne or len(ligne) <= max(i_date, i_lib):
            continue
        try:
            jour = parser_date(ligne[i_date])
        except ValueError:
            continue  # lignes de total, de solde, etc.
        libelle = ligne[i_lib].strip()
        if i_mnt is not None and i_mnt < len(ligne) and ligne[i_mnt].strip():
            montant = parser_montant(ligne[i_mnt])
        else:
            debit = parser_montant(ligne[i_deb]) if i_deb is not None and i_deb < len(ligne) else None
            credit = parser_montant(ligne[i_cre]) if i_cre is not None and i_cre < len(ligne) else None
            if debit:
                montant = -abs(debit)
            elif credit:
                montant = abs(credit)
            else:
                montant = None
        if montant is None:
            continue
        transactions.append(Transaction(jour, libelle, montant, chemin.name))
    return transactions


def lire_ofx(chemin: Path) -> list[Transaction]:
    texte = _lire_texte(chemin)
    transactions = []
    for bloc in re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|(?=</BANKTRANLIST>))", texte, re.S | re.I):
        def champ(nom: str) -> str | None:
            m = re.search(rf"<{nom}>([^<\r\n]*)", bloc, re.I)
            return m.group(1).strip() if m else None

        dt = champ("DTPOSTED")
        mnt = champ("TRNAMT")
        if not dt or not mnt:
            continue
        libelle = " ".join(x for x in (champ("NAME"), champ("MEMO")) if x)
        transactions.append(
            Transaction(parser_date(dt[:8]), libelle, parser_montant(mnt) or 0.0, chemin.name)
        )
    return transactions


def lire_ofx_avec_solde(chemin: Path) -> tuple[list[Transaction], InfoReleve]:
    transactions = lire_ofx(chemin)
    texte = _lire_texte(chemin)
    solde = date_solde = None
    m = re.search(r"<LEDGERBAL>.*?<BALAMT>([^<\r\n]+).*?<DTASOF>(\d{8})", texte, re.S | re.I)
    if m:
        solde, date_solde = parser_montant(m.group(1)), parser_date(m.group(2))
    return transactions, InfoReleve(
        fichier=chemin.name,
        debut=min((t.date for t in transactions), default=None),
        fin=date_solde or max((t.date for t in transactions), default=None),
        nb_operations=len(transactions),
        total=round(sum(t.montant for t in transactions), 2),
        solde_final=solde,
    )


def verifier_releves(infos: list[InfoReleve]) -> None:
    """Complète l'ancien solde manquant d'un relevé par le solde final du relevé précédent.

    Les relevés qui n'indiquent que le solde final (banques en ligne) deviennent ainsi
    vérifiables, à condition que les relevés se suivent.
    """
    ordonnes = sorted((i for i in infos if i.fin), key=lambda i: i.fin)
    for precedent, info in zip(ordonnes, ordonnes[1:]):
        if info.solde_initial is not None or precedent.solde_final is None or not info.debut:
            continue
        if 0 <= (info.debut - precedent.fin).days <= 10:
            info.solde_initial = precedent.solde_final


def charger_releves(
    chemins: list[Path], erreurs: list[str] | None = None, infos: list[InfoReleve] | None = None
) -> list[Transaction]:
    """Charge plusieurs relevés, supprime les doublons (relevés qui se chevauchent) et trie par date.

    Si `erreurs` est fourni, un fichier illisible y est signalé et ignoré au lieu d'interrompre
    le chargement (utile pour un dossier alimenté depuis plusieurs appareils).
    `infos` reçoit la période et les soldes des relevés qui les indiquent (PDF, OFX).
    """
    vues: set[tuple] = set()
    resultat = []
    for chemin in chemins:
        suffixe = chemin.suffix.lower()
        try:
            info = None
            if suffixe == ".pdf":
                from .pdf import lire_pdf

                lues, info = lire_pdf(chemin)
            elif suffixe in (".ofx", ".qfx"):
                lues, info = lire_ofx_avec_solde(chemin)
            elif suffixe in (".csv", ".txt", ".tsv"):
                lues = lire_csv(chemin)
            else:
                raise ValueError(f"Format non pris en charge : {chemin.name} (PDF, CSV ou OFX attendu)")
        except (ValueError, OSError) as e:
            if erreurs is None:
                raise
            message = str(e)
            erreurs.append(message if message.startswith(chemin.name) else f"{chemin.name} : {message}")
            continue
        if info is not None and infos is not None:
            infos.append(info)
        # Deux opérations identiques le même jour dans un même relevé sont légitimes :
        # on ne déduplique qu'entre fichiers différents.
        compteur: dict[tuple, int] = {}
        for t in lues:
            base = (t.date, _simplifier(t.libelle), round(t.montant, 2))
            compteur[base] = compteur.get(base, 0) + 1
            cle = (*base, compteur[base])
            if cle in vues:
                continue
            vues.add(cle)
            resultat.append(t)
    resultat.sort(key=lambda t: (t.date, t.libelle))
    return resultat
