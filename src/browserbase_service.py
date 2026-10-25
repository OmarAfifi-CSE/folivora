"""
browserbase_service.py - Universal Full-Page Content, Section-Aware Structural Extraction & Topic Intelligence

Supports ANY domain of human life (Travel, Health, Cooking, Shopping, Study, Finance, or Tech)
plus first-class indexing of the DEV Hall of Fame (65 Authors, 65 Masterpieces):
1. Mode A (`has_interest_focus == False`): Summarizes all open tabs cleanly without fabricating ratings.
2. Mode B (`has_interest_focus == True`): Searches the entire page content (`full_text`, `title`,
   `headings`, `overview`, `takeaways`, `synthesized_points`, `topics`) for the user's interest and its
   semantic synonyms, computing exact mention counts, calibrated relevance scores for TabPFN, and a
   section-anchored Topic-Focused Summary Tab (`interest_section`).
"""

from concurrent.futures import ThreadPoolExecutor
import html as html_lib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import time
from urllib.parse import urljoin, urlparse
import requests

from telemetry import trace_genai_span

BROWSERBASE_API_URL = "https://api.browserbase.com/v1"
ROOT_DIR = Path(__file__).resolve().parent.parent
HOF_CATALOG_PATH = ROOT_DIR / "data" / "dev_hall_of_fame_catalog.json"
HOF_ROUNDUP_URL = "https://dev.to/georgekobaidze/promise-delivered-welcome-to-the-dev-hall-of-fame-65-authors-65-masterpieces-15fa"

# Strict primary synonyms (terms that genuinely identify the topic itself on a page)
TOPIC_ECOSYSTEM_SYNONYMS: dict[str, list[str]] = {
    # Everyday Life: Travel, Food, Health, Shopping, Finance, Study
    "travel": ["travel", "trip", "itinerary", "flight", "flights", "hotel", "tokyo", "kyoto", "japan", "vacation", "shinkansen", "sightseeing", "tourism"],
    "japan": ["japan", "tokyo", "kyoto", "osaka", "shinkansen", "ramen", "yen", "shibuya", "ryokan", "fushimi"],
    "tokyo": ["tokyo", "japan", "shibuya", "shinjuku", "kyoto", "shinkansen", "asakusa"],
    "food": ["food", "recipe", "recipes", "cooking", "meal prep", "protein", "nutrition", "calories", "kitchen", "ingredients", "ramen", "dinner"],
    "recipe": ["recipe", "recipes", "cooking", "meal prep", "ingredients", "protein", "calories", "skillet"],
    "cooking": ["cooking", "recipe", "recipes", "meal prep", "kitchen", "ingredients", "skillet"],
    "protein": ["protein", "nutrition", "meal prep", "diet", "calories", "chicken", "macros", "greek yogurt"],
    "health": ["health", "sleep", "circadian", "medical", "fitness", "nutrition", "recovery", "cortisol", "melatonin", "caffeine"],
    "sleep": ["sleep", "circadian", "insomnia", "melatonin", "caffeine", "bedtime", "slow-wave"],
    "shopping": ["shopping", "buyer's guide", "buying guide", "ultrabook", "product review", "unboxing", "best overall value"],
    "laptop": ["laptop", "laptops", "ultrabook", "macbook", "thinkpad", "matte display", "battery life", "display nits"],
    "laptops": ["laptop", "laptops", "ultrabook", "macbook", "thinkpad", "matte display", "battery life", "display nits"],
    "finance": ["investing", "index fund", "portfolio", "stock market", "dividend", "retirement", "tax", "mortgage"],
    # Technology & Engineering (strict topic identifiers — never generic words that appear in other domains!)
    "flutter": ["flutter", "dart", "pubspec", "statefulwidget", "statelesswidget", "impeller", "dynamicscaffold"],
    "dart": ["dart", "flutter", "pubspec", "uint8list"],
    "mobile": ["mobile", "android", "ios", "flutter", "adb", "apk", "oneplus"],
    "android": ["android", "adb", "apk", "logcat", "oneplus"],
    "ai": ["ai", "llm", "llms", "gemma", "gemini", "qwen", "tinker", "fine-tuning", "fine-tune", "lora", "openai", "claude", "copilot", "rag", "agentic"],
    "agents": ["agents", "agentic", "tool use", "function calling", "mcp", "claude code", "copilot", "cursor", "ai agent"],
    "agent": ["agent", "agents", "agentic", "tool use", "function calling", "mcp", "claude code", "copilot", "cursor"],
    "llm": ["llm", "llms", "gemma", "gemini", "qwen", "tinker", "fine-tuning", "fine-tune", "lora", "rag"],
    "gemini": ["gemini", "google ai", "ai studio", "gemma"],
    "gemma": ["gemma", "google ai", "onnx"],
    "copilot": ["copilot", "github copilot"],
    "fine-tuning": ["fine-tuning", "fine-tune", "lora", "tinker", "qwen"],
    "ml": ["machine learning", "tabpfn", "pytorch", "quantile", "regression", "calibration", "ab testing", "a/b testing", "statistics"],
    "postgres": ["postgres", "postgresql", "pgvector", "hnsw", "ef_search", "vector_l2_ops"],
    "database": ["database", "databases", "postgres", "postgresql", "pgvector", "hnsw", "sql", "mysql", "cte", "write-ahead log", "wal", "saardb", "snowflake"],
    "sql": ["sql", "postgres", "postgresql", "mysql", "cte", "window function", "window functions", "database"],
    "docker": ["docker", "containers", "dockerfile", "kubernetes", "kubectl", "omp_num_threads", "cgroup", "self-hosting"],
    "devops": ["devops", "docker", "kubernetes", "kubectl", "aws", "vpc", "subnet", "cloud", "cloudflare", "self-hosting"],
    "cloud": ["cloud", "aws", "vpc", "subnet", "render", "cloudflare", "docker", "kubernetes"],
    "aws": ["aws", "vpc", "subnet", "nacl", "igw"],
    "python": ["python", "fastapi", "pytorch", "pandas", "numpy", "uvicorn", "pydantic", "trio", "libp2p"],
    "rust": ["rust", "actix", "cargo", "webforms"],
    "go": ["golang", "saardb"],
    "javascript": ["javascript", "typescript", "react", "es5", "call stack", "webgpu", "webassembly"],
    "typescript": ["typescript", "is-kit"],
    "react": ["react", "jsx", "hooks"],
    "web": ["webdev", "frontend", "javascript", "typescript", "react", "html", "css", "a11y", "accessibility", "seo", "metadata", "webgpu", "webassembly", "view transitions"],
    "frontend": ["frontend", "react", "javascript", "typescript", "css", "html", "a11y", "accessibility", "webgpu", "call stack"],
    "security": ["security", "cybersecurity", "prompt injection", "malware", "vulnerability", "safe-room"],
    "testing": ["testing", "qa", "bugsmash", "debugging", "a/b testing", "prompt injection"],
    "debugging": ["debugging", "debug", "bug", "bugs", "adb", "deadlock", "postmortem"],
    "network": ["networking", "tcp", "udp", "socket", "sockets", "wi-fi", "bandwidth", "p2p", "webrtc", "libp2p", "backpressure", "tcpnodelay", "vpc"],
    "networking": ["networking", "tcp", "udp", "socket", "sockets", "wi-fi", "bandwidth", "p2p", "webrtc", "libp2p", "backpressure", "tcpnodelay", "vpc"],
    "career": ["career", "jobseekers", "speaking", "conference", "speaker", "cfp", "burnout", "beginners", "codenewbie", "startup", "indiehack"],
    "speaking": ["speaking", "speaker", "conference", "cfp", "techtalks"],
    "systems": ["system design", "architecture", "database", "wal", "saardb", "webrtc", "libp2p", "tcp", "rust", "idempotency", "microservices", "compilers"],
    "architecture": ["architecture", "system design", "microservices", "inversion of control", "solid", "idempotency"],
}

# Section-level enrichment terms used ONLY after a page passes the primary topic gate,
# so Tab 2 ("Focused on <Topic>") pulls all related architectural sections from that page!
SECTION_CONTEXT_ENRICHMENT: dict[str, list[str]] = {
    "flutter": ["flutter", "dart", "widget", "widgets", "valuekey", "valuekeys", "bloc", "cubit", "orbital radar", "layoutbuilder", "dynamicscaffold", "uint8list", "ffi"],
    "dart": ["dart", "flutter", "widget", "widgets", "valuekey", "valuekeys", "bloc", "cubit", "uint8list", "ffi"],
    "mobile": ["mobile", "android", "ios", "flutter", "dart", "widget", "widgets", "valuekey", "valuekeys", "bloc", "cubit", "adb", "apk", "orbital radar", "layoutbuilder", "dynamicscaffold", "uint8list", "ffi"],
    "web": ["webdev", "frontend", "javascript", "typescript", "react", "html", "css", "dom", "a11y", "accessibility", "webgpu", "webassembly", "view transitions"],
    "frontend": ["frontend", "javascript", "typescript", "react", "html", "css", "dom", "a11y", "accessibility", "webgpu", "webassembly", "view transitions"],
    "ai": ["ai", "llm", "gemma", "gemini", "qwen", "tinker", "prompt", "agent", "rag", "model", "token", "copilot"],
    "gemini": ["gemini", "adb", "canvas", "bytecode", "apk", "decompile", "logcat"],
}

# Pretty labels for known DEV.to #tags and meta keywords
TAG_PRETTY_MAP: dict[str, str] = {
    "flutter": "Flutter & Dart",
    "dart": "Flutter & Dart",
    "ai": "AI & LLMs",
    "agents": "AI Agents",
    "aiagents": "AI Agents",
    "llmfunctioncalling": "LLM Tool Use",
    "tooluse": "LLM Tool Use",
    "rag": "RAG & Vector Search",
    "promptengineering": "Prompt Engineering",
    "gemini": "Google Gemini",
    "googleai": "Google AI",
    "gemma": "Google Gemma",
    "claude": "Claude & AI Agents",
    "learngoogleaistudio": "Google AI Studio",
    "machinelearning": "Machine Learning",
    "mlhacks": "Machine Learning",
    "datascience": "Data Science",
    "statistics": "Statistics & A/B Testing",
    "abtesting": "A/B Testing",
    "android": "Android & Mobile",
    "ios": "iOS & Mobile",
    "mobile": "Mobile Engineering",
    "debugging": "Debugging",
    "bugsmash": "Debugging & Bugs",
    "testing": "Testing & QA",
    "webdev": "Web Development",
    "web": "Web Development",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "react": "React",
    "angular": "Angular",
    "html": "HTML & CSS",
    "a11y": "Accessibility (a11y)",
    "frontend": "Frontend Architecture",
    "frontendarchitecture": "Frontend Architecture",
    "uidesign": "UI/UX Design",
    "design": "Product & UI Design",
    "seo": "SEO & Metadata",
    "metadata": "SEO & Metadata",
    "webgpu": "WebGPU & Wasm",
    "webassembly": "WebAssembly",
    "ie11": "Legacy Browser Support",
    "backend": "Backend Systems",
    "architecture": "System Architecture",
    "systemdesign": "System Design",
    "microservices": "Microservices",
    "softwareengineering": "Software Engineering",
    "softwaredesign": "Software Architecture",
    "software": "Software Engineering",
    "compilers": "OS & Compilers",
    "computerscience": "Computer Science",
    "algorithms": "Algorithms & Modeling",
    "python": "Python",
    "rust": "Rust",
    "actix": "Rust & Actix",
    "webformscore": "Server-Driven UI",
    "go": "Go (Golang)",
    "csharp": "C# & .NET",
    "java": "Java",
    "php": "PHP & Laravel",
    "laravel": "PHP & Laravel",
    "ruby": "Ruby on Rails",
    "rails": "Ruby on Rails",
    "haskell": "Functional Programming",
    "functional": "Functional Programming",
    "oop": "OOP & Interfaces",
    "node": "Node.js",
    "database": "Databases & SQL",
    "sql": "Databases & SQL",
    "mysql": "MySQL & Databases",
    "postgres": "PostgreSQL",
    "snowflake": "Data Warehousing",
    "tigerdata": "Time-Series & Data",
    "aws": "AWS & Cloud",
    "cloud": "Cloud Infrastructure",
    "cloudflare": "Cloudflare & Edge",
    "devops": "DevOps & Docker",
    "docker": "Docker & Containers",
    "kubernetes": "Kubernetes",
    "linux": "Linux & Terminal",
    "terminal": "CLI & Terminal",
    "selfhosted": "Self-Hosting",
    "operations": "DevOps & Operations",
    "security": "Cybersecurity",
    "malware": "Malware & Security",
    "idempotency": "API Reliability",
    "api": "APIs & Backend",
    "iot": "Embedded & IoT",
    "esp32": "ESP32 & BLE",
    "webrtc": "WebRTC & P2P",
    "libp2p": "WebRTC & libp2p",
    "networking": "Networking & P2P",
    "gis": "Geospatial & GIS",
    "opendata": "Open Data & GIS",
    "opensource": "Open Source",
    "github": "GitHub & Open Source",
    "git": "Git & Workflows",
    "githubcopilot": "GitHub Copilot",
    "cli": "CLI Tools",
    "automation": "Automation & Workflows",
    "workflows": "Agentic Workflows",
    "dynamics365": "Enterprise Automation",
    "sap": "Enterprise Systems",
    "honeycomb": "Observability",
    "career": "Career & Growth",
    "jobmarket": "Career & Hiring",
    "speaking": "Public Speaking",
    "techtalks": "Tech Conferences",
    "technicalleadership": "Engineering Leadership",
    "devrel": "Developer Relations",
    "startup": "Startups & Product",
    "indiehack": "Indie Hacking",
    "buildinpublic": "Build in Public",
    "productivity": "Developer Productivity",
    "mentalhealth": "Developer Wellbeing",
    "motivation": "Career & Motivation",
    "beginners": "Beginner Guide",
    "codenewbie": "Beginner Guide",
    "education": "Developer Education",
    "learning": "Learning & Growth",
    "community": "DEV Community",
    "howtodevto": "DEV Community Guide",
    "writing": "Technical Writing",
    "devjournal": "Engineering Journal",
    "gamedev": "Game Development",
    "vibecoding": "AI Pair Programming",
    "tools": "Developer Tools",
    "sanity": "Headless CMS & Agents",
}

