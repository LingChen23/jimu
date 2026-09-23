from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class Repository:
    """Small, transactional index. No samples or complete participant profiles."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    participant_id TEXT NOT NULL,
                    participant_name TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    task_status TEXT NOT NULL,
                    movement_count INTEGER NOT NULL,
                    total_score REAL,
                    score_model_version TEXT,
                    oss_prefix TEXT NOT NULL,
                    zip_size INTEGER NOT NULL,
                    archive_status TEXT NOT NULL,
                    uploaded_at TEXT,
                    error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS tasks_start_time ON tasks(start_time);
                CREATE INDEX IF NOT EXISTS tasks_participant ON tasks(participant_id);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def healthy(self):
        with self.connection() as db:
            db.execute('SELECT task_id FROM tasks LIMIT 1').fetchone()

    def get(self, task_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        return dict(row) if row else None

    def reserve(self, summary):
        # Keep the original task prefix even if another upload changes its date.
        columns = ','.join(summary)
        placeholders = ','.join('?' for _ in summary)
        with self.connection() as db:
            db.execute(f'INSERT OR IGNORE INTO tasks ({columns},archive_status) '
                       f'VALUES ({placeholders},\'processing\')', tuple(summary.values()))
        return self.get(summary['task_id'])

    def complete(self, summary):
        fields = {**summary, 'archive_status': 'archived', 'error_code': None,
                  'uploaded_at': datetime.now(timezone.utc).isoformat()}
        task_id = fields.pop('task_id')
        assignments = ','.join(f'{key}=?' for key in fields)
        with self.connection() as db:
            db.execute(f'UPDATE tasks SET {assignments} WHERE task_id=?',
                       (*fields.values(), task_id))

    def fail(self, task_id, code):
        with self.connection() as db:
            db.execute("UPDATE tasks SET archive_status='retryable',error_code=? "
                       "WHERE task_id=? AND archive_status!='archived'", (code, task_id))

    def pending(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM tasks WHERE archive_status!='archived'")]

    @staticmethod
    def _like(value):
        return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'

    def statistics(self):
        with self.connection() as db:
            row = db.execute("SELECT count(*) AS total, avg(total_score) AS average_score, "
                "max(substr(start_time,1,10)) AS latest_date FROM tasks WHERE archive_status='archived'").fetchone()
            result = dict(row)
            result['latest_count'] = db.execute("SELECT count(*) FROM tasks WHERE archive_status='archived' "
                "AND substr(start_time,1,10)=?", (result['latest_date'],)).fetchone()[0]
        return result

    def history(self, *, task_id='', participant_id='', participant_name='', date='',
                page=1, page_size=20):
        """Return one filtered page without exposing storage implementation fields."""
        clauses = ["archive_status='archived'"]
        parameters = []
        for column, value in (
            ('task_id', task_id),
            ('participant_id', participant_id),
            ('participant_name', participant_name),
        ):
            if value:
                clauses.append(f"{column} LIKE ? ESCAPE '\\'")
                parameters.append(self._like(value))
        if date:
            clauses.append('substr(start_time,1,10)=?')
            parameters.append(date)
        where = ' AND '.join(clauses)
        fields = ('task_id,participant_id,participant_name,start_time,end_time,task_status,'
                  'movement_count,total_score,score_model_version,zip_size,uploaded_at')
        offset = (page - 1) * page_size
        with self.connection() as db:
            total = db.execute(f'SELECT count(*) FROM tasks WHERE {where}', parameters).fetchone()[0]
            rows = db.execute(
                f'SELECT {fields} FROM tasks WHERE {where} '
                'ORDER BY start_time DESC,task_id DESC LIMIT ? OFFSET ?',
                (*parameters, page_size, offset),
            ).fetchall()
        return {'items': [dict(row) for row in rows], 'page': page,
                'page_size': page_size, 'total': total}
