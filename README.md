# Assistant financier

Analyse vos relevés de compte, repère les opérations qui reviennent (abonnements,
loyer, crédit, factures, salaire…) à partir de leur **date** et de leur **libellé**,
puis **anticipe les prochains prélèvements**.

Fonctionne avec **Docker** (recommandé) ou directement avec Python 3.10+, sans dépendance à installer.

## Avec Docker

1. Préparez un dossier `donnees/` à côté de `docker-compose.yml`, et mettez-y vos relevés :

   ```
   donnees/
   ├── releves/        ← vos relevés CSV/OFX
   └── budget.json     ← créé par --creer-budget (facultatif)
   ```

   ```bash
   mkdir -p donnees/releves
   ```

2. **Sous Linux uniquement**, pour que les fichiers créés (budget.json, exports) vous appartiennent :

   ```bash
   printf "UID=$(id -u)\nGID=$(id -g)\n" > .env
   ```

   (inutile avec Docker Desktop sur Mac ou Windows, ou si votre identifiant est 1000).

3. Lancez, avec les mêmes options que ci-dessous :

   ```bash
   docker compose run --rm assistant                          # récurrences et prochaines échéances
   docker compose run --rm assistant --alertes --solde 1520.40
   docker compose run --rm assistant --creer-budget
   docker compose run --rm assistant --budget
   docker compose run --rm assistant --solde 1520.40 --export previsions.csv   # écrit dans donnees/
   ```

   La première commande construit l'image (environ 175 Mo). Après une mise à jour du code :
   `docker compose build`.

Sur un **NAS Synology**, suivez le guide [synology/README.md](synology/README.md) : installation avec
Container Manager et alertes automatiques par e-mail via le Planificateur de tâches.

Pour essayer sans vos données, avec les relevés d'exemple intégrés à l'image :

```bash
docker compose build
docker run --rm assistant-financier /app/exemples --solde 1204.33
```

Vos relevés et votre `budget.json` restent sur votre machine : ils sont montés dans le conteneur
au lancement, jamais copiés dans l'image (voir `.dockerignore`), et `donnees/` est exclu de git.

## Utilisation

1. Exportez vos relevés depuis l'espace client de votre banque au format **CSV** ou **OFX**
   (souvent « Télécharger mes opérations »). Plus il y a de mois, meilleure est la détection :
   3 mois minimum, 12 pour repérer les échéances annuelles.
2. Mettez-les dans un dossier `releves/` (lu par défaut, sinon indiquez le chemin). Les relevés qui se chevauchent ne posent pas de problème :
   les doublons sont supprimés.
3. Lancez :

```bash
python3 -m assistant_financier            # lit le dossier releves/
python3 -m assistant_financier mes_releves/2026/
```

Options utiles (identiques avec Docker) :

| Option | Effet |
|---|---|
| `--jours 60` | prévoit sur 60 jours (31 par défaut) |
| `--solde 1520.40` | votre solde à la date du dernier relevé : projette le solde et alerte en cas de découvert prévu |
| `--decouvert-autorise 300` | votre découvert autorisé, pour signaler un risque de rejet de prélèvement |
| `--sans-depenses-courantes` | projette le solde avec les seuls prélèvements récurrents |
| `--du 01/11/2026` | début de la prévision (par défaut : lendemain du dernier relevé) |
| `--debits` | n'affiche que les sorties d'argent |
| `--export previsions.csv` | exporte les échéances prévues (s'ouvre dans Excel) |
| `--budget` | affiche les dépenses du mois par catégorie, comparées à votre budget |
| `--mois 05/2026` | mois analysé par `--budget` (par défaut : mois du dernier relevé) |
| `--creer-budget` | crée `budget.json` à partir de vos dépenses moyennes |
| `--alertes` | n'affiche que les alertes (hausses d'abonnements, découvert prévu) ; code de sortie 3 s'il y a une alerte à traiter |
| `--config mon_budget.json` | utilise un autre fichier de budget que `budget.json` |

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

## Alertes : hausses d'abonnements

Quand un prélèvement à montant fixe change de tarif, l'assistant le signale en tête du rapport
(et sous le budget) :

```
=== ⚠ Alertes : hausses d'abonnements ===
  FREE MOBILE                          19,99 € → 24,99 €     (+25,0 %) depuis le 08/06/2026, soit +60,00 € par an NOUVEAU
  NETFLIX COM                          13,49 € → 15,99 €     (+18,5 %) depuis le 12/05/2026, soit +30,00 € par an NOUVEAU
  Surcoût total : +90,00 € par an
```

- **NOUVEAU** marque une hausse survenue dans les 2 derniers mois des relevés.
- Les prévisions et le budget utilisent directement le nouveau tarif.
- Ne déclenchent pas d'alerte : les écarts de moins de 1 % (conversion de devise), les factures
  à montant variable (électricité…), les baisses, les hausses de revenus et les abonnements résiliés.