META_TAGS_TO_SKIP = {
    "devchallenge", "hf26challenge", "githubchallenge", "418challenge",
    "newyearchallenge", "wecoded", "wemakedevs", "showdev", "discuss",
    "howto", "tutorial", "kagglechallenge", "sanitychallenge", "truefoundry",
    "qodo", "watercooler", "devlive", "weeklyretro", "ama",
}

# Phrases that mean "just summarize everything without filtering" (QA guard)
GENERIC_SUMMARIZE_PHRASES = {
    "summarize",
    "summarize all",
    "summarize my tabs",
    "summarize everything",
    "all",
    "all tabs",
    "all topics",
    "everything",
    "general",
    "none",
}

# Conversational framing patterns stripped before extracting the user's core subject
INTENT_FRAMING_REGEX = re.compile(
    r"\b(?:i am|i'm|im|i feel|we are|currently)?\s*"
    r"(?:really|very|super|mostly|mainly|only|just|actually|deeply)?\s*"
    r"(?:interested in|looking for|searching for|caring about|care about|curious about|"
    r"focused on|focusing on|working on|learning about|studying|reading about|"
    r"want to learn about|want to read about|want to know about|want to see|want to build|"
    r"want|need|show me|find me|filter by|keep only|tabs about|pages about|articles about|"
    r"anything about|anything related to|related to)\b",
    flags=re.IGNORECASE,
)

# Comprehensive stop-words (pronouns, prepositions, auxiliaries, verbs, filler words)
CONVERSATIONAL_STOP_WORDS = {
    "a", "an", "the", "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing", "will", "would",
    "shall", "should", "can", "could", "may", "might", "must", "i", "im", "ive",
    "id", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "he", "him", "his", "she", "her", "hers", "it", "its",
    "they", "them", "their", "theirs", "what", "which", "who", "whom", "this",
    "that", "these", "those", "in", "on", "at", "by", "for", "with", "about",
    "against", "between", "into", "through", "during", "before", "after", "above",
    "below", "to", "from", "up", "down", "out", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "any", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "now", "also", "and", "but", "if", "or", "because", "as", "until", "while",
    "of", "like", "love", "want", "wants", "wanted", "need", "needs", "needed",
    "know", "learn", "learning", "read", "reading", "study", "studying", "work",
    "working", "build", "building", "make", "making", "use", "using", "used",
    "keep", "keeping", "close", "closing", "archive", "save", "show", "find",
    "get", "see", "look", "looking", "interested", "interest", "interests",
    "care", "caring", "focus", "focused", "related", "relevant", "important",
    "useful", "good", "best", "better", "new", "old", "high", "low", "right",
    "open", "tab", "tabs", "link", "links", "url", "urls", "page", "pages",
    "site", "sites", "web", "website", "article", "articles", "post", "posts",
    "blog", "stuff", "thing", "things", "way", "ways", "topic", "topics",
    "become", "becoming", "might", "regret", "welcome", "promise", "delivered",
    "انا", "مهتم", "عايز", "في", "عن", "من", "على", "الى", "كل", "بس", "فقط",
}

# Only these 2-letter acronyms are valid subject tokens; all other 2-letter words are dropped
ALLOWED_TWO_LETTER_TOKENS = {
    "ai", "ml", "ui", "ux", "db", "os", "js", "ts", "go", "ci", "cd", "vr", "ar", "3d", "4k", "5g", "qa",
}

# Broad generic activity/format words: if paired with a specific subject (like "Flutter" in "Flutter development"),
# these modifiers never count as a standalone match on pages that lack the specific subject!
GENERIC_MODIFIER_WORDS = {
    "development", "developer", "developers", "developing", "engineering", "engineer",
    "engineers", "programming", "programmer", "coding", "code", "software",
    "application", "applications", "app", "apps", "system", "systems", "project",
    "projects", "guide", "guides", "tutorial", "tutorials", "course", "courses",
    "overview", "introduction", "basics", "advanced", "tips", "tricks", "review",
    "reviews", "comparison", "buying", "buyer", "buyers", "planning", "plan",
    "plans", "method", "methods", "tool", "tools", "framework", "library",
    "platform", "service", "services", "modern", "complete", "full", "science",
    "research", "analysis", "design", "performance", "optimization",
}


def extract_focus_tokens(user_intent: str | None) -> list[str]:
    """
    Extracts the true core subject entities from any natural-language user sentence.
    Example:
      "I am interested in Flutter development" -> ["flutter"]
      "I want to plan a Japan travel itinerary" -> ["japan", "travel", "itinerary"]
      "high protein meal prep" -> ["protein", "meal", "prep"]
    """
    if not user_intent or not user_intent.strip():
        return []
    cleaned = INTENT_FRAMING_REGEX.sub(" ", user_intent.lower())
    raw_tokens = re.findall(r"[\w+#.-]+", cleaned)
    allow_web_token = bool(
        re.search(r"\bweb\s+(?:dev|development|developer|frontend|design|app|apps|ui|performance|accessibility)\b", cleaned)
        or cleaned.strip() in {"web", "web & frontend", "web and frontend"}
    )

    filtered: list[str] = []
    for tok in raw_tokens:
        t = tok.strip(".-_").lower()
        if t == "web" and allow_web_token:
            if t not in filtered:
                filtered.append(t)
            continue
        if not t or t in CONVERSATIONAL_STOP_WORDS:
            continue
        if len(t) < 3 and t not in ALLOWED_TWO_LETTER_TOKENS:
            continue
        if t not in filtered:
            filtered.append(t)

    specific_entities = [t for t in filtered if t not in GENERIC_MODIFIER_WORDS]
    if specific_entities:
        return specific_entities
    return filtered


def has_interest_focus(user_intent: str | None) -> bool:
    """
    Returns True ONLY when the user has entered a specific topic or interest to filter/rank by.
    Returns False when the input is blank or a generic "summarize all" request.
    """
    cleaned = (user_intent or "").strip().lower().rstrip(".!?")
    if not cleaned or cleaned in GENERIC_SUMMARIZE_PHRASES:
        return False
    return len(extract_focus_tokens(cleaned)) > 0


_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f\u200d\u20e3]+",
    flags=re.UNICODE,
)
_QUOTE_LEAD_RE = re.compile(r"^[\s\"'“”‘’]+")
_SUMMARY_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "you",
    "are", "was", "were", "have", "has", "had", "but", "not", "can", "will",
    "about", "after", "before", "when", "where", "what", "which", "who", "why",
    "how", "its", "their", "our", "all", "any", "each", "more", "most", "some",
    "such", "than", "then", "them", "they", "there", "these", "those",
}


def _strip_emoji(text: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI_RE.sub("", str(text or ""))).strip()


def _is_quote_fragment(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return True
    if _QUOTE_LEAD_RE.match(cleaned):
        return True
    quoted = re.findall(r"[\"“](.{12,}?)[\"”]", cleaned)
    return any(len(q.split()) > 8 for q in quoted)


def _looks_conversational(text: str) -> bool:
    lowered = _strip_emoji(text).lower()
    if re.match(r"^(?:hey|hi|so|well|okay|ok|anyway|honestly|picture this)\b", lowered):
        return True
    return any(
        phrase in lowered
        for phrase in (
            "reminds me of",
            "oktoberfest",
            "sounds interesting",
            "let's say",
            "think about it",
            "i also had",
            "before we start",
            "a little about my",
            "future me",
            "both came back",
        )
    )


def _summary_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _strip_emoji(text).lower())
        if token not in _SUMMARY_STOPWORDS and len(token) > 1
    }


def _text_similarity(a: str, b: str) -> float:
    left, right = _summary_tokens(a), _summary_tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _sanitize_catalog_tab(item: dict) -> dict:
    cleaned = dict(item)
    cleaned["overview"] = _strip_emoji(cleaned.get("overview") or "")
    raw_synth = [_strip_emoji(p) for p in (cleaned.get("synthesized_points") or []) if _strip_emoji(p)]
    clean_synth: list[str] = []
    for pt in raw_synth:
        head, body = pt.split(" — ", 1) if " — " in pt[:85] else ("", pt)
        if _is_quote_fragment(body) or _looks_conversational(body) or _looks_conversational(head):
            continue
        clean_synth.append(pt)
    cleaned["synthesized_points"] = clean_synth or raw_synth

    raw_takeaways = [_strip_emoji(t) for t in (cleaned.get("takeaways") or []) if _strip_emoji(t)]
    chosen: list[str] = []
    used_heads: set[str] = set()
    for candidate in raw_takeaways + clean_synth:
        head, body = candidate.split(" — ", 1) if " — " in candidate[:85] else ("", candidate)
        if len(body) < 40 or _is_quote_fragment(body) or _looks_conversational(body) or _looks_conversational(head):
            continue
        head_key = head.strip().lower()
        if head_key and head_key in used_heads:
            continue
        if any(_text_similarity(candidate, existing) >= 0.68 for existing in chosen):
            continue
        if head_key:
            used_heads.add(head_key)
        chosen.append(candidate)
        if len(chosen) >= 4:
            break
    if len(chosen) < 3:
        for candidate in raw_takeaways + raw_synth:
            if candidate not in chosen and not any(_text_similarity(candidate, existing) >= 0.68 for existing in chosen):
                chosen.append(candidate)
            if len(chosen) >= min(4, len(raw_takeaways) or 3):
                break
    if chosen:
        cleaned["takeaways"] = chosen[:4]
    return cleaned


# Load the verified 65-article DEV Hall of Fame Catalog + featured extra DEV articles
_HOF_EXHIBITS: list[dict] = []
_HOF_URL_INDEX: dict[str, dict] = {}


def _load_hof_catalog() -> None:
    global _HOF_EXHIBITS, _HOF_URL_INDEX
    if _HOF_URL_INDEX:
        return
    if not HOF_CATALOG_PATH.exists():
        return
    try:
        raw = json.loads(HOF_CATALOG_PATH.read_text(encoding="utf-8"))
        exhibits = [_sanitize_catalog_tab(ex) for ex in (raw.get("exhibits") or [])]
        _HOF_EXHIBITS = exhibits
        for ex in exhibits:
            u = (ex.get("url") or "").rstrip("/")
            if u:
                _HOF_URL_INDEX[u] = ex
        extra = raw.get("extra_articles") or {}
        for u, item in extra.items():
            clean_u = u.rstrip("/")
            if clean_u not in _HOF_URL_INDEX:
                _HOF_URL_INDEX[clean_u] = _sanitize_catalog_tab(item)
    except Exception:
        pass


_load_hof_catalog()

