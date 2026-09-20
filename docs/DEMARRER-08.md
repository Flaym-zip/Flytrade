# Flytrade alpha 08 - le parcours, sans jargon

Flytrade contient deux copies du cerveau : l'atelier apprend ; le marche utilise
une copie gelee. Ce sont des reseaux numeriques synthetiques, pas des neurones
MaleCNS importes. Aucun ordre reel ni portefeuille de cryptomonnaies.

## Je veux apprendre pour la premiere fois

1. Ouvrir Collecte & apprentissage.
2. Enregistrer des observations, puis Copier la collecte dans le corpus ; ou
   importer un export de fenetres JSONL de la source active.
3. Choisir le parcours et Verifier mes donnees sans rien modifier.
4. Preparer une nouvelle experience (cerveau neuf). Lire la confirmation.
5. Demarrer / reprendre la seance. Preparer seul ne lance rien.
6. Attendre la fin des exercices et de la calibration.
7. Ouvrir le test gele, puis le demarrer. Exporter rapport et poids.
8. Arreter collecte et politique, attendre zero fenetre ouverte.
9. Utiliser ce cerveau sur le marche. Une copie des poids et de la calibration
   est transferee ; l'ancien etat est archive ; portefeuille a 20 EUR fictifs.
10. La politique reste arretee. Observer son motif d'attente avant de l'activer.

La calibration de moins de 60 fenetres bloque les mises. Meme au-dela, il faut
assez d'exemples dans le groupe de scores utilise. Ce ne sont pas des garanties
statistiques de profit.

## Je veux reentrainer

- **Reprendre ma seance** : meme cerveau, meme position ; prochain exemple.
- **Rejouer depuis zero** : meme plan sauvegarde, meme graine et memes reglages,
  mais un cerveau neuf. Les changements non appliques du formulaire sont ignores.
- **Continuer l'apprentissage** : seance terminee seulement. Garde les poids,
  ajoute un passage sur son TRAIN uniquement, puis refait sa calibration. Aucun
  nouvel exemple de test ou du corpus n'est ajoute implicitement.
- **Nouvelles donnees ou nouveaux reglages** : les ajouter au corpus puis Preparer
  une nouvelle experience. Ce parcours repart de zero dans cette version.

Les nouveaux passages ne rendent pas les anciens exemples independants.
Un test deja ouvert reste signale, meme apres relecture ou remise a zero.

## Je veux un cerveau initial

Dans Collecte & apprentissage, descendre a Repartir avec un cerveau initial.

**Cerveau neuf dans l'atelier** remet a zero son apprentissage, sa calibration et
sa seance apres archivage. Le corpus, la collecte, le live et le portefeuille
restent intacts. La configuration du formulaire definit la construction.

**Cerveau neuf sur le marche** remplace seulement la copie live, avec ses propres
parametres de construction actuels. Le capital, les observations et l'atelier
restent intacts. Arreter collecte et politique, puis laisser finir les fenetres.
Cette copie initiale n'est pas calibree et ne peut pas miser.

**Remettre uniquement le portefeuille a 20 EUR** ne change aucun poids neuronal.
Il archive l'etat financier puis recommence les compteurs du portefeuille.

Initial = zero correction apprise, pas tous les nombres a zero. Les poids de
prevision commencent a 0.5 ; les poids economiques auxiliaires a zero. L'architecture
et les connexions tirees avec la graine restent presentes.

## Ou sont mes anciennes seances ?

L'atelier propose les 12 archives les plus recentes au telechargement. L'archive
contient poids, plan, calibration et compteurs. Ce n'est pas un fichier de donnees
JSONL. La restauration d'un checkpoint quelconque n'est pas automatisee.
Les etats live remplaces sont egalement conserves dans la base du marche.

Pour revenir a une ancienne installation complete, utiliser la sauvegarde code
ET donnees du script de mise a jour. Ne jamais supprimer le volume Docker pour
recreer un cerveau vierge.

## Tutoriel et Wiki

- /guide : explications, etat actuel et visite des vrais boutons, sans action
  automatique. La progression de la visite n'est pas celle du cerveau.
- /wiki : huit chapitres Debuter, puis la reference scientifique et technique.
- Au debut de chaque chapitre technique : explication simple avant les details.

Le tutoriel, la recherche et les explications sont locaux. Seuls le flux de
marche et les liens externes des sources necessitent Internet.
