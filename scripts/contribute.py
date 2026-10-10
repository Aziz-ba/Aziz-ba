"""
Daily contribution script.
Picks a repo, reads its real files, asks a free model on GitHub Models
(open-source Llama first, fallbacks after) for ONE small improvement as
search/replace edits, validates it, then commits and pushes.

Exits with an error when nothing could be committed, so a skipped day
shows up as a red run instead of a silent green one.
"""

import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

OWNER = "Aziz-ba"
MODELS_URL = "https://models.github.ai/inference/chat/completions"
# Override with the MODELS env var (comma-separated). Open-source first.
DEFAULT_MODELS = [
    "meta/Llama-3.3-70B-Instruct",
    "mistral-ai/Codestral-2501",
    "openai/gpt-4.1-mini",
]
MAX_ATTEMPTS = 3

REPOS = [
    ("rest-api-nodejs",         "JavaScript/Node.js",  "Express REST API with JWT auth, SQLite, Jest tests, Swagger docs"),
    ("imdb-rating-nlp",         "Python",              "NLP ML study: TF-IDF and DistilBERT models to predict IMDb ratings"),
    ("fabric-movie-analytics",  "Python",              "Pandas medallion pipeline (Bronze/Silver/Gold) + Streamlit dashboard"),
    ("web-scraper-js",          "JavaScript/Node.js",  "Web scraper with retries, backoff, CLI, Jest tests"),
    ("iot-sensor-telemetry",    "Python/Docker",       "IoT full stack: MQTT + InfluxDB + Grafana + sensor simulator"),
    ("r-data-analysis",         "R",                   "Statistical analysis report: regression, logistic, EDA"),
    ("socioeconomic-ml",        "Python",              "RandomForest on 100k French socioeconomic data"),
    ("london-boroughs-dataviz", "Python",              "Wikipedia scraper + Streamlit/Plotly map of London boroughs"),
    ("lol-champion-analysis",   "Python",              "EDA on League of Legends champion stats"),
    ("infrastructure-as-code",  "Terraform/HCL",       "AWS infra with Terraform, GitHub Actions CI, Makefile"),
    ("cicd-jenkins-docker",     "Groovy/Docker",       "Jenkins + Docker + Kubernetes CI/CD pipeline for a Node app"),
]

IMPROVEMENT_TYPES = [
    "Add one new test case covering an edge case or missing scenario",
    "Improve error handling or input validation in an existing function",
    "Add a docstring or comment explaining a non-obvious function",
    "Improve a README section: a usage example or a clearer explanation of existing code",
    "Fix a code smell: rename a confusing variable, remove dead code, simplify a condition",
    "Improve a config file (.gitignore, CI workflow, Makefile, Dockerfile)",
]

PATTERNS = {
    "JavaScript": ["README.md", "package.json", "src/*.js", "*.js", "tests/*.js", "test/*.js"],
    "Python":     ["README.md", "requirements.txt", "src/*.py", "*.py", "tests/*.py"],
    "R":          ["README.md", "*.Rmd", "*.R"],
    "Terraform":  ["README.md", "*.tf", "Makefile", ".github/workflows/*.yml"],
    "Groovy":     ["README.md", "Jenkinsfile", "Dockerfile", "*.js", "k8s/*.yaml"],
}


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def read_file(path, max_lines=150):
    try:
        with open(path) as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return None
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines not shown) ...\n"]
    return "".join(lines)


def collect_context(repo_dir, lang):
    key = next((k for k in PATTERNS if k in lang), None)
    patterns = PATTERNS.get(key, ["README.md"])
    snippets, seen = [], set()
    for pat in patterns:
        for path in sorted(glob.glob(os.path.join(repo_dir, pat)))[:3]:
            rel = os.path.relpath(path, repo_dir)
            if rel in seen or os.path.isdir(path) or "node_modules" in rel:
                continue
            content = read_file(path)
            if content:
                seen.add(rel)
                snippets.append(f"=== {rel} ===\n{content}")
            if len(snippets) >= 7:
                return "\n\n".join(snippets), seen
    return "\n\n".join(snippets), seen


def build_prompt(repo_name, lang, description, context, improvement_type):
    return f"""You are improving a real GitHub project with one small, genuinely useful change.

Project: {repo_name} ({lang})
Description: {description}
Suggested improvement type: {improvement_type}

Current files:
{context}

Rules:
- ONE small change, 3-40 lines, in ONE file shown above (or one new test file).
- Only state facts you can see in the files above. Never invent metrics, timings,
  data sources, versions, or behaviour the code does not have.
- No placeholders, no TODOs, no mention of AI or automation.
- Code must stay syntactically valid.
- Each "search" string must be copied EXACTLY from the file, long enough to be unique.
  For a new file, use an empty "search" and put the whole file in "replace".

Return ONLY this JSON, no markdown fences:
{{"file_path": "path/from/repo/root", "edits": [{{"search": "...", "replace": "..."}}], "commit_message": "type: short conventional message"}}"""


