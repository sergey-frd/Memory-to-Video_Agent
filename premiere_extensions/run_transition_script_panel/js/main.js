(function () {
    "use strict";

    var DEFAULT_SCRIPT = "E:\\Git\\P_h_o_t_o\\Dv_Ivan\\2026\\reports\\Ivan26_o04_apply_transitions.jsx";
    var scriptPathInput = document.getElementById("scriptPath");
    var statusText = document.getElementById("statusText");
    var runButton = document.getElementById("runButton");
    var browseButton = document.getElementById("browseButton");
    var defaultButton = document.getElementById("defaultButton");

    function setStatus(message) {
        statusText.textContent = message;
    }

    function getCep() {
        if (window.__adobe_cep__) {
            return window.__adobe_cep__;
        }
        return null;
    }

    function evalScript(script, callback) {
        var cep = getCep();
        if (!cep || typeof cep.evalScript !== "function") {
            setStatus("ERROR: CEP evalScript is not available. Run this panel inside Premiere Pro.");
            return;
        }
        cep.evalScript(script, callback);
    }

    function runSelectedScript() {
        var path = scriptPathInput.value.replace(/^\s+|\s+$/g, "");
        if (!path) {
            setStatus("ERROR: JSX path is empty.");
            return;
        }
        try {
            localStorage.setItem("transitionRunner.lastScriptPath", path);
        } catch (err) {
        }
        setStatus("Running:\n" + path);
        evalScript("runExternalJsx(" + JSON.stringify(path) + ")", function (result) {
            setStatus(result || "Done.");
        });
    }

    function browseForScript() {
        var cep = getCep();
        if (!cep || !window.cep || !window.cep.fs || typeof window.cep.fs.showOpenDialogEx !== "function") {
            setStatus("Browse is unavailable in this CEP runtime. Paste the JSX path manually.");
            return;
        }
        var startPath = scriptPathInput.value || DEFAULT_SCRIPT;
        var result = window.cep.fs.showOpenDialogEx(
            false,
            false,
            "Choose generated Premiere JSX",
            startPath,
            ["jsx"]
        );
        if (result && result.err === 0 && result.data && result.data.length) {
            scriptPathInput.value = result.data[0];
            setStatus("Selected:\n" + result.data[0]);
        }
    }

    function loadLastScriptPath() {
        try {
            var lastPath = localStorage.getItem("transitionRunner.lastScriptPath");
            if (lastPath) {
                scriptPathInput.value = lastPath;
            }
        } catch (err) {
        }
    }

    loadLastScriptPath();

    runButton.addEventListener("click", runSelectedScript);
    browseButton.addEventListener("click", browseForScript);
    defaultButton.addEventListener("click", function () {
        scriptPathInput.value = DEFAULT_SCRIPT;
        setStatus("Default Ivan script selected.");
    });
}());
