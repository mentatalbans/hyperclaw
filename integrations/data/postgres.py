"""PostgresConnector — PostgreSQL database integration."""

from __future__ import annotations

import logging

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorInfo,
    ConnectorUnavailableError,
)

logger = logging.getLogger("hyperclaw.integrations.postgres")


class PostgresConnector(BaseConnector):
    """
    PostgreSQL database connector using asyncpg.

    Required config:
        - connection_string: PostgreSQL connection URL
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.connection_string = config.get("connection_string", "")

        if config.get("enabled", False) and not self.connection_string:
            raise ValueError("PostgresConnector requires connection_string")

        self._pool = None

    @property
    def info(self) -> ConnectorInfo:
        return ConnectorInfo(
            connector_id="postgres",
            platform="postgres",
            category="data",
            capabilities=frozenset(
                [
                    ConnectorCapability.READ_DATA,
                    ConnectorCapability.WRITE_DATA,
                    ConnectorCapability.DELETE_DATA,
                    ConnectorCapability.LIST_DATA,
                    ConnectorCapability.SEARCH,
                ]
            ),
            auth_type="api_key",
            rate_limit_per_minute=1000,
        )

    async def _ensure_pool(self):
        """Ensure connection pool is created."""
        if self._pool is None:
            try:
                import asyncpg
            except ImportError:
                raise ConnectorUnavailableError("asyncpg not installed")

            self._pool = await asyncpg.create_pool(
                self.connection_string,
                min_size=1,
                max_size=10,
            )

    async def health(self) -> bool:
        """Check if PostgreSQL connection is available."""
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            return False

        try:
            await self._ensure_pool()
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"PostgreSQL health check failed: {e}")
            return False

    async def query(self, sql: str, params: list | None = None) -> list[dict]:
        """Execute a SQL query and return results."""
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            raise ConnectorUnavailableError("asyncpg not installed")

        await self._ensure_pool()

        async with self._pool.acquire() as conn:
            if params:
                rows = await conn.fetch(sql, *params)
            else:
                rows = await conn.fetch(sql)

            return [dict(row) for row in rows]

    async def execute(self, sql: str, params: list | None = None) -> dict:
        """Execute a SQL statement (INSERT/UPDATE/DELETE)."""
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            raise ConnectorUnavailableError("asyncpg not installed")

        await self._ensure_pool()

        async with self._pool.acquire() as conn:
            if params:
                result = await conn.execute(sql, *params)
            else:
                result = await conn.execute(sql)

            return {"status": result}

    async def list_tables(self) -> list[dict]:
        """List all tables in the database."""
        sql = """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """
        return await self.query(sql)

    async def _read_impl(self, resource_id: str, **kwargs) -> dict:
        """Read a row by ID."""
        table = kwargs.get("table", "")
        if not table:
            raise ValueError("table parameter required")

        result = await self.query(
            f"SELECT * FROM {table} WHERE id = $1",
            [resource_id],
        )
        return result[0] if result else {}

    async def _write_impl(self, resource_type: str, data: dict, **kwargs) -> dict:
        """Insert a row."""
        columns = ", ".join(data.keys())
        placeholders = ", ".join(f"${i+1}" for i in range(len(data)))
        values = list(data.values())

        result = await self.query(
            f"INSERT INTO {resource_type} ({columns}) VALUES ({placeholders}) RETURNING *",
            values,
        )
        return result[0] if result else {}

    async def _delete_impl(self, resource_id: str, **kwargs) -> bool:
        """Delete a row."""
        table = kwargs.get("table", "")
        if not table:
            raise ValueError("table parameter required")

        await self.execute(
            f"DELETE FROM {table} WHERE id = $1",
            [resource_id],
        )
        return True

    async def _list_impl(
        self, resource_type: str, filters: dict, **kwargs
    ) -> list[dict]:
        """List rows from a table."""
        sql = f"SELECT * FROM {resource_type}"

        if filters:
            conditions = []
            params = []
            for i, (key, value) in enumerate(filters.items()):
                conditions.append(f"{key} = ${i+1}")
                params.append(value)
            sql += " WHERE " + " AND ".join(conditions)
            return await self.query(sql, params)

        return await self.query(sql)

    async def _search_impl(self, query: str, **kwargs) -> list[dict]:
        """Execute a raw SQL query."""
        return await self.query(query)
