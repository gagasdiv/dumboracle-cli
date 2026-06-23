# dumb-oracle

A small, error-tolerant Oracle admin CLI for **local** databases. It replaces the
old `.bat` + `.sql` scripts with a single maintainable Python tool.

- Pick a database from a YAML file (multiple connections + credentials).
- Uses **python-oracledb in thick mode** (needs an Oracle client; a 23ai client
  talks to databases 19c → 23ai and up).
- Any operation that fails just prints the error and drops you back to the menu —
  it never crashes the whole tool.

## What it can do

After you pick a database:

1. **Create user + grants** — creates the user and applies the everyday grant set
   (`CREATE SESSION`, `CREATE TABLE/VIEW/SEQUENCE/...`, `CREATE DATABASE LINK`,
   `EXP/IMP_FULL_DATABASE`, quota on the default tablespace). If the user already
   exists it asks before `DROP USER ... CASCADE`.
2. **Rename user / schema** — in-place rename via the `SYS.USER$` trick (Oracle has
   no supported `ALTER USER ... RENAME`). **Requires a SYS / SYSDBA connection.**
3. **Enable supplemental logging** — turns on DB-level minimal supplemental logging
   (SYSDBA) and adds `SUPPLEMENTAL LOG DATA (ALL) COLUMNS` to every table in a schema.
4. **Export schema (expdp)** — dumps to `./<user>--yyyymmdd-hhii/` containing the
   dump file(s) and the log.
5. **Import schema (impdp)** — point it at a folder; it **auto-finds** the `.dmp`
   files (works for dumps made by this tool *or* plain `expdp`), auto-detects the
   source schema/tablespaces, asks for the target schema, and if that schema exists
   offers to rename it to `<user>--yyyymmdd-hhii` first. Defaults objects to the
   `USERS` tablespace but asks if you want a different one.
6. **Clone schema** — duplicates a schema to `<user>--yyyymmdd-hhii` directly over a
   temporary loopback DB link (no dump files). The clone's password is set to its
   own name.

## Requirements

- Python 3.9+
- An Oracle client for **thick mode**. To use export/import/clone you need
  `expdp`/`impdp`, which ship with a **full client**, a **database install**, or the
  **Instant Client *tools* package** (Instant Client *basic* alone does *not*
  include them).

## Install (virtual environment)

The repo is self-contained and portable. Create a venv and install deps with the
included scripts:

```powershell
# Windows / PowerShell
.\setup.ps1
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
./setup.sh
source .venv/bin/activate
```

Both scripts create `.venv`, install `requirements.txt`, and copy
`connections.example.yaml` to `connections.yaml` if it doesn't exist yet.

## Point the tool at an Oracle client

The tool needs `oracle_client_lib_dir` (OCI library, for thick mode) and
`datapump_bin_dir` (`expdp`/`impdp`). You have three ways to set them:

1. **Bundle an Instant Client (portable).** Drop the
   `instantclient-basic-*.zip` and `instantclient-tools-*.zip` for your OS into the
   repo folder, then:

   ```bash
   python -m dumboracle extract-client
   ```

   This checks the zips match your OS, that **both** basic and tools are present,
   unzips them, verifies the OCI lib + `expdp`/`impdp`, and writes the paths into
   `connections.yaml`.

2. **Use an Oracle already installed on the machine.** Check health, then wire it:

   ```bash
   python -m dumboracle doctor       # read-only health report, changes nothing
   python -m dumboracle wire-path    # wires connections.yaml IF tooling is healthy
   ```

   `wire-path` only edits the config when `expdp`, `impdp` and the OCI lib are all
   resolvable via `PATH` / `ORACLE_HOME`; otherwise it explains what's missing and
   exits non-zero.

3. **Edit `connections.yaml` by hand** — set `oracle_client_lib_dir` /
   `datapump_bin_dir` to your Oracle `bin` folder yourself.

## Configure connections

```yaml
oracle_client_lib_dir: ""      # OCI client folder; set by extract-client/wire-path
datapump_bin_dir: ""           # folder with expdp/impdp

connections:
  - name: free23
    alias: hb1                # optional short handle for quick connect
    description: Local Oracle 23ai Free (PDB)
    dsn: localhost:1521/FREEPDB1
    username: system
    password: oracle

  - name: ora19-sys           # use a SYSDBA entry for rename / DB supplemental log
    dsn: localhost:1521/ORCLPDB1
    username: sys
    password: oracle
    sysdba: true
```

- `dsn` uses the service name, so it connects straight into the right PDB — no
  `alter session set container` needed.
- Add a `sysdba: true` entry (typically as `sys`) for the operations that need it
  (rename, DB-level supplemental logging).
- `alias` is optional. At the "Choose a database" menu you can pick a connection by
  its row number **or** by typing its alias (case-insensitive), e.g. `hb1`.
- `connections.yaml` holds credentials and machine-specific paths, so it is
  **git-ignored**. Commit `connections.example.yaml` instead.

## Run

```bash
python -m dumboracle                 # interactive menu (default), reads ./connections.yaml
python -m dumboracle -c /path/to.yaml
python -m dumboracle menu            # same as default
```

Then just follow the menus. Blank input / `0` / Ctrl-C backs out of any prompt.

### Commands

| Command          | What it does                                                        |
| ---------------- | ------------------------------------------------------------------- |
| *(none)* / `menu`| Interactive admin menu (the six operations below).                  |
| `extract-client` | Unzip bundled Instant Client (basic+tools) and wire `connections.yaml`. |
| `doctor`         | Read-only health report of Oracle tooling. Never changes anything.  |
| `wire-path`      | Wire `connections.yaml` to system Oracle, only if tooling is healthy.|

## Notes & caveats

- **Local only by design.** Data Pump writes its dump/log files to a server-side
  `DIRECTORY` object; because the DB is on this machine, the export folder is a
  normal local folder. Pointing this at a remote DB would write files on *that*
  server, not here.
- **Client/DB compatibility.** Oracle client 23ai connects to DB 19c, 21c, 23ai.
  Use a client at least as new as your oldest target (a 19c client also works for
  19c→23ai in most cases).
- **Rename** edits `SYS.USER$` and flushes the shared pool — fine for local/test
  databases, not something to do on production.
- Credentials are passed to `expdp`/`impdp` as a single argument (not through a
  shell), so passwords with special characters are safe.

## Layout

```
dumboracle/
  cli.py          subcommand dispatch + interactive menu (errors return to menu)
  config.py       YAML loader, connection model, comment-preserving key editor
  db.py           oracledb thick-mode helpers (users, grants, rename, directory)
  datapump.py     expdp/impdp runners + dump auto-detection
  operations.py   the six high-level operations
  clienttools.py  extract-client / doctor / wire-path setup helpers
  ui.py           prompts / formatting
  util.py         timestamps, identifier quoting
setup.ps1 / setup.sh        create the venv and install deps
connections.example.yaml    template (committed); connections.yaml is git-ignored
past-scripts/               the original .bat/.sql kept for reference
```
