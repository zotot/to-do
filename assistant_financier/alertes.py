"""Alertes sur les prélèvements récurrents."""

from __future__ import annotations

from .modeles import Hausse, Recurrence


def detecter_hausses(recurrences: list[Recurrence]) -> list[Hausse]:
    """Hausses de tarif des prélèvements à montant fixe encore actifs, les plus récentes d'abord."""
    hausses = []
    for r in recurrences:
        if not r.active or r.montant_moyen >= 0:
            continue  # abonnement arrêté, ou rentrée d'argent (une hausse de salaire n'est pas une alerte)
        for (_, ancien), (depuis, nouveau) in zip(r.paliers, r.paliers[1:]):
            if nouveau > ancien:
                hausses.append(Hausse(
                    libelle=r.cle,
                    depuis=depuis,
                    ancien=ancien,
                    nouveau=nouveau,
                    frequence=r.frequence,
                    surcout_annuel=round((nouveau - ancien) * 365.25 / r.intervalle_jours, 2),
                ))
    hausses.sort(key=lambda h: h.depuis, reverse=True)
    return hausses
