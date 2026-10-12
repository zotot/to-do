"""Projection du solde du compte et détection d'un découvert à venir."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from .budget import mois_complets
from .modeles import Prevision, Recurrence, Transaction

LIBELLE_COURANT = "Dépenses courantes (estimation)"
LIBELLE_DEPART = "Solde actuel"


@dataclass(frozen=True)
class PointSolde:
    date: date
    libelle: str
    montant: float
    solde: float
    prevision: Prevision | None = None  # None pour les dépenses courantes estimées


@dataclass(frozen=True)
class Decouvert:
    date: date  # premier jour où le solde passe sous zéro
    cause: str  # opération qui fait basculer le compte
    montant_cause: float
    solde: float  # solde juste après cette opération
    plus_bas: float
    date_plus_bas: date
    depassement_autorise: date | None  # premier jour au-delà du découvert autorisé

    @property
    def a_approvisionner(self) -> float:
        """Somme à verser avant `date` pour ne jamais passer sous zéro."""
        return -self.plus_bas


def depenses_courantes_par_jour(transactions: list[Transaction], recurrences: list[Recurrence]) -> float:
    """Dépenses hors prélèvements récurrents (courses, essence, sorties…), en euros par jour.

    Calculées sur les mois complets des relevés ; la médiane évite qu'un gros achat
    ponctuel (ordinateur, vacances) ne gonfle l'estimation.
    """
    if not transactions:
        return 0.0
    recurrentes = {id(t) for r in recurrences for t in r.occurrences}
    par_mois: dict[tuple[int, int], float] = defaultdict(float)
    for t in transactions:
        if t.montant < 0 and id(t) not in recurrentes:
            par_mois[(t.date.year, t.date.month)] -= t.montant
    complets = mois_complets(transactions[0].date, transactions[-1].date)
    if complets:
        return round(median(par_mois.get(m, 0.0) for m in complets) / 30.44, 2)
    jours = (transactions[-1].date - transactions[0].date).days + 1
    return round(sum(par_mois.values()) / jours, 2)


def projeter_solde(
    solde: float, du: date, au: date, previsions: list[Prevision], courant_par_jour: float = 0.0
) -> list[PointSolde]:
    """Simule le solde jour par jour : dépenses courantes, puis échéances du jour (débits d'abord).

    Le premier point est le solde de départ, la veille de `du`.
    """
    par_jour: dict[date, list[Prevision]] = defaultdict(list)
    for p in previsions:
        par_jour[p.date].append(p)
    points = [PointSolde(du - timedelta(days=1), LIBELLE_DEPART, 0.0, round(solde, 2))]
    jour = du
    while jour <= au:
        if courant_par_jour:
            solde -= courant_par_jour
            points.append(PointSolde(jour, LIBELLE_COURANT, -courant_par_jour, round(solde, 2)))
        for p in sorted(par_jour.get(jour, []), key=lambda p: p.montant):
            solde += p.montant
            points.append(PointSolde(jour, p.libelle, p.montant, round(solde, 2), p))
        jour += timedelta(days=1)
    return points


def detecter_decouvert(points: list[PointSolde], decouvert_autorise: float = 0.0) -> Decouvert | None:
    premier = next((p for p in points if p.solde < 0), None)
    if premier is None:
        return None
    plus_bas = min(points, key=lambda p: p.solde)
    depassement = next((p.date for p in points if p.solde < -decouvert_autorise), None) if decouvert_autorise else None
    return Decouvert(
        date=premier.date,
        cause=premier.libelle,
        montant_cause=premier.montant,
        solde=premier.solde,
        plus_bas=plus_bas.solde,
        date_plus_bas=plus_bas.date,
        depassement_autorise=depassement,
    )
