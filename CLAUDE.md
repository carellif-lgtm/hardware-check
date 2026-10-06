## Shell e comandi

- **Shell**: zsh su macOS (non bash). I comandi bash possono fallire.
- **Glob e pattern**: zsh espande `*` e `?` prima di eseguire. Usare sempre gli apici:
  - ✅ `grep "pattern" --include="*.py"`
  - ❌ `grep pattern --include=*.py`
- **Bracketed paste**: se appare `^[[200~` all'inizio di un comando, il terminale ha inserito sequenze di controllo. Prevenzione:
  - Disabilitare in `~/.zshrc`: `unset zle_bracketed_paste`
  - Oppure incollare un comando alla volta
  - Oppure disabilitare nelle preferenze del terminale
- **Comandi GNU-only**: `timeout`, `realpath`, `readlink -f` non esistono su macOS. Usare alternative Python o builtin:
  - ✅ `subprocess.run([...], timeout=5)`
  - ❌ `timeout 5 command`
- **Pipe su tabelle**: non usare pipe su comandi che stampano tabelle (es. `vercel whoami | head -1`). Possono fallire con "pipe closed".
- **Verifica output**: verificare sempre l'output grezzo prima di procedere.

## Ambiente

- **Piattaforma**: darwin (macOS), non linux
- **Directory**: ~/hardware-check
- **Remote**: origin https://github.com/carellif-lgtm/hardware-check.git

## Regole di sessione

- Niente `git add/commit/push` senza approvazione scritta esplicita
- PR resta in Draft finché non esplicitamente richiesto
- Track A vs Track B separati:
  - **Track A**: hardware-check (implementazione)
  - **Track B**: E-ValuFy (strict read-only, sessione separata)
- Output grezzo obbligatorio per ogni affermazione
- Verificare con tool esterni (gh, git ls-remote) prima di procedere

## Preflight (obbligatorio prima di modifiche)

```bash
cd ~/hardware-check
pwd
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git remote -v
```

**Stop condition:** Se HEAD ≠ main o working tree ha modifiche inattese → FERMARSI.

## Checklist pre-push

- [ ] `git diff --check` (nessun whitespace error)
- [ ] `git status --short` (solo file previsti)
- [ ] `gh pr view <N> --json headRefOid,changedFiles,isDraft,state`
- [ ] `git ls-remote origin refs/heads/<branch>`
- [ ] CI verde: `gh pr checks <N> --watch`

## Policy di chiusura sessione

- Prima di chiudere: `git status --short`, `git log --oneline -3`, `gh pr list`
- Dichiarare esplicitamente: "Sessione chiusa, nessun commit pendente" o "Commit X pushato, remoto verificato"
- Se la sessione sta per terminare: aggiornare `.claude/SESSION-STATUS.md` e segnalare il rischio

## Governance Track A/B

- **Track A (hardware-check)**: Implementazione, test, deploy
- **Track B (E-ValuFy)**: Strict read-only, sessione separata, mai toccare codice
- **Separazione**: Mai mescolare i due track nella stessa sessione

## Errori noti (E-family)

- **E1/E2**: zsh espande glob, timeout inesistente su macOS → usare apici, subprocess Python
- **E18**: Bracketed paste `^[[200~` → disabilitare zle_bracketed_paste
- **E19**: Tilde finale in nomi branch → rileggere comandi
- **E20**: Rotazione produzione incompleta (Vercel senza Neon) → procedura: 1) Neon API, 2) Attesa sync, 3) Redeploy, 4) Verifica from_cache
