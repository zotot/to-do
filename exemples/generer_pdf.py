"""Génère les relevés PDF d'exemple (exemples/pdf/) à partir des relevés CSV d'exemple.

Deux mises en page courantes, pour tester la lecture des PDF :
  - janvier à mars : banque « traditionnelle » (Date | Valeur | Libellé | Débit | Crédit,
    dates sans l'année, libellés sur deux lignes, plusieurs pages, ancien et nouveau solde) ;
  - avril à juin : banque « en ligne » (Date | Libellé | Montant signé, solde final seul).

Nécessite reportlab (uniquement pour régénérer les exemples) :
    pip install reportlab && python3 exemples/generer_pdf.py
"""

from __future__ import annotations

import calendar
import sys
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE.parent))

from assistant_financier.import_releves import charger_releves  # noqa: E402

SOLDE_DEPART = 1000.00  # solde au 31/12/2025
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
LIGNES_PAR_PAGE = 16  # petit, pour obtenir des relevés sur plusieurs pages


def fr(montant: float) -> str:
    return f"{abs(montant):,.2f}".replace(",", " ").replace(".", ",")


def couper(libelle: str, largeur: int = 34) -> list[str]:
    mots, lignes, courante = libelle.split(), [], ""
    for mot in mots:
        if courante and len(courante) + 1 + len(mot) > largeur:
            lignes.append(courante)
            courante = mot
        else:
            courante = f"{courante} {mot}".strip()
    return lignes + [courante]


def traditionnel(chemin: Path, mois: int, ops, solde_initial: float) -> float:
    c = canvas.Canvas(str(chemin), pagesize=A4)
    debut, fin = date(2026, mois, 1), date(2026, mois, calendar.monthrange(2026, mois)[1])
    pages = [[]]
    for t in ops:
        lignes = couper(t.libelle)
        if len(pages[-1]) + len(lignes) > LIGNES_PAR_PAGE:
            pages.append([])
        pages[-1].extend([(t, i, texte) for i, texte in enumerate(lignes)])
    solde = solde_initial + sum(t.montant for t in ops)

    for n, lignes in enumerate(pages, 1):
        c.setFont("Helvetica-Bold", 13)
        c.drawString(40, 800, "BANQUE EXEMPLE")
        c.setFont("Helvetica", 9)
        c.drawString(40, 785, "M. CLIENT EXEMPLE - Compte de dépôt n° 00012345678")
        c.drawString(40, 772, f"RELEVÉ DE COMPTE du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}")
        y = 740
        c.setFont("Helvetica-Bold", 9)
        for x, titre in ((40, "Date"), (80, "Valeur"), (125, "Libellé des opérations")):
            c.drawString(x, y, titre)
        c.drawRightString(470, y, "Débit")
        c.drawRightString(550, y, "Crédit")
        c.setFont("Helvetica", 9)
        y -= 18
        if n == 1:
            veille = date.fromordinal(debut.toordinal() - 1)
            sens = "CREDITEUR" if solde_initial >= 0 else "DEBITEUR"
            c.drawString(125, y, f"SOLDE {sens} AU {veille:%d.%m.%Y}")
            c.drawRightString(550 if solde_initial >= 0 else 470, y, fr(solde_initial))
            y -= 14
        for t, i, texte in lignes:
            if i == 0:
                c.drawString(40, y, f"{t.date:%d.%m}")
                c.drawString(80, y, f"{t.date:%d.%m}")
                c.drawRightString(470 if t.montant < 0 else 550, y, fr(t.montant))
            c.drawString(125, y, texte)
            y -= 12
        if n == len(pages):
            y -= 6
            debits = -sum(t.montant for t in ops if t.montant < 0)
            credits = sum(t.montant for t in ops if t.montant > 0)
            c.drawString(125, y, "TOTAL DES OPERATIONS")
            c.drawRightString(470, y, fr(debits))
            c.drawRightString(550, y, fr(credits))
            y -= 14
            c.setFont("Helvetica-Bold", 9)
            sens = "CREDITEUR" if solde >= 0 else "DEBITEUR"
            c.drawString(125, y, f"NOUVEAU SOLDE {sens} AU {fin:%d.%m.%Y}")
            c.drawRightString(550 if solde >= 0 else 470, y, fr(solde))
        c.setFont("Helvetica", 7)
        c.drawString(40, 40, "Banque Exemple - SA au capital de 1 000 000 EUR - RCS Exemple 000 000 000")
        c.drawRightString(550, 40, f"Page {n}/{len(pages)}")
        c.showPage()
    c.save()
    return solde


def en_ligne(chemin: Path, mois: int, ops, solde_initial: float) -> float:
    c = canvas.Canvas(str(chemin), pagesize=A4)
    fin = date(2026, mois, calendar.monthrange(2026, mois)[1])
    solde = solde_initial + sum(t.montant for t in ops)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, 800, "Exemple Banque en ligne")
    c.setFont("Helvetica", 10)
    c.drawString(40, 782, f"Relevé de compte - {MOIS[mois - 1]} 2026")
    y = 750
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, "Date opération")
    c.drawString(130, y, "Libellé")
    c.drawRightString(550, y, "Montant")
    c.setFont("Helvetica", 9)
    y -= 16
    for t in ops:
        c.drawString(40, y, f"{t.date:%d/%m/%Y}")
        c.drawString(130, y, t.libelle[:70])
        c.drawRightString(550, y, ("-" if t.montant < 0 else "+") + fr(t.montant) + " €")
        y -= 13
    y -= 10
    c.setFont("Helvetica-Bold", 10)
    c.drawString(130, y, f"Solde au {fin:%d/%m/%Y}")
    c.drawRightString(550, y, ("-" if solde < 0 else "") + fr(solde) + " €")
    c.showPage()
    c.save()
    return solde


def main() -> None:
    ops = charger_releves(sorted((RACINE).glob("*.csv")))
    dossier = RACINE / "pdf"
    dossier.mkdir(exist_ok=True)
    solde = SOLDE_DEPART
    for mois in range(1, 7):
        du_mois = [t for t in ops if t.date.month == mois]
        chemin = dossier / f"releve_2026_{mois:02d}.pdf"
        generateur = traditionnel if mois <= 3 else en_ligne
        solde = generateur(chemin, mois, du_mois, solde)
        print(f"{chemin.name} : {len(du_mois)} opérations, solde final {fr(solde)}")


if __name__ == "__main__":
    main()
