"""Suivi du budget mensuel par catégorie."""

from __future__ import annotations

import calendar
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .categories import EPARGNE, REVENUS, Categoriseur
from .modeles import Prevision, Transaction

Mois = tuple[int, int]  # (année, mois)

TOLERANCE_JOURS = 5  # un relevé qui commence le 4 du mois couvre quand même tout le mois


@dataclass
class LigneBudget:
    categorie: str
    depense: float  # montant dépensé sur le mois (positif, remboursements déduits)
    prevu: float  # dépense attendue en fin de mois (= depense si le mois est terminé)
    moyenne: float  # moyenne mensuelle sur les mois complets des relevés
    budget: float | None

    @property
    def reste(self) -> float | None:
        return None if self.budget is None else self.budget - self.prevu

    @property
    def taux(self) -> float | None:
        return self.prevu / self.budget if self.budget else None


@dataclass
class RapportBudget:
    mois: Mois
    complet: bool
    lignes: list[LigneBudget]
    revenus: float
    revenus_prevus: float
    non_classees: list[Transaction]

    @property
    def depenses(self) -> float:
        return sum(l.depense for l in self.lignes if l.categorie != EPARGNE)

    @property
    def depenses_prevues(self) -> float:
        return sum(l.prevu for l in self.lignes if l.categorie != EPARGNE)


def _fin_du_mois(mois: Mois) -> date:
    return date(mois[0], mois[1], calendar.monthrange(*mois)[1])


def _mois_de(d: date) -> Mois:
    return (d.year, d.month)


def mois_complets(debut: date, fin: date) -> list[Mois]:
    """Mois entièrement couverts par les relevés."""
    resultat = []
    a, m = debut.year, debut.month
    while (a, m) <= _mois_de(fin):
        premier, dernier = date(a, m, 1), _fin_du_mois((a, m))
        if debut <= premier + timedelta(days=TOLERANCE_JOURS) and fin >= dernier - timedelta(days=TOLERANCE_JOURS):
            resultat.append((a, m))
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return resultat


def totaux_par_mois(transactions: list[Transaction], cat: Categoriseur) -> dict[Mois, dict[str, float]]:
    """Dépenses par mois et par catégorie (montants positifs), revenus sous la clé REVENUS."""
    totaux: dict[Mois, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in transactions:
        categorie = cat.categoriser(t)
        # Une dépense compte en positif ; un remboursement (crédit hors revenus) vient en déduction.
        totaux[_mois_de(t.date)][categorie] += t.montant if categorie == REVENUS else -t.montant
    return totaux


def moyennes_mensuelles(transactions: list[Transaction], cat: Categoriseur, fin: date | None = None) -> dict[str, float]:
    if not transactions:
        return {}
    totaux = totaux_par_mois(transactions, cat)
    complets = mois_complets(transactions[0].date, fin or transactions[-1].date) or list(totaux)
    sommes: dict[str, float] = defaultdict(float)
    for m in complets:
        for categorie, montant in totaux.get(m, {}).items():
            sommes[categorie] += montant
    return {c: round(s / len(complets), 2) for c, s in sommes.items()}


def suggerer_budget(transactions: list[Transaction], cat: Categoriseur, fin: date | None = None) -> dict[str, float]:
    """Budget de départ : la moyenne de chaque catégorie, arrondie à la dizaine supérieure."""
    return {
        c: float(math.ceil(m / 10) * 10)
        for c, m in sorted(moyennes_mensuelles(transactions, cat, fin).items(), key=lambda x: -x[1])
        if c != REVENUS and m > 0
    }


def rapport_mensuel(
    transactions: list[Transaction],
    mois: Mois,
    cat: Categoriseur,
    budget: dict[str, float] | None = None,
    previsions: list[Prevision] | None = None,
    fin: date | None = None,
) -> RapportBudget:
    """Dépenses du mois par catégorie, comparées à la moyenne et au budget.

    Si les relevés s'arrêtent avant la fin du mois, les échéances récurrentes encore attendues
    (`previsions`) sont ajoutées pour estimer la dépense en fin de mois. `fin` est la date de fin
    des relevés quand elle est connue (relevé PDF arrêté au 30 alors que la dernière opération
    date du 23) : elle évite de prendre un mois terminé pour un mois en cours.
    """
    fin_releves = max(fin or transactions[-1].date, transactions[-1].date)
    complet = mois in mois_complets(transactions[0].date, fin_releves)
    du_mois = [t for t in transactions if _mois_de(t.date) == mois]
    totaux = totaux_par_mois(du_mois, cat).get(mois, {})
    moyennes = moyennes_mensuelles(transactions, cat, fin_releves)
    budget = budget or {}

    a_venir: dict[str, float] = defaultdict(float)
    if not complet:
        for p in previsions or []:
            if fin_releves < p.date <= _fin_du_mois(mois) and _mois_de(p.date) == mois:
                categorie = cat.categoriser(Transaction(p.date, p.libelle_origine or p.libelle, p.montant))
                a_venir[categorie] += p.montant if categorie == REVENUS else -p.montant

    categories = (set(totaux) | set(budget) | set(a_venir)) - {REVENUS}
    lignes = [
        LigneBudget(
            categorie=c,
            depense=round(totaux.get(c, 0.0), 2),
            prevu=round(totaux.get(c, 0.0) + a_venir.get(c, 0.0), 2),
            moyenne=moyennes.get(c, 0.0),
            budget=budget.get(c),
        )
        for c in categories
    ]
    lignes.sort(key=lambda l: -max(l.prevu, l.budget or 0))

    return RapportBudget(
        mois=mois,
        complet=complet,
        lignes=lignes,
        revenus=round(totaux.get(REVENUS, 0.0), 2),
        revenus_prevus=round(totaux.get(REVENUS, 0.0) + a_venir.get(REVENUS, 0.0), 2),
        non_classees=[t for t in du_mois if t.montant < 0 and cat.categoriser(t) == "Autres"],
    )


def charger_config(chemin: Path) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Lit le fichier budget.json : {"budget": {catégorie: montant}, "categories": {catégorie: [mots-clés]}}."""
    if not chemin.exists():
        return {}, {}
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    budget = {c: float(v) for c, v in donnees.get("budget", {}).items()}
    regles = {c: [str(m) for m in mots] for c, mots in donnees.get("categories", {}).items()}
    return budget, regles


def ecrire_config(chemin: Path, budget: dict[str, float], regles: dict[str, list[str]]) -> None:
    contenu = {
        "budget": budget,
        "categories": regles,
    }
    chemin.write_text(json.dumps(contenu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
