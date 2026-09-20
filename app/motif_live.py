"""Independent live instance: never upgrades or overwrites legacy weights."""
from .live import Live
from .motif_brain import MotifBrain, MOTIFS, ENCODER


class MotifLive(Live):
    brain_class = MotifBrain
    encoder = ENCODER
    database_name = 'flytrade-motifs-live.sqlite3'
    model_key = 'motifs'
    compartment_names = MOTIFS

    def __init__(self, data_dir, market):
        self.deployment = None
        super().__init__(data_dir, market)
        import json
        row = self.db.execute('SELECT payload FROM checkpoint WHERE id=1').fetchone()
        if row:
            self.deployment = json.loads(row[0]).get('deployment')
        self.db.execute('CREATE TABLE IF NOT EXISTS deployments (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()

    def checkpoint(self):
        return {**super().checkpoint(), 'deployment': self.deployment}

    def deploy(self, trained, metadata, settings):
        import json, time
        from .live import empty_stats
        if self.running or self.auto:
            raise ValueError('Arreter le live avant de transferer des poids.')
        old = (self.brain, self.stats, self.generation, self.run, self.settings, self.deployment)
        # A persistent audit copy precedes replacement. No historical weights lost.
        self.db.execute('INSERT INTO deployments VALUES (?,?)',
                        (time.time_ns(), json.dumps(self.checkpoint(), allow_nan=False)))
        self.db.commit()
        self.brain = MotifBrain.from_dict(trained.to_dict())
        self.stats = empty_stats(); self.generation += 1; self.run = None
        self.settings = settings; self.deployment = metadata
        try: self._save()
        except Exception:
            self.brain, self.stats, self.generation, self.run, self.settings, self.deployment = old
            raise
        self.log('transfert', 'Poids du replay copies vers les motifs live. Plasticite GELEE. Reference ETH intacte.')

    def reset(self, seed=42):
        before = self.deployment
        self.deployment = None
        try:
            super().reset(seed)
        except Exception:
            self.deployment = before
            raise
