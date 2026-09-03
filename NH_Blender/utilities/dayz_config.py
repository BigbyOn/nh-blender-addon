# ------------------------------------------------------------------------
#  DayZ config parsing (brace helpers + CfgWorlds / CfgSurfaceCharacters)
# ------------------------------------------------------------------------
#  DayZ config parser extracted from the NH Blender add-on.
#  Uses standard library only; CONFIG_SURFACES / CONFIG_CLUTTER are owned
#  here and mutated in place so that importers stay in sync.

import os
import re
import random


CONFIG_SURFACES = {}
CONFIG_CLUTTER = {}

#  Brace helpers
# ------------------------------------------------------------------------

def _extract_block(src: str, brace_index: int):
    depth = 1
    i = brace_index + 1
    n = len(src)
    while i < n and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth != 0:
        raise RuntimeError("Unbalanced braces while parsing config")
    return src[brace_index + 1 : i - 1], i


def _find_class_block(src: str, class_name: str):
    m = re.search(r"class\s+" + re.escape(class_name) + r"\b[^{]*{", src)
    if not m:
        return None
    brace_index = src.find("{", m.start())
    if brace_index == -1:
        return None
    body, _ = _extract_block(src, brace_index)
    return body


def _iter_inner_classes(block: str):
    pos = 0
    n = len(block)
    while pos < n:
        m = re.search(r"class\s+(\w+)[^{]*{", block[pos:])
        if not m:
            break
        name = m.group(1)
        brace_index = pos + m.end() - 1
        body, new_pos = _extract_block(block, brace_index)
        yield name, body
        pos = new_pos


# ------------------------------------------------------------------------
#  Parsing DayZ .cpp
# ------------------------------------------------------------------------

def parse_dayz_config(path: str):

    if not os.path.isfile(path):
        raise RuntimeError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    text = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    surfaces = {}
    clutter = {}

    cfgworlds_block = _find_class_block(text, "CfgWorlds")
    if cfgworlds_block:
        caworld_block = _find_class_block(cfgworlds_block, "CAWorld")
        if caworld_block:
            clutter_block = _find_class_block(caworld_block, "Clutter")
            if clutter_block:
                for c_name, c_body in _iter_inner_classes(clutter_block):
                    m_model = re.search(r'model\s*=\s*"([^"]+)"', c_body)
                    if not m_model:
                        continue
                    model_path = m_model.group(1)
                    m_smin = re.search(r"scaleMin\s*=\s*([0-9.eE+-]+)", c_body)
                    m_smax = re.search(r"scaleMax\s*=\s*([0-9.eE+-]+)", c_body)
                    smin = float(m_smin.group(1)) if m_smin else 1.0
                    smax = float(m_smax.group(1)) if m_smax else 1.0
                    clutter[c_name] = {"model": model_path, "scaleMin": smin, "scaleMax": smax}

    cfgsurf_block = _find_class_block(text, "CfgSurfaceCharacters")
    if cfgsurf_block:
        for s_name, s_body in _iter_inner_classes(cfgsurf_block):
            m_prob = re.search(r"probability\s*\[\]\s*=\s*{([^}]*)}", s_body)
            m_names = re.search(r"names\s*\[\]\s*=\s*{([^}]*)}", s_body, re.S)
            if not (m_prob and m_names):
                continue

            probs_str = m_prob.group(1)
            names_str = m_names.group(1)

            probs = [p.strip() for p in probs_str.split(",") if p.strip()]
            names = [n.strip().strip('"') for n in names_str.split(",") if n.strip()]

            if len(probs) != len(names):
                continue

            probs_f = [float(p) for p in probs]
            surfaces[s_name] = {"names": names, "probs": probs_f}

    CONFIG_SURFACES.clear()
    CONFIG_SURFACES.update(surfaces)
    CONFIG_CLUTTER.clear()
    CONFIG_CLUTTER.update(clutter)


def build_clutter_distribution(surface_name: str):
    if surface_name not in CONFIG_SURFACES:
        raise RuntimeError(f"Surface '{surface_name}' not found in CfgSurfaceCharacters")

    s_def = CONFIG_SURFACES[surface_name]
    names = s_def["names"]
    probs = s_def["probs"]

    used_names, used_probs, used_defs = [], [], {}
    clutter_map_lc = {k.lower(): k for k in CONFIG_CLUTTER.keys()}

    for n, p in zip(names, probs):
        if p <= 0.0:
            continue
        key = clutter_map_lc.get(n.lower())
        if key is None:
            raise RuntimeError(
                f"Clutter class '{n}' is referenced by surface '{surface_name}' "
                f"but not found in CfgWorlds->CAWorld->Clutter"
            )
        c_def = CONFIG_CLUTTER[key]
        model_path = (c_def.get("model") or "").strip()
        if not model_path:
            raise RuntimeError(f"Clutter class '{key}' has no 'model' defined")
        used_names.append(n)
        used_probs.append(p)
        used_defs[n] = c_def

    if not used_names:
        raise RuntimeError(f"Surface '{surface_name}' has no clutter with non-zero probability")

    total = sum(used_probs)
    if total <= 0.0:
        raise RuntimeError(f"Surface '{surface_name}' probabilities sum to zero")

    norm_probs = [p / total for p in used_probs]
    return used_names, norm_probs, used_defs


def pick_weighted_random(names, probs, rng=None):
    rng = rng or random
    r = rng.random()
    acc = 0.0
    for n, p in zip(names, probs):
        acc += p
        if r <= acc:
            return n
    return names[-1]