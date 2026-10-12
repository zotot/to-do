#!/bin/sh
# Vérification automatique pour le Planificateur de tâches de DSM (Synology).
#
#   sh alertes.sh [DOSSIER_DONNEES]
#
# DOSSIER_DONNEES (par défaut : donnees/ à côté du code) contient releves/ et, facultatifs,
# alertes.conf et budget.json. Ce peut être un dossier Synology Drive, par exemple
# /volume1/homes/<utilisateur>/Drive/Finances : vous y déposez vos relevés depuis n'importe
# quel appareil, et vous y lisez le rapport.
#
# À chaque passage :
#   - rapport.txt est (ré)écrit dans DOSSIER_DONNEES : alertes, échéances à venir, budget du mois ;
#   - le script se termine en erreur uniquement s'il y a une alerte NOUVELLE (découvert prévu,
#     hausse d'abonnement récente). Avec l'option « n'envoyer les détails d'exécution que si le
#     script se termine de manière anormale », DSM vous envoie alors les alertes par e-mail.
#     Une alerte identique à la précédente n'est pas renvoyée.
#
# Réglages facultatifs dans DOSSIER_DONNEES/alertes.conf :
#   SOLDE=1520.40               solde du compte à la date du dernier relevé déposé
#   DECOUVERT_AUTORISE=300

PATH=/usr/local/bin:/usr/bin:/bin:$PATH
DOSSIER="$(cd "$(dirname "$0")/.." && pwd)"
DONNEES="${1:-${DONNEES:-$DOSSIER/donnees}}"
IMAGE=assistant-financier
ETAT="$DONNEES/.derniere_alerte"
RAPPORT_FICHIER="$DONNEES/rapport.txt"

if [ ! -d "$DONNEES/releves" ]; then
    echo "Dossier introuvable : $DONNEES/releves (déposez-y vos relevés CSV/OFX)."
    exit 1
fi

reglage() {
    [ -f "$DONNEES/alertes.conf" ] || return 0
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$DONNEES/alertes.conf" \
        | tail -n 1 | tr -d ' \r€' | tr ',' '.'
}

set --
SOLDE=$(reglage SOLDE)
DECOUVERT=$(reglage DECOUVERT_AUTORISE)
[ -n "$SOLDE" ] && set -- "$@" "--solde=$SOLDE"
[ -n "$DECOUVERT" ] && set -- "$@" "--decouvert-autorise=$DECOUVERT"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker build -q -t "$IMAGE" "$DOSSIER" >/dev/null || { echo "Échec de la construction de l'image."; exit 1; }
fi

# Le conteneur lit et écrit avec le propriétaire du dossier de données (et non root).
PROPRIETAIRE=$(stat -c '%u:%g' "$DONNEES")
assistant() {
    docker run --rm --user "$PROPRIETAIRE" -v "$DONNEES:/donnees" "$IMAGE" "$@" 2>&1
}

# 0. Premiers passages : fichiers de réglage à compléter depuis Synology Drive.
if [ ! -f "$DONNEES/alertes.conf" ]; then
    {
        echo "# Réglages des alertes. Retirez le # devant une ligne pour l'activer."
        echo "# Solde du compte à la date de la dernière opération du dernier relevé déposé :"
        echo "# SOLDE=1520,40"
        echo "# Montant de votre découvert autorisé (0 si aucun) :"
        echo "# DECOUVERT_AUTORISE=300"
    } > "$DONNEES/alertes.conf"
    chown "$PROPRIETAIRE" "$DONNEES/alertes.conf" 2>/dev/null
fi
[ -f "$DONNEES/budget.json" ] || assistant --creer-budget >/dev/null

# 1. Rapport complet, lisible depuis Synology Drive.
TEMPORAIRE="$DONNEES/.rapport.tmp"
{
    echo "Rapport de l'assistant financier, généré le $(date '+%d/%m/%Y à %H:%M')"
    if [ -n "$SOLDE" ]; then
        echo "Solde pris en compte : $SOLDE € (à mettre à jour dans alertes.conf à chaque nouveau relevé)"
    else
        echo "Pas de solde dans alertes.conf : le risque de découvert n'est pas vérifié."
    fi
    echo
    assistant "$@"
    echo
    # Budget sans l'en-tête (fichiers ignorés, « N opérations lues »), déjà affiché au-dessus.
    assistant --budget "$@" | awk 'suite { if (!vide && $0 == "") { vide = 1; next } print } /opérations lues du/ { suite = 1 }'
} > "$TEMPORAIRE"
chown "$PROPRIETAIRE" "$TEMPORAIRE" 2>/dev/null
mv -f "$TEMPORAIRE" "$RAPPORT_FICHIER"

# 2. Alertes : code de sortie 3 s'il y a quelque chose à signaler.
RAPPORT=$(assistant --alertes "$@")
CODE=$?

case $CODE in
    0)
        rm -f "$ETAT"
        echo "$RAPPORT"
        exit 0
        ;;
    3)
        EMPREINTE=$(printf '%s' "$RAPPORT" | md5sum | cut -d' ' -f1)
        if [ -f "$ETAT" ] && [ "$(cat "$ETAT")" = "$EMPREINTE" ]; then
            echo "Alerte déjà signalée, rien de nouveau."
            exit 0
        fi
        echo "$EMPREINTE" > "$ETAT"
        chown "$PROPRIETAIRE" "$ETAT" 2>/dev/null
        echo "$RAPPORT"
        echo
        echo "Rapport complet : rapport.txt dans votre dossier ($DONNEES)."
        exit 3
        ;;
    *)
        echo "$RAPPORT"
        exit "$CODE"
        ;;
esac
