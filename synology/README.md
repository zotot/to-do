# Installation sur un NAS Synology

Le principe : vous déposez vos relevés dans un dossier **Synology Drive** depuis n'importe quel
appareil (téléphone, navigateur, ordinateur). Chaque jour, le NAS les analyse :

- il écrit un **`rapport.txt`** dans ce même dossier Drive : alertes, prochaines échéances, budget du mois ;
- il vous envoie un **e-mail seulement s'il y a une nouvelle alerte** (découvert prévu, hausse d'abonnement).

Vous n'avez besoin d'aucun ordinateur allumé, et rien ne sort du NAS : pas de port ouvert, pas de service en ligne.

```
Synology Drive : Finances/
├── releves/          ← vous déposez ici vos relevés CSV/OFX (sous-dossiers par année acceptés)
├── rapport.txt       ← écrit par le NAS à chaque passage : à lire depuis Drive
├── alertes.conf      ← votre solde et votre découvert autorisé (créé au premier passage)
└── budget.json       ← votre budget par catégorie (créé au premier passage)
```

## 1. Prérequis

- **Container Manager** et **Synology Drive Server** installés depuis le Centre de paquets.
  Container Manager est disponible sur les modèles Intel/AMD et sur une partie des modèles ARM
  récents ; s'il n'apparaît pas dans le Centre de paquets, votre modèle ne le prend pas en charge.
- **Notifications par e-mail** configurées : Panneau de configuration → Notification → E-mail
  (envoyez-vous un e-mail de test).

## 2. Le dossier Drive

1. Dans Synology Drive (application ou `https://<votre-nas>/drive`), créez un dossier **`Finances`**
   dans *Mon Drive*, et dedans un dossier **`releves`**.
2. Déposez-y vos relevés CSV ou OFX, téléchargés depuis l'espace client de votre banque.
   Vous pouvez les ranger par année (`releves/2025/`, `releves/2026/`) ; les relevés qui se chevauchent
   ne sont pas comptés deux fois.
3. Notez le **chemin complet** du dossier sur le NAS. Pour *Mon Drive*, c'est en général
   `/volume1/homes/<votre-utilisateur>/Drive/Finances`. Pour le vérifier : File Station → `homes` →
   votre utilisateur → `Drive` → clic droit sur `Finances` → Propriétés (« Emplacement » ; ajoutez
   `/volume1` devant, ou le numéro de votre volume).

Ce que l'assistant ignore sans bloquer : les PDF ou fichiers Excel déposés par erreur et les fichiers
qui ne sont pas des relevés. Il les signale en tête du rapport. Les dossiers techniques de DSM
(`@eaDir`, `#recycle`) et les fichiers temporaires sont ignorés sans message.

## 3. Installer l'assistant

1. Sur GitHub, téléchargez le code en ZIP (bouton **Code → Download ZIP** sur la branche voulue).
2. Dans **File Station**, ouvrez le dossier partagé `docker` (créé par Container Manager), envoyez le ZIP,
   puis clic droit → **Extraire**. Renommez le dossier extrait en **`assistant-financier`**.
3. **Container Manager → Projet → Créer** :
   - Nom du projet : `assistant-financier`
   - Chemin : `/docker/assistant-financier`
   - Source : utiliser le `docker-compose.yml` existant

   Validez (pas besoin de portail web). Container Manager construit l'image. Le conteneur s'arrête
   aussitôt : **c'est normal**, l'assistant affiche un rapport puis se termine.

Le code (`/docker/assistant-financier`) et vos données (le dossier Drive) sont séparés : une mise à jour
de l'assistant ne touche jamais à vos relevés.

## 4. Le passage quotidien

**Panneau de configuration → Planificateur de tâches → Créer → Tâche planifiée → Script défini par
l'utilisateur** :

- **Général** : nom `Assistant financier`, utilisateur **root**.
- **Programmer** : tous les jours, par exemple à 7 h.
- **Paramètres de tâche** :
  - cochez **Envoyer les détails d'exécution par e-mail** et indiquez votre adresse ;
  - cochez **N'envoyer les détails d'exécution que si le script se termine de manière anormale** ;
  - script (avec **votre** chemin de l'étape 2) :

    ```bash
    sh /volume1/docker/assistant-financier/synology/alertes.sh /volume1/homes/<votre-utilisateur>/Drive/Finances
    ```

Lancez-la une première fois : sélectionnez la tâche → **Exécuter**. Après quelques instants,
`rapport.txt`, `alertes.conf` et `budget.json` apparaissent dans votre dossier Drive. Les fichiers sont
créés au nom du propriétaire du dossier : vous pouvez les ouvrir et les modifier normalement.

À chaque passage, le script :

- réécrit `rapport.txt` (alertes, opérations récurrentes, échéances du mois à venir, budget du mois) ;
- vous envoie un e-mail **s'il y a une nouvelle alerte** : découvert prévu, ou hausse d'abonnement
  récente. La même alerte n'est pas renvoyée tous les jours : l'e-mail suivant n'arrive que si la
  situation change (nouveau relevé, nouveau solde, nouvelle alerte) ;
- vous envoie aussi un e-mail en cas de problème (dossier `releves` introuvable…).

## 5. Vos réglages, depuis Drive

**`alertes.conf`** : pour activer l'alerte de découvert, retirez le `#` devant les lignes et indiquez
vos montants :

```
SOLDE=1520,40
DECOUVERT_AUTORISE=300
```

Le solde est celui **à la date de la dernière opération de vos relevés** (il figure en bas du relevé).
Mettez-le à jour chaque fois que vous déposez un nouveau relevé. Sans solde, seules les hausses
d'abonnements sont surveillées.

**`budget.json`** : créé d'après vos dépenses moyennes. Ajustez les montants à vos objectifs et
ajoutez vos propres mots-clés de catégories (voir le [README principal](../README.md#budget-mensuel-par-catégorie)).
Supprimez-le pour qu'il soit recréé d'après vos dernières moyennes.

Ces deux fichiers sont du texte : modifiez-les depuis un ordinateur synchronisé avec Drive, ou depuis
n'importe quel navigateur avec le paquet **Éditeur de texte** de DSM.

## 6. Mettre à jour l'assistant

Remplacez le contenu de `/docker/assistant-financier` par la nouvelle version, puis reconstruisez
l'image : **Container Manager → Projet → assistant-financier → Action → Construire**.
Votre dossier Drive n'est pas concerné.

## Pour aller plus loin : commandes en SSH

Avec SSH activé (Panneau de configuration → Terminal et SNMP), pour lancer l'assistant à la main
avec toutes ses options :

```bash
cd /volume1/docker/assistant-financier
printf "DONNEES=/volume1/homes/$USER/Drive/Finances\nUID=$(id -u)\nGID=$(id -g)\n" > .env
sudo docker compose run --rm assistant --budget --mois 05/2026
sudo docker compose run --rm assistant --solde 1520.40 --jours 60
```

Si `docker compose` n'est pas reconnu, essayez `docker-compose`.
