#import asyncio
import time
import requests
from azure.identity import ClientSecretCredential
from automation_helpers import models
from automation_helpers.sk_logging import log


class AzureLocalHelper:
    """
    Helper class for managing Azure Local (Azure Stack HCI / Edge) VMs using REST API.
    Supports VM lifecycle operations and status retrieval.
    """

    def __init__(self, config: dict):
        try:
            self.config = models.AzureLocalConfig(**config)
            self.api_versions = {
                "hybridcompute": self.config.hybridcompute_api_version,
                "azurestackhci": self.config.azurestackhci_api_version,
                "resourcegraph": self.config.resourcegraph_api_version,
            }

            self.credential = ClientSecretCredential(
                tenant_id=self.config.tenant_id,
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
            )
            log.info("AzureLocalHelper initialized successfully.")
        except Exception as e:
            raise models.HelperError(
                models.ErrorDataBase(
                    message="Failed to initialize AzureLocalHelper",
                    traceback=str(e),
                )
            )

    def _get_access_token(self) -> str:
        try:
            scope = f"{self.config.base_url}/.default"
            token = self.credential.get_token(scope)
            return token.token
        except Exception as e:
            raise models.HelperError(
                models.ErrorDataBase(
                    message="Failed to get Azure token",
                    traceback=str(e),
                )
            )

    def _make_request(self, method: str, url: str, json_data: dict = None) -> dict:
        """
        Normalized request wrapper. Always returns dict with status_code, headers, body.
        """
        try:
            headers = {
                "Authorization": f"Bearer {self._get_access_token()}",
                "Content-Type": "application/json",
            }

            log.info(f"Making {method} request to {url}")
            response = requests.request(method, url, headers=headers, json=json_data)

            if response.status_code == 404:
                return {
                    "status_code": 404,
                    "headers": dict(response.headers),
                    "body": response.text,
                }

            if response.status_code >= 400:
                log.error(f"Azure API Error: {response.text}")
                raise models.HelperError(
                    models.ErrorDataBase(
                        message=f"HTTP {response.status_code} Error",
                        traceback=response.text,
                    )
                )

            try:
                body = response.json()
            except Exception:
                body = response.text

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body,
            }
        except models.HelperError:
            raise
        except Exception as e:
            raise models.HelperError(
                models.ErrorDataBase(
                    message="Request failed",
                    traceback=str(e),
                )
            )

    def _poll_operation(self, operation_url: str, timeout: int = 1800, interval: int = 30) -> dict:
        """
        Polls an Azure async operation until completion or timeout.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            status_response = self._make_request("GET", operation_url)
            body = status_response.get("body", {})

            if isinstance(body, dict):
                status = body.get("status") or body.get("properties", {}).get("provisioningState")
            else:
                status = None

            if status in ["Succeeded", "Failed", "Canceled"]:
                return status_response

            time.sleep(interval)

        raise models.HelperError(
            models.ErrorDataBase(
                message="Operation polling timed out",
                traceback=f"Timeout after {timeout} seconds",
            )
        )

    def _build_url(self, path: str, service: str) -> str:
        api_version = self.api_versions.get(service)
        return f"{self.config.base_url}{path}?api-version={api_version}"

    # -------------------- VM Existence & Details --------------------

    def vm_exists(self, vm_name: str) -> bool:
        """Return True if VM exists, else False."""
        path = "/providers/Microsoft.ResourceGraph/resources"
        url = self._build_url(path, "resourcegraph")
        query = {
            "query": (
                f"Resources "
                f"| where type has 'microsoft.hybridcompute/machines' "
                f"| where tolower(name) == tolower('{vm_name}') "
                "| limit 1"
            ),
        }
        try:
            response = self._make_request("POST", url, json_data=query)
        except models.HelperError as e:
            log.warning(f"Resource Graph query failed: {e}")
            return False

        body = response.get("body", {})
        return body.get("count", 0) > 0

    def vm_exists_specific(self, vm_name: str, subscription_id: str, resource_group_name: str) -> bool:
        """
        Check if a VM exists in a specific subscription and resource group.
        Tries HybridCompute provider.
        Returns True if VM exists (HTTP 200), False if not found (HTTP 404).
        Raises HelperError for other unexpected status codes.
        """
        path = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
            f"/providers/Microsoft.HybridCompute/machines/{vm_name}"
        )
        url = self._build_url(path, "hybridcompute")

        response = self._make_request("GET", url)

        if response["status_code"] == 200:
            log.info(f"VM {vm_name} found via hybridcompute provider.")
            return True
        elif response["status_code"] == 404:
            log.info(f"VM {vm_name} not found via hybridcompute provider.")
            return False
        else:
            log.error(f"Unexpected response: {response['status_code']}")
            return False

    def get_vm_details(self, vm_name: str) -> dict:
        """Return VM details from Resource Graph."""
        path = "/providers/Microsoft.ResourceGraph/resources"
        url = self._build_url(path, "resourcegraph")

        query = {
            "query": (
                "Resources "
                "| where type =~ 'microsoft.hybridcompute/machines' "
                f"| where tolower(name) == tolower('{vm_name}')"
            ),
        }

        try:
            response = self._make_request("POST", url, json_data=query)
        except models.HelperError:
            raise
        except Exception as e:
            raise models.HelperError(
                models.ErrorDataBase(
                    message=f"Failed to query Resource Graph for VM '{vm_name}'",
                    traceback=str(e),
                )
            )

        body = response.get("body", {})
        results = body.get("data", [])

        if len(results) == 0:
            raise models.HelperError(
                models.ErrorDataBase(
                    message="VM not found",
                    traceback=vm_name,
                )
            )
        if len(results) > 1:
            raise models.HelperError(
                models.ErrorDataBase(
                    message="Multiple VMs found",
                    traceback=vm_name,
                )
            )

        vm = results[0]
        return {
            "id": vm.get("id"),
            "name": vm.get("name"),
            "type": vm.get("type"),
            "location": vm.get("location"),
            "resourceGroup": vm.get("resourceGroup"),
            "subscriptionId": vm.get("subscriptionId"),
            "properties": vm.get("properties", {}),
            "tags": vm.get("tags", {}),
        }

    def get_vm_details_specific(self, vm_name: str, subscription_id: str, resource_group_name: str) -> dict:
        """
        Get VM details when subscription and resource group are known using direct REST API.
        Returns the same structure as get_vm_details.
        """
        path = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
            f"/providers/Microsoft.HybridCompute/machines/{vm_name}"
        )
        url = self._build_url(path, "hybridcompute")

        try:
            response = self._make_request("GET", url)
            if not response:
                raise models.HelperError(
                    models.ErrorDataBase(
                        message="VM not found",
                        traceback=vm_name,
                    )
                )
            vm = response.get("body", {})
            return vm
        except models.HelperError:
            raise
        except Exception as e:
            raise models.HelperError(
                models.ErrorDataBase(
                    message="Failed to retrieve VM details",
                    traceback=str(e),
                )
            )

    # -------------------- VM Power State --------------------

    def get_vm_power_state(self, vm_name: str, subscription_id: str, resource_group_name: str) -> dict:
        """
        Get VM power state from Azure Local VM API.
        Returns dict with keys: state, raw, provider.
        JSON path analyzed: properties.status.powerState
        """
        # First Try HybridCompute
        path = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
            f"/providers/Microsoft.HybridCompute/machines/{vm_name}"
        )
        url = self._build_url(path, "hybridcompute")
        response = self._make_request("GET", url)
        body = response.get("body", {})

        raw_state = body.get("properties", {}).get("status", {}).get("powerState")
        provider = "HybridCompute"

        # Fallback to AzureStackHCI if HybridCompute doesn't expose powerState
        if not raw_state:
            path = (
                f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
                f"/providers/Microsoft.HybridCompute/machines/{vm_name}"
                f"/providers/Microsoft.AzureStackHCI/virtualMachineInstances/default"
            )
            url = self._build_url(path, "azurestackhci")
            response = self._make_request("GET", url)
            body = response.get("body", {})
            raw_state = body.get("properties", {}).get("status", {}).get("powerState")
            provider = "AzureStackHCI"

        state_map = {
            "PowerState/running": "running",
            "PowerState/stopped": "stopped",
            "PowerState/deallocated": "deallocated",
            "Running": "running",
            "Stopped": "stopped",
            "Deallocated": "deallocated",
            None: "unknown",
            "AwaitingConnection": "unknown",
        }

        normalized = state_map.get(raw_state, "unknown")

        result = {
            "state": normalized,
            "raw": raw_state,
            "provider": provider,
        }

        log.info("VM %s power state: %s via %s", vm_name, normalized, provider)
        return result

    # -------------------- VM Lifecycle Operations --------------------

    def start_vm(self, vm_name: str, subscription_id: str, resource_group_name: str, wait_time: int = 300) -> None:
        """
        Start an Azure Local VM.
        - Sends POST to the AzureStackHCI start endpoint
        - Polls every 10s until VM state == "running"
        - Handles already running VM gracefully
        - Raises HelperError on failure
        """
        current_state = self.get_vm_power_state(vm_name, subscription_id, resource_group_name)
        if current_state["state"] == "running":
            log.info(f"VM {vm_name} is already running.")
            return None

        path = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
            f"/providers/Microsoft.HybridCompute/machines/{vm_name}"
            f"/providers/Microsoft.AzureStackHCI/virtualMachineInstances/default/start"
        )
        url = self._build_url(path, "azurestackhci")

        log.info(f"Sending start request for VM {vm_name}...")
        self._make_request("POST", url)

        # Poll until VM reaches 'running'
        start_time = time.time()
        while time.time() - start_time < wait_time:
            state = self.get_vm_power_state(vm_name, subscription_id, resource_group_name)
            if state["state"] == "running":
                log.info(f"VM {vm_name} successfully started.")
                return None
            time.sleep(10)

        raise models.HelperError(
            models.ErrorDataBase(
                message="Start operation timed out",
                traceback=f"VM {vm_name} did not reach 'running' within {wait_time} seconds",
            )
        )

    def stop_vm(self, vm_name: str, subscription_id: str, resource_group_name: str, wait_time: int = 300) -> None:
        """
        Stop an Azure Local VM.
        - Sends POST to the AzureStackHCI stop endpoint
        - Polls every 10s until VM state == "stopped" or "deallocated"
        - Handles already stopped/deallocated VM gracefully
        - Raises HelperError on failure
        """
        current_state = self.get_vm_power_state(vm_name, subscription_id, resource_group_name)
        if current_state["state"] in ["stopped", "deallocated"]:
            log.info(f"VM {vm_name} is already {current_state['state']}.")
            return None

        path = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
            f"/providers/Microsoft.HybridCompute/machines/{vm_name}"
            f"/providers/Microsoft.AzureStackHCI/virtualMachineInstances/default/stop"
        )
        url = self._build_url(path, "azurestackhci")

        log.info(f"Sending stop request for VM {vm_name}...")
        self._make_request("POST", url)

        # Poll until VM reaches 'stopped' or 'deallocated'
        start_time = time.time()
        while time.time() - start_time < wait_time:
            state = self.get_vm_power_state(vm_name, subscription_id, resource_group_name)
            if state["state"] in ["stopped", "deallocated"]:
                log.info(f"VM {vm_name} successfully stopped.")
                return None
            time.sleep(10)

        raise models.HelperError(
            models.ErrorDataBase(
                message="Stop operation timed out",
                traceback=f"VM {vm_name} did not reach 'stopped' or 'deallocated' within {wait_time} seconds",
            )
        )

    def restart_vm(self, vm_name: str, subscription_id: str, resource_group_name: str, wait_time: int = 300) -> None:
        """
        Restart an Azure Local VM.
        - Sends POST to the AzureStackHCI restart endpoint
        - Polls every 10s until VM state == "running"
        - Raises HelperError on failure
        """
        path = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
            f"/providers/Microsoft.HybridCompute/machines/{vm_name}"
            f"/providers/Microsoft.AzureStackHCI/virtualMachineInstances/default/restart"
        )
        url = self._build_url(path, "azurestackhci")

        log.info(f"Sending restart request for VM {vm_name}...")
        self._make_request("POST", url)

        # Poll until VM reaches 'running'
        start_time = time.time()
        while time.time() - start_time < wait_time:
            state = self.get_vm_power_state(vm_name, subscription_id, resource_group_name)
            if state["state"] == "running":
                log.info(f"VM {vm_name} successfully restarted.")
                return None
            time.sleep(10)

        raise models.HelperError(
            models.ErrorDataBase(
                message="Restart operation timed out",
                traceback=f"VM {vm_name} did not reach 'running' within {wait_time} seconds",
            )
        )

    def resize_vm(self, vm_name: str, subscription_id: str, resource_group_name: str,
                  memory_mb: int, wait_time: int = 600) -> dict:
        """
        Resize VM hardware configuration memory using AzureStackHCI API.

        - VM must be stopped/deallocated before resize
        - Polls async operation until status == "Succeeded" or timeout
        """
        current_state = self.get_vm_power_state(vm_name, subscription_id, resource_group_name)
        if current_state["state"] not in ["stopped", "deallocated"]:
            raise models.HelperError(
                models.ErrorDataBase(
                    message="Resize not allowed",
                    traceback=f"VM {vm_name} must be stopped/deallocated before resize (current state: {current_state})",
                )
            )

        path = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}"
            f"/providers/Microsoft.HybridCompute/machines/{vm_name}"
            f"/providers/Microsoft.AzureStackHCI/virtualMachineInstances/default"
        )
        url = self._build_url(path, "azurestackhci")

        body = {
            "properties": {
                "hardwareProfile": {
                    "memoryMB": memory_mb,
                }
            }
        }

        log.info(f"Resizing VM {vm_name} to {memory_mb} MB RAM")

        resp = self._make_request("PATCH", url, json_data=body)

        operation_url = (
            resp["headers"].get("Azure-AsyncOperation")
            or resp["headers"].get("Location")
        )

        if operation_url:
            status_response = self._poll_operation(operation_url, timeout=wait_time, interval=30)
            poll_body = status_response.get("body", {})
            if isinstance(poll_body, dict):
                status = poll_body.get("status") or poll_body.get("properties", {}).get("provisioningState")
            else:
                status = None

            if status != "Succeeded":
                raise models.HelperError(
                    models.ErrorDataBase(
                        message="Resize failed",
                        traceback=str(status_response),
                    )
                )
        else:
            log.warning("No operation URL returned; resize may have completed synchronously.")

        return self.get_vm_power_state(vm_name, subscription_id, resource_group_name)
