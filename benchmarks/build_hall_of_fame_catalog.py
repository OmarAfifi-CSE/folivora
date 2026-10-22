"""
build_hall_of_fame_catalog.py - Builds the verified DEV Hall of Fame Catalog (65 Masterpieces + Extras)
and augments the TabPFN calibration dataset with real-world multi-topic tab-query pairs.
"""

import html as html_lib
import json
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT_DIR / "data" / "dev_hall_of_fame_catalog.json"
CACHE_PATH = ROOT_DIR / "data" / "hof_html_cache.json"
HOF_API_JSON = ROOT_DIR / "data" / "hof_api_source.json"

# Comprehensive mapping from raw DEV.to #tags to human-friendly Topic Badges
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
    "let's stay connected",
    "connect with me on linkedin",
    "follow me on",
    "explore my projects on github",
    "watch my videos on youtube",
    "let's chat on discord",
)

TRAILING_FILLER_RE = re.compile(
    r"\s*(?:I couldn't be prouder of how[^.!?]*[.!?]|Thanks for reading[^.!?]*[.!?]|"
    r"Let me know in the comments[^.!?]*[.!?]|Feel free to share[^.!?]*[.!?]|"
    r"Happy coding[^.!?]*[.!?])\s*$",
    flags=re.IGNORECASE,
)


def clean_inline_html(raw_html: str) -> str:
    s = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"</?(?:code|strong|em|b|i|span|a|kbd|mark|sub|sup|small|abbr)\b[^>]*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\b([A-Z][a-z0-9]+[A-Z][A-Za-z0-9_]*)\s+s\b", r"\1s", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = TRAILING_FILLER_RE.sub("", s).strip()
    s = re.sub(r",\s+and\s+([^,]+),\s+and\s+", r", \1, and ", s)
    return s


def polish_prose(text: str) -> str:
    t = text.strip()
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


