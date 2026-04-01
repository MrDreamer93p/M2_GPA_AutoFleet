from __future__ import annotations

import json
import logging
from typing import Any

from .config import settings

LOG = logging.getLogger(__name__)


class PostgresStore:
    def __init__(self, dsn: str = settings.database_dsn) -> None:
        self.dsn = dsn
        self.enabled = bool(dsn)
        self.available = False
        self._conn = None
        self._dict_row = None
        if self.enabled:
            self._ensure_connection()

    def _ensure_connection(self) -> None:
        if not self.enabled:
            return
        if self._conn is not None and not getattr(self._conn, "closed", False):
            return
        try:
            import psycopg
            from psycopg.rows import dict_row

            self._dict_row = dict_row
            self._conn = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
            self.available = True
            self._init_schema()
        except Exception as exc:  # pragma: no cover - network/service dependent
            self.available = False
            self._conn = None
            LOG.warning("postgres store unavailable: %s", exc)

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if not self.enabled:
            return
        self._ensure_connection()
        if not self.available or self._conn is None:
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
        except Exception as exc:  # pragma: no cover - service dependent
            self.available = False
            LOG.warning("postgres exec failed: %s", exc)

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        self._ensure_connection()
        if not self.available or self._conn is None:
            return []
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        except Exception as exc:  # pragma: no cover - service dependent
            self.available = False
            LOG.warning("postgres fetch failed: %s", exc)
            return []

    def _payload(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=True)

    def _init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS events (
            id BIGSERIAL PRIMARY KEY,
            stream_name TEXT NOT NULL,
            topic TEXT,
            event_type TEXT,
            robot_id TEXT,
            mission_id TEXT,
            ts BIGINT,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_events_robot_id ON events(robot_id);
        CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);

        CREATE TABLE IF NOT EXISTS robot_latest (
            robot_id TEXT PRIMARY KEY,
            state TEXT,
            last_ts BIGINT,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS alerts (
            alert_id TEXT PRIMARY KEY,
            robot_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            ts BIGINT NOT NULL,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_alerts_robot_id ON alerts(robot_id);
        CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts DESC);

        CREATE TABLE IF NOT EXISTS missions (
            mission_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            updated_ts BIGINT NOT NULL,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS perception_latest (
            robot_id TEXT PRIMARY KEY,
            ts BIGINT NOT NULL,
            risk_level TEXT NOT NULL,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS video_streams (
            robot_id TEXT PRIMARY KEY,
            ts BIGINT NOT NULL,
            status TEXT NOT NULL,
            proxy_url TEXT,
            snapshot_url TEXT,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS heartbeats (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            robot_id TEXT,
            status TEXT NOT NULL,
            ts BIGINT NOT NULL,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS map_latest (
            robot_id TEXT PRIMARY KEY,
            ts BIGINT NOT NULL,
            obstacle_count INTEGER NOT NULL,
            risk_level TEXT NOT NULL,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS coordination_latest (
            robot_id TEXT PRIMARY KEY,
            ts BIGINT NOT NULL,
            collision_risk TEXT NOT NULL,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
        for statement in [chunk.strip() for chunk in ddl.split(";") if chunk.strip()]:
            self._exec(statement)

    def append_event(
        self,
        stream_name: str,
        payload: dict[str, Any],
        *,
        topic: str | None = None,
        event_type: str | None = None,
        robot_id: str | None = None,
        mission_id: str | None = None,
        ts: int | None = None,
    ) -> None:
        self._exec(
            """
            INSERT INTO events(stream_name, topic, event_type, robot_id, mission_id, ts, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (stream_name, topic, event_type, robot_id, mission_id, ts, self._payload(payload)),
        )

    def upsert_robot_latest(self, robot_id: str, payload: dict[str, Any], state: str | None, last_ts: int | None) -> None:
        self._exec(
            """
            INSERT INTO robot_latest(robot_id, state, last_ts, payload)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (robot_id) DO UPDATE
            SET state = EXCLUDED.state,
                last_ts = EXCLUDED.last_ts,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (robot_id, state, last_ts, self._payload(payload)),
        )

    def upsert_alert(self, alert_id: str, robot_id: str, alert_type: str, severity: str, status: str, ts: int, payload: dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO alerts(alert_id, robot_id, severity, status, alert_type, ts, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (alert_id) DO UPDATE
            SET severity = EXCLUDED.severity,
                status = EXCLUDED.status,
                alert_type = EXCLUDED.alert_type,
                ts = EXCLUDED.ts,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (alert_id, robot_id, severity, status, alert_type, ts, self._payload(payload)),
        )

    def upsert_mission(self, mission_id: str, status: str, updated_ts: int, payload: dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO missions(mission_id, status, updated_ts, payload)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (mission_id) DO UPDATE
            SET status = EXCLUDED.status,
                updated_ts = EXCLUDED.updated_ts,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (mission_id, status, updated_ts, self._payload(payload)),
        )

    def upsert_perception(self, robot_id: str, ts: int, risk_level: str, payload: dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO perception_latest(robot_id, ts, risk_level, payload)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (robot_id) DO UPDATE
            SET ts = EXCLUDED.ts,
                risk_level = EXCLUDED.risk_level,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (robot_id, ts, risk_level, self._payload(payload)),
        )

    def upsert_video_stream(self, robot_id: str, ts: int, status: str, proxy_url: str | None, snapshot_url: str | None, payload: dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO video_streams(robot_id, ts, status, proxy_url, snapshot_url, payload)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (robot_id) DO UPDATE
            SET ts = EXCLUDED.ts,
                status = EXCLUDED.status,
                proxy_url = EXCLUDED.proxy_url,
                snapshot_url = EXCLUDED.snapshot_url,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (robot_id, ts, status, proxy_url, snapshot_url, self._payload(payload)),
        )

    def upsert_heartbeat(self, source_id: str, source_type: str, robot_id: str | None, status: str, ts: int, payload: dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO heartbeats(source_id, source_type, robot_id, status, ts, payload)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (source_id) DO UPDATE
            SET source_type = EXCLUDED.source_type,
                robot_id = EXCLUDED.robot_id,
                status = EXCLUDED.status,
                ts = EXCLUDED.ts,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (source_id, source_type, robot_id, status, ts, self._payload(payload)),
        )

    def upsert_map_summary(self, robot_id: str, ts: int, obstacle_count: int, risk_level: str, payload: dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO map_latest(robot_id, ts, obstacle_count, risk_level, payload)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (robot_id) DO UPDATE
            SET ts = EXCLUDED.ts,
                obstacle_count = EXCLUDED.obstacle_count,
                risk_level = EXCLUDED.risk_level,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (robot_id, ts, obstacle_count, risk_level, self._payload(payload)),
        )

    def upsert_coordination(self, robot_id: str, ts: int, collision_risk: str, payload: dict[str, Any]) -> None:
        self._exec(
            """
            INSERT INTO coordination_latest(robot_id, ts, collision_risk, payload)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (robot_id) DO UPDATE
            SET ts = EXCLUDED.ts,
                collision_risk = EXCLUDED.collision_risk,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            (robot_id, ts, collision_risk, self._payload(payload)),
        )

    def fetch_alerts(self, *, limit: int = 50, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM alerts"
        params: list[Any] = []
        if active_only:
            sql += " WHERE status = %s"
            params.append("active")
        sql += " ORDER BY ts DESC LIMIT %s"
        params.append(limit)
        rows = self._fetchall(sql, tuple(params))
        return [row["payload"] for row in rows if row.get("payload")]
