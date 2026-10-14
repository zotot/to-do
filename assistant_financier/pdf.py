"""Lecture des relevés de compte PDF.

Chaque banque a sa mise en page, mais les relevés français suivent presque tous le même modèle :
un tableau « Date | (Valeur) | Libellé | Débit | Crédit » (ou une seule colonne « Montant »
signée), des libellés parfois sur plusieurs lignes, et un ancien et/ou un nouveau solde.

On reconstitue les lignes du tableau à partir de la position du texte sur la page, puis on
contrôle la lecture : ancien solde + crédits - débits doit redonner le nouveau solde.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .modeles import InfoReleve, Transaction

RE_DATE_DEBUT = re.compile(r"^(\d{2})[./-](\d{2})(?:[./-](\d{4}|\d{2}))?(?=\s|$)")
RE_DATE_COMPLETE = re.compile(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b")
RE_MONTANT = re.compile(r"^([+-])?\s?(\d{1,3}(?:[\s  .]\d{3})*|\d+),(\d{2})\s?(?:€|EUR)?\s?(-)?$")
RE_SOLDE = re.compile(r"\bSOLDE\b")
RE_TOTAL = re.compile(r"^(TOTAL|SOUS[ -]TOTAL|REPORT|A REPORTER)\b")

TOLERANCE_LIGNE = 2.5  # points : deux morceaux de texte à cette hauteur près sont sur la même ligne
ECART_CONTINUATION = 15  # points : une suite de libellé suit sa ligne de près
MAX_CONTINUATIONS = 3


@dataclass
class _Segment:
    x0: float
    x1: float
    texte: str

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class _Ligne:
    page: int
    y: float
    segments: list[_Segment] = field(default_factory=list)

    @property
    def texte(self) -> str:
        return " ".join(s.texte for s in self.segments)


@dataclass
class _Colonnes:
    debit: float | None = None
    credit: float | None = None
    montant: float | None = None  # colonne unique, signée ou complétée par une colonne « Sens »
    sens: float | None = None  # colonne « Sens » : D (débit) / C (crédit)
    libelle: float | None = None


def _simplifier(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().upper()
    return " ".join(texte.split())


def montant_pdf(texte: str) -> float | None:
    """Montant au format français (« 1 234,56 », « -19,99 € », « 12,00- ») ou None."""
    m = RE_MONTANT.match(texte.strip())
    if not m:
        return None
    signe, entier, centimes, signe_final = m.groups()
    valeur = float(re.sub(r"[\s  .]", "", entier) + "." + centimes)
    return -valeur if signe == "-" or signe_final else valeur


def _extraire_lignes(chemin: Path) -> list[_Ligne]:
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LAParams, LTTextLineHorizontal
    except ImportError as e:  # pragma: no cover - dépend de l'installation
        raise ValueError("lecture des PDF impossible : installez pdfminer.six (pip install pdfminer.six)") from e

    lignes: list[_Ligne] = []
    try:
        pages = list(extract_pages(str(chemin), laparams=LAParams(char_margin=1.5, line_margin=0.3)))
    except Exception as e:  # pdfminer lève des exceptions variées (mot de passe, fichier corrompu…)
        nom = type(e).__name__
        if "Password" in nom or "Encrypt" in nom:
            raise ValueError("PDF protégé par mot de passe : enregistrez-le sans mot de passe") from e
        raise ValueError(f"PDF illisible ({nom})") from e

    for numero, page in enumerate(pages, 1):
        segments: list[tuple[float, _Segment]] = []

        def visiter(objet) -> None:
            if isinstance(objet, LTTextLineHorizontal):
                texte = " ".join(objet.get_text().split())
                if texte:
                    segments.append(((objet.y0 + objet.y1) / 2, _Segment(objet.x0, objet.x1, texte)))
            elif hasattr(objet, "__iter__"):
                for enfant in objet:
                    visiter(enfant)

        visiter(page)
        segments.sort(key=lambda s: -s[0])
        page_lignes: list[_Ligne] = []
        for y, segment in segments:
            if page_lignes and abs(page_lignes[-1].y - y) <= TOLERANCE_LIGNE:
                page_lignes[-1].segments.append(segment)
            else:
                page_lignes.append(_Ligne(numero, y, [segment]))
        for ligne in page_lignes:
            ligne.segments.sort(key=lambda s: s.x0)
        lignes += page_lignes
    return lignes


def _entete(ligne: _Ligne) -> _Colonnes | None:
    """Reconnaît la ligne d'en-tête du tableau et la position de ses colonnes."""
    col = _Colonnes()
    for s in ligne.segments:
        t = _simplifier(s.texte)
        if re.match(r"^(DEBIT|DEBITS|DEBIT EUROS|DEBIT \(EUR\)|SORTIES?)\b", t):
            col.debit = s.centre
        elif re.match(r"^(CREDIT|CREDITS|CREDIT EUROS|CREDIT \(EUR\)|ENTREES?)\b", t):
            col.credit = s.centre
        elif re.match(r"^(MONTANT|MONTANTS)\b", t):
            col.montant = s.centre
        elif t == "SENS":
            col.sens = s.centre
        elif re.match(r"^(LIBELLE|NATURE|DESIGNATION|DETAIL|OPERATIONS?|DESCRIPTION)\b", t):
            col.libelle = s.x0
    a_date = any(_simplifier(s.texte).startswith("DATE") for s in ligne.segments)
    if (col.debit is not None and col.credit is not None) or (col.montant is not None and a_date):
        return col
    return None


