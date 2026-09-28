"""
Daily autonomous contribution script.
Picks a repo, reads its files, asks Claude to make one real improvement,
applies it, and pushes the commit.
"""

import anthropic
import subprocess
import os
import json
import datetime
import sys
import tempfile
import shutil

REPOS = [
    ("rest-api-nodejs",         "JavaScript/Node.js",  "Express REST API with JWT auth, SQLite, Jest tests, Swagger docs"),
    ("imdb-rating-nlp",         "Python",              "NLP ML study: TF-IDF and DistilBERT models to predict IMDb ratings"),
    ("fabric-movie-analytics",  "Python",              "Pandas medallion pipeline (Bronze/Silver/Gold) + Streamlit dashboard"),
    ("web-scraper-js",          "JavaScript/Node.js",  "Web scraper with retries, backoff, CLI, Jest tests"),
    ("data-lake-project",       "Python/SQL",          "Data lake with DuckDB local repro and Streamlit dashboard"),
    ("iot-sensor-telemetry",    "Python/Docker",       "IoT full stack: MQTT + InfluxDB + Grafana + sensor simulator"),
    ("r-data-analysis",         "R",                   "Statistical analysis report: regression, logistic, EDA"),
    ("socioeconomic-ml",        "Python",              "RandomForest on 100k French socioeconomic data, R2=0.683"),
    ("london-boroughs-dataviz", "Python",              "Wikipedia scraper + Streamlit/Plotly map of London boroughs"),
    ("lol-champion-analysis",   "Python",              "EDA on League of Legends champion stats"),
    ("infrastructure-as-code",  "Terraform/HCL",       "AWS infra with Terraform, GitHub Actions CI, Makefile"),
    ("cicd-jenkins-docker",     "Groovy/Docker",       "Jenkins + Docker + Kubernetes CI/CD pipeline for a Node app"),
]

IMPROVEMENT_TYPES = [
    "Add or improve a README section (usage example, architecture note, or badge)",
    "Add a missing docstring or inline comment explaining a non-obvious function",
    "Add one new test case covering an edge case or missing scenario",
    "Improve error messages or add input validation to an existing function",
    "Add a small utility function or helper that the project is clearly missing",
    "Fix a code smell: rename a confusing variable, remove dead code, simplify a condition",
    "Add a .gitignore entry or improve an existing config file",
]

def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)

def read_file(path, max_lines=120):
    try:
        with open(path) as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            half = max_lines // 2
            return "".join(lines[:half]) + f"\n... ({len(lines)-max_lines} lines omitted) ...\n" + "".join(lines[-half:])
        return "".join(lines)
    except Exception:
        return None

def collect_context(repo_dir, lang):
    """Read the most relevant files to give Claude context."""
    snippets = []
    priority = []

    if "JavaScript" in lang or "Node" in lang:
        priority = ["README.md", "package.json", "src", "tests", "*.js"]
    elif "Python" in lang:
        priority = ["README.md", "requirements.txt", "src", "*.py", "tests"]
    elif lang == "R":
        priority = ["README.md", "analysis.Rmd", "*.R"]
    elif "Terraform" in lang:
        priority = ["README.md", "main.tf", "variables.tf", "*.tf", "Makefile"]
    else:
        priority = ["README.md"]

    seen = set()
    for pat in priority:
        import glob
        matches = glob.glob(os.path.join(repo_dir, pat)) + \
                  glob.glob(os.path.join(repo_dir, "**", pat), recursive=True)
        for path in sorted(matches)[:4]:
            if path in seen or os.path.isdir(path):
                continue
            seen.add(path)
            content = read_file(path)
            if content:
                rel = os.path.relpath(path, repo_dir)
                snippets.append(f"=== {rel} ===\n{content}")
            if len(snippets) >= 6:
                break

    return "\n\n".join(snippets[:6])

def call_claude(client, repo_name, lang, description, context, improvement_type):
    today = datetime.date.today().isoformat()
    prompt = f"""You are making a real, small daily improvement to a GitHub project.

Project: {repo_name} ({lang})
Description: {description}
Today: {today}

Suggested improvement type: {improvement_type}

Current files:
{context}

---
Your task: make ONE small but genuinely useful improvement to this project.

Rules:
- The change must be real and add actual value (not whitespace, not empty content)
- Keep it small: 5-40 lines changed maximum
- The file must be valid syntax (no broken code)
- NEVER add fake data, fabricated metrics, or placeholder text like "TODO"
- Do not add comments that say "added by automation" or mention AI
- Write naturally as a developer improving their own project

Return ONLY a JSON object with these exact keys:
{{
  "file_path": "relative path from repo root, e.g. src/utils.js",
  "new_content": "the complete new content of that file",
  "commit_message": "a short conventional commit message, e.g. test: add edge case for empty input"
}}

Nothing else -- no explanation, no markdown fences, just the raw JSON object."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text.strip()

def main():
    pat = os.environ.get("GH_PAT")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not pat or not api_key:
        print("Missing GH_PAT or ANTHROPIC_API_KEY", file=sys.stderr)
        sys.exit(1)

    today = datetime.date.today()
    day_index = today.timetuple().tm_yday

    repo_name, lang, description = REPOS[day_index % len(REPOS)]
    improvement_type = IMPROVEMENT_TYPES[day_index % len(IMPROVEMENT_TYPES)]

    print(f"Target repo: {repo_name} | Improvement: {improvement_type}")

    tmpdir = tempfile.mkdtemp()
    try:
        clone_url = f"https://{pat}@github.com/Aziz-ba/{repo_name}.git"
        run(["git", "clone", "--depth", "1", clone_url, tmpdir])

        context = collect_context(tmpdir, lang)
        if not context:
            print("Could not read repo files, skipping.", file=sys.stderr)
            sys.exit(0)

        client = anthropic.Anthropic(api_key=api_key)
        raw = call_claude(client, repo_name, lang, description, context, improvement_type)

        # Strip markdown fences if Claude added them despite instructions
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])

        result = json.loads(raw)
        file_path = result["file_path"].lstrip("/")
        new_content = result["new_content"]
        commit_message = result["commit_message"]

        # Write the improved file
        full_path = os.path.join(tmpdir, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(new_content)

        run(["git", "config", "user.name", "Aziz BENAYED"], cwd=tmpdir)
        run(["git", "config", "user.email", "benayedaziz23@gmail.com"], cwd=tmpdir)
        run(["git", "add", file_path], cwd=tmpdir)

        # Check something actually changed
        diff = run(["git", "diff", "--cached", "--stat"], cwd=tmpdir)
        if not diff.stdout.strip():
            print("No changes detected, skipping commit.")
            sys.exit(0)

        run(["git", "commit", "-m", commit_message], cwd=tmpdir)
        run(["git", "push"], cwd=tmpdir)

        print(f"Pushed: {commit_message} -> {repo_name}/{file_path}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == "__main__":
    main()
