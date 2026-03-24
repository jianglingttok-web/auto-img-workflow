from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class UsageService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def summary(self) -> dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            total_row = conn.execute(
                """
                SELECT
                  COUNT(*) AS total_images,
                  COALESCE(SUM(price_estimated), 0) AS total_cost_estimated,
                  COALESCE(SUM(COALESCE(price_actual, price_estimated)), 0) AS total_cost_actual
                FROM usage
                """
            ).fetchone()
            by_month = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT strftime('%Y-%m', datetime(created_at, 'unixepoch')) AS month,
                           COUNT(*) AS images,
                           COALESCE(SUM(price_estimated), 0) AS cost_estimated,
                           COALESCE(SUM(COALESCE(price_actual, price_estimated)), 0) AS cost_actual
                    FROM usage
                    GROUP BY month
                    ORDER BY month DESC
                    """
                ).fetchall()
            ]
            by_model = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT image_model_id AS model_id,
                           COUNT(*) AS images,
                           COALESCE(SUM(price_estimated), 0) AS cost_estimated,
                           COALESCE(SUM(COALESCE(price_actual, price_estimated)), 0) AS cost_actual
                    FROM usage
                    GROUP BY image_model_id
                    ORDER BY images DESC, model_id ASC
                    """
                ).fetchall()
            ]
            by_group = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT group_name,
                           COUNT(*) AS images,
                           COALESCE(SUM(price_estimated), 0) AS cost_estimated,
                           COALESCE(SUM(COALESCE(price_actual, price_estimated)), 0) AS cost_actual
                    FROM usage
                    GROUP BY group_name
                    ORDER BY images DESC, group_name ASC
                    """
                ).fetchall()
            ]
        return {
            'total_images': int(total_row['total_images'] or 0),
            'total_cost_estimated': float(total_row['total_cost_estimated'] or 0),
            'total_cost_actual': float(total_row['total_cost_actual'] or 0),
            'by_month': by_month,
            'by_model': by_model,
            'by_group': by_group,
        }
