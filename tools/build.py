#!/usr/bin/env python3
"""
Génère le contenu du gopherhole servi par Gophernicus à partir des dépôts
voisins :

  ../portfolio        -> sections presentation, projets, ctf
  ../Fracorbas-Docs   -> sections docs (arborescence Docusaurus) et blog

Les fichiers générés sont écrits à la racine du dépôt (le CI les rsync
ensuite vers le VPS, où le conteneur Gophernicus les sert en direct).

Usage :
    python3 tools/build.py            # depuis la racine du dépôt gopher

Règles Gophernicus utilisées :
  - une ligne de gophermap SANS tabulation = texte d'information
  - "Xnom<TAB>sélecteur<TAB>hôte<TAB>port" = lien (hôte/port facultatifs)
  - "!Titre" en première ligne = titre du menu
  - une gophermap remplace totalement le listing auto du répertoire,
    y compris le lien parent : on ajoute donc des liens "Retour" explicites
  - types : 0 texte, 1 répertoire, d document (pdf), I image, h lien web
"""

import html as htmlmod
import json
import re
import shutil
import subprocess
import textwrap
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO.parent / "Fracorbas-Docs"
PORTFOLIO = REPO.parent / "portfolio"
STATIC = DOCS / "static"

WRAP = 72          # largeur de reflow des paragraphes
BAR = " " + "=" * 40   # séparateur ASCII (espace initiale : '=' seul est une directive gophernicus)

# Caractères qui, en début de ligne de gophermap, ont une signification
# spéciale : on les préfixe d'une espace pour éviter toute ambiguïté.
SPECIAL = ("#", "!", "-", ":", "~", "%", "=", "*", ".", "\t")


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def slugify(name):
    """Convertit un nom de fichier en slug ascii kebab-case gopher-friendly."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", s).strip("-").lower()
    return s or "sans-nom"


def info(line):
    """Formate une ligne d'information de gophermap."""
    line = line.rstrip()
    if line and line[0] in SPECIAL:
        line = " " + line
    return line if line else ""


def link(item_type, name, selector, host=None, port=None):
    """Formate une ligne de lien de gophermap (tabulations simples)."""
    fields = [f"{item_type}{name}", selector]
    if host is not None:
        fields.append(host)
        if port is not None:
            fields.append(str(port))
    return "\t".join(fields)


def web_link(name, url):
    return link("h", name, f"URL:{url}")


def write_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o644)


def write_gophermap(path, title, lines):
    # Une ligne sans tabulation = info ; mais les caractères spéciaux
    # ('=', '-', '*', '.', ':', '~', '%', '#', '!') en position initiale
    # sont des directives gophernicus -> on préfixe d'une espace.
    safe = []
    for line in lines:
        if "\t" not in line and line and line[0] in SPECIAL:
            line = " " + line
        safe.append(line)
    content = "!{}\n".format(title) + "\n".join(safe) + "\n.\n"
    write_file(path, content)


def parse_frontmatter(text):
    """Parseur frontmatter YAML minimal (clé: valeur, listes [a, b])."""
    meta = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for raw in text[3:end].splitlines():
                if not raw or raw[:1].isspace():
                    continue
                m = re.match(r"([A-Za-z_]+):\s*(.*)", raw)
                if not m:
                    continue
                key, value = m.group(1).lower(), m.group(2).strip()
                if value.startswith("[") and value.endswith("]"):
                    value = [v.strip().strip("\"'") for v in value[1:-1].split(",")]
                else:
                    value = value.strip("\"'")
                meta[key] = value
            body = text[end + 4:]
    return meta, body


def prettify(name):
    """'Le-modele-OSI' -> 'Le modele OSI'"""
    return str(name).replace("-", " ").replace("_", " ").strip()