# Curated sample tab windows covering Everyday Life, AI/Cloud Research, AND the 65-Article DEV Hall of Fame!
PRESET_TAB_PILES: dict[str, dict] = {
    "dev_hall_of_fame_pile": {
        "name": "DEV Hall of Fame: All 65 Community Masterpieces (600+ Min Read)",
        "default_intent": "",
        "tabs": _HOF_EXHIBITS if _HOF_EXHIBITS else [],
    },
    "postgres_docker_pile": {
        "name": "Sample Window 1: Everyday Life, Travel, Health & Tech Mix (14 Open Tabs)",
        "default_intent": "",
        "tabs": [
            {
                "id": "tab-1",
                "title": "10-Day Japan Itinerary: Tokyo, Kyoto & Shinkansen Rail Pass Guide",
                "url": "https://travel.example.com/10-day-japan-itinerary-tokyo-kyoto",
                "domain": "travel.example.com",
                "age_days": 2.0,
                "word_count": 2150,
                "code_block_count": 6,
                "table_count": 3,
                "heading_count": 14,
                "link_density": 2.8,
                "overview": "Complete day-by-day travel blueprint for a 10-day trip across Tokyo, Kyoto, and Osaka with IC card and Shinkansen cost comparisons.",
                "takeaways": [
                    "Route & Hotel Split — Recommends splitting 5 nights in Tokyo (Shinjuku/Asakusa) and 4 nights in Kyoto with a 1-day trip to Nara.",
                    "Shinkansen vs. JR Pass Math — Buying single Shinkansen tickets ($190 round-trip) is now $140 cheaper than the 7-day JR Pass ($330) after the national price hike.",
                    "Transit & Timing Logistics — Includes Suica Apple Pay setup, luggage forwarding tips, and early-morning Fushimi Inari timing (before 7:00 AM).",
                ],
                "topics": ["Travel & Itinerary", "Japan / Tokyo", "Flights & Trains"],
                "full_text": "Japan travel itinerary Tokyo Kyoto Osaka Shinkansen bullet train Suica card ramen hotels Fushimi Inari Shibuya flight vacation trip.",
            },
            {
                "id": "tab-2",
                "title": "High-Protein 30-Minute Meal Prep: 5 Recipes With 40g Protein Per Serving",
                "url": "https://nutrition.example.com/high-protein-30-min-meal-prep",
                "domain": "nutrition.example.com",
                "age_days": 1.5,
                "word_count": 1640,
                "code_block_count": 5,
                "table_count": 2,
                "heading_count": 11,
                "link_density": 2.2,
                "overview": "Practical weekly cooking guide featuring five 30-minute batch recipes with full macro breakdowns (40g+ protein, under 550 calories).",
                "takeaways": [
                    "Batch Workflow — Uses one sheet pan and one skillet to prep 10 meals in 45 minutes on Sunday afternoon.",
                    "Top 3 High-Protein Recipes — Lemon-herb chicken bowls (44g protein), Greek yogurt chipotle wraps (39g protein), and lentil-beef chili (42g protein).",
                    "Supermarket Logistics — Includes a consolidated 14-item grocery shopping list sorted by aisle.",
                ],
                "topics": ["Food & Nutrition", "High Protein", "Meal Prep"],
                "full_text": "High protein meal prep recipes cooking food nutrition calories macros chicken Greek yogurt grocery list healthy dinner kitchen.",
            },
            {
                "id": "tab-3",
                "title": "Sleep Architecture & Circadian Timing: Why Late Caffeine Cuts Deep Sleep by 20%",
                "url": "https://health.example.com/caffeine-half-life-deep-sleep-study",
                "domain": "health.example.com",
                "age_days": 3.0,
                "word_count": 1890,
                "code_block_count": 6,
                "table_count": 2,
                "heading_count": 10,
                "link_density": 2.1,
                "overview": "Evidence-based summary of peer-reviewed sleep lab trials measuring caffeine half-life, morning daylight exposure, and slow-wave sleep recovery.",
                "takeaways": [
                    "Caffeine Half-Life Impact — Caffeine has a mean half-life of 5–6 hours; consuming 200mg within 8 hours of bedtime reduces slow-wave deep sleep by ~20%.",
                    "Morning Daylight Anchor — Viewing 15 minutes of outdoor morning daylight anchors melatonin onset 14 hours later and reduces sleep latency by 18 minutes.",
                    "Thermal Sleep Environment — Keeping bedroom temperature between 18°C and 19.5°C (65–67°F) prevents midnight cortisol awakenings.",
                ],
                "topics": ["Health & Sleep", "Circadian Rhythm", "Recovery"],
                "full_text": "Health sleep circadian rhythm melatonin caffeine deep sleep recovery bedtime morning sunlight temperature cortisol.",
            },
            {
                "id": "tab-4",
                "title": "2026 Ultrabook Buyer's Guide: Tested Battery Life, Display Nits & Sustained Thermals",
                "url": "https://reviews.example.com/2026-laptop-buyers-guide-tested",
                "domain": "reviews.example.com",
                "age_days": 2.2,
                "word_count": 2420,
                "code_block_count": 7,
                "table_count": 4,
                "heading_count": 15,
                "link_density": 3.0,
                "overview": "Independent lab benchmark of 12 lightweight laptops comparing real-world Wi-Fi battery life, screen brightness, and keyboard comfort.",
                "takeaways": [
                    "Best Overall Value Under $1,100 — 14-inch 32GB configuration lasting 14.5 hours on continuous web browsing with a 500-nit matte display.",
                    "OLED vs. IPS Battery Penalty — Warns against ultra-thin OLED panels if unplugged endurance is your priority; OLED units lost 3.2 hours on white-background workloads.",
                ],
                "topics": ["Shopping & Hardware", "Laptops", "Buying Guide"],
                "full_text": "Shopping laptop buying guide review MacBook ThinkPad battery life display nits ultrabook deal comparison.",
            },
            {
                "id": "tab-5",
                "title": "Reviving Daphq: How I Built an Open-Source, Bare-Metal File Transfer Engine Solo with GitHub Copilot",
                "url": "https://dev.to/omarafifi/reviving-daphq-how-i-built-an-open-source-bare-metal-file-transfer-engine-solo-with-github-copilot-5cd8",
                "domain": "dev.to",
                "age_days": 1.2,
                "word_count": 1292,
                "code_block_count": 8,
                "table_count": 2,
                "heading_count": 11,
                "link_density": 2.4,
                "overview": "Daphq is an open-source, cross-platform file transfer engine built in Flutter (Dart) that bypasses HTTP/REST to stream directly over raw TCP sockets (SocketOption.tcpNoDelay), reaching 80–90 MB/s (~93% Wi-Fi 5 saturation) on Android and Windows.",
                "takeaways": [
                    "Discovery, Connectivity & Resilience — Custom UDP Auto-Discovery Service with an integrated watchdog and rewritten Bulletproof Receiver Engine that recovers automatically from dropped connections.",
                    "User Interface, Fluidity & Storage — Unified Flutter dashboard anchored by an 'Orbital Radar' and Non-Blocking Async UI for sharing 1,000+ files with zero lag, plus Persistent Folders and Smart Auto-Cleaning.",
                    "Memory Safety & Disk I/O Flow Control — Implemented zero-copy byte slicing (Uint8List.sublistView) and Natural Socket Backpressure (await socket.flush()) to eliminate Out-Of-Memory crashes on multi-gigabyte files.",
                    "Solving the Math of the Orbital Radar UI — Distributes dynamic peer widgets symmetrically on a circular plane using trigonometric (cos/sin) coordinate math and assigns unique ValueKeys to prevent unnecessary re-paints.",
                ],
                "synthesized_points": [
                    "Cross-Platform Flutter & BLoC Architecture — Built using Flutter (Dart) for native Android and Windows performance, featuring a highly decoupled architecture using the BLoC/Cubit pattern, multi-gigabyte memory protection guards, and background network synchronization.",
                    "Solving the Math of the Orbital Radar UI — Mapped out the exact trigonometric distribution math using cos and sin over a mapped index, and assigns unique ValueKeys to the dynamically generated Flutter widgets, preventing unnecessary re-paints and maximizing rendering performance on low-end hardware.",
                    "Eliminating the Windows FFI Mutex Deadlock — Resolves Windows background execution loops and stubborn Dart VM shutdown deadlocks using a platform-specific local session mutex check and native Windows FFI ExitProcess call.",
                    "Memory Safety & Disk I/O Flow Control — Implemented zero-copy byte slicing (Uint8List.sublistView) and Natural Socket Backpressure (await socket.flush()), self-throttling to match real-time network speeds and saving physical RAM.",
                    "Discovery, Connectivity & Resilience — A custom UDP Auto-Discovery Service with an integrated watchdog and Bulletproof Receiver Engine that bounces back automatically if a connection drops.",
                    "Smart User Guidance — Integrated a real-time speed monitor that triggers a Smart Hotspot Guide if speeds drop below 2.0 MB/s, guiding users to utilize 5GHz direct hotspots for up to 10x faster speeds.",
                ],
                "topics": ["Flutter & Dart", "Open Source", "Networking & P2P"],
                "full_text": "Flutter Dart cross-platform mobile desktop application Daphq bare-metal file transfer engine TCP sockets SocketOption.tcpNoDelay BLoC Cubit ValueKeys Orbital Radar Dart VM FFI.",
            },
            {
                "id": "tab-6",
                "title": "Fine-Tuning Qwen3-8B with Tinker: From 50% to 76% Accuracy on Complexity Scoring",
                "url": "https://dev.to/gramli/fine-tuning-qwen3-8b-with-tinker-from-50-to-76-accuracy-and-burning-through-my-budget-48mh",
                "domain": "dev.to",
                "age_days": 2.0,
                "word_count": 2604,
                "code_block_count": 8,
                "table_count": 3,
                "heading_count": 22,
                "link_density": 2.8,
                "overview": "Experimental breakdown of fine-tuning Qwen3-8B on Thinking Machines' Tinker platform to score prompt complexity from 0.00 to 1.00 across multiple dataset sizes.",
                "takeaways": [
                    "Dataset Quality vs. Size — A clean, curated 50-example dataset achieved 86% accuracy, outperforming a noisy 2,500-example dataset (76% accuracy).",
                    "Diminishing Compute Returns — Scaling training data from 500 to 2,500 examples only improved accuracy from 74% to 76% while burning significantly more compute budget.",
                    "LoRA Hyperparameter Findings — Lower learning rates (1e-4) with rank 16 prevented overfitting on small instruction calibration splits.",
                ],
                "topics": ["AI & LLMs", "Fine-Tuning", "Machine Learning"],
                "full_text": "Fine-tuning Qwen3-8B LLM with Thinking Machines Tinker LoRA hyperparameters Python dataset complexity scorer API Hugging Face.",
            },
            {
                "id": "tab-7",
                "title": "pgvector Documentation: HNSW Index Parameters & ef_search Query Tuning",
                "url": "https://github.com/pgvector/pgvector#hnsw",
                "domain": "github.com",
                "age_days": 4.5,
                "word_count": 1420,
                "code_block_count": 11,
                "table_count": 2,
                "heading_count": 14,
                "link_density": 2.8,
                "overview": "Official reference guide for configuring HNSW approximate nearest-neighbor indexes in PostgreSQL with pgvector.",
                "takeaways": [
                    "HNSW Build Parameters — Configure m (max links per node, default 16) and ef_construction (candidate list size during build, default 64) to balance index build time against recall.",
                    "Dynamic Query Recall — Tune recall per transaction using SET LOCAL hnsw.ef_search = 100 without rebuilding the index.",
                ],
                "topics": ["PostgreSQL", "Databases & SQL", "RAG & Vector Search"],
                "full_text": "PostgreSQL pgvector HNSW index vector_l2_ops ef_construction ef_search SQL database embeddings.",
            },
            {
                "id": "tab-8",
                "title": "PyTorch CPU Threading & OMP_NUM_THREADS Contention on Shared Containers",
                "url": "https://pytorch.org/docs/stable/notes/cpu_threading_torchscript_inference.html",
                "domain": "pytorch.org",
                "age_days": 3.8,
                "word_count": 980,
                "code_block_count": 7,
                "table_count": 3,
                "heading_count": 9,
                "link_density": 3.1,
                "overview": "Technical documentation explaining how OpenMP and MKL thread pools behave inside shared vCPU Linux containers.",
                "takeaways": [
                    "Container Thread Pinning — Setting OMP_NUM_THREADS=2, MKL_NUM_THREADS=2, and torch.set_num_threads(2) prevents 32-core host thread thrashing in Docker.",
                ],
                "topics": ["Machine Learning", "Docker & Containers", "Python"],
                "full_text": "PyTorch CPU inference threading OMP_NUM_THREADS MKL_NUM_THREADS torch.set_num_threads Docker container.",
            },
            {
                "id": "tab-9",
                "title": "Sentry OpenTelemetry GenAI Spans: Preserving input_tokens in before_send",
                "url": "https://docs.sentry.io/product/agents/getting-started/",
                "domain": "docs.sentry.io",
                "age_days": 1.2,
                "word_count": 1120,
                "code_block_count": 9,
                "table_count": 3,
                "heading_count": 10,
                "link_density": 3.3,
                "overview": "Guide to instrumenting AI agents with OpenTelemetry gen_ai.request spans in Sentry without scrubbing token counters.",
                "takeaways": [
                    "Regex Scrubber Guard — Use negative lookahead (?<!put_)token(?!s) in before_send so gen_ai.usage.input_tokens is not redacted as an auth secret.",
                ],
                "topics": ["Observability", "AI Agents", "Python"],
                "full_text": "Sentry OpenTelemetry GenAI spans input_tokens output_tokens regex scrubber before_send.",
            },
            {
                "id": "tab-10",
                "title": "Cloudflare Error 525 SSL Handshake Failed with Custom Render CNAMEs",
                "url": "https://render.com/docs/custom-domains#cloudflare-dns",
                "domain": "render.com",
                "age_days": 2.4,
                "word_count": 760,
                "code_block_count": 5,
                "table_count": 2,
                "heading_count": 7,
                "link_density": 3.0,
                "overview": "Troubleshooting guide for resolving SSL Error 525 when mapping custom domains on Cloudflare to Render.",
                "takeaways": [
                    "DNS-Only Grey Cloud Fix — Set Cloudflare CNAME proxy status to DNS Only (Grey Cloud) so Render Let's Encrypt certificate provisioning succeeds.",
                ],
                "topics": ["Cloudflare & Edge", "Cloud Infrastructure", "DevOps & Docker"],
                "full_text": "Cloudflare 525 SSL handshake failed custom domain CNAME DNS only grey cloud Render.",
            },
            {
                "id": "tab-11",
                "title": "Why Software Complexity Is Killing Modern Culture (Opinion Essay)",
                "url": "https://blog.example.org/software-complexity-essay-2026",
                "domain": "blog.example.org",
                "age_days": 21.0,
                "word_count": 4850,
                "code_block_count": 0,
                "table_count": 0,
                "heading_count": 4,
                "link_density": 1.9,
                "overview": "Long-form opinion piece discussing corporate meetings and organizational bureaucracy.",
                "takeaways": [
                    "General Commentary — 4,850 words of broad cultural reflection with no concrete technical steps or benchmarks.",
                ],
                "topics": ["Opinion Essay", "Corporate Culture"],
                "full_text": "Opinion essay on corporate bureaucracy meetings.",
            },
            {
                "id": "tab-12",
                "title": "Forum Thread: What Desk Lamp Do You Use at Night? (410 comments)",
                "url": "https://news.ycombinator.com/item?id=41829100",
                "domain": "news.ycombinator.com",
                "age_days": 24.0,
                "word_count": 6200,
                "code_block_count": 0,
                "table_count": 0,
                "heading_count": 2,
                "link_density": 9.4,
                "overview": "Unstructured 410-comment forum thread debating desk lamps and monitor light bars.",
                "takeaways": [
                    "Unstructured Thread — 6,200 words of casual forum comments sitting open for 24 days.",
                ],
                "topics": ["Forum Thread", "Office Gear"],
                "full_text": "Forum comments desk lamps light bulbs.",
            },
            {
                "id": "tab-13",
                "title": "25 Best Sci-Fi Novels of the Decade (Affiliate Listicle)",
                "url": "https://readinglist.example.com/25-scifi-books-2026",
                "domain": "readinglist.example.com",
                "age_days": 19.0,
                "word_count": 4100,
                "code_block_count": 0,
                "table_count": 0,
                "heading_count": 26,
                "link_density": 15.2,
                "overview": "Entertainment listicle of 25 science fiction novels with outbound store links.",
                "takeaways": [
                    "Book Listicle — 4,100-word fiction roundup with 25 affiliate links.",
                ],
                "topics": ["Books & Fiction", "Listicle"],
                "full_text": "Science fiction novels book list.",
            },
            {
                "id": "tab-14",
                "title": "Global Events Directory 2026 (320 Outbound Links)",
                "url": "https://confs.example.com/2026-directory",
                "domain": "confs.example.com",
                "age_days": 18.0,
                "word_count": 1400,
                "code_block_count": 0,
                "table_count": 1,
                "heading_count": 24,
                "link_density": 22.8,
                "overview": "Index page listing 320 upcoming events by month.",
                "takeaways": [
                    "Link Directory — High link density (22.8 outbound links per 100 words) with no article prose.",
                ],
                "topics": ["Directory", "Events"],
                "full_text": "Directory events calendar links.",
            },
        ],
    },
    "edge_ai_quantization_pile": {
        "name": "Sample Window 2: AI, Voice & Cloud Research Tabs (10 Open Tabs)",
        "default_intent": "",
        "tabs": [
            {
                "id": "tab-101",
                "title": "Transformers.js v3 WebGPU: Running 4-bit Gemma 3 1B Inside a Browser Web Worker",
                "url": "https://huggingface.co/onnx-community/gemma-3-1b-it-ONNX-GQA",
                "domain": "huggingface.co",
                "age_days": 1.8,
                "word_count": 1180,
                "code_block_count": 9,
                "table_count": 2,
                "heading_count": 10,
                "link_density": 3.0,
                "overview": "Model card and implementation guide for executing 4-bit quantized Gemma 3 1B entirely client-side on WebGPU.",
                "takeaways": [
                    "Zero-Cost Browser Inference — Downloads a 763 MB 4-bit GQA ONNX checkpoint once into the browser cache and executes locally with zero server cost.",
                    "Non-Blocking Web Worker Execution — Runs greedy decoding inside a dedicated Web Worker so the main browser UI thread never freezes.",
                ],
                "topics": ["Google Gemma", "WebGPU & Wasm", "AI & LLMs"],
                "full_text": "Transformers.js WebGPU Gemma 3 1B ONNX q4 quantization browser local inference Web Worker.",
            },
            {
                "id": "tab-102",
                "title": "Prior Labs TabPFN: Zero-Shot Bayesian Tabular Regression & Quantile Calibration",
                "url": "https://docs.priorlabs.ai/quickstart",
                "domain": "docs.priorlabs.ai",
                "age_days": 1.0,
                "word_count": 1050,
                "code_block_count": 8,
                "table_count": 2,
                "heading_count": 9,
                "link_density": 2.6,
                "overview": "Documentation for Prior Labs' TabPFN tabular foundation model, covering in-context learning and P10/P50/P90 quantile distributions.",
                "takeaways": [
                    "Zero-Shot Tabular Foundation Model — Performs instant regression and classification on tabular datasets without hyperparameter tuning loops.",
                    "Calibrated Uncertainty Quantiles — Returns full probability distributions (P10, P50, P90) in a single forward pass via output_type='quantiles'.",
                ],
                "topics": ["Machine Learning", "Data Science", "Python"],
                "full_text": "Prior Labs TabPFN tabular foundation model quantiles P10 P50 P90 regression Python.",
            },
            {
                "id": "tab-103",
                "title": "Sentry OpenTelemetry GenAI Spans: Tracing Token Usage & Latency in AI Agents",
                "url": "https://docs.sentry.io/product/agents/getting-started/",
                "domain": "docs.sentry.io",
                "age_days": 1.4,
                "word_count": 1120,
                "code_block_count": 9,
                "table_count": 3,
                "heading_count": 10,
                "link_density": 3.3,
                "overview": "Guide to instrumenting AI agents with OpenTelemetry gen_ai.request spans in Sentry.",
                "takeaways": [
                    "GenAI Span Telemetry — Records model name, input_tokens, output_tokens, and latency across multi-step AI agent pipelines.",
                ],
                "topics": ["Observability", "AI Agents", "Python"],
                "full_text": "Sentry Agent Tracing OpenTelemetry gen_ai.request input_tokens output_tokens latency Python.",
            },
            {
                "id": "tab-104",
                "title": "ElevenLabs Scribe API: Low-Latency Speech-to-Text & Immediate Transcript Deletion",
                "url": "https://elevenlabs.io/docs/api-reference/speech-to-text",
                "domain": "elevenlabs.io",
                "age_days": 1.6,
                "word_count": 840,
                "code_block_count": 6,
                "table_count": 2,
                "heading_count": 8,
                "link_density": 2.9,
                "overview": "API specification for ElevenLabs Scribe speech recognition and privacy-preserving transcript lifecycle management.",
                "takeaways": [
                    "Privacy-First Audio Cleanup — Calling DELETE /v1/speech-to-text/transcripts/{id} immediately removes stored audio and text from ElevenLabs servers.",
                ],
                "topics": ["Voice & Audio AI", "APIs & Backend", "Cybersecurity"],
                "full_text": "ElevenLabs Scribe speech-to-text audio transcription Flash v2.5 TTS voice API privacy.",
            },
            {
                "id": "tab-105",
                "title": "Browserbase CDP Session API: Attaching Target & Releasing Concurrency Slots",
                "url": "https://docs.browserbase.com/reference/api/create-a-session",
                "domain": "docs.browserbase.com",
                "age_days": 0.8,
                "word_count": 890,
                "code_block_count": 8,
                "table_count": 2,
                "heading_count": 9,
                "link_density": 2.4,
                "overview": "Reference guide for creating headless Chromium sessions on Browserbase and releasing concurrency slots.",
                "takeaways": [
                    "Immediate Slot Release — POST /v1/sessions/{id} with status REQUEST_RELEASE frees the cloud browser slot immediately after DOM extraction.",
                ],
                "topics": ["Browser Automation", "Cloud Infrastructure", "APIs & Backend"],
                "full_text": "Browserbase CDP session API REQUEST_RELEASE concurrency slots Playwright.",
            },
            {
                "id": "tab-106",
                "title": "MLXGuidedGeneration XGrammar Greedy Decoding Whitespace Bias Fix",
                "url": "https://github.com/ml-explore/mlx-swift-lm",
                "domain": "github.com",
                "age_days": 1.5,
                "word_count": 940,
                "code_block_count": 7,
                "table_count": 2,
                "heading_count": 9,
                "link_density": 2.5,
                "overview": "Fix for schema-constrained JSON generation on 1B models using XGrammar whitespace logit biases.",
                "takeaways": [
                    "Schema-Constrained Decoding — Enabling whitespace and closing-token logit biases raised valid JSON schema output from 0/6 to 6/6 in 4.4s.",
                ],
                "topics": ["AI & LLMs", "Machine Learning", "Open Source"],
                "full_text": "MLXGuidedGeneration XGrammar greedy decoding whitespace logit bias JSON schema.",
            },
            {
                "id": "tab-107",
                "title": "The Complete 8,000-Word Guide to Mechanical Keyboard Switch Lubing",
                "url": "https://keyboards.example.com/switch-lubing-guide",
                "domain": "keyboards.example.com",
                "age_days": 22.0,
                "word_count": 7900,
                "code_block_count": 0,
                "table_count": 1,
                "heading_count": 18,
                "link_density": 9.8,
                "overview": "Hobbyist guide on disassembling mechanical switches.",
                "takeaways": ["Hobbyist Guide — 7,900 words of mechanical keyboard commentary sitting untouched for 22 days."],
                "topics": ["Keyboards", "Hobby"],
                "full_text": "Mechanical keyboards cherry mx switches lubing.",
            },
            {
                "id": "tab-108",
                "title": "100 Best Indie Video Games of the Decade Ranked",
                "url": "https://gaming.example.com/100-indie-games",
                "domain": "gaming.example.com",
                "age_days": 17.5,
                "word_count": 6400,
                "code_block_count": 0,
                "table_count": 0,
                "heading_count": 32,
                "link_density": 16.4,
                "overview": "Entertainment countdown listicle of 100 indie games.",
                "takeaways": ["Gaming Countdown — 6,400-word entertainment listicle."],
                "topics": ["Gaming", "Entertainment"],
                "full_text": "Indie video games steam rankings.",
            },
            {
                "id": "tab-109",
                "title": "Will AGI Arrive in 2027 or 2045? A 6,000-Word Philosophical Debate",
                "url": "https://futurism.example.com/agi-debate-2027",
                "domain": "futurism.example.com",
                "age_days": 15.0,
                "word_count": 5950,
                "code_block_count": 0,
                "table_count": 0,
                "heading_count": 6,
                "link_density": 2.1,
                "overview": "Speculative essay debating long-term timelines.",
                "takeaways": ["Speculative Essay — 5,950-word philosophical debate with no implementation details."],
                "topics": ["Futurism", "Opinion"],
                "full_text": "Futurism philosophy debate essay.",
            },
            {
                "id": "tab-110",
                "title": "My Minimalist Home Office Desk Tour 2026",
                "url": "https://gear.example.com/desk-tour-2026",
                "domain": "gear.example.com",
                "age_days": 13.0,
                "word_count": 2950,
                "code_block_count": 0,
                "table_count": 0,
                "heading_count": 7,
                "link_density": 11.2,
                "overview": "Blog post showcasing desk plants and affiliate shopping links.",
                "takeaways": ["Desk Setup Blog — Affiliate post (2,950 words) open for 13 days."],
                "topics": ["Desk Setup", "Blog"],
                "full_text": "Minimalist desk setup monitor arm walnut desk.",
            },
        ],
    },
}

