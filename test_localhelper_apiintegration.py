import os
#import asyncio --Removed this.
import logging
from dotenv import load_dotenv
from _automation_helpers.sk_azure_local import AzureLocalHelper  # adjust import path if needed

# Suppress Azure SDK and other library telemetry logs
logging.basicConfig(level=logging.WARNING)

# Load environment variables from .env file
#load_dotenv(".env")  # or just load_dotenv() if your file is named .env
load_dotenv()  # or just load_dotenv() if your file is named .env

# Pull secrets/config from environment
tenant_id = os.getenv("AZURE_TENANT_ID")
client_id = os.getenv("AZURE_CLIENT_ID")
client_secret = os.getenv("AZURE_CLIENT_SECRET")
subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
resource_group = os.getenv("AZURE_RESOURCE_GROUP")
vm_name = os.getenv("AZURE_VM_NAME")
base_url= os.getenv("AZURE_BASE_URL")
authority=os.getenv("AZURE_AUTHORITY")
azurestackhci_api_version=os.getenv("AZURESTACKHCI_API_VERSION")
hybridcompute_api_version=os.getenv("HYBRIDCOMPUTE_API_VERSION")
resourcegraph_api_version=os.getenv("RESOURCEGRAPH_API_VERSION")


# Initialize helper with config
config = {
    "tenant_id": tenant_id,
    "client_id": client_id,
    "client_secret": client_secret,
    "subscription_id": subscription_id,
    "base_url":base_url,
    "hybridcompute_api_version": hybridcompute_api_version,
    "azurestackhci_api_version": azurestackhci_api_version,
    "resourcegraph_api_version": resourcegraph_api_version,
    
}
helper = AzureLocalHelper(config=config)

def main():
    try:
        # --- Existence checks ---
        exists = helper.vm_exists(vm_name)
        print(f"vm_exists: {vm_name} -> {exists}")

        exists_specific = helper.vm_exists_specific(
            vm_name=vm_name,
            subscription_id=subscription_id,
            resource_group_name=resource_group
        )
        print(f"vm_exists_specific: {vm_name} -> {exists_specific}")

        # --- Details ---
        details = helper.get_vm_details(vm_name)
        safe_details = {
            "id": details.get("id"),
            "name": details.get("name"),
            "type": details.get("type"),
            "location": details.get("location"),
        }
        print(f"get_vm_details succeeded for VM: {vm_name}")

        details_specific = helper.get_vm_details_specific(
            vm_name=vm_name,
            subscription_id=subscription_id,
            resource_group_name=resource_group
        )
        print(f"get_vm_details_specific succeeded for VM: {vm_name}")

        # --- Power state ---
        power_state = helper.get_vm_power_state(
            vm_name=vm_name,
            subscription_id=subscription_id,
            resource_group_name=resource_group
        )
        state_value = power_state.get("state", "")
        print(f"get_vm_power_state: {vm_name} -> {state_value}")

        # --- Lifecycle operations ---
        print(f"Starting VM {vm_name}...")
        helper.start_vm(vm_name, subscription_id, resource_group, wait_time=300)
        print(f"VM {vm_name} started successfully.")

        print(f"Stopping VM {vm_name}...")
        helper.stop_vm(vm_name, subscription_id, resource_group)
    
        # Ensure stop completed before resize
        state = helper.get_vm_power_state(vm_name, subscription_id, resource_group)
        assert state["state"] in ["stopped", "deallocated"], f"VM not stopped, current state: {state}"
        print(f"VM {vm_name} stopped successfully.")


        print(f"Resizing VM {vm_name}...")
        helper.resize_vm(
            vm_name=vm_name,
            subscription_id=subscription_id,
            resource_group_name=resource_group,
            memory_mb=12288,
            wait_time=600
        )
        print(f"VM {vm_name} resized successfully.")


    except Exception as e:
        print(f"Integration test failed: {e}")

if __name__ == "__main__":
    main()
    #asyncio.run(main())--Removed this 
