"""Interface web : tableau de bord servi par le NAS, protégé par mot de passe.

    MOT_DE_PASSE_WEB=... python -m assistant_financier --web      (dans le dossier de données)

La page est calculée à partir des fichiers du dossier (releves/, budget.json, alertes.conf) et
recalculée dès qu'un fichier change. Elle ne charge aucune ressource externe.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .solde import LIBELLE_COURANT, LIBELLE_DEPART
from .synthese import HORIZON_JOURS, Synthese, calculer

PORT_PAR_DEFAUT = 8765
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
MOIS_LONGS = ["janvier", "février", "mars", "avril", "mai", "juin",
              "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


# ---------------------------------------------------------------- mise en forme

def eur(montant: float, signe: bool = False, decimales: bool = True) -> str:
    texte = f"{montant:{'+' if signe else ''},.{2 if decimales else 0}f}".replace(",", " ").replace(".", ",")
    return texte.replace("-", "−") + " €"


def eur_court(montant: float) -> str:
    """Graduations d'axe : 0, 500, 1,5 k, 12 k…"""
    if abs(montant) >= 1000:
        valeur = f"{montant / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{valeur} k".replace("-", "−")
    return f"{montant:.0f}".replace("-", "−")


def jour(d: date, annee: bool = True) -> str:
    return f"{JOURS[d.weekday()]} {d:%d/%m}" + (f"/{d:%Y}" if annee else "")


def e(texte: object) -> str:
    return escape(str(texte), quote=True)


def _graduations(mini: float, maxi: float, cible: int = 4) -> list[float]:
    import math

    if maxi <= mini:
        maxi = mini + 1
    brut = (maxi - mini) / cible
    puissance = 10 ** math.floor(math.log10(brut))
    pas = next(m * puissance for m in (1, 2, 2.5, 5, 10) if m * puissance >= brut)
    debut = math.floor(mini / pas) * pas
    valeurs, v = [], debut
    while v <= maxi + pas * 0.001:
        valeurs.append(round(v, 2))
        v += pas
    if valeurs[-1] < maxi:
        valeurs.append(round(v, 2))
    return valeurs


# ---------------------------------------------------------------- graphiques SVG

LARGEUR = 720


