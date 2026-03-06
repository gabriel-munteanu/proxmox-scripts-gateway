# Research: UI Bypass pentru Proxmox Helper Scripts

## Obiectiv

Găsiți o metodă de a rula scriptele de instalare fără interfața UI (whiptail/dialog), pasând parametrii direct.

## Structura Scripturilor

### Fișiere Importate (sourced) de `build.func`

| Fișier | URL | Are UI (whiptail/dialog) |
|--------|-----|--------------------------|
| `api.func` | `.../misc/api.func` | ❌ Nu |
| `core.func` | `.../misc/core.func` | ❌ Nu |
| `error_handler.func` | `.../misc/error_handler.func` | ❌ Nu |

### Fluxul de Execuție al Scriptelor

```
ct/adguard.sh
    ↓
source build.func (care source-ul api.func, core.func, error_handler.func)
    ↓
header_info()      → afișează info header (fără UI)
    ↓
variables()        → setează variabile interne (fără UI)
    ↓
build_container()  → creează containerul efectiv
    ↓
description()      → setează descrierea containerului
```

### Funcții cu UI în `build.func` (~104 referințte la whiptail)

Aceste funcții **NU** sunt apelate în fluxul automat:
- `get_...()` - cer input interactiv când variabilele lipsesc
- `install_script()` - meniu principal cu UI
- `maybe_offer_save_app_defaults()` - salvare preferințe cu UI
- `advanced_settings()` - setări avansate cu UI

### Funcții care NU au UI

Acestea pot fi apelate direct:
- `variables()` - doar setează variabile
- `build_container()` - creează containerul
- `header_info()` - afișează info
- `description()` - setează descrierea

## Concluzie

**DA, este fezabil!** Se poate crea un wrapper care:
1. Sursăază `build.func`
2. Setează variabilele direct (`var_cpu`, `var_ram`, etc.)
3. Apelează `variables()` + `build_container()` fără UI

## Următorul Pas (TODO)

Analiza fezabilității extragerii funcționalității `build_container` în Python în Gateway.

---

# Research: Extragere build_container în Python

## Fluxul Complet de Instalare

```
build_container()
    │
    ├─► Construiește NET_STRING (network config)
    ├─► Descarcă install.func / alpine-install.func
    ├─► Setează variabile de mediu (PCT_OPTIONS, etc.)
    │
    └─► create_lxc_container()
            │
            ├─► Verifică storage (pvesm status)
            ├─► Găsește/descarcă template (pveam)
            ├─► pct create $CTID $TEMPLATE $PCT_OPTIONS
            └─► Pornește containerul
                │
                └─► lxc-attach -n $CTID -- bash -c "$(curl .../install/${var_install}.sh)"
```

## Comenzi Proxmox Utilizate

| Comandă | Scop |
|---------|------|
| `pct create` | Creează container LXC |
| `pct start` | Pornește containerul |
| `pct exec` | Execută comenzi în container |
| `lxc-attach` | Atașează la container pentru script instalare |
| `pvesm status` | Verifică storage disponibil |
| `pveam list/update` | Listează/descarcă template-uri |
| `qm status` | Verifică dacă ID e deja folosit (pentru VM) |

## Ce face build_container() - Detalii

### 1. Network Configuration (~line 3480-3520)
Construiește string-ul de network:
```bash
NET_STRING="-net0 name=eth0,bridge=${BRG:-vmbr0}"
# + MAC, IP, Gateway, VLAN, MTU, IPv6
```

### 2. Features (~line 3525-3545)
```bash
FEATURES="nesting=1,keyctl=1,fuse=1"  # bazat pe CT_TYPE și preferințe
```

### 3. Descarcare install.func (~line 3550-3565)
```bash
if [ "$var_os" == "alpine" ]; then
  _func_url=".../misc/alpine-install.func"
else
  _func_url=".../misc/install.func"
fi
FUNCTIONS_FILE_PATH="$(curl -fsSL "$_func_url")"
```

### 4. Export variabile (~line 3568-3610)
Exportă zeci de variabile: CTID, PASSWORD, SSH_KEYS, timezone, etc.

