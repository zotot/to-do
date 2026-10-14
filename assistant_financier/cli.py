"""Interface en ligne de commande.

Exemples :
    python -m assistant_financier releves/*.csv
    python -m assistant_financier releves/ --jours 60 --solde 1520.40
    python -m assistant_financier releves/ --export previsions.csv
    python -m assistant_financier releves/ --budget
    python -m assistant_financier releves/ --budget --mois 05/2026
    python -m assistant_financier releves/ --creer-budget
    python -m assistant_financier releves/ --alertes
    python -m assistant_financier releves/ --alertes --solde 1520.40 --decouvert-autorise 300
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from .alertes import detecter_hausses
from .analyse import detecter_recurrences, montant_prevu, prevoir
from .budget import RapportBudget, charger_config, ecrire_config, rapport_mensuel, suggerer_budget
from .categories import Categoriseur
from .modeles import InfoReleve
from .import_releves import charger_releves, parser_date, verifier_releves
from .solde import LIBELLE_COURANT, LIBELLE_DEPART, Decouvert, depenses_courantes_par_jour, detecter_decouvert, projeter_solde

EXTENSIONS = {".csv", ".txt", ".tsv", ".ofx", ".qfx", ".pdf"}
JOURS_SEMAINE = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
NOMS_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def euros(montant: float, signe: bool = True) -> str:
    texte = f"{montant:{'+' if signe else ''},.2f}".replace(",", " ").replace(".", ",")
    return f"{texte} €"


def _barre(taux: float, largeur: int = 12) -> str:
    plein = min(largeur, round(taux * largeur))
    return "█" * plein + "░" * (largeur - plein)


JOURS_HAUSSE_RECENTE = 62
# Code de sortie de --alertes quand il y a une alerte à traiter (découvert prévu ou hausse récente) :
# permet à un planificateur (cron, DSM…) de n'envoyer un e-mail que dans ce cas.
CODE_ALERTE = 3


def afficher_hausses(recurrences, fin_releves: date) -> None:
    hausses = detecter_hausses(recurrences)
    if not hausses:
        return
    print("=== ⚠ Alertes : hausses d'abonnements ===")
    for h in hausses:
        recente = " NOUVEAU" if (fin_releves - h.depuis).days <= JOURS_HAUSSE_RECENTE else ""
        print(
            f"  {h.libelle[:32]:<32} {euros(h.ancien, False):>11} → {euros(h.nouveau, False):<11}"
            f" (+{h.pourcentage * 100:.1f} %)".replace(".", ",") + f" depuis le {h.depuis:%d/%m/%Y}, soit {euros(h.surcout_annuel)} par an{recente}"
        )
    total = sum(h.surcout_annuel for h in hausses)
    if len(hausses) > 1:
        print(f"  Surcoût total : {euros(total)} par an")
    print()


def afficher_decouvert(d: Decouvert, solde: float, fin_releves: date, courant: float, autorise: float) -> None:
    print("=== ⚠ Alerte : découvert prévu ===")
    jour = f"{JOURS_SEMAINE[d.date.weekday()]} {d.date:%d/%m/%Y}"
    if d.cause == LIBELLE_DEPART:
        print(f"  Le compte est déjà à découvert : {euros(d.solde)}.")
    elif d.cause == LIBELLE_COURANT:
        print(f"  Le {jour}, vos dépenses courantes feraient passer le compte à {euros(d.solde)}.")
    else:
        print(f"  Le {jour}, {d.cause} ({euros(d.montant_cause)}) ferait passer le compte à {euros(d.solde)}.")
    print(f"  Point le plus bas : {euros(d.plus_bas)} le {d.date_plus_bas:%d/%m/%Y}.")
    if d.depassement_autorise:
        print(
            f"  ⚠ Au-delà de votre découvert autorisé ({euros(autorise, False)}) dès le {d.depassement_autorise:%d/%m/%Y} :"
            " risque de rejet de prélèvement et de frais."
        )
    elif autorise:
        print(f"  Reste dans votre découvert autorisé ({euros(autorise, False)}), mais des agios s'appliquent.")
    avant = "dès que possible" if d.cause == LIBELLE_DEPART else f"avant le {d.date:%d/%m/%Y}"
    print(f"  → Versez au moins {euros(d.a_approvisionner, False)} sur le compte {avant}.")
    _note_projection(solde, fin_releves, courant)
    print()


def _note_projection(solde: float, fin_releves: date, courant: float) -> None:
    courant_txt = f" et dépenses courantes estimées à {euros(courant, False)}/jour" if courant else ""
    print(f"  (projection : solde de {euros(solde)} au {fin_releves:%d/%m/%Y}, prélèvements récurrents prévus{courant_txt})")


def afficher_budget(r: RapportBudget, fin_releves: date, budget_suggere: bool, chemin_config: Path) -> None:
    annee, mois = r.mois
    titre = f"Budget de {NOMS_MOIS[mois - 1]} {annee}"
    if not r.complet:
        titre += f" (relevés jusqu'au {fin_releves:%d/%m}, fin de mois estimée avec les prélèvements prévus)"
    print(f"=== {titre} ===")
    if budget_suggere:
        print(f"  Pas de fichier {chemin_config} : budget suggéré d'après vos moyennes (--creer-budget pour l'enregistrer).")

    col_prevu = "" if r.complet else f"{'Fin de mois':>13}"
    print(f"\n  {'Catégorie':<24}{'Dépensé':>13}{col_prevu}{'Moyenne':>13}{'Budget':>13}{'Reste':>13}")
    for l in r.lignes:
        ligne = f"  {l.categorie[:24]:<24}{euros(l.depense, False):>13}"
        if not r.complet:
            ligne += f"{euros(l.prevu, False):>13}"
        ligne += f"{euros(l.moyenne, False):>13}"
        if l.budget is None:
            ligne += f"{'—':>13}{'':>13}"
        else:
            ligne += f"{euros(l.budget, False):>13}{euros(l.reste):>13}  {_barre(l.taux or 0)} {l.taux:4.0%}"
            if l.taux > 1:
                ligne += "  ⚠ dépassé" if l.depense > l.budget else "  ⚠ dépassement prévu"
        print(ligne)

    total_budget = sum(l.budget or 0 for l in r.lignes if l.categorie != "Épargne")
    print(f"\n  Total dépenses (hors épargne) : {euros(r.depenses, False)}", end="")
    if not r.complet:
        print(f", {euros(r.depenses_prevues, False)} prévus en fin de mois", end="")
    print(f" / budget {euros(total_budget, False)}" if total_budget else "")
    revenus = r.revenus if r.complet else r.revenus_prevus
    print(f"  Revenus du mois             : {euros(revenus, False)}")
    reste = revenus - (r.depenses if r.complet else r.depenses_prevues)
    print(f"  Reste à vivre / épargne     : {euros(reste)}")

    if r.non_classees:
        print(f"\n  Opérations non classées (ajoutez des mots-clés dans {chemin_config}, rubrique \"categories\") :")
        for t in sorted(r.non_classees, key=lambda t: t.montant)[:10]:
            print(f"    {t.date:%d/%m}  {t.libelle[:50]:<50} {euros(t.montant):>12}")


# Dossiers techniques (Synology, corbeilles, instantanés) et fichiers temporaires à ne pas lire.
DOSSIERS_IGNORES = {"@eaDir", "#recycle", "#snapshot", "#SynoRecycle", "@tmp"}


FORMATS = "PDF, CSV, OFX"


def _est_ignore(nom: str) -> bool:
    return nom in DOSSIERS_IGNORES or nom.startswith((".", "~"))


def _parcourir(racine: Path, illisibles: list[str], caches: list[str]) -> list[Path]:
    """Fichiers d'un dossier et de ses sous-dossiers, sans les dossiers techniques ni les fichiers cachés.

    Les dossiers qu'on ne peut pas ouvrir (droits d'accès) sont ajoutés à `illisibles` au lieu
    d'être sautés en silence.
    """
    trouves = []

    def erreur(e: OSError) -> None:
        illisibles.append(str(Path(e.filename).relative_to(racine)) if e.filename != str(racine) else ".")

    for dossier, sous_dossiers, noms in os.walk(racine, onerror=erreur):
        sous_dossiers[:] = sorted(d for d in sous_dossiers if not _est_ignore(d))
        for nom in sorted(noms):
            chemin = Path(dossier) / nom
            if _est_ignore(nom):
                caches.append(str(chemin.relative_to(racine)))
            else:
                trouves.append(chemin)
    return sorted(trouves)


def _fichiers(entrees: list[str], ignores: list[str] | None = None) -> list[Path]:
    """Relevés à lire : fichiers donnés, ou contenu des dossiers (sous-dossiers compris).

    Les fichiers d'un format non pris en charge (Excel, image…) sont ajoutés à `ignores`.
    Si aucun relevé n'est trouvé, le message dit ce que contient le dossier, pour comprendre pourquoi.
    """
    fichiers: list[Path] = []
    non_pris_en_charge: list[str] = []
    illisibles: list[str] = []
    caches: list[str] = []
    for e in entrees:
        p = Path(e)
        if p.is_dir():
            for f in _parcourir(p, illisibles, caches):
                if f.suffix.lower() in EXTENSIONS:
                    fichiers.append(f)
                else:
                    non_pris_en_charge.append(str(f.relative_to(p)))
        elif p.exists():
            fichiers.append(p)
        elif entrees == ["releves"]:
            raise SystemExit(
                f"Aucun dossier releves/ ici : placez-y vos relevés ({FORMATS}), ou indiquez leur chemin "
                "en argument."
            )
        else:
            raise SystemExit(f"Fichier introuvable : {e}")
    if ignores is not None:
        ignores.extend(non_pris_en_charge)
    if illisibles:
        print(f"⚠ Dossier(s) impossible(s) à ouvrir (droits d'accès) : {', '.join(illisibles)}")
    if not fichiers:
        lieu = ", ".join(f"{e}/" if Path(e).is_dir() else e for e in entrees)
        details = [f"Aucun relevé trouvé dans {lieu} (formats acceptés : {FORMATS})."]
        if non_pris_en_charge:
            details.append(f"  Fichiers présents mais d'un autre format : {', '.join(non_pris_en_charge[:10])}"
                           + (" …" if len(non_pris_en_charge) > 10 else ""))
        if caches:
            details.append(f"  Fichiers cachés ou temporaires ignorés : {', '.join(caches[:10])}")
        if illisibles:
            details.append("  Certains dossiers n'ont pas pu être ouverts : vérifiez leurs droits d'accès.")
        if not (non_pris_en_charge or caches or illisibles):
            details.append("  Le dossier est vide : déposez vos relevés directement dedans (ou dans un sous-dossier).")
        raise SystemExit("\n".join(details))
    return fichiers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assistant_financier",
        description="Analyse vos relevés de compte et anticipe les prochains prélèvements.",
    )
    parser.add_argument(
        "releves", nargs="*", default=["releves"],
        help="fichiers CSV/OFX ou dossier les contenant (défaut : dossier releves/)",
    )
    parser.add_argument("--jours", type=int, default=31, help="horizon de prévision en jours (défaut : 31)")
    parser.add_argument("--du", help="début de la prévision (JJ/MM/AAAA). Défaut : lendemain du dernier relevé")
    parser.add_argument("--solde", type=float, help="solde actuel, pour projeter le solde après chaque échéance")
    parser.add_argument("--debits", action="store_true", help="n'afficher que les prélèvements (pas les rentrées)")
    parser.add_argument("--export", help="exporter les prévisions dans un fichier CSV")
    parser.add_argument("--budget", action="store_true", help="afficher les dépenses du mois par catégorie et le budget")
    parser.add_argument("--mois", help="mois du budget (MM/AAAA). Défaut : mois du dernier relevé")
    parser.add_argument("--config", default="budget.json", help="fichier de budget et de catégories (défaut : budget.json)")
    parser.add_argument("--creer-budget", action="store_true", help="créer le fichier de budget à partir de vos moyennes")
    parser.add_argument("--alertes", action="store_true", help="n'afficher que les alertes (hausses, découvert)")
    parser.add_argument(
        "--decouvert-autorise", type=float, default=0.0, metavar="MONTANT",
        help="montant de votre découvert autorisé, pour signaler un risque de rejet de prélèvement",
    )
    parser.epilog = (
        f"Avec --alertes, le code de sortie vaut {CODE_ALERTE} s'il y a un découvert prévu, une hausse "
        f"d'abonnement de moins de {JOURS_HAUSSE_RECENTE} jours ou un relevé PDF mal lu, 0 sinon."
    )
    parser.add_argument(
        "--web", action="store_true",
        help="lancer l'interface web (dossier courant : releves/, budget.json ; mot de passe : MOT_DE_PASSE_WEB)",
    )
    parser.add_argument("--port", type=int, help="port de l'interface web (défaut : 8765)")
    parser.add_argument(
        "--diagnostic", action="store_true",
        help="décrire, anonymisée, la structure des relevés PDF mal lus (à transmettre pour ajuster la lecture)",
    )
    parser.add_argument(
        "--sans-depenses-courantes", action="store_true",
        help="projeter le solde avec les seuls prélèvements récurrents (sans courses, essence…)",
    )
    args = parser.parse_args(argv)
    if args.web:
        from .web import servir

        dossier = Path(args.releves[0]).parent if args.releves != ["releves"] else Path.cwd()
        return servir(dossier, args.port)

    ignores: list[str] = []
    erreurs: list[str] = []
    infos: list[InfoReleve] = []
    chemins = _fichiers(args.releves, ignores)
    transactions = charger_releves(chemins, erreurs, infos)
    if args.diagnostic:
        return _diagnostic(args, chemins, erreurs, infos)
    if ignores:
        print(f"Fichiers ignorés (formats acceptés : {FORMATS}) : {', '.join(ignores)}")
    for erreur in erreurs:
        print(f"Fichier illisible, ignoré : {erreur}")
    if ignores or erreurs:
        print()
    if not transactions:
        print("Aucune opération lue dans les relevés.")
        return 1
    debut_releves, fin_releves = transactions[0].date, transactions[-1].date
    print(f"{len(transactions)} opérations lues du {debut_releves:%d/%m/%Y} au {fin_releves:%d/%m/%Y}.")
    # Relevé mal lu ou PDF illisible : les chiffres ne sont plus fiables, c'est une alerte.
    args.releves_a_verifier = _controler_releves(infos) + sum(
        1 for e in erreurs if e.split(" : ", 1)[0].lower().endswith(".pdf")
    )
    args.depart = fin_releves  # date à laquelle le solde est connu
    if args.solde is None:
        _solde_automatique(args, infos, fin_releves)
    print()

    if args.budget or args.creer_budget:
        return _budget(args, transactions)

    detectees = detecter_recurrences(transactions, fin_releves)
    toutes = [r for r in detectees if r.montant_moyen < 0] if args.debits else detectees
    recurrences = [r for r in toutes if r.active]
    arretees = [r for r in toutes if not r.active]

    du = parser_date(args.du) if args.du else args.depart + timedelta(days=1)
    au = du + timedelta(days=args.jours - 1)
    # Le solde se projette avec toutes les opérations, rentrées comprises, même avec --debits.
    projection = _projection(args, transactions, detectees, du, au)

    afficher_hausses(recurrences, fin_releves)
    if args.alertes:
        if not detecter_hausses(recurrences):
            print("Aucune hausse d'abonnement détectée.")
        if args.solde is None:
            print("Indiquez votre solde (--solde) pour vérifier le risque de découvert.")
        elif projection and not projection[1]:
            bas = min(projection[0], key=lambda p: p.solde)
            print(f"Pas de découvert prévu d'ici le {au:%d/%m/%Y} (point le plus bas : {euros(bas.solde)} le {bas.date:%d/%m/%Y}).")
        hausses_recentes = [
            h for h in detecter_hausses(recurrences) if (fin_releves - h.depuis).days <= JOURS_HAUSSE_RECENTE
        ]
        decouvert = projection[1] if projection else None
        return CODE_ALERTE if hausses_recentes or decouvert or args.releves_a_verifier else 0

    print("=== Opérations récurrentes détectées ===")
    if not recurrences:
        print("Aucune. Il faut en général au moins 2 à 3 mois de relevés.\n")
    for r in recurrences:
        jour = f"vers le {r.jour_du_mois}" if r.jour_du_mois else f"tous les {round(r.intervalle_jours)} j"
        variable = " (variable)" if r.montant_variable else ""
        print(
            f"  {r.cle[:32]:<32} {euros(montant_prevu(r)):>14}{variable:<11} "
            f"{r.frequence:<13} {jour:<14} {len(r.occurrences)} fois, confiance {r.confiance:.0%}"
        )

    mensuel = sum(
        montant_prevu(r) * 30.44 / r.intervalle_jours for r in recurrences if r.montant_moyen < 0
    )
    print(f"\n  Charges récurrentes estimées : {euros(mensuel)} / mois\n")

    if arretees:
        print("=== Récurrences qui semblent arrêtées (résiliées ou terminées) ===")
        for r in arretees:
            print(f"  {r.cle[:32]:<32} {euros(r.montant_dernier):>14}   {r.frequence:<13} dernière fois le {r.derniere_date:%d/%m/%Y}")
        print()

    previsions = prevoir(recurrences, du, au)
    soldes = {p.prevision: p.solde for p in projection[0] if p.prevision} if projection else {}

    print(f"=== Échéances prévues du {du:%d/%m/%Y} au {au:%d/%m/%Y} ===")
    if not previsions:
        print("  Aucune échéance prévue sur cette période.")
    for p in previsions:
        ligne = f"  {JOURS_SEMAINE[p.date.weekday()]} {p.date:%d/%m/%Y}  {p.libelle[:32]:<32} {euros(p.montant):>14}"
        if p.montant_variable:
            ligne += " ~"
        if p in soldes:
            solde = soldes[p]
            ligne += f"   solde prévu : {euros(solde)}" + ("  ⚠ découvert" if solde < 0 else "")
        print(ligne)
    total_debits = sum(p.montant for p in previsions if p.montant < 0)
    total_credits = sum(p.montant for p in previsions if p.montant > 0)
    print(f"\n  Total prélèvements attendus : {euros(total_debits)}")
    if total_credits:
        print(f"  Total rentrées attendues    : {euros(total_credits)}")
    if projection:
        points = projection[0]
        bas = min(points, key=lambda p: p.solde)
        print(f"  Solde prévu au {au:%d/%m/%Y}     : {euros(points[-1].solde)} (point le plus bas : {euros(bas.solde)} le {bas.date:%d/%m/%Y})")
        _note_projection(args.solde, du - timedelta(days=1), projection[2])
    print("  (~ = montant variable, estimé sur les 3 dernières échéances)")
    if fin_releves < date.today() - timedelta(days=45):
        print(f"\n  Note : le dernier relevé date du {fin_releves:%d/%m/%Y}, pensez à ajouter les plus récents.")

    if args.export:
        with open(args.export, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["date", "libelle", "montant", "frequence", "montant_variable", "confiance"])
            for p in previsions:
                w.writerow([
                    f"{p.date:%d/%m/%Y}", p.libelle, f"{p.montant:.2f}".replace(".", ","),
                    p.frequence, "oui" if p.montant_variable else "non", f"{p.confiance:.2f}",
                ])
        print(f"\nPrévisions exportées dans {args.export}")
    return 0


def _controler_releves(infos: list[InfoReleve]) -> int:
    """Affiche le contrôle des soldes des relevés PDF/OFX : un relevé mal lu ne passe pas inaperçu.

    Renvoie le nombre de relevés dont les soldes ne concordent pas avec les opérations lues.
    """
    if not infos:
        return 0
    verifier_releves(infos)
    verifies = [i for i in infos if i.ecart == 0]
    faux = [i for i in infos if i.ecart not in (None, 0)]
    for i in sorted(faux, key=lambda i: i.fichier):
        print(
            f"⚠ {i.fichier} : les opérations lues ne correspondent pas aux soldes du relevé "
            f"(écart de {euros(i.ecart, False)}). Le relevé a peut-être été mal lu : vérifiez ses opérations "
            "(description anonymisée du relevé : option --diagnostic)."
        )
    non_verifiables = len(infos) - len(verifies) - len(faux)
    resume = f"Contrôle des soldes : {len(verifies)}/{len(infos)} relevé(s) vérifié(s)"
    if not faux and verifies:
        resume += " ✓"
    if non_verifiables:
        resume += f", {non_verifiables} sans ancien solde à comparer"
    print(resume + ".")
    return len(faux)


def _diagnostic(args: argparse.Namespace, chemins: list[Path], erreurs: list[str], infos: list[InfoReleve]) -> int:
    """Diagnostic anonymisé des relevés PDF illisibles, mal lus, ou donnés explicitement."""
    from .pdf import diagnostic_pdf

    verifier_releves(infos)
    par_nom = {i.fichier: i for i in infos}
    explicites = {Path(e).name for e in args.releves if Path(e).is_file()}
    a_examiner = []
    for chemin in chemins:
        if chemin.suffix.lower() != ".pdf":
            continue
        info = par_nom.get(chemin.name)
        illisible = any(e.startswith(f"{chemin.name} :") for e in erreurs)
        mal_lu = info is not None and (info.ecart not in (None, 0) or info.nb_operations == 0)
        if illisible or mal_lu or chemin.name in explicites:
            a_examiner.append(chemin)
    if not a_examiner:
        print("Tous les relevés PDF sont lus correctement : aucun diagnostic nécessaire.")
        return 0
    for chemin in a_examiner:
        print(diagnostic_pdf(chemin))
        print()
    print("Vérifiez que rien de personnel n'apparaît avant de transmettre ce diagnostic.")
    return 0


def _solde_automatique(args: argparse.Namespace, infos: list[InfoReleve], fin_releves: date) -> None:
    """Prend le solde final du relevé le plus récent, s'il couvre bien les dernières opérations."""
    avec_solde = [i for i in infos if i.solde_final is not None and i.fin]
    if not avec_solde:
        return
    dernier = max(avec_solde, key=lambda i: i.fin)
    if dernier.fin < fin_releves:
        print(
            f"Solde du relevé {dernier.fichier} non utilisé : il date du {dernier.fin:%d/%m/%Y}, "
            f"avant les dernières opérations (indiquez le solde avec --solde)."
        )
        return
    args.solde = dernier.solde_final
    args.depart = dernier.fin
    print(f"Solde lu dans {dernier.fichier} : {euros(dernier.solde_final)} au {dernier.fin:%d/%m/%Y}.")


