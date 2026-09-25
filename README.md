# observatorio_electrico

Bot que responde preguntas sobre publicaciones del sector eléctrico chileno —
precio nudo, tarificación, normas técnicas, informes y balances del
Coordinador, minutas de operación del CEN, discrepancias en curso, etc.
La idea final es un solo bot que dé el "estado del arte" de varias fuentes
del sector. **Se parte por una sola fuente: el Panel de Expertos.** El resto
(CNE, precio nudo, tarificación, normas técnicas, balances, minutas del CEN)
se agrega después, como módulos nuevos dentro de este mismo proyecto -- no
como proyectos separados. Este README es el punto de partida para una
conversación nueva que no tiene el contexto de cómo se llegó hasta acá.

## Proyecto hermano: correspondencia_cen

Este proyecto es el segundo de una familia. El primero,
`correspondencia_cen` (misma carpeta GitHub, al lado de este), hace lo mismo
pero para la correspondencia del Coordinador Eléctrico Nacional
(`cartas.coordinador.cl`). Ya está funcionando en una VPS Oracle chica
(1 vCPU / ~1GB RAM) con:

- Scraper propio (Python + `requests`, sin navegador) que guarda cada carta
  nueva en una base SQLite con índice FTS5 (texto completo buscable).
- Bot de Telegram con motor de IA (function calling: DeepSeek primero,
  Gemini de respaldo si falla) que responde preguntas en lenguaje natural
  sobre esa base.
- Dos herramientas para el modelo: una que busca y devuelve un **resumen**
  liviano de varios candidatos, y otra que trae el **texto completo** de UNA
  carta puntual cuando hace falta detalle -- nunca se le entrega al modelo
  un extracto cortado a N caracteres al azar (eso se probó y se descartó:
  "o el resumen, o el completo, nunca un recorte arbitrario").
- Memoria conversacional simple por (chat, usuario), ventana de 30 minutos.
- Todo como servicios `systemd` (bot corriendo siempre, scraper por timer
  cada 3h), en un repo de GitHub que la VM sincroniza con `git pull`.

**Este proyecto nuevo debería reusar ese mismo patrón** (estructura de
carpetas, `motor_ia/proveedores_ia.py` se puede vendorear casi textual --
es genérico, no depende del dominio), no reinventarlo. Si hace falta ver
código de referencia, está en ese repo hermano.

## Por qué una VM aparte (no la misma que correspondencia_cen)

El sitio del Panel es bastante más difícil de scrapear que el del
Coordinador (ver abajo) -- probablemente necesite un navegador headless en
vez de `requests` simple, lo que pesa más en RAM y es más frágil. Se
decidió aislarlo en su propia VM (Oracle, especificación idéntica a la de
`correspondencia_cen`) para que un bug o un pico de recursos acá no le
pegue al bot de cartas que ya funciona. La VM la va a proporcionar el
usuario -- no está conectada todavía.

Por ahora el bot de Telegram de este proyecto es **separado** del de
`correspondencia_cen`. Unificarlos en un solo bot que consulte varias bases
es una decisión a futuro, no para ahora.

## Fuente: Panel de Expertos

Sitio: https://panelexpertos.cl/ -- resuelve "discrepancias" del sector
eléctrico (una parte presenta una discrepancia, el Panel dirime).

**RESUELTO (24-sep-2026): ya se investigó el mecanismo real, con Playwright +
`explorar_sitio.py` (queda en este repo).** La página `/discrepancias/
tramitadas/` no trae los datos en el HTML -- usa el plugin de WordPress
**"ShareoneDrive"**, que muestra una carpeta de **OneDrive/SharePoint** real
(un año = una carpeta, cada discrepancia = una subcarpeta dentro de esa
carpeta). Clave: **no hace falta navegador para el scraper final**, solo
para descubrir el mecanismo (ya hecho). Todo funciona con `requests` simple:

