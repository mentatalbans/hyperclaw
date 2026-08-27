"""
Infrastructure Connectors.
Integration with Docker, AWS, GCP, Azure, Tailscale, Snowflake, BigQuery, Datadog, PagerDuty.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """Docker container."""
    id: str
    name: str
    image: str
    status: str
    ports: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class CloudResource:
    """Cloud resource."""
    id: str
    name: str
    type: str
    region: str | None = None
    status: str = ""
    raw: dict = field(default_factory=dict)


class InfrastructureConnector(ABC):
    """Abstract base for infrastructure connectors."""

    name: str = "base"


# ═══════════════════════════════════════════════════════════════════════════════
# DOCKER
# ═══════════════════════════════════════════════════════════════════════════════

class DockerConnector(InfrastructureConnector):
    """Docker Engine API connector."""

    name = "docker"

    def __init__(self, host: str | None = None):
        self.host = host or os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
        if self.host.startswith("unix://"):
            self.transport = httpx.HTTPTransport(uds=self.host[7:])
            self.base_url = "http://localhost"
        else:
            self.transport = None
            self.base_url = self.host

    async def list_containers(self, all: bool = False) -> list[Container]:
        async with httpx.AsyncClient(transport=self.transport) as client:
            response = await client.get(
                f"{self.base_url}/containers/json",
                params={"all": str(all).lower()},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                Container(
                    id=c["Id"][:12],
                    name=c["Names"][0].lstrip("/") if c.get("Names") else "",
                    image=c["Image"],
                    status=c["Status"],
                    ports={p["PrivatePort"]: p.get("PublicPort") for p in c.get("Ports", [])},
                    raw=c,
                )
                for c in data
            ]

    async def start_container(self, container_id: str) -> dict:
        async with httpx.AsyncClient(transport=self.transport) as client:
            response = await client.post(
                f"{self.base_url}/containers/{container_id}/start",
                timeout=30.0,
            )
            if response.status_code == 204:
                return {"status": "started"}
            response.raise_for_status()
            return response.json()

    async def stop_container(self, container_id: str, timeout: int = 10) -> dict:
        async with httpx.AsyncClient(transport=self.transport) as client:
            response = await client.post(
                f"{self.base_url}/containers/{container_id}/stop",
                params={"t": timeout},
                timeout=30.0,
            )
            if response.status_code == 204:
                return {"status": "stopped"}
            response.raise_for_status()
            return response.json()

    async def logs(self, container_id: str, tail: int = 100) -> str:
        async with httpx.AsyncClient(transport=self.transport) as client:
            response = await client.get(
                f"{self.base_url}/containers/{container_id}/logs",
                params={"stdout": "true", "stderr": "true", "tail": tail},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.text

    async def run(self, image: str, command: str | None = None, **kwargs) -> Container:
        """Create and start a container."""
        async with httpx.AsyncClient(transport=self.transport) as client:
            # Create container
            create_response = await client.post(
                f"{self.base_url}/containers/create",
                json={
                    "Image": image,
                    "Cmd": command.split() if command else None,
                    **kwargs,
                },
                timeout=60.0,
            )
            create_response.raise_for_status()
            container_id = create_response.json()["Id"]

            # Start container
            await client.post(
                f"{self.base_url}/containers/{container_id}/start",
                timeout=30.0,
            )

            return Container(id=container_id[:12], name="", image=image, status="running")


# ═══════════════════════════════════════════════════════════════════════════════
# AWS
# ═══════════════════════════════════════════════════════════════════════════════

class AWSConnector(InfrastructureConnector):
    """AWS connector (via boto3)."""

    name = "aws"

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
    ):
        self.access_key = access_key or os.environ.get("AWS_ACCESS_KEY_ID")
        self.secret_key = secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY")
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")

    def _get_client(self, service: str):
        try:
            import boto3
        except ImportError:
            raise ImportError("boto3 required: pip install boto3")

        return boto3.client(
            service,
            region_name=self.region,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
        )

    async def list_ec2_instances(self) -> list[CloudResource]:
        import asyncio

        ec2 = self._get_client("ec2")

        def _list():
            response = ec2.describe_instances()
            instances = []
            for reservation in response["Reservations"]:
                for instance in reservation["Instances"]:
                    name = ""
                    for tag in instance.get("Tags", []):
                        if tag["Key"] == "Name":
                            name = tag["Value"]
                            break
                    instances.append(
                        CloudResource(
                            id=instance["InstanceId"],
                            name=name,
                            type="ec2",
                            region=self.region,
                            status=instance["State"]["Name"],
                            raw=instance,
                        )
                    )
            return instances

        return await asyncio.to_thread(_list)

    async def list_s3_buckets(self) -> list[CloudResource]:
        import asyncio

        s3 = self._get_client("s3")

        def _list():
            response = s3.list_buckets()
            return [
                CloudResource(
                    id=bucket["Name"],
                    name=bucket["Name"],
                    type="s3",
                    raw=bucket,
                )
                for bucket in response["Buckets"]
            ]

        return await asyncio.to_thread(_list)

    async def invoke_lambda(self, function_name: str, payload: dict) -> dict:
        import asyncio
        import json

        lambda_client = self._get_client("lambda")

        def _invoke():
            response = lambda_client.invoke(
                FunctionName=function_name,
                Payload=json.dumps(payload),
            )
            return json.loads(response["Payload"].read())

        return await asyncio.to_thread(_invoke)


# ═══════════════════════════════════════════════════════════════════════════════
# GCP
# ═══════════════════════════════════════════════════════════════════════════════

class GCPConnector(InfrastructureConnector):
    """Google Cloud Platform connector."""

    name = "gcp"

    def __init__(
        self,
        project_id: str | None = None,
        credentials_path: str | None = None,
    ):
        self.project_id = project_id or os.environ.get("GCP_PROJECT_ID")
        self.credentials_path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        self._token: str | None = None

    async def _get_token(self) -> str:
        if self._token:
            return self._token

        # Use gcloud CLI for token
        import subprocess

        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            self._token = result.stdout.strip()
            return self._token
        raise RuntimeError("Failed to get GCP token")

    async def list_compute_instances(self, zone: str = "us-central1-a") -> list[CloudResource]:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/zones/{zone}/instances",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                CloudResource(
                    id=instance["id"],
                    name=instance["name"],
                    type="compute",
                    region=zone,
                    status=instance["status"],
                    raw=instance,
                )
                for instance in data.get("items", [])
            ]

    async def list_gcs_buckets(self) -> list[CloudResource]:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://storage.googleapis.com/storage/v1/b",
                headers={"Authorization": f"Bearer {token}"},
                params={"project": self.project_id},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                CloudResource(
                    id=bucket["id"],
                    name=bucket["name"],
                    type="gcs",
                    region=bucket.get("location"),
                    raw=bucket,
                )
                for bucket in data.get("items", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# AZURE
# ═══════════════════════════════════════════════════════════════════════════════

class AzureConnector(InfrastructureConnector):
    """Microsoft Azure connector."""

    name = "azure"

    def __init__(
        self,
        subscription_id: str | None = None,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ):
        self.subscription_id = subscription_id or os.environ.get("AZURE_SUBSCRIPTION_ID")
        self.tenant_id = tenant_id or os.environ.get("AZURE_TENANT_ID")
        self.client_id = client_id or os.environ.get("AZURE_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("AZURE_CLIENT_SECRET")
        self._token: str | None = None

    async def _get_token(self) -> str:
        if self._token:
            return self._token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://management.azure.com/.default",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            self._token = data["access_token"]
            return self._token

    async def list_vms(self, resource_group: str | None = None) -> list[CloudResource]:
        token = await self._get_token()

        url = f"https://management.azure.com/subscriptions/{self.subscription_id}"
        if resource_group:
            url += f"/resourceGroups/{resource_group}"
        url += "/providers/Microsoft.Compute/virtualMachines?api-version=2023-03-01"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                CloudResource(
                    id=vm["id"],
                    name=vm["name"],
                    type="vm",
                    region=vm["location"],
                    raw=vm,
                )
                for vm in data.get("value", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# TAILSCALE
# ═══════════════════════════════════════════════════════════════════════════════

class TailscaleConnector(InfrastructureConnector):
    """Tailscale VPN connector."""

    name = "tailscale"

    def __init__(self, api_key: str | None = None, tailnet: str | None = None):
        self.api_key = api_key or os.environ.get("TAILSCALE_API_KEY")
        self.tailnet = tailnet or os.environ.get("TAILSCALE_TAILNET", "-")
        self.base_url = "https://api.tailscale.com/api/v2"

    async def list_devices(self) -> list[CloudResource]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/tailnet/{self.tailnet}/devices",
                auth=(self.api_key, ""),
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                CloudResource(
                    id=device["id"],
                    name=device["name"],
                    type="tailscale",
                    status="online" if device.get("online") else "offline",
                    raw=device,
                )
                for device in data.get("devices", [])
            ]

    async def get_device(self, device_id: str) -> CloudResource:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/device/{device_id}",
                auth=(self.api_key, ""),
                timeout=30.0,
            )
            response.raise_for_status()
            device = response.json()

            return CloudResource(
                id=device["id"],
                name=device["name"],
                type="tailscale",
                status="online" if device.get("online") else "offline",
                raw=device,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# SNOWFLAKE
# ═══════════════════════════════════════════════════════════════════════════════

class SnowflakeConnector(InfrastructureConnector):
    """Snowflake data warehouse connector."""

    name = "snowflake"

    def __init__(
        self,
        account: str | None = None,
        user: str | None = None,
        password: str | None = None,
        warehouse: str | None = None,
        database: str | None = None,
    ):
        self.account = account or os.environ.get("SNOWFLAKE_ACCOUNT")
        self.user = user or os.environ.get("SNOWFLAKE_USER")
        self.password = password or os.environ.get("SNOWFLAKE_PASSWORD")
        self.warehouse = warehouse or os.environ.get("SNOWFLAKE_WAREHOUSE")
        self.database = database or os.environ.get("SNOWFLAKE_DATABASE")

    def _get_connection(self):
        try:
            import snowflake.connector
        except ImportError:
            raise ImportError("snowflake-connector-python required: pip install snowflake-connector-python")

        return snowflake.connector.connect(
            account=self.account,
            user=self.user,
            password=self.password,
            warehouse=self.warehouse,
            database=self.database,
        )

    async def query(self, sql: str) -> list[dict]:
        import asyncio

        def _query():
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(sql)
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    async def list_warehouses(self) -> list[CloudResource]:
        results = await self.query("SHOW WAREHOUSES")
        return [
            CloudResource(
                id=row["name"],
                name=row["name"],
                type="warehouse",
                status=row.get("state", ""),
                raw=row,
            )
            for row in results
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# BIGQUERY
# ═══════════════════════════════════════════════════════════════════════════════

class BigQueryConnector(InfrastructureConnector):
    """Google BigQuery connector."""

    name = "bigquery"

    def __init__(self, project_id: str | None = None):
        self.project_id = project_id or os.environ.get("GCP_PROJECT_ID")
        self.base_url = "https://bigquery.googleapis.com/bigquery/v2"

    async def _get_token(self) -> str:
        import subprocess

        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        raise RuntimeError("Failed to get GCP token")

    async def query(self, sql: str) -> list[dict]:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/projects/{self.project_id}/queries",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": sql, "useLegacySql": False},
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("jobComplete"):
                # Poll for completion
                job_id = data["jobReference"]["jobId"]
                while True:
                    import asyncio
                    await asyncio.sleep(1)
                    status_response = await client.get(
                        f"{self.base_url}/projects/{self.project_id}/queries/{job_id}",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=30.0,
                    )
                    status_response.raise_for_status()
                    data = status_response.json()
                    if data.get("jobComplete"):
                        break

            schema = data.get("schema", {}).get("fields", [])
            columns = [field["name"] for field in schema]

            return [
                dict(zip(columns, [cell.get("v") for cell in row.get("f", [])]))
                for row in data.get("rows", [])
            ]

    async def list_datasets(self) -> list[CloudResource]:
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/projects/{self.project_id}/datasets",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                CloudResource(
                    id=ds["id"],
                    name=ds["datasetReference"]["datasetId"],
                    type="dataset",
                    raw=ds,
                )
                for ds in data.get("datasets", [])
            ]


# ═══════════════════════════════════════════════════════════════════════════════
# DATADOG
# ═══════════════════════════════════════════════════════════════════════════════

class DatadogConnector(InfrastructureConnector):
    """Datadog monitoring connector."""

    name = "datadog"

    def __init__(
        self,
        api_key: str | None = None,
        app_key: str | None = None,
        site: str = "datadoghq.com",
    ):
        self.api_key = api_key or os.environ.get("DATADOG_API_KEY")
        self.app_key = app_key or os.environ.get("DATADOG_APP_KEY")
        self.base_url = f"https://api.{site}/api/v1"

    async def get_metrics(self, query: str, start: int, end: int) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/query",
                headers={
                    "DD-API-KEY": self.api_key,
                    "DD-APPLICATION-KEY": self.app_key,
                },
                params={"query": query, "from": start, "to": end},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def list_monitors(self) -> list[dict]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/monitor",
                headers={
                    "DD-API-KEY": self.api_key,
                    "DD-APPLICATION-KEY": self.app_key,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def send_event(self, title: str, text: str, **kwargs) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/events",
                headers={
                    "DD-API-KEY": self.api_key,
                    "DD-APPLICATION-KEY": self.app_key,
                },
                json={"title": title, "text": text, **kwargs},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGERDUTY
# ═══════════════════════════════════════════════════════════════════════════════

class PagerDutyConnector(InfrastructureConnector):
    """PagerDuty incident management connector."""

    name = "pagerduty"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("PAGERDUTY_API_KEY")
        self.base_url = "https://api.pagerduty.com"

    async def list_incidents(self, status: str = "triggered,acknowledged") -> list[dict]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/incidents",
                headers={
                    "Authorization": f"Token token={self.api_key}",
                    "Content-Type": "application/json",
                },
                params={"statuses[]": status.split(",")},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json().get("incidents", [])

    async def create_incident(
        self,
        title: str,
        service_id: str,
        urgency: str = "high",
        **kwargs,
    ) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/incidents",
                headers={
                    "Authorization": f"Token token={self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "incident": {
                        "type": "incident",
                        "title": title,
                        "service": {"id": service_id, "type": "service_reference"},
                        "urgency": urgency,
                        **kwargs,
                    }
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json().get("incident", {})

    async def acknowledge_incident(self, incident_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{self.base_url}/incidents/{incident_id}",
                headers={
                    "Authorization": f"Token token={self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"incident": {"type": "incident", "status": "acknowledged"}},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json().get("incident", {})

    async def resolve_incident(self, incident_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{self.base_url}/incidents/{incident_id}",
                headers={
                    "Authorization": f"Token token={self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"incident": {"type": "incident", "status": "resolved"}},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json().get("incident", {})


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

INFRASTRUCTURE_CONNECTORS: dict[str, type[InfrastructureConnector]] = {
    "docker": DockerConnector,
    "aws": AWSConnector,
    "gcp": GCPConnector,
    "google_cloud": GCPConnector,
    "azure": AzureConnector,
    "tailscale": TailscaleConnector,
    "snowflake": SnowflakeConnector,
    "bigquery": BigQueryConnector,
    "datadog": DatadogConnector,
    "pagerduty": PagerDutyConnector,
}


def get_infrastructure_connector(name: str, **kwargs) -> InfrastructureConnector:
    """Get an infrastructure connector by name."""
    name = name.lower()

    if name not in INFRASTRUCTURE_CONNECTORS:
        available = ", ".join(sorted(INFRASTRUCTURE_CONNECTORS.keys()))
        raise ValueError(f"Unknown infrastructure connector: {name}. Available: {available}")

    return INFRASTRUCTURE_CONNECTORS[name](**kwargs)