def title_from(md_path, meta):
    for key in ("title", "sidebar_label", "description"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().strip("\"'")
    return prettify(md_path.stem)


# ---------------------------------------------------------------------------
# Conversion Markdown -> texte brut
# ---------------------------------------------------------------------------

IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
ADMON_LABELS = {"tip": "ASTUCE", "warning": "ATTENTION", "note": "NOTE", "info": "INFO", "danger": "DANGER", "caution": "ATTENTION"}


def clean_inline(text):
    text = LINK_RE.sub(
        lambda m: f"{m.group(1)} ({m.group(2)})"
        if m.group(2).startswith("http")
        else m.group(1),
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<![\\\w])_([^_\n]+)_(?!\w)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = htmlmod.unescape(text)
    return text


def resolve_img_src(src, md_path):
    if src.startswith("/"):
        return (STATIC / src.lstrip("/")).resolve()
    return (md_path.parent / src).resolve()


def wrap_block(text, first_indent="", next_indent=""):
    return textwrap.wrap(
        text,
        width=WRAP,
        initial_indent=first_indent,
        subsequent_indent=next_indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]


def md_to_text(md_path):
    """
    Convertit un fichier Markdown en texte brut gopher.
    Retourne (lignes, images) où images = [{src, alt}] dans l'ordre.
    """
    raw = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)
    images = []
    out = []
    para = []

    def flush():
        nonlocal para
        if para:
            text = clean_inline(" ".join(para))
            for piece in text.split("\n"):
                out.extend(wrap_block(piece))
            para = []

    def add_list_item(line):
        flush()
        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)", clean_inline(line))
        if m:
            indent, marker, rest = m.groups()
            out.extend(wrap_block(
                f"{marker} {rest}",
                first_indent=indent,
                next_indent=indent + " " * (len(marker) + 1),
            ))

    in_code = False
    admon = None  # (label, titre) si dans une admonition

    for raw_line in body.splitlines():
        line = raw_line.rstrip()

        # Blocs de code
        if in_code:
            if line.lstrip().startswith("```"):
                out.append("")
                in_code = False
            else:
                out.append("    " + raw_line)
            continue
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            in_code = True
            lang = stripped.lstrip("`").strip()
            out.append("")
            out.append("    --- code {} ---".format(lang) if lang else "    --- code ---")
            continue

        # Fin d'admonition
        if admon and stripped == ":::":
            flush()
            admon = None
            out.append("")
            continue

        # Début d'admonition
        m = re.match(r"^:::\s*([a-zA-Z]+)\s*(.*)", line)
        if m:
            flush()
            kind = m.group(1).lower()
            label = ADMON_LABELS.get(kind, kind.upper())
            title = m.group(2).strip()
            admon = (label, title)
            out.append("")
            out.append(">>> {} : {}".format(label, title) if title else ">>> {} :".format(label))
            continue

        # Truncate marker Docusaurus
        if re.match(r"^<!--\s*truncate\s*-->$", stripped, re.I):
            flush()
            out.append("")
            continue

        # Commentaire HTML
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue

        # Image
        if IMG_RE.search(line):
            flush()
            def repl(m):
                alt, src = m.group(1), m.group(2)
                images.append({"src": resolve_img_src(src, md_path), "alt": clean_inline(alt) or Path(src).name})
                return ""
            line = IMG_RE.sub(repl, line).strip()
            if line:
                out.append("  [image : {}]".format(images[-1]["alt"]))
            continue

        # Titre
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush()
            level, text = len(m.group(1)), clean_inline(m.group(2))
            out.append("")
            if level == 1:
                t = text.upper()
                out.append(t)
                out.append("=" * min(len(t), WRAP))
            elif level == 2:
                out.append(text)
                out.append("-" * min(len(text), WRAP))
            else:
                out.append("- " + text)
            continue

        # Ligne horizontale
        if re.match(r"^-{3,}$|^\*{3,}$|^_{3,}$", stripped):
            flush()
            out.append(BAR)
            continue

        # Tableau : conservé tel quel (lisible en monospace)
        if stripped.startswith("|"):
            flush()
            out.append(line)
            continue

        # Bloc de citation
        m = re.match(r"^\s*>\s?(.*)", line)
        if m:
            flush()
            out.extend(wrap_block(clean_inline(m.group(1)), first_indent="| ", next_indent="| "))
            continue

        # Paragraphe / liste
        if stripped:
            if re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
                add_list_item(line)
            else:
                para.append(line.strip())
        else:
            flush()
            out.append("")

    flush()
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()

    # Lignes d'info d'en-tête (date, tags)
    header = []
    if isinstance(meta.get("date"), str):
        header.append("Le {}".format(meta["date"]))
    if isinstance(meta.get("tags"), list):
        header.append("Tags : " + ", ".join(meta["tags"]))
    return out, images, meta, header


