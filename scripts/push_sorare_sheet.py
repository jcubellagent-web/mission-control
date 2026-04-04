import json
import datetime
import subprocess
import os

SHEET_ID = "1EaGd-GNCrC_8bsMBHt9bPNJ-l28ITnsxB7FwUWrzzKg" # This needs to be the actual ID of your Google Sheet
GOG_PATH = "/opt/homebrew/bin/gog"
JAIN_SSH_TARGET = "jc_agent@100.121.89.84"
SHEET_UPDATE_JSON_PATH = "~/sorare_ml/data/sheet_update.json"

def get_last_logged_date():
    try:
        # Get the last row of the Daily Log to check the date
        cmd = [GOG_PATH, "sheets", "get", SHEET_ID, "Daily Log!A:A", "--plain"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = result.stdout.strip().split('\n')
        if len(lines) > 1: # More than just the header
            return lines[-1].strip() # Last date in column A
        return None
    except Exception as e:
        print(f"Error getting last logged date: {e}")
        return None

def push_data_to_sheet():
    today = datetime.date.today().strftime("%Y-%m-%d")
    last_logged_date = get_last_logged_date()

    if last_logged_date == today:
        print(f"Data for {today} already logged. Skipping update.")
        return

    try:
        # SSH to J.A.I.N and cat the JSON file
        ssh_command = f"ssh {JAIN_SSH_TARGET} 'cat {SHEET_UPDATE_JSON_PATH}'"
        print(f"Executing: {ssh_command}")
        ssh_result = subprocess.run(ssh_command, shell=True, capture_output=True, text=True, check=True)
        sheet_data = json.loads(ssh_result.stdout)
        print(f"Received data from J.A.I.N: {sheet_data}")

        # Format data for appending
        row_data = [
            sheet_data.get("Date", ""),
            sheet_data.get("Missions Submitted", ""),
            sheet_data.get("Missions Completed", ""),
            sheet_data.get("Competitions Entered", ""),
            sheet_data.get("Competitions Submitted", ""),
            sheet_data.get("Notes", ""),
            sheet_data.get("ML Bot Version", "")
        ]
        values_json = json.dumps([row_data])

        # Append to Google Sheet
        append_command = [
            GOG_PATH, "sheets", "append", SHEET_ID, "Daily Log!A:G",
            "--values-json", values_json
        ]
        print(f"Executing: {' '.join(append_command)}")
        append_result = subprocess.run(append_command, capture_output=True, text=True, check=True)
        print(f"Sheet update successful: {append_result.stdout}")

    except subprocess.CalledProcessError as e:
        print(f"Command failed: {e}")
        print(f"Stderr: {e.stderr}")
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON from J.A.I.N: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    push_data_to_sheet()
