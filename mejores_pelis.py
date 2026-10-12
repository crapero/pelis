#!/usr/bin/env python3
"""
mejores_pelis.py - Las mejores películas del día en la TV (datos de mi.tv)

Descarga la programación de los canales que elijas, se queda con las películas,
completa el puntaje de las que no lo traen (opcional, con TMDB) y te muestra:
  1) el ranking de las mejores del día
  2) una "rutina" de películas que no se pisan entre sí

Uso:
    python3 mejores_pelis.py              # hoy
    python3 mejores_pelis.py --manana     # mañana
    python3 mejores_pelis.py --dias 3     # hoy, mañana y pasado
    python3 mejores_pelis.py --manana --dias 2   # mañana y pasado
    python3 mejores_pelis.py --dias 7 --html     # página para el celular (pelis.html)

Requisitos (una sola vez):
    Arch/CachyOS:   sudo pacman -S python-requests python-beautifulsoup4
    Debian/Ubuntu:  sudo apt install python3-requests python3-bs4

Puntajes faltantes (opcional): sacá una API key gratis en
https://www.themoviedb.org/settings/api y antes de correr el script hacé:
    export TMDB_API_KEY="tu_clave"          (bash/zsh)
    set -Ux TMDB_API_KEY "tu_clave"         (fish, una sola vez)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Faltan librerías. Instalá con:  sudo apt install python3-requests python3-bs4")

# ---------------------------------------------------------------- CONFIGURACIÓN
# Canales a revisar: "identificador en mi.tv": "nombre a mostrar".
# Para agregar uno, abrí su página en mi.tv: la dirección es mi.tv/ar/canales/<identificador>
CANALES = {
    "tnt": "TNT",
    "fox": "Star Channel",
    "space": "Space",
    "cinecanal": "Cinecanal",
    "fx": "FX",
    "amc": "AMC",
    "warner-channel": "Warner",
    "sony": "Sony",
    # "hbo": "HBO",          # descomentá si algún día lo tenés
    "tcm": "TCM",
    "paramount-channel": "Paramount",
    "studio-universal": "Studio Universal",
}

PAIS = "ar"
ZONA = ZoneInfo("America/Argentina/Cordoba")
PUNTAJE_MINIMO_RANKING = 6.0  # en "Mejores películas" solo se muestran las de este puntaje o más
PUNTAJE_MINIMO_RUTINA = 7.0   # en la rutina solo entran pelis con este puntaje o más
PUNTAJE_MINIMO_ALTERNATIVA = 7.0  # pelis que se pisan con la rutina y se muestran como alternativa
TOP = None                    # máximo de pelis en el ranking por día (None = todas)
DURACION_ULTIMA = 2.0         # horas asumidas para la última peli de cada canal
AJUSTE_HORAS = 0              # mi.tv da horas UTC y ya se convierten; esto es un ajuste extra
CORTE_DIA = 5                 # hasta esta hora de la madrugada se cuenta como el día anterior
CACHE_TMDB = os.path.expanduser("~/.cache/mejores_pelis_tmdb_v3.json")
# ------------------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/137.0 Safari/537.36",
    "Accept-Language": "es-AR,es;q=0.9",
}


def descargar_canal(canal, fecha):
    """Devuelve el HTML con la grilla de un canal para una fecha (AAAA-MM-DD)."""
    url = f"https://mi.tv/{PAIS}/async/channel/{canal}/{fecha}/0"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def interpretar_subtitulo(texto):
    """'Drama / 1999 / 8.5' -> ('Drama', 1999, 8.5). Lo que falte queda en None."""
    partes = [p.strip() for p in texto.split("/")]
    genero = partes[0] if partes else ""
    anio = puntaje = None
    for p in partes[1:]:
        if re.fullmatch(r"(19|20)\d{2}", p):
            anio = int(p)
        elif re.fullmatch(r"\d{1,2}([.,]\d)?", p):
            puntaje = float(p.replace(",", "."))
    return genero, anio, puntaje


def leer_grilla(html, canal, fecha, todo=False):
    """Saca los programas de la grilla (mi.tv da las horas en UTC)."""
    sopa = BeautifulSoup(html, "html.parser")
    base = datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=UTC)
    programas, anterior = [], None
    for li in sopa.select("#listings ul li"):
        hora = li.select_one("span.time")
        titulo = li.select_one("h2")
        if not hora or not titulo:
            continue  # publicidad u otra cosa
        h, m = map(int, re.findall(r"\d+", hora.text)[:2])
        inicio = base.replace(hour=h, minute=m) + timedelta(hours=AJUSTE_HORAS)
        if anterior and inicio < anterior["inicio"]:   # pasó la medianoche
            inicio += timedelta(days=1)
            base += timedelta(days=1)
        sub = li.select_one("span.sub-title")
        subtitulo = " ".join(sub.text.split()) if sub else ""
        genero, anio, puntaje = interpretar_subtitulo(subtitulo)
        enlace = li.select_one("a")
        href = enlace.get("href", "") if enlace else ""
        m_anio = re.search(r"-((?:19|20)\d{2})(?:-\d+)?$", href)
        if anio is None and m_anio:
            anio = int(m_anio.group(1))
        # si el subtítulo no es "Género / Año / Puntaje", suele ser el título original
        original = None
        if "/" not in subtitulo and not re.search(r"episodio|temporada", subtitulo, re.I):
            original, genero = subtitulo or None, ""
        img = None
        caja = li.select_one("div.image")
        m_img = re.search(r"url\(\\?['\"]?(.*?)\\?['\"]?\)", caja.get("style", "")) if caja else None
        if m_img and "fallback" not in m_img.group(1):
            img = m_img.group(1)
        sinopsis = li.select_one("p.synopsis")
        p = {
            "img": img, "sinopsis": " ".join(sinopsis.text.split()) if sinopsis else "",
            "canal": CANALES[canal], "titulo": titulo.text.strip(), "genero": genero,
            "anio": anio, "puntaje": puntaje, "fuente": "mi.tv" if puntaje else None,
            "inicio": inicio, "fin": None, "href": href, "sub": subtitulo, "original": original,
        }
        if anterior:
            anterior["fin"] = inicio
        programas.append(p)
        anterior = p
    if anterior:
        anterior["fin"] = anterior["inicio"] + timedelta(hours=DURACION_ULTIMA)
    for p in programas:   # pasar de UTC a la hora de Argentina
        p["inicio"] = p["inicio"].astimezone(ZONA)
        p["fin"] = p["fin"].astimezone(ZONA)
    return programas if todo else [p for p in programas if es_pelicula(p)]


def es_pelicula(p):
    """Las pelis tienen un enlace que termina en el año: /ar/programas/nueve-reinas-2000
    y las series en temporada/episodio: /ar/programas/algo-s05e10"""
    if re.search(r"-s\d+e\d+$", p["href"]):
        return False
    if re.search(r"-(19|20)\d{2}(-\d+)?$", p["href"]) or "/pelicula" in p["href"]:
        return True
    return p["anio"] is not None


# ------------------------------------------------------------ puntajes de TMDB
def cargar_cache():
    try:
        with open(CACHE_TMDB) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def guardar_cache(cache):
    os.makedirs(os.path.dirname(CACHE_TMDB), exist_ok=True)
    with open(CACHE_TMDB, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def elegir_resultado(resultados, anio):
    """De lo que devuelve TMDB, se queda con la peli del mismo año (±1)
    que tenga más votos. Así no confunde pelis con nombres parecidos."""
    buenos = []
    for r in resultados:
        fecha = r.get("release_date") or ""
        if anio and not (fecha[:4].isdigit() and abs(int(fecha[:4]) - anio) <= 1):
            continue
        if r.get("vote_count", 0) >= 20:
            buenos.append(r)
    return max(buenos, key=lambda r: r["vote_count"]) if buenos else None


def buscar_tmdb(p, clave):
    """Busca la peli en TMDB. Devuelve {puntaje, img, sinopsis} o None."""
    for consulta in filter(None, [p["original"], p["titulo"]]):
        params = {"api_key": clave, "query": consulta, "language": "es-AR"}
        if p["anio"]:
            params["year"] = p["anio"]
        try:
            r = requests.get("https://api.themoviedb.org/3/search/movie",
                             params=params, timeout=15)
        except requests.RequestException:
            return "error"
        if r.status_code == 401:
            sys.exit("TMDB rechazó la clave: revisá TMDB_API_KEY (usá la 'API Key' corta).")
        peli = elegir_resultado(r.json().get("results", []), p["anio"])
        if peli:
            ruta = peli.get("backdrop_path") or peli.get("poster_path")
            return {
                "puntaje": round(peli["vote_average"], 1),
                "img": f"https://image.tmdb.org/t/p/w780{ruta}" if ruta else None,
                "sinopsis": peli.get("overview") or "",
            }
    return None


def completar_puntajes(pelis, todas=False):
    """Completa puntajes (y para la página, imágenes y sinopsis) con TMDB.
    todas=True consulta todas las pelis, no solo las que no tienen puntaje."""
    clave = os.environ.get("TMDB_API_KEY")
    faltan = [p for p in pelis if todas or p["puntaje"] is None]
    if not faltan:
        return
    if not clave:
        n = sum(p["puntaje"] is None for p in pelis)
        print(f"({n} pelis sin puntaje. Configurá TMDB_API_KEY para completarlas.)\n")
        return
    cache = cargar_cache()
    for k, p in enumerate(faltan, 1):
        llave = f"{p['titulo']}|{p['anio']}"
        if llave not in cache:
            print(f"\rConsultando TMDB... {k}/{len(faltan)}", end="", file=sys.stderr)
            datos = buscar_tmdb(p, clave)
            if datos == "error":
                continue
            cache[llave] = datos
        datos = cache[llave]
        if not datos:
            continue
        if p["puntaje"] is None and datos["puntaje"]:
            p["puntaje"], p["fuente"] = datos["puntaje"], "TMDB"
        if datos.get("img"):
            p["img"] = datos["img"]          # las de TMDB son mejores que las de mi.tv
        if not p["sinopsis"] and datos.get("sinopsis"):
            p["sinopsis"] = datos["sinopsis"]
    print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
    guardar_cache(cache)


# ------------------------------------------------------- rutina sin choques
def armar_rutina(pelis):
    """Elige pelis que no se superpongan maximizando 'cuánto valen' en total.
    Una peli de 8.5 vale mucho más que dos de 7, por eso se usa (puntaje - 6)^2."""
    candidatas = sorted((p for p in pelis if (p["puntaje"] or 0) >= PUNTAJE_MINIMO_RUTINA),
                        key=lambda p: p["fin"])
    n = len(candidatas)
    mejor = [0.0] * (n + 1)
    eleccion = [None] * (n + 1)
    for i in range(1, n + 1):
        p = candidatas[i - 1]
        j = i - 1   # última peli que termina antes de que empiece esta
        while j > 0 and candidatas[j - 1]["fin"] > p["inicio"]:
            j -= 1
        con = mejor[j] + (p["puntaje"] - 6) ** 2
        if con > mejor[i - 1]:
            mejor[i], eleccion[i] = con, j
        else:
            mejor[i] = mejor[i - 1]
    rutina, i = [], n
    while i > 0:
        if eleccion[i] is not None and mejor[i] != mejor[i - 1]:
            rutina.append(candidatas[i - 1])
            i = eleccion[i]
        else:
            i -= 1
    return list(reversed(rutina))


def alternativas(rutina, pelis):
    """Para cada peli de la rutina, las otras buenas que se pisan con ella.
    Cada alternativa va con la peli de la rutina con la que más tiempo comparte."""
    alts = {id(r): [] for r in rutina}
    elegidas = {id(r) for r in rutina}
    for p in pelis:
        if id(p) in elegidas or (p["puntaje"] or 0) < PUNTAJE_MINIMO_ALTERNATIVA:
            continue
        mejor, solape = None, timedelta(0)
        for r in rutina:
            comun = min(p["fin"], r["fin"]) - max(p["inicio"], r["inicio"])
            if comun > solape:
                mejor, solape = r, comun
        if mejor:
            alts[id(mejor)].append(p)
    for lista in alts.values():
        lista.sort(key=lambda p: p["inicio"])
    return alts


# ------------------------------------------------------------------- salida
def linea(p):
    pts = f"{p['puntaje']:.1f}" if p["puntaje"] else " ? "
    marca = "*" if p["fuente"] == "TMDB" else " "
    anio = f" ({p['anio']})" if p["anio"] else ""
    ahora = datetime.now(ZONA)
    en_curso = "  [YA EMPEZÓ]" if p["inicio"] <= ahora < p["fin"] else ""
    return f"{p['inicio']:%d/%m %H:%M}  {pts}{marca}  {p['canal']:<13} {p['titulo']}{anio}{en_curso}"


def main():
    ap = argparse.ArgumentParser(description="Las mejores pelis del día según mi.tv")
    ap.add_argument("--manana", action="store_true", help="empezar desde mañana")
    ap.add_argument("--dias", type=int, default=1, metavar="N",
                    help="cuántos días mostrar (ej: --dias 3 = hoy, mañana y pasado)")
    ap.add_argument("--todo-el-dia", action="store_true",
                    help="incluir las que ya terminaron hoy")
    ap.add_argument("--html", nargs="?", const="pelis.html", metavar="ARCHIVO",
                    help="genera una página para el celular (por defecto pelis.html)")
    ap.add_argument("--debug", metavar="CANAL",
                    help="muestra TODO lo que devuelve mi.tv para un canal (ej: --debug tnt)")
    args = ap.parse_args()

    ahora = datetime.now(ZONA)
    inicio_dia = ahora.date()
    if args.manana:
        inicio_dia += timedelta(days=1)
    dias = [inicio_dia + timedelta(days=d) for d in range(max(1, args.dias))]

    if args.debug:
        fecha = dias[0].strftime("%Y-%m-%d")
        html = descargar_canal(args.debug, fecha)
        print(f"URL: https://mi.tv/{PAIS}/async/channel/{args.debug}/{fecha}/0")
        print(f"Tamaño del HTML: {len(html)} caracteres\n")
        CANALES.setdefault(args.debug, args.debug)
        for p in leer_grilla(html, args.debug, fecha, todo=True):
            tipo = "PELI " if es_pelicula(p) else "otro "
            print(f"{tipo}{p['inicio']:%d/%m %H:%M}  {p['titulo']}  |  {p['sub']}  |  {p['href']}")
        return

    # Bajar cada día de cada canal. Las grillas se pisan un poco entre días
    # (la de hoy trae la madrugada siguiente), así que se eliminan repetidas.
    vistas, pelis = set(), []
    for dia in dias:
        fecha = dia.strftime("%Y-%m-%d")
        for canal in CANALES:
            try:
                for p in leer_grilla(descargar_canal(canal, fecha), canal, fecha):
                    clave = (p["canal"], p["inicio"])
                    if clave not in vistas:
                        vistas.add(clave)
                        pelis.append(p)
            except requests.RequestException as e:
                print(f"No pude bajar {CANALES[canal]} del {fecha}: {e}", file=sys.stderr)
            time.sleep(0.5)   # no saturar el sitio

    if not pelis:
        sys.exit("No encontré películas. Puede que mi.tv haya cambiado su página.")

    if args.html:
        completar_puntajes(pelis, todas=True)   # también trae imágenes y sinopsis
        generar_html(pelis, dias, ahora, args.html)
        return

    if not args.todo_el_dia:
        pelis = [p for p in pelis if p["fin"] > ahora]
    completar_puntajes(pelis)

    for dia in dias:
        # lo de la madrugada de hoy (antes del corte) se muestra junto con hoy
        del_dia = [p for p in pelis if max(dia_tv(p), dias[0]) == dia]
        titulo = f"{NOMBRES_DIA[dia.weekday()]} {dia:%d/%m}"
        if not del_dia:
            print(f"\n##### {titulo}: mi.tv todavía no tiene programación para este día #####")
            continue
        print(f"\n##### {titulo} #####")
        print(f"--- Mejores películas ({PUNTAJE_MINIMO_RANKING}+) ---")
        ranking = [p for p in del_dia if (p["puntaje"] or 0) >= PUNTAJE_MINIMO_RANKING]
        # se eligen las mejores (hasta TOP) y se muestran en orden de horario
        ranking = sorted(ranking, key=lambda p: p["puntaje"], reverse=True)[:TOP]
        for p in sorted(ranking, key=lambda p: p["inicio"]):
            print(linea(p))
        print(f"--- Rutina sin superposiciones (pelis de {PUNTAJE_MINIMO_RUTINA}+) ---")
        rutina = armar_rutina(del_dia)
        alts = alternativas(rutina, del_dia)
        for p in rutina:
            print(f"{linea(p)}  -> {p['fin']:%H:%M}")
            for a in alts[id(p)]:
                print(f"      o  {linea(a)}  -> {a['fin']:%H:%M}")

    if any(p["fuente"] == "TMDB" for p in pelis):
        print("\n* puntaje sacado de TMDB (mi.tv no lo tenía)")


def generar_html(pelis, dias, ahora, archivo):
    """Arma una página para el celular con TODAS las pelis; los filtros se manejan ahí."""
    en_rutina, alts_de = set(), {}
    indice = {id(p): i for i, p in enumerate(pelis)}
    datos_dias = []
    for dia in dias:
        del_dia = [p for p in pelis if max(dia_tv(p), dias[0]) == dia]
        if not del_dia:
            continue
        nombre = "Hoy" if dia == ahora.date() else NOMBRES_DIA[dia.weekday()][:3]
        datos_dias.append({"fecha": dia.isoformat(), "corto": nombre, "num": f"{dia:%d/%m}"})
        vigentes = [p for p in del_dia if p["fin"] > ahora]
        rutina = armar_rutina(vigentes)
        for r, lista in alternativas(rutina, vigentes).items():
            alts_de[r] = [indice[id(a)] for a in lista]
        for p in rutina:
            en_rutina.add(id(p))
    datos = {
        "generado": f"{ahora:%d/%m %H:%M}",
        "minRutina": PUNTAJE_MINIMO_RUTINA,
        "dias": datos_dias,
        "pelis": [{
            "titulo": p["titulo"], "anio": p["anio"], "genero": p["genero"], "canal": p["canal"],
            "ini": p["inicio"].isoformat(), "fin": p["fin"].isoformat(),
            "puntaje": p["puntaje"], "fuente": p["fuente"], "img": p["img"],
            "sinopsis": p["sinopsis"], "dia": max(dia_tv(p), dias[0]).isoformat(),
            "rutina": id(p) in en_rutina, "alts": alts_de.get(id(p), []),
        } for p in pelis],
    }
    js = json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")
    if os.path.dirname(archivo):
        os.makedirs(os.path.dirname(archivo), exist_ok=True)
    with open(archivo, "w", encoding="utf-8") as f:
        f.write(PLANTILLA.replace("/*__DATOS__*/null", js))
    print(f"Listo: {os.path.abspath(archivo)}  ({len(pelis)} películas, {len(datos_dias)} días)")
    print("Para verla en el celular (misma red WiFi):")
    print("  python3 -m http.server 8000")
    print("  y en el celular abrí  http://<IP de tu PC>:8000/" + os.path.basename(archivo))


NOMBRES_DIA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def dia_tv(p):
    """Día 'de tele' de una peli: lo que empieza antes de las 5 AM cuenta para el día anterior."""
    return (p["inicio"] - timedelta(hours=CORTE_DIA)).date()


PLANTILLA = r'''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0e0d12">
<title>Pelis en la tele</title>
<style>
:root{
  --bg:#0e0d12; --panel:#17161d; --panel2:#201f28; --line:#2c2a36;
  --txt:#f1eff6; --sub:#a9a5b8; --dim:#6f6b80;
  --acento:#ffb547; --vivo:#ff4d5e;
  --oro:#ffc94d; --verde:#4fd18b; --amar:#d9c75a; --gris:#7d7990;
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--txt);font:15px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  padding-bottom:calc(24px + env(safe-area-inset-bottom))}
button,select,input{font:inherit;color:inherit}
header{position:sticky;top:0;z-index:5;background:rgba(14,13,18,.92);backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding-top:env(safe-area-inset-top)}
.top{display:flex;align-items:baseline;justify-content:space-between;padding:14px 16px 8px}
.top h1{font-size:21px;font-weight:800;letter-spacing:-.02em}
.top h1 span{color:var(--acento)}
.top small{color:var(--dim);font-size:12px}
.dias{display:flex;gap:8px;overflow-x:auto;padding:4px 16px 10px;scrollbar-width:none}
.dias::-webkit-scrollbar{display:none}
.dia{flex:0 0 auto;border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:6px 12px;
  text-align:center;cursor:pointer;min-width:62px}
.dia b{display:block;font-size:13px}
.dia span{font-size:11px;color:var(--sub)}
.dia.on{background:var(--acento);border-color:var(--acento);color:#1a1205}
.dia.on span{color:#3d2b06}
.barra{display:flex;gap:8px;padding:0 16px 12px;align-items:center}
.seg{display:flex;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:3px}
.seg button{border:0;background:none;padding:6px 12px;border-radius:7px;font-size:13px;font-weight:600;color:var(--sub);cursor:pointer}
.seg button.on{background:var(--panel2);color:var(--txt)}
select{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:7px 8px;font-size:13px;flex:1;min-width:0}
.filtro-btn{border:1px solid var(--line);background:var(--panel);border-radius:10px;padding:7px 11px;font-size:13px;cursor:pointer;white-space:nowrap}
.filtro-btn.on{border-color:var(--acento);color:var(--acento)}
.filtros{display:none;padding:0 16px 14px;gap:12px;flex-direction:column}
.filtros.abierto{display:flex}
.fila{display:flex;align-items:center;gap:12px}
.fila label{font-size:13px;color:var(--sub);white-space:nowrap}
.fila output{font-weight:700;color:var(--acento);min-width:30px;text-align:right}
input[type=range]{flex:1;accent-color:var(--acento)}
.check{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--sub);cursor:pointer}
.check input{accent-color:var(--acento);width:18px;height:18px}
.canales{display:flex;flex-wrap:wrap;gap:6px}
.canal-chip{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:4px 10px;font-size:12px;cursor:pointer;color:var(--dim)}
.canal-chip.on{color:var(--txt);border-color:#4a4658;background:var(--panel2)}
.cuenta{padding:14px 16px 4px;font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em}
main{display:flex;flex-direction:column;gap:14px;padding:6px 16px}
.card{background:var(--panel);border-radius:16px;overflow:hidden;border:1px solid var(--line)}
.card.fin{opacity:.45}
.banner{position:relative;aspect-ratio:16/8;background:var(--panel2);overflow:hidden}
.banner img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;z-index:1}
.banner .ph{position:absolute;inset:0;padding:16px 70px 0 16px;font-size:26px;font-weight:900;
  letter-spacing:-.03em;line-height:1.05;color:rgba(255,255,255,.2);overflow:hidden}
.banner::after{z-index:1}
.banner::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.25) 0%,transparent 35%,transparent 55%,rgba(23,22,29,.95) 100%)}
.pts{position:absolute;top:10px;right:10px;z-index:2;font-weight:800;font-size:15px;padding:4px 9px;border-radius:9px;
  color:#14120a;display:flex;gap:4px;align-items:baseline}
.pts small{font-size:9px;font-weight:700;opacity:.7}
.n8{background:var(--oro)} .n7{background:var(--verde)} .n6{background:var(--amar)} .n0{background:var(--gris)}
.npreg{background:rgba(0,0,0,.55);color:var(--sub);border:1px solid rgba(255,255,255,.15)}
.hora{position:absolute;left:12px;bottom:10px;z-index:2;font-weight:800;font-size:20px;letter-spacing:-.02em;text-shadow:0 1px 8px rgba(0,0,0,.6)}
.hora span{font-size:13px;font-weight:600;color:var(--sub)}
.vivo{position:absolute;top:10px;left:10px;z-index:2;background:var(--vivo);color:#fff;font-size:11px;font-weight:800;
  padding:3px 8px;border-radius:6px;letter-spacing:.06em}
.prog{position:absolute;left:0;right:0;bottom:0;height:3px;background:rgba(255,255,255,.12);z-index:3}
.prog i{display:block;height:100%;background:var(--vivo)}
.info{padding:10px 14px 14px}
.info h3{font-size:17px;font-weight:750;line-height:1.25}
.info h3 small{font-weight:500;color:var(--dim);font-size:13px}
.meta{margin-top:4px;font-size:13px;color:var(--sub);display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center}
.canal-tag{font-weight:700;color:var(--txt);background:var(--panel2);border-radius:6px;padding:1px 7px;font-size:12px}
.sin{margin-top:8px;font-size:13px;color:var(--sub);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;cursor:pointer}
.sin.abierta{-webkit-line-clamp:unset}
.alts{border-top:1px solid var(--line);padding:8px 14px 10px;background:rgba(255,255,255,.015)}
.alts h4{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;font-weight:600;margin-bottom:6px}
.alt-item + .alt-item{border-top:1px solid rgba(255,255,255,.04)}
.alt{display:flex;align-items:center;gap:10px;padding:7px 0;font-size:13px;cursor:pointer}
.alt::after{content:"›";color:var(--dim);font-size:18px;line-height:1;transition:transform .15s}
.alt-item.abierta .alt::after{transform:rotate(90deg)}
.alt-det{display:none;padding:0 0 10px 52px;font-size:13px;color:var(--sub)}
.alt-item.abierta .alt-det{display:block}
.alt-det .m{color:var(--dim);font-size:12px;margin-bottom:4px}
.alt .h{font-weight:700;min-width:42px;font-variant-numeric:tabular-nums}
.alt .p{font-weight:800;font-size:12px;padding:2px 6px;border-radius:6px;color:#14120a}
.alt .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.alt .c{color:var(--dim);font-size:12px;white-space:nowrap}
.hueco{text-align:center;font-size:12px;color:var(--dim);display:flex;align-items:center;gap:10px}
.hueco::before,.hueco::after{content:"";flex:1;border-top:1px dashed var(--line)}
.vacio{text-align:center;color:var(--dim);padding:50px 20px}
.nota{text-align:center;color:var(--dim);font-size:11px;padding:20px 16px 0}
@media (min-width:720px){main{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));max-width:1200px;margin:0 auto}
  .hueco{grid-column:1/-1}}
</style>
</head>
<body>
<header>
  <div class="top"><h1>Pelis en la <span>tele</span></h1><small id="gen"></small></div>
  <nav class="dias" id="dias"></nav>
  <div class="barra">
    <div class="seg" id="vista">
      <button data-v="todas">Todas</button><button data-v="rutina">Rutina</button>
    </div>
    <select id="orden" aria-label="Ordenar">
      <option value="puntaje">Mejor puntaje</option>
      <option value="hora">Por horario</option>
      <option value="canal">Por canal</option>
    </select>
    <button class="filtro-btn" id="fbtn">Filtros</button>
  </div>
  <div class="filtros" id="filtros">
    <div class="fila"><label for="min">Puntaje mínimo</label><input type="range" id="min" min="0" max="9" step="0.5"><output id="minv"></output></div>
    <div class="fila">
      <label class="check"><input type="checkbox" id="sinp"> Incluir sin puntaje</label>
      <label class="check"><input type="checkbox" id="term"> Mostrar terminadas</label>
    </div>
    <div class="canales" id="canales"></div>
  </div>
</header>
<div class="cuenta" id="cuenta"></div>
<main id="lista"></main>
<p class="nota">Horarios de mi.tv · Puntajes de IMDb (vía mi.tv) y TMDB</p>

<script>
const D = /*__DATOS__*/null;

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const hhmm = d => d.toLocaleTimeString("es-AR", {hour:"2-digit", minute:"2-digit", hour12:false});
const pelis = D.pelis.map((p, i) => ({...p, i, ini:new Date(p.ini), fin:new Date(p.fin)}));
const abiertos = new Set();   // lo que el usuario desplegó (sobrevive al redibujado)
const canales = [...new Set(pelis.map(p => p.canal))].sort();

// --- estado (se recuerda en el teléfono) ---
const def = {vista:"todas", orden:"puntaje", min:6, sinp:false, term:false, apagados:[]};
let est = {...def};
try { Object.assign(est, JSON.parse(localStorage.getItem("pelis-est") || "{}")); } catch(e) {}
const guardar = () => { try { localStorage.setItem("pelis-est", JSON.stringify(est)); } catch(e) {} };
const hoyISO = (() => { const d = new Date(Date.now() - 5*3600e3); return d.toLocaleDateString("en-CA", {timeZone:"America/Argentina/Cordoba"}); })();
est.dia = D.dias.some(d => d.fecha === hoyISO) ? hoyISO : D.dias[0]?.fecha;

$("#gen").textContent = "Actualizado " + D.generado;

// --- controles ---
$("#dias").innerHTML = D.dias.map(d =>
  `<button class="dia" data-d="${d.fecha}"><b>${esc(d.corto)}</b><span>${esc(d.num)}</span></button>`).join("");
$("#dias").onclick = e => { const b = e.target.closest(".dia"); if (b) { est.dia = b.dataset.d; pintar(); scrollTo({top:0}); } };
$("#vista").onclick = e => { const b = e.target.closest("button"); if (b) { est.vista = b.dataset.v; guardar(); pintar(); } };
$("#orden").onchange = e => { est.orden = e.target.value; guardar(); pintar(); };
$("#fbtn").onclick = () => $("#filtros").classList.toggle("abierto");
$("#min").oninput = e => { est.min = +e.target.value; guardar(); pintar(); };
$("#sinp").onchange = e => { est.sinp = e.target.checked; guardar(); pintar(); };
$("#term").onchange = e => { est.term = e.target.checked; guardar(); pintar(); };
$("#canales").innerHTML = canales.map(c => `<button class="canal-chip" data-c="${esc(c)}">${esc(c)}</button>`).join("");
$("#canales").onclick = e => {
  const b = e.target.closest(".canal-chip"); if (!b) return;
  const c = b.dataset.c, i = est.apagados.indexOf(c);
  i >= 0 ? est.apagados.splice(i, 1) : est.apagados.push(c);
  guardar(); pintar();
};

function fondo(canal) {   // color propio para cada canal cuando no hay imagen
  let h = 0; for (const c of canal) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `linear-gradient(135deg, hsl(${h} 45% 28%), hsl(${(h + 40) % 360} 40% 14%))`;
}

function nivel(p) {
  if (p.puntaje == null) return "npreg";
  return p.puntaje >= 8 ? "n8" : p.puntaje >= 7 ? "n7" : p.puntaje >= 6 ? "n6" : "n0";
}

function tarjeta(p, ahora, conAlts = false) {
  const vivo = p.ini <= ahora && ahora < p.fin, termino = p.fin <= ahora;
  const avance = vivo ? Math.round((ahora - p.ini) / (p.fin - p.ini) * 100) : 0;
  const pts = p.puntaje == null ? "?" : p.puntaje.toFixed(1);
  const img = p.img
    ? `<img loading="lazy" src="${esc(p.img)}" alt="" onerror="this.remove()">` : "";
  return `<article class="card${termino ? " fin" : ""}">
    <div class="banner">
      <div class="ph" style="background:${fondo(p.canal)}">${esc(p.titulo)}</div>${img}
      ${vivo ? '<span class="vivo">EN VIVO</span>' : ""}
      <span class="pts ${nivel(p)}">${pts}${p.fuente === "TMDB" ? "<small>TMDB</small>" : ""}</span>
      <div class="hora">${hhmm(p.ini)} <span>→ ${hhmm(p.fin)}</span></div>
      ${vivo ? `<div class="prog"><i style="width:${avance}%"></i></div>` : ""}
    </div>
    <div class="info">
      <h3>${esc(p.titulo)} ${p.anio ? `<small>(${p.anio})</small>` : ""}</h3>
      <div class="meta"><span class="canal-tag">${esc(p.canal)}</span>${p.genero ? `<span>${esc(p.genero)}</span>` : ""}
        ${p.ini.toDateString() !== new Date(est.dia + "T12:00").toDateString() ? `<span>· ${p.ini.toLocaleDateString("es-AR",{weekday:"short", day:"numeric"})}</span>` : ""}</div>
      ${p.sinopsis ? `<p class="sin${abiertos.has("s" + p.i) ? " abierta" : ""}" data-k="s${p.i}">${esc(p.sinopsis)}</p>` : ""}
    </div>
    ${conAlts && p.alts.length ? `<div class="alts"><h4>También a esta hora</h4>${p.alts.map(i => {
      const a = pelis[i];
      const k = "a" + p.i + "-" + a.i;
      const extra = [a.genero, a.anio, `${hhmm(a.ini)} → ${hhmm(a.fin)}`].filter(Boolean).join(" · ");
      return `<div class="alt-item${abiertos.has(k) ? " abierta" : ""}" data-k="${k}">
        <div class="alt"><span class="h">${hhmm(a.ini)}</span><span class="p ${nivel(a)}">${a.puntaje.toFixed(1)}</span>
          <span class="t">${esc(a.titulo)}</span><span class="c">${esc(a.canal)}</span></div>
        <div class="alt-det"><div class="m">${esc(extra)}${a.fuente === "TMDB" ? " · puntaje TMDB" : ""}</div>
          ${esc(a.sinopsis || "Sin descripción disponible.")}</div>
      </div>`;
    }).join("")}</div>` : ""}
  </article>`;
}

function duracion(ms) {
  const m = Math.round(ms / 60000), h = Math.floor(m / 60);
  return h ? `${h} h ${String(m % 60).padStart(2, "0")} min` : `${m} min`;
}

function pintar() {
  const ahora = new Date();
  document.querySelectorAll(".dia").forEach(b => b.classList.toggle("on", b.dataset.d === est.dia));
  document.querySelectorAll("#vista button").forEach(b => b.classList.toggle("on", b.dataset.v === est.vista));
  document.querySelectorAll(".canal-chip").forEach(b => b.classList.toggle("on", !est.apagados.includes(b.dataset.c)));
  $("#orden").value = est.orden; $("#min").value = est.min; $("#minv").textContent = est.min.toFixed(1);
  $("#sinp").checked = est.sinp; $("#term").checked = est.term;
  const filtrando = est.min > 0 || est.apagados.length || !est.sinp;
  $("#fbtn").classList.toggle("on", filtrando);
  $("#orden").disabled = est.vista === "rutina";

  let lista = pelis.filter(p => p.dia === est.dia);
  let html = "";
  if (est.vista === "rutina") {
    lista = lista.filter(p => p.rutina && (est.term || p.fin > ahora)).sort((a, b) => a.ini - b.ini);
    lista.forEach((p, i) => {
      if (i) { const libre = p.ini - lista[i-1].fin; if (libre >= 10*60e3) html += `<div class="hueco">libre ${duracion(libre)}</div>`; }
      html += tarjeta(p, ahora, true);
    });
    $("#cuenta").textContent = lista.length ? `Rutina de ${lista.length} películas sin superposiciones (${D.minRutina}+)` : "";
  } else {
    lista = lista.filter(p =>
      !est.apagados.includes(p.canal) &&
      (est.term || p.fin > ahora) &&
      (p.puntaje == null ? est.sinp : p.puntaje >= est.min));
    const orden = {
      puntaje: (a, b) => (b.puntaje ?? -1) - (a.puntaje ?? -1) || a.ini - b.ini,
      hora: (a, b) => a.ini - b.ini || (b.puntaje ?? -1) - (a.puntaje ?? -1),
      canal: (a, b) => a.canal.localeCompare(b.canal) || a.ini - b.ini,
    }[est.orden];
    lista.sort(orden);
    html = lista.map(p => tarjeta(p, ahora)).join("");
    $("#cuenta").textContent = `${lista.length} películas`;
  }
  $("#lista").innerHTML = html || `<div class="vacio">No hay películas con estos filtros.</div>`;
}

$("#lista").addEventListener("click", e => {
  const el = e.target.closest(".alt-item") || e.target.closest(".sin");
  if (!el) return;
  const k = el.dataset.k;
  el.classList.toggle("abierta") ? abiertos.add(k) : abiertos.delete(k);
});
pintar();
setInterval(pintar, 60000);   // actualiza "EN VIVO" y la barra de avance
</script>
</body>
</html>
'''


if __name__ == "__main__":
    main()
