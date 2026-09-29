"""
Daily autonomous contribution script.
Uses a pre-defined rotating task list -- no API key needed.
Each task makes a real, small improvement to one of the repos.
"""

import subprocess
import os
import datetime
import sys
import tempfile
import shutil
import json

# Each entry: (repo, file_to_edit, description, patch_fn)
# patch_fn receives the current file content and returns new content

def patch_socioeconomic_readme(content):
    if "## Results" in content and "cross-validation" not in content:
        return content.replace(
            "## Results",
            "## Results\n\n> **Note:** Baseline scores below are from a single train/test split. "
            "Cross-validation (5-fold) is on the roadmap to confirm generalisability.\n"
        )
    return None

def patch_imdb_readme(content):
    if "## Usage" in content and "virtual environment" not in content.lower():
        return content.replace(
            "## Usage",
            "## Setup\n\n```bash\npython -m venv .venv && source .venv/bin/activate\npip install -r requirements.txt\n```\n\n## Usage"
        )
    return None

def patch_web_scraper_readme(content):
    if "## Usage" in content and "output to file" not in content.lower():
        return content.replace(
            "## Usage",
            "## Usage\n\n> **Tip:** pipe output to a file with `node cli.js <url> > output.txt`\n"
        )
    return None

def patch_london_readme(content):
    if "## Insights" in content and "source:" not in content.lower():
        return content.replace(
            "## Insights",
            "## Insights\n\n> Data sourced from Wikipedia (List of London boroughs). "
            "Population figures are from the most recent census available on that page.\n"
        )
    return None

def patch_lol_readme(content):
    if "## Usage" in content and "dataset" not in content.lower():
        return content.replace(
            "## Usage",
            "## Dataset\n\nChampion stats scraped from the official LoL wiki. "
            "Re-run `fetch_data.py` to refresh before analysis.\n\n## Usage"
        )
    return None

def patch_iot_readme(content):
    if "## Stack" in content and "ports" not in content.lower():
        return content.replace(
            "## Stack",
            "## Ports\n\n| Service | Port |\n|---|---|\n| Mosquitto (MQTT) | 1883 |\n"
            "| InfluxDB | 8086 |\n| Grafana | 3000 |\n\n## Stack"
        )
    return None

def patch_fabric_readme(content):
    if "## Local reproduction" in content and "minutes" not in content.lower():
        return content.replace(
            "## Local reproduction",
            "## Local reproduction\n\n> Runs in under 2 minutes on a standard laptop. "
            "No Fabric or cloud account needed.\n"
        )
    return None

def patch_infra_readme(content):
    if "## Usage" in content and "destroy" not in content.lower():
        return content.replace(
            "## Usage",
            "## Usage\n\n> **Cleanup:** run `make destroy` (or `terraform destroy`) "
            "to tear down all resources and avoid unexpected AWS charges.\n"
        )
    return None

def patch_data_lake_readme(content):
    if "## Local" in content and "duckdb" not in content.lower():
        return content.replace(
            "## Local",
            "## Local\n\n> Uses DuckDB as a local Snowflake substitute -- "
            "no cloud credentials required for the demo.\n"
        )
    return None

def patch_rest_api_readme(content):
    if "## Auth flow" in content and "revoke" not in content.lower():
        return content.replace(
            "## Auth flow",
            "## Auth flow\n\n> JWTs are signed with `JWT_SECRET` (set in `.env`). "
            "Tokens expire after 24 h. There is no revocation endpoint -- "
            "rotate the secret to invalidate all tokens at once.\n"
        )
    return None

def patch_r_analysis_readme(content):
    if "## Contents" in content and "knit" not in content.lower():
        return content.replace(
            "## Contents",
            "## Reproduce\n\nOpen `analysis.Rmd` in RStudio and click **Knit** "
            "to regenerate the full HTML report with all charts.\n\n## Contents"
        )
    return None

def patch_cicd_readme(content):
    if "## Usage" in content and "prerequisites" not in content.lower():
        return content.replace(
            "## Usage",
            "## Prerequisites\n\n- Docker and Docker Compose installed\n"
            "- Jenkins image pulled: `docker pull jenkins/jenkins:lts`\n\n## Usage"
        )
    return None

TASKS = [
    ("socioeconomic-ml",        "README.md", "docs: clarify that baseline scores are single-split, note CV roadmap",         patch_socioeconomic_readme),
    ("imdb-rating-nlp",         "README.md", "docs: add virtualenv setup block before Usage section",                         patch_imdb_readme),
    ("web-scraper-js",          "README.md", "docs: add tip about piping CLI output to a file",                               patch_web_scraper_readme),
    ("london-boroughs-dataviz", "README.md", "docs: add data source note to Insights section",                                patch_london_readme),
    ("lol-champion-analysis",   "README.md", "docs: add Dataset section explaining data origin",                              patch_lol_readme),
    ("iot-sensor-telemetry",    "README.md", "docs: add ports table to stack overview",                                       patch_iot_readme),
    ("fabric-movie-analytics",  "README.md", "docs: note that local pipeline runs in under 2 min",                           patch_fabric_readme),
    ("infrastructure-as-code",  "README.md", "docs: add cleanup warning to avoid unexpected AWS charges",                    patch_infra_readme),
    ("data-lake-project",       "README.md", "docs: note DuckDB as local Snowflake substitute",                              patch_data_lake_readme),
    ("rest-api-nodejs",         "README.md", "docs: document JWT expiry and secret-rotation revocation strategy",            patch_rest_api_readme),
    ("r-data-analysis",         "README.md", "docs: add Reproduce section with RStudio Knit instructions",                   patch_r_analysis_readme),
    ("cicd-jenkins-docker",     "README.md", "docs: add Prerequisites section listing Docker requirements",                  patch_cicd_readme),
]

def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)

def main():
    pat = os.environ.get("GH_PAT")
    if not pat:
        print("Missing GH_PAT", file=sys.stderr)
        sys.exit(1)

    today = datetime.date.today()
    day_index = today.timetuple().tm_yday
    repo_name, file_path, commit_message, patch_fn = TASKS[day_index % len(TASKS)]

    print(f"Target: {repo_name}/{file_path}")
    print(f"Commit: {commit_message}")

    tmpdir = tempfile.mkdtemp()
    try:
        clone_url = f"https://{pat}@github.com/Aziz-ba/{repo_name}.git"
        run(["git", "clone", "--depth", "1", clone_url, tmpdir])

        full_path = os.path.join(tmpdir, file_path)
        if not os.path.exists(full_path):
            print(f"{file_path} not found in {repo_name}, skipping.")
            sys.exit(0)

        with open(full_path) as f:
            original = f.read()

        new_content = patch_fn(original)
        if new_content is None or new_content == original:
            print("Patch already applied or not applicable, skipping.")
            sys.exit(0)

        with open(full_path, "w") as f:
            f.write(new_content)

        run(["git", "config", "user.name", "Aziz BENAYED"], cwd=tmpdir)
        run(["git", "config", "user.email", "benayedaziz23@gmail.com"], cwd=tmpdir)
        run(["git", "add", file_path], cwd=tmpdir)

        diff = run(["git", "diff", "--cached", "--stat"], cwd=tmpdir)
        if not diff.stdout.strip():
            print("No diff after patch, skipping.")
            sys.exit(0)

        run(["git", "commit", "-m", commit_message], cwd=tmpdir)
        run(["git", "push"], cwd=tmpdir)

        print(f"Done: pushed '{commit_message}' to {repo_name}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == "__main__":
    main()