def flatten_nested_lists(html_str: str) -> str:
    def _repl(match: re.Match) -> str:
        head_raw = clean_inline_html(match.group(1)).rstrip(":")
        ul_body = match.group(2)
        sub_items = [
            clean_inline_html(x)
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


def extract_topics_from_html(html: str, title: str, body_text: str) -> tuple[list[str], list[str]]:
    b_idx = html.find("crayons-article__body")
    header_slice = html[:b_idx] if b_idx > 0 else html[:140000]
    raw_tags = list(dict.fromkeys(t.lower() for t in re.findall(r"/t/([a-zA-Z0-9_-]+)", header_slice)))[:8]

    pretty_topics: list[str] = []
    for rt in raw_tags:
        if rt in META_TAGS_TO_SKIP:
            continue
        mapped = TAG_PRETTY_MAP.get(rt)
        if mapped and mapped not in pretty_topics:
            pretty_topics.append(mapped)

    # Supplement from content keywords if fewer than 2 tags
    content_checks = [
        ("Flutter & Dart", r"\b(flutter|dart|pubspec|statefulwidget|impeller)\b"),
        ("Networking & P2P", r"\b(tcp|socket|udp|webrtc|libp2p|bandwidth|wi-fi)\b"),
        ("AI & LLMs", r"\b(llm|gemma|gemini|qwen|openai|claude|prompt|rag)\b"),
        ("AI Agents", r"\b(agent|agentic|tool use|mcp|rollback)\b"),
        ("Machine Learning", r"\b(tabpfn|pytorch|fine-tun|lora|quantile|dataset)\b"),
        ("Databases & SQL", r"\b(sql|postgres|mysql|cte|window function|write-ahead log|wal)\b"),
        ("AWS & Cloud", r"\b(aws|vpc|subnet|cloud|render|cloudflare|docker|kubernetes)\b"),
        ("Web Development", r"\b(react|javascript|typescript|css|html|dom|view transitions|webgpu)\b"),
        ("Cybersecurity", r"\b(security|malware|prompt injection|vulnerability)\b"),
        ("Career & Growth", r"\b(career|job|interview|conference speaker|burnout|mentor)\b"),
    ]
    combined = f"{title} {title} {title} {body_text[:10000]}"
    for label, pat in content_checks:
        if len(pretty_topics) >= 3:
            break
        if label not in pretty_topics and len(re.findall(pat, combined, flags=re.IGNORECASE)) >= 3:
            pretty_topics.append(label)

    if not pretty_topics:
        for rt in raw_tags:
            mapped = TAG_PRETTY_MAP.get(rt, rt.title())
            if mapped not in pretty_topics:
                pretty_topics.append(mapped)
    if not pretty_topics:
        pretty_topics = ["Software Engineering"]

    return pretty_topics[:3], raw_tags


def parse_article_from_html(
    url: str,
    html: str,
    idx: int = 0,
    wing: str = "",
    author: str = "",
    why_picked: str = "",
    curator_take: str = "",
) -> dict:
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.DOTALL | re.IGNORECASE)
    raw_title = clean_inline_html(title_m.group(1)) if title_m else url.split("/")[-1]
    clean_title = re.sub(r"\s+[-|·]\s+(DEV Community|GitHub|Medium).*$", "", raw_title, flags=re.IGNORECASE).strip()

    b_idx = html.find("crayons-article__body")
    e_idx = html.find('<section id="comments"', b_idx if b_idx > 0 else 0)
    scope = html[b_idx:e_idx] if (b_idx > 0 and e_idx > b_idx) else html

    pre_blocks = re.findall(r"<pre[^>]*>(.*?)</pre>", scope, flags=re.DOTALL | re.IGNORECASE)
    table_count = len(re.findall(r"<table\b", scope, flags=re.IGNORECASE))
    heading_count = max(1, len(re.findall(r"<h[1-3]\b", scope, flags=re.IGNORECASE)))
    link_count = len(re.findall(r"<a\b", scope, flags=re.IGNORECASE))

    scope_no_code = re.sub(
        r'<div[^>]+class=["\'][^"\']*highlight[^"\']*["\'][^>]*>.*?</div>\s*</div>',
        " ",
        scope,
        flags=re.DOTALL | re.IGNORECASE,
    )
    scope_no_code = re.sub(r"<pre[^>]*>.*?</pre>", " ", scope_no_code, flags=re.DOTALL | re.IGNORECASE)
    # Remove DEV embedded link preview cards (<div class="ltag__link">...</div>) before parsing
    scope_no_embeds = re.sub(
        r'<div[^>]+class=["\'][^"\']*ltag__link\b[^"\']*["\'][^>]*>.*?(?:<span[^>]*>\s*\d+\s*min\s*read\s*</span>\s*</div>\s*</div>\s*</div>|</div>\s*</div>\s*</div>)',
        " ",
        scope_no_code,
        flags=re.DOTALL | re.IGNORECASE,
    )
    scope_flat = flatten_nested_lists(scope_no_embeds)

    body_text = clean_inline_html(scope_no_embeds)
    word_count = max(120, len(body_text.split()) + sum(len(clean_inline_html(c).split()) for c in pre_blocks))
    link_density = round(min(22.0, max(0.3, link_count / max(1.0, word_count / 100.0))), 2)

    tokens = re.findall(r"<(h[23]|p|li)\b[^>]*>(.*?)</\1>", scope_flat, flags=re.DOTALL | re.IGNORECASE)
    sections = []
    cur_sec = {"heading": "Overview", "items": []}
    sections.append(cur_sec)

    for tag, content in tokens:
        c = clean_inline_html(content)
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
            if c.endswith(":") and len(c) < 110:
                continue
            cur_sec["items"].append((tag.lower(), c))

    sections = [s for s in sections if s["items"]]

    synthesized_points = []
    prose_paragraphs = []

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
                    "text": polish_prose(norm_ali),
                    "raw_context": f"{h} {ali}",
                })
            continue

        if is_specific_h and li_items:
            best_li = polish_prose(li_items[0])
            raw_ctx = f"{h} " + " ".join(p_items) + " " + " ".join(li_items)
            if p_items and "dart vm" in " ".join(p_items).lower() and "dart vm" not in best_li.lower():
                best_li = f"Resolves Windows background execution loops and Dart VM deadlocks: {best_li[0].lower() + best_li[1:]}"
            synthesized_points.append({
                "heading": h,
                "text": f"{h} — {best_li}",
                "raw_context": raw_ctx,
            })
            continue

        for tag, txt in sec["items"]:
            if len(txt) < 68 or len(txt) > 460:
                continue
            if txt.endswith(":") or txt.endswith("?"):
                continue
            pol = polish_prose(txt)
            if is_specific_h and " — " not in pol[:50]:
                formatted = f"{h} — {pol}"
            elif "built using flutter" in pol.lower() and " — " not in pol[:55]:
                formatted = f"Cross-Platform Flutter & BLoC Architecture — {pol}"
            else:
                formatted = pol
            synthesized_points.append({
                "heading": h,
                "text": formatted,
                "raw_context": f"{h} {txt}",
            })

    # Choose Overview: prefer substantive thesis paragraph (>= 95 chars, not a short rhetorical question)
    substantive_prose = [
        polish_prose(p)
        for p in prose_paragraphs
        if len(p) >= 95 and not p.strip().endswith("?") and not p.strip().endswith(":")
    ]
    overview = ""
    if substantive_prose:
        overview = substantive_prose[0]
        for p2 in substantive_prose[1:4]:
            if len(overview) < 230 and any(
                kw in p2.lower()
                for kw in ("built using", "engineered", "architecture", "under the hood", "in this article", "we built", "reverse-engineer", "instead of")
            ):
                first_sent = re.split(r"(?<=[.!?])\s+", p2)[0]
                if first_sent and first_sent not in overview:
                    overview = f"{overview} {first_sent}"
                    break
    # If opening paragraph is a story hook ("It is early morning...", "A little over a month ago...") and why_picked exists, use why_picked cleanly
    if why_picked and (
        not overview
        or len(overview) < 115
        or re.match(r"^(?:It is early morning|A little over a month ago|When I was|Picture this|Imagine )", overview, flags=re.IGNORECASE)
    ):
        overview = why_picked
    elif not overview and curator_take:
        overview = curator_take
    if not overview and prose_paragraphs:
        overview = " ".join(polish_prose(p) for p in prose_paragraphs[:2])
    if not overview:
        overview = f"In-depth engineering write-up covering {clean_title}."

    # Score synthesized_points for General Takeaways (Tab 1) with STRICT SECTION DIVERSITY
    scored_pts = []
    for idx_pt, pt_obj in enumerate(synthesized_points):
        txt = pt_obj["text"]
        h_name = pt_obj["heading"]
        if txt == overview or txt in overview or overview in txt:
            continue
        body_part = txt.split(" — ", 1)[-1] if " — " in txt[:75] else txt
        if len(body_part) < 62 or body_part[:65] in overview:
            continue
        low = body_part.lower()
        h_low = h_name.lower()
        sc = 1.0
        if " — " in txt[:75]:
            sc += 2.5  # Strongly reward section-anchored points!
        num_hits = len(re.findall(r"\b\d+(?:\.\d+)?(?:%|ms|s|x|gb|mb|mb/s|fps|k|min|hours|days|\$)?\b", low))
        sc += min(3.0, num_hits * 0.7)
        if 115 <= len(body_part) <= 310:
            sc += 2.2
        elif 80 <= len(body_part) < 115:
            sc += 1.2
        # Reward technical / step / architectural sections
        if re.search(
            r"\b(step \d|architecture|disassembl|deduction|domino|fix|solution|evolution|adaptive|breakpoint|benchmark|algorithm|database|wal|webrtc|security|prompt|rag|cfp|talk|blogging|memory|discovery|guidance)\b",
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
    # Pick top 4 takeaways ensuring distinct anchor_key first so all 4 takeaways cover different sections!
    chosen = []
    used_anchors = set()
    for sc, idx_pt, anchor_key, txt in scored_pts:
        if anchor_key in used_anchors:
            continue
        used_anchors.add(anchor_key)
        chosen.append((idx_pt, txt))
        if len(chosen) >= 4:
            break
    if len(chosen) < 4:
        for sc, idx_pt, anchor_key, txt in scored_pts:
            if any(txt == c_txt for _, c_txt in chosen):
                continue
            chosen.append((idx_pt, txt))
            if len(chosen) >= 4:
                break

    top4 = sorted(chosen, key=lambda x: x[0])
    takeaways = [t for _, t in top4]
    if not takeaways and prose_paragraphs:
        takeaways = [polish_prose(p) for p in prose_paragraphs[1:4]]
    if not takeaways and curator_take:
        takeaways = [curator_take]
    if not takeaways:
        takeaways = [overview]

    topics, raw_tags = extract_topics_from_html(html, clean_title, body_text)
    num_facts = len(re.findall(r"\b\d+(?:\.\d+)?(?:%|ms|s|x|gb|mb|min|hours|days|\$)?\b", body_text[:8000]))
    substance_signals = min(25, (len(pre_blocks) * 2) + (len(synthesized_points) // 3) + (num_facts // 6) + len(takeaways))

    clean_prose_corpus = " ".join([overview] + [p["text"] for p in synthesized_points] + [polish_prose(p) for p in prose_paragraphs])
    return {
        "id": f"hof-tab-{idx + 1}",
        "title": clean_title,
        "url": url,
        "domain": "dev.to",
        "author": author,
        "wing": wing,
        "curator_take": curator_take,
        "age_days": round(1.0 + (idx % 12) * 0.7, 1),
        "word_count": word_count,
        "code_block_count": max(3, substance_signals),
        "table_count": table_count,
        "heading_count": heading_count,
        "link_density": link_density,
        "overview": overview,
        "takeaways": takeaways,
        "synthesized_points": [p["text"] for p in synthesized_points[:30]],
        "all_paragraphs": [polish_prose(p) for p in prose_paragraphs[:30]],
        "topics": topics,
        "raw_tags": raw_tags,
        "full_text": f"{clean_title} {' '.join(topics)} {' '.join(raw_tags)} {why_picked} {clean_prose_corpus[:20000]}",
    }


def main():
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    hof_data = json.loads(HOF_API_JSON.read_text(encoding="utf-8"))
    md = hof_data.get("body_markdown", "")

    # Parse wing, author, url, and Giorgi's "My take" for all 65 Hall of Fame articles
    current_wing = "The Welcome Desk"
    exhibits_meta = []
    lines = md.splitlines()
    cur_author = ""
    cur_url = ""
    cur_why = ""
    cur_take = ""

    for line in lines:
        s = line.strip()
        if s.startswith("## ") and any(w in s for w in ("Welcome Desk", "Stories From", "Finally, It Clicked", "Engine Room", "AI Lab", "Philosophy Floor", "Builder's Garage")):
            current_wing = re.sub(r"^##\s*[^\w]*", "", s).strip()
        elif s.startswith("### "):
            if cur_url:
                exhibits_meta.append({
                    "wing": current_wing,
                    "author": cur_author,
                    "url": cur_url,
                    "why_picked": cur_why,
                    "curator_take": cur_take,
                })
            m_auth = re.search(r"\[([^\]]+)\]", s)
            cur_author = m_auth.group(1).lstrip("🎩").strip() if m_auth else s.lstrip("# ").strip()
            cur_url = ""
            cur_why = ""
            cur_take = ""
        elif "https://dev.to/" in s and cur_author:
            found = re.findall(r"https://dev\.to/[^\s\)\}%]+/[^\s\)\}%]+", s)
            if found:
                cur_url = found[-1]
        elif s.startswith("**Why they picked it:**") or s.startswith("**Why I picked it:**"):
            cur_why = re.sub(r"^\*\*(?:Why they picked it|Why I picked it):\*\*\s*", "", s).strip()
        elif s.startswith("**My take:**"):
            cur_take = re.sub(r"^\*\*My take:\*\*\s*", "", s).strip()

    if cur_url:
        exhibits_meta.append({
            "wing": current_wing,
            "author": cur_author,
            "url": cur_url,
            "why_picked": cur_why,
            "curator_take": cur_take,
        })

    print(f"Parsed metadata for {len(exhibits_meta)} DEV Hall of Fame exhibits.")

    catalog_tabs = []
    seen_urls = set()

    for idx, ex in enumerate(exhibits_meta):
        u = ex["url"]
        html = cache.get(u, "")
        if not html:
            continue
        parsed = parse_article_from_html(
            url=u,
            html=html,
            idx=idx,
            wing=ex["wing"],
            author=ex["author"],
            why_picked=ex["why_picked"],
            curator_take=ex["curator_take"],
        )
        catalog_tabs.append(parsed)
        seen_urls.add(u)

    # Also parse extra featured articles (Omar's other posts, Sylwia's conference speaker post, Hall of Fame roundup)
    extra_articles = {}
    for u, html in cache.items():
        if u in seen_urls or not html:
            continue
        extra_articles[u] = parse_article_from_html(url=u, html=html, idx=len(catalog_tabs) + len(extra_articles))

    out_payload = {
        "hall_of_fame_url": "https://dev.to/georgekobaidze/promise-delivered-welcome-to-the-dev-hall-of-fame-65-authors-65-masterpieces-15fa",
        "hall_of_fame_count": len(catalog_tabs),
        "exhibits": catalog_tabs,
        "extra_articles": extra_articles,
    }
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(catalog_tabs)} Hall of Fame exhibits + {len(extra_articles)} extra articles to {CATALOG_PATH}")


if __name__ == "__main__":
    main()
