"""Interface en ligne de commande.

Exemples :
    python -m assistant_financier releves/*.csv
    python -m assistant_financier releves/ --jours 60 --solde 1520.40
    python -m assistant_financier releves/ --export previsions.csv
    python -m assistant_financier releves/ --budget
    python -m assistant_financier releves/ --budget --mois 05/2026
    python -m assistant_financier releves/ --creer-budget
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

from .analyse import detecter_recurrences, montant_prevu, prevoir
from .budget import RapportBudget, charger_config, ecrire_config, rapport_mensuel, suggerer_budget
from .categories import Categoriseur
from .import_releves import charger_releves, parser_date

EXTENSIONS = {".csv", ".txt", ".tsv", ".ofx", ".qfx"}
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


def _fichiers(entrees: list[str]) -> list[Path]:
    fichiers = []
    for e in entrees:
        p = Path(e)
        if p.is_dir():
            fichiers += sorted(f for f in p.iterdir() if f.suffix.lower() in EXTENSIONS)
        elif p.exists():
            fichiers.append(p)
        else:
            raise SystemExit(f"Fichier introuvable : {e}")
    if not fichiers:
        raise SystemExit("Aucun relevé trouvé (formats acceptés : CSV, OFX).")
    return fichiers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assistant_financier",
        description="Analyse vos relevés de compte et anticipe les prochains prélèvements.",
    )
    parser.add_argument("releves", nargs="+", help="fichiers CSV/OFX ou dossier les contenant")
    parser.add_argument("--jours", type=int, default=31, help="horizon de prévision en jours (défaut : 31)")
    parser.add_argument("--du", help="début de la prévision (JJ/MM/AAAA). Défaut : lendemain du dernier relevé")
    parser.add_argument("--solde", type=float, help="solde actuel, pour projeter le solde après chaque échéance")
    parser.add_argument("--debits", action="store_true", help="n'afficher que les prélèvements (pas les rentrées)")
    parser.add_argument("--export", help="exporter les prévisions dans un fichier CSV")
    parser.add_argument("--budget", action="store_true", help="afficher les dépenses du mois par catégorie et le budget")
    parser.add_argument("--mois", help="mois du budget (MM/AAAA). Défaut : mois du dernier relevé")
    parser.add_argument("--config", default="budget.json", help="fichier de budget et de catégories (défaut : budget.json)")
    parser.add_argument("--creer-budget", action="store_true", help="créer le fichier de budget à partir de vos moyennes")
    args = parser.parse_args(argv)

    transactions = charger_releves(_fichiers(args.releves))
    if not transactions:
        print("Aucune opération lue dans les relevés.")
        return 1
    debut_releves, fin_releves = transactions[0].date, transactions[-1].date
    print(f"{len(transactions)} opérations lues du {debut_releves:%d/%m/%Y} au {fin_releves:%d/%m/%Y}.\n")

    if args.budget or args.creer_budget:
        return _budget(args, transactions)

    toutes = detecter_recurrences(transactions, fin_releves)
    if args.debits:
        toutes = [r for r in toutes if r.montant_moyen < 0]
    recurrences = [r for r in toutes if r.active]
    arretees = [r for r in toutes if not r.active]

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

    du = parser_date(args.du) if args.du else fin_releves + timedelta(days=1)
    au = du + timedelta(days=args.jours - 1)
    previsions = prevoir(recurrences, du, au)

    print(f"=== Échéances prévues du {du:%d/%m/%Y} au {au:%d/%m/%Y} ===")
    if not previsions:
        print("  Aucune échéance prévue sur cette période.")
    solde = args.solde
    for p in previsions:
        ligne = f"  {JOURS_SEMAINE[p.date.weekday()]} {p.date:%d/%m/%Y}  {p.libelle[:32]:<32} {euros(p.montant):>14}"
        if p.montant_variable:
            ligne += " ~"
        if solde is not None:
            solde += p.montant
            ligne += f"   solde prévu : {euros(solde)}" + ("  ⚠ découvert" if solde < 0 else "")
        print(ligne)
    total_debits = sum(p.montant for p in previsions if p.montant < 0)
    total_credits = sum(p.montant for p in previsions if p.montant > 0)
    print(f"\n  Total prélèvements attendus : {euros(total_debits)}")
    if total_credits:
        print(f"  Total rentrées attendues    : {euros(total_credits)}")
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
        suggestion = suggerer_budget(transactions, categoriseur)
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
        budget = suggerer_budget(transactions, categoriseur)
    recurrences = [r for r in detecter_recurrences(transactions, fin_releves) if r.active]
    previsions = prevoir(recurrences, fin_releves + timedelta(days=1), fin_releves + timedelta(days=31))
    rapport = rapport_mensuel(transactions, mois, categoriseur, budget, previsions)
    afficher_budget(rapport, fin_releves, budget_suggere, chemin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
