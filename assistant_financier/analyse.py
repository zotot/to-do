"""Détection des opérations récurrentes et prévision des prochaines échéances."""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median, pstdev

from .libelles import normaliser
from .modeles import Prevision, Recurrence, Transaction

# nom, période en jours, tolérance en jours
FREQUENCES = [
    ("hebdomadaire", 7, 2),
    ("bimensuelle", 14, 3),
    ("mensuelle", 30.44, 5),
    ("bimestrielle", 60.9, 8),
    ("trimestrielle", 91.3, 12),
    ("semestrielle", 182.6, 18),
    ("annuelle", 365.25, 25),
]
MOIS_PAR_FREQUENCE = {"mensuelle": 1, "bimestrielle": 2, "trimestrielle": 3, "semestrielle": 6, "annuelle": 12}

SEUIL_REGULARITE = 0.75  # part minimale d'intervalles conformes à la fréquence
SEUIL_MONTANT_VARIABLE = 0.05  # écart-type relatif au-delà duquel le montant est "variable"
TOLERANCE_TARIF = 0.01  # deux montants à moins de 1 % l'un de l'autre = même tarif (conversion de devise…)
MAX_CHANGEMENTS_TARIF = 2


def _classer(intervalles: list[int]) -> tuple[str, float, float] | None:
    """Renvoie (fréquence, période, régularité) si les intervalles sont réguliers."""
    if not intervalles:
        return None
    med = median(intervalles)
    for nom, periode, tolerance in FREQUENCES:
        if abs(med - periode) <= tolerance:
            # Un mois sauté (intervalle double) est toléré mais compte à moitié.
            score = 0.0
            for i in intervalles:
                if abs(i - periode) <= tolerance:
                    score += 1
                elif abs(i - 2 * periode) <= 2 * tolerance:
                    score += 0.5
            regularite = score / len(intervalles)
            if regularite >= SEUIL_REGULARITE:
                return nom, periode, regularite
            return None
    return None


def _ajouter_mois(d: date, mois: int, jour: int) -> date:
    annee = d.year + (d.month - 1 + mois) // 12
    m = (d.month - 1 + mois) % 12 + 1
    return date(annee, m, min(jour, calendar.monthrange(annee, m)[1]))


def _paliers(operations: list[Transaction], periode: float) -> list[tuple[date, float]] | None:
    """Découpe les montants en paliers de tarif consécutifs (13,49 € puis 15,99 €…).

    Renvoie None si le montant varie trop souvent pour être un tarif fixe (facture d'énergie…).
    """
    paliers: list[tuple[date, float]] = []
    tailles: list[int] = []
    for t in operations:
        montant = abs(t.montant)
        if paliers and abs(montant - paliers[-1][1]) <= paliers[-1][1] * TOLERANCE_TARIF + 0.005:
            tailles[-1] += 1
        else:
            paliers.append((t.date, montant))
            tailles.append(1)
    if len(paliers) == 1:
        return paliers
    # Il faut assez d'historique pour distinguer un changement de tarif d'un montant variable,
    # sauf pour les échéances espacées (trimestrielles, annuelles) où 2 occurrences suffisent.
    if len(operations) < (2 if periode >= 85 else 3):
        return None
    # Un tarif intermédiaire doit tenir au moins deux échéances ; le premier palier peut être
    # tronqué par le début des relevés, le dernier vient peut-être de commencer.
    if len(paliers) - 1 > MAX_CHANGEMENTS_TARIF or any(n < 2 for n in tailles[1:-1]):
        return None
    return paliers


def _evaluer(cle: str, operations: list[Transaction], min_occurrences: int = 2) -> Recurrence | None:
    operations = sorted(operations, key=lambda t: t.date)
    # Plusieurs opérations le même jour : on les traite comme une seule échéance.
    dates = sorted({t.date for t in operations})
    if len(dates) < min_occurrences:
        return None
    intervalles = [(b - a).days for a, b in zip(dates, dates[1:])]
    classement = _classer(intervalles)
    if classement is None:
        return None
    frequence, periode, regularite = classement

    montants = [abs(t.montant) for t in operations]
    moy = mean(montants)
    paliers = _paliers(operations, periode)
    # Un changement de tarif n'est pas un montant variable : on prévoit le nouveau tarif.
    variabilite = 0.0 if paliers else (pstdev(montants) / moy if moy else 0.0)

    jour_du_mois = None
    if frequence in MOIS_PAR_FREQUENCE:
        jour_du_mois = round(median(t.date.day for t in operations))

    # Confiance : nombre d'occurrences, régularité des dates, stabilité du montant.
    nb = len(dates)
    confiance = min(1.0, nb / 4) * 0.5 + regularite * 0.35 + (0.15 if variabilite <= SEUIL_MONTANT_VARIABLE else 0.05)

    return Recurrence(
        cle=cle,
        libelle=operations[-1].libelle,
        frequence=frequence,
        intervalle_jours=periode,
        montant_moyen=round(moy if operations[0].montant > 0 else -moy, 2),
        montant_dernier=operations[-1].montant,
        montant_variable=variabilite > SEUIL_MONTANT_VARIABLE,
        jour_du_mois=jour_du_mois,
        occurrences=operations,
        confiance=round(confiance, 2),
        jamais_le_week_end=all(d.weekday() < 5 for d in dates),
        paliers=paliers or [],
    )


