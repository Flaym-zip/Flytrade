# Modele alpha06 : fonctions et limites

## Pas un cerveau MaleCNS importe

2048 KC synthetiques (ou 1024 comme controle), projection clairsemee fixe,
252 PN numeriques, cinq modules de lecture derives du prototype precedent.
Les noms gamma/PPL1/PAM sont des inspirations, pas des identifiants de vraies cellules.
Pas de spikes ni calibration electrophysiologique. Le reseau reste un algorithme CPU.

## Representation

16 caracteristiques de prix passe + 8 canaux de liquidite + 4 canaux candidat.
Neuf fonctions de reponse par caracteristique. Chaque KC echantillonne six PN.
Avec liquidite : quatre attributs de prix, un de liquidite, un du candidat.
Sans liquidite : cinq attributs de prix, un du candidat. Le nombre de KC est
independant du nombre de features; doubler les KC ne rajoute pas d'information.
APL = seuil par quantile, pas un vrai neurone reconstruit. A 5%, 103 KC environ
sur 2048 restent actives pour un candidat. Les ensembles peuvent varier entre
contextes et entre candidats. Le compteur cumule compte des cellules distinctes.
La multiplication visuelle par six de la jauge APL n'existe plus.

## Deux apprentissages differents

1. Prevision de touche : deltas locaux binaires sur les cinq lectures MBON.
   All : moyenne des mises a jour des trois actions, calculees a partir du meme
   etat avant resultat. Chosen : seulement celle de l'action selectionnee.
2. Utilite economique auxiliaire : nouvelle lecture sur les memes KC.
   Cible tanh(net_theorique_par_euro / 2). Mise a jour locale bornee -1..1.
   Les gains de differentes amplitudes ne produisent plus exactement la meme
   cible. Le poids de cette mise a jour vaut 0.4 fois le taux principal.
   Seules des cotations differrees deja observees (alpha06) sont utilisees.
   Les quotes alpha05 immediates ne sont pas converties en executions fictives.

La tete d'utilite ne participe PAS a la calibration de probabilite et ne modifie
pas ses poids. Son score n'est ni des euros ni une probabilite. Elle est un
instrument d'experience a evaluer par ablation, pas une amelioration demontree.
La politique monetaire utilise les probabilites de touche calibrees et les
multiplicateurs, pas directement ce score auxiliaire.

## Decision de taille, regle explicite

Tailles candidates : .1, 1, 5, 10, 20. Budget de perte B*max_fraction (5% par defaut),
limite absolue max_stake, une seule reserve en cours. Refus si perte depuis le pic
atteint drawdown_limit. Aucun financement ni levier.

Pour chaque action et taille, avec p_bas = borne prudente de calibration et M_bas
= multiplicateur effectif degrade du slippage autorise, le moteur compare :

    U = p_bas * log(1 + s*(M_bas-1-c)/B)
        + (1-p_bas) * log(1 - s*(1+c)/B)

Il refuse U<=0, une esperance prudente insuffisante et s*(1+c)>=B. Les bornes de
calibration ne corrigent pas la dependance temporelle ou un changement de regime.
Ce n'est ni une garantie statistique ni une allocation optimale reelle.

## Lecture live du renforcement

A chaque reglement execute :

    u = tanh(net_EUR / max(.1, .05*capital_avant))
    DAN_gain = max(u,0), DAN_perte = max(-u,0)

Cette visualisation graduelle n'implique PAS que les poids live soient modifies.
Ils restent geles pour conserver la compatibilite de leur calibration. Leur
apprentissage et le transfert se font explicitement dans l'atelier.

## Economie

Portefeuille EUR fictif et sous-jacent ETH-USD. Pas de conversion FX simulee,
les paiements multiplient une mise fictive dans sa propre unite.
Reglement uniquement sur quote_lock apres delai, bornes du contrat figees au clic.
L'emetteur Brownien est une hypothese; le cours observe est discret par transactions.
Le fait qu'un modele apprenne a profiter d'une faiblesse de l'emetteur n'etablit
pas une competence financiere externe. La calibration des quotes reelles reste absente.

## Sources primaires d'inspiration, pas validation du code

https://elifesciences.org/articles/62576
https://www.nature.com/articles/nn.3660
https://www.nature.com/articles/s41467-021-21388-w

Ces sources ne fournissent pas nos nombres de neurones, notre fonction d'utilite,
nos parametres ni une relation naturelle entre MBON et trading.
