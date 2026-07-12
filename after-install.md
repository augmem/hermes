# Finish setup

After Git installation, select Cortext as the active Hermes memory provider:

```bash
hermes config set memory.provider cortext
```

This is currently required even if you passed `--enable`: Hermes's Git plugin
installer enables a plugin but does not select an exclusive memory provider.
