import io
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from assistant_financier.analyse import detecter_recurrences
from assistant_financier.cli import main
from assistant_financier.modeles import Prevision, Transaction
from assistant_financier.solde import (
    LIBELLE_COURANT, LIBELLE_DEPART, depenses_courantes_par_jour, detecter_decouvert, projeter_solde,
)

EXEMPLES = Path(__file__).resolve().parent.parent / "exemples"


def prev(jour, libelle, montant):
    return Prevision(jour, libelle, montant, "mensuelle", 1.0, False)


class TestProjection(unittest.TestCase):
    def setUp(self):
        self.previsions = [
            prev(date(2026, 7, 1), "SALAIRE", 2000.0),
            prev(date(2026, 7, 5), "LOYER", -700.0),
            prev(date(2026, 7, 10), "PRET", -300.0),
        ]

    def test_sans_decouvert(self):
        points = projeter_solde(1000.0, date(2026, 7, 1), date(2026, 7, 31), self.previsions)
        self.assertEqual(points[0].libelle, LIBELLE_DEPART)
        self.assertEqual(points[-1].solde, 2000.0)
        self.assertIsNone(detecter_decouvert(points))

    def test_decouvert_cause_par_un_prelevement(self):
        points = projeter_solde(500.0, date(2026, 7, 2), date(2026, 7, 31), self.previsions)
        d = detecter_decouvert(points, decouvert_autorise=300.0)
        self.assertEqual((d.date, d.cause, d.solde), (date(2026, 7, 5), "LOYER", -200.0))
        self.assertEqual((d.plus_bas, d.date_plus_bas), (-500.0, date(2026, 7, 10)))
        self.assertEqual(d.depassement_autorise, date(2026, 7, 10))
        self.assertEqual(d.a_approvisionner, 500.0)

    def test_dans_le_decouvert_autorise(self):
        points = projeter_solde(600.0, date(2026, 7, 2), date(2026, 7, 31), self.previsions)
        d = detecter_decouvert(points, decouvert_autorise=500.0)
        self.assertEqual(d.plus_bas, -400.0)
        self.assertIsNone(d.depassement_autorise)

    def test_debits_avant_credits_le_meme_jour(self):
        previsions = [prev(date(2026, 7, 1), "SALAIRE", 2000.0), prev(date(2026, 7, 1), "LOYER", -700.0)]
        points = projeter_solde(100.0, date(2026, 7, 1), date(2026, 7, 1), previsions)
        self.assertEqual(detecter_decouvert(points).cause, "LOYER")

    def test_depenses_courantes(self):
        points = projeter_solde(50.0, date(2026, 7, 2), date(2026, 7, 4), [], courant_par_jour=20.0)
        d = detecter_decouvert(points)
        self.assertEqual((d.date, d.cause, d.solde), (date(2026, 7, 4), LIBELLE_COURANT, -10.0))

    def test_deja_a_decouvert(self):
        points = projeter_solde(-30.0, date(2026, 7, 2), date(2026, 7, 4), [])
        self.assertEqual(detecter_decouvert(points).cause, LIBELLE_DEPART)


class TestDepensesCourantes(unittest.TestCase):
    def test_hors_prelevements_et_mediane(self):
        ops = []
        # Achats à des jours irréguliers (sinon ce seraient des récurrences) ; mars : gros achat ponctuel.
        for m, jour, courses in ((1, 15, 300.0), (2, 3, 310.0), (3, 22, 2000.0)):
            ops.append(Transaction(date(2026, m, 1), "VIR SEPA RECU SALAIRE", 2000.0))
            ops.append(Transaction(date(2026, m, 5), "PRLV SEPA LOYER", -700.0))
            ops.append(Transaction(date(2026, m, jour), "CB COURSES", -courses))
            ops.append(Transaction(date(2026, m, 30 - jour), "CB DIVERS", -10.0))
        ops.append(Transaction(date(2026, 4, 1), "VIR SEPA RECU SALAIRE", 2000.0))  # mars est complet
        ops.sort(key=lambda t: t.date)
        par_jour = depenses_courantes_par_jour(ops, detecter_recurrences(ops))
        self.assertAlmostEqual(par_jour, 320.0 / 30.44, places=2)  # médiane de 310, 320 et 2010


class TestLigneDeCommande(unittest.TestCase):
    def lancer(self, *args, code_attendu=None):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            code = main([str(EXEMPLES), *args])
        if code_attendu is not None:
            self.assertEqual(code, code_attendu)
        return sortie.getvalue()

    def test_code_de_sortie_des_alertes(self):
        self.lancer("--alertes", "--solde", "500", "--du", "02/07/2026", code_attendu=3)  # découvert
        self.lancer("--alertes", "--solde", "3000", code_attendu=3)  # hausses récentes (Netflix, Free)
        # Relevé du 1er trimestre seul : ni hausse ni découvert.
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            code = main([str(EXEMPLES / "releve_2026_T1.csv"), "--alertes", "--solde", "3000"])
        self.assertEqual(code, 0)

    def test_alerte_affichee(self):
        texte = self.lancer("--alertes", "--solde", "500", "--du", "02/07/2026", "--decouvert-autorise", "400")
        self.assertIn("Alerte : découvert prévu", texte)
        self.assertIn("LOYER IMMO GESTION", texte)
        self.assertIn("Au-delà de votre découvert autorisé", texte)

    def test_pas_d_alerte(self):
        texte = self.lancer("--alertes", "--solde", "3000")
        self.assertNotIn("Alerte : découvert prévu", texte)
        self.assertIn("Pas de découvert prévu", texte)

    def test_debits_seuls_n_ignorent_pas_le_salaire(self):
        # Avec --debits, le salaire n'est pas affiché mais compte toujours dans le solde projeté.
        self.assertNotIn("Alerte : découvert prévu", self.lancer("--debits", "--solde", "1500", "--jours", "40"))


if __name__ == "__main__":
    unittest.main()
