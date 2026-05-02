import subprocess
import pathlib
import sys

def run_scripts():
    # 1. Locate the scripts directory
    scripts_dir = pathlib.Path("./scripts")
    
    # 2. Find all .py files (excluding this runner and __init__.py)
    script_files = sorted([
        f for f in scripts_dir.glob("*.py") 
        if f.name != "__init__.py"
    ])
    
    if not script_files:
        print("No scripts found in scripts/ folder.")
        return

    results = []
    print(f"🚀 Starting execution of {len(script_files)} scripts...\n")

    for script in script_files:
        print(f"Running: {script.name}...", end=" ", flush=True)
        
        # 3. Run the script using 'uv run' to ensure the environment is correct
        # This automatically handles the 'smartmdao' import issue we fixed earlier
        process = subprocess.run(
            ["uv", "run", str(script)],
            capture_output=True,
            text=True
        )
        
        if process.returncode == 0:
            print("✅ SUCCESS")
            results.append((script.name, "Pass"))
        else:
            print("❌ FAILED")
            # You can print process.stderr here if you want to see why it failed
            results.append((script.name, "Fail"))

    # 4. Final Status Report
    print("\n" + "="*30)
    print("📊 FINAL STATUS REPORT")
    print("="*30)
    for name, status in results:
        icon = "✅" if status == "Pass" else "❌"
        print(f"{icon} {name}: {status}")
    
    # Exit with error code if any script failed
    if any(status == "Fail" for _, status in results):
        sys.exit(1)

if __name__ == "__main__":
    run_scripts()