- **Listar contenido de una carpeta**: `POST` a
  `https://panelexpertos.cl/wp-admin/admin-ajax.php` con
  `action=shareonedrive-get-filelist`, más `drive_id`, `account_id`,
  `listtoken`, `_ajax_nonce`, `lastFolder` (id de la carpeta) y `folderPath`
  (base64 de un array JSON con la cadena de ids hasta ahí). El `listtoken` y
  el `_ajax_nonce` hay que sacarlos de la página HTML al cargarla (son por
  sesión/carga, no fijos) -- **eso sí requiere una carga inicial de la
  página** (con `requests` alcanza, no hace falta navegador, solo parsear el
  HTML/JS embebido para extraerlos).
- La respuesta es JSON con un campo `"html"` que trae el listado como
  fragmento HTML (hay que parsearlo, con regex o BeautifulSoup, similar a
  como se lee `cartas.coordinador.cl`). Cada carpeta/archivo tiene
  `data-id` (el id real de OneDrive) y `data-name`.
- **Descargar un archivo**: `GET` directo a
  `https://panelexpertos.cl/wp-admin/admin-ajax.php?action=shareonedrive-download&dl=1&id=<file_id>&drive_id=<drive_id>&account_id=<account_id>&listtoken=<token>`
  -- esto SÍ se probó con `requests` puro y funcionó: bajó un dictamen real
  de 45 páginas (PDF válido, extraíble con PyMuPDF sin problema).
- **Estructura real de cada discrepancia** (probado en el caso real
  "04-2026 Disc. Guacolda en contra del Coordinador"): subcarpetas
  `01.Actuaciones del Panel`, `02.Escritos Presentados`, `03.Actas`, y el
  archivo final `Dictamen Discrepancia N°X-YYYY.pdf` -- calza exacto con las
  fases del procedimiento del DS 44 (admisibilidad, escritos, audiencia,
  dictamen).
- **Prueba de extremo a extremo ya hecha**: se listaron las 26 discrepancias
  de 2026, se bajó el dictamen de una de ellas (N°4-2026, Guacolda contra el
  Coordinador por el Estudio de Capacidad Técnica Disponible en Sistemas de
  Transmisión Dedicados), se extrajo el texto completo (134.341 caracteres),
  y se le preguntó a DeepSeek quién presentó la discrepancia, contra quién y
  sobre qué -- contestó bien y citó la carta de origen (DE 08393-25), y fue
  honesto en que no podía confirmar el resultado final porque esa parte del
  PDF no estaba en el extracto que se le pasó (no inventó nada).
- **Pendiente todavía**: conseguir los ids de carpeta de cada año (2004 a
  2026) sin tener que hacer clic uno por uno -- o se navega una vez con
  Playwright y se guarda el mapeo año→id, o se prueba si el listado de
  "Inicio" (la raíz) ya los expone directamente. Tampoco se probó aún si el
  `listtoken`/`_ajax_nonce` de una carga de página sirve para *todas* las
  llamadas subsiguientes de esa sesión de scraping o si expira rápido -- hay
  que probarlo antes de dar por cerrado el diseño del scraper de producción.
- El buscador en la IP directa (`64.202.184.231`) puede ignorarse -- el
  mecanismo real (ShareoneDrive + admin-ajax.php) es más que suficiente y ya
  está probado.

### Qué es el Panel y cómo funciona en detalle

(Verificado contra el texto completo del **Decreto Supremo N° 44 de 2017**
del Ministerio de Energía -- Reglamento del Panel de Expertos --, guardado
en este repo como `DS_44_2017_Reglamento_Panel_de_Expertos.pdf`, junto con
`LGSE_DFL_4_20018.pdf` -- Ley General de Servicios Eléctricos completa. **Ojo:
lo que sigue viene del reglamento real, no de un resumen de internet** --
la primera versión de este README tenía un error: los actores NO se llaman
"Presentante" / "Demandado", esos términos no aparecen en el texto.)