def served_images(images, seen):
    """Filtre les images réellement copiées et attache leur sélecteur gopher."""
    alts = {}
    for img in images:
        alts.setdefault(img["src"], img["alt"])
    return [{"src": src, "alt": alts.get(src, ""), "path": path}
            for src, path in seen.items()]


def build_text_doc(title, lines, images, header=None):
    """Assemble le contenu final d'un fichier texte servi en gophertype 0."""
    sep = "=" * min(max(len(title), 3), WRAP)
    content = [sep, title, sep, ""]
    if header:
        content.append(" / ".join(header))
        content.append("")
    content.append("")
    content.extend(lines)
    if images:
        content.append("")
        content.append("-" * WRAP)
        content.append("Images associées (accessibles depuis le menu gopher) :")
        for img in images:
            content.append("  - {} : {}".format(img["path"], img["alt"]))
    return "\n".join(content) + "\n"


# ---------------------------------------------------------------------------
# Conversion HTML -> texte (via lynx)
# ---------------------------------------------------------------------------

def html_to_text(html_path, portfolio_page=False):
    """Convertit du HTML en texte via lynx.

    portfolio_page : retire la navigation (boutons, emojis) des pages du
    portfolio, en ne gardant que le contenu à partir du titre principal.
    """
    result = subprocess.run(
        ["lynx", "-dump", "-nolist", "-display_charset=UTF-8",
         "-assume_charset=UTF-8", str(html_path)],
        capture_output=True, text=True,
    )
    text = result.stdout
    if not portfolio_page:
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    cleaned = []
    for line in text.splitlines():
        stripped = line.strip()
        if "(BUTTON)" in stripped:
            continue
        if "retour au terminal" in stripped:
            continue
        # Lignes sans caractère alphabétique : emojis des cartes, etc.
        if stripped and not re.search(r"[A-Za-zÀ-ÿ]", stripped):
            continue
        cleaned.append(line)
    text = "\n".join(cleaned)

    # Démarre au titre principal (skip le header restant)
    match = re.search(r"^\s*Bastien BONORA\s*$", text, re.M)
    if match:
        text = text[match.start():]
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# Section : présentation (portfolio)
# ---------------------------------------------------------------------------

def build_presentation():
    out = REPO / "presentation"
    if out.exists():
        shutil.rmtree(out)

    pres_root = PORTFOLIO / "root" / "presentation"
    entries = []

    entries.append(link("0", "À propos de moi (parcours, compétences)", "/presentation/a-propos.txt"))
    write_file(out / "a-propos.txt",
               html_to_text(PORTFOLIO / "src/HTML/presentation.html", portfolio_page=True) + "\n")

    cv = pres_root / "CV" / "CV_Bastien_BONORA_2025.pdf"
    if cv.exists():
        shutil.copy(cv, out / "cv-bastien-bonora-2025.pdf")
        entries.append(link("d", "Mon CV (PDF)", "/presentation/cv-bastien-bonora-2025.pdf"))

    png = pres_root / "Projet_Orientation.png"
    if png.exists():
        shutil.copy(png, out / "projet-orientation.png")
        entries.append(link("I", "Mon projet d'orientation (image)", "/presentation/projet-orientation.png"))

    releve = pres_root / "Releve_Note_Bac.pdf"
    if releve.exists():
        shutil.copy(releve, out / "releve-note-bac.pdf")
        entries.append(link("d", "Relevé de notes du Bac (PDF)", "/presentation/releve-note-bac.pdf"))

    pubkey = pres_root / "pubkey"
    if pubkey.exists():
        shutil.copy(pubkey, out / "pgp-publique.txt")
        entries.append(link("0", "Clé publique PGP", "/presentation/pgp-publique.txt"))

    # Certifications
    certif_src = PORTFOLIO / "root" / "certif"
    certif_entries = []
    if certif_src.exists():
        certif_out = out / "certifications"
        certif_out.mkdir(parents=True, exist_ok=True)
        for pdf in sorted(certif_src.glob("*.pdf")):
            name = slugify(pdf.stem) + ".pdf"
            shutil.copy(pdf, certif_out / name)
            label = prettify(pdf.stem).replace(" Certification", "").replace("Certification ", "").strip()
            certif_entries.append(link("d", "Certification " + label + " (PDF)",
                                       "/presentation/certifications/" + name))
        write_gophermap(
            out / "certifications" / "gophermap",
            "Certifications",
            ["", "Les certifications que j'ai obtenues.", ""]
            + certif_entries
            + ["", link("1", "Retour à la présentation", "/presentation")],
        )
        entries.append(link("1", "Mes certifications", "/presentation/certifications"))

    write_gophermap(
        out / "gophermap",
        "Présentation - Bastien BONORA",
        ["", "Passionné par l'informatique et la cybersécurité,", "étudiant en Réseaux et Télécoms à l'IUT d'Annecy.", ""]
        + entries
        + ["", link("1", "Retour au menu principal", "/")],
    )


