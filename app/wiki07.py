"""Wiki parameter registry: defaults/bounds come from the executable schemas.
Descriptions are documentation, not invented biological measurements.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from .academy06 import Config06
from .economy06 import Rules06, STAKES

TRAIN = {
'seed': ('Graine', 'Sans unite', "Fixe la projection PN vers KC, les sous-ensembles equilibres et les tirages. Changer de graine recree un autre reseau. Ne pas choisir apres coup uniquement la graine gagnante.", 'Nouveau protocole vierge.'),
'n_kc': ('Nombre de Kenyon cells', 'Unites synthetiques', "1024 ou 2048, pas des cellules MaleCNS. Doubler augmente la capacite et le cout CPU, pas la quantite de donnees. Les poids de dimensions differentes ne sont pas convertis automatiquement.", 'Nouveau reseau et nouvelle calibration.'),
'mode': ('Parcours', 'Categorie', "curriculum = A exclusifs equilibres puis B ambigus puis C naturel; balanced = A seulement; chronological = tout TRAIN a chaque passage. CAL et TEST restent naturels.", 'Recreer le protocole, conserver le meme decoupage pour comparer.'),
'epochs': ('Passages', 'Nombre', "Repetitions de A dans curriculum/balanced, repetitions de tout TRAIN dans chronological. B et C n\'ont qu\'un passage en curriculum. Repeter ne cree pas de nouvelles observations.", 'Surveiller la memorisation et le cout CPU.'),
'shuffle_train': ('Melanger TRAIN', 'Booleen', "Permute seulement les exemples a l\'interieur des phases TRAIN apres separation chronologique. Le melange casse leur succession temporelle; ne pas l\'utiliser pour pretendre mesurer une adaptation a un regime en ligne.", 'Graine et permutation reproductibles; CAL/TEST inchanges.'),
'signal': ('Retour apres resultat', 'all / chosen', "all : les trois issues observees apres fermeture modifient les poids, via moyenne de trois mises a jour calculees depuis le meme etat. chosen : seule la decision faite recoit +1/-1. Aucune issue n\'est visible avant le choix.", 'All apprend plus de chaque fenetre mais change le protocole scientifique.'),
'learning_rate': ('Vitesse d\'apprentissage', 'Sans unite', "Multiplie chaque correction KC-MBON. Un taux eleve accelere l\'ajustement mais peut faire osciller ou saturer les poids. Ce n\'est ni la vitesse du marche ni celle de l\'animation.", 'Tester quelques valeurs sur un jeu de developpement; ne pas optimiser sur TEST.'),
'epsilon': ('Exploration TRAIN', 'Fraction', "Probabilite de choisir au hasard une des trois cases plutot que le meilleur score. En mode all, change surtout le choix affiche, pas les trois cibles apprises. CAL, TEST et politique live sont sans exploration volontaire.", '0.15 = 15 %, pas 0.15 %.'),
'sparsity': ('Fraction de KC active', 'Fraction', "Le seuil APL est le quantile 1-sparsity des excitations par candidat. 0.05 laisse environ 5 % actives; le nombre exact depend des egalites et du seuil numerique. Plus n\'est pas necessairement mieux.", 'Modifie les representations : nouveau protocole et calibration.'),
'use_liquidity': ('Capteurs de liquidite', 'Booleen', "Ajoute les caracteristiques du carnet et du flux aux projections. Les lignes sans carnet valide sont exclues du protocole quand cette option est active. Le compteur general du corpus peut donc depasser l\'effectif utilisable.", 'Ne jamais remplir un carnet manquant par un carnet futur ou neutre invente.'),
'economic_head': ('Lecture economique auxiliaire', 'Booleen', "Entraine des poids distincts a predire tanh(resultat net par unite / 2), seulement si une cotation differee a ete enregistree. Cette sortie n\'est pas une probabilite ni le PnL attendu; elle n\'est pas utilisee pour choisir la mise.", 'Le taux de cette lecture vaut 0.4 fois learning_rate.'),
'max_windows': ('Fenetre du corpus utilise', 'Episodes recents', "Selectionne au plus ce nombre de lignes recentes, puis filtre la liquidite. Le corpus reste entier sur disque. La separation 60/20/20 est refaite sur cette selection; changer ce nombre change les partitions.", 'Une selection de donnees est un nouveau protocole a documenter.'),
'batch': ('Lot CPU', 'Episodes par passage du worker', "Nombre maximal de mises a jour traitees ensemble par le worker. Ne correspond pas a une moyenne de gradient sur des exemples simultanes : les exemples sont appris dans l\'ordre du plan. Une transaction SQLite valide le lot.", 'Plus grand = interface potentiellement moins reactive; ne change pas l\'objectif.'),
}
ECON = {
'quote_mode': ('Origine des multiplicateurs', 'manual / synthetic', "manual : trois hypotheses fixes. synthetic : emetteur brownien causal a volatilite estimee, sans formule Euphoria. Dans les deux cas, aucun ordre reel ni prix garanti.", 'Mettre collecte/politique en pause et terminer les contrats avant modification.'),
'multipliers': ('Trois multiplicateurs affiches', 'Facteurs', "Ordre HAUSSE, STABLE, BAISSE. Utilises seulement en mode manuel. Chaque valeur est entre 1.01 et 100. Ils ne changent pas avec le cours tant que le mode reste manuel.", 'Attention a la convention total/profit.'),
'convention': ('Convention manuelle', 'total / profit', "total inclut la mise dans le versement. profit ajoute la mise rendue : le gross devient M+1. En mode synthetique la convention est toujours total, quel que soit ce menu.", 'Un mauvais choix change entierement le resultat economique.'),
'execution_delay': ('Delai de confirmation', 'Secondes', "Entre la decision et la nouvelle cotation du meme contrat. La fenetre est choisie assez loin pour conserver au moins 10 s de preavis a l\'execution. Le delai configure n\'est pas une mesure d\'Euphoria.", 'Re-cotation au premier tick du moteur apres echeance; refus si trop tard.'),
'max_slippage': ('Degradation maximale', 'Fraction; UI en %', "Refus si le multiplicateur gross execute est inferieur a celui au clic multiplie par 1-max_slippage. Amelioration acceptee. Pour decider, le modele utilise deja une cotation prudente diminuee de cette tolerance.", 'Tolerance, pas un frais ajoute une seconde fois au versement.'),
'haircut': ('Decote simulee', 'Fraction; UI en %', "Multiplie le gross par 1-haircut pour obtenir le multiplicateur effectif. Hypothese de stress appliquee au versement, differente du slippage de confirmation.", '0.01 = decote de 1 % du versement total.'),
'cost_per_stake': ('Cout par mise', 'Fraction; UI en %', "Cout fictif c*s pour une mise s, sur succes et echec. Il entre dans la reservation, le seuil de rentabilite et le resultat net. Pas une affirmation sur les frais Euphoria.", 'Le cout maximal possible compte dans le budget de risque.'),
'min_edge': ('Avantage prudent minimal', 'Gain net / unite misee', "Exige p_lower * M_pire - 1 - cout > min_edge. 0.02 signifie plus de 0.02 par euro engage selon nos hypotheses, pas une hausse de 2 % de l\'ETH.", 'Augmenter fait generalement attendre plus; ne rend pas les probabilites vraies.'),
'stake_mode': ('Mode de mise', 'adaptive / fixed', "adaptive compare les montants autorises par l\'utilite logarithmique prudente. fixed n\'examine que fixed_stake. Tous les garde-fous restent actifs dans les deux modes.", 'C\'est une regle explicite, pas un neurone moteur biologique.'),
'fixed_stake': ('Mise fixe', 'EUR fictifs', "Utilisee seulement en mode fixed. Si elle depasse le plafond de risque, le programme attend au lieu de la reduire automatiquement.", 'Montants possibles : 0.1 / 1 / 5 / 10 / 20.'),
'max_stake': ('Mise maximale', 'EUR fictifs', "Plafond absolu sur les montants candidats. Il ne remplace pas le plafond proportionnel max_fraction.", 'Avec 20 EUR et 5 %, 5/10/20 EUR restent exclus meme si max_stake=20.'),
'max_fraction': ('Part maximale du capital exposee', 'Fraction; UI en %', "Budget min(capital*max_fraction, capital-reserve). Le cout total s*(1+c) doit rentrer dans ce budget. Exposer tout le capital est refuse par l\'utilite logarithmique meme si la fraction vaut 1.", '0.05 = 5 %. Au depart sans cout, budget de 1 EUR.'),
'drawdown_limit': ('Limite depuis le plus haut', 'EUR fictifs', "La politique arrete les nouvelles mises lorsque pic du capital realise - capital actuel atteint cette valeur. La collecte continue. Ne supprime pas une perte deja realisee.", 'Seuil d\'arret, pas garantie de perte maximale exacte : un contrat ouvert peut depasser le seuil.'),
'synthetic_margin': ('Marge de l\'emetteur fictif', 'Fraction; UI en %', "En synthetique : M=min(plafond,(1-marge)/p_emetteur). Si ce montant est inferieur a 1.01, aucune cotation. La marge n\'est exacte que dans le modele simplifie avant autres limites.", 'Ce parametre ne calibre pas l\'emetteur sur Euphoria.'),
'max_multiplier': ('Plafond de cotation synthetique', 'Facteur total', "Limite les cotations extremes de l\'emetteur. S\'applique uniquement en mode synthetique; ne corrige pas une probabilite de touche mal estimee.", 'Un plafond bas peut supprimer des paris interessants dans le modele; mesurer sans promesse.'),
'volatility_floor': ('Plancher de volatilite emetteur', 'USD / racine(seconde)', "Minimum de sigma. Le modele prend aussi le RMS des variations a 1 s sur 10 et 30 s. Evite une estimation presque nulle apres une courte periode plate.", 'Hypothese de marche; ce n\'est pas la volatilite neuronale ou un taux d\'interet.'),
}


def model_fingerprint():
    root=Path(__file__).parent
    names=['brain06.py','decision_brain.py','motif_brain.py','economy06.py','economics.py','academy06.py','dataset.py','feed07.py','market.py','orderbook.py','run06.py','run08.py','academy08.py']
    h=sha256()
    for name in names:h.update(name.encode());h.update((root/name).read_bytes())
    return h.hexdigest()


def wiki_parameters():
    groups=[]
    for label,model,descriptions in [('Entrainement',Config06,TRAIN),('Economie et risque',Rules06,ECON)]:
        schema=model.model_json_schema()['properties'];defaults=model().model_dump();items=[]
        for key, prop in schema.items():
            title,unit,description,caution=descriptions[key]
            limits={k:v for k,v in prop.items() if k in ('minimum','maximum','exclusiveMinimum','exclusiveMaximum','enum','type','minItems','maxItems')}
            if key in ('fixed_stake','max_stake'):limits['enum']=list(STAKES)
            if key=='multipliers':limits.update(each_min=1.01,each_max=100)
            items.append(dict(key=key,title=title,unit=unit,default=defaults[key],limits=limits,
                              description=description,caution=caution))
        groups.append(dict(name=label,source='app/'+('academy06.py' if model is Config06 else 'economy06.py'),items=items))
    return dict(version='0.8.0-alpha',code_sha256=model_fingerprint(),groups=groups,
                note='Valeurs par defaut du code, pas les reglages de votre session active.')