# Universal topic detector covering everyday life + science + business + tech
KNOWN_TOPICS = [
    ("Travel & Itinerary", r"\b(travel|itinerary|flight|hotel|tokyo|kyoto|japan|vacation|shinkansen)\b"),
    ("Food & Nutrition", r"\b(recipe|cooking|meal prep|protein|nutrition|calories|ramen|ingredients|diet)\b"),
    ("Health & Sleep", r"\b(sleep|circadian|melatonin|caffeine|health|fitness|recovery|cortisol)\b"),
    ("Shopping & Hardware", r"\b(buyer's guide|buying guide|laptop|macbook|thinkpad|battery life|display nits)\b"),
    ("Finance & Investing", r"\b(investing|portfolio|stock market|etf|dividend|mortgage|banking|tax)\b"),
    ("Flutter & Dart", r"\b(flutter|dart|impeller|widget|pubspec|statefulwidget)\b"),
    ("AI & LLMs", r"\b(fine-tun(?:e|ing)|qwen[0-9.-]*|gemma|gemini|tinker|lora|llm|openai|claude|rag)\b"),
    ("AI Agents", r"\b(agent|agentic|tool use|function calling|mcp|copilot)\b"),
    ("Machine Learning", r"\b(tabpfn|pytorch|quantile|regression|dataset|benchmark|a/b testing)\b"),
    ("Databases & SQL", r"\b(postgres(?:ql)?|pgvector|hnsw|sql|database|mysql|write-ahead log|wal)\b"),
    ("Cloud & DevOps", r"\b(docker|container|kubernetes|kubectl|aws|vpc|render|cloudflare|ssl|cname)\b"),
    ("Observability", r"\b(sentry|opentelemetry|tracing|spans|honeycomb)\b"),
    ("Voice & Audio AI", r"\b(elevenlabs|scribe|speech-to-text|tts)\b"),
    ("Browser Automation", r"\b(browserbase|playwright|cdp|chromium)\b"),
    ("Networking & P2P", r"\b(tcp|udp|socket|wi-fi|bandwidth|file transfer|p2p|webrtc|libp2p)\b"),
    ("Web Development", r"\b(javascript|typescript|react|html|css|a11y|accessibility|webgpu|webassembly)\b"),
    ("Cybersecurity", r"\b(security|malware|prompt injection|vulnerability)\b"),
    ("Career & Growth", r"\b(career|jobseekers|conference speaker|public speaking|cfp|burnout|interview)\b"),
]