**Qué es**: órgano creado por ley (LGSE, Título VI, arts. 208 a 212 y
212º-13), de competencia acotada, integrado por **7 profesionales** (5
ingenieros o licenciados en ciencias económicas + 2 abogados), designados
por el **Tribunal de Defensa de la Libre Competencia** mediante concurso
público, con mandato de **6 años** (renovación parcial cada 3). Se
pronuncia mediante **dictámenes vinculantes** sobre discrepancias en
materias tasadas expresamente por ley -- no tiene competencia general, solo
sobre lo que la ley enumera.

**Actores reales** (verificado en el texto, arts. 2, 32 y 35 del DS 44):
- **Requirente**: quien presenta la discrepancia (así lo llama el
  reglamento en el art. 32 letra f).
- La contraparte -- el reglamento **no usa un sustantivo fijo** para ella
  (no dice "demandado"), solo la describe como "la persona, empresa o
  entidad en contra de la cual se presenta la discrepancia" (art. 32
  letra h).
- **Partes**: ambos lados de la discrepancia en conjunto.
- **Interesados / terceros interesados**: se suman al proceso con informes
  u observaciones. La **Comisión (CNE)** y la **Superintendencia (SEC)**
  son "interesados" en las discrepancias donde no son parte (art. 35).
- **Panel de Expertos**: dirime, apoyado por su **Secretario Abogado**
  (recibe y certifica presentaciones, hace el examen de admisibilidad,
  notifica a las partes, y publica los dictámenes en el sitio web -- art. 16).
- **Coordinador** y **Coordinados**: tienen definición propia en el
  reglamento (art. 2) y aparecen seguido como partes, dado que muchas
  discrepancias son justamente contra decisiones del Coordinador o de la CNE.