# ---------------------------------------------------------------------------
# Section : projets (portfolio)
# ---------------------------------------------------------------------------

def build_projets():
    out = REPO / "projets"
    if out.exists():
        shutil.rmtree(out)
    write_gophermap(
        out / "gophermap",
        "Projets",
        [
            "",
            "Nastruire (2022)",
            "  Site web réalisé en équipe pendant mes études :",
            "  premiers pas dans la gestion de projet et la",
            "  collaboration sur un projet de groupe.",
            web_link("Voir le site Nastruire", "https://nastruire.fr"),
            "",
            "Bastodoc - documentation personnelle",
            "  Ma documentation technique (Docusaurus) :",
            "  Linux, Docker, réseaux, hacking, debug...",
            "  Une partie est aussi servie ici, en gopher.",
            web_link("Voir la documentation web", "https://docs.bastienbonora.fr"),
            link("1", "La doc, version gopher", "/docs"),
            "",
            "Ce gopherhole",
            "  Le portfolio, la doc et le blog version",
            "  old-school, servis par Gophernicus.",
            "",
            link("1", "Retour au menu principal", "/"),
        ],
    )


# ---------------------------------------------------------------------------
# Section : CTF (write-ups)
# ---------------------------------------------------------------------------

def build_ctf():
    out = REPO / "ctf"
    if out.exists():
        shutil.rmtree(out)

    entries = []
    for html_file in sorted((PORTFOLIO / "root" / "CTF").glob("*.html")):
        name = slugify(html_file.stem) + ".txt"
        text = html_to_text(html_file)
        text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
        write_file(out / name, text)
        entries.append(link("0", "TryHackMe - {} (write-up)".format(prettify(html_file.stem)),
                            "/ctf/" + name))

    # Les write-ups CTF issus de la doc sont déjà convertis dans /docs,
    # on les référence ici pour centraliser les challenges.
    docs_ctf = REPO / "docs" / "hacking" / "tryhackme" / "ctf"
    if docs_ctf.exists():
        for txt in sorted(docs_ctf.glob("*.txt")):
            entries.append(link("0", "TryHackMe - {} (write-up)".format(prettify(txt.stem)),
                                "/docs/hacking/tryhackme/ctf/" + txt.name))

    write_gophermap(
        out / "gophermap",
        "CTF - write-ups",
        ["", "Comptes-rendus de challenges TryHackMe et CTF.", ""]
        + entries
        + ["", link("1", "Retour au menu principal", "/")],
    )


# ---------------------------------------------------------------------------
# Section : documentation (Fracorbas-Docs/docs)
# ---------------------------------------------------------------------------

def category_info(src_dir):
    """Retourne (label, position) depuis un _category_.json."""
    cat_file = src_dir / "_category_.json"
    if cat_file.exists():
        try:
            data = json.loads(cat_file.read_text(encoding="utf-8"))
            return data.get("label", None), data.get("position", 99)
        except (json.JSONDecodeError, OSError):
            pass
    return None, 99


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}