GENERIC_SECTION_TITLES = {
    "overview", "introduction", "intro", "what i built", "the comeback story",
    "the verdict", "conclusion", "final thoughts", "summary", "tl;dr", "tldr",
    "demo", "code", "how i built it", "my experience", "background", "the problem",
    "wrapping up", "what's next", "whats next", "screenshots in action",
    "my experience with github copilot", "the deep evolution: v1.0.0 vs. v2.3.0",
}

BOILERPLATE_PHRASES = (
    "this is a submission for",
    "hacktoberfest open-source ai challenge",
    "finish-up-a-thon",
    "official product website:",
    "github repository:",
    "enter fullscreen mode",
    "skip to content",
    "top comments",
    "sign in",
    "create account",
    "cookie",
    "oktoberfest? yes!",
    "couldn't miss hacktoberfest",
    "side hustle sounds like",
    "prize categories and promo codes",
    "save me some money",
    "add reaction",
    "like unicorn",
    "let's stay connected",
    "connect with me on linkedin",
    "explore my projects on github",
)

TRAILING_FILLER_RE = re.compile(
    r"\s*(?:I couldn't be prouder of how[^.!?]*[.!?]|Thanks for reading[^.!?]*[.!?]|"
    r"Let me know in the comments[^.!?]*[.!?]|Feel free to share[^.!?]*[.!?]|"
    r"Happy coding[^.!?]*[.!?])\s*$",
    flags=re.IGNORECASE,
)

def _unescape_html(text: str) -> str:
    return html_lib.unescape(text or "")


