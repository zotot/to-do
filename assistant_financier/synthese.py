"""Toutes les données de l'interface web, calculées en une fois à partir du dossier de données."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .alertes import detecter_hausses
from .analyse import detecter_recurrences, montant_prevu, prevoir
from .budget import RapportBudget, charger_config, mois_complets, rapport_mensuel, suggerer_budget, totaux_par_mois
from .categories import EPARGNE, REVENUS, Categoriseur
from .import_releves import charger_releves, verifier_releves
from .modeles import Hausse, InfoReleve, Prevision, Recurrence, Transaction
from .solde import Decouvert, PointSolde, depenses_courantes_par_jour, detecter_decouvert, projeter_solde

HORIZON_JOURS = 31
JOURS_HAUSSE_RECENTE = 62
MOIS_HISTORIQUE = 12


@dataclass
class Controle:
    fichier: str
    statut: str  # "ok", "ecart", "non verifiable"
    ecart: float | None
    nb_operations: int


@dataclass
class Mois:
    annee: int
    mois: int
    revenus: float
    depenses: float
    complet: bool


@dataclass
class Synthese:
    erreur: str | None = None  # empêche tout le reste (aucun relevé…)
    nb_operations: int = 0
    debut: date | None = None
    fin: date | None = None
    controles: list[Controle] = field(default_factory=list)
    ignores: list[str] = field(default_factory=list)
    illisibles: list[str] = field(default_factory=list)
    solde: float | None = None
    date_solde: date | None = None
    source_solde: str = ""
    decouvert_autorise: float = 0.0
    points: list[PointSolde] = field(default_factory=list)
    decouvert: Decouvert | None = None
    depenses_courantes_jour: float = 0.0
    previsions: list[tuple[Prevision, float | None, str]] = field(default_factory=list)  # (échéance, solde après, catégorie)
    recurrences: list[tuple[Recurrence, str]] = field(default_factory=list)  # (récurrence, catégorie)
    arretees: list[Recurrence] = field(default_factory=list)
    charges_mensuelles: float = 0.0
    hausses: list[Hausse] = field(default_factory=list)
    budget: RapportBudget | None = None
    budget_suggere: bool = False
    historique: list[Mois] = field(default_factory=list)

    @property
    def releves_mal_lus(self) -> list[Controle]:
        return [c for c in self.controles if c.statut == "ecart"]

    def hausse_recente(self, h: Hausse) -> bool:
        return bool(self.fin) and (self.fin - h.depuis).days <= JOURS_HAUSSE_RECENTE

    @property
    def point_bas(self) -> PointSolde | None:
        return min(self.points, key=lambda p: p.solde) if self.points else None


def lire_reglages(chemin: Path) -> dict[str, str]:
    """Lignes « CLE=valeur » de alertes.conf (les lignes commentées par # sont ignorées)."""
    if not chemin.exists():
        return {}
    reglages = {}
    for ligne in chemin.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*?)\s*$", ligne)
        if m:
            reglages[m.group(1)] = m.group(2)
    return reglages


def _nombre(texte: str | None) -> float | None:
    if not texte:
        return None
    try:
        return float(texte.replace("€", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def calculer(dossier: Path) -> Synthese:
    """Analyse complète du dossier de données (releves/, budget.json, alertes.conf)."""
    from .cli import _fichiers  # même découverte des fichiers que la ligne de commande

    s = Synthese()
    reglages = lire_reglages(dossier / "alertes.conf")
    s.decouvert_autorise = _nombre(reglages.get("DECOUVERT_AUTORISE")) or 0.0

    erreurs: list[str] = []
    infos: list[InfoReleve] = []
    try:
        chemins = _fichiers([str(dossier / "releves")], s.ignores)
    except SystemExit as e:
        s.erreur = str(e).replace(str(dossier) + "/", "")
        return s
    transactions = charger_releves(chemins, erreurs, infos)
    s.illisibles = erreurs
    if not transactions:
        s.erreur = "Aucune opération n'a pu être lue dans les relevés."
        return s
    s.nb_operations = len(transactions)
    s.debut, s.fin = transactions[0].date, transactions[-1].date

    verifier_releves(infos)
    for i in sorted(infos, key=lambda i: (i.fin or date.min, i.fichier)):
        statut = "ok" if i.ecart == 0 else "ecart" if i.ecart is not None else "non verifiable"
        s.controles.append(Controle(i.fichier, statut, i.ecart, i.nb_operations))

    # Solde : celui de alertes.conf s'il est indiqué, sinon le solde final du relevé le plus récent.
    depart = s.fin
    solde_conf = _nombre(reglages.get("SOLDE"))
    avec_solde = [i for i in infos if i.solde_final is not None and i.fin and i.fin >= s.fin]
    if solde_conf is not None:
        s.solde, s.date_solde, s.source_solde = solde_conf, s.fin, "alertes.conf"
    elif avec_solde:
        dernier = max(avec_solde, key=lambda i: i.fin)
        s.solde, s.date_solde, s.source_solde = dernier.solde_final, dernier.fin, dernier.fichier
        depart = dernier.fin

    budget, regles = charger_config(dossier / "budget.json")
    cat = Categoriseur(regles)

    toutes = detecter_recurrences(transactions, s.fin)
    actives = [r for r in toutes if r.active]
    s.arretees = [r for r in toutes if not r.active]
    s.recurrences = [(r, cat.categoriser(r.occurrences[-1])) for r in actives]
    s.charges_mensuelles = round(sum(montant_prevu(r) * 30.44 / r.intervalle_jours for r in actives if r.montant_moyen < 0), 2)
    s.hausses = detecter_hausses(actives)

    du = depart + timedelta(days=1)
    au = du + timedelta(days=HORIZON_JOURS - 1)
    previsions = prevoir(actives, du, au)
    soldes_apres: dict[Prevision, float] = {}
    if s.solde is not None:
        s.depenses_courantes_jour = depenses_courantes_par_jour(transactions, toutes)
        s.points = projeter_solde(s.solde, du, au, previsions, s.depenses_courantes_jour)
        s.decouvert = detecter_decouvert(s.points, s.decouvert_autorise)
        soldes_apres = {p.prevision: p.solde for p in s.points if p.prevision}
    s.previsions = [
        (p, soldes_apres.get(p), cat.categoriser(Transaction(p.date, p.libelle_origine or p.libelle, p.montant)))
        for p in previsions
    ]

    s.budget_suggere = not budget
    if s.budget_suggere:
        budget = suggerer_budget(transactions, cat, depart)
    mois = (s.fin.year, s.fin.month)
    s.budget = rapport_mensuel(transactions, mois, cat, budget, prevoir(actives, du, s.fin + timedelta(days=31)), depart)

    complets = set(mois_complets(s.debut, max(depart, s.fin)))
    totaux = totaux_par_mois(transactions, cat)
    for (a, m) in sorted(totaux)[-MOIS_HISTORIQUE:]:
        t = totaux[(a, m)]
        depenses = sum(v for c, v in t.items() if c not in (REVENUS, EPARGNE))
        s.historique.append(Mois(a, m, round(t.get(REVENUS, 0.0), 2), round(depenses, 2), (a, m) in complets))
    return s
