"""Interface en ligne de commande.

Exemples :
    python -m assistant_financier releves/*.csv
    python -m assistant_financier releves/ --jours 60 --solde 1520.40
    python -m assistant_financier releves/ --export previsions.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

from .analyse import detecter_recurrences, montant_prevu, prevoir
from .import_releves import charger_releves, parser_date

EXTENSIONS = {".csv", ".txt", ".tsv", ".ofx", ".qfx"}
JOURS_SEMAINE = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def euros(montant: float) -> str:
    texte = f"{montant:+,.2f}".replace(",", " ").replace(".", ",")
    return f"{texte} €"


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
    args = parser.parse_args(argv)

    transactions = charger_releves(_fichiers(args.releves))
    if not transactions:
        print("Aucune opération lue dans les relevés.")
        return 1
    debut_releves, fin_releves = transactions[0].date, transactions[-1].date
    print(f"{len(transactions)} opérations lues du {debut_releves:%d/%m/%Y} au {fin_releves:%d/%m/%Y}.\n")

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


if __name__ == "__main__":
    sys.exit(main())
