"""Structures de données partagées."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Transaction:
    date: date
    libelle: str
    montant: float  # négatif = débit, positif = crédit
    source: str = ""

    @property
    def est_debit(self) -> bool:
        return self.montant < 0


@dataclass
class Recurrence:
    """Une opération qui revient régulièrement (abonnement, loyer, salaire...)."""

    cle: str  # libellé normalisé
    libelle: str  # libellé lisible (le plus récent)
    frequence: str  # "hebdomadaire", "mensuelle", ...
    intervalle_jours: float
    montant_moyen: float
    montant_dernier: float
    montant_variable: bool
    jour_du_mois: int | None
    occurrences: list[Transaction] = field(default_factory=list)
    confiance: float = 0.0
    active: bool = True  # False si l'opération n'apparaît plus dans les derniers relevés
    jamais_le_week_end: bool = False

    @property
    def derniere_date(self) -> date:
        return self.occurrences[-1].date


@dataclass(frozen=True)
class Prevision:
    date: date
    libelle: str
    montant: float
    frequence: str
    confiance: float
    montant_variable: bool
    libelle_origine: str = ""  # libellé bancaire complet de la dernière occurrence
