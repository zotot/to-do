import tempfile
import unittest
from datetime import date
from pathlib import Path

from assistant_financier.analyse import detecter_recurrences, prevoir, prochaines_dates
from assistant_financier.import_releves import charger_releves, parser_montant
from assistant_financier.libelles import normaliser
from assistant_financier.modeles import Transaction

EXEMPLES = Path(__file__).resolve().parent.parent / "exemples"


class TestLibelles(unittest.TestCase):
    def test_libelle_stable_d_un_mois_a_l_autre(self):
        a = normaliser("PRLV SEPA FREE MOBILE FM-1234567 ECH/080126 ID EMETTEUR/FR12ZZZ")
        b = normaliser("PRLV SEPA FREE MOBILE FM-7654321 ECH/080226 ID EMETTEUR/FR12ZZZ")
        self.assertEqual(a, "FREE MOBILE")
        self.assertEqual(a, b)

    def test_carte(self):
        self.assertEqual(normaliser("CB NETFLIX.COM 12/03"), normaliser("PAIEMENT PAR CARTE X1234 NETFLIX.COM 12/04"))

    def test_libelle_reduit_au_prefixe(self):
        self.assertEqual(normaliser("ECHEANCE PRET 00123456"), "ECHEANCE PRET")


class TestImport(unittest.TestCase):
    def test_montants(self):
        self.assertEqual(parser_montant("-1 234,56"), -1234.56)
        self.assertEqual(parser_montant("1.234,56 €"), 1234.56)
        self.assertEqual(parser_montant("-45.90"), -45.90)
        self.assertEqual(parser_montant("12,00-"), -12.0)
        self.assertIsNone(parser_montant(""))

    def test_releves_qui_se_chevauchent_sont_dedoublonnes(self):
        ops = charger_releves(sorted(EXEMPLES.glob("*.csv")))
        cles = [(t.date, t.libelle, t.montant) for t in ops]
        self.assertEqual(len(cles), len(set(cles)))
        self.assertEqual(ops[0].date, date(2026, 1, 1))

    def test_ofx(self):
        contenu = """OFXHEADER:100
<OFX><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260108<TRNAMT>-19.99<NAME>PRLV SEPA FREE MOBILE
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260208<TRNAMT>-19.99<NAME>PRLV SEPA FREE MOBILE
</BANKTRANLIST></OFX>"""
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / "releve.ofx"
            chemin.write_text(contenu)
            ops = charger_releves([chemin])
        self.assertEqual([(t.date, t.montant) for t in ops], [(date(2026, 1, 8), -19.99), (date(2026, 2, 8), -19.99)])


class TestDossierDrive(unittest.TestCase):
    """Dossier alimenté depuis Synology Drive : sous-dossiers, fichiers parasites."""

    def test_lecture_recursive_et_fichiers_ignores(self):
        from assistant_financier.cli import _fichiers

        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            for chemin in ("2026/releve_T1.csv", "releve_T2.csv", "2026/@eaDir/releve_T1.csv@SynoEAStream",
                           "#recycle/ancien.csv", ".cache/x.csv", "~$temp.csv", "releve_juin.pdf"):
                (racine / chemin).parent.mkdir(parents=True, exist_ok=True)
                (racine / chemin).write_text("x")
            ignores = []
            trouves = [str(f.relative_to(racine)) for f in _fichiers([d], ignores)]
        self.assertEqual(trouves, ["2026/releve_T1.csv", "releve_T2.csv"])
        self.assertEqual(ignores, ["releve_juin.pdf"])

    def test_fichier_illisible_ignore(self):
        with tempfile.TemporaryDirectory() as d:
            note = Path(d) / "note.txt"
            note.write_text("penser à payer la cantine\n")
            erreurs = []
            ops = charger_releves([note, EXEMPLES / "releve_2026_T1.csv"], erreurs)
            with self.assertRaises(ValueError):
                charger_releves([note])  # sans liste d'erreurs : comportement strict inchangé
        self.assertTrue(ops)
        self.assertEqual(len(erreurs), 1)
        self.assertTrue(erreurs[0].startswith("note.txt : colonnes introuvables"))


def mensuel(libelle, montant, jour, mois, annee=2026):
    return [Transaction(date(annee, m, jour), libelle, montant) for m in mois]


class TestRecurrences(unittest.TestCase):
    def test_detection_et_prevision(self):
        ops = mensuel("PRLV SEPA ABONNEMENT GAZ", -40.0, 10, range(1, 6))
        ops += [Transaction(date(2026, 5, j), f"CB BOULANGERIE {j:02d}/05", -3.0 - j) for j in (2, 9, 13, 21)]
        recs = detecter_recurrences(ops, date(2026, 5, 31))
        self.assertEqual([r.cle for r in recs], ["ABONNEMENT GAZ"])
        self.assertEqual(recs[0].frequence, "mensuelle")
        prev = prevoir(recs, date(2026, 6, 1), date(2026, 7, 31))
        self.assertEqual([p.date for p in prev], [date(2026, 6, 10), date(2026, 7, 10)])
        self.assertEqual(prev[0].montant, -40.0)

    def test_abonnement_resilie(self):
        ops = mensuel("PRLV SEPA SALLE DE SPORT", -29.9, 3, range(1, 4))
        ops += [Transaction(date(2026, 6, 30), "CB DIVERS", -1.0)]
        recs = detecter_recurrences(ops)
        self.assertFalse(recs[0].active)
        self.assertEqual(prevoir(recs, date(2026, 7, 1), date(2026, 8, 31)), [])

    def test_mois_saute_tolere(self):
        ops = mensuel("PRLV SEPA ASSURANCE", -20.0, 15, [1, 2, 4, 5, 6])
        self.assertEqual(detecter_recurrences(ops)[0].frequence, "mensuelle")

    def test_deux_abonnements_meme_marchand(self):
        ops = mensuel("CB APPLE.COM/BILL", -0.99, 3, range(1, 6))
        ops += mensuel("CB APPLE.COM/BILL", -9.99, 18, range(1, 6))
        recs = detecter_recurrences(ops)
        self.assertEqual(sorted(r.montant_dernier for r in recs), [-9.99, -0.99])

    def test_report_au_jour_ouvre(self):
        # Débité les 5 sauf le week-end : la prévision du dimanche 5/07/2026 passe au lundi 6.
        ops = mensuel("PRLV SEPA LOYER", -700.0, 5, [1, 2, 3, 5, 6])
        r = detecter_recurrences(ops)[0]
        self.assertIn(date(2026, 7, 6), prochaines_dates(r, date(2026, 7, 31)))

    def test_fin_de_mois(self):
        ops = mensuel("PRLV SEPA CREDIT", -100.0, 31, [1, 3, 5])
        ops += mensuel("PRLV SEPA CREDIT", -100.0, 28, [2]) + mensuel("PRLV SEPA CREDIT", -100.0, 30, [4])
        r = detecter_recurrences(ops)[0]
        self.assertEqual(prochaines_dates(r, date(2026, 7, 31))[0].month, 6)


if __name__ == "__main__":
    unittest.main()