def _periode(lignes: list[_Ligne]) -> tuple[date | None, date | None]:
    """Première et dernière date complètes (JJ/MM/AAAA) citées dans le relevé, hors opérations."""
    dates = []
    for ligne in lignes:
        for j, m, a in RE_DATE_COMPLETE.findall(ligne.texte):
            try:
                dates.append(date(int(a), int(m), int(j)))
            except ValueError:
                pass
    return (min(dates), max(dates)) if dates else (None, None)


def _date_operation(m: re.Match, fin: date | None) -> date | None:
    jour, mois, annee = int(m.group(1)), int(m.group(2)), m.group(3)
    if annee:
        a = int(annee) + (2000 if len(annee) == 2 else 0)
    elif fin:
        # Date sans année : celle de la fin du relevé, ou l'année d'avant pour un relevé
        # à cheval sur deux années (opérations de décembre dans un relevé arrêté en janvier).
        a = fin.year if (mois, jour) <= (fin.month, fin.day) else fin.year - 1
    else:
        return None
    try:
        return date(a, mois, jour)
    except ValueError:
        return None


def _montants(ligne: _Ligne, col: _Colonnes) -> tuple[list[tuple[_Segment, float]], list[_Segment]]:
    """Sépare les montants (colonnes de droite) du reste du texte de la ligne."""
    montants, reste = [], []
    for s in ligne.segments:
        valeur = montant_pdf(s.texte)
        if valeur is not None and (col.libelle is None or s.x0 > col.libelle + 40):
            montants.append((s, valeur))
            continue
        # Montant collé au libellé par l'extraction : « LIBELLE 12,50 »
        m = re.match(r"^(.*\S)\s+([+-]?\d{1,3}(?:[\s .]\d{3})*,\d{2}\s?(?:€|EUR)?-?)$", s.texte)
        if m and montant_pdf(m.group(2)) is not None and s is ligne.segments[-1] and len(ligne.segments) > 1:
            reste.append(_Segment(s.x0, s.x1, m.group(1)))
            montants.append((_Segment(s.x1 - 1, s.x1, m.group(2)), montant_pdf(m.group(2))))
            continue
        reste.append(s)
    return montants, reste


SENS_DEBIT = {"D", "DB", "DEB", "DEBIT"}
SENS_CREDIT = {"C", "CR", "CRE", "CREDIT"}


def _extraire_sens(reste: list[_Segment], col: _Colonnes) -> tuple[str | None, list[_Segment]]:
    """Retire de la ligne l'indicateur D/C de la colonne « Sens », s'il y en a une."""
    if col.sens is None:
        return None, reste
    for s in reste:
        t = _simplifier(s.texte)
        if abs(s.centre - col.sens) <= 25 and t in SENS_DEBIT | SENS_CREDIT:
            return ("D" if t in SENS_DEBIT else "C"), [r for r in reste if r is not s]
    return None, reste


def _signer(segment: _Segment, valeur: float, texte_ligne: str, col: _Colonnes, sens: str | None = None) -> float:
    """Débit (négatif) ou crédit (positif) selon la colonne où se trouve le montant."""
    if sens:
        return -abs(valeur) if sens == "D" else abs(valeur)
    if col.debit is not None and col.credit is not None:
        if abs(segment.centre - col.debit) < abs(segment.centre - col.credit):
            return -abs(valeur)
        return abs(valeur)
    if "DEBITEUR" in texte_ligne:
        return -abs(valeur)
    return valeur  # colonne « Montant » : le signe est dans le texte


