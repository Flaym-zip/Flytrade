# Correctif installation 1 - 2026-09-20

Sauvegarde avec proprietaire correct, verification SQLite sur copie temporaire
et redemarrage conditionnel de l'ancien service avant tout remplacement de code.
Aucun changement de app/. Voir CORRECTIF-INSTALLATION-08.md.

# Alpha 08 - clarte et cycle de vie

- Palette et hierarchie adaptees de la maquette fournie ; aucun script de mockup.
- Tutoriel dynamique, visite sans action, confirmations et identites des cerveaux.
- Reprendre, Rejouer frais et Continuer avec poids conserves separes.
- Reset atelier ou cerveau live distinct du portefeuille ; archives avant remplacement.
- Apercu de repartition des donnees sans modification, options avancees repliees.
- Registre durable des tests ouverts, avertissement apres reset et relecture.
- Wiki : huit chapitres debutants et resumes simples des 21 chapitres techniques.
- Les noyaux de calcul neuronal, l'economie et la source de prix ne changent pas.

# alpha 07 / 0.7.0-alpha

## Flux et audit
- Kraken Spot ETH/USD, trades WS v2 et carnet 100 niveaux CRC32.
- Correction de convention taker -> maker pour le flux execute interne.
- Snapshot trade utilise seulement pour amorcer, pas pour creer 30s de faux direct.
- GET REST Ticker toutes les 2s normalement, deadline 1,6s; Retry-After et erreurs
  de limites respectes. Aucune promesse de cadence de succes en cas de panne.
- Prix indicatif REST jamais utilise pour regler les touches ou remplacer les trades.
- Donnees, etats et calibration isoles sous sources/kraken; Coinbase conserve.
- Imports de source incompatible refuses. Aucun melange automatique de places.
- Panne, trou detectable et horloge incoherente : mise en securite comme auparavant.

## Wiki local
- 21 chapitres, environ 12 800 mots en francais.
- Equations du programme, biologie identifiee separement, 17 sources primaires.
- Registre genere de 29 parametres; constantes techniques documentees.
- Recherche locale, ancres, calculs EV/KC/Wilson, impression integrale.
- Liens contextuels depuis les regles economiques et le protocole.
- Aucune bibliotheque scientifique chargee d'un CDN dans le navigateur.

## Preservation
- Ni nouveau cerveau, ni nouvelle fonction economique : moteur alpha06 conserve.
- Sauvegardes SQLite/JSON recursives et nettoyage des WAL de profil a la restauration.
- Aucun reset du profil source existant; Kraken vierge au premier lancement seulement.
