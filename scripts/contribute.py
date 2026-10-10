"""
Daily contribution script.
Picks a repo, reads its real files, asks a free open-source model (Groq by
default, any OpenAI-compatible API works) for ONE small improvement as
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
import time
import urllib.error
import urllib.request

OWNER = "Aziz-ba"
# Any OpenAI-compatible endpoint works. Default: Groq free tier (open-source models).
# Override with LLM_URL and MODELS (comma-separated) env vars.
DEFAULT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "qwen/qwen3-32b",
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
    url = os.environ.get("LLM_URL") or DEFAULT_URL
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 4000,
        # Ask for a bare JSON answer; dropped below if the model rejects it.
        "response_format": {"type": "json_object"},
    }
    # Free tiers are often briefly overloaded: retry 500/503 with a pause.
    for wait in (15, 45, None):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "daily-contribution-script",
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                status, body = resp.status, resp.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code == 400 and "response_format" in payload:
                payload.pop("response_format")
                print(f"  {model}: no JSON mode, retrying without it")
                continue
            if e.code in (500, 502, 503) and wait:
                print(f"  {model}: HTTP {e.code}, retrying in {wait}s")
                time.sleep(wait)
                continue
            raise ValueError(f"HTTP {e.code}: {' '.join(e.read().decode('utf-8', 'replace').split())[:400]}")
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise ValueError(f"API reply not usable (HTTP {status}): {body[:300]!r}")
    if not content:
        raise ValueError(f"model returned empty content: {body[:300]!r}")
    return content


def available_models(token):
    """Ask the provider which chat models exist, so retired names don't break the run."""
    url = (os.environ.get("LLM_URL") or DEFAULT_URL).rsplit("/chat/completions", 1)[0] + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                               "User-Agent": "daily-contribution-script"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ids = [m["id"].removeprefix("models/") for m in json.load(resp).get("data", [])]
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Could not list models ({e}), using configured list as-is")
        return None
    skip = ("embed", "image", "tts", "audio", "live", "vision", "guard", "whisper", "veo", "imagen", "omni", "preview")
    return [m for m in ids if not any(s in m.lower() for s in skip)]


def pick_models(configured, available):
    if available is None:
        return configured
    chosen = [m for m in configured if m in available]
    # Fallbacks: newest fast models first, then anything else that can chat.
    extra = sorted((m for m in available if "flash" in m and "lite" not in m and m not in chosen), reverse=True)
    return (chosen + extra)[:4] or available[:4]


def parse_json(raw):
    # Drop reasoning blocks some models emit, then find the object with our keys.
    raw = re.sub(r"<(thought|think)>.*?</\1>", "", raw, flags=re.S)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(raw, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "file_path" in obj and "edits" in obj:
            return obj
    raise ValueError(f"model answer has no usable JSON: {raw[:300]!r}")


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
    elif rel.endswith(".tf") and shutil.which("terraform"):
        # Same checks as the repo's own CI: auto-format, then fmt -check and validate.
        run(["terraform", "fmt", rel], cwd=repo_dir)
        run(["terraform", "fmt", "-check", "-recursive"], cwd=repo_dir)
        run(["terraform", "init", "-backend=false", "-input=false"], cwd=repo_dir)
        run(["terraform", "validate"], cwd=repo_dir)
        shutil.rmtree(os.path.join(repo_dir, ".terraform"), ignore_errors=True)
        lock = os.path.join(repo_dir, ".terraform.lock.hcl")
        if run(["git", "ls-files", "--error-unmatch", ".terraform.lock.hcl"], cwd=repo_dir, check=False).returncode:
            if os.path.exists(lock):
                os.remove(lock)


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
        if os.environ.get("DRY_RUN") == "true":
            full = run(["git", "show", "--format=", "HEAD"], cwd=tmpdir).stdout
            summary(f"DRY RUN, not pushed: `{msg}` to **{repo_name}/{rel}** using `{model}`\n```diff\n{full}```")
            return True
        run(["git", "push"], cwd=tmpdir)
        summary(f"Pushed `{msg}` to **{repo_name}/{rel}** using `{model}`\n```\n{diff}```")
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def commits_today(pat):
    """Count Aziz's commits today (UTC) on default branches, i.e. what the graph shows."""
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    url = f"https://api.github.com/search/commits?q=author:{OWNER}+committer-date:{today}&per_page=1"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "daily-contribution-script",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp).get("total_count", 0)
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"Could not check today's commits ({e}), contributing anyway")
        return 0


def pat_days_left(pat):
    """Days until GH_PAT expires (GitHub sends this as a response header), None if it never expires."""
    req = urllib.request.Request("https://api.github.com/user", headers={
        "Authorization": f"Bearer {pat}", "User-Agent": "daily-contribution-script"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.headers.get("github-authentication-token-expiration")
    if not raw:
        return None
    # Format: "2026-12-31 23:59:59 UTC"
    expires = datetime.datetime.strptime(raw.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")
    return (expires - datetime.datetime.utcnow()).days


def main():
    pat = os.environ.get("GH_PAT")
    token = os.environ.get("LLM_API_KEY")
    if not pat or not token:
        sys.exit("Missing GH_PAT or LLM_API_KEY secret")

    try:
        days = pat_days_left(pat)
    except urllib.error.HTTPError as e:
        sys.exit(f"GH_PAT rejected by GitHub (HTTP {e.code}): it is expired or revoked, create a new one")
    print(f"GH_PAT expires in: {'never' if days is None else f'{days} days'}")
    # Fail the run (= email) two weeks ahead, but still contribute first.
    pat_warning = days is not None and days < 14

    # Several runs are scheduled per day; only the first that succeeds commits.
    # A manual run from the Actions tab always contributes.
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        done = commits_today(pat)
        if done:
            summary(f"Already {done} commit(s) today, nothing to do.")
            if pat_warning:
                sys.exit(f"GH_PAT expires in {days} days: renew it and run `gh secret set GH_PAT`")
            return
    models = [m.strip() for m in os.environ.get("MODELS", "").split(",") if m.strip()] or DEFAULT_MODELS
    models = pick_models(models, available_models(token))
    print(f"Models: {', '.join(models)}")

    now = datetime.datetime.now(datetime.timezone.utc)
    # Later retries in the same day start on different repos.
    start = now.timetuple().tm_yday + (now.hour // 4) * MAX_ATTEMPTS
    # Manual runs can target one repo (REPO input), e.g. to test it.
    pinned = os.environ.get("REPO")
    if pinned:
        start = next(i for i, r in enumerate(REPOS) if r[0] == pinned)
    for i in range(MAX_ATTEMPTS):
        repo_name, lang, description = REPOS[(start + i) % len(REPOS)]
        improvement_type = IMPROVEMENT_TYPES[(start + i) % len(IMPROVEMENT_TYPES)]
        print(f"Attempt {i + 1}: {repo_name} | {improvement_type}")
        try:
            if attempt(repo_name, lang, description, improvement_type, pat, token, models):
                if pat_warning:
                    sys.exit(f"Contributed, but GH_PAT expires in {days} days: renew it and run `gh secret set GH_PAT`")
                return
        except Exception as e:
            print(f"  failed: {e}")

    summary("No contribution pushed today: every attempt was rejected.")
    sys.exit(1)


if __name__ == "__main__":
    main()