def _projection(args: argparse.Namespace, transactions, recurrences, du: date, au: date):
    """Projette le solde et affiche l'alerte de découvert. Renvoie (points, découvert, dépenses/jour) ou None."""
    if args.solde is None:
        return None
    actives = [r for r in recurrences if r.active]
    courant = 0.0 if args.sans_depenses_courantes else depenses_courantes_par_jour(transactions, recurrences)
    points = projeter_solde(args.solde, du, au, prevoir(actives, du, au), courant)
    decouvert = detecter_decouvert(points, args.decouvert_autorise)
    if decouvert:
        if getattr(args, "releves_a_verifier", 0):
            print("⚠ Attention : ce calcul repose sur des relevés mal lus (voir plus haut), ne vous y fiez pas.")
        afficher_decouvert(decouvert, args.solde, du - timedelta(days=1), courant, args.decouvert_autorise)
    return points, decouvert, courant


def _budget(args: argparse.Namespace, transactions) -> int:
    chemin = Path(args.config)
    try:
        budget, regles = charger_config(chemin)
    except (ValueError, AttributeError) as e:
        raise SystemExit(f"{chemin} n'est pas un fichier de budget valide : {e}")
    categoriseur = Categoriseur(regles)

    if args.creer_budget:
        if chemin.exists():
            raise SystemExit(f"{chemin} existe déjà : modifiez-le directement, ou supprimez-le pour le recréer.")
        suggestion = suggerer_budget(transactions, categoriseur, args.depart)
        ecrire_config(chemin, suggestion, regles)
        print(f"Budget créé dans {chemin} d'après vos dépenses moyennes :")
        for c, montant in suggestion.items():
            print(f"  {c:<24} {euros(montant, False):>12}")
        print("\nAjustez les montants selon vos objectifs, puis lancez avec --budget.")
        return 0

    fin_releves = transactions[-1].date
    if args.mois:
        try:
            m, a = args.mois.split("/")
            mois = (int(a), int(m))
            if not 1 <= mois[1] <= 12:
                raise ValueError
        except ValueError:
            raise SystemExit("--mois attend le format MM/AAAA, par exemple 05/2026")
    else:
        mois = (fin_releves.year, fin_releves.month)

    budget_suggere = not budget
    if budget_suggere:
        budget = suggerer_budget(transactions, categoriseur, args.depart)
    detectees = detecter_recurrences(transactions, fin_releves)
    recurrences = [r for r in detectees if r.active]
    du = args.depart + timedelta(days=1)
    _projection(args, transactions, detectees, du, du + timedelta(days=args.jours - 1))
    previsions = prevoir(recurrences, du, fin_releves + timedelta(days=31))
    rapport = rapport_mensuel(transactions, mois, categoriseur, budget, previsions, args.depart)
    afficher_budget(rapport, max(fin_releves, args.depart), budget_suggere, chemin)
    print()
    afficher_hausses(recurrences, fin_releves)
    return 0


if __name__ == "__main__":
    sys.exit(main())