def call_model(model, prompt, token):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 2500,
    }).encode()
    req = urllib.request.Request(MODELS_URL, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)["choices"][0]["message"]["content"]


def parse_json(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start:end + 1])


def apply_edits(repo_dir, result, allowed_files):
    rel = os.path.normpath(result["file_path"].lstrip("/"))
    if rel.startswith("..") or rel.startswith(".git" + os.sep):
        raise ValueError(f"refusing path {rel}")
    full = os.path.join(repo_dir, rel)
    exists = os.path.exists(full)
    if exists and rel not in allowed_files:
        raise ValueError(f"{rel} was not shown to the model")
    content = open(full).read() if exists else ""
    for edit in result["edits"]:
        search, replace = edit["search"], edit["replace"]
        if not search:
            if exists:
                raise ValueError("empty search on an existing file")
            content = replace
        elif content.count(search) != 1:
            raise ValueError(f"search text found {content.count(search)} times in {rel}")
        else:
            content = content.replace(search, replace)
    os.makedirs(os.path.dirname(full) or repo_dir, exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return rel


def validate(repo_dir, rel):
    full = os.path.join(repo_dir, rel)
    if rel.endswith(".py"):
        run([sys.executable, "-m", "py_compile", full])
    elif rel.endswith(".js"):
        run(["node", "--check", full])
        pkg = os.path.join(repo_dir, "package.json")
        if os.path.exists(pkg) and "test" in json.load(open(pkg)).get("scripts", {}):
            run(["npm", "ci", "--no-audit", "--no-fund"], cwd=repo_dir)
            run(["npm", "test"], cwd=repo_dir)
    elif rel.endswith(".json"):
        json.load(open(full))


def summary(line):
    print(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(line + "\n\n")


def attempt(repo_name, lang, description, improvement_type, pat, token, models):
    tmpdir = tempfile.mkdtemp()
    try:
        run(["git", "clone", "--depth", "1", f"https://{pat}@github.com/{OWNER}/{repo_name}.git", tmpdir])
        context, allowed = collect_context(tmpdir, lang)
        if not context:
            raise ValueError("no readable files")
        prompt = build_prompt(repo_name, lang, description, context, improvement_type)

        last_err = None
        for model in models:
            try:
                result = parse_json(call_model(model, prompt, token))
                rel = apply_edits(tmpdir, result, allowed)
                validate(tmpdir, rel)
                break
            except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError,
                    subprocess.CalledProcessError, OSError) as e:
                detail = getattr(e, "stderr", "") or ""
                last_err = f"{model}: {e} {detail[-300:]}"
                print(f"  rejected -> {last_err}")
                run(["git", "checkout", "--", "."], cwd=tmpdir)
                run(["git", "clean", "-fdq"], cwd=tmpdir)
        else:
            raise RuntimeError(last_err)

        run(["git", "config", "user.name", "Aziz BENAYED"], cwd=tmpdir)
        run(["git", "config", "user.email", "benayedaziz23@gmail.com"], cwd=tmpdir)
        run(["git", "add", rel], cwd=tmpdir)
        if not run(["git", "diff", "--cached", "--stat"], cwd=tmpdir).stdout.strip():
            raise RuntimeError("model produced no diff")
        msg = result["commit_message"].strip().splitlines()[0][:72]
        run(["git", "commit", "-m", msg], cwd=tmpdir)
        diff = run(["git", "show", "--stat", "--format=", "HEAD"], cwd=tmpdir).stdout
        run(["git", "push"], cwd=tmpdir)
        summary(f"Pushed `{msg}` to **{repo_name}/{rel}** using `{model}`\n```\n{diff}```")
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    pat = os.environ.get("GH_PAT")
    token = os.environ.get("MODELS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not pat or not token:
        sys.exit("Missing GH_PAT or GITHUB_TOKEN")
    models = [m.strip() for m in os.environ.get("MODELS", "").split(",") if m.strip()] or DEFAULT_MODELS

    day = datetime.date.today().timetuple().tm_yday
    for i in range(MAX_ATTEMPTS):
        repo_name, lang, description = REPOS[(day + i) % len(REPOS)]
        improvement_type = IMPROVEMENT_TYPES[(day + i) % len(IMPROVEMENT_TYPES)]
        print(f"Attempt {i + 1}: {repo_name} | {improvement_type}")
        try:
            if attempt(repo_name, lang, description, improvement_type, pat, token, models):
                return
        except Exception as e:
            print(f"  failed: {e}")

    summary("No contribution pushed today: every attempt was rejected.")
    sys.exit(1)


if __name__ == "__main__":
    main()
