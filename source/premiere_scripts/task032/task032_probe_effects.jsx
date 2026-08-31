(function () {
var base = new File($.fileName).parent.fsName.replace(/\\/g, '/') + '/';
var initial = new File(base + 'TASK_032_EFFECT_AVAILABILITY.txt');
var revision = new File(base + 'TASK_032_REVISION_EFFECT_AVAILABILITY.txt');
initial.encoding = revision.encoding = 'UTF-8';
initial.open('w'); revision.open('w');
try {
    app.enableQE();
    var names = ['Tint', 'Gaussian Blur', 'Lumetri Color', 'Alpha Adjust', 'Solid Composite'];
    for (var i = 0; i < names.length; i++) {
        var effect = qe.project.getVideoEffectByName(names[i]);
        var line = names[i] + ': ' + (effect ? 'AVAILABLE' : 'MISSING');
        initial.writeln(line); revision.writeln(line);
    }
    var limiter = qe.project.getAudioEffectByName('Hard Limiter');
    revision.writeln('Hard Limiter: ' + (limiter ? 'AVAILABLE' : 'MISSING'));
} catch (error) {
    initial.writeln('ERROR ' + error); revision.writeln('ERROR ' + error);
}
initial.close(); revision.close();
})();

// Initial plan needs its own evidence file; revision probing above is separate.
(function () {
    var base = new File($.fileName).parent.fsName + '/';
    var f = new File(base + 'TASK_032_EFFECT_AVAILABILITY.txt');
    f.encoding = 'UTF-8'; f.open('w');
    try {
        app.enableQE();
        var names = ['Tint', 'Gaussian Blur', 'Lumetri Color', 'Alpha Adjust'];
        for (var i = 0; i < names.length; i++) {
            f.writeln(names[i] + ': ' + (qe.project.getVideoEffectByName(names[i]) ? 'AVAILABLE' : 'MISSING'));
        }
    } catch (error) { f.writeln('ERROR ' + error); }
    f.close();
})();
