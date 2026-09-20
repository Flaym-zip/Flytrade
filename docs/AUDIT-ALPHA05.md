# Audit des deux exports fournis

Les calculs complets, les empreintes d'entree et les limites figurent dans
`audit-alpha05.json` a la racine de l'archive.

199 lignes terminees dans l'export, 183 pour la generation courante. Le checkpoint
contient deux fenetres supplementaires encore ouvertes : ne pas les compter comme
resultats connus. La generation courante a 18 mises terminees (4 succes, 14 pertes),
pour +161.63473846153846 unites de l'ancien simulateur, sur une base initiale de 100.
Ce n'est pas le nouveau portefeuille de 20 EUR.

Les 18 cotations financees proviennent toutes du mode synthetique. Le seul gain
au multiplicateur total x100 vaut +98 apres decote de 1% et remboursement de mise
compris dans le versement. Deux gains a x35.5846 rapportent chacun +34.2288, un autre
rapporte +9.1772. Les pertes valent -1 chacune. Les additions et les touches ont
ete recalculees sans incoherence trouvee.

L'emetteur alpha05 pouvait amplifier les multiplicateurs jusqu'a x100 sur une
volatilite estimee dans une tres courte fenetre. La politique utilisait une
calibration de seulement 40 exemples. Le modele deploye n'utilisait pas les
features de liquidite, meme si les observations en contiennent.

Il manque les offres reellement executees apres delai : on ne peut pas calculer
ce qu'aurait gagne cette politique avec un verrouillage deux secondes plus tard.
Les plafonnements de gain de l'audit sont des sensibilites comptables a actions
fixes, pas une reexecution ni une estimation de gains reels.

Alpha06 journalise les deux offres et le delai; cela rend les prochains audits
possibles. Ses cotations restent toutefois synthetiques, non celles d'Euphoria.
