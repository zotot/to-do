import unittest
from datetime import date

from assistant_financier.alertes import detecter_hausses
from assistant_financier.analyse import detecter_recurrences, prevoir
from assistant_financier.modeles import Transaction


def mensuel(libelle, montants, jour=12, debut=1):
    return [Transaction(date(2026, debut + i, jour), libelle, m) for i, m in enumerate(montants)]


class TestHausses(unittest.TestCase):
    def test_hausse_detectee_et_nouveau_tarif_prevu(self):
        ops = mensuel("CB NETFLIX.COM", [-13.49] * 4 + [-15.99] * 2)
        recs = detecter_recurrences(ops)
        self.assertEqual(len(recs), 1)  # pas coupé en deux abonnements
        r = recs[0]
        self.assertFalse(r.montant_variable)
        hausses = detecter_hausses(recs)
        self.assertEqual(len(hausses), 1)
        h = hausses[0]
        self.assertEqual((h.ancien, h.nouveau, h.depuis), (13.49, 15.99, date(2026, 5, 12)))
        self.assertAlmostEqual(h.surcout_annuel, 30.0, places=1)
        self.assertAlmostEqual(h.pourcentage, 0.1853, places=3)
        self.assertEqual(prevoir(recs, date(2026, 7, 1), date(2026, 7, 31))[0].montant, -15.99)

    def test_hausse_le_dernier_mois(self):
        ops = mensuel("PRLV SEPA FREE MOBILE", [-19.99] * 5 + [-24.99])
        self.assertEqual(detecter_hausses(detecter_recurrences(ops))[0].nouveau, 24.99)

    def test_deux_hausses_successives(self):
        ops = mensuel("CB SPOTIFY", [-9.99] * 3 + [-10.99] * 3 + [-11.99] * 2, debut=1)
        hausses = detecter_hausses(detecter_recurrences(ops))
        self.assertEqual([(h.ancien, h.nouveau) for h in hausses], [(10.99, 11.99), (9.99, 10.99)])

    def test_petite_variation_de_change_ignoree(self):
        ops = mensuel("CB CHATGPT SUBSCRIPTION", [-21.50, -21.55, -21.48, -21.52, -21.60])
        self.assertEqual(detecter_hausses(detecter_recurrences(ops)), [])

    def test_montant_variable_sans_alerte(self):
        ops = mensuel("PRLV SEPA EDF", [-60.0, -82.5, -71.3, -95.0, -66.1, -88.0])
        recs = detecter_recurrences(ops)
        self.assertTrue(recs[0].montant_variable)
        self.assertEqual(detecter_hausses(recs), [])

    def test_baisse_et_revenus_ignores(self):
        ops = mensuel("CB DEEZER", [-11.99] * 3 + [-9.99] * 3)
        ops += mensuel("VIR SEPA RECU SALAIRE", [2000.0] * 3 + [2100.0] * 3, jour=1)
        self.assertEqual(detecter_hausses(detecter_recurrences(ops)), [])

    def test_abonnement_arrete_sans_alerte(self):
        ops = mensuel("CB DISNEY PLUS", [-8.99] * 3 + [-10.99] * 2)
        ops.append(Transaction(date(2026, 12, 20), "CB DIVERS", -1.0))
        self.assertEqual(detecter_hausses(detecter_recurrences(ops)), [])


if __name__ == "__main__":
    unittest.main()