def convert_md_file(md_path, out_dir, selector_prefix):
    """
    Convertit un .md en .txt dans out_dir, copie ses images dans
    out_dir/images/<slug>/ et retourne (meta, lignes de menu).
    """
    lines, images, meta, header = md_to_text(md_path)
    title = title_from(md_path, meta)

    doc_slug = slugify(md_path.stem)
    txt_name = doc_slug + ".txt"
    selector = "{}/{}".format(selector_prefix.rstrip("/"), txt_name)

    # Copie des images effectivement présentes sur le disque
    img_dir = out_dir / "images" / doc_slug
    seen = {}
    for img in images:
        src = img["src"]
        if not src.exists() or src in seen:
            continue
        target = slugify(src.name)
        img_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, img_dir / target)
        seen[src] = "{}/images/{}/{}".format(selector_prefix.rstrip("/"), doc_slug, target)

    # Images réellement servies (pour l'en-tête du fichier texte)
    served = served_images(images, seen)
    write_file(out_dir / txt_name, build_text_doc(title, lines, served, header))

    menu_lines = [link("0", title, selector)]
    for img in served:
        menu_lines.append(link("I", "  [image] {}".format(img["alt"]), img["path"]))
    return meta, menu_lines


def flatten_blocks(blocks):
    """blocks = [(position, label, lignes)] -> lignes de menu séparées par des lignes vides."""
    lines = []
    for _, _, block in blocks:
        lines.extend(block)
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def build_docs_dir(src_dir, out_dir, selector, parent_selector, parent_label,
                   title=None, intro=None):
    """
    Convertit récursivement un répertoire de la doc Docusaurus et écrit
    son gophermap. Retourne les lignes du gophermap du répertoire.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    label, position = category_info(src_dir)
    label = label or prettify(src_dir.name)
    description = None
    cat_file = src_dir / "_category_.json"
    if cat_file.exists():
        try:
            data = json.loads(cat_file.read_text(encoding="utf-8"))
            link_data = data.get("link") or {}
            if isinstance(link_data, dict) and link_data.get("description"):
                description = link_data["description"]
        except (json.JSONDecodeError, OSError):
            pass

    blocks = []
    for item in sorted(src_dir.iterdir()):
        if item.name.startswith("_"):
            continue
        if item.suffix.lower() in (".md", ".mdx"):
            meta, menu_lines = convert_md_file(item, out_dir, selector)
            pos = meta.get("sidebar_position")
            try:
                pos = float(pos) if pos is not None else 99.0
            except (TypeError, ValueError):
                pos = 99.0
            blocks.append((pos, item.name, menu_lines))
        elif item.is_dir():
            # Ignore les dossiers de pure images (aucun .md dedans)
            if not any(item.rglob("*.md")) and not any(item.rglob("*.mdx")):
                continue
            sub_label, sub_pos = category_info(item)
            sub_label = sub_label or prettify(item.name)
            sub_selector = "{}/{}".format(selector.rstrip("/"), slugify(item.name))
            build_docs_dir(item, out_dir / slugify(item.name), sub_selector,
                           selector, title or label)
            blocks.append((sub_pos, sub_label, [link("1", sub_label, sub_selector)]))

    blocks.sort(key=lambda b: (b[0], b[1].lower()))
    lines = flatten_blocks(blocks)

    if intro:
        header = [""] + (intro if isinstance(intro, list) else [intro])
    elif description:
        header = ["", description]
    else:
        header = []
    if parent_selector:
        retour = link("1", "Retour : {}".format(parent_label), parent_selector)
    else:
        retour = link("1", "Retour au menu principal", "/")
    write_gophermap(out_dir / "gophermap", title or label, header + lines + ["", retour])
    return lines


def build_docs():
    src_root = DOCS / "docs"
    if not src_root.exists():
        raise SystemExit(f"Source introuvable : {src_root}")
    out = REPO / "docs"
    if out.exists():
        shutil.rmtree(out)

    build_docs_dir(
        src_root, out, "/docs", None, None,
        title="Documentation technique (Bastodoc)",
        intro=["Ma documentation perso : systèmes, protocoles,",
               "programmation, debug et hacking."],
    )


# ---------------------------------------------------------------------------
# Section : blog (Fracorbas-Docs/blog)
# ---------------------------------------------------------------------------

def build_blog():
    src = DOCS / "blog"
    out = REPO / "blog"
    if out.exists():
        shutil.rmtree(out)

    posts = []
    for md_path in sorted(src.glob("**/*.md*")):
        if md_path.name in ("authors.yml", "tags.yml"):
            continue
        lines, images, meta, header = md_to_text(md_path)
        # Convention Docusaurus : les posts en dossier ont un index.md
        # -> on utilise le nom du dossier parent comme slug
        stem = md_path.stem
        if stem == "index" and md_path.parent != src:
            stem = md_path.parent.name
        post_slug = slugify(stem)
        if isinstance(meta.get("title"), str) and meta["title"].strip():
            title = meta["title"].strip().strip("\"'")
        else:
            title = prettify(stem)
        txt_name = post_slug + ".txt"
        selector = "/blog/" + txt_name

        img_dir = out / "images" / post_slug
        seen = {}
        for img in images:
            if not img["src"].exists():
                continue
            if img["src"] not in seen:
                target = slugify(img["src"].name)
                img_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(img["src"], img_dir / target)
                seen[img["src"]] = "/blog/images/{}/{}".format(post_slug, target)

        write_file(out / txt_name, build_text_doc(title, lines, served_images(images, seen), header))

        date = meta.get("date") if isinstance(meta.get("date"), str) else ""
        date_key = date if re.match(r"\d{4}-\d{2}-\d{2}", date) else "0000-00-00"
        posts.append((date_key, title, post_slug, selector, images, seen))

    posts.sort(key=lambda p: p[0], reverse=True)

    entries = []
    for date_key, title, post_slug, selector, images, seen in posts:
        display = title
        if re.match(r"\d{4}-\d{2}-\d{2}", date_key):
            display = "{} - {}".format(date_key, title)
        entries.append(link("0", display, selector))
        for img in served_images(images, seen):
            entries.append(link("I", "  [image] {}".format(img["alt"]), img["path"]))
        entries.append("")

    while entries and entries[-1] == "":
        entries.pop()

    write_gophermap(
        out / "gophermap",
        "Blog",
        ["", "Mes articles : Linux, réseaux, sécu et homelab.", ""]
        + entries
        + ["", link("1", "Retour au menu principal", "/")],
    )


# ---------------------------------------------------------------------------
# Menu racine
# ---------------------------------------------------------------------------

BANNER = r"""
             ,__
    ,_     ,-'  `-,_      __
   (  `,-'          `-,.'  `)
    (  /               `.   )
     `.\                 \ /'
       `-.               ,'
    _____ `-.__________,-' _____
   <_____>-._   ___   _.-<_____>
     <(_<_>  | |___| |  <_>)_>
      `-.<_>  `-'   `-'  <_>-'   .-.
          `-._   ___   _.-'     <(_>
              | |___| |           `-'
              `-'   `-'
"""


def build_root():
    write_gophermap(
        REPO / "gophermap",
        "Bastien BONORA - gopherhole",
        [
            BANNER.rstrip("\n"),
            "",
            "Bienvenue dans mon coin old-school : le portfolio, la doc",
            "perso et le blog, servis en gopher par Gophernicus.",
            "",
            BAR,
            "",
            link("1", "Présentation - parcours, CV, certifications", "/presentation"),
            link("1", "Projets", "/projets"),
            link("1", "CTF - write-ups de challenges", "/ctf"),
            link("1", "Documentation technique (Bastodoc)", "/docs"),
            link("1", "Blog", "/blog"),
            "",
            BAR,
            "",
            web_link("Portfolio web (terminal)", "https://bastienbonora.fr"),
            web_link("Documentation web (Bastodoc)", "https://docs.bastienbonora.fr"),
            web_link("GitHub - Fracorbas02", "https://github.com/Fracorbas02"),
            web_link("LinkedIn", "https://www.linkedin.com/in/bastien-bonora"),
            link("0", "Clé publique PGP", "/presentation/pgp-publique.txt"),
        ],
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not PORTFOLIO.exists():
        raise SystemExit(f"Source introuvable : {PORTFOLIO}")
    if not DOCS.exists():
        raise SystemExit(f"Source introuvable : {DOCS}")

    build_presentation()
    build_projets()
    build_docs()
    build_blog()
    build_ctf()
    build_root()
    print("Gopherhole généré dans", REPO)


if __name__ == "__main__":
    main()