def lire_pdf(chemin: Path, trace: list[tuple[_Ligne, str]] | None = None) -> tuple[list[Transaction], InfoReleve]:
    """Opérations et soldes d'un relevé PDF. `trace` reçoit l'interprétation de chaque ligne (diagnostic)."""
    lignes = _extraire_lignes(chemin)
    if not lignes:
        raise ValueError("aucun texte dans ce PDF (relevé scanné ?) : utilisez le relevé téléchargé depuis votre banque")
    debut, fin = _periode(lignes)

    transactions: list[Transaction] = []
    soldes: list[tuple[date | None, float, str]] = []
    col: _Colonnes | None = None
    courante: dict | None = None  # opération en cours, pour rattacher les suites de libellé

    derniere_date: date | None = None  # date de la dernière opération (dates affichées une fois par jour)
    apres_solde = False  # après un solde, les montants sans date sont des pieds de page, pas des opérations

    def terminer() -> None:
        nonlocal courante
        if courante and courante["montant"] is not None:
            transactions.append(Transaction(courante["date"], " ".join(courante["libelle"]), courante["montant"], chemin.name))
        courante = None

    def noter(ligne: _Ligne, interpretation: str) -> None:
        if trace is not None:
            trace.append((ligne, interpretation))

    for ligne in lignes:
        nouvelles = _entete(ligne)
        if nouvelles:
            terminer()
            col = nouvelles
            noter(ligne, "en-tête du tableau")
            continue
        if col is None:
            noter(ligne, "avant le tableau")
            continue  # coordonnées, adresse…

        texte = _simplifier(ligne.texte)
        montants, reste = _montants(ligne, col)
        sens, reste = _extraire_sens(reste, col)
        texte_reste = " ".join(s.texte for s in reste)

        if RE_SOLDE.search(texte) and montants:
            terminer()
            apres_solde = True
            m = RE_DATE_COMPLETE.search(ligne.texte)
            jour = date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
            segment, valeur = montants[-1]
            soldes.append((jour, _signer(segment, valeur, texte, col, sens), texte))
            noter(ligne, "solde" + (" daté" if jour else " sans date"))
            continue
        if RE_TOTAL.match(_simplifier(texte_reste)):
            terminer()
            noter(ligne, "total (ignoré)")
            continue

        def signe(montant: float) -> str:
            return "débit" if montant < 0 else "crédit"

        m = RE_DATE_DEBUT.match(texte_reste)
        if m:
            jour = _date_operation(m, fin)
            if jour is None:
                terminer()
                noter(ligne, "date invalide (ignorée)")
                continue
            terminer()
            libelle = texte_reste[m.end():].strip()
            libelle = RE_DATE_DEBUT.sub("", libelle, count=1).strip()  # date de valeur
            montant = _signer(*montants[-1], texte, col, sens) if montants else None
            courante = {"date": jour, "libelle": [libelle], "montant": montant,
                        "page": ligne.page, "y": ligne.y, "suites": 0}
            derniere_date, apres_solde = jour, False
            noter(ligne, f"opération ({signe(montant)})" if montant is not None else "opération (montant à suivre)")
            continue

        proche = courante is not None and ligne.page == courante["page"] and courante["y"] - ligne.y <= ECART_CONTINUATION
        # Montant décalé d'une ligne par rapport à la date et au libellé (police, libellé sur 2 lignes).
        if montants and proche and courante["montant"] is None:
            courante["montant"] = _signer(*montants[-1], texte, col, sens)
            if texte_reste:
                courante["libelle"].append(texte_reste)
            courante["y"] = ligne.y
            noter(ligne, f"montant de l'opération ({signe(courante['montant'])})")
            continue
        # Autre opération du même jour : la date n'est imprimée que sur la première.
        if (
            montants and texte_reste and derniere_date and not apres_solde
            and (col.libelle is None or reste[0].x0 >= col.libelle - 5)
        ):
            terminer()
            montant = _signer(*montants[-1], texte, col, sens)
            courante = {"date": derniere_date, "libelle": [texte_reste], "montant": montant,
                        "page": ligne.page, "y": ligne.y, "suites": 0}
            noter(ligne, f"opération du même jour ({signe(montant)})")
            continue

        # Suite du libellé de l'opération précédente (détails SEPA, référence…)
        if (
            courante and not montants and texte_reste
            and ligne.page == courante["page"]
            and courante["y"] - ligne.y <= ECART_CONTINUATION
            and courante["suites"] < MAX_CONTINUATIONS
            and (col.libelle is None or reste[0].x0 >= col.libelle - 5)
        ):
            courante["libelle"].append(texte_reste)
            courante["y"] = ligne.y
            courante["suites"] += 1
            noter(ligne, "suite du libellé")
        else:
            terminer()
            noter(ligne, "montant sans date (ignoré)" if montants else "ignorée")
    terminer()

    if not transactions and col is None:
        raise ValueError("tableau des opérations introuvable (colonnes Date, Libellé, Débit/Crédit ou Montant)")

    datees = sorted((s for s in soldes if s[0]), key=lambda s: s[0])
    initial = datees[0] if len(datees) >= 2 else None
    final = datees[-1] if datees else (soldes[-1] if soldes else None)
    if initial is None and len(soldes) >= 2 and not datees:
        initial = soldes[0]
    info = InfoReleve(
        fichier=chemin.name,
        debut=debut or (min(t.date for t in transactions) if transactions else None),
        fin=(final[0] if final and final[0] else fin) or (max(t.date for t in transactions) if transactions else None),
        nb_operations=len(transactions),
        total=round(sum(t.montant for t in transactions), 2),
        solde_initial=initial[1] if initial else None,
        solde_final=final[1] if final else None,
    )
    return transactions, info