def _par_montant(operations: list[Transaction], tolerance: float = 0.02) -> list[list[Transaction]]:
    """Sépare les opérations d'un même marchand par montant (ex. deux abonnements Apple)."""
    groupes: list[list[Transaction]] = []
    for t in sorted(operations, key=lambda t: abs(t.montant)):
        if groupes:
            ref = abs(groupes[-1][0].montant)
            if abs(abs(t.montant) - ref) <= ref * tolerance:
                groupes[-1].append(t)
                continue
        groupes.append([t])
    return groupes


def detecter_recurrences(transactions: list[Transaction], fin_releves: date | None = None) -> list[Recurrence]:
    """Regroupe les opérations par libellé normalisé et garde celles qui reviennent régulièrement.

    Les récurrences dont la dernière occurrence est trop ancienne par rapport à la fin des
    relevés (abonnement résilié, crédit terminé...) sont marquées `active=False`.
    """
    if not transactions:
        return []
    fin_releves = fin_releves or max(t.date for t in transactions)

    groupes: dict[tuple[str, bool], list[Transaction]] = defaultdict(list)
    for t in transactions:
        groupes[(normaliser(t.libelle), t.est_debit)].append(t)

    resultats = []
    for (cle, _), operations in groupes.items():
        trouvee = _evaluer(cle, operations)
        candidates = [trouvee] if trouvee else []
        if not trouvee or trouvee.montant_variable:
            # Même marchand, plusieurs abonnements à montant fixe (ex. deux forfaits mobiles).
            # Exigences plus strictes pour ne pas confondre avec des achats courants.
            par_montant = [
                r for sous_groupe in _par_montant(operations)
                if (r := _evaluer(cle, sous_groupe, min_occurrences=3)) and not r.montant_variable
            ]
            if len(par_montant) >= 2 or (par_montant and not trouvee):
                candidates = par_montant
        for r in candidates:
            retard = (fin_releves - r.derniere_date).days
            r.active = retard <= r.intervalle_jours * 1.5 + 7
            resultats.append(r)

    resultats.sort(key=lambda r: (r.montant_moyen >= 0, r.montant_moyen))
    return resultats


def prochaines_dates(r: Recurrence, jusqu_au: date) -> list[date]:
    dates = []
    pas_mois = MOIS_PAR_FREQUENCE.get(r.frequence)
    n = 1
    while True:
        if pas_mois:
            suivante = _ajouter_mois(r.derniere_date, pas_mois * n, r.jour_du_mois or r.derniere_date.day)
        else:
            suivante = r.derniere_date + timedelta(days=round(r.intervalle_jours * n))
        if r.jamais_le_week_end and suivante.weekday() >= 5:
            # Prélèvement jamais débité le week-end : reporté au jour ouvré suivant.
            suivante += timedelta(days=7 - suivante.weekday())
        if suivante > jusqu_au:
            return dates
        # Échéance en fin/début de mois (ex. le 31 puis le 2) : évite une date trop rapprochée.
        if (suivante - r.derniere_date).days < r.intervalle_jours / 2:
            n += 1
            continue
        dates.append(suivante)
        n += 1


def montant_prevu(r: Recurrence) -> float:
    if not r.montant_variable:
        return r.montant_dernier
    recents = [t.montant for t in r.occurrences[-3:]]
    return round(mean(recents), 2)


def prevoir(recurrences: list[Recurrence], du: date, au: date) -> list[Prevision]:
    """Liste les échéances attendues entre `du` et `au` (inclus), triées par date."""
    previsions = []
    for r in recurrences:
        if not r.active:
            continue
        for d in prochaines_dates(r, au):
            if d < du:
                continue
            previsions.append(
                Prevision(d, r.cle, montant_prevu(r), r.frequence, r.confiance, r.montant_variable, r.libelle)
            )
    previsions.sort(key=lambda p: (p.date, p.montant))
    return previsions
