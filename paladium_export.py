from __future__ import annotations
import csv
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
BUILD = ROOT / '.export'
OUT = BUILD / 'Paladium_V12_Textures_Complete'
PACK = OUT / 'resourcepack'
ALT = OUT / 'alternates'
MAN = OUT / 'manifests'
ARC = BUILD / 'archives'
ZIP = ROOT / 'Paladium_V12_Textures_Complete_2026-08-19.zip'
for p in (PACK, ALT, MAN, ARC):
    p.mkdir(parents=True, exist_ok=True)

DIST_URL = 'https://cdn.paladium-pvp.fr/games/paladiumv2/paladium.json'
UA = 'Paladium-V12-Resource-Exporter/1.0'


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def load_json(url: str):
    return json.loads(get(url).decode('utf-8-sig'))


def merge_distribution(url: str, seen=None):
    seen = set() if seen is None else seen
    if url in seen:
        raise RuntimeError('distribution loop')
    seen.add(url)
    d = load_json(url)
    merged = {'models': {}, 'files': []}
    parent = d.get('parent')
    if parent:
        p = merge_distribution(urllib.parse.urljoin(url, str(parent)), seen)
        merged['models'].update(p.get('models') or {})
        merged['files'].extend(p.get('files') or [])
    models = d.get('models') or {}
    if isinstance(models, dict):
        merged['models'].update(models)
    files = d.get('files') or []
    if isinstance(files, list):
        merged['files'].extend(files)
    return merged


def model_obj(models, ref):
    if isinstance(ref, dict):
        return ref
    if ref is None:
        return {}
    v = models.get(str(ref), {})
    return v if isinstance(v, dict) else {}


def is_mod_file(file, model):
    url = str(file.get('url') or '')
    path = str(file.get('path') or file.get('name') or '')
    dest = str(model.get('path') or model.get('destination') or model.get('name') or '')
    combo = ' '.join((url, path, dest)).lower().replace('\\', '/')
    suffix = urllib.parse.urlparse(url).path.lower()
    return suffix.endswith(('.pala', '.jar')) and ('/mods/' in combo or 'mods/' in combo or suffix.endswith('.pala'))


def filename_from(file, i):
    u = str(file.get('url') or '')
    n = Path(urllib.parse.unquote(urllib.parse.urlparse(u).path)).name
    return n or f'archive-{i}.pala'


def safe(s):
    return re.sub(r'[^A-Za-z0-9._-]+', '-', s).strip('.-') or 'unknown'


def kind_rel(rel):
    x = rel.lower()
    if x.startswith('textures/'):
        return 'texture'
    if x.startswith('models/'):
        return 'model'
    if x.startswith('blockstates/'):
        return 'blockstate'
    if x.startswith('animations/') or 'animation' in x:
        return 'animation'
    if x.startswith('geo/') or x.endswith('.geo.json'):
        return 'geometry'
    if x.startswith('shaders/'):
        return 'shader'
    if x.startswith(('font/', 'fonts/')):
        return 'font'
    if x.startswith('lang/'):
        return 'lang'
    return 'asset'


def rid(path):
    ps = path.split('/', 2)
    if len(ps) < 3:
        return '', '', ''
    ns, rel = ps[1], ps[2]
    kind = kind_rel(rel)
    rr = rel
    for pre in ('textures/', 'models/', 'blockstates/'):
        if rr.startswith(pre):
            rr = rr[len(pre):]
            break
    for suf in ('.png.mcmeta', '.geo.json', '.animation.json', '.json', '.png', '.obj', '.mtl', '.jem', '.jpm', '.mcmeta'):
        if rr.lower().endswith(suf):
            rr = rr[:-len(suf)]
            break
    return ns, kind, f'{ns}:{rr}'


def tags(path, text=''):
    low = (path + '\n' + text).lower().replace('-', '_')
    out = []
    if 'ancient' in low or 'antique' in low:
        out.append('ARMURE_ANTIQUE_ANCIENT')
    if 'endium' in low:
        out.append('ENDIUM')
    if 'cave' in low:
        out.append('CAVE_BLOCK')
    if 'cube' in low:
        out.append('CUBE_BLOCK')
    if 'lucky' in low:
        out.append('LUCKY_BLOCK')
    return out


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)


