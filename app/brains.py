"""Phase 1: 'Cerveaux' registry. Named, independent workshop slots.

A registry row is a permanent WORKSHOP SLOT (its primary key, workshop_id),
separate from the Brain06 identity (brain_id) currently occupying it.
Full retraining/replay/reset can mint a brand new brain_id inside a slot
(unchanged Phase 0 behaviour: even identical parameters get a distinct
permanent identity); the workshop_id is what the user names, lists,
duplicates and archives. brain_id (permanent identity) and
weight_fingerprint (identity of the weights only) are never conflated here.
"""
from __future__ import annotations
import uuid
from .academy06 import LEGACY_WORKSHOP_ID
from .academy08 import Academy08
from .engine import stamp

STATES = ('neuf', 'entrainement', 'entraine', 'teste', 'deploye', 'archive')


class BrainRegistry:
    def __init__(self, data, expected_source=None):
        self.data = data
        self.expected_source = expected_source

    def _open(self, workshop_id=None):
        return Academy08(self.data, self.expected_source, workshop_id=workshop_id)

    def open(self, workshop_id):
        """A short-lived Academy08 scoped to one named brain, for the training wizard.
        Caller owns the returned instance and must call .close() when done."""
        if not workshop_id:
            raise ValueError('Cerveau introuvable')
        a = self._open(workshop_id)
        self._ensure_schema(a.db)
        if workshop_id == LEGACY_WORKSHOP_ID:
            self._ensure_legacy_row(a.db, a)
        if not a.db.execute('SELECT 1 FROM brains WHERE workshop_id=?', (workshop_id,)).fetchone():
            a.close()
            raise ValueError('Cerveau introuvable')
        return a

    def _ensure_schema(self, db):
        db.executescript('''CREATE TABLE IF NOT EXISTS brains(
            workshop_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
            legacy INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
            parent_workshop_id TEXT,
            initial_n_kc INTEGER NOT NULL, initial_seed INTEGER NOT NULL,
            initial_sparsity REAL NOT NULL, initial_use_liquidity INTEGER NOT NULL);''')

    def _ensure_legacy_row(self, db, legacy_academy=None):
        if db.execute('SELECT 1 FROM brains WHERE workshop_id=?', (LEGACY_WORKSHOP_ID,)).fetchone():
            return
        owns = legacy_academy is None
        legacy = legacy_academy or self._open(LEGACY_WORKSHOP_ID)
        try:
            # A never-trained legacy placeholder is never persisted on its own (Academy06
            # only saves after a real action). Force one save now so its identity is
            # stable across every future probe instead of re-minting on each read. When
            # the live process's own academy is passed in, save through it directly so
            # the persisted identity matches what the Training page is showing right now.
            legacy.save()
            with db:
                db.execute('INSERT INTO brains VALUES (?,?,?,1,0,NULL,?,?,?,?)',
                    (LEGACY_WORKSHOP_ID, 'Cerveau atelier (herite)', stamp(),
                     legacy.brain.n_kc, legacy.brain.seed, legacy.brain.sparsity,
                     int(legacy.brain.use_liquidity)))
        finally:
            if owns:
                legacy.close()

    def create(self, name, n_kc=2048, seed=42, sparsity=.05, use_liquidity=True):
        name = (name or '').strip()
        if not name:
            raise ValueError('Nom obligatoire')
        workshop_id = str(uuid.uuid4())
        a = self._open(workshop_id)
        try:
            self._ensure_schema(a.db)
            self._ensure_legacy_row(a.db)
            with a.db:
                a.db.execute('INSERT INTO brains VALUES (?,?,?,0,0,NULL,?,?,?,?)',
                    (workshop_id, name[:80], stamp(), n_kc, seed, sparsity, int(use_liquidity)))
            a.initialize_brain(seed=seed, n_kc=n_kc, sparsity=sparsity, use_liquidity=use_liquidity)
        finally:
            a.close()
        return self.get(workshop_id)

    def duplicate(self, workshop_id, name):
        name = (name or '').strip()
        if not name:
            raise ValueError('Nom obligatoire')
        src = self._open(workshop_id)
        try:
            self._ensure_schema(src.db)
            row = src.db.execute(
                'SELECT initial_n_kc,initial_seed,initial_sparsity,initial_use_liquidity FROM brains WHERE workshop_id=?',
                (workshop_id,)).fetchone()
            if not row:
                raise ValueError('Cerveau introuvable')
            new_id = str(uuid.uuid4())
            with src.db:
                src.db.execute('INSERT INTO brains VALUES (?,?,?,0,0,?,?,?,?,?)',
                    (new_id, name[:80], stamp(), workshop_id, *row))
            dup = self._open(new_id)
            try:
                dup.clone_brain_from(src.brain)
            finally:
                dup.close()
        finally:
            src.close()
        return self.get(new_id)

    def archive(self, workshop_id):
        a = self._open(workshop_id)
        try:
            self._ensure_schema(a.db)
            if not a.db.execute('SELECT 1 FROM brains WHERE workshop_id=?', (workshop_id,)).fetchone():
                raise ValueError('Cerveau introuvable')
            with a.db:
                a.db.execute('UPDATE brains SET archived=1 WHERE workshop_id=?', (workshop_id,))
        finally:
            a.close()

    def get(self, workshop_id, live_brain_id=None, legacy_academy=None):
        if legacy_academy is not None and workshop_id == LEGACY_WORKSHOP_ID:
            self._ensure_schema(legacy_academy.db)
            self._ensure_legacy_row(legacy_academy.db, legacy_academy)
            row = legacy_academy.db.execute(
                'SELECT workshop_id,name,created_at,legacy,archived,parent_workshop_id FROM brains WHERE workshop_id=?',
                (workshop_id,)).fetchone()
            if not row:
                raise ValueError('Cerveau introuvable')
            return self._describe(legacy_academy, row, live_brain_id)
        a = self._open(workshop_id)
        try:
            self._ensure_schema(a.db)
            self._ensure_legacy_row(a.db)
            row = a.db.execute(
                'SELECT workshop_id,name,created_at,legacy,archived,parent_workshop_id FROM brains WHERE workshop_id=?',
                (workshop_id,)).fetchone()
            if not row:
                raise ValueError('Cerveau introuvable')
            return self._describe(a, row, live_brain_id)
        finally:
            a.close()

    def list(self, live_brain_id=None, legacy_academy=None):
        probe = legacy_academy or self._open()
        try:
            self._ensure_schema(probe.db)
            self._ensure_legacy_row(probe.db, legacy_academy)
            rows = probe.db.execute(
                'SELECT workshop_id,name,created_at,legacy,archived,parent_workshop_id FROM brains ORDER BY created_at'
            ).fetchall()
        finally:
            if legacy_academy is None:
                probe.close()
        out = []
        for row in rows:
            if legacy_academy is not None and row[0] == LEGACY_WORKSHOP_ID:
                out.append(self._describe(legacy_academy, row, live_brain_id))
                continue
            a = self._open(row[0])
            try:
                out.append(self._describe(a, row, live_brain_id))
            finally:
                a.close()
        return out

    def _describe(self, a, row, live_brain_id):
        workshop_id, name, created_at, legacy, archived, parent = row
        state = self._state(a, bool(archived), live_brain_id)
        val = ((a.metrics or {}).get('Calibration gelee') or {})
        dataset_name = None
        if a.config.dataset_id:
            match = next((d for d in a.datasets() if d['dataset_id'] == a.config.dataset_id), None)
            dataset_name = match['name'] if match else None
        return dict(
            workshop_id=workshop_id, name=name, created_at=created_at,
            legacy=bool(legacy), archived=bool(archived), parent_workshop_id=parent,
            brain_id=a.brain.brain_id, brain_id_short=a.brain.brain_id[:8],
            weight_fingerprint=a.brain.fingerprint(), weight_fingerprint_short=a.brain.fingerprint()[:8],
            n_kc=a.brain.n_kc, state=state, updates=a.brain.updates,
            balanced_accuracy_validation=val.get('balanced_accuracy'),
            dataset_id=a.config.dataset_id, dataset_name=dataset_name,
            test_opened=bool(a.test_opened),
            actions=self._actions(state),
        )

    @staticmethod
    def _state(a, archived, live_brain_id):
        if archived:
            return 'archive'
        if live_brain_id and a.brain.brain_id == live_brain_id:
            return 'deploye'
        if not a.session:
            return 'neuf'
        if a.position < len(a.plan):
            return 'entrainement'
        if a.test_opened:
            return 'teste'
        return 'entraine'

    @staticmethod
    def _actions(state):
        if state == 'archive':
            return dict(train=(False, 'Cerveau archive : desarchiver non disponible pour le moment'),
                        test=(False, 'Cerveau archive'), deploy=(False, 'Cerveau archive'),
                        duplicate=(True, None), archive=(False, 'Deja archive'))
        return dict(
            train=(True, 'Ouvre l\'assistant Entrainement pour ce cerveau.') if state in ('neuf', 'entraine', 'teste', 'deploye') else (False, 'Entrainement deja en cours'),
            test=(True, 'Ouvre l\'assistant Entrainement pour ce cerveau.') if state in ('entraine', 'teste', 'deploye') else (False, 'Terminer un entrainement avant de tester'),
            deploy=(True, 'Ouvre l\'assistant Entrainement pour deployer ce cerveau.') if state in ('entraine', 'teste', 'deploye') else (False, 'Le cerveau doit etre entraine (calibration comprise) avant transfert'),
            duplicate=(True, None),
            archive=(True, None) if state != 'entrainement' else (False, 'Mettre l\'entrainement en pause avant d\'archiver'),
        )
