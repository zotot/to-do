import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from assistant_financier.analyse import detecter_recurrences, prevoir
from assistant_financier.budget import (
    charger_config, ecrire_config, mois_complets, moyennes_mensuelles, rapport_mensuel, suggerer_budget,
)
from assistant_financier.categories import Categoriseur
from assistant_financier.modeles import Transaction

T = Transaction


class TestCategories(unittest.TestCase):
    def setUp(self):
        self.cat = Categoriseur()

    def test_categories_par_defaut(self):
        cas = {
            "CB CARREFOUR MARKET 12/03": "Alimentation",
            "PRLV SEPA FREE MOBILE FM-123 ECH/0801": "Télécom & internet",
            "CB NETFLIX.COM 12/03": "Abonnements & loisirs",
            "PAIEMENT PAR CARTE X1234 UBER EATS": "Restaurants & sorties",
            "PAIEMENT PAR CARTE X1234 UBER TRIP": "Transport",
            "CB AMAZON PRIME 03/02": "Abonnements & loisirs",
            "CB AMAZON MKTPLACE 03/02": "Shopping",
            "PRLV SEPA AXA ASSURANCE VIE": "Épargne",
            "RETRAIT DAB 12/03 PARIS": "Retraits d'espèces",
            "CB BOULANGERIE DUPONT": "Alimentation",
            "CB BOULANGER 12/03": "Shopping",
            "VIR SEPA EMIS /A DUPONT": "Virements émis",
            "CB MAGASIN INCONNU": "Autres",
        }
        for libelle, attendu in cas.items():
            with self.subTest(libelle=libelle):
                self.assertEqual(self.cat.categoriser(T(date(2026, 3, 1), libelle, -10.0)), attendu)

    def test_compte_professionnel(self):
        cas = {
            "PRLV SEPA URSSAF AQUITAINE": "Charges sociales",
            "PRLV SEPA CIPAV RETRAITE": "Charges sociales",
            "PRLV SEPA DGFIP TVA 3310CA3": "Impôts & taxes",
            "CB OVH SAS": "Logiciels & services pro",
            "CB AMAZON WEB SERVICES": "Logiciels & services pro",
            "CB MICROSOFT XBOX": "Abonnements & loisirs",
            "COMMISSIONS MONETIQUE": "Frais bancaires",
        }
        for libelle, attendu in cas.items():
            with self.subTest(libelle=libelle):
                self.assertEqual(self.cat.categoriser(T(date(2026, 3, 1), libelle, -10.0)), attendu)
        # Encaissements : remises de carte et virements de clients sont des revenus.
        self.assertEqual(self.cat.categoriser(T(date(2026, 3, 1), "REMISE CB 0512 TPE 1234", 640.0)), "Revenus")
        self.assertEqual(self.cat.categoriser(T(date(2026, 3, 1), "VIR SEPA RECU /DE CLIENT /MOTIF FACTURE", 1800.0)), "Revenus")

    def test_credits(self):
        self.assertEqual(self.cat.categoriser(T(date(2026, 3, 1), "VIR SEPA RECU /DE ORANGE SA /MOTIF SALAIRE", 2000)), "Revenus")
        self.assertEqual(self.cat.categoriser(T(date(2026, 3, 1), "VIR SEPA RECU /DE MAMAN", 50)), "Revenus")
        # Remboursement : vient en déduction de la catégorie de dépense.
        self.assertEqual(self.cat.categoriser(T(date(2026, 3, 1), "REMBT CPAM 123", 25)), "Santé")

    def test_regles_perso_prioritaires(self):
        cat = Categoriseur({"Animaux": ["ZOOPLUS", "VETERINAIRE"], "Courses": ["CARREFOUR"]})
        self.assertEqual(cat.categoriser(T(date(2026, 3, 1), "CB ZOOPLUS DE 03/02", -40)), "Animaux")
        self.assertEqual(cat.categoriser(T(date(2026, 3, 1), "CB CARREFOUR 03/02", -40)), "Courses")


class TestBudget(unittest.TestCase):
    def setUp(self):
        self.cat = Categoriseur()
        self.ops = []
        for m in (1, 2, 3):
            self.ops += [
                T(date(2026, m, 1), "VIR SEPA RECU SALAIRE", 2000.0),
                T(date(2026, m, 5), "PRLV SEPA LOYER", -700.0),
                T(date(2026, m, 25), "PRLV SEPA EDF", -60.0),
                T(date(2026, m, 10), "CB LIDL", -100.0 * m),
            ]
        # Avril incomplet : relevé arrêté au 15, l'EDF du 25 n'est pas encore passée.
        self.ops += [
            T(date(2026, 4, 1), "VIR SEPA RECU SALAIRE", 2000.0),
            T(date(2026, 4, 5), "PRLV SEPA LOYER", -700.0),
            T(date(2026, 4, 10), "CB LIDL", -150.0),
            T(date(2026, 4, 12), "CB LIDL", 20.0),  # remboursement
            T(date(2026, 4, 15), "CB INCONNU", -9.0),
        ]
        self.ops.sort(key=lambda t: t.date)

    def test_mois_complets(self):
        self.assertEqual(mois_complets(date(2026, 1, 3), date(2026, 4, 15)), [(2026, 1), (2026, 2), (2026, 3)])

    def test_moyennes_sur_mois_complets(self):
        moy = moyennes_mensuelles(self.ops, self.cat)
        self.assertEqual(moy["Alimentation"], 200.0)
        self.assertEqual(moy["Logement"], 700.0)
        self.assertEqual(moy["Revenus"], 2000.0)

    def test_suggestion_arrondie(self):
        sugg = suggerer_budget(self.ops, self.cat)
        self.assertEqual(sugg["Énergie & eau"], 60.0)
        self.assertNotIn("Revenus", sugg)

    def test_rapport_mois_en_cours_avec_prevision(self):
        recs = [r for r in detecter_recurrences(self.ops) if r.active]
        previsions = prevoir(recs, date(2026, 4, 16), date(2026, 5, 15))
        r = rapport_mensuel(self.ops, (2026, 4), self.cat, {"Alimentation": 120, "Énergie & eau": 70}, previsions)
        self.assertFalse(r.complet)
        lignes = {l.categorie: l for l in r.lignes}
        self.assertEqual(lignes["Alimentation"].depense, 130.0)  # 150 - 20 remboursés
        self.assertAlmostEqual(lignes["Alimentation"].reste, -10.0)
        self.assertEqual(lignes["Énergie & eau"].depense, 0.0)
        self.assertEqual(lignes["Énergie & eau"].prevu, 60.0)  # prélèvement EDF attendu le 25
        self.assertEqual([t.libelle for t in r.non_classees], ["CB INCONNU"])
        self.assertEqual(r.revenus, 2000.0)

    def test_fichier_config(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / "budget.json"
            self.assertEqual(charger_config(chemin), ({}, {}))
            ecrire_config(chemin, {"Alimentation": 300.0}, {"Animaux": ["ZOOPLUS"]})
            self.assertEqual(json.loads(chemin.read_text())["budget"], {"Alimentation": 300.0})
            self.assertEqual(charger_config(chemin), ({"Alimentation": 300.0}, {"Animaux": ["ZOOPLUS"]}))


if __name__ == "__main__":
    unittest.main()