### 5. Validare storage (~line 3635-3650)
```bash
validate_storage_space "$CONTAINER_STORAGE" "$DISK_SIZE"
```

### 6. create_lxc_container() (~line 3660)
Creează și pornește containerul efectiv.

### 7. Configurare GPU/USB Passthrough (~line 3670-4100)
Adaugă configurații în `/etc/pve/lxc/${CTID}.conf`

### 8. Execuție script instalare (~line 4099)
```bash
lxc-attach -n "$CTID" -- bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/install/${var_install}.sh)"
```

## Variabile Necesare pentru build_container

| Variabilă | Descriere |
|-----------|-----------|
| `CTID` | ID-ul containerului |
| `var_os` | OS: debian, ubuntu, alpine, etc. |
| `var_version` | Versiunea OS |
| `var_cpu` | Număr core-uri |
| `var_ram` | RAM în MB |
| `DISK_SIZE` | Disk în GB |
| `var_template_storage` | Storage pentru template |
| `var_container_storage` | Storage pentru container |
| `BRG` | Bridge de rețea |
| `NET` | IP (dhcp sau static) |
| `GATE` | Gateway |
| `SD` | Search Domain |
| `NS` | Nameserver |
| `PW` | Parola root |
| `SSH` | SSH keys |
| `TAGS` | Tags pentru container |
| `CT_TYPE` | 0=privileged, 1=unprivileged |
| `PROTECT_CT` | Protecție ștergere |
| `CT_TIMEZONE` | Timezone |
| `var_gpu` | yes/no pentru GPU passthrough |

## Opțiuni de Implementare

### Opțiunea A: Python wrapper peste pct/lxc-attach (RECOMANDATĂ)

Python-ul ar gestiona:
1. Primirea request-ului API
2. Validarea input-urilor
3. Logica de business (logs, etc.)
4. Răspunsul JSON

Dar execută:
- `pct create ...` direct
- `lxc-attach -n $CTID -- bash -c "..."` pentru scriptul de instalare

**Avantaje:**
- Simplu de implementat
- Păstrează compatibilitatea cu scriptele originale
- Actualizările scriptelor originale se aplică automat

**Dezavantaje:**
- Dependență de comenzile Proxmox pe host

### Opțiunea B: Replicare completă în Python

Python ar trebuie să:
1. Implementeze toată logica din `build_container()`
2. Gestioneze storage, template-uri
3. Parseze output-ul `pct create`
4. Implementeze GPU/USB passthrough

**Avantaje:**
- Control complet
- Nu depinde de scriptele bash

**Dezavantaje:**
- Muncă enormă de replicat
- Risc de desincronizare cu scriptele originale

### Opțiunea C: Wrapper Bash cu parametri (Intermediară)

Python generează un script bash temporar care:
1. Setează toate variabilele
2. Source-ă `build.func`
3. Apelează `variables()` + `build_container()`

```bash
#!/bin/bash
source <(curl -fsSL .../build.func)

export CTID=100
export var_os=debian
export var_version=12
# ... alte variabile

variables
build_container
```

**Avantaje:**
- Simplu
- Păstrează logica originală

**Dezavantaje:**
- Output-ul bash e greu de parsat în Python

## Concluzie Feasibility

**OPȚIUNEA A este cea mai fezabilă:**

Python Gateway → execută `pct create` + `lxc-attach` → returnează rezultat JSON

```python
# Pseudocode
async def install_container(config: AppConfig):
    # 1. Build pct command
    pct_cmd = build_pct_create_command(config)
    
    # 2. Execute
    result = await run_command(pct_cmd)
    
    # 3. Run install script
    install_cmd = f"lxc-attach -n {config.vmid} -- bash -c \"$(curl -fsSL {INSTALL_SCRIPT_URL})\""
    result = await run_command(install_cmd)
    
    return {"status": "success", "vmid": config.vmid}
```

Aceasta ar necesita:
1. Maparea config-ului Python la variabilele bash
2. Comenzi `pct` și `lxc-attach` disponibile pe host
3. Logging în Python (nu în bash)