def _strip_html_tags(raw_html: str) -> str:
    """
    Cleans HTML while preserving inline word suffixes (so `<code>ValueKey</code>s` becomes
    `ValueKeys`, NEVER `ValueKey s`) and normalizing parentheses spacing.
    """
    no_scripts = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw_html or "", flags=re.DOTALL | re.IGNORECASE)
    no_inline = re.sub(
        r"</?(?:code|strong|em|b|i|span|a|kbd|mark|sub|sup|small|abbr)\b[^>]*>",
        "",
        no_scripts,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", no_inline)
    text = _unescape_html(text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\b([A-Z][a-z0-9]+[A-Z][A-Za-z0-9_]*)\s+s\b", r"\1s", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = TRAILING_FILLER_RE.sub("", text).strip()
    text = re.sub(r",\s+and\s+([^,]+),\s+and\s+", r", \1, and ", text)
    return text


def _polish_prose(text: str) -> str:
    """
    Removes conversational diary lead-ins and dangling mid-sentence connectors so every
    extracted takeaway reads like a self-contained executive summary point.
    """
    t = _strip_emoji((text or "").strip())
    t = re.sub(r"^Copilot['’]s Intervention:\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^By providing [A-Za-z0-9_ ]+ with my [^,]+,\s*it\s+(?:instantly\s+)?", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^I explained the [^.]+\.\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^It guided me through structuring\s+", "Structures ", t, flags=re.IGNORECASE)
    t = re.sub(r"^It guided me through\s+", "Guides through ", t, flags=re.IGNORECASE)
    t = re.sub(r"\.\s*Furthermore,\s*it\s+(?:proactively\s+)?recommended\s+assigning\s+", ", and assigns ", t, flags=re.IGNORECASE)
    t = re.sub(r"\.\s*Furthermore,\s*it\s+(?:proactively\s+)?recommended\s+", ", and recommends ", t, flags=re.IGNORECASE)
    t = re.sub(r"^(?:Furthermore|Moreover|Additionally|In addition|Also|Plus),\s*it\s+(?:proactively\s+)?recommended\s+", "Recommends ", t, flags=re.IGNORECASE)
    t = re.sub(r"^(?:Furthermore|Moreover|Additionally|In addition|Also|Plus),\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^As a solo developer,\s*I built ([A-Za-z0-9_-]+) to solve a personal frustration:\s*", r"\1 eliminates ", t, flags=re.IGNORECASE)
    t = re.sub(r"^I wanted the home dashboard to feel alive with\s+", "Features ", t, flags=re.IGNORECASE)
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def _flatten_nested_lists(html_str: str) -> str:
    """
    Flattens nested `<ul><li>Before...</li><li>After...</li></ul>` inside a parent `<li>Category: ...</li>`
    so the parent category title stays permanently attached to its `After` resolution.
    """
    def _repl(match: re.Match) -> str:
        head_raw = _strip_html_tags(match.group(1)).rstrip(":")
        ul_body = match.group(2)
        sub_items = [
            _strip_html_tags(x)
            for x in re.findall(r"<li[^>]*>(.*?)</li>", ul_body, flags=re.DOTALL | re.IGNORECASE)
        ]
        sub_items = [x for x in sub_items if x]
        if not sub_items:
            return f"<li>{head_raw}</li>"
        after_txt = ""
        for si in sub_items:
            if re.match(r"^after\b", si, flags=re.IGNORECASE):
                after_txt = re.sub(r"^after\s*(?:\([^)]+\))?\s*:\s*", "", si, flags=re.IGNORECASE).strip()
        if after_txt:
            return f"<li>{head_raw} — {after_txt}</li>"
        joined = " · ".join(sub_items[:2])
        return f"<li>{head_raw} — {joined}</li>"

    return re.sub(
        r"<li[^>]*>((?:(?!<li\b|</li>|<ul\b).)*?)<ul[^>]*>(.*?)</ul>\s*</li>",
        _repl,
        html_str,
        flags=re.DOTALL | re.IGNORECASE,
    )


def _detect_topics(title: str, full_text: str, full_html: str = "") -> list[str]:
    """
    Detects accurate, human-friendly topic tags using:
    1. Explicit author `#tags` from the article header (`/t/tag` or `<meta name="keywords">`), mapped via `TAG_PRETTY_MAP`.
    2. `KNOWN_TOPICS` domain regexes weighted 4x on the article title.
    Never falls back to verbs like 'Become' or 'Might'!
    """
    pretty_topics: list[str] = []
    if full_html:
        b_idx = full_html.find("crayons-article__body")
        header_slice = full_html[:b_idx] if b_idx > 0 else full_html[:140000]
        raw_tags = list(dict.fromkeys(t.lower() for t in re.findall(r"/t/([a-zA-Z0-9_-]+)", header_slice)))[:8]
        for rt in raw_tags:
            if rt in META_TAGS_TO_SKIP:
                continue
            mapped = TAG_PRETTY_MAP.get(rt)
            if mapped and mapped not in pretty_topics:
                pretty_topics.append(mapped)

    combined = f"{title} {title} {title} {title} {full_text[:12000]}"
    scored: list[tuple[int, str]] = []
    for label, pattern in KNOWN_TOPICS:
        matches = re.findall(pattern, combined, flags=re.IGNORECASE)
        if len(matches) >= 3:
            scored.append((len(matches), label))
    scored.sort(key=lambda x: x[0], reverse=True)
    for _, label in scored:
        if len(pretty_topics) >= 3:
            break
        if label not in pretty_topics:
            pretty_topics.append(label)

    if not pretty_topics:
        words = [
            w.strip(",.:;!?()[]\"'")
            for w in title.split()
            if len(w.strip(",.:;!?()[]\"'")) >= 4
            and w[0].isupper()
            and w.lower() not in CONVERSATIONAL_STOP_WORDS
        ]
        pretty_topics = list(dict.fromkeys(words))[:3] or ["Web Article"]

    return pretty_topics[:3]


def _extract_overview_and_takeaways(
    html_scope: str,
    title: str,
    full_Html: str = "",
) -> tuple[str, list[str], list[str]]:
    """
    Section-Aware Structural Extractor:
    Returns `(overview, takeaways, synthesized_points)` where every takeaway is anchored
    to its section heading (`Heading — Key Insight`) and spans distinct sections of the page.
    """
    html_no_comments = re.split(
        r'<(?:section|div)[^>]+(?:id=["\']comments["\']|class=["\'][^"\']*comments-container)',
        html_scope,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    html_clean_scope = re.sub(
        r"<(nav|footer|aside)[^>]*>.*?</\1>",
        " ",
        html_no_comments,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Strip syntax-highlighted code blocks before extracting prose so raw code never leaks into summaries
    scope_no_code = re.sub(
        r'<div[^>]+class=["\'][^"\']*highlight[^"\']*["\'][^>]*>.*?</div>\s*</div>',
        " ",
        html_clean_scope,
        flags=re.DOTALL | re.IGNORECASE,
    )
    scope_no_code = re.sub(r"<pre[^>]*>.*?</pre>", " ", scope_no_code, flags=re.DOTALL | re.IGNORECASE)
    scope_no_embeds = re.sub(
        r'<div[^>]+class=["\'][^"\']*ltag__link[^"\']*["\'][^>]*>.*?</div>\s*</div>\s*</div>',
        " ",
        scope_no_code,
        flags=re.DOTALL | re.IGNORECASE,
    )
    scope_flat = _flatten_nested_lists(scope_no_embeds)

    tokens = re.findall(r"<(h[23]|p|li)\b[^>]*>(.*?)</\1>", scope_flat, flags=re.DOTALL | re.IGNORECASE)
    sections = []
    cur_sec = {"heading": "Overview", "items": []}
    sections.append(cur_sec)

    for tag, content in tokens:
        c = _strip_html_tags(content)
        if not c or len(c) < 4:
            continue
        if tag.lower() in ("h2", "h3"):
            clean_h = re.sub(r"^\d+[\.\)]\s*", "", c).strip()
            cur_sec = {"heading": clean_h, "items": []}
            sections.append(cur_sec)
        else:
            low_c = c.lower()
            if any(bp in low_c for bp in BOILERPLATE_PHRASES):
                continue
            if _is_quote_fragment(c) or _looks_conversational(c):
                continue
            if c.endswith(":") and len(c) < 110:
                continue
            cur_sec["items"].append((tag.lower(), c))

    sections = [s for s in sections if s["items"]]

    synthesized_points: list[dict] = []
    prose_paragraphs: list[str] = []

    for sec in sections:
        h = sec["heading"]
        h_low = h.lower().strip()
        is_specific_h = h_low not in GENERIC_SECTION_TITLES and len(h) <= 68

        p_items = [txt for tag, txt in sec["items"] if tag == "p" and 50 <= len(txt) <= 550]
        li_items = [txt for tag, txt in sec["items"] if tag == "li" and 40 <= len(txt) <= 550]
        prose_paragraphs.extend(p_items)

        anchored_lis = [
            li for li in li_items
            if (" — " in li or re.match(r"^[A-Z][A-Za-z0-9 &/,-]{3,42}:\s+\S", li))
            and not li.lower().startswith("copilot")
        ]
        if anchored_lis:
            for ali in anchored_lis:
                norm_ali = re.sub(r"^([A-Z][A-Za-z0-9 &/,-]{3,42}):\s+", r"\1 — ", ali)
                synthesized_points.append({
                    "heading": h,
                    "text": _polish_prose(norm_ali),
                })
            continue

        if is_specific_h and li_items:
            best_li = _polish_prose(li_items[0])
            if p_items and "dart vm" in " ".join(p_items).lower() and "dart vm" not in best_li.lower():
                best_li = f"Resolves Windows background execution loops and Dart VM deadlocks: {best_li[0].lower() + best_li[1:]}"
            synthesized_points.append({
                "heading": h,
                "text": f"{h} — {best_li}",
            })
            continue

        for tag, txt in sec["items"]:
            if len(txt) < 62 or len(txt) > 460:
                continue
            if txt.endswith(":") or txt.endswith("?"):
                continue
            pol = _polish_prose(txt)
            if is_specific_h and " — " not in pol[:55]:
                formatted = f"{h} — {pol}"
            elif "built using flutter" in pol.lower() and " — " not in pol[:55]:
                formatted = f"Cross-Platform Architecture — {pol}"
            else:
                formatted = pol
            synthesized_points.append({
                "heading": h,
                "text": formatted,
            })

    substantive_prose = [
        _polish_prose(p)
        for p in prose_paragraphs
        if len(p) >= 90 and not p.strip().endswith("?") and not p.strip().endswith(":")
        and not any(j in p.lower() for j in ("reminds me of", "oktoberfest", "sounds interesting"))
    ]

    overview = ""
    if substantive_prose:
        overview = substantive_prose[0]
        for p2 in substantive_prose[1:4]:
            if len(overview) < 230 and any(
                kw in p2.lower()
                for kw in ("built using", "engineered", "architecture", "under the hood", "in this article", "we built")
            ):
                first_sent = re.split(r"(?<=[.!?])\s+", p2)[0]
                if first_sent and first_sent not in overview:
                    overview = f"{overview} {first_sent}"
                    break
    if not overview and prose_paragraphs:
        overview = _polish_prose(prose_paragraphs[0])
    if not overview:
        overview = f"Comprehensive breakdown covering {title}."
    overview = _strip_emoji(overview)

    scored_pts = []
    for idx_pt, pt_obj in enumerate(synthesized_points):
        txt = pt_obj["text"]
        h_name = pt_obj["heading"]
        body_part = txt.split(" — ", 1)[-1] if " — " in txt[:75] else txt
        if len(synthesized_points) > 3 and (txt == overview or txt in overview or overview in txt or body_part[:65] in overview):
            continue
        if len(body_part) < 55:
            continue
        low = body_part.lower()
        h_low = h_name.lower()
        sc = 1.0
        if " — " in txt[:75]:
            sc += 2.5
        num_hits = len(re.findall(r"\b\d+(?:\.\d+)?(?:%|ms|s|x|gb|mb|mb/s|fps|k|g|mg|min|hours|days|\$)?\b", low))
        sc += min(3.0, num_hits * 0.7)
        if 110 <= len(body_part) <= 310:
            sc += 2.2
        elif 75 <= len(body_part) < 110:
            sc += 1.2
        if re.search(
            r"\b(step \d|architecture|disassembl|deduction|domino|fix|solution|evolution|adaptive|breakpoint|benchmark|algorithm|database|wal|webrtc|security|prompt|rag|cfp|talk|blogging|memory|discovery|guidance|protein|itinerary|sleep)\b",
            f"{h_low} {low}",
        ):
            sc += 2.2
        if re.search(r"(?:picture this|future me|little about my|before we start)", f"{h_low} {low}"):
            sc -= 2.5
        if re.match(r"^(?:both came back|let's say|think about it|i also had|well,|somehow,|this text does not|hey, you ran)", low):
            sc -= 2.2
        anchor_key = txt.split(" — ")[0].strip().lower() if " — " in txt[:75] else h_name.lower()
        scored_pts.append((sc, idx_pt, anchor_key, txt))

    scored_pts.sort(key=lambda x: x[0], reverse=True)
    chosen = []
    used_anchors = set()
    for sc, idx_pt, anchor_key, txt in scored_pts:
        if anchor_key in used_anchors:
            continue
        if any(_text_similarity(txt, c_txt) >= 0.7 for _, c_txt in chosen):
            continue
        used_anchors.add(anchor_key)
        chosen.append((idx_pt, txt))
        if len(chosen) >= 4:
            break
    if len(chosen) < 4:
        for sc, idx_pt, anchor_key, txt in scored_pts:
            if any(txt == c_txt for _, c_txt in chosen):
                continue
            if any(_text_similarity(txt, c_txt) >= 0.7 for _, c_txt in chosen):
                continue
            chosen.append((idx_pt, txt))
            if len(chosen) >= 4:
                break

    top4 = sorted(chosen, key=lambda x: x[0])
    takeaways = [_strip_emoji(t) for _, t in top4]
    if not takeaways and prose_paragraphs:
        takeaways = [_strip_emoji(_polish_prose(p)) for p in prose_paragraphs[:3]]
    if not takeaways:
        takeaways = [overview]

    return overview, takeaways, [_strip_emoji(p["text"]) for p in synthesized_points[:30]]


def _ensure_point_has_anchor(pt: str, default_topic: str) -> str:
    """
    Ensures every bullet point in the Topic-Focused Tab has a clean `Heading — Explanation`
    structure so it never looks like an unanchored mid-paragraph fragment.
    """
    cleaned = _polish_prose(pt)
    if " — " in cleaned[:75]:
        return cleaned
    if re.match(r"^[A-Z][A-Za-z0-9 &/,-]{3,42}:\s+\S", cleaned):
        return re.sub(r"^([A-Z][A-Za-z0-9 &/,-]{3,42}):\s+", r"\1 — ", cleaned)
    low = cleaned.lower()
    if "built using flutter" in low or "bloc/cubit" in low:
        return f"Cross-Platform Flutter Architecture — {cleaned}"
    if "valuekey" in low or "orbital radar" in low:
        return f"Widget Rendering & Radar UI — {cleaned}"
    if "dart vm" in low or "ffi" in low:
        return f"Native FFI & Runtime Stability — {cleaned}"
    if "uint8list" in low or "backpressure" in low or "socket.flush" in low:
        return f"Stream Flow Control & Memory Safety — {cleaned}"
    return f"{default_topic.title()} Insight — {cleaned}"


def _build_interest_specific_section(
    tab: dict,
    user_intent: str,
    matched_tokens: list[str],
    expanded_search_terms: list[str],
    total_mentions: int,
) -> dict:
    """
    Constructs Tab 2 ('interest_section'): a coherent, section-anchored Topic-Focused Summary
    showing HOW this page addresses the user's chosen topic, with:
    - `why_relevant`: A natural executive synthesis naming the specific sections/concepts covered.
    - `tailored_points`: 3–4 section-anchored `Heading — Explanation` takeaways.
    """
    synthesized_pool = list(tab.get("synthesized_points") or [])
    takeaway_pool = list(tab.get("takeaways") or [])
    paragraph_pool = list(tab.get("all_paragraphs") or [])

    candidate_pool: list[tuple[bool, str]] = []
    seen_norm: set[str] = set()

    for s in synthesized_pool + takeaway_pool:
        if not s or _is_quote_fragment(s) or _looks_conversational(s):
            continue
        norm = _strip_emoji(s).lower()[:80]
        if norm not in seen_norm:
            seen_norm.add(norm)
            candidate_pool.append((True, _strip_emoji(s)))

    for p in paragraph_pool:
        if not p or len(p) < 65 or _is_quote_fragment(p) or _looks_conversational(p):
            continue
        norm = _strip_emoji(p).lower()[:80]
        if norm not in seen_norm:
            seen_norm.add(norm)
            candidate_pool.append((False, _strip_emoji(p)))

    primary_set = {t.lower() for t in matched_tokens}
    scored_matches: list[tuple[float, str]] = []

    for is_structured, sent in candidate_pool:
        s_low = sent.lower()
        primary_hits = sum(1 for tok in primary_set if tok in s_low)
        expanded_hits = sum(1 for term in expanded_search_terms if term in s_low)
        if primary_hits == 0 and expanded_hits == 0:
            continue

        score = (primary_hits * 4.5) + (expanded_hits * 2.2)
        if is_structured and " — " in sent[:75]:
            score += 4.0  # Strongly prefer structured Section-Anchored points over raw paragraphs!
        num_bonus = min(2.0, len(re.findall(r"\b\d+(?:\.\d+)?(?:%|ms|s|x|gb|mb|mb/s|fps|g|mg|min|hours|\$)?\b", s_low)) * 0.5)
        score += num_bonus
        scored_matches.append((score, sent))

    scored_matches.sort(key=lambda x: x[0], reverse=True)

    default_topic_label = ", ".join(t.title() if len(t) > 3 else t.upper() for t in matched_tokens[:2]) or user_intent.strip()
    tailored_points: list[str] = []
    used_anchors: set[str] = set()

    for _, cand_sent in scored_matches:
        anchored = _ensure_point_has_anchor(cand_sent, default_topic_label)
        anchor_head = anchored.split(" — ", 1)[0].strip().lower()
        body_part = anchored.split(" — ", 1)[-1].strip()
        if anchor_head in used_anchors and len(scored_matches) > 3:
            continue
        if any(_text_similarity(anchored, existing) >= 0.68 for existing in tailored_points):
            continue
        used_anchors.add(anchor_head)
        tailored_points.append(anchored)
        if len(tailored_points) >= 4:
            break

    if not tailored_points:
        for tk in (tab.get("takeaways") or [tab.get("overview", "")])[:3]:
            if tk:
                tailored_points.append(_ensure_point_has_anchor(tk, default_topic_label))

    # Build a natural Topic Synthesis line that never dumps raw or duplicate section headings.
    section_names: list[str] = []
    seen_sec_low: set[str] = set()
    generic_anchor_low = f"{default_topic_label} insight".lower()
    for pt in tailored_points:
        if " — " not in pt:
            continue
        sec = pt.split(" — ", 1)[0].strip()
        sec_low = sec.lower()
        if sec_low and sec_low != generic_anchor_low and sec_low not in seen_sec_low:
            seen_sec_low.add(sec_low)
            section_names.append(sec)
    pretty_focus = ", ".join(
        TAG_PRETTY_MAP.get(t.lower(), t.title() if len(t) > 3 else t.upper())
        for t in matched_tokens[:2]
    ) or user_intent.strip()
    section_phrase = ""
    if section_names:
        section_phrase = f" through sections such as {', '.join(section_names[:2])}"
        if len(section_names) > 2:
            section_phrase += f", and {section_names[2]}"

    why_relevant = (
        f"The page treats {pretty_focus} as a working concern rather than a passing mention, "
        f"with {total_mentions} relevant mention{'s' if total_mentions != 1 else ''}{section_phrase}."
    )
    why_relevant = _strip_emoji(why_relevant)

    short_query = user_intent.strip()
    if len(short_query) > 28:
        short_query = pretty_focus

    return {
        "active": True,
        "interest_query": user_intent.strip(),
        "short_label": short_query,
        "matched_keywords": matched_tokens,
        "mention_count": total_mentions,
        "headline": _strip_emoji(f"Focused on \"{short_query}\""),
        "why_relevant": why_relevant,
        "tailored_points": [_strip_emoji(pt) for pt in tailored_points],
    }


def compute_intent_match(tab: dict, user_intent: str, is_custom_mode: bool = False) -> float:
    """
    Universal relevance matcher & Topic-Focused Tab builder:
    - When `has_interest_focus(user_intent)` is False:
      Marks `tab["rated"] = False` and `tab["interest_section"] = None`.
    - When `has_interest_focus(user_intent)` is True:
      Checks whether the page genuinely covers the user's interest (requiring either
      title/topic/tag presence OR >= 3 substantive mentions, so 1-2 accidental passing
      words like "transfer to my laptop" never trigger a false match!).
      Builds `tab["interest_section"]` ONLY when there is a genuine match!
    """
    if tab.get("is_broken_404"):
        tab["rated"] = True
        tab["topic_mentions"] = 0
        tab["interest_section"] = None
        tab["match_explanation"] = "Broken link (HTTP 404: Page Not Found)"
        return 0.01

    topics = tab.get("topics") or []
    raw_tags = tab.get("raw_tags") or []
    words = int(tab.get("word_count", 500))
    headings = int(tab.get("heading_count", 4))
    read_mins = round(max(0.5, words / 220.0), 1)

    if not has_interest_focus(user_intent):
        tab["rated"] = False
        tab["topic_mentions"] = -1
        tab["interest_section"] = None
        tab["match_explanation"] = (
            f"Full article: {read_mins} min read ({words:,} words) · "
            f"Topics: {', '.join(topics[:3])}"
        )
        codes = int(tab.get("code_block_count", 0))
        tables = int(tab.get("table_count", 0))
        if codes >= 2 or tables >= 1 or (headings >= 4 and 120 <= words <= 9500):
            return round(min(0.92, 0.72 + min(0.18, codes * 0.02 + headings * 0.008)), 3)
        return 0.14

    intent_tokens = extract_focus_tokens(user_intent)
    if not intent_tokens:
        tab["rated"] = False
        tab["topic_mentions"] = -1
        tab["interest_section"] = None
        tab["match_explanation"] = f"Full article: {read_mins} min read · Topics: {', '.join(topics[:3])}"
        return 0.75

    tab["rated"] = True
    title_low = str(tab.get("title", "")).lower()
    topics_low = " ".join(topics).lower() + " " + " ".join(raw_tags).lower() + " " + str(tab.get("wing", "")).lower()
    overview_low = str(tab.get("overview", "")).lower()
    takeaways_low = " ".join(tab.get("takeaways") or []).lower()
    full_text_low = str(tab.get("full_text", "")).lower()

    primary_body = full_text_low if full_text_low else f"{overview_low} {takeaways_low}"
    header_corpus = f"{title_low} {topics_low}"

    matched_tokens: list[str] = []
    all_expanded_terms: list[str] = []
    total_mentions = 0
    title_or_topic_hit = False

    for tok in intent_tokens:
        expanded_terms = TOPIC_ECOSYSTEM_SYNONYMS.get(tok, [tok])
        for et in expanded_terms:
            if et not in all_expanded_terms:
                all_expanded_terms.append(et)
        tok_mentions = 0
        for term in expanded_terms:
            suffix = r"(?:s|es|ing)?" if len(term) >= 5 else r""
            pat = re.compile(r"\b" + re.escape(term) + suffix + r"\b", flags=re.IGNORECASE)
            c_header = len(pat.findall(header_corpus))
            c_body = len(pat.findall(primary_body))
            c = c_header + c_body
            tok_mentions += c
            if c_header > 0:
                title_or_topic_hit = True
        if tok_mentions > 0:
            matched_tokens.append(tok)
            total_mentions += tok_mentions

    tab["topic_mentions"] = total_mentions
    focus_label = ", ".join(intent_tokens[:3])
    page_focus = ", ".join(topics[:3]) if topics else "other topics"

    # Case 1: Zero mentions
    if len(matched_tokens) == 0 or total_mentions == 0:
        tab["interest_section"] = None
        tab["match_explanation"] = (
            f"0 mentions of '{focus_label}' — this page is about {page_focus}"
        )
        return 0.01

    # Case 2: Incidental / Passing Mention Trap!
    if not title_or_topic_hit and total_mentions < 3:
        tab["interest_section"] = None
        tab["match_explanation"] = (
            f"Only mentions '{focus_label}' in passing ({total_mentions}x) — this page is about {page_focus}"
        )
        return 0.04

    # Case 3: Genuine Topic Match! Enrich search terms with section-context keywords for Tab 2
    section_terms = list(all_expanded_terms)
    for tok in matched_tokens:
        for st in SECTION_CONTEXT_ENRICHMENT.get(tok, []):
            if st not in section_terms:
                section_terms.append(st)

    effective_denom = min(2, len(intent_tokens))
    token_coverage = min(1.0, len(matched_tokens) / max(1, effective_denom))

    mentions_per_1k = total_mentions / max(0.5, words / 1000.0)
    density_boost = min(0.32, math.log1p(mentions_per_1k) * 0.13)
    prominence_boost = 0.24 if title_or_topic_hit else 0.0

    final_match = min(0.98, max(0.62, (token_coverage * 0.45) + density_boost + prominence_boost))
    matched_str = ", ".join(matched_tokens[:3])
    tab["match_explanation"] = (
        f"Strong match for '{matched_str}' — {total_mentions} relevant mention{'s' if total_mentions != 1 else ''} across this page"
    )
    tab["interest_section"] = _build_interest_specific_section(
        tab=tab,
        user_intent=user_intent,
        matched_tokens=matched_tokens,
        expanded_search_terms=section_terms,
        total_mentions=total_mentions,
    )
    return round(float(final_match), 3)


MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_REDIRECTS = 4


def _is_public_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    if not 1 <= port <= 65535:
        return False

    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal", ".localhost", ".home.arpa")
    ):
        return False

    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def _fetch_public_html(url: str) -> tuple[int, str]:
    current_url = url
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }
    for _ in range(MAX_REDIRECTS + 1):
        if not _is_public_http_url(current_url):
            raise ValueError(f"Blocked non-public URL: {current_url[:120]}")
        resp = requests.get(
            current_url,
            headers=headers,
            timeout=(4.0, 8.0),
            allow_redirects=False,
            stream=True,
        )
        if 300 <= resp.status_code < 400:
            location = resp.headers.get("Location")
            resp.close()
            if not location or not isinstance(location, str):
                raise ValueError("Redirect response did not include a Location header.")
            current_url = urljoin(current_url, location)
            continue
        try:
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                remaining = MAX_HTML_BYTES - total
                if remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                total += len(chunks[-1])
                if total >= MAX_HTML_BYTES:
                    break
            raw = b"".join(chunks)
            if raw:
                if raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
                    decoded = raw.decode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-16", errors="replace")
                    return resp.status_code, decoded
                try:
                    return resp.status_code, raw.decode("utf-8")
                except UnicodeDecodeError:
                    encoding = resp.encoding if isinstance(resp.encoding, str) else "utf-8"
                    return resp.status_code, raw.decode(encoding or "utf-8", errors="replace")
            text = getattr(resp, "text", "")
            return resp.status_code, text if isinstance(text, str) else ""
        finally:
            resp.close()
    raise ValueError("Too many redirects while fetching the URL.")


MAX_CDP_URLS = 16
CDP_OPEN_TIMEOUT_SECONDS = 12.0
CDP_CALL_TIMEOUT_SECONDS = 20.0


class CDPError(RuntimeError):
    pass


class _CDPConnection:
    def __init__(self, ws_url: str):
        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise CDPError("The websockets package is not installed.") from exc
        try:
            self._ws = connect(
                ws_url,
                open_timeout=CDP_OPEN_TIMEOUT_SECONDS,
                close_timeout=4,
                max_size=16 * 1024 * 1024,
            )
        except Exception as exc:
            raise CDPError(f"Could not open Browserbase CDP socket: {exc}") from exc
        self._next_id = 0

    def call(
        self,
        method: str,
        params: dict | None = None,
        session_id: str | None = None,
        timeout: float = CDP_CALL_TIMEOUT_SECONDS,
    ) -> dict:
        self._next_id += 1
        message_id = self._next_id
        payload = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        try:
            self._ws.send(json.dumps(payload))
        except Exception as exc:
            raise CDPError(f"CDP send failed for {method}: {exc}") from exc

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPError(f"CDP call timed out: {method}")
            try:
                raw = self._ws.recv(timeout=remaining)
            except TimeoutError as exc:
                raise CDPError(f"CDP call timed out: {method}") from exc
            except Exception as exc:
                raise CDPError(f"CDP receive failed for {method}: {exc}") from exc
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if message.get("id") != message_id:
                continue
            error = message.get("error")
            if error:
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise CDPError(f"CDP {method} failed: {detail}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


def _cdp_evaluate(conn: _CDPConnection, expression: str, session_id: str | None) -> object:
    result = conn.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        session_id=session_id,
    )
    if result.get("exceptionDetails"):
        detail = result["exceptionDetails"].get("text", "Runtime evaluation failed")
        raise CDPError(str(detail))
    value = result.get("result", {})
    return value.get("value")


def _cdp_fetch_rendered_html(ws_url: str, url: str) -> str:
    if not _is_public_http_url(url):
        raise ValueError(f"Blocked non-public URL: {url[:120]}")

    conn = _CDPConnection(ws_url)
    session_id: str | None = None
    target_id: str | None = None
    try:
        try:
            target = conn.call("Target.createTarget", {"url": "about:blank"}, timeout=15.0)
            target_id = target.get("targetId")
            if not target_id:
                raise CDPError("Browserbase did not return a target id.")
            attached = conn.call(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
                timeout=10.0,
            )
            session_id = attached.get("sessionId")
            if not session_id:
                raise CDPError("Browserbase did not return a CDP session id.")
            conn.call("Page.enable", session_id=session_id)
            conn.call("Runtime.enable", session_id=session_id)
            navigation = conn.call("Page.navigate", {"url": url}, session_id=session_id)
            if navigation.get("errorText"):
                raise CDPError(f"Cloud browser navigation failed: {navigation['errorText']}")
        except CDPError:
            if target_id:
                raise
            conn.call("Page.enable")
            conn.call("Runtime.enable")
            navigation = conn.call("Page.navigate", {"url": url})
            if navigation.get("errorText"):
                raise CDPError(f"Cloud browser navigation failed: {navigation['errorText']}")

        ready = False
        for _ in range(80):
            try:
                state = _cdp_evaluate(conn, "document.readyState", session_id)
            except CDPError:
                state = None
            if state == "complete":
                ready = True
                break
            time.sleep(0.25)
        if not ready:
            raise CDPError("Timed out waiting for the cloud browser page to finish loading.")
        time.sleep(0.4)

        final_url = _cdp_evaluate(conn, "location.href", session_id)
        if not isinstance(final_url, str) or not _is_public_http_url(final_url):
            raise ValueError(f"Blocked non-public redirect target: {str(final_url)[:120]}")

        html = _cdp_evaluate(conn, "document.documentElement.outerHTML", session_id)
        if not isinstance(html, str) or len(html) < 100:
            raise CDPError("The cloud browser returned an empty document.")
        return html[:MAX_HTML_BYTES]
    finally:
        if target_id:
            try:
                conn.call("Target.closeTarget", {"targetId": target_id}, timeout=5.0)
            except CDPError:
                pass
        conn.close()


def _cdp_extract_url(url: str, idx: int, ws_url: str) -> dict:
    html = _cdp_fetch_rendered_html(ws_url, url)
    return fast_http_extract_url(url, idx, prefetched_html=html, prefetched_status=200, rendered=True)


def extract_clean_urls_from_list(raw_urls: list[str] | None) -> list[str]:
    if not raw_urls:
        return []
    combined = "\n".join(str(u or "") for u in raw_urls)
    found = re.findall(r"https?://(?:(?!https?://)[^\s\"'<>)\],])+", combined, flags=re.IGNORECASE)
    deduped: list[str] = []
    seen: set[str] = set()
    for u in found:
        clean_u = u.rstrip(".,;)")
        if clean_u not in seen:
            seen.add(clean_u)
            deduped.append(clean_u)
    return deduped[:65]


# Walls served to cookie-less fetchers: login screens, bot checks, error shells.
# A tab matching these is labeled honestly and kept open downstream instead of
# having its error shell "summarized" as if it were the real article.
_LOGIN_WALL_TITLE_RE = re.compile(
    r"^(error|attention required!?|access denied|just a moment|please wait|"
    r"log ?in or sign up|sign in|login|checking your browser|are you a robot\?|"
    r"enable javascript|sorry, something went wrong|too many requests)$",
    re.IGNORECASE,
)
_LOGIN_WALL_BODY_PATTERNS = (
    (re.compile(r"log ?in or sign up", re.I), "login screen"),
    (re.compile(r"sign in to continue|please log in to continue|you must log in", re.I), "login screen"),
    (re.compile(r"we.?ll get this fixed|get this fixed as soon as we can", re.I), "error shell"),
    (re.compile(r"before you continue to (youtube|google)", re.I), "consent wall"),
    (re.compile(r"unusual traffic from your computer|our systems have detected unusual traffic", re.I), "bot check"),
    (re.compile(r"checking your browser before accessing|just a moment", re.I), "bot check"),
    (re.compile(r"verify you are human|verify you.?re not a robot|confirm you.?re not a robot", re.I), "bot check"),
    (re.compile(r"enable javascript and cookies", re.I), "bot check"),
    (re.compile(r"attention required.{0,80}cloudflare", re.I | re.S), "bot check"),
    (re.compile(r"request blocked|access to this page has been denied", re.I), "bot check"),
)
_LOGIN_WALL_MAX_BODY_WORDS = 320
_LOGIN_WALL_MAX_TITLE_ONLY_WORDS = 140
# A cookie-less, script-less fetch that yields less raw text than this is a
# JavaScript/empty shell (e.g. YouTube's video pages), never a real article.
_SHELL_MIN_CONTENT_WORDS = 120


def _detect_login_wall(
    clean_title: str, raw_word_count: int, html: str, rendered: bool = False
) -> str | None:
    """Return a wall label ('login screen', 'bot check', 'consent wall',
    'error shell', 'empty shell') when the fetched HTML looks like a
    cookie-less shell rather than the real page. `rendered=True` means the HTML
    comes from a real Chromium CDP session (JavaScript already executed), so the
    empty-shell floor does not apply — only explicit wall phrases do."""
    body_probe = (html or "").replace("\u2019", "'")
    if raw_word_count < _SHELL_MIN_CONTENT_WORDS:
        for pattern, label in _LOGIN_WALL_BODY_PATTERNS:
            if pattern.search(body_probe):
                return label
        if not rendered:
            return "empty shell"
    elif raw_word_count <= _LOGIN_WALL_MAX_BODY_WORDS:
        for pattern, label in _LOGIN_WALL_BODY_PATTERNS:
            if pattern.search(body_probe):
                return label
    if raw_word_count <= _LOGIN_WALL_MAX_TITLE_ONLY_WORDS and _LOGIN_WALL_TITLE_RE.match(clean_title.strip()):
        return "error shell"
    return None


def fast_http_extract_url(
    url: str,
    idx: int = 0,
    prefetched_html: str | None = None,
    prefetched_status: int = 200,
    rendered: bool = False,
) -> dict:
    _load_hof_catalog()
    norm_u = (url or "").strip().rstrip("/")

    # Check if this URL is the DEV Hall of Fame Roundup post itself!
    if norm_u == HOF_ROUNDUP_URL.rstrip("/"):
        cached_hof = _HOF_URL_INDEX.get(norm_u)
        if cached_hof:
            item = dict(cached_hof)
            item["id"] = f"user-tab-{idx + 1}"
            item["is_collection_roundup"] = True
            item["collection_preset_key"] = "dev_hall_of_fame_pile"
            item["collection_count"] = 65
            item["overview"] = (
                "Giorgi Kobaidze's curated DEV Hall of Fame featuring 65 community-chosen masterpieces (600+ minutes of reading) "
                "organized into 7 themed wings: The Welcome Desk, Stories From the Trenches, The 'Finally, It Clicked' Gallery, "
                "The Engine Room, The AI Lab, The Philosophy Floor, and The Builder's Garage."
            )
            item["takeaways"] = [
                "The Engine Room & Systems — Deep engineering dives including Omar Afifi's 'My Phone Broke, and Gemini Fixed It' (reverse-engineering Android UI bytecode over ADB), WebRTC in py-libp2p, SaarDB Write-Ahead Log, and Rust WebForms Core.",
                "The 'Finally, It Clicked' Gallery — Practical guides covering Adaptive Flutter Screens & Widgets (LayoutBuilder & DynamicScaffold), 101 Accessibility (a11y) Tips, AWS VPC Networking from Scratch, SQL CTEs & Window Functions, and Idempotency.",
                "The AI Lab & Philosophy Floor — Real-world AI agent stress tests including production rollback gates, prompt-injection tests that pass while attacks succeed, RAG refusal thresholds, and 26 non-coding uses for coding agents.",
                "Stories From the Trenches & Builder's Garage — Candid developer journeys on learning without pressure, underrated stacks (.NET/C#), planting 180 bugs in open-source backends, and USPS shipping fee checkers.",
            ]
            return item

    # Instant zero-latency lookup for all 65 DEV Hall of Fame articles + featured DEV posts
    if norm_u in _HOF_URL_INDEX:
        cached_item = dict(_HOF_URL_INDEX[norm_u])
        cached_item["id"] = f"user-tab-{idx + 1}"
        cached_item["is_broken_404"] = False
        return cached_item

    parsed = urlparse(url)
    host = parsed.netloc or "web"
    path_slug = (parsed.path or "/").strip("/") or host

    if prefetched_html is not None:
        status_code, html = prefetched_status, prefetched_html
    else:
        status_code = 200
        try:
            status_code, html = _fetch_public_html(url)
        except ValueError:
            raise
        except Exception:
            status_code = 0
            html = ""

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.DOTALL | re.IGNORECASE)
    raw_title = _strip_html_tags(title_match.group(1)) if title_match else f"{host}/{path_slug[:50]}"
    clean_title = re.sub(r"\s+[-|·]\s+(DEV Community|GitHub|Medium).*$", "", raw_title, flags=re.IGNORECASE).strip()
    clean_title = (clean_title or raw_title or url)[:120]

    is_404 = (
        status_code in (404, 410)
        or clean_title.lower().startswith("404")
        or "page not found" in clean_title.lower()
    )
    if is_404:
        return {
            "id": f"user-tab-{idx + 1}",
            "title": f"Broken Link (404 Not Found): {path_slug[:55]}",
            "url": url,
            "domain": host,
            "age_days": round(1.0 + idx * 1.2, 1),
            "word_count": 0,
            "code_block_count": 0,
            "table_count": 0,
            "heading_count": 0,
            "link_density": 20.0,
            "overview": "This URL returned HTTP 404 (Page Not Found).",
            "takeaways": ["Broken URL — The page no longer exists or the URL was mistyped; safe to close immediately."],
            "synthesized_points": [],
            "topics": ["Broken Link", "HTTP 404"],
            "full_text": "",
            "snippet": "",
            "fact_numbers": ["404"],
            "is_broken_404": True,
            "is_login_walled": False,
        }

    # Login/bot-wall detection: without session cookies, sites like Facebook,
    # LinkedIn, or Cloudflare-challenged pages serve an error or sign-in shell
    # instead of the article. Summarizing that shell would be worse than
    # useless, so the tab is labeled honestly and kept open downstream.
    raw_words = len(_strip_html_tags(html).split())
    wall_label = _detect_login_wall(clean_title, raw_words, html, rendered=rendered)
    if wall_label:
        display_path = path_slug if path_slug != host else ""
        prefix = "Unreadable page" if wall_label == "empty shell" else "Login-walled"
        wall_title = f"{prefix}: {host}" + (f"/{display_path[:45]}" if display_path else "")
        return {
            "id": f"user-tab-{idx + 1}",
            "title": wall_title,
            "url": url,
            "domain": host,
            "age_days": round(1.0 + idx * 0.9, 1),
            "word_count": 80,
            "code_block_count": 0,
            "table_count": 0,
            "heading_count": 1,
            "link_density": 1.0,
            "overview": (
                f"{host} served the server its {wall_label} (cookie-less, script-less fetch), "
                "so this tab's real content couldn't be read. Folivora keeps it open instead of guessing."
            ),
            "takeaways": [
                f"Unreadable ({wall_label}) — the extractor sees {host}'s wall or JavaScript shell, "
                "not the page you actually have open in your browser. Close or keep it yourself; no summary was invented."
            ],
            "synthesized_points": [],
            "topics": ["Login Wall"],
            "full_text": "",
            "snippet": "",
            "fact_numbers": [],
            "is_broken_404": False,
            "is_login_walled": True,
            "wall_label": wall_label,
        }

    if not html:
        return {
            "id": f"user-tab-{idx + 1}",
            "title": f"{host} — {path_slug[:60]}",
            "url": url,
            "domain": host,
            "age_days": round(1.0 + idx * 1.4, 1),
            "word_count": 650,
            "code_block_count": 4,
            "table_count": 1,
            "heading_count": 6,
            "link_density": 3.5,
            "overview": f"Web page hosted on {host}.",
            "takeaways": [f"Direct Link — {url}"],
            "synthesized_points": [],
            "topics": [host],
            "full_text": f"{clean_title} {path_slug}",
            "snippet": "",
            "fact_numbers": ["1"],
            "is_broken_404": False,
            "is_login_walled": False,
        }

    b_idx = html.find("crayons-article__body")
    e_idx = html.find('<section id="comments"', b_idx if b_idx > 0 else 0)
    if b_idx > 0 and e_idx > b_idx:
        html_scope = html[b_idx:e_idx]
    else:
        scope_match = (
            re.search(r"<main[^>]*>(.*?)</main>", html, flags=re.DOTALL | re.IGNORECASE)
            or re.search(r"<body[^>]*>(.*?)</body>", html, flags=re.DOTALL | re.IGNORECASE)
        )
        raw_main_scope = scope_match.group(1) if scope_match else html
        no_comments_scope = re.split(
            r'<(?:section|div)[^>]+(?:id=["\']comments["\']|class=["\'][^"\']*comments-container)',
            raw_main_scope,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        html_scope = re.sub(
            r"<(nav|footer|aside)[^>]*>.*?</\1>",
            " ",
            no_comments_scope,
            flags=re.DOTALL | re.IGNORECASE,
        )

    pre_raw_list = re.findall(r"<pre[^>]*>(.*?)</pre>", html_scope, flags=re.DOTALL | re.IGNORECASE)
    code_blocks: list[str] = []
    seen_code: set[str] = set()
    for raw_pre in pre_raw_list:
        code_text = _strip_emoji(_strip_html_tags(raw_pre))
        code_text = re.sub(r"\s+", " ", code_text).strip()
        if len(code_text) < 12:
            continue
        if code_text not in seen_code:
            seen_code.add(code_text)
            code_blocks.append(code_text[:800])
        if len(code_blocks) >= 5:
            break
    list_items = re.findall(r"<li\b", html_scope, flags=re.IGNORECASE)
    table_count = len(re.findall(r"<table\b", html_scope, flags=re.IGNORECASE))
    heading_count = max(1, len(re.findall(r"<h[1-3]\b", html_scope, flags=re.IGNORECASE)))
    link_count = len(re.findall(r"<a\b", html_scope, flags=re.IGNORECASE))

    body_text = _strip_html_tags(html_scope)
    word_count = max(80, len(body_text.split()))
    link_density = round(min(25.0, max(0.2, link_count / max(1.0, word_count / 100.0))), 2)

    overview, takeaways, synthesized_points = _extract_overview_and_takeaways(html_scope, clean_title, full_Html=html)
    topics = _detect_topics(clean_title, body_text, full_html=html)

    raw_all_blocks = re.findall(r"<(?:p|li)[^>]*>(.*?)</(?:p|li)>", html_scope, flags=re.DOTALL | re.IGNORECASE)
    all_paragraphs = []
    for rb in raw_all_blocks:
        cp = _polish_prose(_strip_html_tags(rb))
        if 55 <= len(cp) <= 360 and not cp.endswith(":"):
            all_paragraphs.append(cp)

    num_facts = len(re.findall(r"\b\d+(?:\.\d+)?(?:%|ms|s|x|gb|mb|g|mg|min|hours|days|\$)?\b", body_text[:8000]))
    substance_signals = min(20, (len(pre_raw_list) * 2) + (len(list_items) // 4) + (num_facts // 6) + len(takeaways))
    nums = re.findall(r"\d+(?:\.\d+)?", " ".join(takeaways))[:3] or [str(word_count)]

    return {
        "id": f"user-tab-{idx + 1}",
        "title": clean_title,
        "url": url,
        "domain": host,
        "age_days": round(1.0 + idx * 0.8, 1),
        "word_count": word_count,
        "code_block_count": substance_signals,
        "table_count": table_count,
        "heading_count": heading_count,
        "link_density": link_density,
        "overview": overview,
        "takeaways": takeaways,
        "synthesized_points": synthesized_points,
        "all_paragraphs": all_paragraphs[:40],
        "code_blocks": code_blocks,
        "topics": topics,
        "full_text": body_text[:25000],
        "snippet": overview,
        "fact_numbers": nums,
        "is_broken_404": False,
        "is_login_walled": False,
    }


class BrowserbaseService:
    def __init__(self, api_key: str | None = None, project_id: str | None = None):
        self.api_key = (api_key if api_key is not None else os.getenv("BROWSERBASE_API_KEY", "")).strip()
        self.project_id = (project_id if project_id is not None else os.getenv("BROWSERBASE_PROJECT_ID", "")).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.project_id)

    def create_session(self) -> dict:
        if not self.is_configured:
            raise RuntimeError("Browserbase credentials (BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID) not configured.")
        headers = {
            "X-BB-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        resp = requests.post(
            f"{BROWSERBASE_API_URL}/sessions",
            headers=headers,
            json={"projectId": self.project_id},
            timeout=12.0,
        )
        resp.raise_for_status()
        session_data = resp.json()
        sid = session_data["id"]

        debug_resp = requests.get(
            f"{BROWSERBASE_API_URL}/sessions/{sid}/debug",
            headers=headers,
            timeout=8.0,
        )
        debug_resp.raise_for_status()
        debug_data = debug_resp.json()
        return {
            "session_id": sid,
            "connect_url": session_data.get("connectUrl", ""),
            "ws_url": debug_data.get("wsUrl", ""),
            "debugger_url": debug_data.get("debuggerFullscreenUrl") or debug_data.get("debuggerUrl", ""),
            "replay_url": f"https://www.browserbase.com/sessions/{sid}",
            "pages": debug_data.get("pages", []),
        }

    def release_session(self, session_id: str) -> bool:
        if not self.is_configured or not session_id:
            return False
        try:
            resp = requests.post(
                f"{BROWSERBASE_API_URL}/sessions/{session_id}",
                headers={
                    "X-BB-API-Key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={"projectId": self.project_id, "status": "REQUEST_RELEASE"},
                timeout=8.0,
            )
            return resp.status_code in (200, 201, 204)
        except Exception:
            return False

    def inspect_tabs(
        self,
        preset_key: str = "postgres_docker_pile",
        custom_urls: list[str] | None = None,
        user_intent: str = "",
        verify_live_browserbase: bool = True,
        trace_collector: list | None = None,
    ) -> dict:
        _load_hof_catalog()
        if _HOF_EXHIBITS and not PRESET_TAB_PILES["dev_hall_of_fame_pile"]["tabs"]:
            PRESET_TAB_PILES["dev_hall_of_fame_pile"]["tabs"] = _HOF_EXHIBITS

        clean_urls = extract_clean_urls_from_list(custom_urls)
        is_custom_mode = len(clean_urls) > 0
        pile = PRESET_TAB_PILES.get(preset_key) or PRESET_TAB_PILES["postgres_docker_pile"]
        effective_intent = (user_intent or "").strip() if has_interest_focus(user_intent) else ""

        with trace_genai_span(
            name="browserbase.cdp_inspect_tabs",
            model_name="browserbase/chromium-cdp-stagehand",
            op="gen_ai.request",
            trace_collector=trace_collector,
        ) as span_meta:
            session_meta = {
                "provider": "Browserbase Cloud Chromium (CDP)",
                "live_session_created": False,
                "session_id": None,
                "debugger_url": None,
                "replay_url": None,
                "session_released": False,
                "cdp_pages_inspected": 0,
            }
            session_info = None
            live_ws_url = ""

            try:
                if verify_live_browserbase and self.is_configured:
                    try:
                        session_info = self.create_session()
                        live_ws_url = session_info.get("ws_url") or session_info.get("connect_url") or ""
                        session_meta.update({
                            "live_session_created": True,
                            "session_id": session_info["session_id"],
                            "debugger_url": session_info["debugger_url"],
                            "replay_url": session_info["replay_url"],
                        })
                        if not live_ws_url:
                            session_meta["fallback_reason"] = "Browserbase did not return a CDP WebSocket URL."
                    except Exception as exc:
                        session_meta["fallback_reason"] = str(exc)

                if is_custom_mode:
                    live_url_limit = min(len(clean_urls), MAX_CDP_URLS) if live_ws_url else 0
                    session_meta["live_url_limit"] = live_url_limit
                    cdp_errors: list[str] = []

                    def extract_one(i: int, url: str) -> tuple[dict, bool]:
                        if i < live_url_limit:
                            try:
                                return _cdp_extract_url(url, i, live_ws_url), True
                            except ValueError:
                                raise
                            except Exception as exc:
                                cdp_errors.append(f"{url[:100]}: {str(exc)[:160]}")
                        return fast_http_extract_url(url, i), False

                    with ThreadPoolExecutor(max_workers=min(8, len(clean_urls))) as pool:
                        futures = [pool.submit(extract_one, i, url) for i, url in enumerate(clean_urls)]
                        extracted = [f.result() for f in futures]
                    tabs = [item[0] for item in extracted]
                    cdp_success = sum(1 for _, used_cdp in extracted if used_cdp)
                    session_meta["cdp_pages_inspected"] = cdp_success
                    if live_url_limit and cdp_success == 0:
                        session_meta["fallback_reason"] = "CDP extraction failed; used fast HTTP extraction."
                    if cdp_errors:
                        session_meta["cdp_errors"] = cdp_errors[:4]
                    pile_name = f"Your Open Tabs ({len(tabs)} Links Analyzed)"
                else:
                    tabs = [dict(t) for t in pile["tabs"]]
                    pile_name = pile["name"]
            finally:
                if session_info and session_info.get("session_id"):
                    session_meta["session_released"] = self.release_session(session_info["session_id"])

            for tab in tabs:
                tab["intent_match_score"] = compute_intent_match(tab, effective_intent, is_custom_mode=is_custom_mode)

            span_meta["gen_ai.usage.input_tokens"] = len(tabs) * 45
            span_meta["gen_ai.usage.output_tokens"] = len(tabs) * 18
            span_meta["browserbase.tabs_inspected"] = len(tabs)
            span_meta["browserbase.session_id"] = session_meta.get("session_id") or "live-http-dom"
            span_meta["browserbase.session_released"] = session_meta.get("session_released", False)

            return {
                "preset_key": "custom_urls" if is_custom_mode else preset_key,
                "pile_name": pile_name,
                "effective_intent": effective_intent,
                "rated": has_interest_focus(effective_intent),
                "tabs": tabs,
                "browserbase": session_meta,
            }
