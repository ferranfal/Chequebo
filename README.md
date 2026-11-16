# Chequebo

**Monitor de procesos de escritorio para Linux.** Lanza un programa, registra su
consumo y vuelve después para ver qué ocurrió. Software libre con licencia MIT.

## Instalación y arranque

Requiere Linux con escritorio y Python 3.10 o superior. No necesita servidor.

```bash
git clone https://github.com/ferranfal/Chequebo.git
cd Chequebo
./install.sh
./run.sh
```

El instalador usa `uv` si está disponible; en caso contrario usa `venv` y `pip`.
Instala las dependencias en `.venv`, dentro de esta carpeta, sin cambiar el Python
del sistema. En Debian/Ubuntu, si falta `venv`: `sudo apt install python3-venv`.
Si falta `libxcb-cursor0` para X11, el instalador la descarga con APT y la extrae
dentro de `.venv/native`, sin sudo; `run.sh` la carga solo para Chequebo.
Si Qt informa de otras bibliotecas gráficas ausentes, instala `libegl1`, `libopengl0`
y las bibliotecas XCB necesarias mediante el gestor de paquetes de tu distribución.

También puedes instalarlo en tu propio entorno con `pip install '.[nvidia]'` y
ejecutar `chequebo`. Sin el extra `nvidia`, CPU, RAM, disco y sesiones siguen funcionando.

## Uso

1. Escribe un comando y elige su carpeta de trabajo. Pulsa **Lanzar y grabar**.
   Ejemplo incluido: `python3 examples/workload.py`, desde la carpeta del proyecto.
2. Para un programa ya abierto, pulsa **Elegir proceso y grabar**.
3. Elige toda la sesión, los últimos minutos o un intervalo en segundos desde
   el inicio. Pulsa una gráfica o mueve el deslizador para inspeccionar un instante.
4. La tabla inferior conserva los **máximos de la sesión completa** y los compara
   con los del intervalo visible. Pulsa un máximo para localizarlo en las gráficas.
5. Exporta las muestras o el informe de máximos a CSV. **Abrir sesión** recupera
   una grabación anterior, incluso si su programa ya ha terminado.

**Parar grabación y cerrar la ventana no terminan el programa monitorizado.**
Cerrar la ventana sí detiene la recogida de muestras. Minimizarla permite continuar.
Las sesiones se guardan en `~/.local/share/chequebo` (respeta `XDG_DATA_HOME`).
Cada muestra se confirma en SQLite; un cierre inesperado no borra las anteriores.
La salida del comando se guarda en el `.log` situado junto a su sesión.

El comando se ejecuta directamente, sin shell y con entrada estándar cerrada.
Para tuberías o redirecciones, usa explícitamente `bash -lc 'tu comando'`.
Esta primera versión está pensada para tareas no interactivas.

## Qué significa cada medida

| Medida | Alcance y significado |
|---|---|
| CPU del programa | Suma del tiempo de CPU de los procesos observados, dividida por el tiempo transcurrido y las CPU lógicas del equipo. 100 % equivale a ocuparlas todas. |
| CPU lógicas equivalentes | Referencia secundaria: una unidad es el tiempo de una CPU lógica ocupada al 100 %. No cuenta núcleos físicos ni identifica CPU concretas. |
| Cada CPU lógica | Actividad del **sistema completo** en el instante seleccionado. Incluye otros programas. |
| RAM PSS | Memoria física de los procesos observados, prorrateando páginas compartidas. Evita sumar la RSS como si toda la memoria fuera privada. |
| Swap | Suma de la swap que informa Linux para los procesos observados. |
| Disco | Bytes realmente leídos/escritos según los contadores de Linux, por segundo. El uso de caché puede producir cero lecturas físicas. |
| GPU | Actividad global del dispositivo; media entre dispositivos si hay varios. No se atribuye ese porcentaje al programa. |
| VRAM | Memoria NVIDIA asociada a los PID observados, sin duplicar un mismo PID entre las listas de gráficos y cómputo. Suma entre tarjetas. |

El número de CPU lógicas **se detecta automáticamente**. No hay un número de
núcleos codificado en la interfaz. Las sesiones guardan la topología de su equipo
de origen y pueden abrirse en otro con una configuración diferente.

## NVIDIA y otros dispositivos

**Comprobar GPU** muestra el resultado del acceso al controlador y la memoria de
las tarjetas detectadas. También permite reintentar la lectura durante una sesión.

```bash
./run.sh --check-gpu
nvidia-smi
```

NVIDIA usa NVML, instalada mediante `nvidia-ml-py`; requiere un controlador
funcional y acceso a los dispositivos `/dev/nvidia*`. No hace falta instalar
PyTorch ni el CUDA Toolkit. Para AMD se lee la actividad global si el controlador
expone `gpu_busy_percent` en sysfs. La VRAM AMD/Intel y la actividad Intel no están
implementadas en esta versión. Una métrica ausente aparece como **No disponible**,
nunca como cero. Un cero de VRAM válido puede significar que el programa no usa GPU.

## Límites de esta primera versión

- Es un monitor **por muestreo**, de 0,2 a 60 segundos, con 1 segundo por defecto.
  Los máximos son los mayores valores **observados**; no garantizan capturar picos
  que ocurran entre muestras. CPU y disco son promedios entre lecturas.
- Sigue procesos hijos detectados y conserva sus identidades PID + creación.
  Los programas lanzados tienen su propia sesión POSIX: también se encuentran
  hijos huérfanos que mantengan esa sesión. Hijos muy breves, hijos que se separen
  antes de ser detectados y trabajo delegado a servicios externos pueden escapar.
  No utiliza cgroups, eBPF ni un servicio con privilegios.
- No puede recuperar historia anterior a la grabación. Las ejecuciones demasiado
  cortas pueden terminar sin muestras. La primera lectura de tasas sirve de referencia.
- El acceso a procesos de otros usuarios o restricciones de `/proc` puede impedir
  algunas medidas. La selección gráfica muestra procesos de tu usuario.
- No mide red por proceso ni energía por proceso. No atribuye la actividad de
  cada CPU lógica ni la actividad global de GPU a un programa concreto.
- Las muestras se conservan en memoria para explorar una sesión. Esta versión
  prioriza ejecuciones individuales, no monitorización de meses enteros.

## Grabación desde terminal

```bash
./run.sh --headless --interval 0.5 -- python3 examples/workload.py
./run.sh --headless --pid 1234
./run.sh --headless --output mi-sesion.chequebo -- ./mi-programa
```

Ctrl+C detiene el registro, sin matar el programa. Puedes abrir el archivo después
en la ventana. `--data-dir /ruta` cambia la carpeta de sesiones.

## Código y pruebas

El código se divide en módulos pequeños, sin servicios externos:

- `metrics.py`: procesos, CPU, memoria, disco y adaptador GPU.
- `session.py`: SQLite, máximos y CSV.
- `recorder.py`: lanzamiento y recogida en un hilo independiente.
- `ui.py`: ventana de escritorio y navegación por las gráficas.
- `__main__.py`: entrada de escritorio y terminal.

```bash
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -e '.[dev,nvidia]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

Pruebas para máximos y valores ausentes, guardado, procesos reales, hijos huérfanos,
parada de grabación, deduplicación de VRAM, navegación y normalización con 1, 4, 8,
16, 64 y 128 CPU lógicas. La integración continua ejecuta las pruebas en Linux.
La compatibilidad de cada controlador GPU requiere una comprobación en hardware real.

Licencia del código de Chequebo: [MIT](LICENSE). Sus dependencias conservan sus
propias licencias.
