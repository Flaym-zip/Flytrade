# Flytrade baseline 100-trades-v1

Date : 2026-09-19

## Environnement

- ETH/USD live
- grille temporelle : 5 s
- grille prix : 0,50 USD
- préavis minimal : 10 s

## Réseau

- encodeur grid-usd-0.5-v1
- 108 entrées
- 1024 KC
- inhibition APL
- compartiments rapide / intermédiaire / lent
- DAN positif / négatif
- MBON
- sorties hausse / stable / baisse

## Résultats

- 100 essais valides
- 54 succès
- 46 échecs

Décisions :
- hausse : 9
- stable : 84
- baisse : 7

## Baseline comparative

- Flytrade : 54 %
- stratégie toujours STABLE sur les mêmes fenêtres : ~56 %

## Interprétation

Le réseau a principalement appris le biais moyen en faveur
de STABLE. Ce run ne constitue pas encore une preuve que
les représentations KC/MBON discriminent correctement les
patterns de marché.

Les trois fichiers expérimentaux sont protégés par SHA-256
dans SHA256SUMS.
