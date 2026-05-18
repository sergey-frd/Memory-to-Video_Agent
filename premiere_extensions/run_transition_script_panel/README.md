# Run Transition Script CEP Panel

Small unsigned Adobe Premiere Pro CEP panel for running generated `.jsx` transition and transform scripts.

## Install

From the repository root:

```powershell
.\install_premiere_transition_panel.bat
```

Restart Premiere Pro, then open:

```text
Window > Extensions > Run Transition Script
```

If the panel is not listed, make sure Premiere was fully restarted after installation.

## Use

1. Open the target `.prproj`.
2. Open and activate the target sequence, for example `Ivan26_o04`.
3. Open the panel.
4. Select or paste the generated `.jsx` path, for example `*_apply_transitions.jsx` or `*_apply_transforms.jsx`.
5. Press `Run`.

The generated JSX writes its own `.log` file next to the script.
