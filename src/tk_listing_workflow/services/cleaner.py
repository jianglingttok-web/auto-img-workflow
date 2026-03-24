from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path


class ExpiredTaskCleaner:
    def __init__(self, db_path: Path, runtime_root: Path) -> None:
        self.db_path = Path(db_path)
        self.runtime_root = Path(runtime_root)

    def cleanup(self, now_ts: float | None = None) -> int:
        now = float(now_ts or time.time())
        cleaned = 0
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT task_id FROM tasks WHERE expires_at <= ? AND status != 'expired'",
                (now,),
            ).fetchall()
            for row in rows:
                task_id = str(row['task_id'])
                task_dir = self.runtime_root / task_id
                if task_dir.exists():
                    shutil.rmtree(task_dir, ignore_errors=True)
                conn.execute(
                    "UPDATE tasks SET status='expired', updated_at=?, result_zip_path=NULL WHERE task_id=?",
                    (now, task_id),
                )
                cleaned += 1
            conn.commit()
        return cleaned
