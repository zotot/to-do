# Assistant financier

Analyse vos relevés de compte, repère les opérations qui reviennent (abonnements,
loyer, crédit, factures, salaire…) à partir de leur **date** et de leur **libellé**,
puis **anticipe les prochains prélèvements**.

Python 3.10 ou plus, sans aucune dépendance à installer.

## Utilisation

1. Exportez vos relevés depuis l'espace client de votre banque au format **CSV** ou **OFX**
   (souvent « Télécharger mes opérations »). Plus il y a de mois, meilleure est la détection :
   3 mois minimum, 12 pour repérer les échéances annuelles.
2. Mettez-les dans un dossier, par exemple `releves/`. Les relevés qui se chevauchent ne posent pas de problème :
   les doublons sont supprimés.
3. Lancez :

```bash
python3 -m assistant_financier releves/
```

Options utiles :

| Option | Effet |
|---|---|
| `--jours 60` | prévoit sur 60 jours (31 par défaut) |
| `--solde 1520.40` | projette votre solde après chaque échéance et signale un découvert |
| `--du 01/11/2026` | début de la prévision (par défaut : lendemain du dernier relevé) |
| `--debits` | n'affiche que les sorties d'argent |
| `--export previsions.csv` | exporte les échéances prévues (s'ouvre dans Excel) |

Exemple avec les relevés fournis dans `exemples/` :

```
$ python3 -m assistant_financier exemples/ --solde 1204.33

=== Opérations récurrentes détectées ===
  LOYER IMMO GESTION                    -780,00 €            mensuelle     vers le 5      6 fois, confiance 100%
  ECHEANCE PRET                         -312,45 €            mensuelle     vers le 10     6 fois, confiance 100%
  EDF CLIENTS PARTICULIERS               -72,90 € (variable) mensuelle     vers le 15     6 fois, confiance 90%
  ...
=== Récurrences qui semblent arrêtées (résiliées ou terminées) ===
  SALLE SPORT FITPARK                    -29,90 €   mensuelle     dernière fois le 03/02/2026

=== Échéances prévues du 24/06/2026 au 24/07/2026 ===
  mer. 01/07/2026  SOCIETE EXEMPLE                     +2 450,00 €   solde prévu : +3 654,33 €
  dim. 05/07/2026  LOYER IMMO GESTION                    -780,00 €   solde prévu : +2 874,33 €
  ...
```

## Comment ça marche

- **Import** (`import_releves.py`) : reconnaît automatiquement le séparateur (`;` `,` tabulation),
  l'encodage (UTF-8 ou Windows), les montants à la française (`-1 234,56 €`), une colonne
  montant unique ou des colonnes débit/crédit séparées, et ignore les lignes d'en-tête que
  certaines banques ajoutent (n° de compte, solde…).
- **Libellés** (`libelles.py`) : un même prélèvement change de libellé chaque mois
  (`PRLV SEPA FREE MOBILE FM-1234567 ECH/080126…`). On retire les préfixes bancaires,
  les dates, les références et les numéros pour obtenir une clé stable : `FREE MOBILE`.
- **Récurrences** (`analyse.py`) : pour chaque clé, on mesure l'écart entre les dates et on
  reconnaît une fréquence (hebdomadaire, mensuelle, trimestrielle, annuelle…). Un mois manquant
  est toléré. Deux abonnements chez le même marchand (montants différents) sont séparés.
  Les achats courants (supermarché…) sont écartés car irréguliers.
- **Prévision** : les échéances mensuelles sont placées au jour habituel du mois ; si un
  prélèvement n'a jamais eu lieu le week-end, il est reporté au lundi. Pour un montant variable
  (électricité…), on prend la moyenne des 3 dernières échéances.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Pistes pour la suite

- Lecture des relevés **PDF** (dépend de la mise en page de chaque banque).
- Catégorisation des dépenses (logement, transport, abonnements…) et budget mensuel.
- Alertes : prélèvement inhabituel, hausse d'un abonnement, découvert prévu.
- Interface web ou tableau de bord.