- Pour vérifier seulement les alertes : `python3 -m assistant_financier releves/ --alertes`.

## Alerte : découvert prévu

Indiquez votre solde (`--solde`, à la date du dernier relevé) : l'assistant simule votre compte jour
par jour et vous prévient si un prélèvement va le faire passer à découvert.

```
$ python3 -m assistant_financier releves/ --alertes --solde 500 --decouvert-autorise 400

=== ⚠ Alerte : découvert prévu ===
  Le dim. 05/07/2026, LOYER IMMO GESTION (-780,00 €) ferait passer le compte à -337,32 €.
  Point le plus bas : -1 214,76 € le 01/08/2026.
  ⚠ Au-delà de votre découvert autorisé (400,00 €) dès le 08/07/2026 : risque de rejet de prélèvement et de frais.
  → Versez au moins 1 214,76 € sur le compte avant le 05/07/2026.
  (projection : solde de +500,00 € au 01/07/2026, prélèvements récurrents prévus et dépenses courantes estimées à 14,33 €/jour)
```

- La simulation compte les **prélèvements et rentrées récurrents** prévus, plus vos **dépenses
  courantes** (courses, essence, sorties…), estimées d'après la médiane des mois précédents,
  pour qu'un gros achat ponctuel ne fausse pas l'estimation. `--sans-depenses-courantes` les retire.
- Le même jour, les débits passent avant les crédits : si le loyer et le salaire tombent le 1er,
  l'alerte vous prévient quand même (votre banque peut traiter le loyer en premier).
- Sans découvert prévu, `--alertes` indique le point le plus bas du solde sur la période (`--jours`).
- Le rapport habituel affiche aussi le solde prévu après chaque échéance.

## Budget mensuel par catégorie

Chaque opération est classée automatiquement (Logement, Alimentation, Transport, Abonnements & loisirs,
Énergie & eau, Télécom, Santé, Assurances, Crédits, Impôts, Shopping, Restaurants, Épargne…)
d'après les mots-clés de son libellé.

1. Créez votre budget à partir de vos habitudes :

   ```bash
   python3 -m assistant_financier releves/ --creer-budget
   ```

   Cela écrit `budget.json` avec, pour chaque catégorie, votre moyenne mensuelle arrondie à la dizaine.
2. Modifiez `budget.json` pour fixer vos objectifs, et ajoutez vos propres mots-clés si besoin
   (les vôtres passent avant ceux par défaut) :

   ```json
   {
     "budget": { "Alimentation": 350, "Restaurants & sorties": 100, "Animaux": 40 },
     "categories": {
       "Animaux": ["VETERINAIRE", "ZOOPLUS"],
       "Restaurants & sorties": ["LA CAVE DU COIN"]
     }
   }
   ```
3. Suivez le mois :

   ```bash
   python3 -m assistant_financier releves/ --budget
   ```

```
=== Budget de juin 2026 (relevés jusqu'au 23/06, fin de mois estimée avec les prélèvements prévus) ===

  Catégorie                     Dépensé  Fin de mois      Moyenne       Budget        Reste
  Logement                     780,00 €     780,00 €     780,00 €     780,00 €      +0,00 €  ████████████ 100%
  Alimentation                 246,57 €     246,57 €     415,11 €     350,00 €    +103,43 €  ████████░░░░  70%
  ...
  Total dépenses (hors épargne) : 1 544,76 €, 1 544,76 € prévus en fin de mois / budget 1 650,00 €
  Revenus du mois             : 2 450,00 €
  Reste à vivre / épargne     : +905,24 €

  Opérations non classées (ajoutez des mots-clés dans budget.json, rubrique "categories") :
    12/06  CB LA CAVE DU COIN 12/06                               -23,50 €
```

- Si le mois n'est pas terminé, la colonne **Fin de mois** ajoute les prélèvements récurrents encore
  attendus, pour vous prévenir *avant* un dépassement (« ⚠ dépassement prévu »).
- La **moyenne** est calculée sur les mois entièrement couverts par vos relevés.
- Les **remboursements** (CPAM, retour d'un achat) viennent en déduction de leur catégorie ;
  les salaires et virements reçus comptent comme revenus.
- L'**épargne** (livret, assurance vie…) est affichée mais exclue du total des dépenses.

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
# ou dans le conteneur :
docker run --rm -v "$PWD/tests:/app/tests:ro" -w /app --entrypoint python assistant-financier -m unittest discover -s tests
```

## Pistes pour la suite

- Lecture des relevés **PDF** (dépend de la mise en page de chaque banque).
- Autres alertes : prélèvement inconnu, facture variable anormalement élevée.
- Lire le solde directement dans les relevés OFX (balise `LEDGERBAL`).
- Interface web ou tableau de bord.
