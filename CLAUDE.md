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
- PR 5 resta in Draft finché non esplicitamente richiesto
- Track B (E-ValuFy): sola lettura, mai toccare
- Output grezzo obbligatorio per ogni affermazione
- Verificare con tool esterni (gh, git ls-remote) prima di procedere