**Confirmado con un escrito real** (una presentación de discrepancia real
de 2025, no solo la ley): **no existe un rol genérico tipo "demandado" ni
en el sitio ni en los escritos**. Los escritos nombran directamente a la
institución o empresa contra la que se presenta ("Presenta Discrepancia en
contra del Coordinador Eléctrico Nacional..."), no una etiqueta de rol. Dos
convenciones prácticas que sí se repiten y vale la pena usar en la base:
- **"Acto Discrepado"**: así suelen llamar (convención de redacción, no
  término legal) al acto/carta/instrucción puntual que se discute --
  aparece definido dentro del mismo escrito ("...carta DE06549-24, en
  adelante el 'Acto Discrepado'").
- Estructura fija del escrito: **"EN LO PRINCIPAL"** (la discrepancia
  propiamente tal) + **"OTROSÍ"** numerados (acompaña documentos, acredita
  personería, señala domicilio y correos, otorga poder a representantes),
  dirigido siempre a **"Honorable Panel de Expertos"**. El propio escrito
  suele incluir su autoexamen de admisibilidad (competencia, plazo,
  requisitos del art. 32) -- útil si en algún momento se quiere extraer
  metadata estructurada del cuerpo del documento, no solo del listado del
  sitio.

**Implicancia para la base de datos**: el campo `requirente`/`contraparte`
debe guardar el **nombre real de la entidad** (ej. "Betel SpA", "Coordinador
Eléctrico Nacional"), no un rol genérico -- porque así es como el propio
Panel y los escritos los identifican.

**Procedimiento real** (arts. 31 a 39 del DS 44):
1. **Presentación**: por escrito, dentro de 15 días desde el hecho que la
   origina como regla general -- pero hay **decenas de plazos especiales**
   según la materia exacta (10, 15, 20, 30, 45, 50 días, corridos o hábiles
   según el caso -- ver el detalle letra por letra en el art. 28).
2. **Examen de admisibilidad**: el Secretario Abogado informa al Panel
   dentro de 24 horas; el Panel notifica a partes/Comisión/Superintendencia
   y publica en su sitio dentro de 3 días, y en sesión especial declara
   admisible o inadmisible.
3. **Programa de trabajo**: si es admisible, el Panel da un plazo (mínimo 7
   días) para que otros interesados presenten informes/observaciones, y fija
   una **audiencia pública obligatoria** (entre 10 y 30 días desde la
   notificación).
4. **Dictamen**: fundado, y con una característica clave -- el Panel **debe
   optar por una de las alternativas en disputa, sin poder fijar valores
   intermedios** (art. 36: "optando por una u otra alternativa... sin que
   pueda adoptar valores intermedios"). Se notifica a partes, Comisión,
   Superintendencia e interesados; es público desde la notificación.
5. **Es vinculante y no admite recursos** de ningún tipo, ni jurisdiccionales
   ni administrativos (art. 37). Única excepción: el Ministro de Energía
   puede declararlo "inaplicable" dentro de 10 días, y solo si trata
   materias ajenas a la competencia del Panel (art. 38) -- no es una
   revisión de fondo.

**Materias de competencia** (arts. 27 a 30 del DS 44, sobre la base del
art. 208 LGSE): **no es una competencia genérica** -- es una lista de ~20
materias específicas enumeradas letra por letra, cada una con su propio
plazo de presentación y de dictamen: informes de servicios complementarios,
estudios de costos eficientes, valorización de instalaciones, autorización
de conexión, acceso abierto en transmisión (nacional y dedicada), plan de
expansión de transmisión, calificación de líneas/subestaciones, vidas
útiles de elementos de transmisión, tarificación de distribución (VNR,
costos de explotación), licitaciones de suministro y sus proyecciones de
demanda, revisión de precios de contratos, discrepancias de gas
(valorización, tasa de costo de capital, rentabilidad), e indemnizaciones
por operación de embalses. **Vale la pena usar esta lista (o algo parecido)
como vocabulario controlado para el campo `materia` en la base**, en vez de
texto libre, para que las consultas por tipo de materia sean confiables.

**Financiamiento**: presupuesto anual vía "cargo por servicio público" que
pagan todos los usuarios finales -- mismo mecanismo que financia al
Coordinador --, fijado anualmente por la Comisión (art. 212º-13 LGSE).

**Para el motor de IA de este proyecto**: el usuario ya dejó armado un
prompt maestro (`Prompt_IA_Panel_de_Expertos.txt`, en este repo) con
disciplina estricta de citación (artículo exacto + cita textual, formato
RESPUESTA/FUNDAMENTO NORMATIVO/ANÁLISIS/TRAZABILIDAD, sin inferir ni
completar vacíos). Ese prompt es para responder preguntas **normativas**
("¿qué plazo tiene tal discrepancia?", "¿quién puede presentar tal cosa?")
basándose solo en los dos PDF -- es un motor distinto (o una herramienta
adicional) al que responde sobre el contenido de discrepancias puntuales
ya tramitadas. No mezclar los dos: uno cita ley, el otro cita casos reales.

### Volumen y estructura de los datos

- 2024: 47 discrepancias nuevas, 404 dictámenes emitidos (resolviendo 40
  casos) -- es decir, **cada discrepancia trae ~10 documentos en promedio**,
  no es un documento por caso.
- El sitio organiza el archivo histórico por año, con años visibles desde
  2004 (~20 años de historial). La carga histórica completa es una tarea
  grande -- no intentarla de una sola vez; empezar por lo reciente/en curso
  y backfillear después, igual que se hizo con el backfill huérfano de
  `correspondencia_cen`.
- Casos están en dos estados: **"En curso"** y **"Tramitadas"** (cerradas).

## Fuente: CNE (Comisión Nacional de Energía)

Segundo módulo, base propia (`datos/cne.sqlite`, no comparte tablas con
`discrepancias.sqlite`). A diferencia del Panel (una sola dimensión:
discrepancias), la CNE es un árbol de secciones, cada una con su propio
mecanismo -- no asumir que lo que funciona para una sirve para otra.

### Normas Técnicas y Servicios Complementarios -- HECHO

- Fuente real: `https://apinormastecnicas.cne.cl/normas` y `/servicios` --
  API JSON limpia sin login, detrás de un iframe en
  `portalnormativo.cne.cl/?page_id=1577` (ese `page_id` es fijo, confirmado
  en el propio HTML de la página, no se regenera).
- `/normas` trae 11 categorías de resoluciones (las 10 Normas Técnicas
  numeradas + "Informe SSCC") más una categoría aparte "Normativa vigente"
  (esquema distinto: el texto consolidado actual + anexos, sin fechas ni
  número de resolución). `/servicios` trae 1 categoría más, mismo esquema
  que las resoluciones.
- **Ningún archivo de este módulo es descargable directo** -- el 100% de
  los links son de SharePoint interno de la CNE (`comisionenergia.sharepoint.com`).
  Medido con Playwright real: ~1.2GB de RAM por descarga (el visor de PDF
  de SharePoint es pesado), no viable en esta VPS de 1GB. Por eso este
  módulo guarda solo metadata + link, nunca texto -- ver
  `adquisicion/scraper_cne_normativa.py`.
- Ya conectado al bot (`motor_ia/herramientas_ia_cne.py`:
  `buscar_resoluciones_cne`, `buscar_normativa_vigente_cne`) y al chequeo
  diario (`chequeo_diario.py` revisa `/normas` y `/servicios` cada mañana
  junto con el Panel, un solo mensaje de Telegram si hay novedades en
  cualquiera de las dos fuentes).

### Estudios (`cne.cl/estudios/electricidad/`) -- INVESTIGADO, NO CARGADO

- Página WordPress plana, sin API: 10 estudios en una lista simple, cada
  uno con fecha de publicación y un botón "Descargar".
- Mixto: 8 de los 10 son PDF directos en el propio `wp-content/uploads` de
  cne.cl (fácil, mismo patrón que correspondencia_cen). Los otros 2 son
  SharePoint (mismo problema que Normas Técnicas -- metadata solamente).
- Falta: escribir el scraper (reutilizar `es_link_directo`/`_descargar_y_extraer`
  de `scraper_cne_normativa.py`, la lógica ya está probada) y sumarlo al
  bot/chequeo diario.

### Tarificación (`cne.cl/tarificacion/electrica/`) -- BLOQUEADO, no insistir sin nueva pista

**No es un mecanismo único.** La página principal solo lista 18
subcategorías (Precio Nudo Corto Plazo, VAD, Costos de Falla, Cargos de
Transmisión, Expansión de Transmisión, etc.). Cada subcategoría se
comporta distinto -- confirmado con casos reales, no es una suposición:

- **"Precio Medio de Mercado"** (subcategoría relacionada, no una de las
  18 principales): funciona bien. Tiene un dropdown real (`<ul
  class="dropdown-menu" id="pmmList">`, server-rendered, sin JS) con un
  link por año (`cne.cl/precio-medio-de-mercado-2/2026-2/`), y esas
  páginas de año SÍ tienen PDFs directos en `wp-content/uploads` (probado:
  9 publicaciones reales de 2026, mensuales).
- **"Precio Nudo Corto Plazo"** (una de las 18, la que preguntó el
  usuario): las páginas de período
  (`cne.cl/tarificacion/electrica/precio-nudo-corto-plazo/<periodo>/`,
  ej. "2026-segundo-semestre") **cargan completamente vacías** para
  cualquier acceso automatizado -- probado con:
  - `curl` plano (HTML ya viene con `<div class="single-post-container"
    ...></div>` vacío, sin JS).
  - Playwright con JS completo, esperando `networkidle` + 2-3s.
  - Playwright navegando primero por la página padre (referer real) antes
    de entrar al período.
  - Playwright con scroll (por si era lazy-load con IntersectionObserver).
  - Playwright con medidas anti-detección (`navigator.webdriver`
    sobrescrito, `--disable-blink-features=AutomationControlled`).
  - Probado en un período reciente (2024) Y uno de 2005 -- ambos vacíos
    por igual, así que no es un hueco de contenido nuevo, es sistémico.
  - No hay ninguna request de red (XHR/fetch/admin-ajax) que traiga el
    contenido -- se revisó el tráfico completo de red, nada relevante.
  - El buscador propio del sitio (`cne.cl/?s=...`) tampoco indexa estos
    documentos.
  
  **PERO el usuario, navegando a mano en su propio browser real, SÍ ve el
  contenido** -- un acordeón "▲ Fijación de Precios de Nudo Segundo
  Semestre 2026" con varios ítems descargables (Informe Técnico
  Definitivo, Bases de Cálculo, Precios de Nudo Definitivo, Informe
  Técnico Preliminar, etc.). Confirmó con capturas de pantalla la URL
  exacta (`.../precio-nudo-corto-plazo/2026-segundo-semestre/`) -- es la
  misma que se probó automatizado, y aun así no coincide el resultado.
  Dos archivos reales que el usuario encontró a mano, para referencia
  concreta de qué se busca:
  - `https://www.cne.cl/wp-content/uploads/2026/08/ITD-PNCP-Jul-2026.pdf`
    (Informe Técnico Definitivo, Precio Nudo Corto Plazo)
  - `https://www.cne.cl/wp-content/uploads/2026/09/Informe-Final-Estudio-periodo-de-control-de-punta-1.pdf`
    (otra subcategoría, "Período de Control de Punta" -- mismo problema,
    ni se investigó el mecanismo todavía)

  **Hipótesis no descartadas, sin verificar:** geobloqueo o bloqueo de IP
  de datacenter (la VM/este entorno corren desde una IP de nube, no
  residencial chilena); algún tipo de rate-limiting o desafío anti-bot que
  no se manifiesta como error HTTP sino como contenido vacío; una cookie o
  estado de sesión que solo se genera con interacción humana real que
  Playwright no está replicando aunque parezca idéntico. **Antes de
  retomar esto, lo más eficiente es pedirle al usuario que abra las
  DevTools (F12 → Network) en su navegador real mientras carga esa página,
  y compare qué requests aparecen ahí contra lo que se ve automatizado --
  eso resolvería la duda en un minuto en vez de seguir adivinando.**
- Las 16 subcategorías restantes: sin investigar todavía. No asumir que se
  comportan como ninguna de las dos anteriores.

### Diario Oficial de Chile -- EN INVESTIGACIÓN, con mecanismo entendido

Pedido nuevo: revisar todos los días si hay publicaciones del sector
eléctrico (CNE, Ministerio de Energía) en la edición electrónica
(`diariooficial.interior.gob.cl/edicionelectronica/`).

- Cada publicación de cada edición tiene un PDF directo, sin login:
  `/publicaciones/AAAA/MM/DD/<edición>/<sección>/<CVE>.pdf`. La página de
  una edición organiza las publicaciones en tablas con un header de
  Ministerio (`<td class="title4">MINISTERIO DE ENERGÍA</td>`) -- se puede
  filtrar por ahí directo, con el título completo de cada publicación al
  lado del link. Probado con la edición real de hoy (44557, 24-09-2026):
  sección "Ministerio de Energía" con 6 publicaciones reales (nombramientos
  de SEREMI, esa fecha puntual no tenía nada de la CNE en particular).
- **El obstáculo**: pedir una edición requiere `date` + `edition` como
  parámetros, y el servidor devuelve 403 si no coinciden exactamente --
  no es un cálculo simple de días hábiles (hay feriados, Fiestas Patrias,
  etc. que se saltan de forma no trivial). El sitio resuelve esto con un
  datepicker JS propio (`#datep`, jQuery UI). Ya se probó y funciona:
  seleccionando una fecha en el datepicker con Playwright, devuelve la URL
  con el par correcto (probado: 24-08-2026 → edición 44532). Una vez que
  se tiene el par correcto, la descarga posterior funciona con `requests`
  normal, sin navegador.
- **Para producción** alcanza con resolver la edición de HOY una vez al
  día (no hace falta backfill de fechas pasadas para el chequeo diario) --
  eso sí es un costo bajo y acotado, a diferencia del problema de
  Tarificación. Falta: revisar `js/do.js` del sitio (no se alcanzó a mirar
  ese archivo) para ver si el cálculo fecha→edición se puede replicar sin
  navegador en absoluto, o si hay que aceptar un Playwright liviano una
  vez al día (medir RAM real antes de asumir que es barato, no dar por
  sentado que es mucho más liviano que el caso de SharePoint solo porque
  esta página no tiene visor de PDF -- medirlo).

## Decisiones de diseño ya tomadas

- **No se guardan PDF ni PPT.** Se descargan transitoriamente solo para
  extraer el texto, y se botan -- igual que en `correspondencia_cen`. Si
  alguien quiere el archivo original, se le da el link, no una copia local.
- **Dos tablas, no una**: `discrepancias` (caso: número, requirente,
  contraparte, materia -- idealmente del vocabulario controlado del art. 28
  DS44, no texto libre --, estado, fecha apertura/cierre) y `documentos`
  (cada escrito/informe/dictamen, texto completo, ligado a su discrepancia
  por FK). FTS5 sobre `documentos`.
- **Sin resumen pregrabado para empezar.** Mismo aprendizaje que con
  `correspondencia_cen`: no pre-truncar ni pre-resumir por las dudas: probar
  primero con texto completo + búsqueda FTS, agregar resumen solo si se
  demuestra que hace falta.
- Debería poder responder preguntas tipo: "¿qué discrepancias hay abiertas?",
  "¿de qué se trata la discrepancia donde Guacolda pedía que se le pagaran
  los sobrecostos?", "¿qué dijo el Panel en el caso X?", "¿cuál se cerró
  recientemente?".

## Qué falta para arrancar

1. Conseguir la VM del usuario y dejarla lista (Python, venv -- mismos pasos
   que se hicieron para `correspondencia_cen`).
2. ~~Investigar cómo se puede scrapear el sitio del Panel~~ -- **hecho**, ver
   arriba (ShareoneDrive + `admin-ajax.php`, probado de extremo a extremo con
   datos reales de 2026). Queda resolver el mapeo año→id de carpeta y cuánto
   dura vigente el `listtoken`/`_ajax_nonce` antes de escribir el scraper
   "de producción" (`explorar_sitio.py`, en este repo, es el script
   exploratorio que probó todo esto -- no es el scraper final, hay que
   convertirlo en uno que no dependa de Playwright para las descargas).
3. Diseñar el scraper real: recorrer año por año (empezando por 2026/"En
   curso" y lo más reciente, no los 20 años de una sola pasada) → por cada
   discrepancia, bajar los PDF de `02.Escritos Presentados` y el `Dictamen`
   final, extraer texto, guardar en las dos tablas (`discrepancias` +
   `documentos`) -- sin guardar los PDF.
4. Bot de Telegram con el mismo patrón de dos herramientas (buscar liviano /
   detalle completo) que ya funciona en `correspondencia_cen`
   (`motor_ia/proveedores_ia.py` ya está copiado a este repo, listo para
   reusar).

## Cómo pedirle ayuda a Claude en este proyecto

Este README tiene todo el contexto necesario para retomar donde se dejó.
No asumas que el sitio del Panel se comporta como `cartas.coordinador.cl` --
la primera tarea real es investigar el sitio antes de construir nada.
