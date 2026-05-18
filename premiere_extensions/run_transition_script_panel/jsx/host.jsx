function runExternalJsx(rawPath) {
    try {
        if (!rawPath) {
            return "ERROR: JSX path is empty.";
        }

        var normalizedPath = String(rawPath).replace(/\\/g, "/");
        var scriptFile = new File(normalizedPath);
        if (!scriptFile.exists) {
            return "ERROR: JSX file does not exist:\n" + normalizedPath;
        }

        $.evalFile(scriptFile);
        return "OK: Script executed.\n" + normalizedPath;
    } catch (err) {
        var line = err && err.line ? "\nLine: " + err.line : "";
        return "ERROR while running JSX:\n" + err + line;
    }
}