# Mots génériques conservés par le diagnostic : ils décrivent la structure du relevé, pas vous.
MOTS_CONSERVES = set(
    "SOLDE CREDITEUR DEBITEUR AU DU LE AL DES DE ET EN TOTAL TOTAUX SOUS MOUVEMENTS OPERATION OPERATIONS "
    "NOUVEAU ANCIEN PRECEDENT FINAL REPORT REPORTER DATE DATES VALEUR LIBELLE LIBELLES NATURE DESIGNATION "
    "DETAIL DEBIT DEBITS CREDIT CREDITS EUROS EUR MONTANT SENS PAGE RELEVE COMPTE COURANT CB CARTE PRLV "
    "PRELEVEMENT SEPA VIR VIREMENT RECU EMIS INST FACTURE COMMISSION COMMISSIONS FRAIS COTISATION REMISE "
    "REMISES CHEQUE CHQ RETRAIT DAB PAIEMENT ECHEANCE PRET INTERETS TVA TPE PERIODE ARRETE AGIOS "
    "D C DB CR DEB CRE".split()
)


def masquer(texte: str) -> str:
    """Anonymise un texte : chiffres remplacés par 9, mots non génériques par des X."""
    def mot(m: re.Match) -> str:
        if _simplifier(m.group(0)) in MOTS_CONSERVES:
            return m.group(0)
        return "".join("X" if c.isupper() else "x" for c in m.group(0))
    return re.sub(r"\d", "9", re.sub(r"[^\W\d_]+", mot, texte))


def diagnostic_pdf(chemin: Path) -> str:
    """Structure anonymisée d'un relevé PDF et interprétation de chaque ligne, pour ajuster la lecture."""
    sortie = [f"Diagnostic de lecture : {masquer(chemin.name)} (anonymisé : chiffres remplacés par 9, noms par X)"]
    trace: list[tuple[_Ligne, str]] = []
    try:
        transactions, info = lire_pdf(chemin, trace)
        debits = sum(1 for t in transactions if t.montant < 0)
        sortie.append(
            f"{len(transactions)} opérations lues ({debits} débits, {len(transactions) - debits} crédits) ; "
            f"ancien solde {'trouvé' if info.solde_initial is not None else 'absent'}, "
            f"nouveau solde {'trouvé' if info.solde_final is not None else 'absent'} ; "
            + ("soldes cohérents" if info.ecart == 0 else "soldes NON cohérents" if info.ecart is not None else "contrôle impossible")
        )
    except ValueError as e:
        sortie.append(f"Erreur : {masquer(str(e))}")
    sortie.append("")
    sortie.append("page  hauteur  interprétation              position:texte")
    for ligne, interpretation in trace:
        segments = " | ".join(f"{s.x0:.0f}:{masquer(s.texte)}" for s in ligne.segments)
        sortie.append(f"p{ligne.page:<3} {ligne.y:7.0f}  {interpretation:<27} {segments}")
    return "\n".join(sortie)