def write_csv(name, data, cols):
    with (MAN / name).open('w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(data)


visual_suffixes = ('.png', '.png.mcmeta', '.json', '.mcmeta', '.obj', '.mtl', '.jem', '.jpm', '.ttf', '.otf', '.fsh', '.vsh', '.glsl')
dist = merge_distribution(DIST_URL)
models = dist['models']
selected = []
seen_urls = set()
for f in dist['files']:
    if not isinstance(f, dict):
        continue
    m = model_obj(models, f.get('model'))
    if not is_mod_file(f, m):
        continue
    u = str(f.get('url') or '')
    if not u or u in seen_urls:
        continue
    seen_urls.add(u)
    selected.append((f, m))
if not selected:
    raise RuntimeError('No .pala/.jar mod files found in current distribution')
print('Selected archives:', len(selected), flush=True)

rows, special, conflicts, links, source = [], [], [], [], []
owner, shas, bytes_by_path = {}, {}, {}
for i, (f, m) in enumerate(selected, 1):
    url = str(f.get('url') or '')
    name = filename_from(f, i)
    mod = Path(name).stem
    print(f'[{i}/{len(selected)}] {name}', flush=True)
    data = get(url)
    sha256 = hashlib.sha256(data).hexdigest()
    sha1 = hashlib.sha1(data).hexdigest()
    expected = str(f.get('sha256') or '')
    expected1 = str(f.get('sha1') or '')
    if expected and sha256.lower() != expected.lower():
        raise RuntimeError(f'SHA256 mismatch: {name}')
    if expected1 and sha1.lower() != expected1.lower():
        raise RuntimeError(f'SHA1 mismatch: {name}')
    (ARC / safe(name)).write_bytes(data)
    source.append({'index': i, 'archive': name, 'url': url, 'size': len(data), 'sha1': sha1, 'sha256': sha256})
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise RuntimeError(f'Not a ZIP-compatible pala: {name}') from e
    with z:
        for info in z.infolist():
            if info.is_dir():
                continue
            path = info.filename.replace('\\', '/').lstrip('/')
            if not path.startswith('assets/') or '../' in path:
                continue
            if not path.lower().endswith(visual_suffixes):
                continue
            b = z.read(info)
            h = hashlib.sha256(b).hexdigest()
            ns, k, r = rid(path)
            text = b.decode('utf-8', errors='ignore') if path.lower().endswith('.json') else ''
            tg = tags(path, text)
            row = {'archive_index': i, 'mod': mod, 'archive': name, 'asset_path': path, 'namespace': ns, 'kind': k, 'resource_id': r, 'size': len(b), 'sha256': h, 'special_tags': ';'.join(tg)}
            rows.append(row)
            if tg:
                special.append(row.copy())
            if text:
                try:
                    obj = json.loads(text)
                except Exception:
                    obj = None
                if obj is not None:
                    vals = set()
                    for v in strings(obj):
                        s = v.strip().replace('\\', '/')
                        if len(s) <= 300 and (re.fullmatch(r'[a-z0-9_.-]+:[a-z0-9_./-]+', s, re.I) or 'textures/' in s.lower() or s.lower().endswith('.png')):
                            vals.add(s)
                    for v in sorted(vals):
                        links.append({'source_resource_id': r, 'source_asset_path': path, 'mod': mod, 'referenced_value': v})
            target = PACK / path
            if path in shas and shas[path] != h:
                pm = owner[path]
                alt = ALT / safe(pm) / path
                alt.parent.mkdir(parents=True, exist_ok=True)
                if not alt.exists():
                    alt.write_bytes(bytes_by_path[path])
                conflicts.append({'asset_path': path, 'previous_mod': pm, 'previous_sha256': shas[path], 'winning_mod': mod, 'winning_sha256': h})
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b)
            owner[path] = mod
            shas[path] = h
            bytes_by_path[path] = b

