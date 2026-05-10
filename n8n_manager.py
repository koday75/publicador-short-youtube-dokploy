import os
import sys
import json
import argparse
import requests
from requests.exceptions import RequestException

# Configuration
N8N_URL = "https://n8n.estrellitastudio.es"
API_VERSION = "v1"

# We will read the API Key from an environment variable or a local config file
CONFIG_FILE = ".n8n_config.json"

def get_api_key():
    # Try environment variable first
    api_key = os.environ.get("N8N_API_KEY")
    if api_key:
        return api_key
    
    # Fallback to local config file
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                return config.get("api_key")
        except Exception:
            pass
    return None

def save_api_key(api_key):
    with open(CONFIG_FILE, 'w') as f:
        json.dump({"api_key": api_key}, f, indent=4)
    print(f"API Key saved to {CONFIG_FILE} (DO NOT share this file!)")

def get_headers(api_key):
    return {
        "X-N8N-API-KEY": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

def print_error(msg, e=None):
    print(f"\n[ERROR] {msg}")
    if e:
        print(f"Details: {e}")
    sys.exit(1)

def test_connection(api_key):
    print(f"Testing connection to {N8N_URL}...")
    headers = get_headers(api_key)
    try:
        # We ping the workflows endpoint with a small limit just to test
        response = requests.get(f"{N8N_URL}/api/{API_VERSION}/workflows", headers=headers, params={"limit": 1}, timeout=10)
        
        if response.status_code == 200:
            print("[SUCCESS] Connected to n8n Public API!")
            return True
        elif response.status_code == 401 or response.status_code == 403:
            print("[ERROR] Authentication failed. Please check your API Key.")
            return False
        else:
            print(f"[ERROR] Unexpected status code: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except RequestException as e:
        print_error("Failed to connect. Network error or timeout.", e)

def list_workflows(api_key):
    headers = get_headers(api_key)
    try:
        print("\nFetching workflows...")
        response = requests.get(f"{N8N_URL}/api/{API_VERSION}/workflows", headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        workflows = data.get("data", [])
        if not workflows:
            print("No workflows found.")
            return
            
        print(f"\nFound {len(workflows)} workflows:")
        print("-" * 50)
        for wf in workflows:
            wf_id = wf.get("id")
            wf_name = wf.get("name")
            wf_active = "Active" if wf.get("active") else "Inactive"
            print(f"[{wf_id}] {wf_name} ({wf_active})")
        print("-" * 50)
        
    except requests.exceptions.HTTPError as e:
        print_error(f"HTTP Error: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print_error("Failed to list workflows.", e)

def create_workflow(api_key, filepath):
    if not os.path.exists(filepath):
        print_error(f"File not found: {filepath}")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            workflow_data = json.load(f)
    except json.JSONDecodeError:
        print_error(f"Invalid JSON format in file: {filepath}")
    except Exception as e:
        print_error(f"Error reading file {filepath}", e)

    headers = get_headers(api_key)
    
    # n8n expects the workflow structure at the root, 
    # but sometimes exports wrap it. 
    # For now, we assume the JSON is exactly what the API expects.
    
    print(f"\nCreating workflow from {filepath}...")
    try:
        response = requests.post(f"{N8N_URL}/api/{API_VERSION}/workflows", headers=headers, json=workflow_data, timeout=15)
        
        if response.status_code == 200 or response.status_code == 201:
            data = response.json()
            wf_id = data.get('id')
            print(f"[SUCCESS] Workflow created successfully! ID: {wf_id}")
        else:
            print_error(f"Failed to create workflow. Status: {response.status_code}\nResponse: {response.text}")
            
    except Exception as e:
        print_error("Failed to create workflow via API.", e)

def update_workflow(api_key, wf_id, filepath):
    if not os.path.exists(filepath):
        print_error(f"File not found: {filepath}")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            workflow_data = json.load(f)
    except json.JSONDecodeError:
        print_error(f"Invalid JSON format in file: {filepath}")
    except Exception as e:
        print_error(f"Error reading file {filepath}", e)

    headers = get_headers(api_key)
    
    print(f"\nUpdating workflow {wf_id} from {filepath}...")
    try:
        # Trying PUT since PATCH failed with 405
        response = requests.put(f"{N8N_URL}/api/{API_VERSION}/workflows/{wf_id}", headers=headers, json=workflow_data, timeout=15)
        
        if response.status_code == 200:
            print(f"[SUCCESS] Workflow {wf_id} updated successfully!")
        else:
            print_error(f"Failed to update workflow. Status: {response.status_code}\nResponse: {response.text}")
            
    except Exception as e:
        print_error("Failed to update workflow via API.", e)

def main():
    parser = argparse.ArgumentParser(description="n8n Workflow Manager Bridge")
    parser.add_argument("--set-key", help="Save the n8n API Key locally for future use")
    parser.add_argument("action", nargs="?", choices=["test", "list", "create", "update"], help="Action to perform")
    parser.add_argument("file", nargs="?", help="JSON file path (required for 'create'/'update' action)")
    parser.add_argument("--id", help="Workflow ID (required for 'update' action)")
    
    args = parser.parse_args()

    if args.set_key:
        save_api_key(args.set_key)
        return

    api_key = get_api_key()
    if not api_key:
        print_error("API key is not set. Please run: python n8n_manager.py --set-key YOUR_API_KEY")

    if not args.action:
        parser.print_help()
        return

    if args.action == "test":
        test_connection(api_key)
    elif args.action == "list":
        list_workflows(api_key)
    elif args.action == "create":
        if not args.file:
            print_error("Please provide the path to the workflow JSON file to create.")
        create_workflow(api_key, args.file)
    elif args.action == "update":
        if not args.file or not args.id:
            print_error("Update requires both --id WORKFLOW_ID and the file path.")
        update_workflow(api_key, args.id, args.file)

if __name__ == "__main__":
    main()