def graphique_solde(s: Synthese) -> str:
    """Solde prévu : une courbe, la ligne zéro, le découvert autorisé, le point bas.

    Chaque mouvement de la journée est tracé (dépenses courantes, puis débits, puis crédits) : le
    creux du matin avant l'arrivée du salaire est visible, et le point bas est le même que celui
    de l'alerte de découvert et de la tuile « Point le plus bas ».
    """
    if len(s.points) < 2:
        return ""
    origine = s.points[0].date
    dates = [origine + timedelta(days=i) for i in range((s.points[-1].date - origine).days + 1)]
    trace = [((p.date - origine).days, p.solde) for p in s.points]  # (jour, solde) dans l'ordre
    fin_de_jour: dict[date, float] = {}
    plus_bas_jour: dict[date, float] = {}
    evenements: dict[date, list[tuple[str, float]]] = {}
    for p in s.points:
        fin_de_jour[p.date] = p.solde
        plus_bas_jour[p.date] = min(plus_bas_jour.get(p.date, p.solde), p.solde)
        if p.libelle not in (LIBELLE_COURANT, LIBELLE_DEPART):
            evenements.setdefault(p.date, []).append((p.libelle, p.montant))
    valeurs = [v for _, v in trace]

    hauteur, g, dr, h, b = 260, 56, 84, 18, 30
    mini = min(min(valeurs), 0.0)
    if mini < 0 and s.decouvert_autorise:
        mini = min(mini, -s.decouvert_autorise)
    ticks = _graduations(mini, max(max(valeurs), 0.0))
    y_min, y_max = ticks[0], ticks[-1]
    largeur_trace = LARGEUR - g - dr

    def x(i: int) -> float:
        return g + largeur_trace * i / (len(dates) - 1)

    def y(v: float) -> float:
        return h + (hauteur - h - b) * (y_max - v) / (y_max - y_min)

    morceaux = [f'<svg viewBox="0 0 {LARGEUR} {hauteur}" class="graph" role="img" '
                f'aria-label="Solde prévu du {dates[0]:%d/%m} au {dates[-1]:%d/%m}">']
    for t in ticks:
        classe = "axe" if t == 0 else "grille"
        morceaux.append(f'<line class="{classe}" x1="{g}" x2="{LARGEUR - dr}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        morceaux.append(f'<text class="tick" x="{g - 8}" y="{y(t) + 4:.1f}" text-anchor="end">{e(eur_court(t))}</text>')
    if s.decouvert_autorise and y_min <= -s.decouvert_autorise:
        yd = y(-s.decouvert_autorise)
        morceaux.append(f'<line class="limite" x1="{g}" x2="{LARGEUR - dr}" y1="{yd:.1f}" y2="{yd:.1f}"/>')
        morceaux.append(f'<text class="tick" x="{LARGEUR - dr + 6}" y="{yd + 4:.1f}">découvert autorisé</text>')
    for i, d in enumerate(dates):
        if d.weekday() == 0 or i == 0:
            morceaux.append(f'<text class="tick" x="{x(i):.1f}" y="{hauteur - 8}" text-anchor="middle">{d:%d/%m}</text>')

    ligne = " ".join(f"{'M' if n == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for n, (i, v) in enumerate(trace))
    zero = y(max(min(0.0, y_max), y_min))
    morceaux.append(f'<path class="aire" d="{ligne} L{x(trace[-1][0]):.1f},{zero:.1f} L{x(0):.1f},{zero:.1f} Z"/>')
    morceaux.append(f'<path class="courbe" d="{ligne}"/>')

    # Point bas : le premier minimum, comme pour l'alerte de découvert.
    n_bas = min(range(len(trace)), key=lambda n: trace[n][1])
    i_bas, v_bas = trace[n_bas]
    xb, yb = x(i_bas), y(v_bas)
    morceaux.append(f'<circle class="point" cx="{xb:.1f}" cy="{yb:.1f}" r="4"/>')
    ancre = "start" if xb < g + 90 else "end" if xb > LARGEUR - dr - 90 else "middle"
    # Tout le reste de la courbe est au-dessus du minimum : l'étiquette va dessous, sauf près de l'axe.
    y_texte = yb + 20 if yb + 24 < hauteur - b else yb - 12
    morceaux.append(f'<text class="etiquette" x="{xb:.1f}" y="{y_texte:.1f}" text-anchor="{ancre}">'
                    f'point bas {e(eur(v_bas, decimales=False))} le {dates[i_bas]:%d/%m}</text>')
    morceaux.append(f'<text class="etiquette" x="{x(trace[-1][0]) + 8:.1f}" y="{y(trace[-1][1]) + 4:.1f}">'
                    f'{e(eur(trace[-1][1], decimales=False))}</text>')

    # Couche de survol : réticule vertical + infobulle, aussi au clavier (flèches).
    morceaux.append(f'<line class="reticule" x1="0" x2="0" y1="{h}" y2="{hauteur - b}" visibility="hidden"/>')
    morceaux.append(f'<rect class="survol" x="{g}" y="{h}" width="{largeur_trace}" height="{hauteur - h - b}" '
                    f'tabindex="0" aria-label="Parcourir le solde jour par jour avec les flèches"/>')
    morceaux.append("</svg>")
    donnees = {
        "g": g, "l": largeur_trace, "w": LARGEUR, "x": [round(x(i), 1) for i in range(len(dates))],
        "jours": [{"d": jour(d), "s": eur(fin_de_jour[d]), "neg": fin_de_jour[d] < 0,
                   "b": eur(plus_bas_jour[d]) if plus_bas_jour[d] < fin_de_jour[d] - 0.005 else "",
                   "bneg": plus_bas_jour[d] < 0,
                   "e": [[lib, eur(m, signe=True)] for lib, m in evenements.get(d, [])]}
                  for d in dates],
    }
    return ('<div class="graph-cadre" data-graph="solde">' + "".join(morceaux)
            + '<div class="infobulle" hidden></div>'
            + f'<script type="application/json">{_json(donnees)}</script></div>')


def graphique_mois(s: Synthese) -> str:
    """Revenus et dépenses par mois : colonnes groupées, deux séries."""
    mois = s.historique
    if not mois:
        return ""
    hauteur, g, dr, h, b = 230, 56, 16, 16, 34
    ticks = _graduations(0, max(max(m.revenus, m.depenses) for m in mois))
    y_max = ticks[-1]
    largeur_trace = LARGEUR - g - dr
    bande = largeur_trace / len(mois)
    barre = min(24.0, bande * 0.32)

    def y(v: float) -> float:
        return h + (hauteur - h - b) * (y_max - v) / y_max

    def colonne(x0: float, v: float, classe: str) -> str:
        y0, yv = y(0), y(v)
        r = min(4.0, (y0 - yv) / 2, barre / 2)
        if y0 - yv < 0.5:
            return ""
        return (f'<path class="{classe}" d="M{x0:.1f},{y0:.1f} V{yv + r:.1f} Q{x0:.1f},{yv:.1f} {x0 + r:.1f},{yv:.1f} '
                f'H{x0 + barre - r:.1f} Q{x0 + barre:.1f},{yv:.1f} {x0 + barre:.1f},{yv + r:.1f} V{y0:.1f} Z"/>')

    morceaux = [f'<svg viewBox="0 0 {LARGEUR} {hauteur}" class="graph" role="img" '
                f'aria-label="Revenus et dépenses des {len(mois)} derniers mois">']
    for t in ticks:
        morceaux.append(f'<line class="{"axe" if t == 0 else "grille"}" x1="{g}" x2="{LARGEUR - dr}" '
                        f'y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        morceaux.append(f'<text class="tick" x="{g - 8}" y="{y(t) + 4:.1f}" text-anchor="end">{e(eur_court(t))}</text>')
    infos = []
    for i, m in enumerate(mois):
        centre = g + bande * (i + 0.5)
        morceaux.append(colonne(centre - barre - 1, m.revenus, "serie1"))
        morceaux.append(colonne(centre + 1, m.depenses, "serie2"))
        nom = MOIS_COURTS[m.mois - 1] + ("*" if not m.complet else "")
        morceaux.append(f'<text class="tick" x="{centre:.1f}" y="{hauteur - 14}" text-anchor="middle">{e(nom)}</text>')
        if i == 0 or m.mois == 1:
            morceaux.append(f'<text class="tick" x="{centre:.1f}" y="{hauteur - 1}" text-anchor="middle">{m.annee}</text>')
        morceaux.append(f'<rect class="survol-barre" data-i="{i}" x="{g + bande * i:.1f}" y="{h}" width="{bande:.1f}" '
                        f'height="{hauteur - h - b}" tabindex="0" aria-label="{e(MOIS_LONGS[m.mois - 1])} {m.annee}"/>')
        infos.append({"t": f"{MOIS_LONGS[m.mois - 1]} {m.annee}" + ("" if m.complet else " (mois partiel)"),
                      "r": eur(m.revenus), "d": eur(m.depenses), "s": eur(m.revenus - m.depenses, signe=True)})
    morceaux.append("</svg>")
    return ('<div class="graph-cadre" data-graph="mois">' + "".join(morceaux)
            + '<div class="infobulle" hidden></div>'
            + f'<script type="application/json">{_json(infos)}</script></div>')


def _json(donnees: object) -> str:
    # Pas de « </script> » possible dans les libellés : on échappe < et >.
    return json.dumps(donnees, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")


# ---------------------------------------------------------------- page

def _alertes(s: Synthese) -> str:
    cartes = []
    d = s.decouvert
    if d:
        texte = (f"Découvert prévu le {jour(d.date)} : le solde passerait à {eur(d.solde)} "
                 f"(point le plus bas {eur(d.plus_bas)} le {d.date_plus_bas:%d/%m}). "
                 f"Versez au moins {eur(d.a_approvisionner)} avant le {d.date:%d/%m}.")
        if d.depassement_autorise:
            texte += f" Découvert autorisé dépassé dès le {d.depassement_autorise:%d/%m} : risque de rejet de prélèvement."
        if s.releves_mal_lus:
            texte = ("Calcul fondé sur des relevés mal lus : ne vous y fiez pas tant que leur lecture n'est pas "
                     "corrigée. " + texte)
        cartes.append(("critique", "⚠", "Découvert prévu" + (" (non fiable)" if s.releves_mal_lus else ""), texte))
    for c in s.releves_mal_lus:
        cartes.append(("critique", "⚠", "Relevé mal lu",
                       f"{c.fichier} : les opérations lues ne correspondent pas aux soldes (écart de {eur(c.ecart or 0)}). "
                       "Ses chiffres ne sont pas fiables : voir diagnostic.txt dans le dossier."))
    for h in s.hausses:
        if s.hausse_recente(h):
            cartes.append(("attention", "↑", "Hausse d'abonnement",
                           f"{h.libelle} : {eur(h.ancien)} → {eur(h.nouveau)} (+{h.pourcentage * 100:.1f} %)".replace(".", ",")
                           + f" depuis le {h.depuis:%d/%m/%Y}, soit {eur(h.surcout_annuel)} de plus par an."))
    for erreur in s.illisibles:
        cartes.append(("info", "i", "Fichier ignoré", erreur))
    if s.ignores:
        cartes.append(("info", "i", "Fichiers ignorés", "Format non pris en charge : " + ", ".join(s.ignores)))
    if not cartes:
        cartes.append(("bon", "✓", "Rien à signaler", "Ni découvert prévu, ni hausse d'abonnement récente, et tous les relevés sont bien lus."))
    return "".join(
        f'<div class="alerte {classe}"><span class="icone" aria-hidden="true">{icone}</span>'
        f'<div><strong>{e(titre)}</strong><p>{e(texte)}</p></div></div>'
        for classe, icone, titre, texte in cartes
    )


def _tuiles(s: Synthese) -> str:
    tuiles = []
    if s.solde is not None:
        source = "alertes.conf" if s.source_solde == "alertes.conf" else "dernier relevé"
        tuiles.append(("hero", "Solde du compte", eur(s.solde), f"au {s.date_solde:%d/%m/%Y} ({source})"))
        if s.points:
            bas = s.point_bas
            tuiles.append(("", f"Solde prévu dans {HORIZON_JOURS} jours", eur(s.points[-1].solde), f"le {s.points[-1].date:%d/%m/%Y}"))
            tuiles.append(("neg" if bas.solde < 0 else "", "Point le plus bas", eur(bas.solde), f"le {jour(bas.date)}"))
    else:
        tuiles.append(("hero", "Solde du compte", "inconnu", "relevés sans solde : indiquez SOLDE dans alertes.conf"))
    tuiles.append(("", "Charges récurrentes", eur(-s.charges_mensuelles), "par mois, prélèvements détectés"))
    return "".join(
        f'<div class="tuile {classe}"><span class="libelle">{e(lib)}</span>'
        f'<span class="valeur">{e(val)}</span><span class="detail">{e(det)}</span></div>'
        for classe, lib, val, det in tuiles
    )


def _echeances(s: Synthese) -> str:
    if not s.previsions:
        return '<p class="vide">Aucune échéance prévue sur la période.</p>'
    lignes = []
    for p, solde, categorie in s.previsions:
        alerte = solde is not None and solde < 0
        lignes.append(
            f'<tr><td>{e(jour(p.date, annee=False))}</td><td>{e(p.libelle)}'
            + (' <span class="badge" title="montant variable, estimé">≈ estimé</span>' if p.montant_variable else "")
            + f'</td><td class="secondaire optionnel">{e(categorie)}</td>'
            f'<td class="nombre {"pos" if p.montant > 0 else ""}">{e(eur(p.montant, signe=True))}</td>'
            f'<td class="nombre">{e(eur(solde)) if solde is not None else "—"}'
            + (' <span class="statut critique">⚠ découvert</span>' if alerte else "")
            + "</td></tr>"
        )
    return ('<table><thead><tr><th>Date</th><th>Opération</th><th class="optionnel">Catégorie</th>'
            '<th class="nombre">Montant</th><th class="nombre">Solde après</th></tr></thead>'
            f'<tbody>{"".join(lignes)}</tbody></table>')


def _budget(s: Synthese) -> str:
    r = s.budget
    if not r:
        return ""
    a, m = r.mois
    titre = f"{MOIS_LONGS[m - 1]} {a}" + ("" if r.complet else " (en cours, fin de mois estimée)")
    lignes = []
    for l in r.lignes:
        if l.budget:
            taux = l.prevu / l.budget
            depasse = taux > 1
            etat = ('<span class="statut critique">⚠ dépassé</span>' if depasse and l.depense > l.budget
                    else '<span class="statut critique">⚠ dépassement prévu</span>' if depasse else "")
            jauge = (f'<div class="jauge" role="meter" aria-valuenow="{l.prevu:.0f}" aria-valuemin="0" '
                     f'aria-valuemax="{l.budget:.0f}"><div class="remplissage {"trop" if depasse else ""}" '
                     f'style="width:{min(taux, 1) * 100:.1f}%"></div></div>')
            montants = f"{eur(l.prevu, decimales=False)} / {eur(l.budget, decimales=False)}"
            reste = f'{eur(l.budget - l.prevu, signe=True, decimales=False)} {etat}'
        else:
            jauge, montants, reste = '<div class="jauge vide"></div>', eur(l.prevu, decimales=False), "pas de budget"
        lignes.append(f'<tr><td>{e(l.categorie)}</td><td class="col-jauge">{jauge}</td>'
                      f'<td class="nombre">{montants}</td><td class="nombre secondaire">{reste}</td>'
                      f'<td class="nombre secondaire optionnel">{e(eur(l.moyenne, decimales=False))}</td></tr>')
    note = ('<p class="note">Budget suggéré d\'après vos moyennes : ajustez-le dans budget.json.</p>'
            if s.budget_suggere else "")
    revenus = r.revenus if r.complet else r.revenus_prevus
    depenses = r.depenses if r.complet else r.depenses_prevues
    return (f'<h3 class="sous-titre">{e(titre)}</h3>{note}'
            '<table class="budget"><thead><tr><th>Catégorie</th><th>Consommé</th><th class="nombre">Dépensé / budget</th>'
            '<th class="nombre">Reste</th><th class="nombre optionnel">Moyenne</th></tr></thead>'
            f'<tbody>{"".join(lignes)}</tbody></table>'
            f'<p class="resume">Revenus {e(eur(revenus))} · dépenses {e(eur(depenses))} (hors épargne) · '
            f'reste {e(eur(revenus - depenses, signe=True))}</p>')


def _recurrences(s: Synthese) -> str:
    lignes = []
    for r, categorie in s.recurrences:
        quand = f"vers le {r.jour_du_mois}" if r.jour_du_mois else f"tous les {round(r.intervalle_jours)} j"
        lignes.append(
            f'<tr><td>{e(r.cle)}</td><td class="secondaire optionnel">{e(categorie)}</td><td>{e(r.frequence)}, {e(quand)}</td>'
            f'<td class="nombre {"pos" if r.montant_moyen > 0 else ""}">{e(eur(r.montant_dernier, signe=True))}'
            + (' <span class="badge">≈ variable</span>' if r.montant_variable else "")
            + f'</td><td class="nombre secondaire optionnel">{len(r.occurrences)}</td></tr>'
        )
    html = ('<table><thead><tr><th>Opération</th><th class="optionnel">Catégorie</th><th>Rythme</th>'
            '<th class="nombre">Montant</th>'
            f'<th class="nombre optionnel">Fois</th></tr></thead><tbody>{"".join(lignes)}</tbody></table>') if lignes else \
        '<p class="vide">Aucune opération récurrente détectée : il faut en général 3 mois de relevés.</p>'
    if s.arretees:
        html += ('<p class="note">Semblent arrêtées : ' + ", ".join(
            f"{e(r.cle)} (dernière fois le {r.derniere_date:%d/%m/%Y})" for r in s.arretees) + "</p>")
    return html


def _controles(s: Synthese) -> str:
    if not s.controles:
        return '<p class="note">Relevés CSV : pas de soldes à contrôler.</p>'
    lignes = []
    for c in s.controles:
        statut = {"ok": '<span class="statut bon">✓ soldes vérifiés</span>',
                  "ecart": f'<span class="statut critique">⚠ écart de {e(eur(c.ecart or 0))}</span>',
                  "non verifiable": '<span class="statut neutre">– pas d\'ancien solde</span>'}[c.statut]
        lignes.append(f'<tr><td>{e(c.fichier)}</td><td class="nombre">{c.nb_operations}</td><td>{statut}</td></tr>')
    return ('<table><thead><tr><th>Relevé</th><th class="nombre">Opérations</th><th>Contrôle</th></tr></thead>'
            f'<tbody>{"".join(lignes)}</tbody></table>')


def rendre_page(s: Synthese, version: str = "", genere: datetime | None = None) -> str:
    genere = genere or datetime.now()
    if s.erreur:
        corps = (f'<section class="carte"><div class="alerte critique"><span class="icone">⚠</span><div>'
                 f'<strong>Aucune donnée à afficher</strong><p class="pre">{e(s.erreur)}</p></div></div></section>')
        sous_titre = "Déposez vos relevés dans le dossier releves/ de Synology Drive."
    else:
        sous_titre = (f"Relevés du {s.debut:%d/%m/%Y} au {s.fin:%d/%m/%Y} · {s.nb_operations} opérations"
                      + (f" · solde lu dans {s.source_solde}" if s.source_solde else ""))
        solde = graphique_solde(s)
        corps = (
            f'<section class="alertes" aria-label="Alertes">{_alertes(s)}</section>'
            f'<section class="tuiles">{_tuiles(s)}</section>'
            + (f'<section class="carte"><h2>Solde prévu sur {HORIZON_JOURS} jours</h2>'
               f'<p class="note">Prélèvements et rentrées récurrents, plus vos dépenses courantes estimées '
               f'à {e(eur(s.depenses_courantes_jour))} par jour.</p>{solde}</section>' if solde else "")
            + f'<section class="carte"><h2>Prochaines échéances</h2>{_echeances(s)}</section>'
            f'<section class="carte"><h2>Budget du mois</h2>{_budget(s)}</section>'
            f'<section class="carte"><h2>Revenus et dépenses par mois</h2>'
            '<div class="legende"><span><i class="cle serie1"></i>Revenus</span><span><i class="cle serie2"></i>'
            'Dépenses (hors épargne)</span></div>'
            f'{graphique_mois(s)}<p class="note">* mois incomplet dans vos relevés.</p></section>'
            f'<section class="carte"><h2>Opérations récurrentes</h2>{_recurrences(s)}</section>'
            f'<section class="carte"><h2>Contrôle des relevés</h2>{_controles(s)}</section>'
        )
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Assistant financier</title>
<style>{CSS}</style></head>
<body><main>
<header><h1>Assistant financier</h1><p class="secondaire">{e(sous_titre)}</p></header>
{corps}
<footer>Mis à jour le {genere:%d/%m/%Y à %H:%M}{f" · version {e(version)}" if version else ""} ·
calculé sur votre NAS, aucune donnée ne quitte le dossier.</footer>
</main><script>{JS}</script></body></html>"""


CSS = """
:root{color-scheme:light;--plan:#f9f9f7;--surface:#fcfcfb;--encre:#0b0b0b;--encre2:#52514e;--muet:#898781;
--grille:#e1e0d9;--axe:#c3c2b7;--bord:rgba(11,11,11,.10);--serie1:#2a78d6;--serie2:#eb6834;--piste:#cde2fb;
--bon:#0ca30c;--bon-texte:#006300;--attention:#fab219;--critique:#d03b3b;--fond-critique:#fbeaea;--fond-attention:#fdf3dc;
--fond-bon:#e7f5e7;--fond-info:#f0efec}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--plan:#0d0d0d;--surface:#1a1a19;
--encre:#f0efec;--encre2:#c3c2b7;--grille:#2c2c2a;--axe:#383835;--bord:rgba(255,255,255,.10);--serie1:#3987e5;
--serie2:#d95926;--piste:#184f95;--bon-texte:#0ca30c;--fond-critique:#3a1d1d;--fond-attention:#3a3018;--fond-bon:#16301a;
--fond-info:#262624}}
*{box-sizing:border-box}
body{margin:0;background:var(--plan);color:var(--encre);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 10px}.sous-titre{font-size:15px;margin:0 0 6px}
header{margin-bottom:18px}.secondaire{color:var(--encre2)}.note{color:var(--encre2);font-size:13px;margin:6px 0 10px}
.carte{background:var(--surface);border:1px solid var(--bord);border-radius:12px;padding:18px 18px 14px;margin:0 0 16px;
overflow-x:auto}
.alertes{display:grid;gap:10px;margin-bottom:16px}
.alerte{display:flex;gap:12px;align-items:flex-start;border-radius:10px;padding:12px 14px;border:1px solid var(--bord)}
.alerte p{margin:2px 0 0}.alerte .icone{font-weight:700;width:22px;height:22px;border-radius:50%;display:inline-flex;
align-items:center;justify-content:center;flex:none;color:#fff;font-size:13px}
.alerte.critique{background:var(--fond-critique)}.alerte.critique .icone{background:var(--critique)}
.alerte.attention{background:var(--fond-attention)}.alerte.attention .icone{background:var(--attention);color:#0b0b0b}
.alerte.bon{background:var(--fond-bon)}.alerte.bon .icone{background:var(--bon)}
.alerte.info{background:var(--fond-info)}.alerte.info .icone{background:var(--muet)}
.pre{white-space:pre-wrap}
.tuiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:16px}
.tuile{background:var(--surface);border:1px solid var(--bord);border-radius:12px;padding:14px 16px;display:flex;
flex-direction:column;gap:2px}
.tuile .libelle{color:var(--encre2);font-size:13px}.tuile .valeur{font-size:26px;font-weight:600}
.tuile.hero .valeur{font-size:48px;line-height:1.1}.tuile .detail{color:var(--muet);font-size:13px}
.tuile.neg .valeur{color:var(--critique)}
table{width:100%;border-collapse:collapse;font-size:14px}th{text-align:left;color:var(--encre2);font-weight:500;
border-bottom:1px solid var(--axe);padding:6px 8px}td{border-bottom:1px solid var(--grille);padding:7px 8px;vertical-align:middle}
.nombre{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.pos{color:var(--bon-texte)}
.badge{font-size:12px;color:var(--encre2);border:1px solid var(--bord);border-radius:6px;padding:0 5px;white-space:nowrap}
.statut{font-size:13px;white-space:nowrap}.statut.critique{color:var(--critique);font-weight:600}
.statut.bon{color:var(--bon-texte)}.statut.neutre{color:var(--muet)}
.col-jauge{width:32%;min-width:120px}.jauge{height:10px;border-radius:5px;background:var(--piste);overflow:hidden}
.jauge.vide{background:var(--grille)}.remplissage{height:100%;background:var(--serie1);border-radius:0 4px 4px 0}
.remplissage.trop{background:var(--critique)}
.resume{margin:10px 0 0;color:var(--encre2)}.vide{color:var(--encre2)}
.legende{display:flex;gap:18px;font-size:13px;color:var(--encre2);margin-bottom:4px}
.cle{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.cle.serie1{background:var(--serie1)}.cle.serie2{background:var(--serie2)}
.graph-cadre{position:relative}.graph{width:100%;height:auto;display:block}
.graph .grille{stroke:var(--grille);stroke-width:1}.graph .axe{stroke:var(--axe);stroke-width:1}
.graph .limite{stroke:var(--critique);stroke-width:1;opacity:.6}
.graph .tick{fill:var(--muet);font-size:11px;font-variant-numeric:tabular-nums}
.graph .etiquette{fill:var(--encre);font-size:12px;font-weight:600}
.graph .courbe{fill:none;stroke:var(--serie1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.graph .aire{fill:var(--serie1);opacity:.10}
.graph .point{fill:var(--serie1);stroke:var(--surface);stroke-width:2}
.graph .serie1{fill:var(--serie1)}.graph .serie2{fill:var(--serie2)}
.graph .reticule{stroke:var(--encre2);stroke-width:1}
.graph .survol,.graph .survol-barre{fill:transparent;cursor:crosshair;outline:none}
.graph .survol-barre:hover,.graph .survol-barre:focus{fill:var(--encre);fill-opacity:.04}
.infobulle{position:absolute;pointer-events:none;background:var(--surface);color:var(--encre);border:1px solid var(--bord);
border-radius:8px;padding:8px 10px;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.12);min-width:150px;max-width:280px;z-index:2}
.infobulle .v{font-weight:650;font-size:15px}.infobulle .neg{color:var(--critique)}.infobulle .l{color:var(--encre2)}
.infobulle .ligne{display:flex;justify-content:space-between;gap:12px}
.infobulle .k{display:inline-block;width:10px;height:2px;margin-right:6px;vertical-align:middle}
footer{color:var(--muet);font-size:12px;margin-top:24px}
@media (max-width:640px){.tuiles{grid-template-columns:1fr 1fr;gap:10px}.tuile.hero{grid-column:1/-1}
.tuile{padding:12px}.tuile .valeur{font-size:20px}.tuile.hero .valeur{font-size:38px}td,th{padding:6px 5px}.optionnel{display:none}
.col-jauge{width:22%;min-width:60px}.carte{padding:14px 12px 10px}
.graph .tick{font-size:20px}.graph .etiquette{font-size:21px}.graph .courbe{stroke-width:3.5}
.graph .point{stroke-width:3}.graph circle.point{r:7}}
"""

JS = """
(function(){
  function ligne(parent, valeur, libelle, classeValeur, cle){
    var d=document.createElement('div');d.className='ligne';
    var a=document.createElement('span');a.className='l';
    if(cle){var k=document.createElement('i');k.className='k';k.style.background=cle;a.appendChild(k);}
    a.appendChild(document.createTextNode(libelle));
    var b=document.createElement('span');b.className='v'+(classeValeur?' '+classeValeur:'');b.textContent=valeur;
    d.appendChild(a);d.appendChild(b);parent.appendChild(d);
  }
  function placer(cadre, bulle, xSvg, largeurSvg){
    var r=cadre.getBoundingClientRect();var x=xSvg*r.width/largeurSvg;
    bulle.hidden=false;var w=bulle.offsetWidth;
    bulle.style.left=Math.max(0,Math.min(r.width-w,x+12))+'px';bulle.style.top='8px';
  }
  var style=getComputedStyle(document.documentElement);
  document.querySelectorAll('[data-graph="solde"]').forEach(function(cadre){
    var data=JSON.parse(cadre.querySelector('script').textContent);
    var svg=cadre.querySelector('svg'),bulle=cadre.querySelector('.infobulle');
    var ret=svg.querySelector('.reticule'),zone=svg.querySelector('.survol');var courant=-1;
    function montrer(i){
      i=Math.max(0,Math.min(data.x.length-1,i));courant=i;var j=data.jours[i];
      ret.setAttribute('x1',data.x[i]);ret.setAttribute('x2',data.x[i]);ret.setAttribute('visibility','visible');
      bulle.textContent='';var t=document.createElement('div');t.className='l';t.textContent=j.d;bulle.appendChild(t);
      ligne(bulle,j.s,'Solde en fin de journée',j.neg?'neg':'',style.getPropertyValue('--serie1'));
      if(j.b){ligne(bulle,j.b,'Au plus bas dans la journée',j.bneg?'neg':'',null);}
      j.e.forEach(function(ev){ligne(bulle,ev[1],ev[0],'',null);});
      placer(cadre,bulle,data.x[i],data.w);
    }
    function cacher(){bulle.hidden=true;ret.setAttribute('visibility','hidden');}
    zone.addEventListener('pointermove',function(ev){
      var r=svg.getBoundingClientRect();var x=(ev.clientX-r.left)*data.w/r.width;
      montrer(Math.round((x-data.g)/data.l*(data.x.length-1)));
    });
    zone.addEventListener('pointerleave',cacher);zone.addEventListener('blur',cacher);
    zone.addEventListener('focus',function(){montrer(courant<0?0:courant);});
    zone.addEventListener('keydown',function(ev){
      if(ev.key==='ArrowRight'){montrer(courant+1);ev.preventDefault();}
      if(ev.key==='ArrowLeft'){montrer(courant-1);ev.preventDefault();}
    });
  });
  document.querySelectorAll('[data-graph="mois"]').forEach(function(cadre){
    var data=JSON.parse(cadre.querySelector('script').textContent);
    var bulle=cadre.querySelector('.infobulle');var w=parseFloat(cadre.querySelector('svg').viewBox.baseVal.width);
    cadre.querySelectorAll('.survol-barre').forEach(function(zone){
      function montrer(){
        var m=data[+zone.dataset.i];bulle.textContent='';
        var t=document.createElement('div');t.className='l';t.textContent=m.t;bulle.appendChild(t);
        ligne(bulle,m.r,'Revenus','',style.getPropertyValue('--serie1'));
        ligne(bulle,m.d,'Dépenses','',style.getPropertyValue('--serie2'));
        ligne(bulle,m.s,'Différence','',null);
        placer(cadre,bulle,parseFloat(zone.getAttribute('x'))+parseFloat(zone.getAttribute('width'))/2,w);
      }
      zone.addEventListener('pointerenter',montrer);zone.addEventListener('focus',montrer);
      zone.addEventListener('pointerleave',function(){bulle.hidden=true;});
      zone.addEventListener('blur',function(){bulle.hidden=true;});
    });
  });
})();
"""


# ---------------------------------------------------------------- serveur

class _Cache:
    """Recalcule la page seulement quand un fichier du dossier change."""

    def __init__(self, dossier: Path, version: str):
        self.dossier, self.version = dossier, version
        self._signature: tuple | None = None
        self._page = ""
        self._verrou = threading.Lock()

    def _signature_actuelle(self) -> tuple:
        elements = []
        for racine, dossiers, fichiers in os.walk(self.dossier):
            dossiers[:] = sorted(d for d in dossiers if not d.startswith(("@", "#", ".")))
            for nom in sorted(fichiers):
                if nom.startswith("."):
                    continue
                try:
                    st = os.stat(os.path.join(racine, nom))
                except OSError:
                    continue
                elements.append((racine, nom, st.st_mtime_ns, st.st_size))
        return (date.today(), tuple(elements))

    def page(self) -> str:
        with self._verrou:
            signature = self._signature_actuelle()
            if signature != self._signature:
                try:
                    self._page = rendre_page(calculer(self.dossier), self.version)
                except Exception:  # une erreur de calcul ne doit pas arrêter le serveur
                    traceback.print_exc()
                    s = Synthese(erreur="Erreur pendant l'analyse des relevés : voir le journal du conteneur.")
                    self._page = rendre_page(s, self.version)
                self._signature = signature
            return self._page


def creer_serveur(dossier: Path, mot_de_passe: str, port: int = PORT_PAR_DEFAUT, hote: str = "0.0.0.0",
                  version: str = "") -> ThreadingHTTPServer:
    cache = _Cache(dossier, version)
    attendu = mot_de_passe.encode()

    class Gestionnaire(BaseHTTPRequestHandler):
        server_version = "AssistantFinancier"
        sys_version = ""

        def _autorise(self) -> bool:
            entete = self.headers.get("Authorization", "")
            if not entete.startswith("Basic "):
                return False
            try:
                _, _, fourni = base64.b64decode(entete[6:]).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                return False
            return hmac.compare_digest(fourni.encode(), attendu)

        def _envoyer(self, code: int, corps: str, type_contenu: str = "text/html; charset=utf-8", **entetes) -> None:
            donnees = corps.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", type_contenu)
            self.send_header("Content-Length", str(len(donnees)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            for cle, valeur in entetes.items():
                self.send_header(cle.replace("_", "-"), valeur)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(donnees)

        def do_GET(self) -> None:  # noqa: N802 (nom imposé par http.server)
            chemin = self.path.split("?", 1)[0]
            if chemin == "/sante":
                return self._envoyer(200, "ok", "text/plain; charset=utf-8")
            if not self._autorise():
                time.sleep(1)  # ralentit les essais de mot de passe
                return self._envoyer(401, "<p>Mot de passe requis.</p>",
                                     WWW_Authenticate='Basic realm="Assistant financier", charset="UTF-8"')
            if chemin not in ("/", "/index.html"):
                return self._envoyer(404, "<p>Page introuvable.</p>")
            self._envoyer(200, cache.page())

        do_HEAD = do_GET

        def log_message(self, format: str, *args) -> None:  # journal discret, sans en-têtes
            sys.stderr.write(f"{self.address_string()} {format % args}\n")

    return ThreadingHTTPServer((hote, port), Gestionnaire)


def servir(dossier: Path, port: int | None = None) -> int:
    mot_de_passe = os.environ.get("MOT_DE_PASSE_WEB", "")
    if not mot_de_passe:
        print("MOT_DE_PASSE_WEB n'est pas défini : l'interface web refuse de démarrer sans mot de passe.",
              file=sys.stderr)
        return 2
    port = port or int(os.environ.get("PORT_WEB", PORT_PAR_DEFAUT))
    serveur = creer_serveur(dossier, mot_de_passe, port, version=os.environ.get("VERSION_ASSISTANT", ""))
    print(f"Interface web sur le port {port} (dossier {dossier}).", flush=True)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _apercu(dossier: Path, sortie: Path) -> None:  # pragma: no cover - outil de développement
    sortie.write_text(rendre_page(calculer(dossier), "aperçu"), encoding="utf-8")