(PACK / 'pack.mcmeta').write_text(json.dumps({'pack': {'pack_format': 1, 'description': 'Paladium V12 - assets visuels officiels actuels'}}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
fields = ['archive_index', 'mod', 'archive', 'asset_path', 'namespace', 'kind', 'resource_id', 'size', 'sha256', 'special_tags']
write_csv('assets.csv', rows, fields)
(MAN / 'assets.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
write_csv('special_assets.csv', special, fields)
write_csv('source_archives.csv', source, ['index', 'archive', 'url', 'size', 'sha1', 'sha256'])
write_csv('conflicts.csv', conflicts, ['asset_path', 'previous_mod', 'previous_sha256', 'winning_mod', 'winning_sha256'])
write_csv('model_resource_links.csv', links, ['source_resource_id', 'source_asset_path', 'mod', 'referenced_value'])
prov = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'distribution_url': DIST_URL, 'archive_count': len(selected), 'asset_entries': len(rows), 'unique_resourcepack_paths': len(shas), 'different_content_collisions': len(conflicts), 'special_asset_entries': len(special), 'model_resource_links': len(links), 'legacy_or_fabric_sources_used': False}
(MAN / 'provenance.json').write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding='utf-8')
counts = Counter(r['kind'] for r in rows)
sp = ['# Assets spéciaux détectés', '']
for tag in ('ARMURE_ANTIQUE_ANCIENT', 'ENDIUM', 'CAVE_BLOCK', 'CUBE_BLOCK', 'LUCKY_BLOCK'):
    mm = [r for r in special if tag in r['special_tags'].split(';')]
    sp += [f'## {tag} — {len(mm)} entrée(s)'] + [f"- `{r['resource_id']}` → `{r['asset_path']}` ({r['mod']})" for r in mm] + ['']
(OUT / 'SPECIAL_ASSETS.md').write_text('\n'.join(sp), encoding='utf-8')
(OUT / 'ALL_RESOURCE_IDS.txt').write_text('\n'.join(f"{r['resource_id']}\t{r['asset_path']}\t{r['mod']}" for r in sorted(rows, key=lambda x: (x['resource_id'], x['asset_path'], x['mod']))) + '\n', encoding='utf-8')
readme = f'''# Paladium V12 — textures et modèles complets\n\nExport construit uniquement depuis le manifeste officiel courant `{DIST_URL}`. Aucun asset des ports Fabric/NeoForge de RABRIC ni d'anciens dossiers versionnés n'est utilisé.\n\n## Contenu\n- `resourcepack/` : chemins `assets/<namespace>/...` exacts, directement réutilisables dans un pack.\n- `manifests/assets.csv` / `.json` : ID textuel `namespace:path`, chemin, mod source, SHA-256.\n- `manifests/model_resource_links.csv` : références modèle/texture trouvées dans les JSON.\n- `SPECIAL_ASSETS.md` : Ancient/Antique, Endium, Cave Block, Cube Block, Lucky Block.\n- `alternates/` : variantes actuelles en cas de collision de chemins, afin de ne rien perdre.\n\n## Résumé\n- Archives officielles courantes : **{len(selected)}**\n- Entrées visuelles : **{len(rows)}**\n- Chemins uniques : **{len(shas)}**\n- Collisions distinctes conservées : **{len(conflicts)}**\n- Liens modèle → ressource : **{len(links)}**\n- Répartition : `{dict(counts)}`\n\nPour un texture pack, le chemin exact sous `assets/` est la référence autoritaire. Les IDs numériques historiques ne sont pas nécessaires à la substitution de ressources ; `resource_id` fournit l'ID textuel pratique.\n'''
(OUT / 'README_FR.md').write_text(readme, encoding='utf-8')
if ZIP.exists():
    ZIP.unlink()
with zipfile.ZipFile(ZIP, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as oz:
    for p in sorted(OUT.rglob('*')):
        if p.is_file():
            oz.write(p, arcname=f'Paladium_V12_Textures_Complete/{p.relative_to(OUT).as_posix()}')
print(json.dumps(prov, indent=2), flush=True)
print('ZIP_BYTES', ZIP.stat().st_size, flush=True)
