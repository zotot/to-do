import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from assistant_financier.cli import main
from assistant_financier.import_releves import charger_releves, verifier_releves
from assistant_financier.modeles import InfoReleve
from assistant_financier.pdf import lire_pdf, montant_pdf

EXEMPLES = Path(__file__).resolve().parent.parent / "exemples"
PDF = sorted((EXEMPLES / "pdf").glob("*.pdf"))

try:
    from reportlab.pdfgen import canvas
except ImportError:  # reportlab ne sert qu'à fabriquer des PDF de test
    canvas = None


def cle(t):
    return (t.date, t.libelle, round(t.montant, 2))


class TestMontants(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(montant_pdf("1 234,56"), 1234.56)
        self.assertEqual(montant_pdf("1.234,56"), 1234.56)
        self.assertEqual(montant_pdf("-19,99 €"), -19.99)
        self.assertEqual(montant_pdf("+2 450,00 €"), 2450.0)
        self.assertEqual(montant_pdf("12,00-"), -12.0)
        self.assertEqual(montant_pdf("0,99"), 0.99)
        for pas_un_montant in ("12.05", "03/01", "FM-4960229", "Page 1/2", "1234"):
            self.assertIsNone(montant_pdf(pas_un_montant), pas_un_montant)


class TestExemplesPdf(unittest.TestCase):
    def test_memes_operations_que_les_csv(self):
        pdf = charger_releves(PDF)
        csv = charger_releves(sorted(EXEMPLES.glob("*.csv")))
        self.assertEqual(sorted(map(cle, pdf)), sorted(map(cle, csv)))

    def test_libelle_sur_deux_lignes(self):
        ops, _ = lire_pdf(EXEMPLES / "pdf" / "releve_2026_01.pdf")
        salaire = next(t for t in ops if t.montant > 0)
        self.assertEqual(salaire.libelle, "VIR SEPA RECU /DE SOCIETE EXEMPLE /MOTIF SALAIRE JANVIER")

    def test_soldes_et_controle(self):
        infos = []
        charger_releves(PDF, [], infos)
        verifier_releves(infos)
        self.assertEqual([i.ecart for i in infos], [0.0] * 6)
        janvier = infos[0]
        self.assertEqual((janvier.solde_initial, janvier.solde_final), (1000.0, 1767.97))
        self.assertEqual(infos[-1].fin, date(2026, 6, 30))

    def test_solde_automatique_en_ligne_de_commande(self):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            main([str(EXEMPLES / "pdf"), "--alertes"])
        texte = sortie.getvalue()
        self.assertIn("6/6 relevé(s) vérifié(s) ✓", texte)
        self.assertIn("Solde lu dans releve_2026_06.pdf", texte)


class TestControle(unittest.TestCase):
    def test_ecart_signale(self):
        info = InfoReleve("mai.pdf", date(2026, 5, 1), date(2026, 5, 31), 10, total=-100.0,
                          solde_initial=500.0, solde_final=412.5)
        self.assertEqual(info.ecart, -12.5)

    def test_chainage_des_releves(self):
        avril = InfoReleve("avril.pdf", date(2026, 4, 1), date(2026, 4, 30), 5, -50.0, solde_final=950.0)
        mai = InfoReleve("mai.pdf", date(2026, 5, 2), date(2026, 5, 31), 5, -50.0, solde_final=900.0)
        juillet = InfoReleve("juillet.pdf", date(2026, 7, 1), date(2026, 7, 31), 5, -50.0, solde_final=800.0)
        verifier_releves([juillet, mai, avril])
        self.assertEqual(mai.ecart, 0.0)
        self.assertIsNone(juillet.ecart)  # juin manquant : pas de comparaison possible


class TestDiagnostic(unittest.TestCase):
    def test_masquage(self):
        from assistant_financier.pdf import masquer

        texte = masquer("12.05 PRLV SEPA URSSAF AQUITAINE ECH/120526 1 234,56 M. Jean Dupont")
        self.assertEqual(texte, "99.99 PRLV SEPA XXXXXX XXXXXXXXX XXX/999999 9 999,99 X. Xxxx Xxxxxx")

    def test_diagnostic_anonyme(self):
        from assistant_financier.pdf import diagnostic_pdf

        texte = diagnostic_pdf(EXEMPLES / "pdf" / "releve_2026_01.pdf")
        self.assertIn("en-tête du tableau", texte)
        self.assertIn("solde daté", texte)
        self.assertIn("soldes cohérents", texte)
        for prive in ("CARREFOUR", "NETFLIX", "1 767,97", "00012345678", "SOCIETE EXEMPLE"):
            self.assertNotIn(prive, texte)
        # Textes du relevé (après « position: ») : aucun autre chiffre que 9.
        lignes = texte.split("position:texte", 1)[1].strip().splitlines()
        textes = [seg.split(":", 1)[1] for ligne in lignes for seg in ligne.split(" | ") if ":" in seg]
        self.assertTrue(textes)
        self.assertEqual({c for t in textes for c in t if c.isdigit()}, {"9"})

    def test_option_diagnostic(self):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            main([str(EXEMPLES / "pdf"), "--diagnostic"])
        self.assertIn("aucun diagnostic nécessaire", sortie.getvalue())


@unittest.skipIf(canvas is None, "reportlab requis pour fabriquer les PDF de test")
class TestPdfFabriques(unittest.TestCase):
    def fabriquer(self, dessiner, **options):
        dossier = tempfile.mkdtemp()
        chemin = Path(dossier) / "releve.pdf"
        c = canvas.Canvas(str(chemin), **options)
        dessiner(c)
        c.save()
        return chemin

    def test_releve_a_cheval_sur_deux_annees_et_solde_debiteur(self):
        def dessiner(c):
            c.drawString(40, 800, "Relevé du 15/12/2025 au 14/01/2026")
            for x, t in ((40, "Date"), (120, "Libellé")):
                c.drawString(x, 760, t)
            c.drawRightString(450, 760, "Débit")
            c.drawRightString(530, 760, "Crédit")
            c.drawString(120, 745, "SOLDE CREDITEUR AU 14.12.2025")
            c.drawRightString(530, 745, "20,00")
            c.drawString(40, 730, "28.12")
            c.drawString(120, 730, "CB CADEAU NOEL")
            c.drawRightString(450, 730, "45,00")
            c.drawString(40, 715, "05.01")
            c.drawString(120, 715, "REMBOURSEMENT")
            c.drawRightString(530, 715, "10,00")
            c.drawString(120, 700, "NOUVEAU SOLDE DEBITEUR AU 14.01.2026")
            c.drawRightString(450, 700, "15,00")

        ops, info = lire_pdf(self.fabriquer(dessiner))
        self.assertEqual([(t.date, t.montant) for t in ops], [(date(2025, 12, 28), -45.0), (date(2026, 1, 5), 10.0)])
        self.assertEqual((info.solde_initial, info.solde_final, info.ecart), (20.0, -15.0, 0.0))

    def test_colonne_sens(self):
        def dessiner(c):
            c.drawString(40, 800, "Relevé du 01/05/2026 au 31/05/2026")
            c.drawString(40, 760, "Date")
            c.drawString(110, 760, "Libellé")
            c.drawRightString(470, 760, "Montant")
            c.drawString(500, 760, "Sens")
            lignes = [("", "SOLDE AU 30/04/2026", "1 000,00", "C"),
                      ("05/05/2026", "PRLV SEPA URSSAF AQUITAINE", "850,00", "D"),
                      ("10/05/2026", "VIR SEPA RECU /DE CLIENT", "1 800,00", "C"),
                      ("", "SOLDE AU 31/05/2026", "1 950,00", "C")]
            for i, (jour, libelle, montant, sens) in enumerate(lignes):
                y = 745 - 14 * i
                c.drawString(40, y, jour)
                c.drawString(110, y, libelle)
                c.drawRightString(470, y, montant)
                c.drawString(500, y, sens)

        ops, info = lire_pdf(self.fabriquer(dessiner))
        self.assertEqual([(t.libelle, t.montant) for t in ops],
                         [("PRLV SEPA URSSAF AQUITAINE", -850.0), ("VIR SEPA RECU /DE CLIENT", 1800.0)])
        self.assertEqual(info.ecart, 0.0)

    def test_releve_mal_lu_declenche_une_alerte(self):
        def dessiner(c):
            c.drawString(40, 800, "Relevé du 01/05/2026 au 31/05/2026")
            c.drawString(40, 760, "Date")
            c.drawString(110, 760, "Libellé")
            c.drawRightString(400, 760, "Débit")
            c.drawRightString(480, 760, "Crédit")
            c.drawString(110, 745, "SOLDE CREDITEUR AU 30.04.2026")
            c.drawRightString(480, 745, "1 000,00")
            c.drawString(40, 730, "05.05")
            c.drawString(110, 730, "PRLV SEPA URSSAF")
            c.drawRightString(400, 730, "850,00")
            c.drawString(110, 715, "NOUVEAU SOLDE CREDITEUR AU 31.05.2026")
            c.drawRightString(480, 715, "100,00")  # 50 € d'écart : une opération manque

        chemin = self.fabriquer(dessiner)
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            code = main([str(chemin), "--alertes", "--solde", "100000"])
        self.assertEqual(code, 3)
        self.assertIn("écart de 50,00 €", sortie.getvalue())
        with redirect_stdout(io.StringIO()) as diag:
            main([str(chemin.parent), "--diagnostic"])
        self.assertIn("soldes NON cohérents", diag.getvalue())

    def test_pdf_sans_texte(self):
        chemin = self.fabriquer(lambda c: c.rect(100, 100, 200, 200, fill=1))
        with self.assertRaisesRegex(ValueError, "scanné"):
            lire_pdf(chemin)

    def test_pdf_protege(self):
        chemin = self.fabriquer(lambda c: c.drawString(100, 700, "Relevé"), encrypt="secret")
        with self.assertRaisesRegex(ValueError, "mot de passe"):
            lire_pdf(chemin)

    def test_pdf_qui_n_est_pas_un_releve(self):
        chemin = self.fabriquer(lambda c: c.drawString(100, 700, "Facture n° 123 - Total 45,00 €"))
        erreurs = []
        self.assertEqual(charger_releves([chemin], erreurs), [])
        self.assertIn("tableau des opérations introuvable", erreurs[0])


if __name__ == "__main__":
    unittest.main()
