#!/bin/sh
# Vérification automatique pour le Planificateur de tâches de DSM (Synology).
#
# Lance l'assistant en mode --alertes et se termine en erreur uniquement s'il y a une alerte
# NOUVELLE (découvert prévu, hausse d'abonnement récente) : avec l'option « n'envoyer les
# détails d'exécution que si le script se termine de manière anormale », DSM vous envoie
# alors le rapport par e-mail. Une alerte identique à la précédente n'est pas renvoyée.
#
# Réglages facultatifs dans donnees/alertes.conf :
#   SOLDE=1520.40               solde du compte à la date du dernier relevé déposé
#   DECOUVERT_AUTORISE=300

PATH=/usr/local/bin:/usr/bin:/bin:$PATH
DOSSIER="${DOSSIER:-$(cd "$(dirname "$0")/.." && pwd)}"
DONNEES="$DOSSIER/donnees"
IMAGE=assistant-financier
ETAT="$DONNEES/.derniere_alerte"

if [ ! -d "$DONNEES/releves" ]; then
    echo "Dossier introuvable : $DONNEES/releves (déposez-y vos relevés CSV/OFX)."
    exit 1
fi

reglage() {
    [ -f "$DONNEES/alertes.conf" ] || return 0
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$DONNEES/alertes.conf" \
        | tail -n 1 | tr -d ' \r€' | tr ',' '.'
}

set -- --alertes
SOLDE=$(reglage SOLDE)
DECOUVERT=$(reglage DECOUVERT_AUTORISE)
[ -n "$SOLDE" ] && set -- "$@" "--solde=$SOLDE"
[ -n "$DECOUVERT" ] && set -- "$@" "--decouvert-autorise=$DECOUVERT"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker build -q -t "$IMAGE" "$DOSSIER" >/dev/null || { echo "Échec de la construction de l'image."; exit 1; }
fi

# Le conteneur écrit avec le propriétaire du dossier donnees/ (et non root).
PROPRIETAIRE=$(stat -c '%u:%g' "$DONNEES")
RAPPORT=$(docker run --rm --user "$PROPRIETAIRE" -v "$DONNEES:/donnees" "$IMAGE" "$@" 2>&1)
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
        echo "$RAPPORT"
        exit 3
        ;;
    *)
        echo "$RAPPORT"
        exit "$CODE"
        ;;
esac
