import base64
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from assistant_financier.synthese import calculer
from assistant_financier.web import creer_serveur, rendre_page, servir

EXEMPLES = Path(__file__).resolve().parent.parent / "exemples"


def dossier_exemple(*, csv_supplementaire: str = "", reglages: str = "") -> Path:
    dossier = Path(tempfile.mkdtemp())
    shutil.copytree(EXEMPLES / "pdf", dossier / "releves")
    if csv_supplementaire:
        (dossier / "releves" / "extra.csv").write_text(csv_supplementaire, encoding="utf-8")
    if reglages:
        (dossier / "alertes.conf").write_text(reglages, encoding="utf-8")
    return dossier


class TestPage(unittest.TestCase):
    def test_page_complete(self):
        s = calculer(dossier_exemple())
        page = rendre_page(s, "test")
        for attendu in ("Solde du compte", "5 716,41 €", "Solde prévu sur 31 jours", "Prochaines échéances",
                        "Budget du mois", "Revenus et dépenses par mois", "Hausse d&#x27;abonnement", "soldes vérifiés"):
            self.assertIn(attendu, page)
        self.assertNotIn("http://", page.split("<body>", 1)[1])  # aucune ressource externe
        self.assertNotIn("https://", page)

    def test_point_bas_coherent(self):
        s = calculer(dossier_exemple(reglages="SOLDE=300\n"))
        page = rendre_page(s)
        self.assertEqual(s.point_bas.solde, 185.36)
        self.assertIn("point bas 185 € le 01/07", page)

    def test_decouvert_affiche(self):
        s = calculer(dossier_exemple(reglages="SOLDE=-20\nDECOUVERT_AUTORISE=100\n"))
        page = rendre_page(s)
        self.assertIn("Découvert prévu", page)
        self.assertIn("découvert autorisé", page)

    def test_decouvert_non_fiable_si_releve_mal_lu(self):
        from assistant_financier.synthese import Controle

        s = calculer(dossier_exemple(reglages="SOLDE=-20\n"))
        s.controles.append(Controle("faux.pdf", "ecart", 12.5, 3))
        self.assertIn("Découvert prévu (non fiable)", rendre_page(s))

    def test_libelles_echappes(self):
        csv = "Date;Libellé;Montant\n" + "".join(
            f"0{m}/0{m}/2026;<script>alert('x')</script> </script>;-9,99\n" for m in range(1, 7))
        page = rendre_page(calculer(dossier_exemple(csv_supplementaire=csv)))
        self.assertNotIn("<script>alert", page)
        self.assertEqual(page.count("</script>"), page.count("<script"))  # aucune balise injectée

    def test_dossier_vide(self):
        dossier = Path(tempfile.mkdtemp())
        (dossier / "releves").mkdir()
        page = rendre_page(calculer(dossier))
        self.assertIn("Aucune donnée à afficher", page)
        self.assertIn("Le dossier est vide", page)


class TestServeur(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.serveur = creer_serveur(dossier_exemple(), "s3cret", port=0, hote="127.0.0.1")
        cls.url = f"http://127.0.0.1:{cls.serveur.server_address[1]}"
        threading.Thread(target=cls.serveur.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.serveur.shutdown()
        cls.serveur.server_close()

    def ouvrir(self, chemin="/", mot_de_passe=None):
        requete = urllib.request.Request(self.url + chemin)
        if mot_de_passe is not None:
            jeton = base64.b64encode(f"moi:{mot_de_passe}".encode()).decode()
            requete.add_header("Authorization", f"Basic {jeton}")
        try:
            with urllib.request.urlopen(requete, timeout=30) as reponse:
                return reponse.status, reponse.read().decode(), reponse.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), e.headers

    def test_mot_de_passe_obligatoire(self):
        code, _, entetes = self.ouvrir()
        self.assertEqual(code, 401)
        self.assertIn("Basic", entetes["WWW-Authenticate"])
        self.assertEqual(self.ouvrir(mot_de_passe="faux")[0], 401)

    def test_page_avec_mot_de_passe(self):
        code, corps, entetes = self.ouvrir(mot_de_passe="s3cret")
        self.assertEqual(code, 200)
        self.assertIn("Assistant financier", corps)
        self.assertIn("default-src 'none'", entetes["Content-Security-Policy"])
        self.assertEqual(entetes["Cache-Control"], "no-store")

    def test_sante_sans_mot_de_passe(self):
        self.assertEqual(self.ouvrir("/sante")[:2], (200, "ok"))

    def test_page_inconnue(self):
        self.assertEqual(self.ouvrir("/../../etc/passwd", mot_de_passe="s3cret")[0], 404)

    def test_refus_sans_mot_de_passe_configure(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"MOT_DE_PASSE_WEB": ""}):
            self.assertEqual(servir(Path(tempfile.mkdtemp())), 2)


if __name__ == "__main__":
    unittest.main()
