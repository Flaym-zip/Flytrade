# Installer alpha 08 sans perdre ses donnees

**Archive corrigee : correctif installation 1.**
Pour l'erreur SQLite `readonly database` pendant la sauvegarde, lire d'abord
`CORRECTIF-INSTALLATION-08.md`. Les calculs et l'interface restent alpha 08.


1. Dans l'application, arreter politique, collecte et atelier. Laisser finir
   toutes les fenetres du marche. Ne supprimer ni le volume ni le dossier actif.
2. Extraire flytrade-alpha08-correctif-installation.zip dans un AUTRE dossier que le projet actif.
3. Executer le script de cette archive en lui donnant le projet existant :

```
bash ~/Projets/_archives/Flytrade/updates/alpha08-correctif/flytrade/mettre_a_jour.sh \
  ~/Projets/Flytrade
```

Lire les chemins, le port et le volume detectes. Confirmer avec `oui`.
Le script conserve .env, sauvegarde source et volume, verifie les baselines
locales lorsqu'un SHA256SUMS est present, puis reconstruit le conteneur.

Aucun modele, portefeuille ou corpus n'est remis a zero par cette installation.
Les bases alpha06/07 continuent a etre utilisees. Alpha08 ajoute une table de
suivi des tests deja ouverts et des metadonnees de provenance des seances.
Les collectes actives enregistrees peuvent reprendre apres un redemarrage ;
l'atelier et la politique redemarrent en pause. D'ou l'arret demande avant update.

Le volume historique peut toujours s'appeler flylab-alpha_memory : c'est normal.
Ne pas executer `docker compose down -v` ni `docker volume prune`.

Verifier depuis le dossier du projet :

```
cd ~/Projets/Flytrade
sudo docker compose ps
```

Recharger la page avec Ctrl+Maj+R. Ouvrir /guide puis lire les differences entre
Reprendre, Rejouer, Continuer et Cerveau neuf. Le guide est aussi dans
`docs/DEMARRER-08.md`.

## Retour en arriere

La sauvegarde est indiquee par le script, dans
`~/Projets/_archives/Flytrade/backups/flytrade-sauvegarde-DATE-.../`.
Elle contient le code precedent et les donnees du volume a cet instant.
Arreter les activites avant de restaurer.

```
bash ~/Projets/_archives/Flytrade/updates/alpha08-correctif/flytrade/restaurer_sauvegarde.sh \
  CHEMIN_EXACT_DE_LA_SAUVEGARDE ~/Projets/Flytrade
```

Confirmer `restaurer`. La restauration archive aussi l'etat abandonne. Elle
restaure code et donnees ENSEMBLE. Les apprentissages posterieurs a la sauvegarde
ne restent pas actifs. Ne pas seulement recopier une ancienne base a cote d'un
fichier WAL recent, ni melanger les sources Kraken et Coinbase.

## Portee

Pas de connexion au portefeuille d'Euphoria. Aucun ordre reel. L'installation
conserve les hypotheses economiques et les calculs neuronaux d'alpha07.
Les captures de verification sont hors ligne, avec une copie des observations
fournies ; elles ne montrent pas une connexion actuelle au marche.
