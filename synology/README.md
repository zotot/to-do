# Installation sur un NAS Synology

L'assistant tourne dans **Container Manager** (DSM 7.2 ou plus). Le Planificateur de tâches de DSM
peut ensuite vérifier vos comptes chaque jour et vous envoyer un **e-mail seulement s'il y a une alerte**
(découvert prévu, hausse d'abonnement).

Aucun port n'est ouvert et rien ne sort du NAS : vos relevés restent dans votre dossier.

## 1. Prérequis

- **Container Manager** installé depuis le Centre de paquets. Il est disponible sur les modèles
  Intel/AMD et sur une partie des modèles ARM récents ; s'il n'apparaît pas dans le Centre de paquets,
  votre modèle ne le prend pas en charge.
- Pour les alertes par e-mail : **Panneau de configuration → Notification → E-mail** configuré
  (un e-mail de test doit arriver).
- Conseillé : **SSH** activé (Panneau de configuration → Terminal et SNMP), pour lancer les
  commandes à la main.

## 2. Copier l'assistant sur le NAS

1. Sur GitHub, téléchargez le code en ZIP (bouton **Code → Download ZIP** sur la branche voulue).
2. Dans **File Station**, ouvrez le dossier partagé `docker` (créé par Container Manager), envoyez le ZIP,
   puis clic droit → **Extraire**. Renommez le dossier extrait en `assistant-financier`.
3. Dans `assistant-financier`, créez le dossier `donnees`, et dedans le dossier `releves` :

   ```
   /volume1/docker/assistant-financier/
   ├── Dockerfile, docker-compose.yml, assistant_financier/, synology/ …
   └── donnees/
       ├── releves/          ← déposez ici vos relevés CSV/OFX
       ├── alertes.conf      ← votre solde (étape 5)
       └── budget.json       ← facultatif, créé par --creer-budget
   ```

Astuce : avec **Synology Drive**, synchronisez `donnees/releves` avec un dossier de votre ordinateur
pour y déposer vos relevés directement après les avoir téléchargés depuis votre banque.

## 3. Construire l'image

**Container Manager → Projet → Créer** :

- Nom du projet : `assistant-financier`
- Chemin : `/docker/assistant-financier`
- Source : utiliser le `docker-compose.yml` existant

Validez (pas besoin de portail web). Container Manager construit l'image puis lance l'assistant une fois.
Le conteneur s'arrête aussitôt, **c'est normal** : c'est un outil qui affiche un rapport puis se termine.
Le rapport est visible dans **Conteneur → assistant-financier → Journal**.

En SSH, l'équivalent est :

```bash
cd /volume1/docker/assistant-financier
sudo docker compose build
```

## 4. Utiliser l'assistant (SSH)

```bash
cd /volume1/docker/assistant-financier
sudo docker compose run --rm --user "$(id -u):$(id -g)" assistant --solde 1520.40
sudo docker compose run --rm --user "$(id -u):$(id -g)" assistant --creer-budget
sudo docker compose run --rm --user "$(id -u):$(id -g)" assistant --budget
```

`--user "$(id -u):$(id -g)"` fait écrire `budget.json` et les exports avec votre compte DSM
(sinon : erreur « Permission denied »). Toutes les options du [README principal](../README.md) fonctionnent.
Si `docker compose` n'est pas reconnu, essayez `docker-compose`.

## 5. Alertes automatiques par e-mail

1. Copiez `synology/alertes.conf.exemple` dans `donnees/alertes.conf` et indiquez votre solde et votre
   découvert autorisé. Le fichier se modifie avec le paquet **Éditeur de texte**.

   ```
   SOLDE=1520,40
   DECOUVERT_AUTORISE=300
   ```

   Le solde est celui **à la date de la dernière opération de vos relevés** : mettez-le à jour chaque fois
   que vous déposez un nouveau relevé. Sans `SOLDE`, seules les hausses d'abonnements sont surveillées.

2. **Panneau de configuration → Planificateur de tâches → Créer → Tâche planifiée → Script défini par
   l'utilisateur** :

   - **Général** : nom `Assistant financier`, utilisateur **root**.
   - **Programmer** : tous les jours, par exemple à 8 h.
   - **Paramètres de tâche** :
     - cochez **Envoyer les détails d'exécution par e-mail** et indiquez votre adresse ;
     - cochez **N'envoyer les détails d'exécution que si le script se termine de manière anormale** ;
     - script :

       ```bash
       sh /volume1/docker/assistant-financier/synology/alertes.sh
       ```

3. Testez : sélectionnez la tâche → **Exécuter**. S'il y a une alerte, vous recevez l'e-mail.

Ce que fait le script :

- s'il y a un **découvert prévu** ou une **hausse d'abonnement récente**, il se termine en erreur et
  DSM vous envoie le rapport ;
- il ne renvoie **pas deux fois la même alerte** : l'e-mail suivant n'arrive que si la situation change
  (nouveau relevé, nouveau solde, nouvelle alerte) ;
- s'il manque quelque chose (dossier `releves` vide, fichier illisible…), vous recevez aussi l'e-mail
  avec l'erreur ;
- s'il n'y a rien à signaler, aucun e-mail.

## 6. Mettre à jour l'assistant

Remplacez les fichiers du dossier `assistant-financier` par la nouvelle version **sans toucher à
`donnees/`**, puis reconstruisez l'image : **Container Manager → Projet → assistant-financier →
Action → Construire**, ou en SSH `sudo docker compose build`.
